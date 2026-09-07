"""Preserve existing Hair Tool dependencies during ordinary library appends.

Hair Tool's importer deduplicates by remapping *existing* node groups to the
new library copies and deleting the existing IDs. Creating a Prism Profile can
therefore reset shaders used elsewhere, including the Bridge's original shader.
Within non-forced imports only, reuse existing groups in the opposite direction.
Explicit Hair Tool updates retain their native behavior. No vendor files change.
"""

from functools import wraps
import inspect
import re
import sys

import bpy


_PATCHES = []
_PRESERVE_IMPORT = False


def _reuse_existing_groups(new_groups, original_groups, data_type="node_groups"):
    """Only remap newly appended IDs, using an exact Blender numeric suffix."""
    originals = {group.as_pointer() for group in original_groups.values()}
    replacements = []
    for new_group in new_groups:
        if new_group is None or new_group.as_pointer() in originals:
            continue
        candidates = [
            old_group for name, old_group in original_groups.items()
            if re.fullmatch(re.escape(name) + r"\.\d{3,}", new_group.name)
            and old_group.bl_idname == new_group.bl_idname
        ]
        if len(candidates) == 1:
            replacements.append((new_group, candidates[0]))
    # Remap first, then remove unused imports. Never delete pre-existing data.
    for new_group, old_group in replacements:
        new_group.user_remap(old_group)
    for new_group, _old_group in replacements:
        if new_group.users == 0 and not new_group.use_fake_user:
            getattr(bpy.data, data_type).remove(new_group)


def _remap_wrapper(original):
    @wraps(original)
    def remap(new_ngroups, original_ngroups, data_type="node_groups"):
        if _PRESERVE_IMPORT and data_type == "node_groups":
            return _reuse_existing_groups(new_ngroups, original_ngroups, data_type)
        return original(new_ngroups, original_ngroups, data_type)
    return remap


def _import_wrapper(original):
    signature = inspect.signature(original)

    @wraps(original)
    def append(*args, **kwargs):
        global _PRESERVE_IMPORT

        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        previous = _PRESERVE_IMPORT
        _PRESERVE_IMPORT = not bound.arguments.get("force_update", False)
        try:
            return original(*args, **kwargs)
        finally:
            _PRESERVE_IMPORT = previous
    return append


def _patch_aliases(original, replacement):
    # Hair Tool uses both direct imports and wildcard aliases of these helpers.
    for name, module in tuple(sys.modules.items()):
        if module is None or not (name == "hair_tool" or name.startswith("hair_tool.")):
            continue
        for attribute, value in tuple(vars(module).items()):
            if value is original:
                setattr(module, attribute, replacement)
                _PATCHES.append((module, attribute, original, replacement))


def install_runtime_integration():
    if _PATCHES:
        return
    try:
        from hair_tool.hair_baking import hair_geometry_nodes_shared as shared
        from hair_tool.utils import general_utils
    except ImportError:
        return
    # Guard API availability before installing any part of the integration.
    importers = (getattr(shared, "import_default_hair_mat", None),
                 getattr(general_utils, "import_node_group", None))
    remapper = getattr(general_utils, "remap_node_groups", None)
    if not callable(remapper) or not all(
        callable(fn) and "force_update" in inspect.signature(fn).parameters
        for fn in importers
    ):
        return
    _patch_aliases(remapper, _remap_wrapper(remapper))
    for importer in importers:
        _patch_aliases(importer, _import_wrapper(importer))


def remove_runtime_integration():
    for module, attribute, original, replacement in reversed(_PATCHES):
        if getattr(module, attribute, None) is replacement:
            setattr(module, attribute, original)
    _PATCHES.clear()
