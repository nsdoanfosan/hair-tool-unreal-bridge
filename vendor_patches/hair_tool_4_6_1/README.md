# Hair Tool 4.6.1 subsystem fixes

These source patches keep Hair Tool's subsystem metadata attached to the same
modifier when systems are sorted, and make subsystem 10 and later read/write
their own settings. Git stores only the replacement method and hash-checked
patch tools. Full installed Hair Tool sources and generated candidates stay
local.

`sort_hair_mods.pyfrag` fixes the stale subsystem-index map by reading the current
index before each modifier move. It restores the active subsystem by modifier
name, so its deformer selection and authored values follow it. The starting
modifier position is taken from the current stack; repeating a sort on the same
tree is safe.

`patch_subsystem_indices.py` replaces the single-character index extraction in
`HairSystemProps.get_node_val` and `set_node_val` with the complete final bracketed
integer. For example, editing subsystem 10 previously edited subsystem 0.

## Supported source

Both tools require Hair Tool `bl_info.version == (4, 6, 1)` and the exact source
hash below. A different vendor update or local edit is rejected for review.

| File | Original SHA-256 | Patched SHA-256 |
| --- | --- | --- |
| `hair_baking/hair_nodes_tree.py` | `bca9201a3d002b7b0e94c8ef2028b8baad2bfe964ac240273c888d54b4bd527f` | `6d5371872bb89a10638c3913874fdc881ca8ff69ba8e1e294b100629b059b6b7` |
| `hair_tool_props.py` | `cea0944fa4a4d3c414e51df87cb3344ce2e5fce7317617b63cff23d084565f63` | `019ec623ec483b8c39e4d027053fde2c95edef8b0f1d3c26d7629eb90056fa59` |

## Prepare and inspect

From the repository root, choose an existing installed add-on and a separate
scratch directory. The `prepare` command compiles the candidate, checks its hash,
and writes a JSON receipt. It does not change the installed file.

```powershell
$addonPath = 'C:/Users/PARK/AppData/Roaming/Blender Foundation/Blender/5.2/scripts/addons/hair_tool'
$candidatePath = Join-Path $env:TEMP 'hair-tool-461-patch'
python vendor_patches/hair_tool_4_6_1/patch_sort_hair_mods.py prepare --addon-dir $addonPath --candidate-dir $candidatePath
python vendor_patches/hair_tool_4_6_1/patch_subsystem_indices.py prepare --addon-dir $addonPath --candidate-dir $candidatePath
```

## Verify

```powershell
python -m pytest -q tests/test_vendor_sort_patch.py
& 'C:/Program Files/Blender Foundation/Blender 5.2/blender.exe' --background --factory-startup --disable-autoexec --python-exit-code 1 --python tests/blender_vendor_sort_smoke.py -- --candidate "$candidatePath/hair_baking/hair_nodes_tree.py"
& 'C:/Program Files/Blender Foundation/Blender 5.2/blender.exe' --background --factory-startup --disable-autoexec --python-exit-code 1 --python tests/blender_vendor_subsystem_indices_smoke.py -- --candidate-dir $candidatePath
```

Run Blender with a temporary `BLENDER_USER_CONFIG` and temporary `TEMP`/`TMP`
directories. These tests enable add-ons with `default_set=False`, create only
disposable scenes, and never save user preferences. The second smoke uses the
existing Prism fixture and its own temporary .blend/profile registry.

Coverage includes all 1–5-system permutations and active selections; repeated
sorting; interleaved non-hair modifiers; 20 real Blender modifier/RNA cases; and
12 actual Prism/Filter subsystems with reads at indices 0–11 and writes at 10/11.

## Apply after verification

```powershell
python vendor_patches/hair_tool_4_6_1/patch_sort_hair_mods.py apply --addon-dir $addonPath --candidate-dir $candidatePath
python vendor_patches/hair_tool_4_6_1/patch_subsystem_indices.py apply --addon-dir $addonPath --candidate-dir $candidatePath
```

Each apply creates a timestamped backup beside its source, verifies the backup
hash, rechecks the source, replaces that file atomically, and verifies the result.
Reapplying the same patch reports `already-applied`. This does not edit
`userpref.blend` or save the current .blend.

Reload the affected Hair Tool modules and Unreal Bridge integration in the live
Blender session after applying. Existing registered RNA callbacks must receive
the updated class methods as well; disk replacement alone is not a live reload.
To roll back, restore the verified `.pre-htue-fix-*` backup for each changed file,
then reload those modules again.
