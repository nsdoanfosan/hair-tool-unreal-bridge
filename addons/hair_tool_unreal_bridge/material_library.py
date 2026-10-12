"""Receive saved shared hair materials without replacing local assignments."""

from pathlib import Path

import bpy

from . import contract, nodes, profile_registry, profile_sync, schema


SHARED_MATERIAL_NAMES = (
    "M_HT_Default_Material_01",
    "M_HT_Default_Material_short_01",
    "M_HT_Default_Material_short_02",
)
DEFAULT_LIBRARY = schema.DEFAULT_TEXTURE_ROOT.parent / "hair_sibuki_09.blend"


def _source_path(value, source):
    value = str(value or "")
    if value.startswith("//"):
        return (source.parent / value[2:]).resolve()
    return Path(value).resolve()


def _saved_profile(material, name, source):
    settings = material.htue_settings
    if not settings.initialized:
        raise ValueError(f"Saved shared material is not configured: {name}")
    values = profile_sync.material_values(material)
    if values.get("texture_root"):
        values["texture_root"] = str(_source_path(values["texture_root"], source))
    profile_id = str(settings.profile_id or profile_sync.deterministic_profile_id(name))
    if profile_id != profile_sync.deterministic_profile_id(name):
        raise ValueError(f"Saved source is bound to a different shared material: {name}")
    configured = str(settings.profile_registry_path or "")
    path = (
        _source_path(configured, source) if configured else
        Path(values["texture_root"]).parent / profile_sync.PROFILE_FILENAME
        if values.get("texture_root") else source.parent / profile_sync.PROFILE_FILENAME
    )
    shared = profile_registry.load_registry(path)["profiles"].get(profile_id)
    if shared is not None:
        if shared.get("name") not in {None, name}:
            raise ValueError(f"Shared profile belongs to a different material: {name}")
        if profile_registry.values_hash(shared.get("values") or {}) != shared.get("content_hash"):
            raise ValueError(f"Shared profile content hash does not match: {name}")
        record = dict(shared)
    else:
        # A saved source remains usable before its first registry publication.
        record = {"revision": int(settings.profile_revision), "values": values,
                  "content_hash": profile_registry.values_hash(values)}
    missing = set(profile_sync.PROFILE_FIELDS) - set(record["values"])
    if missing:
        raise ValueError(f"Incomplete shared material {name}: {', '.join(sorted(missing))}")
    return profile_id, path, record


def _validate_receiver(material):
    if material.library is not None:
        raise ValueError(f"Linked material cannot be refreshed locally: {material.name}")
    if nodes.find_hair_shader(material) is None and nodes.find_legacy_hair_shader(material) is None:
        raise ValueError(f"Existing material has no compatible Hair shader: {material.name}")


def _receive(material, profile):
    profile_id, path, record = profile
    settings = material.htue_settings
    _validate_receiver(material)
    # This button explicitly receives source values, including when revision/hash
    # match but unsaved local controls differ. Never flush/publish the receiver.
    settings.initialized = False
    settings.profile_id = profile_id
    settings.profile_registry_path = str(path)
    settings.initialized = True
    profile_sync._apply_record(material, record)
    profile_sync._PENDING.pop(profile_sync._material_key(material), None)
    material.use_fake_user = True
    contract.persist_material_contract(material)


def _remove_temporary_dependencies(before):
    for collection in (bpy.data.node_groups, bpy.data.images, bpy.data.textures):
        temporary = [item for item in collection if item.as_pointer() not in before]
        changed = True
        while changed:
            changed = False
            for item in list(temporary):
                if item.users == 0 or (item.use_fake_user and item.users == 1):
                    collection.remove(item)
                    temporary.remove(item)
                    changed = True


def load_shared_materials(filepath):
    source = Path(bpy.path.abspath(str(filepath))).resolve()
    missing = [name for name in SHARED_MATERIAL_NAMES if name not in bpy.data.materials]
    existing = [name for name in SHARED_MATERIAL_NAMES if name not in missing]
    if not source.is_file():
        raise ValueError(f"Shared hair library not found: {source}")
    if bpy.data.filepath and source == Path(bpy.data.filepath).resolve():
        raise ValueError("This is the current file; choose the saved shared hair source")

    # Read all three saved templates even when local materials already exist.
    # Keep the local material datablocks, object slots and Profile references.
    with bpy.data.libraries.load(str(source), link=False) as (available, _loaded):
        absent = [name for name in SHARED_MATERIAL_NAMES if name not in available.materials]
        if absent:
            raise ValueError("Source library is missing: " + ", ".join(absent))
    original = {name: bpy.data.materials.get(name) for name in existing}
    for material in original.values():
        _validate_receiver(material)
    before = {item.as_pointer() for collection in
              (bpy.data.node_groups, bpy.data.images, bpy.data.textures) for item in collection}
    templates = []
    succeeded = False
    refreshed, up_to_date = [], []
    try:
        with bpy.data.libraries.load(str(source), link=False) as (_available, loaded):
            loaded.materials = list(SHARED_MATERIAL_NAMES)
        templates = list(loaded.materials)
        if any(material is None for material in templates):
            raise RuntimeError("Blender did not append every requested shared material")
        profiles = [_saved_profile(m, name, source)
                    for name, m in zip(SHARED_MATERIAL_NAMES, templates)]
        for name, template, profile in zip(SHARED_MATERIAL_NAMES, templates, profiles):
            target = original.get(name) or template
            if name in original:
                settings = target.htue_settings
                same = (settings.initialized and settings.profile_id == profile[0]
                        and settings.profile_revision == int(profile[2]["revision"])
                        and settings.profile_hash == profile[2]["content_hash"]
                        and profile_sync.material_values(target) == profile[2]["values"])
                (up_to_date if same else refreshed).append(name)
            _receive(target, profile)
        succeeded = True
    finally:
        for name, material in zip(SHARED_MATERIAL_NAMES, templates):
            if material is not None and (name in original or not succeeded):
                bpy.data.materials.remove(material)
        _remove_temporary_dependencies(before)
    return {
        "source": str(source),
        "loaded": missing,
        "existing": existing,
        "refreshed": refreshed,
        "up_to_date": up_to_date,
    }
