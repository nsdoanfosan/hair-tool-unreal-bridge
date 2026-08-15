import json
from pathlib import Path

from . import schema


def persist_material_contract(material):
    settings = getattr(material, "htue_settings", None)
    if settings is None or not settings.initialized:
        return None
    encoded = schema.dumps_contract(material.name, settings)
    if str(material.get(schema.CONTRACT_PROPERTY) or "") != encoded:
        material[schema.CONTRACT_PROPERTY] = encoded
    return json.loads(encoded)


def refresh_material_contract(material):
    """Persist bridge-owned controls; deformer data travels on evaluated geometry."""
    data = persist_material_contract(material)
    return data, {
        "transport": "evaluated SystemColor.RGB via UV1.RG + UV3.G",
        "system_color_alpha_used": False,
        "deferred_to_mesh_export": True,
    }


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
