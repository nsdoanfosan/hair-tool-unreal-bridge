import json

import bpy


COMBINED_PREVIEW_PROPERTY = "_htue_combined_ao_preview"
COMBINED_PREVIEW_ROOT_PROPERTY = "_htue_combined_ao_root"
COMBINED_PREVIEW_SOURCES_PROPERTY = "_htue_combined_ao_sources"
PREVIEW_COLLECTION_NAME = "HTUE Combined AO Preview"
DEFAULT_EXPORT_COLLECTION_NAME = "Export"
EXPORT_TARGET_PROPERTY = "_htue_export_target"
EXPORT_LINK_ADDED_PROPERTY = "_htue_export_link_added"
EXPORT_HIERARCHY_MOVED_PROPERTY = "_htue_export_hierarchy_moved"
EXPORT_ORIGINAL_PARENT_PROPERTY = "_htue_export_original_parent"
BRIDGE_AO_MODIFIER_PROPERTY = "_htue_bridge_ao_modifier"
_AO_INITIALIZATION_ROOTS = set()
AO_MODIFIER_FIELDS = {
    "samples": ("Input_3", 8),
    "base_color_value": ("Input_13", 0.0),
    "spread_angle": ("Input_4", 1.0471975512),
    "blur_steps": ("Input_8", 1),
    "first_bounce_factor": ("Input_14", 0.6),
    "second_bounce_factor": ("Input_15", 0.4),
    "use_custom_normals": ("Socket_0", False),
}
BRIDGE_AO_MODIFIER_NAME = "HTUE HT_Mesh_AO"
GEOMETRY_INPUT_SOCKET_NAMES = {
    "Input_3": "Samples",
    "Input_13": "Base Color Value",
    "Input_4": "Spread Ange",
    "Input_8": "Blur Steps",
    "Input_14": "First Bounce Factor",
    "Input_15": "Second Bounce Factor",
    "Socket_0": "Use Custom Normals",
    "Input_7": "AO Attribute Name",
    "Input_16": "Use AO",
}


def _modifier_input_group(modifier, identifier):
    """Return one Geometry Nodes interface input on Blender 5.2+."""
    try:
        return modifier.properties.inputs[identifier]
    except (AttributeError, KeyError, TypeError):
        inputs = getattr(getattr(modifier, "properties", None), "inputs", None)
        socket_name = GEOMETRY_INPUT_SOCKET_NAMES.get(identifier)
        interface = getattr(getattr(modifier, "node_group", None), "interface", None)
        if inputs is None or socket_name is None or interface is None:
            return None
        for item in interface.items_tree:
            if (
                getattr(item, "item_type", None) == "SOCKET"
                and getattr(item, "in_out", None) == "INPUT"
                and item.name == socket_name
            ):
                try:
                    return inputs[item.identifier]
                except (KeyError, TypeError):
                    return None
        return None


def _modifier_input_get(modifier, identifier, fallback=None):
    """Read a Geometry Nodes input across Blender API generations."""
    input_group = _modifier_input_group(modifier, identifier)
    if input_group is not None:
        try:
            return input_group["value"]
        except (KeyError, TypeError):
            return fallback
    try:
        return modifier.get(identifier, fallback)
    except (AttributeError, TypeError):
        return fallback


def _modifier_input_set(modifier, identifier, value):
    """Write a Geometry Nodes input across Blender API generations."""
    input_group = _modifier_input_group(modifier, identifier)
    if input_group is not None:
        input_group["value"] = value
        return
    if getattr(getattr(modifier, "properties", None), "inputs", None) is not None:
        raise KeyError(
            f'Geometry Nodes input "{identifier}" is unavailable on {modifier.name}'
        )
    modifier[identifier] = value


def _modifier_input_state(modifier, identifier):
    """Capture one Geometry Nodes value for atomic operator rollback."""
    input_group = _modifier_input_group(modifier, identifier)
    if input_group is not None:
        return ("interface", "value" in input_group, input_group.get("value"))
    try:
        return ("legacy", identifier in modifier, modifier.get(identifier))
    except (AttributeError, TypeError):
        return ("missing", False, None)


def _restore_modifier_input(modifier, identifier, state):
    """Restore a value captured by :func:`_modifier_input_state`."""
    storage, existed, value = state
    if storage == "interface":
        input_group = _modifier_input_group(modifier, identifier)
        if input_group is None:
            return
        if existed:
            input_group["value"] = value
        elif "value" in input_group:
            del input_group["value"]
        return
    if storage == "legacy":
        if existed:
            modifier[identifier] = value
        else:
            try:
                if identifier in modifier:
                    del modifier[identifier]
            except (AttributeError, TypeError):
                pass


