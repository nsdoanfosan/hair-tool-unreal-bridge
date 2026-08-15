import bpy


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
