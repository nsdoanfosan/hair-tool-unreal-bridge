# Unreal Material Bridge

Blender–Unreal material integration add-on. It currently keeps Hair Tool card
controls synchronized and provides a lightweight `M_LayerBlend` Height preview
for clearance and silhouette checks in Blender 5.1. It does not modify Hair
Tool's own add-on files or node groups. The Python package and Git repository
retain the legacy `hair_tool_unreal_bridge` name so existing `.blend` files,
scripts, and the Blender junction continue to work.

## What is synchronized

The add-on creates a reversible, per-material compatibility copy named
`HTUE_HairShaderMain::<material>`. The original Hair Tool node group, add-on,
interface values, and Deformer links are never edited or disabled. Inside the
copy, the legacy HairShaderMain color result is replaced by the synchronized
stack. Legacy material colors and mix controls are not connected to the new
stack; Hair Tool supplies evaluated Deformer attributes only. The stack is
evaluated in the same order in Blender and Unreal:

1. `HT Base Color`
2. System Color (evaluated Hair Tool `SystemColor.RGB` in both renderers)
3. Root (`RFAOS.G`, optionally `IRD Map.G`; random source is `RFAOS.R` or `IRD Map.R`)
4. Tip (`RFAOS.G`, optionally `OneMinus(IRD Map.G)`; same shared random source)
5. ID tint (the same `RFAOS.R` or `IRD Map.R` source)
6. Depth tint (`Depth` vertex attribute or `IRD Map.B`)
7. AO (`RFAOS.B` and `ORM Map.R`)

Each color stage exposes `Normal`, `Multiply`, `Overlay`, `Soft Light`, and
`Add`. Blender UI labels and Unreal material parameter names are identical.
Opacity, Pixel Depth Offset, Hair BSDF-only surface controls, and other
Unreal-only rendering controls remain owned by Unreal.

`Set Factor`, `Set System Color`, Random, AO, and Depth stay connected through
Hair Tool's own Attribute nodes. A missing native input is filled without
replacing any existing link, and the added link is recorded so Restore can
remove it. On Blender 5.2, Curves Geometry Nodes can draw a stored attribute
while the evaluated Python data exposes no attributes and cannot convert to a
mesh. The Bridge therefore verifies reachable `Store Named Attribute` nodes on
the final Geometry path and refreshes SystemColor availability only after a
relevant geometry or node-tree update. Missing Blender AO data is neutralized inside the preview; AO is
enabled only when it exists on evaluated viewport geometry. The bridge does not
force the expensive AO generator onto every live system. In **Per System** mode,
only an explicitly linked final output that has no native AO modifier receives
a reversible Bridge-owned `HT_Mesh_AO` modifier. The Empty AO controls update
that fallback modifier in the viewport; native Hair Tool AO modifiers remain
unchanged. Switching that output to **Combined** disables only the Bridge-owned
live modifier, because Combined AO is evaluated on the joined preview/export
geometry. The 3D View sidebar has a dedicated **Unreal Bridge** tab. AO evaluation
is an explicit per-export-Empty choice: **Per System** evaluates each Hair Tool
system before joining, while **Combined** joins only generated cards and then
evaluates AO once. There is no automatic mode switch. `_02` defaults to Per
System because its tested combined mean fell from `0.543` to `0.236`. Samples,
Spread Angle, Base Color Value, Blur, Bounce factors, and Custom Normals are
stored on the Empty and applied to disposable export copies plus that
Bridge-owned fallback. Blender and Unreal consume the same AO attribute. The
editable systems are only hidden and can be restored with **Return to Live Hair
Tool**.
Material edits remain live on the cached preview; geometry edits require
Refresh. Send to Unreal always follows the selected AO order. `AO
Strength` softens excessive card self-occlusion in both renderers without
disabling the AO layer. Hair Tool's safe Map Range behavior is also preserved:
`HT Root Range = 0` means a full Root layer, not a disabled one.
Starting Send to Unreal automatically removes the display cache and restores
the original live Hair Tool systems before it evaluates the export.

