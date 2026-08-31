"""Automatic hub-and-spoke synchronization for Hair Tool material controls."""

import json
from pathlib import Path
import time
import uuid

import bpy

from . import profile_registry, schema


PROFILE_NAMESPACE = uuid.UUID("e62c93dc-68bb-5ee9-9d91-e81bf7ff69cd")
PROFILE_FILENAME = "hair_tool_unreal_profiles.json"
PROFILE_FIELDS = (
    "texture_root",
    "texture_set",
    *schema.VECTOR_FIELDS.keys(),
    *schema.SCALAR_FIELDS.keys(),
)
PUBLISH_DELAY_SECONDS = 0.45
POLL_INTERVAL_SECONDS = 0.75

_APPLYING = set()
_PENDING = {}
_LAST_REGISTRY_STAMPS = {}
_DEFORMER_SOURCES_DIRTY = True


def _material_key(material):
    return int(material.as_pointer())


def _material_from_pending(item):
    material = bpy.data.materials.get(item.get("name", ""))
    if material is None or _material_key(material) != item.get("key"):
        return None
    return material


def is_applying_profile(material):
    return _material_key(material) in _APPLYING


def deterministic_profile_id(material_name):
    target = schema.material_instance_path(str(material_name)).casefold()
    return str(uuid.uuid5(PROFILE_NAMESPACE, target))


def ensure_profile_id(material):
    settings = material.htue_settings
    profile_id = str(settings.profile_id or "").strip()
    if not profile_id:
        profile_id = deterministic_profile_id(material.name)
        settings.profile_id = profile_id
    return profile_id


def registry_path(material):
    settings = material.htue_settings
    configured = str(settings.profile_registry_path or "").strip()
    if configured:
        return Path(bpy.path.abspath(configured)).resolve()
    texture_root = str(settings.texture_root or "").strip()
    if texture_root:
        texture_registry = (
            Path(bpy.path.abspath(texture_root)).resolve().parent / PROFILE_FILENAME
        )
        if texture_registry.is_file():
            return texture_registry
    blend_path = str(bpy.data.filepath or "").strip()
    if not blend_path:
        return None
    return Path(blend_path).resolve().parent / PROFILE_FILENAME


def material_values(material):
    settings = material.htue_settings
    values = {}
    for field in PROFILE_FIELDS:
        value = getattr(settings, field)
        if field in schema.VECTOR_FIELDS:
            values[field] = [float(component) for component in value]
        elif field in schema.BLEND_FIELDS or field in {"texture_root", "texture_set"}:
            values[field] = str(value)
        else:
            values[field] = float(value)
    return values


def _base_values(settings):
    raw = str(settings.profile_base_json or "")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _set_synced_state(material, record):
    settings = material.htue_settings
    settings.profile_revision = int(record.get("revision", 0))
    settings.profile_hash = str(record.get("content_hash") or "")
    settings.profile_base_json = profile_registry.canonical_json(record.get("values") or {})
    settings.profile_sync_status = "SYNCED"
    settings.profile_sync_error = ""


def _set_error(material, status, message):
    settings = material.htue_settings
    settings.profile_sync_status = status
    settings.profile_sync_error = str(message)


def _apply_record(material, record):
    settings = material.htue_settings
    key = _material_key(material)
    _APPLYING.add(key)
    try:
        for field, value in (record.get("values") or {}).items():
            if field not in PROFILE_FIELDS or not hasattr(settings, field):
                continue
            setattr(settings, field, value)
        from . import contract, nodes

        nodes.sync_material(material)
        _set_synced_state(material, record)
        contract.persist_material_contract(material)
    finally:
        _APPLYING.discard(key)


