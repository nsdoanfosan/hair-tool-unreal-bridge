"""Real Hair Tool reset, import, reload and proxy isolation regressions.

Run only in factory-startup background Blender. All blend/registry writes are
inside the inherited temporary fixture; no user preferences are saved.
"""
from pathlib import Path
from functools import wraps
from types import ModuleType, SimpleNamespace
import importlib
import json
import runpy
import sys

state = runpy.run_path(str(Path(__file__).with_name("blender_preferred_material_smoke.py")))
import bpy
from hair_tool import curves_from_grid, material_operators
from hair_tool.hair_baking import hair_geometry_nodes_shared as shared
from hair_tool.utils import general_utils
from hair_tool_unreal_bridge import material_compat, profile_sync, schema

assert bpy.app.background
profile_sync.unregister_auto_sync()
temporary = state["state"]["temporary"]
preferred = bpy.data.materials[material_compat.PREFERRED_DEFAULT_MATERIAL]
stock = bpy.data.materials["HT_Default_Material"]
source = state["output"]
profile = shared.get_profile_mod(source)
profile.properties.inputs[shared.prof_mat_input_name]["value"] = preferred
source.data.materials.clear()
source.data.materials.append(preferred)
bpy.context.view_layer.objects.active = source
source_shader = bpy.data.node_groups[preferred[schema.ORIGINAL_SHADER_GROUP_PROPERTY]]
protected = material_compat._protected_groups()
graph_state = state["state"]["graph_state"]
before_graphs = {pointer: graph_state(tree) for pointer, (tree, _owner) in protected.items()}
before_material = (preferred.as_pointer(), preferred[schema.CONTRACT_PROPERTY],
                   preferred.htue_settings.profile_id, preferred.htue_settings.profile_registry_path,
                   tuple(preferred.htue_settings.base_color), preferred["user_payload"])


def assert_preserved():
    assert before_material == (preferred.as_pointer(), preferred[schema.CONTRACT_PROPERTY],
                               preferred.htue_settings.profile_id, preferred.htue_settings.profile_registry_path,
                               tuple(preferred.htue_settings.base_color), preferred["user_payload"])
    for pointer, (tree, _owner) in protected.items():
        assert graph_state(tree) == before_graphs[pointer], tree.name
    assert source_shader["existing_data_marker"] == "KEEP"
    assert abs(source_shader.nodes["Existing User Value"].outputs[0].default_value - .731) < 1e-6


def rejected(call):
    try:
        call()
    except material_compat.ProtectedHairMaterialError as exc:
        assert "Restore Original Hair Tool Nodes" in str(exc)
    else:
        raise AssertionError("Destructive Bridge reset was allowed")
    assert_preserved()


# Direct API reset must refuse before deleting material IDs or source groups.
rejected(lambda: shared.import_default_hair_mat(preferred, force_update=True))
rejected(lambda: shared.import_ng_base("HairShaderMain", lib_file=shared.MAT_LIB_FILE, force_update=True))
rejected(lambda: shared.import_default_hair_shader(["HairShaderMain", "HTool_UV", "HTool_Normal"]))

# The actual native operator now recognizes a Bridge-only material assignment,
# and reports the reason without applying its earlier optional UV/image reset.
operator = material_operators.HTOOL_OT_ImportDefaultHairMat
assert operator.poll(bpy.context)
uv_owner_before = profile.properties.inputs[shared.uv_owner_input_name]["value"]
for mode in ("MATERIAL", "SHADER", "APPEND"):
    reports = []
    op = SimpleNamespace(update_type=mode, reset_uvs=True, reset_texture=True,
                         report=lambda levels, message: reports.append((levels, message)))
    assert operator.execute(op, bpy.context) == {"CANCELLED"}
    assert len(reports) == 1 and reports[0][0] == {"ERROR"}
    assert "Restore Original Hair Tool Nodes" in reports[0][1]
    assert profile.properties.inputs[shared.uv_owner_input_name]["value"] == uv_owner_before
    assert_preserved()

# An unrelated explicit update still runs, retaining imported copies of any
# protected dependencies rather than resetting globally shared Bridge sources.
root = bpy.data.node_groups.new("HTUE_Unmanaged_Force_Root", "ShaderNodeTree")
root.nodes.new("ShaderNodeGroup").node_tree = source_shader
root["version"] = "library"
root_path = Path(temporary.name) / "force-unmanaged.blend"
bpy.data.libraries.write(str(root_path), {root})
root["version"] = "old"
updated = general_utils.import_node_group(root.name, str(root_path), force_update=True)
assert updated["version"] == "library"
assert updated.nodes[0].node_tree != source_shader
assert_preserved()

# Resetting an unconfigured stock material remains an actual native reset.
stock["native_reset_marker"] = True
stock = shared.import_default_hair_mat(stock, force_update=True)
assert "native_reset_marker" not in stock
assert not stock.htue_settings.initialized
assert_preserved()

