"""Run with Blender --factory-startup --background --python this_file."""
import sys
from pathlib import Path
import addon_utils
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons'))
assert addon_utils.enable('hair_tool_unreal_bridge', default_set=False) is not None
from hair_tool_unreal_bridge import nodes, profile_sync, schema

# The disposable shader test must not publish a shared production profile.
profile_sync.ensure_material = lambda *args, **kwargs: None
group = bpy.data.node_groups.new('HairShaderMain_DedupQA', 'ShaderNodeTree')
for name in nodes.REQUIRED_HAIR_INPUTS:
    group.interface.new_socket(name=name, in_out='INPUT', socket_type=(
        'NodeSocketColor' if 'Color' in name and 'Mix' not in name else 'NodeSocketFloat'))
for name in ('Random Id [Map]', 'Factor [Map]', 'Depth [Map]', 'Vert Color AO [Map]'):
    group.interface.new_socket(name=name, in_out='INPUT', socket_type='NodeSocketFloat')
group.interface.new_socket(name='Shader', in_out='OUTPUT', socket_type='NodeSocketShader')
group.nodes.new('NodeGroupInput')
output = group.nodes.new('NodeGroupOutput')
shader = group.nodes.new('ShaderNodeBsdfPrincipled')
color = group.nodes.new('NodeReroute')
color.label = 'Color'
group.links.new(color.outputs[0], shader.inputs['Base Color'])
group.links.new(shader.outputs[0], output.inputs['Shader'])
material = bpy.data.materials.new('M_HT_DedupQA')
material.use_nodes = True
material.node_tree.nodes.clear()
host = material.node_tree.nodes.new('ShaderNodeGroup')
host.node_tree = group
out = material.node_tree.nodes.new('ShaderNodeOutputMaterial')
material.node_tree.links.new(host.outputs['Shader'], out.inputs['Surface'])
material.htue_settings.initialized = True
stack_name = f'{schema.BRIDGE_GROUP_PREFIX}::{material.name}'
clone_name = f'{schema.SHADER_CLONE_PREFIX}::{material.name}'
nodes.setup_material(material)

# Reproduce a saved suffix plus the unused canonical stack left by older setup.
stack = host.node_tree.nodes[schema.INTERNAL_STACK_NODE_NAME].node_tree
stack.name = stack_name + '.001'
orphan = stack.copy()
orphan.name = stack_name
for _ in range(3):
    nodes.setup_material(material)
    assert host.node_tree.name == clone_name
    assert host.node_tree.nodes[schema.INTERNAL_STACK_NODE_NAME].node_tree.name == stack_name
    assert [g.name for g in bpy.data.node_groups if g.name.startswith(stack_name)] == [stack_name]
    assert [g.name for g in bpy.data.node_groups if g.name.startswith(clone_name)] == [clone_name]
print('COLOR_STACK_REPEAT_SETUP_OK')

# Two users share the shader; only one has SystemColor. Its RGB remains valid
# even with zero alpha, so alpha cannot be used as the availability mask.
from hair_tool_unreal_bridge import deformer_sync
plain_mesh = bpy.data.meshes.new('PlainQA')
colored_mesh = bpy.data.meshes.new('ColoredQA')
for mesh in (plain_mesh, colored_mesh):
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh.materials.append(material)
attribute = colored_mesh.color_attributes.new(
    name='SystemColor', type='FLOAT_COLOR', domain='POINT')
for entry in attribute.data:
    entry.color = (0.2, 0.4, 0.6, 0.0)
plain = bpy.data.objects.new('PlainQA', plain_mesh)
colored = bpy.data.objects.new('ColoredQA', colored_mesh)
for obj in (plain, colored):
    bpy.context.scene.collection.objects.link(obj)
nodes.setup_material(material)
assert plain[deformer_sync.SYSTEM_SOURCE_PROPERTY] == 0.0
assert colored[deformer_sync.SYSTEM_SOURCE_PROPERTY] == 1.0
gate = host.inputs['HTUE System Attribute Available']
component_gate = gate.links[0].from_node
assert component_gate.type == 'MIX_RGB'
object_gate = component_gate.inputs[1].links[0].from_node
assert object_gate.attribute_type == 'OBJECT'
assert object_gate.attribute_name == deformer_sync.SYSTEM_SOURCE_PROPERTY
stack = host.node_tree.nodes[schema.INTERNAL_STACK_NODE_NAME]
assert stack.inputs['System Attribute Available'].is_linked
colored_mesh.color_attributes.remove(attribute)
profile_sync._DEFORMER_SOURCES_DIRTY = True
profile_sync._refresh_deformer_sources_if_dirty()
assert colored[deformer_sync.SYSTEM_SOURCE_PROPERTY] == 0.0
print('SYSTEM_COLOR_PER_OBJECT_GATE_OK')
