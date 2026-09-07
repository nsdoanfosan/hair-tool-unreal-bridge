"""Preserve existing Hair Tool dependencies during ordinary library appends.

Hair Tool's importer deduplicates by remapping *existing* node groups to the
new library copies and deleting the existing IDs. Creating a Prism Profile can
therefore reset shaders used elsewhere, including the Bridge's original shader.
Ordinary imports reuse unambiguous dependencies. Explicit native resets stay
available for unmanaged data; resets that would destroy a Bridge material or
its source shader are rejected before modification. No vendor files change.
"""

from functools import wraps
import inspect
import re
import sys

import bpy


_PATCHES = []
_CLASS_PATCHES = []
_HOOKS = {}
_MODULE_COUNT = -1
_MODULE_SHAPES = ()
_PRESERVE_IMPORT = False
PREFERRED_DEFAULT_MATERIAL = "M_HT_Default_Material_01"
_HOOK_ATTRIBUTE = "_htue_material_compat_hook"


class ProtectedHairMaterialError(RuntimeError):
    """An explicit Hair Tool reset would destroy configured Bridge data."""


def _is_managed(material):
    return bool(getattr(getattr(material, "htue_settings", None), "initialized", False))


def _protected_groups():
    """Include saved source shaders and every dependency of managed materials."""
    from . import schema

    protected = {}

    def visit(tree, material_name):
        if tree is None or tree.as_pointer() in protected:
            return
        protected[tree.as_pointer()] = (tree, material_name)
        for node in tree.nodes:
            visit(getattr(node, "node_tree", None), material_name)

    for material in bpy.data.materials:
        if not _is_managed(material):
            continue
        visit(material.node_tree, material.name)
        source_name = str(material.get(schema.ORIGINAL_SHADER_GROUP_PROPERTY) or "")
        visit(bpy.data.node_groups.get(source_name), material.name)
    return protected


def _reset_error(material_name, component="material"):
    return ProtectedHairMaterialError(
        f"Cannot reset {component} used by Unreal Bridge material '{material_name}': "
        "this would remove its configured shader or textures. To intentionally "
        "reset it, first use 'Restore Original Hair Tool Nodes' on that material."
    )


def _check_node_reset(node_name):
    group = bpy.data.node_groups.get(node_name)
    if group is not None:
        owner = _protected_groups().get(group.as_pointer())
        if owner is not None:
            raise _reset_error(owner[1], f"node group '{node_name}'")


def preferred_default_material():
    """Reuse PARK's configured Hair material without rebuilding its shader."""
    from . import nodes

    material = bpy.data.materials.get(PREFERRED_DEFAULT_MATERIAL)
    if material is not None and (
        nodes.find_hair_shader(material) or nodes.find_legacy_hair_shader(material)
    ):
        return material
    return None


def _material_import_wrapper(original):
    append = _import_wrapper(original)

    @wraps(original)
    def import_material(old_mat=None, force_update=False):
        preferred = preferred_default_material()
        if old_mat is None and not force_update and preferred is not None:
            return preferred
        if force_update:
            from hair_tool.hair_baking import hair_geometry_nodes_shared as shared

            target = old_mat or bpy.data.materials.get(shared.hair_mat_name)
            if _is_managed(target):
                raise _reset_error(target.name)
        return append(old_mat, force_update)
    return import_material


def _profile_default_wrapper(original, shared):
    @wraps(original)
    def setup(curve_obj, profile_mod):
        preferred = preferred_default_material()
        if preferred is None:
            return original(curve_obj, profile_mod)
        profile_mod.properties.inputs[shared.prof_mat_input_name]["value"] = preferred
    return setup


