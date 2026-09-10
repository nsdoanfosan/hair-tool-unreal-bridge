"""Exercise native Hair Tool selection, filters and transactional deformers.

Run only with --background --factory-startup. No preferences are saved.
"""

import addon_utils
import bmesh
import bpy
from pathlib import Path
import sys
import tempfile

assert bpy.app.background
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
assert addon_utils.enable("hair_tool", default_set=False, persistent=False)
import hair_tool

for cls in hair_tool.auto_load.ordered_classes:
    if issubclass(cls, (bpy.types.PropertyGroup, bpy.types.Operator, bpy.types.AddonPreferences)):
        bpy.utils.register_class(cls)
hair_tool.hair_tool_props.register_properties()
if "hair_tool" not in bpy.context.preferences.addons:
    bpy.context.preferences.addons.new().module = "hair_tool"
assert addon_utils.enable("hair_tool_unreal_bridge", default_set=False, persistent=False)
import hair_tool_unreal_bridge as bridge
from hair_tool_unreal_bridge import export_masks, hair_system_compat, material_compat, profile_sync
from hair_tool.hair_baking import hair_geometry_nodes_shared as shared
from hair_tool.hair_baking.hair_geometry_nodes import HTOOL_OT_TransferHairSystem

profile_sync.unregister_auto_sync()
if bpy.app.timers.is_registered(bridge.initialize_export_ao_after_register):
    bpy.app.timers.unregister(bridge.initialize_export_ao_after_register)
temporary = tempfile.TemporaryDirectory()
for material in bpy.data.materials:
    material.htue_settings.profile_registry_path = str(Path(temporary.name) / "profiles.json")
material_compat.install_runtime_integration()
export_masks.install_runtime_integration()

bpy.ops.mesh.primitive_cylinder_add(vertices=8, radius=0.15, depth=1)
source = bpy.context.object
source.name = "HTUE_TEST_GUIDE"
bm = bmesh.new()
bm.from_mesh(source.data)
bmesh.ops.delete(bm, geom=[f for f in bm.faces if all(v.co.z > 0 for v in f.verts)], context="FACES_ONLY")
bm.to_mesh(source.data)
bm.free()
sharp = source.data.attributes.new("sharp_edge", "BOOLEAN", "EDGE")
for edge in source.data.edges:
    sharp.data[edge.index].value = all(source.data.vertices[i].co.z < 0 for i in edge.vertices)
assert bpy.ops.object.add_hair_system(mode="PRISM_MESH_GUIDE", draw_popup=False) == {"FINISHED"}
HTOOL_OT_TransferHairSystem.transfer_hsys_to_target(bpy.context, source, move_method="EMPTYCURVE")
output = bpy.context.object
output.name = "HTUE_TEST_OUTPUT"

probe = bpy.data.objects.new("HTUE_TEST_PROBE", bpy.data.meshes.new("HTUE_TEST_PROBE"))
bpy.context.scene.collection.objects.link(probe)
tree = bpy.data.node_groups.new("HTUE_TEST_PROBE", "GeometryNodeTree")
tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
info = tree.nodes.new("GeometryNodeObjectInfo")
info.inputs["Object"].default_value = output
group_output = tree.nodes.new("NodeGroupOutput")
tree.links.new(info.outputs["Geometry"], group_output.inputs["Geometry"])
probe.modifiers.new("Probe", "NODES").node_group = tree


def geometry_counts():
    bpy.context.view_layer.update()
    mesh = probe.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    return len(mesh.vertices), len(mesh.polygons)


before = geometry_counts()
assert before[0] > 0
for index in (0, 0, 1, 2):
    output.ht_props.hair_nodes.system_index = index
    assert bpy.ops.object.add_hair_system(mode="FILTER", draw_popup=False) == {"FINISHED"}
    assert geometry_counts() == before

# Native and Bridge actions honor the same Hair Object Users selection.
output.ht_props.surface_props.active_obj_index = bpy.context.scene.objects.find(source.name)
assert shared.get_current_hair_object(bpy.context) == source
assert export_masks._active_hair_tool_state(bpy.context)[0] == source
output.ht_props.surface_props.active_obj_index = bpy.context.scene.objects.find(output.name)

