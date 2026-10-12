"""Read the real saved source in a disposable process; never save production data."""
import json
from pathlib import Path
import sys

import addon_utils
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
assert addon_utils.enable("hair_tool_unreal_bridge", default_set=False) is not None
from hair_tool_unreal_bridge import material_library as library
from hair_tool_unreal_bridge import profile_sync, profile_registry

unrelated = bpy.data.materials.new("Import regression sentinel")
unrelated.diffuse_color = (0.12, 0.23, 0.34, 1)
before = tuple(unrelated.diffuse_color)
source = library.DEFAULT_LIBRARY
with bpy.data.libraries.load(str(source), link=False) as (_a, loaded):
    loaded.materials = [library.SHARED_MATERIAL_NAMES[1]]
existing = loaded.materials[0]
existing.use_fake_user = True
existing_pointer = existing.as_pointer()
registry_path = profile_sync.registry_path(existing)
registry_before = registry_path.read_bytes()
record = profile_registry.load_registry(registry_path)['profiles'][existing.htue_settings.profile_id]
# Reproduce a receiver that already has Short 01, with stale/unsaved controls.
existing.htue_settings.system_blend_mode = 'ADD'
existing.htue_settings.root_mix = .123
existing.htue_settings.profile_revision = max(0, int(record['revision']) - 1)
mesh = bpy.data.meshes.new('Shared receiver assignment')
receiver = bpy.data.objects.new('Shared receiver assignment', mesh)
mesh.materials.append(existing)
baseline_count = len(bpy.data.materials)

try:
    library.load_shared_materials(str(source.parent / "missing-library.blend"))
except ValueError:
    pass
else:
    raise AssertionError("Missing source must fail")
assert len(bpy.data.materials) == baseline_count
report = library.load_shared_materials(str(source))
assert len(report['loaded']) == 2, report
assert report['existing'] == [library.SHARED_MATERIAL_NAMES[1]], report
assert report['refreshed'] == [library.SHARED_MATERIAL_NAMES[1]], report
assert existing.as_pointer() == existing_pointer
assert receiver.data.materials[0] is existing
assert profile_sync.material_values(existing) == record['values']
assert existing.htue_settings.profile_revision == record['revision']
assert existing.htue_settings.profile_sync_status == 'SYNCED'
assert profile_sync._material_key(existing) not in profile_sync._PENDING
assert registry_path.read_bytes() == registry_before
assert tuple(unrelated.diffuse_color) == before
assert len(bpy.data.materials) == baseline_count + 2

rows = []
for name in library.SHARED_MATERIAL_NAMES:
    material = bpy.data.materials[name]
    contract = json.loads(material["htue_contract_json"])
    assert material.htue_settings.initialized, name
    assert material.use_fake_user, name
    assert material.node_tree is not None, name
    assert contract["material_instance_path"] == (
        "/Game/Material/HairTool/MI/MI_" + name.removeprefix("M_")
    ), contract
    rows.append({"name": name, "mi": contract["material_instance_path"],
                 "texture_set": material.htue_settings.texture_set})
count = len(bpy.data.materials)
assert bpy.ops.htue.load_shared_materials(filepath=str(source)) == {"FINISHED"}
assert len(bpy.data.materials) == count
assert receiver.data.materials[0].as_pointer() == existing_pointer
assert registry_path.read_bytes() == registry_before
assert not any(name.startswith(existing.name + '.') for name in bpy.data.materials.keys())
# Reject an incompatible same-name receiver before refreshing any other material.
name = library.SHARED_MATERIAL_NAMES[2]
good = bpy.data.materials[name]
good.name = name + '.kept-for-validation'
bad = bpy.data.materials.new(name)
existing.htue_settings.root_mix = .234
count = len(bpy.data.materials)
try:
    library.load_shared_materials(str(source))
except ValueError as exc:
    assert 'compatible Hair shader' in str(exc)
else:
    raise AssertionError('Invalid receiver must fail before mutations')
assert len(bpy.data.materials) == count
assert abs(existing.htue_settings.root_mix - .234) < 1e-6
assert registry_path.read_bytes() == registry_before
bpy.data.materials.remove(bad)
good.name = name
library.load_shared_materials(str(source))
assert profile_sync.material_values(existing) == record['values']
print("HTUE_SHARED_MATERIAL_LIBRARY_OK " + json.dumps(rows))
