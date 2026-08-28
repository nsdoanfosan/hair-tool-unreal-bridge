"""Low-cost, preview-only Blender geometry for Unreal M_LayerBlend height."""

from __future__ import annotations

from collections import deque
import hashlib
import json
from pathlib import Path

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, StringProperty

from . import layerblend_contract


MODIFIER_NAME = "Unreal M_LayerBlend Height Preview"
MODIFIER_MARKER = "umb_layerblend_height_preview"
GROUP_PREFIX = "UMB_MLayerBlendHeightPreview"
GROUP_OWNER_PROPERTY = "umb_owner_object"
GROUP_MARKER = "umb_layerblend_height_group"
GROUP_SIGNATURE_PROPERTY = "umb_layerblend_height_group_signature"
SOURCE_SIGNATURE_PROPERTY = "umb_layerblend_height_source_signature"
SYNC_ERROR_PROPERTY = "umb_layerblend_height_sync_error"
DEFAULT_UV_MAP = "UVMap"
DEFAULT_COLOR_ATTRIBUTE = "Color"
AUTO_SYNC_DEFER_SECONDS = 0.1
AUTO_SYNC_RETRY_SECONDS = 0.25
AUTO_SYNC_SCHEMA = 2
EXPORT_SUSPEND_KEY = "umb_layerblend_height_auto_sync_suspended"
GPRO_GROUP_NODE_NAMES = {"GPro_Instance", "GPro_RealizeAndProxy"}
GPRO_COLLECTION_INPUT_IDENTIFIERS = ("Socket_2", "Socket_5")
CACHE_COLLECTION_NAME = "UMB Height Preview Cache"
CACHE_COLLECTION_MARKER = "umb_layerblend_height_cache_collection"
CACHE_OBJECT_MARKER = "umb_layerblend_height_cache"
CACHE_SOURCE_PROPERTY = "umb_layerblend_height_source"
CACHE_SOURCE_OBJECT_PROPERTY = "umb_layerblend_height_cache_object"
CACHE_SIGNATURE_PROPERTY = "umb_layerblend_height_cache_signature"
SOURCE_DISPLAY_TYPE_PROPERTY = "umb_layerblend_height_source_display_type"
TEMP_GENERATOR_MARKER = "umb_layerblend_height_temp_generator"

_REPORT_CACHE = {}
_AUTO_SYNC_RUNNING = False
_AUTO_SYNC_PENDING = False
_AUTO_SYNC_LAST_RESULT = {}


def _is_preview_modifier(modifier):
    if modifier.type != "NODES":
        return False
    node_group = getattr(modifier, "node_group", None)
    return bool(
        modifier.get(MODIFIER_MARKER)
        or (node_group and node_group.get(GROUP_MARKER))
        or modifier.name == MODIFIER_NAME
        or modifier.name.startswith(f"{MODIFIER_NAME}.")
    )


def _preview_modifiers(obj):
    return [modifier for modifier in obj.modifiers if _is_preview_modifier(modifier)]


def _preview_modifier(obj):
    modifiers = _preview_modifiers(obj)
    if not modifiers:
        return None
    marked = [modifier for modifier in modifiers if modifier.get(MODIFIER_MARKER)]
    return (marked or modifiers)[-1]


def _is_cached_preview(obj):
    return bool(obj and obj.get(CACHE_OBJECT_MARKER))


def _cached_preview_object(source):
    cached_name = str(source.get(CACHE_SOURCE_OBJECT_PROPERTY) or "")
    cached = bpy.data.objects.get(cached_name) if cached_name else None
    if cached and _is_cached_preview(cached) and cached.parent == source:
        return cached
    for child in source.children:
        if _is_cached_preview(child):
            source[CACHE_SOURCE_OBJECT_PROPERTY] = child.name
            return child
    return None


def _cache_collection(scene, *, create=False):
    collection = next(
        (
            candidate
            for candidate in bpy.data.collections
            if candidate.get(CACHE_COLLECTION_MARKER)
        ),
        None,
    )
    if collection is None and create:
        collection = bpy.data.collections.new(CACHE_COLLECTION_NAME)
        collection[CACHE_COLLECTION_MARKER] = True
        collection.hide_render = True
    if collection is not None and collection.name not in scene.collection.children:
        try:
            scene.collection.children.link(collection)
        except RuntimeError:
            pass
    return collection


def _remember_source_display_type(source):
    if SOURCE_DISPLAY_TYPE_PROPERTY not in source:
        source[SOURCE_DISPLAY_TYPE_PROPERTY] = source.display_type


def _restore_source_display_type(source):
    display_type = str(source.get(SOURCE_DISPLAY_TYPE_PROPERTY) or "")
    if display_type:
        try:
            source.display_type = display_type
        except (TypeError, ValueError):
            pass
        del source[SOURCE_DISPLAY_TYPE_PROPERTY]


def _apply_cached_preview_visibility(source):
    cached = _cached_preview_object(source)
    if cached is None:
        _restore_source_display_type(source)
        return False
    settings = getattr(source, "umb_layerblend_preview", None)
    enabled = bool(settings and settings.enabled)
    fresh = str(cached.get(CACHE_SIGNATURE_PROPERTY) or "") == str(
        source.get(SOURCE_SIGNATURE_PROPERTY) or ""
    )
    should_show = enabled and fresh and source.mode != "EDIT"
    if cached.hide_get() == should_show:
        cached.hide_set(not should_show)
    if should_show:
        _remember_source_display_type(source)
        if source.display_type != "WIRE":
            source.display_type = "WIRE"
    else:
        _restore_source_display_type(source)
    return should_show


def sync_cached_preview_visibility(scene=None):
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None:
        return 0
    updated = 0
    for obj in scene.objects:
        if _is_cached_preview(obj) and obj.parent is not None:
            _apply_cached_preview_visibility(obj.parent)
            updated += 1
    return updated


@persistent
def _on_cache_visibility_depsgraph_update(scene, _depsgraph):
    if not any(_is_cached_preview(obj) for obj in scene.objects):
        return
    sync_cached_preview_visibility(scene)


def _layerblend_slots(obj):
    rows = []
    for slot_index, slot in enumerate(obj.material_slots):
        material = slot.material
        if not layerblend_contract.is_layerblend_material(material):
            continue
        node = (
            material.node_tree.nodes.get(layerblend_contract.HEIGHT_NODE_NAME)
            if material.use_nodes and material.node_tree
            else None
        )
        image = getattr(node, "image", None) if node else None
        rows.append((slot_index, material, image))
    return rows


