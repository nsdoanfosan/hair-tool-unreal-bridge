"""Verify the requested default with real Hair Tool Profile creation."""
from pathlib import Path
import runpy

state = runpy.run_path(str(Path(__file__).with_name("blender_prism_material_smoke.py")))
import bpy
import bmesh
from hair_tool.hair_baking import hair_geometry_nodes_shared as shared
from hair_tool.hair_baking.hair_geometry_nodes import HTOOL_OT_TransferHairSystem
from hair_tool_unreal_bridge import material_compat

preferred = bpy.data.materials["M_HT_Existing_Prism_Test"]
preferred.name = material_compat.PREFERRED_DEFAULT_MATERIAL
stock = bpy.data.materials["HT_Default_Material"]
material_ids = {m.as_pointer() for m in bpy.data.materials}
graph_state = state["graph_state"]
shader_state = graph_state(preferred.node_tree)
assert shared.import_default_hair_mat() == preferred
assert shared.import_default_hair_mat(stock) == stock

# The previously created Profile still explicitly owns the stock material.
existing = bpy.data.objects["New_Prism_Guide_curve"]
assert shared.get_set_hair_profile_mod(existing).properties.inputs[shared.prof_mat_input_name]["value"] == stock
shared.store_profile_info(bpy.context, existing, data_name="last_profile_info")

bpy.ops.mesh.primitive_cylinder_add(vertices=8, radius=0.15, depth=1)
prism = bpy.context.object
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
output = bpy.context.object
profile = shared.get_profile_mod(output)
assert profile.properties.inputs[shared.prof_mat_input_name]["value"] == preferred
assert output.active_material == preferred
assert stock not in list(output.data.materials)
assert material_ids.issubset({m.as_pointer() for m in bpy.data.materials})
# Hair Tool may create a plain material for the guide mesh itself; no additional
# stock or configured Hair material may be imported or duplicated.
assert all(not m.name.startswith(("HT_", "M_HT_"))
           for m in bpy.data.materials if m.as_pointer() not in material_ids)
assert graph_state(preferred.node_tree) == shader_state
assert bpy.data.materials.get("HT_Default_Material") == stock

# An explicit custom material inherited by a new Profile remains intentional.
custom = preferred.copy()
custom.name = "Custom_Chosen_Hair"
profile.properties.inputs[shared.prof_mat_input_name]["value"] = custom
assert shared.get_set_hair_profile_mod(output).properties.inputs[shared.prof_mat_input_name]["value"] == custom
preferred.name = "Temporarily_Unavailable_Default"
assert shared.import_default_hair_mat() == stock
preferred.name = material_compat.PREFERRED_DEFAULT_MATERIAL
print("HTUE_PREFERRED_DEFAULT_MATERIAL_OK")
