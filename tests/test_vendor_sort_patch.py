"""Regression for Hair Tool subsystem identity after multi-step reordering."""
import ast
import importlib.util
import itertools
from pathlib import Path
from types import SimpleNamespace
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCH_ROOT = ROOT / "vendor_patches" / "hair_tool_4_6_1"
SPEC = importlib.util.spec_from_file_location("hair_tool_sort_patch", PATCH_ROOT / "patch_sort_hair_mods.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)


class MovableList(list):
    def move(self, old, new):
        self.insert(new, self.pop(old))

    def find(self, name):
        return next((index for index, item in enumerate(self) if item.name == name), -1)


def hair_indices(obj):
    return {item.name: index for index, item in enumerate(item for item in obj.modifiers if item.name.startswith("Hair_"))}


namespace = {"get_hair_sys_mod_name_to_idx": hair_indices, "update_indent_cache": lambda obj, tree: None}
exec(compile(textwrap.dedent((PATCH_ROOT / "sort_hair_mods.pyfrag").read_text()), "sort_hair_mods.pyfrag", "exec"), namespace)
sort_method = namespace["sort_hair_mods"]


def check_reorder(order, active_index, interleaved):
    names = [f"Hair_{index}" for index in range(len(order))]
    modifiers = MovableList(SimpleNamespace(name=name) for name in names)
    modifiers.insert(0, SimpleNamespace(name="Prefix"))
    if interleaved and len(order) > 1:
        modifiers.insert(2, SimpleNamespace(name="Between"))
    modifiers.append(SimpleNamespace(name="Suffix"))
    metadata = MovableList(SimpleNamespace(name=name, authored_mask=index / 10, deformer_index=index) for index, name in enumerate(names))
    original_metadata = {item.name: item for item in metadata}
    hair_nodes = SimpleNamespace(hair_systems=metadata, system_index=active_index)
    obj = SimpleNamespace(modifiers=modifiers, ht_props=SimpleNamespace(hair_nodes=hair_nodes))
    nodes = {name: SimpleNamespace(name=name, order_idx=None) for name in names}
    tree = SimpleNamespace(nodes=nodes, get_sorted_nodes=lambda: [nodes[names[index]] for index in order])
    sort_method(tree, obj)
    expected = [names[index] for index in order]
    assert list(hair_indices(obj)) == expected
    assert [item.name for item in metadata] == expected
    assert metadata[hair_nodes.system_index] is original_metadata[names[active_index]]
    assert all(item is original_metadata[item.name] for item in metadata)
    assert [item.name for item in modifiers if not item.name.startswith("Hair_")] == (["Prefix", "Between", "Suffix"] if interleaved and len(order) > 1 else ["Prefix", "Suffix"])
    # A second sort must not change metadata identity or active selection.
    before = tuple(id(item) for item in metadata), hair_nodes.system_index
    sort_method(tree, obj)
    assert before == (tuple(id(item) for item in metadata), hair_nodes.system_index)


@pytest.mark.parametrize("count", range(1, 6))
@pytest.mark.parametrize("interleaved", [False, True])
def test_all_orders_keep_metadata_and_active_system(count, interleaved):
    for order in itertools.permutations(range(count)):
        for active_index in range(count):
            check_reorder(order, active_index, interleaved)


def test_empty_tree_is_unchanged():
    sort_method(SimpleNamespace(nodes={}), None)


def test_unknown_vendor_revision_is_rejected_before_generation():
    with pytest.raises(ValueError, match="Source hash differs"):
        patch.build_candidate(b"class HairSystemTree:\n    def sort_hair_mods(self, obj):\n        pass\n")


def test_replacement_is_only_one_method():
    tree = ast.parse(textwrap.dedent((PATCH_ROOT / "sort_hair_mods.pyfrag").read_text()))
    assert len(tree.body) == 1
    assert isinstance(tree.body[0], ast.FunctionDef)
    assert tree.body[0].name == "sort_hair_mods"


def test_apply_keeps_verified_backup_and_is_idempotent(tmp_path):
    addon = tmp_path / "installed"
    candidate = tmp_path / "candidate"
    addon.mkdir()
    candidate.mkdir()
    (addon / "__init__.py").write_text("bl_info = {'version': (4, 6, 1)}\n")
    relative = Path("fixture.py")
    original, changed = b"authored = 1\n", b"authored = 2\n"
    (addon / relative).write_bytes(original)
    (candidate / relative).write_bytes(changed)
    options = {"relative_source": relative, "original_hash": patch.sha256(original), "patched_hash": patch.sha256(changed)}
    receipt = patch.apply(addon, candidate, **options)
    assert receipt["status"] == "applied"
    assert Path(receipt["backup"]).read_bytes() == original
    assert (addon / relative).read_bytes() == changed
    assert patch.apply(addon, candidate, **options)["status"] == "already-applied"
    assert len(list(addon.glob("fixture.py.pre-htue-fix-*"))) == 1


def test_apply_rejects_changed_source_without_overwriting_it(tmp_path):
    addon = tmp_path / "installed"
    candidate = tmp_path / "candidate"
    addon.mkdir()
    candidate.mkdir()
    (addon / "__init__.py").write_text("bl_info = {'version': (4, 6, 1)}\n")
    relative = Path("fixture.py")
    authored, changed = b"custom user edits\n", b"expected patch\n"
    (addon / relative).write_bytes(authored)
    (candidate / relative).write_bytes(changed)
    with pytest.raises(ValueError, match="refusing to overwrite"):
        patch.apply(addon, candidate, relative_source=relative, original_hash=patch.sha256(b"vendor original"), patched_hash=patch.sha256(changed))
    assert (addon / relative).read_bytes() == authored
    assert not list(addon.glob("fixture.py.pre-htue-fix-*"))