def _modifier_input_values(modifier):
    """Yield exposed Geometry Nodes values without assuming modifier IDProperties."""
    inputs = getattr(getattr(modifier, "properties", None), "inputs", None)
    if inputs is not None:
        for identifier in dir(inputs):
            if identifier.startswith("_"):
                continue
            try:
                input_group = inputs[identifier]
                if "value" in input_group:
                    yield input_group["value"]
            except (KeyError, TypeError):
                continue
        return
    try:
        for identifier in modifier.keys():
            yield modifier.get(identifier)
    except (AttributeError, TypeError):
        return


def _legacy_bridge_ao_marker(modifier):
    try:
        return bool(modifier.get(BRIDGE_AO_MODIFIER_PROPERTY))
    except (AttributeError, TypeError):
        return False


def _is_bridge_ao_modifier(modifier):
    return bool(
        modifier.type == "NODES"
        and (
            modifier.name == BRIDGE_AO_MODIFIER_NAME
            or modifier.name.startswith(f"{BRIDGE_AO_MODIFIER_NAME}.")
            or _legacy_bridge_ao_marker(modifier)
        )
    )


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


def hair_system_hierarchy_root(obj):
    """Return the top Hair Tool object below its asset Empty."""
    current = obj
    while current is not None and current.parent is not None:
        if current.parent.type == "EMPTY":
            break
        parent = current.parent
        setup_references_parent = any(
            modifier.type == "NODES"
            and modifier.node_group is not None
            and modifier.node_group.name.startswith("Hair_System_Setup")
            and any(value == parent for value in _modifier_input_values(modifier))
            for modifier in current.modifiers
        )
        if not setup_references_parent:
            break
        current = parent
    return current


def move_hair_system_under_empty(obj, target):
    """Move a complete Hair Tool hierarchy while preserving its world transform."""
    root = hair_system_hierarchy_root(obj)
    if root is None:
        raise RuntimeError(f"{obj.name}: Hair Tool hierarchy root is unavailable")
    if target == root or target in root.children_recursive:
        raise RuntimeError(f"{obj.name}: the chosen Empty would create a parent cycle")
    if root.parent == target:
        return root

    if not bool(root.get(EXPORT_HIERARCHY_MOVED_PROPERTY)):
        if root.parent is not None:
            root[EXPORT_ORIGINAL_PARENT_PROPERTY] = root.parent
        root[EXPORT_HIERARCHY_MOVED_PROPERTY] = True
    world_matrix = root.matrix_world.copy()
    root.parent = target
    root.matrix_world = world_matrix
    return root


def restore_hair_system_hierarchy(root):
    """Restore a hierarchy moved by :func:`move_hair_system_under_empty`."""
    if root is None or not bool(root.get(EXPORT_HIERARCHY_MOVED_PROPERTY)):
        return False
    original_parent = root.get(EXPORT_ORIGINAL_PARENT_PROPERTY)
    if not isinstance(original_parent, bpy.types.Object):
        original_parent = None
    world_matrix = root.matrix_world.copy()
    root.parent = original_parent
    root.matrix_world = world_matrix
    if EXPORT_ORIGINAL_PARENT_PROPERTY in root:
        del root[EXPORT_ORIGINAL_PARENT_PROPERTY]
    if EXPORT_HIERARCHY_MOVED_PROPERTY in root:
        del root[EXPORT_HIERARCHY_MOVED_PROPERTY]
    return True


def has_ao_modifier(obj):
    return bool(ao_modifiers(obj))


def ao_modifiers(obj):
    return [
        modifier
        for modifier in obj.modifiers
        if modifier.type == "NODES"
        and (
            _is_bridge_ao_modifier(modifier)
            or (
                modifier.node_group is not None
                and modifier.node_group.name.startswith("HT_Mesh_AO")
            )
        )
    ]


def bridge_ao_modifiers(obj):
    """Return AO modifiers created by this bridge, independent of group renames."""
    return [
        modifier
        for modifier in obj.modifiers
        if _is_bridge_ao_modifier(modifier)
    ]


def _hair_tool_ao_node_group():
    node_group = bpy.data.node_groups.get("HT_Mesh_AO")
    if node_group is not None:
        return node_group
    try:
        from hair_tool.hair_mesh_helpers import get_node_group

        node_group = get_node_group("HT_Mesh_AO", False)
    except (ImportError, AttributeError, RuntimeError):
        node_group = None
    if node_group is None:
        raise RuntimeError(
            'Hair Tool node group "HT_Mesh_AO" is unavailable; '
            "use Hair Tool > Generate AO (mod) once and relink"
        )
    return node_group


