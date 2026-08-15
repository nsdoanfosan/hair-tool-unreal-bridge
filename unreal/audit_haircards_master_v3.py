import json
import os
from pathlib import Path
import traceback

import unreal


MASTER = "/Game/Material/HairTool/Master/M_HT_HairCards"
INSTANCES = (
    "/Game/Material/HairTool/MI/MI_HT_Default_Material_01",
    "/Game/Material/HairTool/MI/MI_HT_Default_Material_blow_01",
    "/Game/Material/HairTool/MI/MI_HT_Default_Material_short_01",
    "/Game/Material/HairTool/MI/MI_HT_Default_Material_short_02",
)
RESULT_PATH = Path(
    os.environ.get(
        "HTUE_AUDIT_RESULT_PATH",
        r"C:\UnrealProjects\MyProject2\work\audit_htue_haircards_master_v3.json",
    )
)


def first_json(value):
    if isinstance(value, str) and value.lstrip().startswith("{"):
        return value
    if isinstance(value, tuple):
        for item in value:
            if isinstance(item, str) and item.lstrip().startswith("{"):
                return item
    return None


def parse_json_call(value):
    text = first_json(value)
    if text is None:
        raise RuntimeError(f"Codex material tool returned no JSON: {value!r}")
    return json.loads(text)


def input_source(expression, input_name, by_id):
    item = next(
        (
            candidate
            for candidate in expression.get("inputs") or []
            if candidate.get("name") == input_name
        ),
        None,
    )
    return by_id.get(item.get("source_id")) if item else None


def mask_signature(expression, by_id):
    if not expression or expression.get("class") != "MaterialExpressionComponentMask":
        return None
    source = input_source(expression, "Input", by_id)
    if source is None:
        inputs = expression.get("inputs") or []
        source = by_id.get(inputs[0].get("source_id")) if inputs else None
    if not source or source.get("class") != "MaterialExpressionTextureCoordinate":
        return None
    channel = "".join(
        letter
        for letter, key in (("R", "mask_r"), ("G", "mask_g"), ("B", "mask_b"), ("A", "mask_a"))
        if expression.get(key)
    )
    return [int(source.get("coordinate_index", -1)), channel]


def saturated_mask_signature(expression, by_id):
    if not expression or expression.get("class") != "MaterialExpressionSaturate":
        return None
    source = input_source(expression, "Input", by_id)
    if source is None:
        inputs = expression.get("inputs") or []
        source = by_id.get(inputs[0].get("source_id")) if inputs else None
    return mask_signature(source, by_id)


def constant_value(expression):
    if not expression or expression.get("class") != "MaterialExpressionConstant":
        return None
    for key in ("r", "value", "default_value"):
        value = expression.get(key)
        if value is not None:
            return float(value)
    captions = expression.get("captions") or []
    try:
        return float(captions[0])
    except (IndexError, TypeError, ValueError):
        return None


report = {
    "schema": "htue.unreal.material-audit.v3",
    "master": MASTER,
    "instances": {},
    "errors": [],
}

