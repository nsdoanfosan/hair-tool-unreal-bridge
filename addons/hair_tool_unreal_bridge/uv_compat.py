"""Read-only Hair Tool UV contract audit and isolated, lossless repairs.

No vendor patches, load-time migrations, global node remaps or preference writes.
"""
import json
import math

import bpy
from mathutils import Matrix

REPORT_NAME = "Hair Tool Compatibility Report"
ATTRS = {"UV_Start": "FLOAT2", "UV_End": "FLOAT2", "UV_Matrix": "FLOAT4X4"}


def input_value(modifier, identifier):
    try:
        return modifier.properties.inputs[identifier]["value"]
    except (AttributeError, KeyError, TypeError):
        try:
            return modifier.get(identifier)
        except (AttributeError, TypeError):
            return None


def set_input_value(modifier, identifier, value):
    if hasattr(modifier, "properties"):
        modifier.properties.inputs[identifier]["value"] = value
    else:
        modifier[identifier] = value


def profiles(obj):
    for mod in obj.modifiers:
        if mod.type != "NODES" or not mod.node_group:
            continue
        for socket in mod.node_group.interface.items_tree:
            if socket.item_type == "SOCKET" and socket.in_out == "INPUT" and socket.name == "UV Owner":
                yield mod, socket.identifier


def attribute_reads(tree, seen=None):
    """Conservative inventory, including nested and muted branches."""
    seen = seen if seen is not None else set()
    if tree.as_pointer() in seen:
        return set()
    seen.add(tree.as_pointer())
    names = set()
    for node in tree.nodes:
        if node.bl_idname == "GeometryNodeInputNamedAttribute":
            socket = node.inputs.get("Name")
            if socket and not socket.is_linked and socket.default_value in ATTRS:
                names.add(socket.default_value)
        if node.type == "GROUP" and node.node_tree:
            names.update(attribute_reads(node.node_tree, seen))
    return names


def _matrix(start, end):
    return Matrix(((end[0] - start[0], 0, 0, start[0]),
                   (0, start[1] - end[1], 0, end[1]),
                   (0, 0, 1, 0), (0, 0, 0, 1)))


def _close(a, b):
    return all(abs(a[r][c] - b[r][c]) <= 1e-6 for r in range(4) for c in range(4))


def _values(mesh):
    values = {}
    for name, kind in ATTRS.items():
        attr = mesh.attributes.get(name)
        if attr is None:
            continue
        if attr.data_type != kind or attr.domain != "POINT" or len(attr.data) != len(mesh.vertices):
            raise ValueError(f"{name}: expected {kind} on POINT domain")
        seq = [x.value.copy() if kind == "FLOAT4X4" else tuple(x.vector) for x in attr.data]
        for value in seq:
            flat = [x for row in value for x in row] if kind == "FLOAT4X4" else value
            if not all(math.isfinite(x) for x in flat):
                raise ValueError(f"{name}: non-finite coordinates")
        values[name] = seq
    return values


def _conversion(values):
    """Return equivalent dual-format data; reject conflicting/lossy sources."""
    starts, ends, matrices = (values.get(n) for n in ATTRS)
    if matrices is not None:
        converted_starts = [(m[0][3], m[1][3] + m[1][1]) for m in matrices]
        converted_ends = [(m[0][3] + m[0][0], m[1][3]) for m in matrices]
        if not all(_close(m, _matrix(s, e)) for m, s, e in zip(matrices, converted_starts, converted_ends)):
            raise ValueError("Rotated/sheared/projective UV matrices cannot be represented by legacy UV boxes")
        for existing, converted in ((starts, converted_starts), (ends, converted_ends)):
            if existing is not None and any(abs(a - b) > 1e-6 for old, new in zip(existing, converted) for a, b in zip(old, new)):
                raise ValueError("Legacy UV coordinates conflict with UV_Matrix; choose the authoritative layout manually")
        starts, ends = converted_starts, converted_ends
    elif starts is not None and ends is not None:
        matrices = [_matrix(s, e) for s, e in zip(starts, ends)]
    else:
        raise ValueError("No complete UV layout available for reconstruction")
    return dict(zip(ATTRS, (starts, ends, matrices)))


def inspect_profile(obj, mod, identifier):
    required = attribute_reads(mod.node_group)
    owner = input_value(mod, identifier)
    row = {"object": obj.name, "modifier": mod.name, "uv_owner": owner.name if owner else None,
           "reads": sorted(required), "issues": [], "repairable": False, "missing": []}
    def issue(code, message):
        row["issues"].append({"code": code, "message": message})
    if not mod.show_viewport:
        issue("VIEWPORT_DISABLED", "Profile modifier is disabled in the viewport")
    if not owner or owner.type != "MESH":
        issue("UV_OWNER_MISSING", "UV Owner must reference a mesh")
        return row
    if not len(owner.data.vertices):
        issue("UV_LAYOUT_EMPTY", "UV Owner has no UV regions")
        return row
    try:
        values = _values(owner.data)
    except ValueError as exc:
        issue("INVALID_ATTRIBUTE", str(exc))
        return row
    missing = required - values.keys()
    row["missing"] = sorted(missing)
    if missing:
        issue("UV_SCHEMA_MISMATCH", "Profile reads missing attributes: " + ", ".join(sorted(missing)))
    if not required:
        issue("UNKNOWN_UV_READER", "No recognized static UV reader; custom/dynamic graphs require manual review")
    # A matrix-only reader can support rotation: don't reject valid modern layouts.
    needs_conversion = bool(missing) or ("UV_Matrix" in values and bool({"UV_Start", "UV_End"} & values.keys()))
    if needs_conversion:
        try:
            _conversion(values)
        except ValueError as exc:
            issue("UV_CONVERSION_UNSAFE", str(exc))
        else:
            row["repairable"] = bool(missing)
    if obj.library or owner.library or owner.data.library or obj.override_library or owner.override_library:
        row["repairable"] = False
        issue("LINKED_DATA", "Linked/override data requires manual review")
    return row