def _modifier_collection_value(modifier, identifier):
    """Read a Geometry Nodes Collection input across Blender API generations."""
    try:
        value = modifier.properties.inputs[identifier]["value"]
    except (AttributeError, KeyError, TypeError):
        try:
            value = modifier.get(identifier)
        except (AttributeError, TypeError):
            value = None
    return value if isinstance(value, bpy.types.Collection) else None


def _gpro_modifier_collections(modifier):
    node_group = getattr(modifier, "node_group", None)
    if modifier.type != "NODES" or node_group is None:
        return []
    if (
        modifier.name not in GPRO_GROUP_NODE_NAMES
        and node_group.name not in GPRO_GROUP_NODE_NAMES
    ):
        return []

    identifiers = list(GPRO_COLLECTION_INPUT_IDENTIFIERS)
    interface = getattr(node_group, "interface", None)
    for item in getattr(interface, "items_tree", ()):
        if (
            getattr(item, "item_type", None) == "SOCKET"
            and getattr(item, "in_out", None) == "INPUT"
            and getattr(item, "socket_type", None) == "NodeSocketCollection"
        ):
            identifiers.append(item.identifier)

    collections = []
    seen = set()
    for identifier in identifiers:
        collection = _modifier_collection_value(modifier, identifier)
        pointer = collection.as_pointer() if collection else 0
        if collection is not None and pointer not in seen:
            seen.add(pointer)
            collections.append(collection)
    return collections


def _is_gpro_group_host(obj):
    return any(
        modifier.type == "NODES"
        and getattr(modifier, "node_group", None)
        and (
            modifier.name in GPRO_GROUP_NODE_NAMES
            or modifier.node_group.name in GPRO_GROUP_NODE_NAMES
        )
        for modifier in obj.modifiers
    )


def _object_instance_collections(obj):
    collections = []
    seen = set()

    instance_collection = (
        obj.instance_collection
        if obj.instance_type == "COLLECTION" and obj.instance_collection
        else None
    )
    if instance_collection is not None:
        seen.add(instance_collection.as_pointer())
        collections.append(instance_collection)

    for modifier in obj.modifiers:
        for collection in _gpro_modifier_collections(modifier):
            pointer = collection.as_pointer()
            if pointer not in seen:
                seen.add(pointer)
                collections.append(collection)
    return collections


def _scene_sync_objects(scene):
    """Return direct and recursively instanced objects visible through a Scene.

    Group Pro keeps member objects inside unlinked collections and exposes those
    collections through an Empty instance or a GPro Geometry Nodes modifier.
    ``scene.objects`` therefore cannot see those material users on its own.
    """
    direct = list(scene.objects)
    direct_pointers = {obj.as_pointer() for obj in direct}
    queued_pointers = set(direct_pointers)
    seen_collection_pointers = set()
    group_pro_hosts = set()
    queue = deque(direct)
    objects = []

    while queue:
        obj = queue.popleft()
        objects.append(obj)
        collections = _object_instance_collections(obj)
        if _is_gpro_group_host(obj):
            group_pro_hosts.add(obj.as_pointer())
        for collection in collections:
            pointer = collection.as_pointer()
            if pointer in seen_collection_pointers:
                continue
            seen_collection_pointers.add(pointer)
            for member in collection.all_objects:
                member_pointer = member.as_pointer()
                if member_pointer in queued_pointers:
                    continue
                queued_pointers.add(member_pointer)
                queue.append(member)

    return objects, {
        "direct_objects": len(direct),
        "instanced_objects": len(queued_pointers - direct_pointers),
        "instanced_collections": len(seen_collection_pointers),
        "group_pro_hosts": len(group_pro_hosts),
    }