# Reproduce an Outliner click on a hidden guide. The actual active/selected
# object changes, so context.active_object-only native operators also agree.
bpy.ops.object.select_all(action="DESELECT")
source.hide_set(True)
bpy.context.view_layer.objects.active = source
assert hair_system_compat.output_for_hidden_guide(bpy.context) == output
assert hair_system_compat.synchronize_hidden_guide_selection() == output
assert bpy.context.active_object == output
assert output.select_get()
assert shared.get_current_hair_object(bpy.context) == output
assert export_masks._active_hair_tool_state(bpy.context)[0] == output

# Outliner can make a hidden guide active in the View Layer while both
# context active/object fields are absent. The guide's hide state is retained.
output.select_set(False)
bpy.context.view_layer.objects.active = source
with bpy.context.temp_override(active_object=None, object=None, selected_objects=[]):
    assert hair_system_compat.output_for_hidden_guide(bpy.context) == output
    assert hair_system_compat.synchronize_hidden_guide_selection() == output
assert source.hide_get()
assert bpy.context.view_layer.objects.active == output

# A visible guide remains independently editable.
source.hide_set(False)
bpy.context.view_layer.objects.active = source
assert hair_system_compat.synchronize_hidden_guide_selection() is None
assert bpy.context.active_object == source

# Multiple visible consumers cannot be resolved safely without a user choice.
source.hide_set(True)
second = output.copy()
bpy.context.scene.collection.objects.link(second)
assert hair_system_compat.output_for_hidden_guide(bpy.context) is None
assert hair_system_compat.synchronize_hidden_guide_selection() is None
bpy.data.objects.remove(second, do_unlink=True)
bpy.context.view_layer.objects.active = output

# Built-ins and export masks must repair stale deformer indices before insert.
system = output.ht_props.hair_nodes.hair_systems[output.ht_props.hair_nodes.system_index]
for group in (shared.HD_Color, shared.HD_FactorSet, "HTUE_Export_Weight", "HTUE_Export_PixelDepthOffset"):
    system.deformer_index = 99
    shared.add_hair_deformer(output, group)
    assert geometry_counts() == before
    mod = shared.get_hair_mod_by_idx(output, output.ht_props.hair_nodes.system_index)
    assert len(system.deformers) == len(shared.get_deformer_nodes_list(mod.node_group, with_setup_node=True))


def insertion_state(mod):
    return (
        tuple((node.as_pointer(), tuple(node.location)) for node in mod.node_group.nodes),
        tuple(sorted((link.from_socket.as_pointer(), link.to_socket.as_pointer()) for link in mod.node_group.links)),
        tuple((len(item.deformers), item.deformer_index) for item in output.ht_props.hair_nodes.hair_systems),
    )


# Inject a failure after links change, exercising a real native insertion.
original_influence = shared.influence_input_add_node


def interrupted_influence(*_args, **_kwargs):
    raise RuntimeError("expected test interruption")


for group in (shared.HD_Color, "HTUE_Export_Weight"):
    old_state = insertion_state(mod)
    shared.influence_input_add_node = interrupted_influence
    try:
        try:
            shared.add_hair_deformer(output, group)
        except RuntimeError as error:
            assert "previous node chain was restored" in str(error)
        else:
            raise AssertionError("The interrupted insertion unexpectedly succeeded")
    finally:
        shared.influence_input_add_node = original_influence
    assert insertion_state(mod) == old_state
    assert geometry_counts() == before

# Every imported alias is patched, restored, then safely installed again.
from hair_tool import curves_from_grid, ribbons_operations
assert curves_from_grid.add_hair_deformer is export_masks._safe_add_hair_deformer
assert ribbons_operations.add_hair_deformer is export_masks._safe_add_hair_deformer
patches = dict(export_masks._patched_add_targets)
export_masks.install_runtime_integration()
assert export_masks._patched_add_targets == patches
export_masks.remove_runtime_integration()
for (module, attribute), original in patches.items():
    assert getattr(module, attribute) is original
export_masks.install_runtime_integration()
hair_system_compat.install_runtime_integration()
hair_system_compat.install_runtime_integration()
assert list(bpy.app.handlers.load_post).count(hair_system_compat._on_load) == 1
hair_system_compat.remove_runtime_integration()
assert hair_system_compat._on_load not in bpy.app.handlers.load_post
print("HTUE_NATIVE_HAIR_SYSTEM_INTEGRATION_OK", {"vertices": before[0], "polygons": before[1], "patched_aliases": len(patches)})
