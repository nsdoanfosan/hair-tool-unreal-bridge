import json

import bpy


COMBINED_PREVIEW_PROPERTY = "_htue_combined_ao_preview"
COMBINED_PREVIEW_ROOT_PROPERTY = "_htue_combined_ao_root"
COMBINED_PREVIEW_SOURCES_PROPERTY = "_htue_combined_ao_sources"
PREVIEW_COLLECTION_NAME = "HTUE Combined AO Preview"
DEFAULT_EXPORT_COLLECTION_NAME = "Export"
EXPORT_TARGET_PROPERTY = "_htue_export_target"
EXPORT_LINK_ADDED_PROPERTY = "_htue_export_link_added"
AO_MODIFIER_FIELDS = {
    "samples": ("Input_3", 8),
    "base_color_value": ("Input_13", 0.0),
    "spread_angle": ("Input_4", 1.0471975512),
    "blur_steps": ("Input_8", 1),
    "first_bounce_factor": ("Input_14", 0.6),
    "second_bounce_factor": ("Input_15", 0.4),
    "use_custom_normals": ("Socket_0", False),
}


def is_hair_tool_output(obj):
    """Return whether *obj* is an editable Hair Tool output object."""
    if obj is None or obj.type not in {"CURVES", "MESH"}:
        return False
    node_group_names = {
        modifier.node_group.name
        for modifier in obj.modifiers
        if modifier.type == "NODES" and modifier.node_group is not None
    }
    normalized = set()
    for modifier in obj.modifiers:
        if modifier.type != "NODES":
            continue
        normalized.add(
            str(modifier.name).strip().replace(" ", "_").casefold()
        )
        if modifier.node_group is not None:
            normalized.add(
                modifier.node_group.name.strip().replace(" ", "_").casefold()
            )
    if "edit_mesh" in normalized:
        return False
    return (
        any(name.startswith("Hair_System_Setup") for name in node_group_names)
        and any(name.startswith("Hair_System_Profile") for name in node_group_names)
    )


def selected_hair_tool_outputs(context, render_only=True):
    return [
        obj
        for obj in context.selected_objects
        if is_hair_tool_output(obj)
        and not obj.get(COMBINED_PREVIEW_PROPERTY)
        and (
            not render_only
            or (
                not obj.hide_render
                and obj.visible_get(view_layer=context.view_layer)
            )
        )
    ]


def export_collection_name():
    try:
        from send2ue.constants import ToolInfo
        return str(ToolInfo.EXPORT_COLLECTION.value)
    except (ImportError, AttributeError):
        return DEFAULT_EXPORT_COLLECTION_NAME


def export_collection():
    return bpy.data.collections.get(export_collection_name())


def export_empties():
    collection = export_collection()
    if collection is None:
        return []
    return sorted(
        (
            obj
            for obj in collection.objects
            if obj.type == "EMPTY"
        ),
        key=lambda obj: obj.name.casefold(),
    )


def assigned_export_target(obj):
    target = obj.get(EXPORT_TARGET_PROPERTY)
    if not isinstance(target, bpy.types.Object) or target.type != "EMPTY":
        return None
    collection = export_collection()
    if collection is None or target.name not in collection.objects:
        return None
    return target


def inherited_export_target(obj):
    collection = export_collection()
    if collection is None:
        return None
    exported = set(collection.all_objects)
    parent = obj.parent
    while parent is not None:
        if parent.type == "EMPTY" and parent in exported:
            return parent
        parent = parent.parent
    return None


def export_target(obj):
    if EXPORT_TARGET_PROPERTY in obj:
        return assigned_export_target(obj)
    return inherited_export_target(obj)


def has_ao_modifier(obj):
    return any(
        modifier.type == "NODES"
        and modifier.node_group is not None
        and modifier.node_group.name.startswith("HT_Mesh_AO")
        for modifier in obj.modifiers
    )


def _same_material(candidate, material):
    return bool(
        candidate
        and (
            candidate == material
            or getattr(candidate, "original", None) == material
            or candidate.name == material.name
        )
    )


def _uses_material(obj, material):
    return any(
        _same_material(slot.material, material)
        for slot in getattr(obj, "material_slots", ())
    )


def has_source_attribute(material, attribute_name):
    for obj in bpy.data.objects:
        if not _uses_material(obj, material):
            continue
        attributes = getattr(getattr(obj, "data", None), "attributes", None)
        if attributes and attributes.get(attribute_name) is not None:
            return True
    return False


