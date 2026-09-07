"""Verify patched Hair Tool callbacks on twelve real procedural subsystems."""
import addon_utils
import ast
import bpy
import json
from pathlib import Path
import runpy
import sys

assert bpy.app.background
args = sys.argv[sys.argv.index("--") + 1:]
candidate_dir = Path(args[args.index("--candidate-dir") + 1])
repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo / "vendor_patches/hair_tool_4_6_1"))
import patch_sort_hair_mods as sort_patch
import patch_subsystem_indices as index_patch
assert addon_utils.enable("hair_tool", default_set=False, persistent=False)
import hair_tool
from hair_tool.hair_baking import hair_nodes_tree as tree_module
from hair_tool.hair_baking import hair_geometry_nodes_shared as shared


def candidate_methods(path, owner_name, names, native_module, expected_hash):
    content = path.read_bytes()
    assert sort_patch.sha256(content) == expected_hash
    tree = ast.parse(content)
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner_name)
    namespace = dict(vars(native_module))
    for method in owner.body:
        if isinstance(method, ast.FunctionDef) and method.name in names:
            exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
            setattr(getattr(native_module, owner_name), method.name, namespace[method.name])


candidate_methods(candidate_dir / sort_patch.RELATIVE_SOURCE, "HairSystemTree", {"sort_hair_mods"}, tree_module, sort_patch.PATCHED_SHA256)
candidate_methods(candidate_dir / index_patch.RELATIVE_SOURCE, "HairSystemProps", {"get_node_val", "set_node_val"}, hair_tool.hair_tool_props, index_patch.PATCHED_SHA256)
# The existing Prism fixture registers native RNA/operators and creates verified
# procedural geometry, then saves/reloads only its own temporary .blend.
runpy.run_path(str(Path(__file__).with_name("blender_prism_material_smoke.py")))
output = bpy.data.objects["New_Prism_Guide_curve"]
bpy.ops.object.select_all(action="DESELECT")
output.select_set(True)
bpy.context.view_layer.objects.active = output
output.ht_props.surface_props.active_obj_index = bpy.context.scene.objects.find(output.name)
while len(shared.get_hair_sys_mod_list(output)) < 12:
    assert bpy.ops.object.add_hair_system(mode="FILTER", draw_popup=False) == {"FINISHED"}

modifiers = shared.get_hair_sys_mod_list(output)
systems = output.ht_props.hair_nodes.hair_systems
assert len(modifiers) == len(systems) == 12
for index, modifier in enumerate(modifiers):
    modifier.node_group.nodes[shared.affect_tag_node_name].inputs["Tag"].default_value = f"ORIGINAL_{index}"
for index in range(12):
    assert systems[index].filter_tag == f"ORIGINAL_{index}", (index, systems[index].filter_tag)
systems[10].filter_tag = "UPDATED_TEN"
systems[11].filter_tag = "UPDATED_ELEVEN"
assert systems[0].filter_tag == "ORIGINAL_0"
assert systems[1].filter_tag == "ORIGINAL_1"
assert systems[10].filter_tag == "UPDATED_TEN"
assert systems[11].filter_tag == "UPDATED_ELEVEN"
assert modifiers[10].node_group.nodes[shared.affect_tag_node_name].inputs["Tag"].default_value == "UPDATED_TEN"
assert modifiers[11].node_group.nodes[shared.affect_tag_node_name].inputs["Tag"].default_value == "UPDATED_ELEVEN"
assert len(shared.get_hair_sys_mod_list(output)) == len(systems) == 12
for index, modifier in enumerate(shared.get_hair_sys_mod_list(output)):
    assert len(systems[index].deformers) == len(shared.get_deformer_nodes_list(modifier.node_group, with_setup_node=True))
print("HTUE_VENDOR_SUBSYSTEM_INDICES_SMOKE_OK=" + json.dumps({"subsystems": 12, "read_indices": list(range(12)), "written_indices": [10, 11], "unchanged_indices": [0, 1], "metadata_aligned": True, "candidate_sha256": index_patch.PATCHED_SHA256}))
