# Hair Tool Unreal Bridge

Sidecar Blender add-on and Unreal material builder for keeping Hair Tool card
color controls readable and synchronized across Blender 5.1 and Unreal Engine
5.7. It does not modify Hair Tool's own add-on files or node groups.

## What is synchronized

The add-on creates a reversible, per-material compatibility copy named
`HTUE_HairShaderMain::<material>`. The original Hair Tool node group, add-on,
interface values, and Deformer links are never edited or disabled. Inside the
copy, only the final result of the duplicate legacy color-mix chain is replaced
by the synchronized stack. The stack is evaluated in the same order in Blender
and Unreal:

1. `HT Base Color`
2. Root (`RFAOS.G`, optionally `IRD Map.G`)
3. Tip (`RFAOS.G`, optionally `OneMinus(IRD Map.G)`)
4. ID tint (`RFAOS.R` or `IRD Map.R`)
5. Depth tint (`Depth` vertex attribute or `IRD Map.B`)
6. System Color (Hair Tool `SystemColor.RGB` in Blender; `RFAOS.A` selects the
   synchronized `System Color 01` or `System Color 02` in Unreal)
7. AO (`RFAOS.B` and `ORM Map.R`)

Each color stage exposes `Normal`, `Multiply`, `Overlay`, `Soft Light`, and
`Add`. Blender UI labels and Unreal material parameter names are identical.
Opacity, Pixel Depth Offset, Hair BSDF-only surface controls, and other
Unreal-only rendering controls remain owned by Unreal.

`Set Factor`, `Set System Color`, Random, AO, and Depth stay connected through
Hair Tool's own Attribute nodes. A missing native input is filled without
replacing any existing link, and the added link is recorded so Restore can
remove it. Missing Blender AO data is neutralized inside the preview while the
synchronized Unreal vertex-AO influence remains unchanged. An authored `AO`
attribute or Hair Tool's opt-in `HT_Mesh_AO` generator enables the live AO
preview; the bridge does not force the expensive AO generator on.

## Transport contract

Every configured Blender material stores a versioned JSON contract in the
`htue_contract_json` custom property. The existing unique-name exporter asks
this sidecar to refresh live Hair Tool inputs and Deformer colors immediately
before it reads the property. The two dominant `SystemColor.RGB` values are
classified by the original Alpha channel. Geometry Nodes-evaluated Deformer
output is preferred over stale converted meshes when both are present.

Send to Unreal exports:

- Vertex color `RFAOS`: Random, Factor, AO, System alpha.
- UV2: tagged, packed Random + Depth and Factor.
- UV3: tagged AO and System alpha.
- Flow, IRD, ORM, and Opacity texture roles.

UV2's Random+Depth pair is two UNORM8 values. Send to Unreal forces full
precision UV build settings for Hair Tool skeletal meshes so the pair remains
decodable with Skeletal Nanite.

## Blender use

The repository is installed through a Windows junction at Blender's user add-on
directory. Enable **Hair Tool Unreal Bridge**, open Material Properties, and
click **Set Up hair_sibuki_08 Materials**. Editing a displayed value updates the
compatible preview group and persisted Unreal contract together. **Read Set
System Color** refreshes the two exported colors on demand; Send to Unreal does
the same refresh automatically.

Implementation-only sockets are hidden from Blender's recursive Surface UI.
The compact **Hair Tool Unreal Bridge** UI uses native Blender 5.1 child panels
for Source, Base, Root, Tip, ID, Depth, System Color, and AO/Roughness. Hair
Tool's original Surface inputs remain available and active.
Existing bridge materials are upgraded automatically when their `.blend` file
is reopened; **Sync Contract** also performs the UI migration immediately.

**Restore Original Hair Tool Nodes** removes the compatibility copy and added
links, then reconnects the captured Hair Tool input state. The original Hair
Tool node group definition is never edited.

## Unreal build

`unreal/build_haircards_master.py` rebuilds
`/Game/Material/HairTool/Master/M_HT_HairCards` and updates the four instances
under `/Game/Material/HairTool/MI`. Run it with the project's UE 5.7 Python
commandlet and the CodexMaterialTools plugin, with Unreal Editor closed so asset
packages cannot be overwritten by two processes.

Material Instance parameters follow the same numbered sections as Blender:
Textures, Base, Root, Tip, ID, Depth, System Color, and AO/Roughness. UV,
surface/flow, opacity, and Pixel Depth Offset are placed in clearly marked
`UNREAL ONLY` groups at the bottom.

## Tests

```powershell
python -m pytest -q
```

`tests/blender_smoke.py` runs under Blender 5.1 factory startup and proves that
Hair Tool Attribute links survive setup, migration, and restoration.
`tests/actual_blend_readonly.py` performs the same four-material audit against
`hair_sibuki_08.blend` without saving it.
