"""Run with installed Hair Tool in factory-startup background Blender (no prefs save)."""

import addon_utils
import bpy
import bmesh
import json
from pathlib import Path
import sys
import tempfile

assert bpy.app.background, "Run only in a disposable background Blender"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
assert addon_utils.enable("hair_tool", default_set=False, persistent=False)
# Hair Tool deliberately skips registration in background mode. Register its
# native data/operator classes only; no viewport tools, handlers, or prefs save.
import hair_tool
for cls in hair_tool.auto_load.ordered_classes:
    if issubclass(cls, (bpy.types.PropertyGroup, bpy.types.Operator, bpy.types.AddonPreferences)):
        bpy.utils.register_class(cls)
hair_tool.hair_tool_props.register_properties()
if "hair_tool" not in bpy.context.preferences.addons:
    bpy.context.preferences.addons.new().module = "hair_tool"
assert addon_utils.enable("hair_tool_unreal_bridge", default_set=False, persistent=False)
from hair_tool.hair_baking import hair_geometry_nodes_shared as shared
from hair_tool.hair_baking.hair_geometry_nodes import HTOOL_OT_TransferHairSystem
from hair_tool_unreal_bridge import material_compat, nodes, profile_sync, schema
import hair_tool_unreal_bridge as bridge

bridge.initialize_export_ao_after_register()
profile_sync.unregister_auto_sync()
temporary = tempfile.TemporaryDirectory()


def graph_state(tree):
    return (
        tree.as_pointer(),
        tuple((n.name, n.bl_idname, n.node_tree.as_pointer()
               if getattr(n, "node_tree", None) else None) for n in tree.nodes),
        tuple(sorted((l.from_node.name, l.from_socket.identifier,
                      l.to_node.name, l.to_socket.identifier) for l in tree.links)),
    )


material = shared.import_default_hair_mat()
material.name = "M_HT_Existing_Prism_Test"
shader = material.node_tree.nodes["HairShaderMain"]
original = shader.node_tree
original["existing_data_marker"] = "KEEP"
custom = original.nodes.new("ShaderNodeValue")
custom.name = "Existing User Value"
custom.outputs[0].default_value = 0.731
material.htue_settings.texture_root = temporary.name
material.htue_settings.profile_registry_path = str(Path(temporary.name) / "profiles.json")
nodes.setup_material(material)
material.htue_settings.base_color = (0.17, 0.23, 0.31, 1.0)
texture = bpy.data.images.new("Existing Painted Texture", 4, 4)
texture_node = material.node_tree.nodes.new("ShaderNodeTexImage")
texture_node.image = texture
material["user_payload"] = "preserve this"

bpy.ops.mesh.primitive_cube_add()
existing = bpy.context.object
existing.name = "Existing_Material_User"
existing.data.materials.append(material)
existing.data.attributes.new("ExistingWeight", "FLOAT", "POINT").data[0].value = 0.42
group_state = {g.name: graph_state(g) for g in bpy.data.node_groups}
material_state = graph_state(material.node_tree)
contract = material[schema.CONTRACT_PROPERTY]
base_color = tuple(material.htue_settings.base_color)


def assert_preserved():
    for name, state in group_state.items():
        group = bpy.data.node_groups.get(name)
        assert group is not None and graph_state(group) == state, name
    assert graph_state(material.node_tree) == material_state
    assert material[schema.CONTRACT_PROPERTY] == contract
    assert tuple(material.htue_settings.base_color) == base_color
    assert material["user_payload"] == "preserve this"
    assert existing.data.materials[0] == material
    assert texture_node.image == texture
    assert abs(existing.data.attributes["ExistingWeight"].data[0].value - 0.42) < 1e-6
    assert original["existing_data_marker"] == "KEEP"
    assert abs(original.nodes["Existing User Value"].outputs[0].default_value - 0.731) < 1e-6


# This is the material bootstrap used by Prism Mesh Guide's new Profile.
new_material = shared.import_default_hair_mat()
assert new_material != material
assert_preserved()
assert shared.import_default_hair_mat() == new_material
assert_preserved()

# Exercise the actual operator path. Skip only its final UI popup in background.
bpy.ops.mesh.primitive_cylinder_add(vertices=8, radius=0.15, depth=1)
prism = bpy.context.object
prism.name = "New_Prism_Guide"
# Prism requires an open tip and a capped root bounded by sharp edges.
bm = bmesh.new()
bm.from_mesh(prism.data)
bmesh.ops.delete(bm, geom=[f for f in bm.faces if all(v.co.z > 0 for v in f.verts)], context="FACES_ONLY")
bm.to_mesh(prism.data)
bm.free()
sharp = prism.data.attributes.new("sharp_edge", "BOOLEAN", "EDGE")
for edge in prism.data.edges:
    sharp.data[edge.index].value = all(prism.data.vertices[i].co.z < 0 for i in edge.vertices)