def _registry_stamp(path):
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def pull_material(material, bootstrap=False):
    if not getattr(material.htue_settings, "initialized", False):
        return False
    path = registry_path(material)
    if path is None:
        _set_error(material, "UNAVAILABLE", "Save the .blend or choose a profile registry path")
        return False
    profile_id = ensure_profile_id(material)
    try:
        registry = profile_registry.load_registry(path)
    except profile_registry.RegistryError as exc:
        _set_error(material, "ERROR", exc)
        return False
    record = registry["profiles"].get(profile_id)
    if record is None:
        if bootstrap:
            schedule_publish(material, PROFILE_FIELDS, immediate=True)
        else:
            _set_error(material, "UNAVAILABLE", "Shared profile has not been created yet")
        return False
    expected_hash = profile_registry.values_hash(record.get("values") or {})
    if expected_hash != str(record.get("content_hash") or ""):
        _set_error(material, "ERROR", "Shared profile content hash does not match")
        return False
    settings = material.htue_settings
    if (
        int(settings.profile_revision) == int(record.get("revision", 0))
        and str(settings.profile_hash or "") == expected_hash
    ):
        settings.profile_sync_status = "SYNCED"
        settings.profile_sync_error = ""
        return False
    _apply_record(material, record)
    _LAST_REGISTRY_STAMPS[str(path)] = _registry_stamp(path)
    return True


def _publish_material(material, changed_fields, force=False):
    if not getattr(material.htue_settings, "initialized", False):
        return False
    path = registry_path(material)
    if path is None:
        _set_error(material, "UNAVAILABLE", "Save the .blend or choose a profile registry path")
        return False
    profile_id = ensure_profile_id(material)
    settings = material.htue_settings
    local_values = material_values(material)
    base_values = _base_values(settings)
    base_revision = int(settings.profile_revision)
    changed_fields = {str(field) for field in changed_fields if field in PROFILE_FIELDS}
    if force and not changed_fields:
        changed_fields = set(PROFILE_FIELDS)
    result = {}
    try:
        with profile_registry.registry_lock(path):
            registry = profile_registry.load_registry(path)
            remote = registry["profiles"].get(profile_id)
            if remote is None:
                revision = 1
                values = local_values
            else:
                remote_revision = int(remote.get("revision", 0))
                remote_values = dict(remote.get("values") or {})
                if force or remote_revision == base_revision:
                    values = local_values
                else:
                    values = profile_registry.merge_changed_values(
                        remote_values,
                        base_values,
                        local_values,
                        changed_fields,
                    )
                if values == remote_values:
                    result["record"] = remote
                    result["changed"] = False
                    revision = None
                else:
                    revision = remote_revision + 1
            if revision is not None:
                record = profile_registry.profile_record(
                    profile_id,
                    material.name,
                    revision,
                    values,
                    source={
                        "blend_file": str(bpy.data.filepath or ""),
                        "material": material.name,
                    },
                )
                registry["profiles"][profile_id] = record
                profile_registry.save_registry(path, registry)
                result["record"] = record
                result["changed"] = True
    except profile_registry.ProfileConflictError as exc:
        _set_error(material, "CONFLICT", exc)
        return False
    except profile_registry.RegistryLockError:
        schedule_publish(material, changed_fields, immediate=False)
        return False
    except profile_registry.RegistryError as exc:
        _set_error(material, "ERROR", exc)
        return False
    _set_synced_state(material, result["record"])
    from . import contract

    contract.persist_material_contract(material)
    _LAST_REGISTRY_STAMPS[str(path)] = _registry_stamp(path)
    return bool(result.get("changed"))


def schedule_publish(material, changed_fields, immediate=False):
    if is_applying_profile(material):
        return
    path = registry_path(material)
    if path is None:
        _set_error(material, "UNAVAILABLE", "Save the .blend or choose a profile registry path")
        return
    ensure_profile_id(material)
    key = _material_key(material)
    existing = _PENDING.get(key)
    fields = set(existing.get("fields", ())) if existing else set()
    if isinstance(changed_fields, str):
        fields.add(changed_fields)
    else:
        fields.update(changed_fields)
    _PENDING[key] = {
        "key": key,
        "name": material.name,
        "fields": fields,
        "deadline": time.monotonic() if immediate else time.monotonic() + PUBLISH_DELAY_SECONDS,
    }
    material.htue_settings.profile_sync_status = "PENDING"
    material.htue_settings.profile_sync_error = ""