def audit(objects=None):
    objects = list(bpy.context.scene.objects if objects is None else objects)
    rows = [inspect_profile(obj, mod, identifier) for obj in objects for mod, identifier in profiles(obj)]
    return {"schema": 1, "blender": bpy.app.version_string, "profiles_checked": len(rows),
            "affected_profiles": sum(bool(row["issues"]) for row in rows),
            "repairable_profiles": sum(row["repairable"] for row in rows), "profiles": rows}


def repair_profile(obj, mod, identifier):
    """Re-audit before mutation; bind only a validated private copy, rollback on failure."""
    before = inspect_profile(obj, mod, identifier)
    if not before["repairable"]:
        return {"status": "skipped", "before": before}
    source = input_value(mod, identifier)
    converted = _conversion(_values(source.data))
    mesh = source.data.copy()
    candidate = None
    try:
        candidate = bpy.data.objects.new(source.name + "_compatible", mesh)
        # An ID reference retains the original layout even after saving/reloading.
        candidate["htue_uv_original"] = source
        for name in before["missing"]:
            mesh.attributes.new(name, ATTRS[name], "POINT")
            # Fetch each attribute after creation: adding attributes invalidates RNA handles.
            attr = mesh.attributes[name]
            for item, value in zip(attr.data, converted[name]):
                if name == "UV_Matrix":
                    item.value = value
                else:
                    item.vector = value
        _conversion(_values(mesh))
        set_input_value(mod, identifier, candidate)
        obj.update_tag()
        after = inspect_profile(obj, mod, identifier)
        if after["missing"]:
            raise RuntimeError("UV compatibility verification failed")
    except Exception:
        set_input_value(mod, identifier, source)
        if candidate is not None:
            bpy.data.objects.remove(candidate)
        bpy.data.meshes.remove(mesh)
        raise
    return {"status": "repaired", "before": before, "after": after}


def repair(objects):
    results = []
    for obj in list(objects):
        for mod, identifier in list(profiles(obj)):
            try:
                results.append(repair_profile(obj, mod, identifier))
            except Exception as exc:
                # Individual repairs rollback; keep a visible receipt for any
                # earlier successes instead of cancelling their undo boundary.
                results.append({"status": "failed", "object": obj.name,
                                "modifier": mod.name, "error": str(exc)})
    return {"repairs": results}


def _report(data):
    text = bpy.data.texts.get(REPORT_NAME) or bpy.data.texts.new(REPORT_NAME)
    text.clear()
    text.write(json.dumps(data, ensure_ascii=False, indent=2))


class HTUE_OT_audit_hair_uv(bpy.types.Operator):
    bl_idname = "htue.audit_hair_uv"
    bl_label = "Check Hair Compatibility"
    bl_description = "Check current scene Hair Tool UV readers and data; write a detailed Text Editor report"

    def execute(self, context):
        data = audit(context.scene.objects)
        _report(data)
        self.report({"WARNING"} if data["affected_profiles"] else {"INFO"},
                    f"{data['profiles_checked']} profiles checked; {data['affected_profiles']} need review. See {REPORT_NAME}")
        return {"FINISHED"}


class HTUE_OT_repair_hair_uv(bpy.types.Operator):
    bl_idname = "htue.repair_hair_uv"
    bl_label = "Repair Selected UV Compatibility"
    bl_description = "Recheck selected objects and add missing equivalent UV attributes on private copies; preserve original layouts"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            data = repair(context.selected_objects)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _report(data)
        count = sum(x["status"] == "repaired" for x in data["repairs"])
        failures = sum(x["status"] == "failed" for x in data["repairs"])
        self.report({"WARNING"} if failures else {"INFO"},
                    f"Repaired {count} profiles; {failures} failed. See {REPORT_NAME}")
        return {"FINISHED"}


class HTUE_PT_hair_uv_compat(bpy.types.Panel):
    bl_label = "Hair Compatibility"
    bl_idname = "HTUE_PT_hair_uv_compat"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Unreal Bridge"

    def draw(self, context):
        layout = self.layout
        if context.object:
            data = audit([context.object])
            box = layout.box()
            box.label(text=context.object.name, icon="OBJECT_DATA")
            if not data["profiles_checked"]:
                box.label(text="No Hair Tool UV Profile")
            elif not data["affected_profiles"]:
                box.label(text="UV compatibility OK", icon="CHECKMARK")
            else:
                for profile in data["profiles"]:
                    for issue in profile["issues"][:3]:
                        box.label(text=issue["code"].replace("_", " "), icon="ERROR")
                    if profile["repairable"]:
                        box.label(text="Equivalent UV data can be restored")
        layout.operator("htue.audit_hair_uv", icon="VIEWZOOM")
        layout.operator("htue.repair_hair_uv", icon="TOOL_SETTINGS")
        layout.label(text="Details: Text Editor > Compatibility Report")
        layout.label(text="UV formats only; custom graphs need review")


CLASSES = (HTUE_OT_audit_hair_uv, HTUE_OT_repair_hair_uv, HTUE_PT_hair_uv_compat)
