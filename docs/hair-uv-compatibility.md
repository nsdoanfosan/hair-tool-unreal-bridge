# Hair Tool UV compatibility

Blender sidebar: **Unreal Bridge > Hair Compatibility**. The active object's
UV contract is checked as the panel draws. **Check Hair Compatibility** scans
the current scene and writes `Hair Tool Compatibility Report` in Blender's
Text Editor. Inspection does not change objects, UVs, node groups or materials.

Select the affected outputs and use **Repair Selected UV Compatibility**.
Each Profile is checked again immediately before repair. Only missing UV
attributes with an unambiguous equivalent representation can be repaired.
The report records the original owner, missing attributes and post-repair result.

## Checks

- Nested Profile nodes reading `UV_Start`, `UV_End` or `UV_Matrix` against the
  actual UV Owner mesh attributes.
- Missing/non-mesh owners, empty layouts, wrong attribute types/domains and
  non-finite values.
- Conflicting legacy coordinates and matrices; transformations that cannot
  be represented without loss as legacy axis-aligned UV boxes.
- Disabled Profile modifiers and linked/override data that need manual review.

The reader inventory is conservative and includes muted/conditional branches.
Custom graphs, dynamically supplied attribute names, texture/alpha problems,
strand density, collection visibility and general Blender/add-on API compatibility
are outside this check. A clean report is a UV contract result, not a guarantee
that every hair system is visible or generally compatible.

## Repair and preservation

Repair copies the UV mesh, retains all existing attributes, and adds only the
missing equivalent format. It binds a private UV Owner to that Profile; other
users of the original owner remain untouched. The new owner stores an ID
reference `htue_uv_original` to retain the original through saves. Ctrl-Z undoes
the operator. No node groups or materials are rebuilt, no vendor sources are
patched, and no preferences or blend files are saved automatically.

Both conversion directions are supported. Rotation, shear, projective matrices,
conflicting formats, linked data and incomplete layouts are reported for manual
review. Each Profile repair rolls back on failure. Repairs to earlier Profiles
in a multi-selection are independent; Ctrl-Z can undo a completed operator.

Hair Tool 4.6.1 can reuse a saved legacy Profile while writing new UV layouts as
`UV_Matrix`. Its validity check accepts the matrix attribute without checking the
reader's expected format. Repair bridges that mismatch without replacing shared
Profile groups. A later native UV edit can remove the compatibility attributes;
the panel detects that recurrence and repair can be run again. There is deliberately
no automatic migration during load or a vendor update.

## Validation

Run with Blender 5.2 (registration tests do not save preferences):

```powershell
& 'C:/Program Files/Blender Foundation/Blender 5.2/blender.exe' --factory-startup --background --python-exit-code 1 --python tests/blender_uv_compat.py
```

This uses real Geometry Nodes evaluation to prove zero UVs become the intended
coordinates, and checks reverse conversion, shared-owner isolation, idempotence,
conflicting values, unsupported transforms, invalid data, rollback and UI operator
registration. API entry points: `uv_compat.audit(objects)` and
`uv_compat.repair(objects)`; the latter is an explicit mutation.