try:
    material = unreal.EditorAssetLibrary.load_asset(MASTER)
    if material is None:
        raise RuntimeError(f"Master material is missing: {MASTER}")

    compile_errors = [
        str(item)
        for item in (unreal.MaterialEditingLibrary.recompile_material(material) or [])
    ]
    scalar_parameters = sorted(
        str(name)
        for name in unreal.MaterialEditingLibrary.get_scalar_parameter_names(material)
    )
    vector_parameters = sorted(
        str(name)
        for name in unreal.MaterialEditingLibrary.get_vector_parameter_names(material)
    )
    texture_parameters = sorted(
        str(name)
        for name in unreal.MaterialEditingLibrary.get_texture_parameter_names(material)
    )
    graph = parse_json_call(
        unreal.CodexGraphDumpToolsLibrary.dump_material_expression_graph(MASTER)
    )
    expressions = graph.get("expressions") or []
    by_id = {expression.get("id"): expression for expression in expressions}

    texcoord_indices = sorted(
        int(expression.get("coordinate_index", -1))
        for expression in expressions
        if expression.get("class") == "MaterialExpressionTextureCoordinate"
    )
    payload_masks = sorted(
        signature
        for expression in expressions
        if (signature := mask_signature(expression, by_id)) is not None
        and signature[0] in (1, 2, 3)
    )

    rgb_assembly_count = 0
    for expression in expressions:
        if expression.get("class") != "MaterialExpressionAppendVector":
            continue
        inner = input_source(expression, "A", by_id)
        blue = input_source(expression, "B", by_id)
        if not inner or inner.get("class") != "MaterialExpressionAppendVector":
            continue
        red = input_source(inner, "A", by_id)
        green = input_source(inner, "B", by_id)
        if (
            saturated_mask_signature(red, by_id) == [1, "R"]
            and saturated_mask_signature(green, by_id) == [1, "G"]
            and saturated_mask_signature(blue, by_id) == [3, "G"]
        ):
            rgb_assembly_count += 1

    tag_bounds = []
    for expression in expressions:
        if expression.get("class") != "MaterialExpressionIf":
            continue
        left = input_source(expression, "A", by_id)
        right = input_source(expression, "B", by_id)
        signature = mask_signature(left, by_id)
        bound = constant_value(right)
        if signature in ([2, "R"], [3, "R"]) and bound is not None:
            tag_bounds.append(round(bound, 5))
    tag_bounds.sort()

    obsolete_scalars = sorted(
        set(scalar_parameters)
        & {"System Mask Contrast", "System Mask Bias", "System Mask Invert"}
    )
    obsolete_vectors = sorted(
        set(vector_parameters) & {"System Color 01", "System Color 02"}
    )
    required_scalars = {
        "System Color Influence",
        "System Blend Mode",
        "HT Root Mix",
        "HT Tip Mix",
        "AO Strength",
    }
    missing_scalars = sorted(required_scalars - set(scalar_parameters))

    report["master_result"] = {
        "expression_count": len(expressions),
        "compile_errors": compile_errors,
        "scalar_parameter_count": len(scalar_parameters),
        "vector_parameter_count": len(vector_parameters),
        "texture_parameters": texture_parameters,
        "payload_texcoord_indices": sorted(set(index for index in texcoord_indices if index in (1, 2, 3))),
        "payload_masks": payload_masks,
        "rgb_assembly_count": rgb_assembly_count,
        "tag_bounds": tag_bounds,
        "obsolete_scalars": obsolete_scalars,
        "obsolete_vectors": obsolete_vectors,
        "missing_required_scalars": missing_scalars,
    }

    if compile_errors:
        report["errors"].append(f"Compile errors: {compile_errors}")
    if report["master_result"]["payload_texcoord_indices"] != [1, 2, 3]:
        report["errors"].append("The saved graph does not read UV1, UV2 and UV3")
    for required_mask in ([1, "R"], [1, "G"], [2, "R"], [2, "G"], [3, "R"], [3, "G"]):
        if required_mask not in payload_masks:
            report["errors"].append(f"Missing payload mask: {required_mask}")
    if rgb_assembly_count != 1:
        report["errors"].append(
            f"Expected one UV1.RG + UV3.G SystemColor assembly, got {rgb_assembly_count}"
        )
    if tag_bounds != [5.99, 5.99, 7.01, 7.01]:
        report["errors"].append(f"Payload v3 tag bounds are wrong: {tag_bounds}")
    if obsolete_scalars or obsolete_vectors:
        report["errors"].append(
            f"Obsolete alpha-classification parameters remain: {obsolete_scalars + obsolete_vectors}"
        )
    if missing_scalars:
        report["errors"].append(f"Required synchronized parameters are missing: {missing_scalars}")

    for path in INSTANCES:
        instance = unreal.EditorAssetLibrary.load_asset(path)
        if instance is None:
            report["errors"].append(f"Material instance is missing: {path}")
            continue
        parent = instance.get_editor_property("parent")
        parent_path = parent.get_path_name() if parent else None
        report["instances"][path] = {
            "parent": parent_path,
            "system_color_influence": float(
                unreal.MaterialEditingLibrary.get_material_instance_scalar_parameter_value(
                    instance, "System Color Influence"
                )
            ),
            "system_blend_mode": float(
                unreal.MaterialEditingLibrary.get_material_instance_scalar_parameter_value(
                    instance, "System Blend Mode"
                )
            ),
        }
        parent_package = parent_path.split(".", 1)[0] if parent_path else None
        if parent_package != MASTER:
            report["errors"].append(f"Wrong parent for {path}: {parent_path}")
except Exception as exc:
    report["errors"].append(f"{exc}\n{traceback.format_exc()}")

RESULT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
unreal.log("CODEX_AUDIT_HTUE_HAIRCARDS_MASTER_V3_DONE")
if report["errors"]:
    raise RuntimeError("HTUE HairCards v3 audit failed; see result JSON")