assert bpy.ops.object.add_hair_system(mode="PRISM_MESH_GUIDE") == {"FINISHED"}
HTOOL_OT_TransferHairSystem.transfer_hsys_to_target(bpy.context, prism, move_method="EMPTYCURVE")
shared.set_grid_surface_props(prism, as_grid=True)
output = bpy.context.object
assert output != prism
assert len(output.ht_props.hair_nodes.hair_systems) == 1
profile = shared.get_profile_mod(output)
assert profile is not None
assert profile.properties.inputs[shared.prof_mat_input_name]["value"] == new_material
assert_preserved()
# Curves Object.dimensions can stay zero for generated Geometry Nodes output.
# Read the actual mesh component through a disposable native Object Info node.
probe_mesh = bpy.data.meshes.new("PrismGeometryProbe")
probe_object = bpy.data.objects.new("PrismGeometryProbe", probe_mesh)
bpy.context.scene.collection.objects.link(probe_object)
probe_tree = bpy.data.node_groups.new("PrismGeometryProbe", "GeometryNodeTree")
probe_tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
probe_info = probe_tree.nodes.new("GeometryNodeObjectInfo")
probe_info.inputs["Object"].default_value = output
probe_output = probe_tree.nodes.new("NodeGroupOutput")
probe_tree.links.new(probe_info.outputs["Geometry"], probe_output.inputs["Geometry"])
probe_object.modifiers.new("Probe", "NODES").node_group = probe_tree
bpy.context.view_layer.update()
generated_vertices = len(probe_object.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices)
assert generated_vertices > 0, "Prism must produce actual geometry"
# A later ordinary Deformer import must also preserve existing Geometry Nodes.
geometry_state = {g.name: graph_state(g) for g in bpy.data.node_groups
                  if g.bl_idname == "GeometryNodeTree"}
probe = bpy.data.node_groups.new("HTUE_AppendProbe", "GeometryNodeTree")
probe.nodes.new("GeometryNodeGroup").node_tree = bpy.data.node_groups["Constant"]
probe_path = Path(temporary.name) / "append-probe.blend"
bpy.data.libraries.write(str(probe_path), {probe})
bpy.data.node_groups.remove(probe)
imported_probe = shared.import_ng_base("HTUE_AppendProbe", lib_file=str(probe_path))
assert imported_probe.nodes[0].node_tree == bpy.data.node_groups["Constant"]
for name, state in geometry_state.items():
    assert graph_state(bpy.data.node_groups[name]) == state, name
assert_preserved()
print("PRISM_MATERIAL_PRESERVATION_OK", json.dumps({"preserved_groups": len(group_state),
      "preserved_geometry_groups": len(geometry_state),
      "generated_vertices": generated_vertices,
      "material": material.name, "new_output": output.name, "new_material": new_material.name}))

# Runtime installation is idempotent and restores every original imported alias.
patches = list(material_compat._PATCHES)
material_compat.install_runtime_integration()
assert material_compat._PATCHES == patches
material_compat.remove_runtime_integration()
for module, attribute, native, _replacement in patches:
    assert getattr(module, attribute) is native
material_compat.install_runtime_integration()
assert_preserved()

# Never treat a shared ID or a merely similar name as an imported duplicate.
old = bpy.data.node_groups.new("PreserveCollision", "ShaderNodeTree")
similar = bpy.data.node_groups.new("PreserveCollisionX", "ShaderNodeTree")
duplicate = bpy.data.node_groups.new("PreserveCollision.001", "ShaderNodeTree")
owner = bpy.data.node_groups.new("PreserveCollisionOwner", "ShaderNodeTree")
instance = owner.nodes.new("ShaderNodeGroup")
instance.node_tree = duplicate
material_compat._reuse_existing_groups({old, similar, duplicate}, {old.name: old})
assert instance.node_tree == old
assert bpy.data.node_groups.get(similar.name) == similar
assert bpy.data.node_groups.get(old.name) == old

# Explicit updates stay explicit, and a failed import cannot leak append mode.
def fail_import(force_update=False):
    assert material_compat._PRESERVE_IMPORT == (not force_update)
    raise RuntimeError("expected failure")

wrapped = material_compat._import_wrapper(fail_import)
for forced in (False, True):
    try:
        wrapped(force_update=forced)
    except RuntimeError as exc:
        assert str(exc) == "expected failure"
    else:
        raise AssertionError("Import error was swallowed")
    assert material_compat._PRESERVE_IMPORT is False

# Persistence check includes the Bridge's native load/save handlers, with an
# isolated registry so the test never publishes to a real shared profile.
blend_path = Path(temporary.name) / "prism-preserved.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
bpy.ops.wm.open_mainfile(filepath=str(blend_path))
restored = bpy.data.materials["M_HT_Existing_Prism_Test"]
assert restored.htue_settings.initialized
assert tuple(restored.htue_settings.base_color) == base_color
assert restored["user_payload"] == "preserve this"
assert any(n.type == "TEX_IMAGE" and n.image and n.image.name == "Existing Painted Texture"
           for n in restored.node_tree.nodes)
assert bpy.data.objects["Existing_Material_User"].data.materials[0] == restored
restored_original = bpy.data.node_groups[restored[schema.ORIGINAL_SHADER_GROUP_PROPERTY]]
assert restored_original["existing_data_marker"] == "KEEP"
assert abs(restored_original.nodes["Existing User Value"].outputs[0].default_value - 0.731) < 1e-6
assert len(bpy.data.objects["New_Prism_Guide_curve"].ht_props.hair_nodes.hair_systems) == 1
print("PRISM_SAVE_RELOAD_AND_RUNTIME_HOOKS_OK")