def flush_material(material, force=False):
    key = _material_key(material)
    item = _PENDING.pop(key, None)
    fields = set(item.get("fields", ())) if item else set()
    if not fields and not force:
        return False
    return _publish_material(material, fields, force=force)


def flush_pending(force=False):
    now = time.monotonic()
    for key, item in list(_PENDING.items()):
        if not force and float(item.get("deadline", 0.0)) > now:
            continue
        _PENDING.pop(key, None)
        material = _material_from_pending(item)
        if material is not None:
            _publish_material(material, item.get("fields", ()))


def ensure_material(material, bootstrap=True):
    if not getattr(material.htue_settings, "initialized", False):
        return False
    ensure_profile_id(material)
    return pull_material(material, bootstrap=bootstrap)


def sync_material_now(material):
    flush_material(material)
    if material.htue_settings.profile_sync_status == "CONFLICT":
        return False
    pulled = pull_material(material, bootstrap=True)
    published = flush_material(material)
    if published:
        pull_material(material, bootstrap=False)
    return bool(pulled or published)


def on_load():
    global _DEFORMER_SOURCES_DIRTY

    _PENDING.clear()
    _LAST_REGISTRY_STAMPS.clear()
    _DEFORMER_SOURCES_DIRTY = True
    for material in bpy.data.materials:
        if getattr(getattr(material, "htue_settings", None), "initialized", False):
            ensure_material(material, bootstrap=True)


def _poll_registries():
    paths = {}
    for material in bpy.data.materials:
        settings = getattr(material, "htue_settings", None)
        if settings is None or not settings.initialized:
            continue
        path = registry_path(material)
        if path is not None:
            paths.setdefault(str(path), []).append(material)
    for path_text, materials in paths.items():
        stamp = _registry_stamp(path_text)
        previous = _LAST_REGISTRY_STAMPS.get(path_text)
        if previous == stamp:
            continue
        _LAST_REGISTRY_STAMPS[path_text] = stamp
        for material in materials:
            if _material_key(material) not in _PENDING:
                pull_material(material, bootstrap=True)


def mark_deformer_sources_dirty(depsgraph=None):
    """Queue one source-presence refresh after relevant Geometry changes."""
    global _DEFORMER_SOURCES_DIRTY

    if depsgraph is None:
        _DEFORMER_SOURCES_DIRTY = True
        return True
    data_types = tuple(
        data_type
        for data_type in (
            getattr(bpy.types, "NodeTree", None),
            getattr(bpy.types, "Mesh", None),
            getattr(bpy.types, "Curves", None),
            getattr(bpy.types, "Curve", None),
        )
        if data_type is not None
    )
    for update in depsgraph.updates:
        data = update.id
        if isinstance(data, data_types) or (
            isinstance(data, bpy.types.Object)
            and bool(getattr(update, "is_updated_geometry", False))
        ):
            _DEFORMER_SOURCES_DIRTY = True
            return True
    return False


def _refresh_deformer_sources_if_dirty():
    global _DEFORMER_SOURCES_DIRTY

    if not _DEFORMER_SOURCES_DIRTY:
        return False
    _DEFORMER_SOURCES_DIRTY = False
    from . import nodes

    changed = False
    for material in bpy.data.materials:
        settings = getattr(material, "htue_settings", None)
        if settings is not None and settings.initialized:
            changed = nodes.refresh_system_attribute_availability(material) or changed
    return changed


def auto_sync_timer():
    flush_pending(force=False)
    _poll_registries()
    _refresh_deformer_sources_if_dirty()
    return POLL_INTERVAL_SECONDS


def register_auto_sync():
    if not bpy.app.timers.is_registered(auto_sync_timer):
        bpy.app.timers.register(auto_sync_timer, first_interval=0.25, persistent=True)


def unregister_auto_sync():
    global _DEFORMER_SOURCES_DIRTY

    _PENDING.clear()
    _LAST_REGISTRY_STAMPS.clear()
    _DEFORMER_SOURCES_DIRTY = True
    if bpy.app.timers.is_registered(auto_sync_timer):
        bpy.app.timers.unregister(auto_sync_timer)