# Numeric names cannot identify the library source when existing siblings exist.
base = bpy.data.node_groups.new("HTUE_Numeric_Dependency", "ShaderNodeTree")
base.nodes.new("ShaderNodeValue").outputs[0].default_value = .1
chosen = bpy.data.node_groups.new("HTUE_Numeric_Dependency.001", "ShaderNodeTree")
chosen.nodes.new("ShaderNodeValue").outputs[0].default_value = .9
root = bpy.data.node_groups.new("HTUE_Numeric_Root", "ShaderNodeTree")
root.nodes.new("ShaderNodeGroup").node_tree = chosen
path = Path(temporary.name) / "numeric-dependency.blend"
bpy.data.libraries.write(str(path), {root})
bpy.data.node_groups.remove(root)
imported = general_utils.import_node_group("HTUE_Numeric_Root", str(path))
assert abs(imported.nodes[0].node_tree.nodes[0].outputs[0].default_value - .9) < 1e-6
assert imported.nodes[0].node_tree != base

# Native from_profile creates every proxy through the shared GP_EMPTY_MESH
# factory. Slot writes to the second output must never affect the first.
proxy_class = next(cls for cls in vars(curves_from_grid).values()
                   if isinstance(cls, type) and "create_proxy_obj" in vars(cls))
first = proxy_class.create_proxy_obj("HTUE_Proxy_First", source)
first.data.materials.clear()
first.data.materials.append(preferred)
second = proxy_class.create_proxy_obj("HTUE_Proxy_Second", source)
assert second.data != first.data
second.data.materials[0] = stock
assert first.data.materials[0] == preferred

# Rebind old native aliases, old wrappers, and aliases in modules loaded later.
late = ModuleType("hair_tool._htue_compat_late_test")
sys.modules[late.__name__] = late
old_wrapped = shared.import_default_hair_mat
late.old_native = material_compat._native(old_wrapped)
late.old_wrapper = old_wrapped
importlib.reload(general_utils)
importlib.reload(shared)
importlib.reload(material_operators)
importlib.reload(curves_from_grid)
material_compat.install_runtime_integration()
assert shared.import_default_hair_mat() == preferred
assert late.old_native is shared.import_default_hair_mat
assert late.old_wrapper is shared.import_default_hair_mat
late.new_wrapper = shared.import_default_hair_mat
material_compat.install_runtime_integration()
assert late.new_wrapper is shared.import_default_hair_mat

@wraps(shared.import_default_hair_mat)
def third_party_wrapper(*args, **kwargs):
    return "third party"

late.third_party = third_party_wrapper
late.old_wrapper = third_party_wrapper
material_compat.install_runtime_integration()
assert late.third_party is third_party_wrapper
assert late.old_wrapper is third_party_wrapper
patches = list(material_compat._PATCHES)
class_patches = list(material_compat._CLASS_PATCHES)
material_compat.install_runtime_integration()
assert material_compat._PATCHES == patches
assert material_compat._CLASS_PATCHES == class_patches
scan = material_compat._hair_modules
def no_scan():
    raise AssertionError("Healthy depsgraph install performed a full module scan")
material_compat._hair_modules = no_scan
try:
    for _ in range(100):
        material_compat.install_runtime_integration()
finally:
    material_compat._hair_modules = scan
rejected(lambda: shared.import_default_hair_mat(preferred, force_update=True))

# Registered and freshly reloaded Python operator classes are both guarded.
for cls in (material_operators.HTOOL_OT_ImportDefaultHairMat,
            bpy.types.Operator.bl_rna_get_subclass_py("HAIR_OT_load_default_mat")):
    assert cls.poll(bpy.context)
    reports = []
    op = SimpleNamespace(update_type="MATERIAL", reset_uvs=False, reset_texture=False,
                         report=lambda levels, message: reports.append(message))
    assert cls.execute(op, bpy.context) == {"CANCELLED"}
    assert reports

# Uninstall also handles a wrapper copied after the final installation.
late.untracked_wrapper = shared.import_default_hair_mat
material_compat.remove_runtime_integration()
for module, attribute, native, _replacement in patches:
    assert getattr(module, attribute) is native, (module.__name__, attribute)
for cls, attribute, native, _replacement in class_patches:
    assert vars(cls)[attribute] is native, (cls.__name__, attribute)
assert not getattr(late.untracked_wrapper, material_compat._HOOK_ATTRIBUTE, None)
material_compat.install_runtime_integration()
assert shared.import_default_hair_mat() == preferred
assert_preserved()
print("HTUE_MATERIAL_COMPAT_REGRESSIONS_OK", json.dumps({
    "protected_groups": len(protected), "module_aliases": len(material_compat._PATCHES),
    "class_hooks": len(material_compat._CLASS_PATCHES),
    "checks": ["managed_resets_refused", "native_operator_reports", "native_unmanaged_updates",
               "numeric_dependencies", "proxy_material_isolation", "native_reload_aliases", "uninstall"],
}))
