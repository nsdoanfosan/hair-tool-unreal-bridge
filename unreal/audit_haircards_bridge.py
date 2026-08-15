import json
import os
from pathlib import Path

import unreal


MASTER_PATH = "/Game/Material/HairTool/Master/M_HT_HairCards"
HANDOFF_JSON = Path(
    os.environ.get(
        "HTUE_HANDOFF_JSON",
        r"C:\UnrealProjects\MyProject2\work\hair_material_json_test.json",
    )
)
RESULT_PATH = Path(
    os.environ.get(
        "HTUE_AUDIT_RESULT_PATH",
        r"C:\UnrealProjects\MyProject2\work\audit_htue_haircards_bridge.json",
    )
)
TOLERANCE = 1.0e-5


def first_json(value):
    candidates = value if isinstance(value, tuple) else (value,)
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.lstrip().startswith("{"):
            return candidate
    return None


def plugin_json(value):
    encoded = first_json(value)
    return json.loads(encoded) if encoded else {"raw": repr(value)}


def object_package_path(value):
    if value is None:
        return None
    return value.get_path_name().split(".", 1)[0]


def color_values(value):
    return [float(value.r), float(value.g), float(value.b), float(value.a)]


def load_handoff_entries():
    payload = json.loads(HANDOFF_JSON.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("handoff")
    if not isinstance(payload, list):
        raise ValueError("handoff JSON is not a list")
    return payload


def close_enough(actual, expected):
    return abs(float(actual) - float(expected)) <= TOLERANCE


MEL = unreal.MaterialEditingLibrary
errors = []
result = {
    "master": MASTER_PATH,
    "handoff": str(HANDOFF_JSON),
    "compile_errors": [],
    "graph": {},
    "root_inputs": {},
    "instances": [],
    "errors": errors,
}

master = unreal.load_asset(MASTER_PATH)
if master is None:
    errors.append(f"missing master: {MASTER_PATH}")
else:
    result["compile_errors"] = [
        str(item) for item in (MEL.recompile_material(master) or [])
    ]
    if result["compile_errors"]:
        errors.extend(
            f"master compile: {message}" for message in result["compile_errors"]
        )
    graph = plugin_json(
        unreal.CodexMaterialToolsLibrary.dump_material_expression_graph(MASTER_PATH)
    )
    root_inputs = plugin_json(
        unreal.CodexMaterialToolsLibrary.dump_material_root_inputs(MASTER_PATH)
    )
    result["graph"] = graph
    result["root_inputs"] = root_inputs
    blend_calls = [
        expression
        for expression in graph.get("expressions", [])
        if str(expression.get("function", "")).endswith("blendFunc.blendFunc")
    ]
    result["blend_function_call_count"] = len(blend_calls)
    if len(blend_calls) != 6:
        errors.append(f"expected 6 blendFunc calls, got {len(blend_calls)}")

scalar_names = {str(name) for name in MEL.get_scalar_parameter_names(master)} if master else set()
vector_names = {str(name) for name in MEL.get_vector_parameter_names(master)} if master else set()
texture_names = {str(name) for name in MEL.get_texture_parameter_names(master)} if master else set()
result["master_parameter_names"] = {
    "scalar": sorted(scalar_names),
    "vector": sorted(vector_names),
    "texture": sorted(texture_names),
}

for entry in load_handoff_entries():
    if not str(entry.get("name", "")).startswith("M_HT_"):
        continue
    path = entry["material_instance_path"]
    mi = unreal.load_asset(path)
    row = {"path": path, "exists": mi is not None, "mismatches": []}
    result["instances"].append(row)
    if mi is None:
        errors.append(f"missing instance: {path}")
        continue

    row["parent"] = object_package_path(mi.get_editor_property("parent"))
    if row["parent"] != MASTER_PATH:
        row["mismatches"].append(
            f"parent expected {MASTER_PATH}, got {row['parent']}"
        )

    hair_tool = entry.get("hair_tool") or {}
    sync_parameters = set(hair_tool.get("sync_parameters") or [])
    provided = set((hair_tool.get("scalar_parameters") or {})) | set(
        hair_tool.get("vector_parameters") or {}
    )
    if sync_parameters != provided:
        row["mismatches"].append("sync_parameters differs from provided parameters")

    for name, expected in (hair_tool.get("scalar_parameters") or {}).items():
        if name not in scalar_names:
            row["mismatches"].append(f"master missing scalar: {name}")
            continue
        actual = float(MEL.get_material_instance_scalar_parameter_value(mi, name))
        if not close_enough(actual, expected):
            row["mismatches"].append(
                f"{name}: expected {expected}, got {actual}"
            )

    for name, expected in (hair_tool.get("vector_parameters") or {}).items():
        if name not in vector_names:
            row["mismatches"].append(f"master missing vector: {name}")
            continue
        actual = color_values(
            MEL.get_material_instance_vector_parameter_value(mi, name)
        )
        if len(expected) != 4 or any(
            not close_enough(actual[index], expected[index]) for index in range(4)
        ):
            row["mismatches"].append(
                f"{name}: expected {expected}, got {actual}"
            )

    for texture in entry.get("textures") or []:
        name = texture["param"]
        if name not in texture_names:
            row["mismatches"].append(f"master missing texture: {name}")
            continue
        actual_path = object_package_path(
            MEL.get_material_instance_texture_parameter_value(mi, name)
        )
        expected_path = f"/Game/Textures/{texture['asset_name']}"
        if actual_path != expected_path:
            row["mismatches"].append(
                f"{name}: expected {expected_path}, got {actual_path}"
            )

    if row["mismatches"]:
        errors.extend(f"{path}: {message}" for message in row["mismatches"])

RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
unreal.log(f"HTUE_AUDIT_RESULT:{RESULT_PATH}")
if errors:
    raise RuntimeError("HTUE audit failed; see result JSON")
