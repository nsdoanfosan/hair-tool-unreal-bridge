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
