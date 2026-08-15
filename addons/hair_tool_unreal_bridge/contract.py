import json
from pathlib import Path

from . import schema


def persist_material_contract(material):
    settings = getattr(material, "htue_settings", None)
    if settings is None or not settings.initialized:
        return None
    encoded = schema.dumps_contract(material.name, settings)
    material[schema.CONTRACT_PROPERTY] = encoded
    return json.loads(encoded)


def refresh_material_contract(material):
    """Pull live Hair Tool sockets and Deformer colors before export reads JSON."""
    from . import deformer_sync, nodes

    nodes.pull_hair_tool_values(material)
    deformer_result = deformer_sync.sync_system_colors(material)
    data = persist_material_contract(material)
    return data, deformer_result


def material_contract(material):
    value = material.get(schema.CONTRACT_PROPERTY)
    if not value:
        return None
    return schema.loads_contract(value)


def validate_material(material):
    errors = []
    try:
        data = material_contract(material)
    except Exception as exc:
        return [str(exc)]
    if data is None:
        return ["material has no persisted HTUE contract"]
    errors.extend(schema.validate_contract(data))
    for entry in data.get("textures") or []:
        path = Path(str(entry.get("file") or ""))
        if not path.is_file():
            errors.append(f"missing {entry.get('param')}: {path}")
    return errors
