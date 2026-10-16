"""Standalone material refresh after the legacy original was not saved/appended."""
import json
from pathlib import Path
import runpy
import tempfile

import bpy


baseline = runpy.run_path(str(Path(__file__).with_name('blender_color_stack_repeat_setup.py')))
nodes, profile_sync, schema = (baseline[name] for name in ('nodes', 'profile_sync', 'schema'))
material, host = (baseline[name] for name in ('material', 'host'))
material.use_fake_user = True
original = bpy.data.node_groups[material[schema.ORIGINAL_SHADER_GROUP_PROPERTY]]
assert original.use_fake_user, 'New setups must retain the original for save/reload'
# Reproduce a material-only append, where the legacy original is not a dependency.
bpy.data.node_groups.remove(original)
stack_name = f'{schema.BRIDGE_GROUP_PREFIX}::{material.name}'
clone_name = f'{schema.SHADER_CLONE_PREFIX}::{material.name}'


def settings_state(mat):
    return profile_sync.material_values(mat)


def shader_state(shader):
    return nodes._capture_shader_inputs(material, material.node_tree, shader)


with tempfile.TemporaryDirectory(prefix='htue-refresh-qa-') as directory:
    material.htue_settings.texture_root = directory
    material.htue_settings.root_mix = 0.321
    material.htue_settings.tip_range = 0.654
    material.htue_settings.system_color_influence = 0.876
    host.inputs['Root Color Mix Factor'].default_value = 0.123
    host.inputs['Tip Color Mix Factor'].default_value = 0.456
    factor = material.node_tree.nodes.new('ShaderNodeValue')
    factor.name = 'User Factor Connection'
    factor.outputs[0].default_value = 0.234
    material.node_tree.links.new(factor.outputs[0], host.inputs['Factor [Map]'])
    saved_settings = settings_state(material)
    saved_shader = shader_state(host)
    original_output = [(link.from_socket.name, link.to_node.name, link.to_socket.name)
                       for output in host.outputs for link in output.links]

    # Old saved shaders lack their per-object SystemColor gate hook.
    material.node_tree.nodes.remove(material.node_tree.nodes['HTUE System Availability'])
    host.inputs['HTUE System Attribute Available'].default_value = 0
    for _ in range(3):
        nodes.setup_material(material)
        assert settings_state(material) == saved_settings
        assert shader_state(host) == saved_shader
        assert host.node_tree.name == clone_name
        assert host.inputs['HTUE System Attribute Available'].is_linked
        assert [(link.from_socket.name, link.to_node.name, link.to_socket.name)
                for output in host.outputs for link in output.links] == original_output
        assert [g.name for g in bpy.data.node_groups if g.name.startswith(stack_name)] == [stack_name]
        assert [g.name for g in bpy.data.node_groups if g.name.startswith(clone_name)] == [clone_name]

    # Saving only synthetic QA data is permitted in this factory process.
    # Disable profile publication: this test must not touch the shared registry.
    profile_sync.sync_all_materials_now = lambda: []
    path = str(Path(directory) / 'standalone.blend')
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path, load_ui=True)
    material = bpy.data.materials['M_HT_DedupQA']
    host = nodes.find_hair_shader(material)
    assert bpy.data.node_groups.get(material[schema.ORIGINAL_SHADER_GROUP_PROPERTY]) is None
    nodes.setup_material(material)
    assert settings_state(material) == saved_settings
    assert shader_state(host) == saved_shader
    assert host.inputs['HTUE System Attribute Available'].is_linked

    # An incompatible clone must fail before altering existing hooks or groups.
    color = next(node for node in host.node_tree.nodes
                 if node.bl_idname == 'NodeReroute' and node.label == 'Color')
    color.label = 'Unrelated reroute'
    before_groups = sorted(bpy.data.node_groups.keys())
    before_nodes = sorted(material.node_tree.nodes.keys())
    before_links = [(link.from_node.name, link.from_socket.name,
                     link.to_node.name, link.to_socket.name)
                    for link in material.node_tree.links]
    try:
        nodes.setup_material(material)
    except RuntimeError as error:
        assert 'final Color reroute' in str(error)
    else:
        raise AssertionError('Invalid clone must be rejected')
    assert sorted(bpy.data.node_groups.keys()) == before_groups
    assert sorted(material.node_tree.nodes.keys()) == before_nodes
    assert [(link.from_node.name, link.from_socket.name,
             link.to_node.name, link.to_socket.name)
            for link in material.node_tree.links] == before_links

print('HTUE_REFRESH_WITHOUT_ORIGINAL_OK ' + json.dumps({
    'repeat': 3, 'saved_reload': True, 'factor_and_color_settings_preserved': True,
    'invalid_clone_preflight': True,
}))
