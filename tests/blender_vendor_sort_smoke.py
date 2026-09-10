"""Exercise the prepared vendor method on real Blender modifier/RNA collections."""
import addon_utils
import ast
import bpy
import importlib.util
import json
from pathlib import Path
import sys

assert bpy.app.background, "Run only in disposable background Blender"
args = sys.argv[sys.argv.index("--") + 1:]
candidate = Path(args[args.index("--candidate") + 1])
repo = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hair_tool_sort_patch", repo / "vendor_patches/hair_tool_4_6_1/patch_sort_hair_mods.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)
candidate_bytes = candidate.read_bytes()
assert patch.sha256(candidate_bytes) == patch.PATCHED_SHA256
assert addon_utils.enable("hair_tool", default_set=False, persistent=False)
from hair_tool.hair_baking import hair_nodes_tree as vendor
from hair_tool.hair_baking import hair_geometry_nodes_shared as shared

tree_ast = ast.parse(candidate_bytes)
owner = next(node for node in tree_ast.body if isinstance(node, ast.ClassDef) and node.name == "HairSystemTree")
method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "sort_hair_mods")
namespace = dict(vars(vendor))
exec(compile(ast.Module(body=[method], type_ignores=[]), str(candidate), "exec"), namespace)
candidate_method = namespace["sort_hair_mods"]


class AuditSystem(bpy.types.PropertyGroup):
    marker: bpy.props.StringProperty()
    deformer_index: bpy.props.IntProperty()
    authored_mask: bpy.props.FloatProperty()


class AuditHairNodes(bpy.types.PropertyGroup):
    hair_systems: bpy.props.CollectionProperty(type=AuditSystem)
    system_index: bpy.props.IntProperty()


class AuditProps(bpy.types.PropertyGroup):
    hair_nodes: bpy.props.PointerProperty(type=AuditHairNodes)


for cls in (AuditSystem, AuditHairNodes, AuditProps):
    bpy.utils.register_class(cls)
assert not hasattr(bpy.types.Object, "ht_props"), "Hair Tool native registration is intentionally skipped in background"
bpy.types.Object.ht_props = bpy.props.PointerProperty(type=AuditProps)

cases = []
names = ["Prism Mesh Guide", "Filter.001", "Filter", "Weight", "Filter.002"]
for order in ([4, 3, 2, 1, 0], [1, 2, 3, 4, 0], [2, 0, 4, 1, 3], [0, 1, 2, 3, 4]):
    for active_index in range(5):
        mesh = bpy.data.meshes.new("SortAudit")
        obj = bpy.data.objects.new("SortAudit", mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.modifiers.new("Prefix", "BEVEL")
        groups = []
        for index, name in enumerate(names):
            group = bpy.data.node_groups.new(shared.hairsys_mod_group_name + "_SortAudit", "GeometryNodeTree")
            groups.append(group)
            obj.modifiers.new(name, "NODES").node_group = group
            item = obj.ht_props.hair_nodes.hair_systems.add()
            item.marker = name
            item.deformer_index = index + 2
            item.authored_mask = index / 10
            if index == 1:
                obj.modifiers.new("Between", "BEVEL")
        obj.modifiers.new("Suffix", "BEVEL")
        obj.ht_props.hair_nodes.system_index = active_index
        node_tree = vendor.HairSystemTree.__new__(vendor.HairSystemTree)
        node_tree.nodes = {}
        node_tree.root_nodes = []
        for name in names:
            node_tree.add_node(name, "", "")
        node_tree.root_nodes = [node_tree.nodes[names[index]] for index in order]
        candidate_method(node_tree, obj)
        actual_modifiers = [modifier.name for modifier in shared.get_hair_sys_mod_list(obj)]
        metadata = obj.ht_props.hair_nodes.hair_systems
        assert actual_modifiers == [names[index] for index in order]
        assert actual_modifiers == [item.marker for item in metadata]
        assert metadata[obj.ht_props.hair_nodes.system_index].marker == names[active_index]
        for item in metadata:
            original_index = names.index(item.marker)
            assert item.deformer_index == original_index + 2
            assert abs(item.authored_mask - original_index / 10) < 1e-6
        assert [modifier.name for modifier in obj.modifiers if modifier.type != "NODES"] == ["Prefix", "Between", "Suffix"]
        candidate_method(node_tree, obj)
        assert actual_modifiers == [item.marker for item in metadata]
        assert metadata[obj.ht_props.hair_nodes.system_index].marker == names[active_index]
        cases.append({"order": list(order), "active_before": names[active_index], "active_after": metadata[obj.ht_props.hair_nodes.system_index].marker, "aligned": True})
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh)
        for group in groups:
            bpy.data.node_groups.remove(group)

del bpy.types.Object.ht_props
for cls in (AuditProps, AuditHairNodes, AuditSystem):
    bpy.utils.unregister_class(cls)
addon_utils.disable("hair_tool", default_set=False)
print("HTUE_VENDOR_SORT_SMOKE_OK=" + json.dumps({"cases": len(cases), "aligned": all(case["aligned"] for case in cases), "candidate_sha256": patch.PATCHED_SHA256}))