## Creating a new Prism Mesh Guide

Ordinary Hair Tool material and node-group imports reuse existing dependency
groups without replacing their datablocks. This preserves custom shader nodes,
textures, links, and the original shader used by the Unreal Bridge when a new
Prism Profile creates `HT_Default_Material`. The compatibility hooks are removed
when the Bridge is disabled; Hair Tool's installed files remain unchanged.
Explicit Hair Tool **Whole Material** / shader updates still perform their
documented reset/update, so they are not a way to create a new material safely.

Regression: run `tests/blender_prism_material_smoke.py` with installed Hair Tool
in background Blender, using `--factory-startup --python-exit-code 1 --python`.
The test enables add-ons with `default_set=False` and never saves preferences.

## Shared Hair profiles

Configured Hair Tool materials use one shared registry named
`hair_tool_unreal_profiles.json`. With no explicit registry path, the Bridge first
reuses an existing registry beside the configured texture folder, then falls back
to the saved `.blend` folder. Hair and tail files in different folders therefore
share the same profile when they use the same texture root. A custom registry path
can still be assigned per material.
Local property edits update the Blender preview immediately, while the shared JSON
profile synchronizes only when the `.blend` is loaded or saved, or when **Sync Shared
Hair Profile Now** is pressed. No registry polling runs during ordinary editing.

Changing a Bridge control marks that local field as pending. Saving the `.blend`
or pressing **Sync Shared Hair Profile Now** publishes those edits and receives a
newer shared revision. Concurrent edits to different fields are merged. If two
stale files edit the same field, the second publish is stopped and the material
panel reports a conflict instead of silently overwriting the newer value.

Legacy Hair Tool materials that expose a top-level `Albedo` input are supported
without editing their original node group. The Bridge installs a reversible
top-level color stack, records the prior Albedo link, and restores it on removal.
The 3D View sidebar shows the active shared-profile revision and sync status.

The registry is written atomically under a short-lived process lock and stores a
revision plus a content hash for each profile. The unique-name exporter and Send
to Unreal keep their existing responsibilities: they consume the refreshed
Bridge contract but do not own profile synchronization.

## M_LayerBlend Height preview

The integration is material-driven and selection-independent. On file load and
when Tiling Material Batch reports a material handoff, the add-on queues one
current-Scene scan for editable meshes using `M_LayerBlend_*` materials. Repeated
notifications are coalesced, and no periodic scene scan runs while Blender is
idle. This automatic pass synchronizes Unreal values and the export contract; it
does not leave a live Geometry Nodes modifier on user meshes. After a direct
manual material-slot edit, **Unreal Bridge > M_LayerBlend Height Preview > Sync
Data** forces the same scene-wide data synchronization immediately.

Select one or more source meshes in Object Mode and press **Build / Refresh
Selected** to create an exact, frozen preview cache. The builder samples each
material's existing `UEUN_Height` image and follows these Unreal terms:

`(Height.R × layer Height_Strengh × master Height × Color.R − Center) × Magnitude`

The current Unreal master reports `Center = 0`, so the approximation moves only
outward. Centimeters are converted using Blender's scene unit scale. Material
boundaries are split only during the one-shot build to prevent neighboring
material slots from sharing a displaced vertex. The Bridge adds no subdivision;
it uses the mesh resolution already produced earlier in the modifier stack. A
temporary Geometry Nodes generator performs the build and is removed immediately;
the persistent result is an ordinary Mesh in `UMB Height Preview Cache`.

The frozen cache is shown in Object Mode while the source is displayed as wire.
In Edit Mode the cache is hidden automatically so Blender draws only the authored
mesh and face-selection overlay. Returning to Object Mode restores a still-valid
cache. Mesh or UV edits require **Build / Refresh Selected** again; material-data
changes mark the old cache stale and hide it until rebuilt.

Contract synchronization still traverses Group Pro referenced Collections,
including nested groups and mesh groups driven by `GPro_Instance`. Group Pro host
Meshes never receive a second Height pass. Exact cache generation is intentionally
selection-driven and only builds the explicitly selected editable source meshes.

