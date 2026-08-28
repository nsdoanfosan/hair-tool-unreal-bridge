import tempfile
from pathlib import Path

import addon_utils
import bpy


addon_utils.enable("hair_tool_unreal_bridge", default_set=False, persistent=False)

from hair_tool_unreal_bridge import profile_registry, profile_sync


temporary = tempfile.TemporaryDirectory()
registry_path = Path(temporary.name) / "hair_tool_unreal_profiles.json"


def prepared_material(name):
    material = bpy.data.materials.new(name)
    settings = material.htue_settings
    settings.profile_registry_path = str(registry_path)
    settings.initialized = True
    return material


source = prepared_material("M_HT_Profile_Source")
source.htue_settings.base_color = (0.1, 0.2, 0.3, 1.0)
profile_sync.flush_material(source)
assert source.htue_settings.profile_revision == 1
assert source.htue_settings.profile_sync_status == "SYNCED"

child = prepared_material("M_HT_Profile_Child")
child.htue_settings.profile_id = source.htue_settings.profile_id
assert profile_sync.pull_material(child)
assert tuple(child.htue_settings.base_color) == tuple(source.htue_settings.base_color)
assert child.htue_settings.profile_revision == 1

source.htue_settings.root_mix = 0.72
profile_sync.flush_material(source)
assert source.htue_settings.profile_revision == 2
assert profile_sync.pull_material(child)
assert abs(child.htue_settings.root_mix - 0.72) < 1.0e-6
assert child.htue_settings.profile_revision == 2

# A stale child changing another field merges without restoring the older root value.
source.htue_settings.tip_mix = 0.4
profile_sync.flush_material(source)
assert source.htue_settings.profile_revision == 3
child.htue_settings.ao_strength = 0.25
profile_sync.flush_material(child)
assert child.htue_settings.profile_revision == 4
profile_sync.pull_material(source)
assert abs(source.htue_settings.tip_mix - 0.4) < 1.0e-6
assert abs(source.htue_settings.ao_strength - 0.25) < 1.0e-6

registry = profile_registry.load_registry(registry_path)
record = registry["profiles"][source.htue_settings.profile_id]
assert record["revision"] == 4
assert record["content_hash"] == profile_registry.values_hash(record["values"])

# Updating the same field from a stale base is stopped instead of last-writer-wins.
profile_sync.pull_material(child)
source.htue_settings.root_mix = 0.31
profile_sync.flush_material(source)
child.htue_settings.root_mix = 0.91
profile_sync.flush_material(child)
assert child.htue_settings.profile_sync_status == "CONFLICT"
assert "root_mix" in child.htue_settings.profile_sync_error

print("HTUE_PROFILE_SYNC_SMOKE_OK")