def _profile_creation_wrapper(original, shared):
    @wraps(original)
    def create(obj):
        was_present = shared.get_profile_mod(obj) is not None
        profile = original(obj)
        preferred = preferred_default_material()
        if was_present or profile is None or preferred is None:
            return profile
        current = profile.properties.inputs[shared.prof_mat_input_name]["value"]
        # A new Profile may inherit HT_Default_Material from last_profile_info
        # without calling setup_profile_mat. Preserve intentional custom choices.
        if current is None or current.name == shared.hair_mat_name:
            profile.properties.inputs[shared.prof_mat_input_name]["value"] = preferred
            for slot in obj.material_slots:
                if current is not None and slot.material == current:
                    if obj.data.users > 1:
                        slot.link = "OBJECT"
                    slot.material = preferred
            shared.assig_hair_mat_to_hsys_owner(profile)
            shared.store_profile_info(bpy.context, obj, data_name="last_profile_info")
        return profile
    return create


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
            candidate = candidates[0]
            # Blender increments an existing suffix: Foo.001 becomes Foo.002,
            # not Foo.001.001. A sibling therefore makes the source ambiguous.
            family = re.sub(r"\.\d{3,}$", "", candidate.name)
            siblings = [name for name in original_groups
                        if re.sub(r"\.\d{3,}$", "", name) == family]
            if len(siblings) == 1:
                replacements.append((new_group, candidate))
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
        if data_type == "node_groups":
            # An explicit update to an unrelated root can append dependencies
            # of a Bridge shader. Keep those imports separate; never replace the
            # existing source/clone dependencies through the native remapper.
            protected = _protected_groups()
            protected_names = {group.name for group, _owner in protected.values()}
            original_ngroups = {
                name: group for name, group in original_ngroups.items()
                if name not in protected_names
            }
        return original(new_ngroups, original_ngroups, data_type)
    return remap


def _import_wrapper(original):
    signature = inspect.signature(original)

    @wraps(original)
    def append(*args, **kwargs):
        global _PRESERVE_IMPORT

        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        if bound.arguments.get("force_update", False) and "node_name" in bound.arguments:
            _check_node_reset(bound.arguments["node_name"])
        previous = _PRESERVE_IMPORT
        _PRESERVE_IMPORT = not bound.arguments.get("force_update", False)
        try:
            return original(*args, **kwargs)
        finally:
            _PRESERVE_IMPORT = previous
    return append


def _hair_modules():
    for name, module in tuple(sys.modules.items()):
        if module is not None and (name == "hair_tool" or name.startswith("hair_tool.")):
            yield module


def _native(function):
    # functools.wraps copies function attributes. The self marker distinguishes
    # our actual wrapper from another integration that wraps it later.
    while inspect.isfunction(function) and getattr(function, _HOOK_ATTRIBUTE, None) is function:
        function = function.__wrapped__
    return function


def _function_key(function):
    if not inspect.isfunction(function):
        return None
    native = _native(function)
    return (native.__module__, native.__qualname__)


def _replacement(original, factory):
    original = _native(original)
    key = _function_key(original)
    previous = _HOOKS.get(original)
    if previous is not None:
        return previous
    replacement = factory(original)
    setattr(replacement, _HOOK_ATTRIBUTE, replacement)
    _HOOKS[original] = replacement
    return replacement


def _record_patch(registry, owner, attribute, original, replacement):
    record = (owner, attribute, original, replacement)
    for index, (old_owner, old_attribute, _old, _new) in enumerate(registry):
        if old_owner is owner and old_attribute == attribute:
            registry[index] = record
            return
    registry.append(record)


def _patch_aliases(original, replacement):
    # Only replace identities we own or have previously wrapped. Metadata alone
    # would also match another add-on's functools.wraps wrapper.
    key = _function_key(original)
    known = {function for function in _HOOKS if _function_key(function) == key}
    known.update(_HOOKS[function] for function in tuple(known))
    known.update((original, replacement))
    for module in _hair_modules():
        for attribute, value in tuple(vars(module).items()):
            if not inspect.isfunction(value) or value not in known:
                continue
            if value is not replacement:
                setattr(module, attribute, replacement)
            _record_patch(_PATCHES, module, attribute, _native(original), replacement)


def _update_material_poll(original):
    @wraps(original)
    def poll(cls, context):
        obj = context.active_object
        if obj is not None and obj.type in {"MESH", "CURVES"}:
            if any(_is_managed(slot.material) for slot in obj.material_slots):
                return True
        return original(cls, context)
    return poll


def _update_material_execute(original):
    @wraps(original)
    def execute(self, context):
        from hair_tool.hair_baking import hair_geometry_nodes_shared as shared

        try:
            # Preflight before the native operator's optional UV/texture resets.
            profile = shared.get_profile_mod(context.active_object)
            material = (profile.properties.inputs[shared.prof_mat_input_name]["value"]
                        if profile else None)
            if self.update_type == "MATERIAL":
                material = material or bpy.data.materials.get(shared.hair_mat_name)
                if _is_managed(material):
                    raise _reset_error(material.name)
            elif self.update_type in {"SHADER", "APPEND"}:
                names = ["HTool_UV", "HTool_Normal"]
                if self.update_type == "SHADER":
                    names.insert(0, "HairShaderMain")
                for name in names:
                    _check_node_reset(name)
            return original(self, context)
        except ProtectedHairMaterialError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
    return execute