def _apply_ao_settings_to_modifier(modifier, settings):
    for field, (identifier, _fallback) in AO_MODIFIER_FIELDS.items():
        _modifier_input_set(modifier, identifier, getattr(settings, field))
    _modifier_input_set(modifier, "Input_7", "AO")
    _modifier_input_set(modifier, "Input_16", True)
    modifier.show_viewport = True
    modifier.show_render = True


def ensure_per_system_ao_modifier(obj, root):
    """Ensure an assigned output has live Hair Tool AO in Per System mode."""
    settings = root.htue_ao_settings
    modifiers = ao_modifiers(obj)
    bridge_modifiers = bridge_ao_modifiers(obj)
    if settings.evaluation_mode != "PER_SYSTEM":
        for modifier in bridge_modifiers:
            modifier.show_viewport = False
            modifier.show_render = False
        return None
    if bridge_modifiers:
        for modifier in bridge_modifiers:
            _apply_ao_settings_to_modifier(modifier, settings)
        return None
    if modifiers:
        return None

    modifier = obj.modifiers.new(name=BRIDGE_AO_MODIFIER_NAME, type="NODES")
    try:
        modifier.node_group = _hair_tool_ao_node_group()
        try:
            modifier[BRIDGE_AO_MODIFIER_PROPERTY] = True
        except (AttributeError, TypeError):
            pass
        _apply_ao_settings_to_modifier(modifier, settings)
    except Exception:
        obj.modifiers.remove(modifier)
        raise
    return modifier


def sync_per_system_ao_modifiers(root):
    """Push one export Empty's AO controls to its live Hair Tool outputs."""
    settings = root.htue_ao_settings
    collection = export_collection()
    if collection is None:
        return []
    synchronized = []
    for obj in collection.objects:
        if (
            not is_hair_tool_output(obj)
            or export_target(obj) != root
            or EXPORT_TARGET_PROPERTY not in obj
        ):
            continue
        if settings.evaluation_mode != "PER_SYSTEM":
            for modifier in bridge_ao_modifiers(obj):
                modifier.show_viewport = False
                modifier.show_render = False
            synchronized.append(obj)
            continue
        ensure_per_system_ao_modifier(obj, root)
        synchronized.append(obj)
    if synchronized and bpy.context.view_layer is not None:
        bpy.context.view_layer.update()
    return synchronized


def remove_bridge_ao_modifiers(obj):
    removed = 0
    for modifier in bridge_ao_modifiers(obj):
        obj.modifiers.remove(modifier)
        removed += 1
    return removed


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


def _literal_named_attribute(node, attribute_name):
    """Return whether *node* writes one literal named attribute."""
    if node.bl_idname != "GeometryNodeStoreNamedAttribute" or node.mute:
        return False
    name_socket = node.inputs.get("Name")
    return bool(
        name_socket is not None
        and not name_socket.is_linked
        and str(name_socket.default_value) == attribute_name
    )


def _geometry_input_links(node):
    for socket in node.inputs:
        if getattr(socket, "type", None) != "GEOMETRY":
            continue
        yield from socket.links


def _node_tree_output_writes_attribute(node_tree, attribute_name, visiting=None):
    """Trace final Geometry outputs and find a reachable Store Named Attribute."""
    if node_tree is None:
        return False
    visiting = set() if visiting is None else visiting
    pointer = int(node_tree.as_pointer())
    if pointer in visiting:
        return False
    visiting.add(pointer)
    try:
        pending = []
        for output in node_tree.nodes:
            if output.bl_idname != "NodeGroupOutput":
                continue
            pending.extend(link.from_node for link in _geometry_input_links(output))

        visited_nodes = set()
        while pending:
            node = pending.pop()
            node_pointer = int(node.as_pointer())
            if node_pointer in visited_nodes:
                continue
            visited_nodes.add(node_pointer)

            if _literal_named_attribute(node, attribute_name):
                return True
            child_tree = getattr(node, "node_tree", None)
            if (
                child_tree is not None
                and not node.mute
                and _node_tree_output_writes_attribute(
                    child_tree, attribute_name, visiting
                )
            ):
                return True
            pending.extend(link.from_node for link in _geometry_input_links(node))
        return False
    finally:
        visiting.remove(pointer)