def has_evaluated_source_attribute(material, attribute_name):
    """Report an attribute only when Hair Tool actually outputs it to the viewport."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in bpy.data.objects:
        if not _uses_material(obj, material):
            continue
        evaluated = obj.evaluated_get(depsgraph)
        attributes = getattr(getattr(evaluated, "data", None), "attributes", None)
        if attributes and attributes.get(attribute_name) is not None:
            return True
        temporary_mesh = None
        try:
            temporary_mesh = evaluated.to_mesh(
                preserve_all_data_layers=True,
                depsgraph=depsgraph,
            )
            evaluated_attributes = getattr(temporary_mesh, "attributes", None)
            if evaluated_attributes and evaluated_attributes.get(attribute_name) is not None:
                return True
        except RuntimeError:
            pass
        finally:
            if temporary_mesh is not None:
                evaluated.to_mesh_clear()
    return False


def has_ao_source(material):
    """Backward-compatible alias for callers outside the bridge."""
    return has_evaluated_source_attribute(material, "AO")


def _find_export_root(obj):
    """Return the nearest Hair Tool export container above *obj*, if present."""
    if obj is not None and EXPORT_TARGET_PROPERTY in obj:
        return assigned_export_target(obj)
    current = obj
    while current is not None:
        if current.type == "EMPTY":
            return current
        current = current.parent
    return None


def _first_ao_modifier(root):
    collection = export_collection()
    if collection is None:
        return None
    objects = [
        obj
        for obj in collection.objects
        if is_hair_tool_output(obj)
        and not obj.hide_render
        and obj.visible_get()
        and export_target(obj) == root
    ]
    for obj in objects:
        for modifier in getattr(obj, "modifiers", ()):
            node_group = getattr(modifier, "node_group", None)
            if (
                modifier.type == "NODES"
                and node_group is not None
                and node_group.name.startswith("HT_Mesh_AO")
            ):
                return modifier
    return None


def ao_bake_configuration(root):
    settings = root.htue_ao_settings
    if not settings.initialized:
        modifier = _first_ao_modifier(root)
        if modifier is not None:
            for field, (identifier, fallback) in AO_MODIFIER_FIELDS.items():
                value = modifier.get(identifier, fallback)
                setattr(settings, field, value)
        settings.initialized = True
    return {
        "evaluation_mode": settings.evaluation_mode,
        "combined_max_ray_distance": settings.combined_max_ray_distance,
        **{
            field: getattr(settings, field)
            for field in AO_MODIFIER_FIELDS
        },
    }


def _preview_objects(root=None):
    root_name = root.name if root is not None else None
    return [
        obj
        for obj in bpy.data.objects
        if bool(obj.get(COMBINED_PREVIEW_PROPERTY))
        and (
            root_name is None
            or str(obj.get(COMBINED_PREVIEW_ROOT_PROPERTY, "")) == root_name
        )
    ]


def combined_ao_preview_state(context_object=None):
    root = _find_export_root(context_object)
    if root is None and context_object is not None and context_object.get(COMBINED_PREVIEW_PROPERTY):
        root = bpy.data.objects.get(str(context_object.get(COMBINED_PREVIEW_ROOT_PROPERTY, "")))
    previews = _preview_objects(root) if root is not None else []
    preview = previews[0] if previews else None
    stats = {}
    if preview is not None:
        try:
            stats = json.loads(str(preview.get("_htue_combined_ao_stats", "{}")))
        except (TypeError, ValueError):
            stats = {}
    return {
        "root": root.name if root is not None else "",
        "exists": preview is not None,
        "object": preview,
        "stats": stats,
    }


def remove_combined_ao_preview(context_object=None, root=None):
    root = root or _find_export_root(context_object)
    if root is None and context_object is not None and context_object.get(COMBINED_PREVIEW_PROPERTY):
        root = bpy.data.objects.get(str(context_object.get(COMBINED_PREVIEW_ROOT_PROPERTY, "")))
    previews = _preview_objects(root) if root is not None else []
    restored = []
    for preview in previews:
        try:
            source_names = json.loads(
                str(preview.get(COMBINED_PREVIEW_SOURCES_PROPERTY, "[]"))
            )
        except (TypeError, ValueError):
            source_names = []
        for source_state in source_names:
            if isinstance(source_state, str):
                source_state = {"name": source_state}
            source = bpy.data.objects.get(str(source_state.get("name", "")))
            if source is None:
                continue
            source.hide_set(bool(source_state.get("hidden", False)))
            source.hide_render = bool(source_state.get("hide_render", False))
            restored.append(source)
        mesh = preview.data if preview.type == "MESH" else None
        bpy.data.objects.remove(preview, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    if bpy.context.view_layer is not None:
        bpy.context.view_layer.update()
    return {"root": root, "restored": restored, "removed": len(previews)}


def _final_hair_tool_sources(root, hair_tool_export):
    collection = export_collection()
    if collection is None:
        return []
    candidates = [
        obj
        for obj in hair_tool_export._final_export_sources(collection)
        if hair_tool_export._asset_group_key(obj) == root
    ]
    return candidates


def _preview_collection():
    collection = bpy.data.collections.get(PREVIEW_COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(PREVIEW_COLLECTION_NAME)
        bpy.context.scene.collection.children.link(collection)
    return collection


def build_combined_ao_preview(material, context_object=None):
    """Build the same joined-AO mesh used by Send to Unreal for one asset."""
    root = _find_export_root(context_object)
    if root is None:
        raise RuntimeError("Select a Hair Tool object under an exported Empty")

    remove_combined_ao_preview(root=root)
    try:
        from send2ue.core import hair_tool_export
    except ImportError as error:
        raise RuntimeError("Send to Unreal Hair Tool exporter is unavailable") from error

    sources = _final_hair_tool_sources(root, hair_tool_export)
    if not sources:
        raise RuntimeError(f"{root.name}: no visible final Hair Tool outputs found")

    state = {
        "temporary_object_names": set(),
        "temporary_mesh_names": set(),
    }
    ao_configuration = ao_bake_configuration(root)
    preview = None
    source_states = [
        {
            "name": source.name,
            "hidden": bool(source.hide_get()),
            "hide_render": bool(source.hide_render),
        }
        for source in sources
    ]
    try:
        parts = []
        for source in sources:
            parts.extend(
                hair_tool_export._evaluated_mesh_objects(
                    source,
                    state,
                    include_system_ao=(
                        ao_configuration["evaluation_mode"] == "PER_SYSTEM"
                    ),
                    ao_settings=ao_configuration,
                )
            )
        if not parts:
            raise RuntimeError(f"{root.name}: evaluated Hair Tool geometry is empty")

        preview = hair_tool_export._join_objects(parts)
        preview.name = f"{root.name}__HTUE_COMBINED_AO_PREVIEW"
        preview.data.name = preview.name
        if ao_configuration["evaluation_mode"] == "COMBINED":
            hair_tool_export._evaluate_combined_ao(
                preview,
                state,
                ao_settings=ao_configuration,
            )
        else:
            hair_tool_export._preserve_per_system_ao(preview, state)
        preview.data.name = preview.name
        hair_tool_export._remove_empty_material_slots(preview)

        collection = _preview_collection()
        for owner in list(preview.users_collection):
            owner.objects.unlink(preview)
        collection.objects.link(preview)
        world_matrix = preview.matrix_world.copy()
        preview.parent = root
        preview.matrix_parent_inverse = root.matrix_world.inverted_safe()
        preview.matrix_world = world_matrix
        preview[COMBINED_PREVIEW_PROPERTY] = True
        preview[COMBINED_PREVIEW_ROOT_PROPERTY] = root.name
        preview[COMBINED_PREVIEW_SOURCES_PROPERTY] = json.dumps(source_states)
        stats = state.get("ao_stats", {}).get(preview.name, {})
        if not stats:
            # The exporter recorded the name before Blender finalized a suffix.
            stats = next(iter(state.get("ao_stats", {}).values()), {})
        preview["_htue_combined_ao_stats"] = json.dumps(stats)
        preview["_htue_ao_evaluation_mode"] = ao_configuration["evaluation_mode"]
        preview["_htue_ao_bake_settings"] = json.dumps(ao_configuration)
        preview.pop("_htue_combined_ao_preview_stale", None)
        preview.hide_render = False

        for source in sources:
            source.hide_set(True)
            source.hide_render = True

        bpy.ops.object.select_all(action="DESELECT")
        preview.hide_set(False)
        preview.select_set(True)
        bpy.context.view_layer.objects.active = preview
        bpy.context.view_layer.update()
        return {
            "root": root,
            "preview": preview,
            "sources": sources,
            "stats": stats,
        }
    except Exception:
        for source_state in source_states:
            source = bpy.data.objects.get(source_state["name"])
            if source is not None:
                source.hide_set(source_state["hidden"])
                source.hide_render = source_state["hide_render"]
        for object_name in list(state["temporary_object_names"]):
            temporary = bpy.data.objects.get(object_name)
            if temporary is not None:
                mesh = temporary.data if temporary.type == "MESH" else None
                bpy.data.objects.remove(temporary, do_unlink=True)
                if mesh is not None and mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
        raise
