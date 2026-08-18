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
remove it. Missing Blender AO data is neutralized inside the preview; AO is
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

## M_LayerBlend Height preview

The integration is material-driven and selection-independent. On file load and
while Blender is open, the add-on scans the current Scene for editable meshes
using Tiling Material Batch `M_LayerBlend_*` materials. Assigning or replacing
one of those materials, rebuilding a Tiling audit report, or running Tiling
consumer migration creates or updates the preview automatically for every user
mesh. **Unreal Bridge > M_LayerBlend Height Preview > Sync Scene** forces the
same scene-wide synchronization immediately. The add-on samples each material's
existing `UEUN_Height` image and follows these useful Unreal terms:

`(Height.R × layer Height_Strengh × master Height × Color.R − Center) × Magnitude`

The current Unreal master reports `Center = 0`, so the approximation moves only
outward. Centimeters are converted using Blender's scene unit scale. Material
boundaries are split only in the evaluated preview to prevent neighboring
material slots from sharing a displaced vertex. The Bridge adds no subdivision;
it uses the mesh resolution already produced earlier in the modifier stack.
Identical material/slot configurations share one Geometry Nodes group instead
of duplicating a graph for every object. Meshes in other Scenes are not evaluated
until that Scene becomes current.

The Height approximation is shown in Object Mode only. In Edit Mode the Bridge
hides its evaluated split/displaced result so Blender displays the authored mesh
and face-selection overlay clearly. Returning to Object Mode restores the Height
preview automatically.

Group Pro is handled through its referenced Collections, including nested groups
and mesh groups driven by `GPro_Instance`. The Bridge applies Height to the actual
member Meshes inside those Collections. It never applies another Height preview
to the Group Pro host Mesh, even when the host carries copied material slots, so
the instanced result cannot be displaced twice.

If a material slot has no Height image or no matching Unreal Material Instance
in the latest valid report, only that slot is skipped and the reason is shown in
the panel. A failed audit JSON never hides the last complete report.

This modifier is display-only. It is excluded from render evaluation and Send
to Unreal explicitly suspends every Bridge-owned Height preview before it finds
or evaluates export geometry, then restores the previous viewport state after
the handoff. The authored base mesh is therefore exported and Unreal applies
Height exactly once.

## Transport contract

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
click **Set Up hair_sibuki_08 Materials**. Editing a displayed value updates the
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
& "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" --background --factory-startup --python tests\blender_smoke.py
& "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" --background --factory-startup --python tests\layerblend_preview_smoke.py -- --repo "$PWD"
```

`tests/blender_smoke.py` runs under Blender 5.1 factory startup and proves that
Hair Tool Deformer links survive setup, migration, and restoration while
legacy material controls remain disconnected from the replacement stack.
`tests/layerblend_preview_smoke.py` proves selection-independent scene sync,
shared node groups, the 2 cm reference displacement, no-subdivision policy, and
export suspension/restoration contract.
`tests/actual_blend_readonly.py` performs the same four-material audit against
`hair_sibuki_08.blend` without saving it.