def object_outputs_named_attribute(obj, attribute_name):
    """Report a literal attribute producer in the enabled modifier stack."""
    attributes = getattr(getattr(obj, "data", None), "attributes", None)
    if attributes and attributes.get(attribute_name) is not None:
        return True
    return any(
        modifier.type == "NODES"
        and modifier.show_viewport
        and _node_tree_output_writes_attribute(
            getattr(modifier, "node_group", None), attribute_name
        )
        for modifier in getattr(obj, "modifiers", ())
        if getattr(modifier, "node_group", None) is not None
    )


def has_structural_source_attribute(material, attribute_name):
    """Detect direct or Geometry Nodes-produced data without evaluated conversion.

    Blender 5.2 can expose a Curves Geometry Nodes result in the viewport while
    ``evaluated.data.attributes`` remains empty and ``to_mesh`` raises.  The
    final Geometry path is therefore also authoritative evidence for Hair Tool
    Store Named Attribute deformers.
    """
    return any(
        _uses_material(obj, material)
        and object_outputs_named_attribute(obj, attribute_name)
        for obj in bpy.data.objects
    )


def has_evaluated_source_attribute(material, attribute_name):
    """Report an attribute only when Hair Tool actually outputs it to the viewport."""
    if has_structural_source_attribute(material, attribute_name):
        return True
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


def ao_settings_initializing(root):
    return root.as_pointer() in _AO_INITIALIZATION_ROOTS


def ao_bake_settings_state(root):
    settings = root.htue_ao_settings
    return {
        "initialized": bool(settings.initialized),
        "evaluation_mode": settings.evaluation_mode,
        "combined_max_ray_distance": settings.combined_max_ray_distance,
        **{
            field: getattr(settings, field)
            for field in AO_MODIFIER_FIELDS
        },
    }


def restore_ao_bake_settings(root, state):
    settings = root.htue_ao_settings
    pointer = root.as_pointer()
    _AO_INITIALIZATION_ROOTS.add(pointer)
    try:
        settings.evaluation_mode = state["evaluation_mode"]
        settings.combined_max_ray_distance = state["combined_max_ray_distance"]
        for field in AO_MODIFIER_FIELDS:
            setattr(settings, field, state[field])
        settings.initialized = state["initialized"]
    finally:
        _AO_INITIALIZATION_ROOTS.discard(pointer)


def initialize_ao_bake_settings(root):
    """Seed persistent AO settings outside Blender UI draw callbacks."""
    settings = root.htue_ao_settings
    if settings.initialized:
        return settings
    modifier = _first_ao_modifier(root)
    values = {
        field: (
            _modifier_input_get(modifier, identifier, fallback)
            if modifier is not None
            else fallback
        )
        for field, (identifier, fallback) in AO_MODIFIER_FIELDS.items()
    }
    original = {
        field: getattr(settings, field)
        for field in AO_MODIFIER_FIELDS
    }
    pointer = root.as_pointer()
    _AO_INITIALIZATION_ROOTS.add(pointer)
    try:
        for field, value in values.items():
            setattr(settings, field, value)
        settings.initialized = True
    except Exception:
        for field, value in original.items():
            setattr(settings, field, value)
        settings.initialized = False
        raise
    finally:
        _AO_INITIALIZATION_ROOTS.discard(pointer)
    return settings


def initialize_existing_export_ao_settings():
    """Initialize already-linked export roots from Hair Tool AO modifiers."""
    initialized = []
    for root in export_empties():
        settings = getattr(root, "htue_ao_settings", None)
        if settings is None or settings.initialized or _first_ao_modifier(root) is None:
            continue
        try:
            initialize_ao_bake_settings(root)
        except Exception as exc:
            print(f"HTUE AO initialization skipped for {root.name}: {exc}")
        else:
            initialized.append(root)
    return initialized


def ao_bake_configuration(root):
    """Return the effective AO configuration without mutating Blender data."""
    settings = root.htue_ao_settings
    configuration = {
        "evaluation_mode": settings.evaluation_mode,
        "combined_max_ray_distance": settings.combined_max_ray_distance,
        **{
            field: getattr(settings, field)
            for field in AO_MODIFIER_FIELDS
        },
    }
    if settings.initialized:
        return configuration

    modifier = _first_ao_modifier(root)
    if modifier is not None:
        for field, (identifier, fallback) in AO_MODIFIER_FIELDS.items():
            configuration[field] = _modifier_input_get(
                modifier, identifier, fallback
            )
    return configuration


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