def _report_directory_fingerprint(directory):
    if directory is None or not directory.is_dir():
        return ()
    rows = []
    for path in directory.glob("unreal_tiling_*.json"):
        if path.name.endswith(".spec.json") or path.name.endswith(".transaction.json"):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append((path.name, stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(rows))


def _cached_report(directory, scan_cache=None):
    key = str(directory) if directory is not None else ""
    if scan_cache is not None and key in scan_cache:
        return scan_cache[key]
    fingerprint = _report_directory_fingerprint(directory)
    cached = _REPORT_CACHE.get(key)
    if cached and cached[0] == fingerprint:
        bundle = cached[1]
    else:
        path = layerblend_contract.latest_report(directory) if directory else None
        bundle = (path, layerblend_contract.load_report(path)) if path else (None, None)
        _REPORT_CACHE[key] = (fingerprint, bundle)
    if scan_cache is not None:
        scan_cache[key] = bundle
    return bundle


def _report_for_slots(slots, scan_cache=None):
    candidates = []
    for _slot_index, material, _image in slots:
        directory = layerblend_contract.report_directory_for_material(material)
        if directory is None:
            continue
        report_path, report = _cached_report(directory, scan_cache)
        if report_path is not None and report is not None:
            candidates.append((report_path, report))
    if not candidates:
        raise RuntimeError(
            "No Tiling Material Batch Unreal report was found. Run Unreal Audit first."
        )
    report_path, report = max(
        candidates,
        key=lambda pair: (pair[0].stat().st_mtime_ns, pair[0].name),
    )
    return report_path, report


def _source_signature(obj, slots, report_path, scene):
    settings = obj.umb_layerblend_preview
    try:
        report_stat = report_path.stat()
        report_identity = [str(report_path), report_stat.st_mtime_ns, report_stat.st_size]
    except OSError:
        report_identity = [str(report_path), 0, 0]
    payload = {
        "schema": AUTO_SYNC_SCHEMA,
        "scene_scale_length": float(scene.unit_settings.scale_length),
        "uv_map": _uv_map_name(obj, settings.uv_map),
        "color_attribute": str(settings.color_attribute or DEFAULT_COLOR_ATTRIBUTE),
        "report": report_identity,
        "slots": [
            {
                "slot": slot_index,
                "material": material.name_full,
                "material_library": str(
                    getattr(getattr(material, "library", None), "filepath", "") or ""
                ),
                "height_image": image.name_full if image else None,
                "height_image_path": bpy.path.abspath(image.filepath) if image else None,
            }
            for slot_index, material, image in slots
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()


def _uv_map_name(obj, requested):
    if requested and obj.data.uv_layers.get(requested):
        return requested
    active = obj.data.uv_layers.active
    return active.name if active else DEFAULT_UV_MAP


def _color_range_by_slot(obj, attribute_name):
    modifier = _preview_modifier(obj)
    old_viewport = modifier.show_viewport if modifier else None
    if modifier:
        modifier.show_viewport = False
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        try:
            attribute = mesh.color_attributes.get(attribute_name)
            if attribute is None:
                return {}
            values = {}
            if attribute.domain == "CORNER":
                for polygon in mesh.polygons:
                    target = values.setdefault(polygon.material_index, [])
                    target.extend(
                        float(attribute.data[index].color[0])
                        for index in polygon.loop_indices
                    )
            elif attribute.domain == "POINT":
                for polygon in mesh.polygons:
                    target = values.setdefault(polygon.material_index, [])
                    target.extend(
                        float(attribute.data[index].color[0])
                        for index in polygon.vertices
                    )
            return {
                slot: {"minimum": min(rows), "maximum": max(rows)}
                for slot, rows in values.items()
                if rows
            }
        finally:
            evaluated.to_mesh_clear()
    finally:
        if modifier:
            modifier.show_viewport = old_viewport


def _new_math(nodes, name, operation, x, y):
    node = nodes.new("ShaderNodeMath")
    node.name = name
    node.label = name
    node.operation = operation
    node.location = (x, y)
    return node


def _group_signature(material_entries, uv_map, color_attribute):
    payload = {
        "schema": AUTO_SYNC_SCHEMA,
        "uv_map": uv_map,
        "color_attribute": color_attribute,
        "materials": [
            {
                "slot_index": entry["slot_index"],
                "material": entry["material"],
                "image": entry["image"].name_full,
                "image_library": str(
                    getattr(getattr(entry["image"], "library", None), "filepath", "") or ""
                ),
                "height_strength": entry["height_strength"],
                "master_height": entry["master_height"],
                "use_vertex_color": entry["use_vertex_color"],
                "center": entry["center"],
                "magnitude_bu": entry["magnitude_bu"],
            }
            for entry in material_entries
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()


def _build_node_group(obj, material_entries, uv_map, color_attribute):
    signature = _group_signature(material_entries, uv_map, color_attribute)
    group_name = f"{GROUP_PREFIX}::{signature[:16]}"
    for existing in bpy.data.node_groups:
        if (
            existing.bl_idname == "GeometryNodeTree"
            and existing.get(GROUP_SIGNATURE_PROPERTY) == signature
        ):
            return existing
    group = bpy.data.node_groups.new(group_name, "GeometryNodeTree")
    group[GROUP_MARKER] = True
    group[GROUP_SIGNATURE_PROPERTY] = signature
    group.color_tag = "GEOMETRY"
    group.description = (
        "Viewport-only approximation of Unreal M_LayerBlend Nanite displacement; "
        "no subdivision is added and export disables this modifier."
    )
    group.interface.new_socket(
        name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
    )
    group.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )

    nodes = group.nodes
    links = group.links
    group_input = nodes.new("NodeGroupInput")
    group_input.location = (-1450, 0)
    group_output = nodes.new("NodeGroupOutput")
    group_output.location = (1200 + 420 * len(material_entries), 0)

    material_index = nodes.new("GeometryNodeInputMaterialIndex")
    material_index.location = (-1450, -260)
    boundaries = nodes.new("GeometryNodeMeshFaceSetBoundaries")
    boundaries.location = (-1230, -260)
    split_edges = nodes.new("GeometryNodeSplitEdges")
    split_edges.location = (-1000, 0)
    links.new(material_index.outputs["Material Index"], boundaries.inputs["Face Group ID"])
    links.new(boundaries.outputs["Boundary Edges"], split_edges.inputs["Selection"])
    links.new(group_input.outputs["Geometry"], split_edges.inputs["Mesh"])

    uv_attribute = nodes.new("GeometryNodeInputNamedAttribute")
    uv_attribute.data_type = "FLOAT_VECTOR"
    uv_attribute.inputs["Name"].default_value = uv_map
    uv_attribute.location = (-1000, -420)

    color_attribute_node = nodes.new("GeometryNodeInputNamedAttribute")
    color_attribute_node.data_type = "FLOAT_COLOR"
    color_attribute_node.inputs["Name"].default_value = color_attribute
    color_attribute_node.location = (-1000, -620)
    separate_color = nodes.new("FunctionNodeSeparateColor")
    separate_color.mode = "RGB"
    separate_color.location = (-780, -620)
    links.new(color_attribute_node.outputs["Attribute"], separate_color.inputs["Color"])
    color_or_one = nodes.new("GeometryNodeSwitch")
    color_or_one.input_type = "FLOAT"
    color_or_one.location = (-540, -620)
    color_or_one.inputs["False"].default_value = 1.0
    links.new(color_attribute_node.outputs["Exists"], color_or_one.inputs["Switch"])
    links.new(separate_color.outputs["Red"], color_or_one.inputs["True"])

    normal = nodes.new("GeometryNodeInputNormal")
    normal.location = (-760, 220)
    geometry_output = split_edges.outputs["Mesh"]

    for order, entry in enumerate(material_entries):
        x = -420 + order * 420
        y = 40
        frame = nodes.new("NodeFrame")
        frame.label = f"Slot {entry['slot_index']} · {entry['material']}"

        image_node = nodes.new("GeometryNodeImageTexture")
        image_node.parent = frame
        image_node.location = (x, y - 320)
        image_node.inputs["Image"].default_value = entry["image"]
        image_node.interpolation = "Linear"
        image_node.extension = "REPEAT"
        links.new(uv_attribute.outputs["Attribute"], image_node.inputs["Vector"])

        height_red = nodes.new("FunctionNodeSeparateColor")
        height_red.parent = frame
        height_red.mode = "RGB"
        height_red.location = (x + 190, y - 320)
        links.new(image_node.outputs["Color"], height_red.inputs["Color"])

        strength = _new_math(nodes, "Height_Strengh", "MULTIPLY", x + 380, y - 320)
        strength.parent = frame
        strength.inputs[1].default_value = entry["height_strength"]
        links.new(height_red.outputs["Red"], strength.inputs[0])

        master_height = _new_math(nodes, "Master Height", "MULTIPLY", x + 570, y - 320)
        master_height.parent = frame
        master_height.inputs[1].default_value = entry["master_height"]
        links.new(strength.outputs[0], master_height.inputs[0])

        raw_height = master_height
        if entry["use_vertex_color"]:
            vertex_scale = _new_math(nodes, "Vertex Color R", "MULTIPLY", x + 760, y - 320)
            vertex_scale.parent = frame
            links.new(master_height.outputs[0], vertex_scale.inputs[0])
            links.new(color_or_one.outputs["Output"], vertex_scale.inputs[1])
            raw_height = vertex_scale

        centered = _new_math(nodes, "Unreal Center", "SUBTRACT", x + 950, y - 320)
        centered.parent = frame
        centered.inputs[1].default_value = entry["center"]
        links.new(raw_height.outputs[0], centered.inputs[0])

        magnitude = _new_math(nodes, "Magnitude to Blender Units", "MULTIPLY", x + 1140, y - 320)
        magnitude.parent = frame
        magnitude.inputs[1].default_value = entry["magnitude_bu"]
        links.new(centered.outputs[0], magnitude.inputs[0])

        scaled_normal = nodes.new("ShaderNodeVectorMath")
        scaled_normal.parent = frame
        scaled_normal.operation = "SCALE"
        scaled_normal.location = (x + 1330, y - 220)
        links.new(normal.outputs["Normal"], scaled_normal.inputs["Vector"])
        links.new(magnitude.outputs[0], scaled_normal.inputs["Scale"])

        selection = nodes.new("FunctionNodeCompare")
        selection.parent = frame
        selection.data_type = "INT"
        selection.operation = "EQUAL"
        selection.location = (x + 1140, y)
        selection.inputs[3].default_value = entry["slot_index"]
        links.new(material_index.outputs["Material Index"], selection.inputs[2])

        set_position = nodes.new("GeometryNodeSetPosition")
        set_position.parent = frame
        set_position.location = (x + 1530, y)
        links.new(geometry_output, set_position.inputs["Geometry"])
        links.new(selection.outputs["Result"], set_position.inputs["Selection"])
        links.new(scaled_normal.outputs["Vector"], set_position.inputs["Offset"])
        geometry_output = set_position.outputs["Geometry"]

    links.new(geometry_output, group_output.inputs["Geometry"])
    return group


def _remove_orphan_group(group, object_name=""):
    if not group or group.users != 0:
        return False
    if not (
        group.get(GROUP_MARKER)
        or (object_name and group.get(GROUP_OWNER_PROPERTY) == object_name)
    ):
        return False
    bpy.data.node_groups.remove(group)
    return True


def _remove_live_preview_modifiers(obj):
    modifiers = _preview_modifiers(obj)
    groups = [getattr(modifier, "node_group", None) for modifier in modifiers]
    for modifier in modifiers:
        obj.modifiers.remove(modifier)
    for group in groups:
        _remove_orphan_group(group, obj.name)
    return len(modifiers)


def remove_cached_preview(source):
    cached = _cached_preview_object(source)
    _restore_source_display_type(source)
    if CACHE_SOURCE_OBJECT_PROPERTY in source:
        del source[CACHE_SOURCE_OBJECT_PROPERTY]
    if cached is None:
        return False
    mesh = cached.data if cached.type == "MESH" else None
    collections = list(cached.users_collection)
    bpy.data.objects.remove(cached, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    for collection in collections:
        if collection.get(CACHE_COLLECTION_MARKER) and not collection.objects:
            bpy.data.collections.remove(collection)
    return True


def _contract_has_effect(data):
    for entry in data.get("materials") or []:
        magnitude = float(entry.get("magnitude_cm", 0.0) or 0.0)
        coefficient = (
            float(entry.get("height_strength", 0.0) or 0.0)
            * float(entry.get("master_height", 0.0) or 0.0)
        )
        center = float(entry.get("center", 0.0) or 0.0)
        if abs(magnitude) > 1.0e-12 and (
            abs(coefficient) > 1.0e-12 or abs(center) > 1.0e-12
        ):
            return True
    return False


def _entries_from_contract(data):
    entries = []
    for material in data.get("materials") or []:
        image_name = str(material.get("height_image") or "")
        image = bpy.data.images.get(image_name)
        if image is None:
            raise RuntimeError(f"Height image is unavailable: {image_name or '<unnamed>'}")
        entry = dict(material)
        entry["image"] = image
        entries.append(entry)
    return entries


def build_cached_preview(source, *, scene=None):
    if source is None or source.type != "MESH":
        raise RuntimeError("Expected a mesh source object.")
    if not source.is_editable:
        raise RuntimeError(f"{source.name} is linked and cannot create a cached preview.")
    if source.mode != "OBJECT":
        raise RuntimeError("Return to Object Mode before building the exact Height preview.")
    scene = scene or bpy.context.scene
    data = sync_preview(source, scene=scene)
    if not _contract_has_effect(data):
        remove_cached_preview(source)
        return {"object": source.name, "zero_effect": True, "vertices": 0, "polygons": 0}

    entries = _entries_from_contract(data)
    settings = source.umb_layerblend_preview
    uv_map = _uv_map_name(source, settings.uv_map)
    color_attribute = str(settings.color_attribute or DEFAULT_COLOR_ATTRIBUTE)
    group = _build_node_group(source, entries, uv_map, color_attribute)
    temporary = None
    generated_mesh = None
    try:
        temporary = source.copy()
        temporary.name = f"__UMB_HEIGHT_BUILD__{source.name}"
        temporary[TEMP_GENERATOR_MARKER] = True
        temporary.hide_viewport = False
        temporary.hide_render = True
        temporary.hide_select = True
        for modifier in list(_preview_modifiers(temporary)):
            temporary.modifiers.remove(modifier)
        scene.collection.objects.link(temporary)
        height_modifier = temporary.modifiers.new("__UMB Exact Height Build", "NODES")
        height_modifier.node_group = group
        height_modifier.show_viewport = True
        height_modifier.show_render = False
        height_modifier.show_in_editmode = False
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = temporary.evaluated_get(depsgraph)
        generated_mesh = bpy.data.meshes.new_from_object(
            evaluated,
            preserve_all_data_layers=True,
            depsgraph=depsgraph,
        )
        if generated_mesh is None:
            raise RuntimeError("Blender did not return an evaluated Height preview mesh.")
    finally:
        if temporary is not None and temporary.name in bpy.data.objects:
            bpy.data.objects.remove(temporary, do_unlink=True)
            bpy.context.view_layer.update()
        _remove_orphan_group(group, source.name)

    generated_mesh.name = f"{source.data.name}__UMBHeightPreviewCache"
    cached = _cached_preview_object(source)
    old_mesh = cached.data if cached and cached.type == "MESH" else None
    if cached is None:
        cached = bpy.data.objects.new(f"{source.name}__UMBHeightPreview", generated_mesh)
        _cache_collection(scene, create=True).objects.link(cached)
    else:
        cached.data = generated_mesh
    if old_mesh is not None and old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)

    world_matrix = source.matrix_world.copy()
    cached.parent = source
    cached.matrix_parent_inverse = source.matrix_world.inverted_safe()
    cached.matrix_world = world_matrix
    cached[CACHE_OBJECT_MARKER] = True
    cached[CACHE_SOURCE_PROPERTY] = source.name
    cached[CACHE_SIGNATURE_PROPERTY] = str(source.get(SOURCE_SIGNATURE_PROPERTY) or "")
    cached.hide_select = True
    cached.hide_render = True
    cached.display_type = "TEXTURED"
    source[CACHE_SOURCE_OBJECT_PROPERTY] = cached.name
    _apply_cached_preview_visibility(source)
    return {
        "object": source.name,
        "cache": cached.name,
        "zero_effect": False,
        "vertices": len(generated_mesh.vertices),
        "polygons": len(generated_mesh.polygons),
    }


def _consolidate_preview_modifiers(obj):
    """Return one preview modifier and remove stale saved or repeated copies.

    Blender does not reliably preserve custom properties on modifiers through
    every duplication/cache workflow.  Fall back to the generated node-group
    marker and stable modifier name, preferring the newest explicitly marked
    modifier when one exists.
    """
    modifiers = _preview_modifiers(obj)
    if not modifiers:
        return None, 0

    keeper = _preview_modifier(obj)
    removed_groups = []
    removed = 0
    for modifier in modifiers:
        if modifier == keeper:
            continue
        removed_groups.append(getattr(modifier, "node_group", None))
        obj.modifiers.remove(modifier)
        removed += 1

    keeper.name = MODIFIER_NAME
    keeper[MODIFIER_MARKER] = True
    keeper.show_in_editmode = False
    keeper.show_on_cage = False
    for group in removed_groups:
        _remove_orphan_group(group, obj.name)
    return keeper, removed


def sync_preview(
    obj,
    *,
    scene=None,
    slots=None,
    report_bundle=None,
    scan_cache=None,
    measure_color_range=False,
):
    if obj is None or obj.type != "MESH":
        raise RuntimeError("Expected an editable mesh object.")
    if not obj.is_editable:
        raise RuntimeError(f"{obj.name} is linked and cannot receive a preview modifier.")
    scene = scene or bpy.context.scene
    slots = slots if slots is not None else _layerblend_slots(obj)
    if not slots:
        raise RuntimeError(f"{obj.name} has no M_LayerBlend tiling material.")

    settings = obj.umb_layerblend_preview
    uv_map = _uv_map_name(obj, settings.uv_map)
    settings.uv_map = uv_map
    color_attribute = str(settings.color_attribute or DEFAULT_COLOR_ATTRIBUTE)
    report_path, report = report_bundle or _report_for_slots(slots, scan_cache)
    color_ranges = (
        _color_range_by_slot(obj, color_attribute) if measure_color_range else {}
    )
    scale_length = max(float(scene.unit_settings.scale_length), 1.0e-9)
    entries = []
    contract_materials = []
    skipped_materials = []
    for slot_index, material, image in slots:
        if image is None:
            skipped_materials.append(
                {
                    "slot_index": slot_index,
                    "material": material.name,
                    "reason": "UEUN_Height image is missing",
                }
            )
            continue
        item = layerblend_contract.instance_item(report, material.name)
        if item is None:
            skipped_materials.append(
                {
                    "slot_index": slot_index,
                    "material": material.name,
                    "reason": f"Material Instance is absent from {report_path.name}",
                }
            )
            continue
        values = layerblend_contract.height_preview_values(item)
        color_range = color_ranges.get(slot_index, {"minimum": 0.0, "maximum": 1.0})
        magnitude_bu = values["magnitude_cm"] / (100.0 * scale_length)
        maximum_multiplier = color_range["maximum"] if values["use_vertex_color"] else 1.0
        maximum_outward_cm = max(
            0.0,
            (
                values["height_strength"]
                * values["master_height"]
                * maximum_multiplier
                - values["center"]
            )
            * values["magnitude_cm"],
        )
        runtime = {
            **values,
            "slot_index": slot_index,
            "material": material.name,
            "image": image,
            "height_image": image.name,
            "height_image_path": bpy.path.abspath(image.filepath),
            "uv_map": uv_map,
            "color_attribute": color_attribute,
            "vertex_color_range": color_range,
            "vertex_color_range_source": (
                "evaluated_geometry" if slot_index in color_ranges else "upper_bound"
            ),
            "magnitude_bu": magnitude_bu,
            "maximum_outward_cm": maximum_outward_cm,
        }
        entries.append(runtime)
        contract_materials.append(
            {key: value for key, value in runtime.items() if key != "image"}
        )

    if not entries:
        reasons = "; ".join(
            f"{row['material']}: {row['reason']}" for row in skipped_materials[:3]
        )
        raise RuntimeError(f"{obj.name} has no previewable M_LayerBlend slot. {reasons}")

    legacy_modifiers_removed = _remove_live_preview_modifiers(obj)

    data = layerblend_contract.build_contract(
        object_name=obj.name,
        report_path=report_path,
        scene_scale_length=scale_length,
        materials=contract_materials,
    )
    data["material_driven_sync"] = True
    data["skipped_materials"] = skipped_materials
    data["cached_preview"] = True
    data["live_geometry_nodes"] = False
    data["legacy_live_modifiers_removed"] = legacy_modifiers_removed
    obj[layerblend_contract.OBJECT_CONTRACT_PROPERTY] = layerblend_contract.dumps_contract(data)
    obj[SOURCE_SIGNATURE_PROPERTY] = _source_signature(obj, slots, report_path, scene)
    if SYNC_ERROR_PROPERTY in obj:
        del obj[SYNC_ERROR_PROPERTY]
    settings.last_report = str(report_path)
    _apply_cached_preview_visibility(obj)
    return data


def remove_preview(obj):
    modifiers_removed = _remove_live_preview_modifiers(obj)
    cache_removed = remove_cached_preview(obj)
    had_contract = layerblend_contract.OBJECT_CONTRACT_PROPERTY in obj
    if layerblend_contract.OBJECT_CONTRACT_PROPERTY in obj:
        del obj[layerblend_contract.OBJECT_CONTRACT_PROPERTY]
    if SOURCE_SIGNATURE_PROPERTY in obj:
        del obj[SOURCE_SIGNATURE_PROPERTY]
    if SYNC_ERROR_PROPERTY in obj:
        del obj[SYNC_ERROR_PROPERTY]
    return bool(modifiers_removed or cache_removed or had_contract)


def sync_scene_previews(scene=None, *, force=False, measure_color_range=False):
    """Synchronize every editable M_LayerBlend mesh in the active Scene."""
    scene = scene or bpy.context.scene
    if scene is None:
        return {
            "scene": None,
            "candidates": 0,
            "synchronized": 0,
            "unchanged": 0,
            "removed": 0,
            "duplicates_removed": 0,
            "legacy_live_modifiers_removed": 0,
            "direct_objects": 0,
            "instanced_objects": 0,
            "instanced_collections": 0,
            "group_pro_hosts": 0,
            "group_pro_hosts_skipped": 0,
            "errors": [],
            "warnings": [],
        }
    sync_objects, traversal = _scene_sync_objects(scene)
    report_cache = {}
    summary = {
        "scene": scene.name,
        "candidates": 0,
        "synchronized": 0,
        "unchanged": 0,
        "removed": 0,
        "duplicates_removed": 0,
        "legacy_live_modifiers_removed": 0,
        "linked_skipped": 0,
        "group_pro_hosts_skipped": 0,
        "errors": [],
        "warnings": [],
        **traversal,
    }
    for obj in sync_objects:
        if obj.type != "MESH":
            continue
        if obj.get(TEMP_GENERATOR_MARKER):
            continue
        live_modifiers = _preview_modifiers(obj)
        summary["duplicates_removed"] += max(0, len(live_modifiers) - 1)
        summary["legacy_live_modifiers_removed"] += _remove_live_preview_modifiers(obj)
        cached = _cached_preview_object(obj)
        if _is_gpro_group_host(obj):
            # The referenced Collection members receive the preview. Applying it
            # again to the host after GPro_Instance would double the displacement.
            summary["group_pro_hosts_skipped"] += 1
            if cached and obj.is_editable:
                remove_preview(obj)
                summary["removed"] += 1
            continue
        slots = _layerblend_slots(obj)
        if not slots:
            if (cached or layerblend_contract.OBJECT_CONTRACT_PROPERTY in obj) and obj.is_editable:
                remove_preview(obj)
                summary["removed"] += 1
            continue
        summary["candidates"] += 1
        if not obj.is_editable:
            summary["linked_skipped"] += 1
            continue
        signature = ""
        try:
            report_bundle = _report_for_slots(slots, report_cache)
            signature = _source_signature(obj, slots, report_bundle[0], scene)
            prior_signature = str(obj.get(SOURCE_SIGNATURE_PROPERTY) or "")
            prior_error = str(obj.get(SYNC_ERROR_PROPERTY) or "")
            if not force and signature == prior_signature and prior_error:
                summary["unchanged"] += 1
                summary["errors"].append(
                    {"object": obj.name, "error": prior_error, "cached": True}
                )
                continue
            if (
                not force
                and signature == prior_signature
                and layerblend_contract.OBJECT_CONTRACT_PROPERTY in obj
            ):
                summary["unchanged"] += 1
                continue
            data = sync_preview(
                obj,
                scene=scene,
                slots=slots,
                report_bundle=report_bundle,
                scan_cache=report_cache,
                measure_color_range=measure_color_range,
            )
            summary["synchronized"] += 1
            for row in data.get("skipped_materials") or []:
                summary["warnings"].append({"object": obj.name, **row})
        except Exception as exc:
            _remove_live_preview_modifiers(obj)
            if signature:
                obj[SOURCE_SIGNATURE_PROPERTY] = signature
            obj[SYNC_ERROR_PROPERTY] = str(exc)
            summary["errors"].append({"object": obj.name, "error": str(exc)})
    global _AUTO_SYNC_LAST_RESULT
    _AUTO_SYNC_LAST_RESULT = summary
    return summary


def notify_materials_synchronized(scene=None, *, immediate=True):
    """Public hook for Tiling Material Batch and other material handoffs."""
    global _AUTO_SYNC_PENDING
    scene = scene or bpy.context.scene
    if scene is not None and not getattr(scene, "umb_layerblend_auto_sync", True):
        return {"scene": scene.name, "disabled": True}
    _REPORT_CACHE.clear()
    if immediate and not _auto_sync_is_suspended():
        _AUTO_SYNC_PENDING = False
        return sync_scene_previews(scene=scene, force=True)
    request_auto_sync()
    return {"requested": True}


def last_auto_sync_result():
    return dict(_AUTO_SYNC_LAST_RESULT)


def _auto_sync_is_suspended():
    return int(bpy.app.driver_namespace.get(EXPORT_SUSPEND_KEY, 0) or 0) > 0


def request_auto_sync():
    """Queue one deferred scene sync, coalescing repeated material notifications."""
    global _AUTO_SYNC_PENDING
    _AUTO_SYNC_PENDING = True
    if not bpy.app.timers.is_registered(auto_sync_timer):
        bpy.app.timers.register(
            auto_sync_timer,
            first_interval=AUTO_SYNC_DEFER_SECONDS,
            persistent=True,
        )


def auto_sync_timer():
    global _AUTO_SYNC_RUNNING, _AUTO_SYNC_PENDING
    if _AUTO_SYNC_RUNNING:
        return AUTO_SYNC_RETRY_SECONDS
    if not _AUTO_SYNC_PENDING:
        return None
    if _auto_sync_is_suspended():
        return AUTO_SYNC_RETRY_SECONDS
    scene = bpy.context.scene
    _AUTO_SYNC_PENDING = False
    if scene is None or not getattr(scene, "umb_layerblend_auto_sync", True):
        return None
    _AUTO_SYNC_RUNNING = True
    try:
        sync_scene_previews(scene=scene)
    except Exception as exc:
        print(f"Unreal Material Bridge automatic M_LayerBlend sync skipped: {exc}")
    finally:
        _AUTO_SYNC_RUNNING = False
    return AUTO_SYNC_DEFER_SECONDS if _AUTO_SYNC_PENDING else None


def register_auto_sync():
    if _on_cache_visibility_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_cache_visibility_depsgraph_update)
    for obj in getattr(bpy.data, "objects", ()):
        if obj.type == "MESH" and obj.is_editable:
            _remove_live_preview_modifiers(obj)
    for collection in list(getattr(bpy.data, "collections", ())):
        if collection.get(CACHE_COLLECTION_MARKER) and not collection.objects:
            bpy.data.collections.remove(collection)
    sync_cached_preview_visibility()
    request_auto_sync()


def unregister_auto_sync():
    global _AUTO_SYNC_PENDING
    if _on_cache_visibility_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_cache_visibility_depsgraph_update)
    if bpy.app.timers.is_registered(auto_sync_timer):
        bpy.app.timers.unregister(auto_sync_timer)
    _AUTO_SYNC_PENDING = False
    _REPORT_CACHE.clear()


def suspend_height_previews():
    bpy.app.driver_namespace[EXPORT_SUSPEND_KEY] = int(
        bpy.app.driver_namespace.get(EXPORT_SUSPEND_KEY, 0) or 0
    ) + 1
    states = []
    for obj in bpy.data.objects:
        for modifier in _preview_modifiers(obj):
            states.append(
                {
                    "kind": "MODIFIER",
                    "object": obj.name,
                    "modifier": modifier.name,
                    "show_viewport": bool(modifier.show_viewport),
                    "show_render": bool(modifier.show_render),
                }
            )
            modifier.show_viewport = False
            modifier.show_render = False
        if _is_cached_preview(obj) and obj.parent is not None:
            states.append(
                {
                    "kind": "CACHE",
                    "object": obj.parent.name,
                    "cache": obj.name,
                    "hidden": bool(obj.hide_get()),
                }
            )
            obj.hide_set(True)
            _restore_source_display_type(obj.parent)
    return states


def restore_height_previews(states):
    restored = []
    try:
        for state in states or []:
            obj = bpy.data.objects.get(str(state.get("object") or ""))
            if state.get("kind") == "CACHE":
                cached = bpy.data.objects.get(str(state.get("cache") or ""))
                if obj is None or cached is None or not _is_cached_preview(cached):
                    continue
                _apply_cached_preview_visibility(obj)
                restored.append(obj.name)
                continue
            modifier = obj.modifiers.get(str(state.get("modifier") or "")) if obj else None
            if modifier is None or not _is_preview_modifier(modifier):
                continue
            modifier.show_viewport = bool(state.get("show_viewport", True))
            modifier.show_render = bool(state.get("show_render", False))
            modifier.show_in_editmode = False
            modifier.show_on_cage = False
            restored.append(obj.name)
    finally:
        count = max(
            0,
            int(bpy.app.driver_namespace.get(EXPORT_SUSPEND_KEY, 0) or 0) - 1,
        )
        if count:
            bpy.app.driver_namespace[EXPORT_SUSPEND_KEY] = count
        else:
            bpy.app.driver_namespace.pop(EXPORT_SUSPEND_KEY, None)
    return restored


def _update_enabled(self, _context):
    obj = self.id_data
    if isinstance(obj, bpy.types.Object):
        _remove_live_preview_modifiers(obj)
        _apply_cached_preview_visibility(obj)


def update_scene_auto_sync(scene, _context):
    if getattr(scene, "umb_layerblend_auto_sync", True):
        notify_materials_synchronized(scene=scene, immediate=False)


class UMB_LayerBlendPreviewSettings(bpy.types.PropertyGroup):
    enabled: BoolProperty(
        name="Show Cached Preview",
        description="Show the manually generated exact Height cache outside Edit Mode",
        default=True,
        update=_update_enabled,
    )
    uv_map: StringProperty(name="UV Map", default=DEFAULT_UV_MAP)
    color_attribute: StringProperty(
        name="Height Scale Attribute", default=DEFAULT_COLOR_ATTRIBUTE
    )
    last_report: StringProperty(default="", options={"HIDDEN"})


class UMB_OT_SyncLayerBlendPreview(bpy.types.Operator):
    bl_idname = "umb.sync_layerblend_height_preview"
    bl_label = "Sync Scene M_LayerBlend Height Data"
    bl_description = (
        "Synchronize Unreal height values and remove legacy live Geometry Nodes previews; "
        "cached meshes are rebuilt separately"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def execute(self, context):
        context.scene.umb_layerblend_auto_sync = True
        try:
            summary = notify_materials_synchronized(
                scene=context.scene,
                immediate=True,
            )
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report(
            {"WARNING"} if summary["errors"] else {"INFO"},
            (
                f"M_LayerBlend material sync: {summary['synchronized']} updated, "
                f"{summary['unchanged']} unchanged, {len(summary['errors'])} unavailable"
            ),
        )
        return {"FINISHED"}


class UMB_OT_BuildLayerBlendCachedPreview(bpy.types.Operator):
    bl_idname = "umb.build_layerblend_cached_preview"
    bl_label = "Build or Refresh Selected Exact Preview"
    bl_description = (
        "Evaluate Unreal Height once into a non-exported mesh cache for selected objects; "
        "the temporary Geometry Nodes generator is removed immediately"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and any(
            obj.type == "MESH" and not _is_cached_preview(obj)
            for obj in context.selected_objects
        )

    def execute(self, context):
        built = []
        zero_effect = []
        errors = []
        for obj in list(context.selected_objects):
            if obj.type != "MESH" or _is_cached_preview(obj) or not _layerblend_slots(obj):
                continue
            try:
                result = build_cached_preview(obj, scene=context.scene)
                if result["zero_effect"]:
                    zero_effect.append(obj.name)
                else:
                    built.append(result)
            except Exception as exc:
                errors.append(f"{obj.name}: {exc}")
        if not built and not zero_effect and not errors:
            self.report({"ERROR"}, "Select at least one editable M_LayerBlend mesh.")
            return {"CANCELLED"}
        if errors:
            self.report({"WARNING"}, f"Built {len(built)} cache(s); {errors[0]}")
        elif zero_effect:
            self.report(
                {"INFO"},
                f"Built {len(built)} cache(s); {len(zero_effect)} object(s) have zero Height effect",
            )
        else:
            polygons = sum(item["polygons"] for item in built)
            self.report({"INFO"}, f"Built {len(built)} exact Height cache(s), {polygons:,} polygons")
        return {"FINISHED"}


class UMB_OT_ClearLayerBlendCachedPreview(bpy.types.Operator):
    bl_idname = "umb.clear_layerblend_cached_preview"
    bl_label = "Clear Selected Preview Cache"
    bl_description = "Remove the generated Height cache while retaining synchronized Unreal data"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and any(
            obj.type == "MESH" and _cached_preview_object(obj) is not None
            for obj in context.selected_objects
        )

    def execute(self, context):
        removed = sum(
            1
            for obj in list(context.selected_objects)
            if obj.type == "MESH" and remove_cached_preview(obj)
        )
        self.report({"INFO"}, f"Removed {removed} cached Height preview(s)")
        return {"FINISHED"}


class UMB_OT_RemoveLayerBlendPreview(bpy.types.Operator):
    bl_idname = "umb.remove_layerblend_height_preview"
    bl_label = "Disable and Remove Scene Height Previews"
    bl_description = (
        "Disable automatic M_LayerBlend synchronization and remove Bridge previews "
        "from the current Scene"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        context.scene.umb_layerblend_auto_sync = False
        removed = 0
        sync_objects, _traversal = _scene_sync_objects(context.scene)
        for obj in sync_objects:
            if obj.type == "MESH" and obj.is_editable and remove_preview(obj):
                removed += 1
        self.report({"INFO"}, f"Removed {removed} Scene height preview(s)")
        return {"FINISHED"}


class UMB_PT_LayerBlendPreview(bpy.types.Panel):
    bl_label = "M_LayerBlend Height Preview"
    bl_idname = "UMB_PT_layerblend_height_preview"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Unreal Bridge"

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        obj = context.object
        layout.prop(scene, "umb_layerblend_auto_sync", text="Material-driven Auto Sync")
        row = layout.row(align=True)
        row.operator("umb.sync_layerblend_height_preview", text="Sync Data", icon="FILE_REFRESH")
        row.operator("umb.remove_layerblend_height_preview", text="Disable & Remove", icon="X")

        summary = last_auto_sync_result()
        if summary and summary.get("scene") == scene.name:
            layout.label(
                text=(
                    f"Scene: {summary.get('candidates', 0)} users · "
                    f"{len(summary.get('errors') or [])} unavailable"
                ),
                icon="CHECKMARK" if not summary.get("errors") else "INFO",
            )
            if summary.get("instanced_objects"):
                layout.label(
                    text=(
                        f"Instances: {summary.get('instanced_objects', 0)} objects · "
                        f"Group Pro hosts {summary.get('group_pro_hosts', 0)}"
                    ),
                    icon="OUTLINER_COLLECTION",
                )

        if not (
            obj
            and obj.type == "MESH"
            and (_layerblend_slots(obj) or _preview_modifier(obj))
        ):
            layout.label(text="Select a user mesh for its preview details", icon="MESH_DATA")
            layout.label(text="Sync is Scene/material based, not selection based")
            return

        settings = obj.umb_layerblend_preview
        object_box = layout.box()
        object_box.label(text=obj.name, icon="OBJECT_DATA")
        layout = object_box
        build_row = layout.row(align=True)
        build_row.operator(
            "umb.build_layerblend_cached_preview",
            text="Build / Refresh Selected",
            icon="MOD_DISPLACE",
        )
        build_row.operator(
            "umb.clear_layerblend_cached_preview",
            text="Clear",
            icon="TRASH",
        )
        layout.prop(settings, "enabled")
        values = layout.box()
        values.use_property_split = True
        values.use_property_decorate = False
        values.prop(settings, "uv_map")
        values.prop(settings, "color_attribute")

        try:
            contract = layerblend_contract.loads_contract(
                obj.get(layerblend_contract.OBJECT_CONTRACT_PROPERTY)
            )
        except Exception as exc:
            contract = None
            layout.label(text=str(exc), icon="ERROR")
        if contract:
            maximum = max(
                (entry.get("maximum_outward_cm", 0.0) for entry in contract["materials"]),
                default=0.0,
            )
            layout.label(text=f"Estimated outward bound: {maximum:.3f} cm", icon="MOD_DISPLACE")
            first = contract["materials"][0]
            layout.label(
                text=f"Unreal Magnitude {first['magnitude_cm']:.3f} cm · Center {first['center']:.3f}"
            )
            layout.label(text=Path(contract["source_report"]).name, icon="FILE_TICK")
            if any(entry.get("scaling_source") != "unreal_report" for entry in contract["materials"]):
                layout.label(text="Run a new Unreal Audit for explicit scaling metadata", icon="INFO")
            skipped = contract.get("skipped_materials") or []
            if skipped:
                layout.label(
                    text=f"{len(skipped)} material slot(s) have no synchronized Height source",
                    icon="INFO",
                )

        cached = _cached_preview_object(obj)
        if cached is not None:
            fresh = str(cached.get(CACHE_SIGNATURE_PROPERTY) or "") == str(
                obj.get(SOURCE_SIGNATURE_PROPERTY) or ""
            )
            layout.label(
                text=(
                    f"Cache: {len(cached.data.polygons):,} polygons"
                    if fresh
                    else "Cache is stale — rebuild after material changes"
                ),
                icon="CHECKMARK" if fresh else "ERROR",
            )
            layout.label(text="Mesh/UV edits require a manual refresh", icon="INFO")
        else:
            layout.label(text="No exact preview cache has been built", icon="MESH_DATA")

        sync_error = str(obj.get(SYNC_ERROR_PROPERTY) or "")
        if sync_error:
            layout.label(text=sync_error[:180], icon="ERROR")

        layout.label(text="Object Mode: frozen exact cache · Edit Mode: original mesh")
        layout.label(text="No live Geometry Nodes · cache is excluded from render/export")


CLASSES = (
    UMB_LayerBlendPreviewSettings,
    UMB_OT_SyncLayerBlendPreview,
    UMB_OT_BuildLayerBlendCachedPreview,
    UMB_OT_ClearLayerBlendCachedPreview,
    UMB_OT_RemoveLayerBlendPreview,
    UMB_PT_LayerBlendPreview,
)