If a material slot has no Height image or no matching Unreal Material Instance
in the latest valid report, only that slot is skipped and the reason is shown in
the panel. A failed audit JSON never hides the last complete report.
If the current Scene has no `M_LayerBlend` material, Sync Data and removal actions
report that absence as normal information instead of raising a Python error.

Cache objects are unselectable, excluded from render, and hidden during Send to
Unreal before export geometry is collected. The authored base mesh is therefore
exported and Unreal applies Height exactly once.

## Transport contract

Hair Tool export masks are authored as separate evaluated attributes so they do
not collide with Hair Tool's `SystemColor`, `Factor`, or `Depth` data. In the 3D
View sidebar open **Unreal Bridge > Unreal Export Masks**, then add **Weight** or
**Pixel Depth Offset** to the active Hair Tool subsystem. Each deformer starts with Hair
Tool's editable Root-to-Tip influence curve and can use the same input-mask
workflow as other Hair Tool deformers. The existing Hair Tool attribute preview
also lists `ChaosWeight` and `HairPixelDepthOffset`. Both custom Deformers are
registered as soon as the Bridge loads, so they also appear in Hair Tool's
Deformer search and under **Add Deformer > Color > Unreal Export Masks**. Before
insertion, the Bridge reconciles Hair Tool's saved Deformer slots with the real
connected node chain. An interrupted insertion is rolled back instead of leaving
an unlinked group or a **Fix missing Deformers** state behind.

`ChaosWeight` stays `FLOAT_COLOR` through Hair Tool's Catmull-Rom card generation.
Storing it as `BYTE_COLOR` before curve interpolation can turn a small negative
overshoot into a near-white stripe. Existing Bridge Weight groups upgrade in
place, preserving their sockets, links, and influence settings. Send2UE already
validates the signed values (negative weights fall back to zero) before writing
the final 8-bit vertex G channel.

Send to Unreal packs only the disposable evaluated export mesh as
`RFAOS.R = HairPixelDepthOffset`, `RFAOS.G = ChaosWeight`, `RFAOS.B = AO`, and
`RFAOS.A = 1`. Missing Weight is safely fixed at zero; missing Pixel Depth
Offset uses neutral one. Nanite material data remains in UV1-UV3. Vertex G is
reserved for Chaos Cloth Weight, while Vertex R is consumed by the hair
material's switchable Pixel Depth Offset mask without changing Blender shading
attributes.

Every configured Blender material stores a versioned JSON contract in the
`htue_contract_json` custom property. The existing unique-name exporter asks
this sidecar to persist the Bridge-owned controls immediately before it reads
the property. Deformer data is transported from evaluated Geometry Nodes;
SystemColor Alpha is ignored.

Send to Unreal exports:

- UV0: card texture coordinates.
- UV1: linear `SystemColor.RG`.
- UV2: tagged, packed Random + Depth and Factor.
- UV3: tagged AO and linear `SystemColor.B`.
- Vertex color `RFAOS`: Random, Factor, AO, and a reserved legacy fallback.
- Flow, IRD, ORM, and Opacity texture roles.

All four skeletal UV sets and both components are occupied in contract v3.
UV2's Random+Depth pair is two UNORM8 values. Send to Unreal forces full
precision UV build settings for Hair Tool skeletal meshes so the pair remains
decodable with Skeletal Nanite.

## Blender use

The repository is installed through a Windows junction at Blender's user add-on
directory. Enable **Unreal Material Bridge**, open Material Properties, and
click **Set Up Standard Hair Materials**. Editing a displayed value updates the
compatible preview group and persisted Unreal contract together. Send to Unreal
reads evaluated `SystemColor.RGB` directly while preparing the disposable export
mesh, so no material-level color copy or Alpha classification is required.

