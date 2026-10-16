"""Blender --factory-startup --background --python this_file (no saves)."""
import bpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from hair_tool_unreal_bridge import system_presence as s
mesh = bpy.data.meshes.new('Fixture')
mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
obj = bpy.data.objects.new('Fixture', mesh)
bpy.context.scene.collection.objects.link(obj)
ng = bpy.data.node_groups.new('Hair_System_Profile_fixture', 'GeometryNodeTree')
for direction in ('INPUT', 'OUTPUT'):
    ng.interface.new_socket(name='Geometry', in_out=direction, socket_type='NodeSocketGeometry')
gi = ng.nodes.new('NodeGroupInput')
go = ng.nodes.new('NodeGroupOutput')
sep = ng.nodes.new('GeometryNodeSeparateComponents')
sep.name = 'Separate Components'
line = ng.nodes.new('GeometryNodeCurvePrimitiveLine')
store = ng.nodes.new('GeometryNodeStoreNamedAttribute')
store.data_type = 'FLOAT_COLOR'
store.domain = 'POINT'
store.inputs['Name'].default_value = 'SystemColor'
store.inputs['Value'].default_value = (0, 0, 0, 0)  # Authored black, alpha ignored.
join = ng.nodes.new('GeometryNodeJoinGeometry')
join2 = ng.nodes.new('GeometryNodeJoinGeometry')
circle = ng.nodes.new('GeometryNodeCurvePrimitiveCircle')
circle.inputs['Resolution'].default_value = 3
circle.inputs['Radius'].default_value = .1
curve_mesh = ng.nodes.new('GeometryNodeCurveToMesh')
dummy = ng.nodes.new('NodeFrame')
dummy.name = 'Capture Curve Attributes'
for output, target in (
    (gi.outputs['Geometry'], join.inputs[0]),
    (line.outputs['Curve'], store.inputs['Geometry']),
    (store.outputs['Geometry'], join.inputs[0]),
    (join.outputs[0], sep.inputs[0]),
    (sep.outputs['Mesh'], join2.inputs[0]),
    (sep.outputs['Curve'], curve_mesh.inputs['Curve']),
    (circle.outputs['Curve'], curve_mesh.inputs['Profile Curve']),
    (curve_mesh.outputs[0], join2.inputs[0]),
    (join2.outputs[0], go.inputs[0]),
):
    ng.links.new(output, target)
mod = obj.modifiers.new('Profile', 'NODES')
mod.node_group = ng


def read():
    bpy.context.view_layer.update()
    me = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    return (len(me.vertices), len(me.polygons)), me


before, _ = read()
s.ensure_object(obj)
after, result = read()
values = [v.value for v in result.attributes[s.ATTRIBUTE].data]
assert before == after == (9, 4), (before, after)
assert values.count(0.) == 3 and values.count(1.) == 6, values
assert s.ensure_object(obj) is False  # Idempotent on repeated refresh.

# Test the actual Send2UE writer, including the existing v3 absent-color sentinel.
sys.path.insert(0, str(ROOT.parent / 'BlenderTools/src/addons'))
from send2ue.core import hair_tool_export as exp
copy = result.copy()
exp._stamp_system_color_presence(copy)
assert [v.value for v in copy.attributes[s.ATTRIBUTE].data] == values
exp._pack_rfaos_nanite_uvs(copy, [(.5, .5, 1., .5, 0., 0., 0.)] * len(copy.loops))
uv = copy.uv_layers[exp.RFAOS_NANITE_UV_START_INDEX + 1]
assert {round(d.uv.y, 3) for d in uv.data} == {1., 2.}
print('COMPONENT_PRESENCE_PASS: topology, valid black alpha=0, absent color, repeat refresh, actual v3 export UV')

# Actual Profile also renders its source mesh. It must inherit the authored
# strand color dynamically, without a baked fallback RGB or a dependency cycle.
join.name = 'Input Geometry'
join2.name = 'Join Geometry'
for link in tuple(curve_mesh.outputs[0].links):
    if link.to_node == join2:
        ng.links.remove(link)
material = ng.nodes.new('GeometryNodeSetMaterial')
ng.links.new(curve_mesh.outputs[0], material.inputs['Geometry'])
ng.links.new(material.outputs['Geometry'], join2.inputs[0])
assert s.ensure_surface_color(ng)
after, result = read()
assert after == (9, 4)
assert all(v.value == 1. for v in result.attributes[s.ATTRIBUTE].data)
store.inputs['Value'].default_value = (.2, .1, .3, 0.)
after, result = read()
assert all(max(abs(a-b) for a,b in zip(v.color[:3], (.2,.1,.3))) < 1e-5
           for v in result.color_attributes['SystemColor'].data)
# Explicitly authored mesh color must survive the inheritance step, too.
authored = mesh.color_attributes.new(name='SystemColor', type='FLOAT_COLOR', domain='POINT')
for value in authored.data:
    value.color = (.4, .2, .1, 0.)
mesh.update()
after, result = read()
colors = [tuple(round(v,3) for v in d.color[:3]) for d in result.color_attributes['SystemColor'].data]
assert colors.count((.4,.2,.1)) == 3, colors
assert after == (9,4)
assert not s.ensure_surface_color(ng)
print('SOURCE_COLOR_INHERITANCE_PASS: dynamic source color, authored mesh color preserved, no geometry changes')