def _proxy_creation_wrapper(original):
    @wraps(original)
    def create(name, ref_ob):
        proxy = original(name, ref_ob)
        # Hair Tool reuses GP_EMPTY_MESH for independently generated outputs.
        # Isolate that empty container before native Profile setup edits slots.
        if proxy is not None and proxy.data.users > 1:
            proxy.data = proxy.data.copy()
        return proxy
    return create


def _patch_method(cls, attribute, factory):
    descriptor = vars(cls).get(attribute)
    descriptor_type = type(descriptor)
    original = (descriptor.__func__
                if isinstance(descriptor, (classmethod, staticmethod)) else descriptor)
    if not callable(original):
        return
    native = _native(original)
    replacement = _replacement(native, factory)
    if replacement is original:
        return
    native_descriptor = (descriptor_type(native)
                         if isinstance(descriptor, (classmethod, staticmethod)) else native)
    new_descriptor = (descriptor_type(replacement)
                      if isinstance(descriptor, (classmethod, staticmethod)) else replacement)
    setattr(cls, attribute, new_descriptor)
    _record_patch(_CLASS_PATCHES, cls, attribute, native_descriptor, new_descriptor)


def _install_operator_hooks():
    from hair_tool import curves_from_grid, material_operators

    classes = {material_operators.HTOOL_OT_ImportDefaultHairMat}
    registered = bpy.types.Operator.bl_rna_get_subclass_py("HAIR_OT_load_default_mat")
    if registered is not None:
        classes.add(registered)
    for cls in classes:
        _patch_method(cls, "poll", _update_material_poll)
        _patch_method(cls, "execute", _update_material_execute)
    for cls in vars(curves_from_grid).values():
        if inspect.isclass(cls) and "create_proxy_obj" in vars(cls):
            _patch_method(cls, "create_proxy_obj", _proxy_creation_wrapper)


def install_runtime_integration():
    global _MODULE_COUNT, _MODULE_SHAPES

    # This is called from the existing depsgraph boundary. Healthy operation is
    # just tracked attribute identities and module sizes, never five module-wide
    # function scans on every viewport update.
    if (_PATCHES and len(sys.modules) == _MODULE_COUNT
            and all(sys.modules.get(module.__name__) is module and len(vars(module)) == size
                    for module, size in _MODULE_SHAPES)
            and all(getattr(owner, name, None) is replacement
                    for owner, name, _native_fn, replacement in _PATCHES)
            and all(vars(cls).get(name) is replacement
                    for cls, name, _native_fn, replacement in _CLASS_PATCHES)):
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
    _patch_aliases(remapper, _replacement(remapper, _remap_wrapper))
    _patch_aliases(importers[0], _replacement(importers[0], _material_import_wrapper))
    _patch_aliases(importers[1], _replacement(importers[1], _import_wrapper))
    for name, factory in (
        ("setup_profile_mat", _profile_default_wrapper),
        ("get_set_hair_profile_mod", _profile_creation_wrapper),
    ):
        original = getattr(shared, name, None)
        if callable(original):
            _patch_aliases(original, _replacement(original, lambda fn: factory(fn, shared)))
    _install_operator_hooks()
    # Another add-on may replace one alias with its own wrapper. Preserve it and
    # stop treating that alias as ours for the inexpensive health check.
    _PATCHES[:] = [record for record in _PATCHES
                   if getattr(record[0], record[1], None) is record[3]]
    _MODULE_COUNT = len(sys.modules)
    _MODULE_SHAPES = tuple((module, len(vars(module))) for module in _hair_modules())


def remove_runtime_integration():
    global _MODULE_COUNT, _MODULE_SHAPES

    # Late imports may hold our wrapper without an entry in _PATCHES yet.
    for module in _hair_modules():
        for attribute, value in tuple(vars(module).items()):
            if inspect.isfunction(value) and getattr(value, _HOOK_ATTRIBUTE, None) is value:
                setattr(module, attribute, _native(value))
    for module, attribute, original, replacement in reversed(_PATCHES):
        if getattr(module, attribute, None) is replacement:
            setattr(module, attribute, original)
    _PATCHES.clear()
    for cls, attribute, original, replacement in reversed(_CLASS_PATCHES):
        if vars(cls).get(attribute) is replacement:
            setattr(cls, attribute, original)
    _CLASS_PATCHES.clear()
    _HOOKS.clear()
    _MODULE_COUNT = -1
    _MODULE_SHAPES = ()
