# Hair Tool Unreal Bridge

Sidecar Blender add-on and Unreal material builder for keeping Hair Tool card
color controls readable and synchronized across Blender 5.1 and Unreal Engine
5.8. It does not modify Hair Tool's own add-on files or node groups.

## What is synchronized

The add-on adds a reversible `HTUE Hair Material Bridge` in front of the
existing `HairShaderMain` Base Color input. The stack is evaluated in the same
order in Blender and Unreal:

1. `HT Base Color`
2. Root (`RFAOS.G`, optionally `IRD Map.G`)
3. Tip (`RFAOS.G`, optionally `OneMinus(IRD Map.G)`)
4. ID tint (`RFAOS.R` or `IRD Map.R`)
5. Depth tint (`Depth` vertex attribute or `IRD Map.B`)
6. System Color (`RFAOS.A` selects `System Color 01` or `System Color 02`)
7. AO (`RFAOS.B` and `ORM Map.R`)

Each color stage exposes `Normal`, `Multiply`, `Overlay`, `Soft Light`, and
`Add`. Blender UI labels and Unreal material parameter names are identical.
Opacity, Pixel Depth Offset, Hair BSDF-only surface controls, and other
Unreal-only rendering controls remain owned by Unreal.

The current `hair_sibuki_08.blend` has no authored `Depth` attribute. The
bridge therefore uses the neutral vertex fallback until one is authored, while
`IRD Map.B` is available immediately through `Depth Map Influence`.

## Transport contract

Every configured Blender material stores a versioned JSON contract in the
`htue_contract_json` custom property. The existing unique-name exporter reads
that property without importing this add-on, so project-specific behavior stays
beside Hair Tool rather than inside it.

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
preview node group and persisted Unreal contract together.

**Restore Original Hair Tool Nodes** removes the bridge and reconnects the
captured Hair Tool input state. The add-on never edits the original Hair Tool
node group definition.

## Unreal build

`unreal/build_haircards_master.py` rebuilds
`/Game/Material/HairTool/Master/M_HT_HairCards` and updates the four instances
under `/Game/Material/HairTool/MI`. Run it with the project's UE 5.8 Python
commandlet and the CodexMaterialTools plugin, with Unreal Editor closed so asset
packages cannot be overwritten by two processes.

## Tests

```powershell
python -m pytest -q
```

`tests/blender_smoke.py` also runs under Blender 5.1 factory startup and checks
node construction and restoration.
