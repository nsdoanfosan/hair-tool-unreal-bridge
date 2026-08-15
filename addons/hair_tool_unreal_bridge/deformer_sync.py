from collections import Counter

import bpy

from . import schema


MAX_SOURCE_OBJECTS = 128
MAX_SAMPLES_PER_OBJECT = 1024
MAX_TOTAL_SAMPLES = 32768


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


def _source_priority(obj):
    active = bpy.context.active_object
    try:
        visible = not obj.hide_viewport and not obj.hide_get()
    except RuntimeError:
        visible = not obj.hide_viewport
    return (
        0 if obj == active else 1,
        0 if visible else 1,
        0 if len(getattr(obj, "modifiers", ())) else 1,
        obj.name,
    )


def has_source_attribute(material, attribute_name):
    for obj in bpy.data.objects:
        if not _uses_material(obj, material):
            continue
        attributes = getattr(getattr(obj, "data", None), "attributes", None)
        if attributes and attributes.get(attribute_name) is not None:
            return True
    return False


def has_ao_source(material):
    """Detect authored AO or Hair Tool's opt-in HT_Mesh_AO generator."""
    for obj in bpy.data.objects:
        if not _uses_material(obj, material):
            continue
        attributes = getattr(getattr(obj, "data", None), "attributes", None)
        if attributes and attributes.get("AO") is not None:
            return True
        for modifier in getattr(obj, "modifiers", ()):
            node_group_name = getattr(getattr(modifier, "node_group", None), "name", "")
            if "HT_Mesh_AO" in node_group_name:
                return True
            try:
                if any(value == "AO" for value in modifier.values() if isinstance(value, str)):
                    return True
            except (AttributeError, TypeError):
                pass
    return False


def _attribute_colors(attribute, indices=None):
    if indices is None:
        indices = range(len(attribute.data))
    else:
        indices = sorted(indices)
    count = len(indices)
    step = max(1, (count + MAX_SAMPLES_PER_OBJECT - 1) // MAX_SAMPLES_PER_OBJECT)
    for index in indices[::step]:
        item = attribute.data[index]
        color = getattr(item, "color", None)
        if color is None or len(color) < 4:
            continue
        yield tuple(float(component) for component in color[:4])


def _material_point_indices(obj, material, attribute):
    """Limit POINT-domain mesh colors to faces using this material instance."""
    data = getattr(obj, "data", None)
    if (
        getattr(obj, "type", None) != "MESH"
        or getattr(attribute, "domain", None) != "POINT"
        or data is None
    ):
        return None
    slot_indices = {
        index
        for index, slot in enumerate(getattr(obj, "material_slots", ()))
        if _same_material(slot.material, material)
    }
    if not slot_indices:
        return set()
    if len(slot_indices) == len(getattr(obj, "material_slots", ())):
        return None
    result = set()
    for polygon in data.polygons:
        if polygon.material_index in slot_indices:
            result.update(polygon.vertices)
    return result


def _dominant_colors(sources, material):
    counters = (Counter(), Counter())
    samples = [0, 0]
    total = 0
    for obj in sources[:MAX_SOURCE_OBJECTS]:
        attributes = getattr(getattr(obj, "data", None), "attributes", None)
        attribute = attributes.get("SystemColor") if attributes else None
        if attribute is None:
            continue
        indices = _material_point_indices(obj, material, attribute)
        if indices == set():
            continue
        for color in _attribute_colors(attribute, indices):
            class_index = 0 if color[3] < 0.5 else 1
            rgb = tuple(round(component, 5) for component in color[:3])
            counters[class_index][rgb] += 1
            samples[class_index] += 1
            total += 1
            if total >= MAX_TOTAL_SAMPLES:
                break
        if total >= MAX_TOTAL_SAMPLES:
            break
    colors = []
    distinct = []
    for counter in counters:
        colors.append(counter.most_common(1)[0][0] if counter else None)
        distinct.append(len(counter))
    return colors, tuple(samples), distinct


def _evaluated_system_sources(material, candidates):
    """Prefer Deformer-evaluated output over stale converted/raw backup meshes."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    generated = []
    fallback = []
    for obj in sorted(candidates, key=_source_priority):
        evaluated = obj.evaluated_get(depsgraph)
        if not _uses_material(evaluated, material):
            continue
        attributes = getattr(getattr(evaluated, "data", None), "attributes", None)
        if not attributes or attributes.get("SystemColor") is None:
            continue
        raw_attributes = getattr(getattr(obj, "data", None), "attributes", None)
        if raw_attributes is None or raw_attributes.get("SystemColor") is None:
            generated.append(evaluated)
        else:
            fallback.append(evaluated)
    return generated or fallback


def sync_system_colors(material, objects=None):
    """Read the two alpha classes written by Hair Tool's Set System Color."""
    if material is None or material.name not in schema.TARGET_TEXTURE_SETS:
        return {"updated": False, "reason": "not a configured Hair Tool material"}
    if objects is None:
        candidates = [obj for obj in bpy.data.objects if _uses_material(obj, material)]
    else:
        candidates = [obj for obj in objects if _uses_material(obj, material)]

    # Hair Tool's Set System Color can be created by Deformer geometry nodes and
    # therefore exist only on evaluated output. Prefer that live result over old
    # converted meshes carrying a stale raw SystemColor attribute.
    scan_objects = _evaluated_system_sources(material, candidates)
    if not scan_objects:
        scan_objects = [
            obj
            for obj in candidates
            if getattr(getattr(obj, "data", None), "attributes", None)
            and obj.data.attributes.get("SystemColor") is not None
        ]
    colors, samples, distinct = _dominant_colors(scan_objects, material)
    settings = material.htue_settings
    updated = []
    for index, rgb in enumerate(colors):
        if rgb is None:
            continue
        field = "system_color_01" if index == 0 else "system_color_02"
        setattr(settings, field, (*rgb, 1.0))
        updated.append(field)
    return {
        "updated": bool(updated),
        "fields": updated,
        "source_objects": len(scan_objects),
        "samples_by_alpha_class": list(samples),
        "distinct_colors_by_alpha_class": distinct,
    }


def sync_all_system_colors():
    return {
        name: sync_system_colors(bpy.data.materials.get(name))
        for name in schema.TARGET_TEXTURE_SETS
        if bpy.data.materials.get(name) is not None
    }
