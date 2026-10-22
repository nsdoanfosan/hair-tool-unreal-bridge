"""Native Profile source meshes must remain inputs, never an export layer.

Run in disposable Blender --background --factory-startup; no preferences saved.
"""
import sys
import tempfile
from pathlib import Path
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from hair_tool_unreal_bridge import system_presence as s

export = bpy.data.collections.new('Export')
bpy.context.scene.collection.children.link(export)
data = bpy.data.meshes.new('SimulationInput')
data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
color = data.color_attributes.new(name='SystemColor', type='FLOAT_COLOR', domain='POINT')
for item in color.data:
    item.color = (1., 0., 0., 1.)
obj = bpy.data.objects.new('RenderHair', data)
export.objects.link(obj)
backup = bpy.data.objects.new('SimulationBackup', data)
bpy.context.scene.collection.objects.link(backup)
backup.hide_render = True

def geometry_tree(name, inputs, outputs):
    tree = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    for n in inputs:
        tree.interface.new_socket(name=n, in_out='INPUT', socket_type='NodeSocketGeometry')
    for n in outputs:
        tree.interface.new_socket(name=n, in_out='OUTPUT', socket_type='NodeSocketGeometry')
    return tree, tree.nodes.new('NodeGroupInput'), tree.nodes.new('NodeGroupOutput')

capture, ci, co = geometry_tree('Capture Curve Attributes_fixture', ['Curves', 'Mesh'], ['Curves'])
capture.links.new(ci.outputs['Curves'], co.inputs['Curves'])
flat, fi, fo = geometry_tree('Flat_Profile_UV_fixture', ['Curves'], ['Geometry'])
circle = flat.nodes.new('GeometryNodeCurvePrimitiveCircle')
circle.inputs['Resolution'].default_value = 3
circle.inputs['Radius'].default_value = .1
convert = flat.nodes.new('GeometryNodeCurveToMesh')
flat.links.new(fi.outputs['Curves'], convert.inputs['Curve'])
flat.links.new(circle.outputs['Curve'], convert.inputs['Profile Curve'])
flat.links.new(convert.outputs[0], fo.inputs['Geometry'])

tree, gi, go = geometry_tree('Hair_System_Profile_fixture', ['Geometry'], ['Geometry'])
line = tree.nodes.new('GeometryNodeCurvePrimitiveLine')
authored = tree.nodes.new('GeometryNodeStoreNamedAttribute')
authored.data_type = 'FLOAT_COLOR'
authored.inputs['Name'].default_value = 'SystemColor'
authored.inputs['Value'].default_value = (.2, .1, .4, 1.)
region = tree.nodes.new('GeometryNodeStoreNamedAttribute')
region.data_type = 'INT'
region.domain = 'CURVE'
region.inputs['Name'].default_value = 'UV_Box_ID'
region.inputs['Value'].default_value = 4
incoming = tree.nodes.new('GeometryNodeJoinGeometry')
incoming.name = 'Input Geometry'
separate = tree.nodes.new('GeometryNodeSeparateComponents')
separate.name = 'Separate Components'
captured = tree.nodes.new('GeometryNodeGroup')
captured.node_tree = capture
captured.name = 'Capture Curve Attributes'
profile = tree.nodes.new('GeometryNodeGroup')
profile.node_tree = flat
profile.name = 'Flat_Profile_UV'
material = tree.nodes.new('GeometryNodeSetMaterial')
material.name = 'Set Material'
join = tree.nodes.new('GeometryNodeJoinGeometry')
join.name = 'Join Geometry'
for output, target in (
    (gi.outputs['Geometry'], incoming.inputs[0]),
    (line.outputs['Curve'], authored.inputs['Geometry']),
    (authored.outputs[0], region.inputs['Geometry']),
    (region.outputs[0], incoming.inputs[0]),
    (incoming.outputs[0], separate.inputs[0]),
    (separate.outputs['Mesh'], captured.inputs['Mesh']),
    (separate.outputs['Curve'], captured.inputs['Curves']),
    (captured.outputs[0], profile.inputs['Curves']),
    (profile.outputs[0], material.inputs['Geometry']),
    (material.outputs[0], join.inputs[0]),
    (separate.outputs['Mesh'], join.inputs[0]),  # Legacy simulation leak.
    (join.outputs[0], go.inputs[0]),
):
    tree.links.new(output, target)
for host in (obj, backup):
    host.modifiers.new('Profile', 'NODES').node_group = tree

def read(host):
    bpy.context.view_layer.update()
    evaluated = host.evaluated_get(bpy.context.evaluated_depsgraph_get())
    geometry = evaluated.evaluated_geometry()
    mesh = geometry.mesh
    return (len(mesh.vertices), len(mesh.polygons),
            [tuple(d.color) for d in mesh.attributes['SystemColor'].data],
            sorted(set(d.value for d in mesh.attributes['UV_Box_ID'].data)))

assert read(obj)[:2] == (9, 4)
assert s.ensure_object(obj)
private = obj.modifiers['Profile'].node_group
assert private != tree and backup.modifiers['Profile'].node_group == tree
assert read(obj)[:2] == (6, 3), read(obj)
assert read(backup)[:2] == (9, 4), read(backup)
assert private.nodes['Capture Curve Attributes'].inputs['Mesh'].is_linked
assert read(obj)[3] == [4]
assert all(max(abs(a-b) for a, b in zip(c, (.2, .1, .4, 1.))) < 1e-6 for c in read(obj)[2])
assert not s.ensure_object(obj)
assert not s.ensure_surface_color(private)  # Native sampling mesh is never recolored.
private.nodes['Flat_Profile_UV'].name = 'Circle_Profile_UV'
assert s.is_native_render_profile(private)

# Old augmented graphs and a future refresh must be repaired idempotently too.
leak = private.nodes.new('GeometryNodeStoreNamedAttribute')
leak.name = 'HTUE Source Mesh SystemColor'
private.links.new(private.nodes[s.PREFIX+'Mesh'].outputs['Geometry'], leak.inputs['Geometry'])
private.links.new(leak.outputs['Geometry'], private.nodes['Join Geometry'].inputs[0])
obj.modifiers['Profile'].show_viewport = False  # Render/export-only modifiers count too.
assert s.ensure_object(obj)
obj.modifiers['Profile'].show_viewport = True
assert not s.ensure_object(obj)
assert read(obj)[:2] == (6, 3)
assert private.nodes['Capture Curve Attributes'].inputs['Mesh'].is_linked

with tempfile.TemporaryDirectory(prefix='htue-render-output-test-') as folder:
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(folder) / 'fixture.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(Path(folder) / 'fixture.blend'), load_ui=True)
    loaded = bpy.data.objects['RenderHair']
    assert not s.ensure_object(loaded)
    assert read(loaded)[:2] == (6, 3)
    assert read(loaded)[3] == [4]
    assert read(bpy.data.objects['SimulationBackup'])[:2] == (9, 4)
print('SIMULATION_MESH_EXCLUSION_PASS: flat/circle Profile output, source sampling, color, UV, shared backup, repeat refresh, saved reload')