The 3D View **Unreal Bridge > Export Collection Link** panel links only the selected,
visible, render-enabled Hair Tool outputs directly to `Export`. When `Export`
contains more than one direct Empty, Blender asks which Empty should own the
Send to Unreal asset. The complete upstream Hair Tool parent chain is kept
together and its top object is placed under that Empty without changing world
transforms. Only the selected final output is linked directly to `Export`; its
hidden source mesh and curve keep their existing disabled state and collection
membership. The Empty target and original parent are stored as object pointers,
so renaming either is safe. **Unlink Selected from Export Collection** removes
only a link that this panel added, restores a hierarchy parent moved by this
panel, removes only a fallback AO modifier that this panel added, and preserves
any pre-existing Export collection link or native Hair Tool AO modifier.
Existing Export links are never replaced automatically. These controls organize
the Blender hierarchy, collection, and missing Per System AO only; they do not
run Send to Unreal.

Implementation-only sockets are hidden from Blender's recursive Surface UI.
The compact **Hair Tool Unreal Bridge** UI uses native Blender 5.1 child panels
for Source, Base, System Color, Root, Tip, ID, Depth, and AO/Roughness. Hair
Tool's original Surface inputs remain available, but their legacy color mixing
does not feed the replacement stack.
For renderer parity, setup also changes each unlinked Hair Tool
`HTool_Normal > Flip Backface Normal` socket from the stock `0.5` to `1.0`.
At `0.5` the backface result is halfway between `N` and `-N`, which is a zero
normal; Unreal's two-sided hair path uses the fully flipped backface normal.
Restore records and reinstates the original per-material socket value without
editing Hair Tool's shared node group.
Existing bridge materials are upgraded automatically when their `.blend` file
is reopened; **Refresh Hooks + Unreal** also performs the UI migration immediately.
Interactive edits update only the one stack socket owned by the changed field;
unchanged sockets and unchanged contract JSON are not rewritten. This avoids
the repeated shader invalidation that previously caused white viewport flashes.

**Restore Original Hair Tool Nodes** removes the compatibility copy and added
links, then reconnects the captured Hair Tool input state. The original Hair
Tool node group definition is never edited.

## Unreal build

`unreal/build_haircards_master.py` rebuilds
`/Game/Material/HairTool/Master/M_HT_HairCards` and updates the four instances
under `/Game/Material/HairTool/MI`. Run it with the project's UE 5.8 Python
commandlet and the CodexMaterialTools plugin, with Unreal Editor closed so asset
packages cannot be overwritten by two processes.

Material Instance parameters follow the same numbered sections as Blender:
Textures, Base, System Color, Root, Tip, ID, Depth, and AO/Roughness. UV,
surface/flow, opacity, and Pixel Depth Offset are placed in clearly marked
`UNREAL ONLY` groups at the bottom.

## Tests

```powershell
python -m pytest -q
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --background --factory-startup --python tests\blender_smoke.py
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --background --factory-startup --python tests\blender_export_masks_smoke.py
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --background --factory-startup --python tests\blender_profile_sync_smoke.py
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --background --factory-startup --python tests\blender_weight_interpolation_smoke.py
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --background --factory-startup --python tests\layerblend_preview_smoke.py -- --repo "$PWD"
```

`tests/blender_smoke.py` runs under Blender 5.2 factory startup and proves that
Hair Tool Deformer links survive setup, migration, and restoration while
legacy material controls remain disconnected from the replacement stack.
`tests/layerblend_preview_smoke.py` proves selection-independent scene sync,
shared node groups, the 2 cm reference displacement, no-subdivision policy, and
export suspension/restoration contract.
`tests/actual_blend_readonly.py` performs the same four-material audit against
`hair_sibuki_09.blend` without saving it. `tests/blender_profile_sync_smoke.py`
proves explicit save-time publish/pull, disjoint stale-edit merging, same-field conflict
detection, and revision/hash updates across two in-memory child materials.
`tests/blender_weight_interpolation_smoke.py` reproduces the legacy byte-color
overflow with real Catmull-Rom geometry and verifies float weights, late clamping,
unchanged geometry, and migration of linked, in-use Weight groups.
