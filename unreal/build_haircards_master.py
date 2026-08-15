import json
import os
import traceback
from pathlib import Path

import unreal


MASTER_PATH = "/Game/Material/HairTool/Master/M_HT_HairCards"
MI_FOLDER = "/Game/Material/HairTool/MI"
HANDOFF_JSON = Path(
    globals().get(
        "HTUE_HANDOFF_JSON",
        os.environ.get(
            "HTUE_HANDOFF_JSON",
            r"C:\UnrealProjects\MyProject2\work\hair_material_json_test.json",
        ),
    )
)
RESULT_PATH = Path(
    globals().get(
        "HTUE_RESULT_PATH",
        os.environ.get(
            "HTUE_RESULT_PATH",
            r"C:\UnrealProjects\MyProject2\work\build_htue_haircards_master_result.json",
        ),
    )
)
DITHER_FUNCTION = "/Game/CC_Shaders/StandardShader/MaterialFunctions/Source/DitherTemporalAA"
BLEND_FUNCTION = "/Game/CC_Shaders/HairShader/Source/Functions/blendFunc"

# Remote wrappers can set either flag to False before executing this file.  The
# defaults intentionally preserve the script's original full-update behavior.
UPDATE_TEXTURE_SETTINGS = bool(globals().get("CODEX_UPDATE_TEXTURE_SETTINGS", True))
UPDATE_INSTANCES = bool(globals().get("CODEX_UPDATE_INSTANCES", True))

SYSTEM_COLOR_UV_INDEX = 1
RFAOS_UV_RG_INDEX = 2
RFAOS_UV_BA_INDEX = 3
RFAOS_UV_TAG = 6.0
RFAOS_UV_TAG_LOWER = 5.99
RFAOS_UV_TAG_UPPER = 7.01

# Material Instance groups mirror the Blender panel order. Synced controls are
# deliberately separated from values that remain Unreal-only.
GROUP_SYNC_TEXTURES = "01 | HTUE SYNC - Textures"
GROUP_SYNC_BASE = "02 | HTUE SYNC - Base"
GROUP_SYNC_SYSTEM = "03 | HTUE SYNC - System Color"
GROUP_SYNC_ROOT = "04 | HTUE SYNC - Root"
GROUP_SYNC_TIP = "05 | HTUE SYNC - Tip"
GROUP_SYNC_ID = "06 | HTUE SYNC - ID"
GROUP_SYNC_DEPTH = "07 | HTUE SYNC - Depth"
GROUP_SYNC_AO = "08 | HTUE SYNC - AO & Roughness"
GROUP_UNREAL_UV = "90 | UNREAL ONLY - UV"
GROUP_UNREAL_SURFACE = "91 | UNREAL ONLY - Surface & Flow"
GROUP_UNREAL_OPACITY = "92 | UNREAL ONLY - Opacity"

INSTANCE_PRESERVED_SCALAR_PARAMETERS = {
    "System Color Mix",
    "System Color Multiply",
    "Roughness Multiplier",
}
INSTANCE_PRESERVED_VECTOR_PARAMETERS = set()

EAL = unreal.EditorAssetLibrary
MEL = unreal.MaterialEditingLibrary
ASSET_TOOLS = unreal.AssetToolsHelpers.get_asset_tools()

result = {
    "master": MASTER_PATH,
    "created_master": False,
    "expressions": 0,
    "instances": [],
    "texture_settings": [],
    "comment_boxes": [],
    "compile_errors": [],
    "errors": [],
    "warnings": [],
}


def first_json(value):
    if isinstance(value, str) and value.lstrip().startswith("{"):
        return value
    if isinstance(value, tuple):
        for item in value:
            if isinstance(item, str) and item.lstrip().startswith("{"):
                return item
    return None


def load_handoff_entries():
    payload = json.loads(HANDOFF_JSON.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("handoff")
    if not isinstance(payload, list):
        raise ValueError(
            f"Hair handoff must be a JSON list or contain a 'handoff' list: {HANDOFF_JSON}"
        )
    return payload


def set_prop(obj, name, value):
    try:
        obj.set_editor_property(name, value)
        return True
    except Exception as exc:
        result["errors"].append(f"set {obj.get_name()}.{name}: {exc}")
        return False


def make(material, class_name, x, y):
    cls = getattr(unreal, class_name, None)
    if cls is None:
        raise RuntimeError(f"Unreal expression class missing: {class_name}")
    node = MEL.create_material_expression(material, cls, x, y)
    if node is None:
        raise RuntimeError(f"Could not create {class_name}")
    return node


def connect(source, output_name, target, input_name):
    ok = MEL.connect_material_expressions(source, output_name, target, input_name)
    if not ok:
        raise RuntimeError(
            f"Connect failed: {source.get_name()}.{output_name} -> "
            f"{target.get_name()}.{input_name}"
        )


def connect_property(source, output_name, property_name):
    prop = getattr(unreal.MaterialProperty, property_name, None)
    if prop is None:
        raise RuntimeError(f"Material property missing: {property_name}")
    ok = MEL.connect_material_property(source, output_name, prop)
    if not ok:
        raise RuntimeError(
            f"Material property connect failed: {source.get_name()}.{output_name} -> {property_name}"
        )


def parameter_common(node, name, group, order, description):
    if "HTUE SYNC" in group:
        description = (
            f"[HTUE SYNC] {description}. Re-export overwrites this parameter."
        )
    elif "UNREAL ONLY" in group:
        description = (
            f"[UNREAL ONLY] {description}. Existing MI values survive re-export."
        )
    set_prop(node, "parameter_name", name)
    set_prop(node, "group", group)
    set_prop(node, "sort_priority", order)
    set_prop(node, "desc", description)
    return node


def scalar(material, name, default, group, order, x, y, description):
    node = make(material, "MaterialExpressionScalarParameter", x, y)
    parameter_common(node, name, group, order, description)
    set_prop(node, "default_value", float(default))
    return node


def vector(material, name, default, group, order, x, y, description):
    node = make(material, "MaterialExpressionVectorParameter", x, y)
    parameter_common(node, name, group, order, description)
    values = list(default)
    while len(values) < 4:
        values.append(1.0)
    set_prop(
        node,
        "default_value",
        unreal.LinearColor(float(values[0]), float(values[1]), float(values[2]), float(values[3])),
    )
    return node


def constant(material, value, x, y):
    node = make(material, "MaterialExpressionConstant", x, y)
    set_prop(node, "r", float(value))
    return node


def binary(material, class_name, a, b, x, y, a_output="", b_output=""):
    node = make(material, class_name, x, y)
    connect(a, a_output, node, "A")
    connect(b, b_output, node, "B")
    return node


def unary(material, class_name, source, x, y, output_name="", input_name="None"):
    node = make(material, class_name, x, y)
    connect(source, output_name, node, input_name)
    return node


def lerp(material, a, b, alpha, x, y, a_output="", b_output="", alpha_output=""):
    node = make(material, "MaterialExpressionLinearInterpolate", x, y)
    connect(a, a_output, node, "A")
    connect(b, b_output, node, "B")
    connect(alpha, alpha_output, node, "Alpha")
    return node


def blend_stage(material, base, layer, alpha, mode, x, y):
    """Apply the shared 0 Normal / 1 Multiply / 2 Overlay / 3 SoftLight / 4 Add contract."""
    blend_function = EAL.load_asset(BLEND_FUNCTION)
    if blend_function is None:
        raise RuntimeError(f"Hair blend function missing: {BLEND_FUNCTION}")
    function_call = make(material, "MaterialExpressionMaterialFunctionCall", x, y)
    set_prop(function_call, "material_function", blend_function)
    connect(base, "", function_call, "base")
    connect(layer, "", function_call, "blend")
    connect(alpha, "", function_call, "t")
    connect(mode, "", function_call, "blendType")

    added = binary(material, "MaterialExpressionAdd", base, layer, x, y + 150)
    add_result = lerp(material, base, added, alpha, x + 190, y + 150)
    add_threshold = constant(material, 3.5, x + 190, y + 260)
    selector = make(material, "MaterialExpressionIf", x + 390, y + 70)
    connect(mode, "", selector, "A")
    connect(add_threshold, "", selector, "B")
    connect(add_result, "", selector, "A > B")
    # Reallusion's blendFunc output is intentionally unnamed ("None" in the
    # graph dump), so an empty output name selects index 0.
    connect(function_call, "", selector, "A == B")
    connect(function_call, "", selector, "A < B")
    return selector


def power(material, base, exponent, x, y, base_output="", exponent_output=""):
    node = make(material, "MaterialExpressionPower", x, y)
    connect(base, base_output, node, "Base")
    connect(exponent, exponent_output, node, "Exp")
    return node


def comment(material, text, x, y, width, height):
    node = make(material, "MaterialExpressionComment", x, y)
    set_prop(node, "text", text)
    return node


def append(material, a, b, x, y, a_output="", b_output=""):
    return binary(material, "MaterialExpressionAppendVector", a, b, x, y, a_output, b_output)


def texcoord(material, coordinate_index, x, y, description):
    node = make(material, "MaterialExpressionTextureCoordinate", x, y)
    set_prop(node, "coordinate_index", int(coordinate_index))
    set_prop(node, "desc", description)
    return node


def component_mask(material, source, channel, x, y, output_name=""):
    channel = str(channel).upper()
    if channel not in {"R", "G", "B", "A"}:
        raise ValueError(f"Unsupported component channel: {channel}")
    node = make(material, "MaterialExpressionComponentMask", x, y)
    for component in ("r", "g", "b", "a"):
        set_prop(node, component, component.upper() == channel)
    # UE 5.8 exposes ComponentMask's single source pin with an empty display
    # name through MaterialEditingLibrary (the reflected property is `Input`).
    connect(source, output_name, node, "")
    return node


def tagged_uv_or_vertex(
    material,
    uv_value,
    vertex,
    vertex_output,
    payload_gate,
    x,
    y,
):
    """Select the decoded UV payload only when both tagged UV sets are valid."""
    return lerp(
        material,
        vertex,
        uv_value,
        payload_gate,
        x,
        y,
        a_output=vertex_output,
    )


def interval_gate(material, value, lower, upper, zero, one, x, y):
    """Return 1 when lower < value < upper, otherwise 0."""
    above = make(material, "MaterialExpressionIf", x, y)
    connect(value, "", above, "A")
    connect(lower, "", above, "B")
    connect(one, "", above, "A > B")
    connect(zero, "", above, "A == B")
    connect(zero, "", above, "A < B")

    below = make(material, "MaterialExpressionIf", x, y + 90)
    connect(value, "", below, "A")
    connect(upper, "", below, "B")
    connect(zero, "", below, "A > B")
    connect(zero, "", below, "A == B")
    connect(one, "", below, "A < B")
    return binary(material, "MaterialExpressionMultiply", above, below, x + 190, y + 45)


def positive_gate(material, value, zero, one, x, y):
    """Return 1 only when value is greater than zero, otherwise 0."""
    node = make(material, "MaterialExpressionIf", x, y)
    connect(value, "", node, "A")
    connect(zero, "", node, "B")
    connect(one, "", node, "A > B")
    connect(zero, "", node, "A == B")
    connect(zero, "", node, "A < B")
    return node


def texture_parameter(material, name, texture_path, group, order, x, y, sampler):
    node = make(material, "MaterialExpressionTextureSampleParameter2D", x, y)
    parameter_common(node, name, group, order, f"Hair texture input: {name}")
    texture = EAL.load_asset(texture_path)
    if texture is None:
        result["warnings"].append(f"Default texture missing: {texture_path}")
    else:
        set_prop(node, "texture", texture)
    set_prop(node, "sampler_type", sampler)
    return node


def ensure_texture_settings(asset_name, virtual):
    path = f"/Game/Textures/{asset_name}"
    texture = EAL.load_asset(path)
    if texture is None:
        result["warnings"].append(f"Texture asset missing: {path}")
        return None
    changed = False
    if bool(texture.get_editor_property("srgb")):
        texture.set_editor_property("srgb", False)
        changed = True
    compression = (
        unreal.TextureCompressionSettings.TC_GRAYSCALE
        if asset_name.lower().endswith("_opacity")
        else unreal.TextureCompressionSettings.TC_MASKS
    )
    if texture.get_editor_property("compression_settings") != compression:
        texture.set_editor_property("compression_settings", compression)
        changed = True
    current_vt = bool(texture.get_editor_property("virtual_texture_streaming"))
    if current_vt != bool(virtual):
        if hasattr(texture, "set_virtual_texture_streaming"):
            texture.set_virtual_texture_streaming(bool(virtual))
        else:
            texture.set_editor_property("virtual_texture_streaming", bool(virtual))
        changed = True
    if changed:
        EAL.save_asset(path, only_if_is_dirty=False)
    result["texture_settings"].append({"path": path, "virtual": bool(virtual), "changed": changed})
    return texture


def create_or_load_master():
    if EAL.does_asset_exist(MASTER_PATH):
        material = EAL.load_asset(MASTER_PATH)
    else:
        folder, name = MASTER_PATH.rsplit("/", 1)
        EAL.make_directory(folder)
        material = ASSET_TOOLS.create_asset(name, folder, unreal.Material, unreal.MaterialFactoryNew())
        result["created_master"] = True
    if material is None:
        raise RuntimeError(f"Could not create/load master: {MASTER_PATH}")
    for expression in list(MEL.get_material_expressions(material)):
        MEL.delete_material_expression(material, expression)
    return material


def build_uv(material, prefix, group, y):
    texcoord = make(material, "MaterialExpressionTextureCoordinate", -6000, y)
    u_tile = scalar(material, f"{prefix} U Tiling", 1.0, group, 10, -6000, y + 180, "UV horizontal tiling")
    v_tile = scalar(material, f"{prefix} V Tiling", 1.0, group, 11, -5800, y + 180, "UV vertical tiling")
    u_offset = scalar(material, f"{prefix} U Offset", 0.0, group, 12, -6000, y + 340, "UV horizontal offset")
    v_offset = scalar(material, f"{prefix} V Offset", 0.0, group, 13, -5800, y + 340, "UV vertical offset")
    tiling = append(material, u_tile, v_tile, -5580, y + 190)
    offset = append(material, u_offset, v_offset, -5580, y + 350)
    scaled = binary(material, "MaterialExpressionMultiply", texcoord, tiling, -5350, y + 80)
    return binary(material, "MaterialExpressionAdd", scaled, offset, -5140, y + 80)


def build_master(material):
    set_prop(material, "blend_mode", unreal.BlendMode.BLEND_MASKED)
    set_prop(material, "shading_model", unreal.MaterialShadingModel.MSM_HAIR)
    set_prop(material, "two_sided", True)
    set_prop(material, "used_with_skeletal_mesh", True)
    set_prop(material, "used_with_nanite", True)
    set_prop(material, "opacity_mask_clip_value", 0.3333)

    virtual_masks = getattr(
        unreal.MaterialSamplerType,
        "SAMPLERTYPE_VIRTUAL_MASKS",
        unreal.MaterialSamplerType.SAMPLERTYPE_VIRTUAL_LINEAR_COLOR,
    )
    linear_gray = unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_GRAYSCALE

    comment(material, "01 | TEXTURES + UV\nFlow / IRD(R=ID,G=Root,B=Depth) / ORM(R=AO,G=Roughness,B=Metallic) / Opacity", -6200, -3200, 1900, 1650)
    shared_uv = build_uv(material, "Main UV", GROUP_UNREAL_UV, -2940)
    opacity_uv = build_uv(material, "Opacity UV", GROUP_UNREAL_UV, 1760)

    flow = texture_parameter(material, "Flow Map", "/Game/Textures/Hair_Long_01_flow", GROUP_SYNC_TEXTURES, 0, -4800, -2960, virtual_masks)
    ird = texture_parameter(material, "IRD Map", "/Game/Textures/Hair_Long_01_IRD", GROUP_SYNC_TEXTURES, 1, -4800, -2620, virtual_masks)
    orm = texture_parameter(material, "ORM Map", "/Game/Textures/Hair_Long_01_ORM", GROUP_SYNC_TEXTURES, 2, -4800, -2280, virtual_masks)
    opacity = texture_parameter(material, "Opacity Map", "/Game/Textures/Hair_Long_01_Opacity", GROUP_SYNC_TEXTURES, 3, -4800, 1980, linear_gray)
    for node in (flow, ird, orm):
        connect(shared_uv, "", node, "UVs")
    connect(opacity_uv, "", opacity, "UVs")

    comment(
        material,
        "HTUE RGB NANITE-SAFE PAYLOAD V3\n"
        "UV1 HairTool_SystemColor_RG = SystemColor.RG | "
        "UV2 HairTool_RFAOS_RG = (6+PackUNorm8(Random,Depth), Factor) | "
        "UV3 HairTool_AO_SystemB = (6+AO, SystemColor.B)\n"
        "UV payload is used only when both tagged U values are in 5.99..7.01; "
        "otherwise VertexColor RGBA is preserved.",
        -4700,
        -2020,
        1750,
        980,
    )
    vertex = make(material, "MaterialExpressionVertexColor", -4540, -1740)
    system_rg = texcoord(
        material,
        SYSTEM_COLOR_UV_INDEX,
        -4540,
        -1700,
        "HairTool_SystemColor_RG (UV1): linear SystemColor.RG",
    )
    uv_rg = texcoord(
        material,
        RFAOS_UV_RG_INDEX,
        -4540,
        -1550,
        "HairTool_RFAOS_RG (UV2): U=tag+UNORM8(Random,Depth), V=Factor",
    )
    uv_ba = texcoord(
        material,
        RFAOS_UV_BA_INDEX,
        -4540,
        -1320,
        "HairTool_AO_SystemB (UV3): U=tag+AO, V=linear SystemColor.B",
    )
    system_rg_r = component_mask(material, system_rg, "R", -4300, -1760)
    system_rg_g = component_mask(material, system_rg, "G", -4300, -1670)
    uv_rg_u = component_mask(material, uv_rg, "R", -4300, -1580)
    uv_rg_v = component_mask(material, uv_rg, "G", -4300, -1490)
    uv_ba_u = component_mask(material, uv_ba, "R", -4300, -1350)
    uv_ba_v = component_mask(material, uv_ba, "G", -4300, -1260)
    payload_tag = constant(material, RFAOS_UV_TAG, -4300, -1130)
    payload_lower = constant(material, RFAOS_UV_TAG_LOWER, -4300, -1040)
    payload_upper = constant(material, RFAOS_UV_TAG_UPPER, -4300, -950)
    payload_zero = constant(material, 0.0, -4100, -1040)
    payload_one = constant(material, 1.0, -4100, -950)
    uv_random_depth_packed = binary(
        material,
        "MaterialExpressionSubtract",
        uv_rg_u,
        payload_tag,
        -4080,
        -1580,
    )
    uv_ao_untagged = binary(
        material,
        "MaterialExpressionSubtract",
        uv_ba_u,
        payload_tag,
        -4080,
        -1350,
    )
    pack_scale = constant(material, 65535.0, -4060, -1730)
    pack_half = constant(material, 0.5, -3880, -1730)
    pack_256 = constant(material, 256.0, -3700, -1730)
    pack_255 = constant(material, 255.0, -3520, -1730)
    packed_scaled = binary(
        material,
        "MaterialExpressionMultiply",
        uv_random_depth_packed,
        pack_scale,
        -3880,
        -1640,
    )
    packed_rounded_input = binary(
        material,
        "MaterialExpressionAdd",
        packed_scaled,
        pack_half,
        -3700,
        -1640,
    )
    packed_integer = unary(
        material,
        "MaterialExpressionFloor",
        packed_rounded_input,
        -3520,
        -1640,
    )
    random_quotient = binary(
        material,
        "MaterialExpressionDivide",
        packed_integer,
        pack_256,
        -3340,
        -1730,
    )
    random_byte = unary(
        material,
        "MaterialExpressionFloor",
        random_quotient,
        -3160,
        -1730,
    )
    random_high_bits = binary(
        material,
        "MaterialExpressionMultiply",
        random_byte,
        pack_256,
        -2980,
        -1640,
    )
    depth_byte = binary(
        material,
        "MaterialExpressionSubtract",
        packed_integer,
        random_high_bits,
        -2800,
        -1640,
    )
    uv_random_value = binary(
        material,
        "MaterialExpressionDivide",
        random_byte,
        pack_255,
        -2980,
        -1810,
    )
    uv_depth_value = binary(
        material,
        "MaterialExpressionDivide",
        depth_byte,
        pack_255,
        -2620,
        -1640,
    )
    uv_factor_value = unary(
        material,
        "MaterialExpressionSaturate",
        uv_rg_v,
        -4080,
        -1480,
    )
    uv_ao_value = unary(
        material,
        "MaterialExpressionSaturate",
        uv_ao_untagged,
        -3880,
        -1350,
    )
    uv_system_r = unary(
        material,
        "MaterialExpressionSaturate",
        system_rg_r,
        -4080,
        -1740,
    )
    uv_system_g = unary(
        material,
        "MaterialExpressionSaturate",
        system_rg_g,
        -4080,
        -1650,
    )
    uv_system_b = unary(
        material,
        "MaterialExpressionSaturate",
        uv_ba_v,
        -4080,
        -1250,
    )
    uv_rg_tag_gate = interval_gate(
        material,
        uv_rg_u,
        payload_lower,
        payload_upper,
        payload_zero,
        payload_one,
        -3860,
        -1080,
    )
    uv_ba_tag_gate = interval_gate(
        material,
        uv_ba_u,
        payload_lower,
        payload_upper,
        payload_zero,
        payload_one,
        -3860,
        -900,
    )
    payload_gate = binary(
        material,
        "MaterialExpressionMultiply",
        uv_rg_tag_gate,
        uv_ba_tag_gate,
        -3440,
        -1000,
    )
    rfaos_random = tagged_uv_or_vertex(
        material,
        uv_random_value,
        vertex,
        "R",
        payload_gate,
        -3640,
        -1640,
    )
    rfaos_factor = tagged_uv_or_vertex(
        material,
        uv_factor_value,
        vertex,
        "G",
        payload_gate,
        -3640,
        -1480,
    )
    rfaos_ao = tagged_uv_or_vertex(
        material,
        uv_ao_value,
        vertex,
        "B",
        payload_gate,
        -3640,
        -1320,
    )
    system_rg_value = append(material, uv_system_r, uv_system_g, -3640, -1740)
    system_color_rgb = append(material, system_rg_value, uv_system_b, -3420, -1740)
    rfaos_depth = lerp(
        material,
        payload_zero,
        uv_depth_value,
        payload_gate,
        -2440,
        -1480,
    )
    id_map_influence = scalar(material, "ID Map Influence", 0.0, GROUP_SYNC_ID, 0, -4080, -1320, "0 matches Blender's exported Random attribute; 1 opts into the IRD.R ID map")
    id_driver = lerp(material, rfaos_random, ird, id_map_influence, -3400, -1460, "", "R")

    base_color = vector(material, "HT Base Color", (0.02, 0.02, 0.02, 1), GROUP_SYNC_BASE, 0, -3160, -1740, "Bridge-owned base color")
    comment(material, "02-07 | ORDERED COLOR STACK\nBase > System > Root > Tip > ID > Depth. Hair Tool deformers supply attributes; legacy HairShaderMain blending is not reused.", -3300, -1940, 4050, 2050)

    system_influence = scalar(material, "System Color Influence", 1.0, GROUP_SYNC_SYSTEM, 0, -3160, -1660, "Strength of evaluated Hair Tool SystemColor.RGB; 0 disables the System stage")
    system_blend_mode = scalar(material, "System Blend Mode", 4.0, GROUP_SYNC_SYSTEM, 1, -2960, -1660, "0 Normal, 1 Multiply, 2 Overlay, 3 Soft Light, 4 Add")
    system_payload_influence = binary(
        material,
        "MaterialExpressionMultiply",
        system_influence,
        payload_gate,
        -2700,
        -1620,
    )
    system_color_result = blend_stage(
        material,
        base_color,
        system_color_rgb,
        system_payload_influence,
        system_blend_mode,
        -2440,
        -1740,
    )

    root_color = vector(material, "HT Root Color", (0.299, 0.115, 0.037, 1), GROUP_SYNC_ROOT, 0, -3160, -1540, "Blender HairShaderMain Root Color")
    tip_color = vector(material, "HT Tip Color", (0.784, 0.499, 0.303, 1), GROUP_SYNC_TIP, 0, -3160, -1340, "Blender HairShaderMain Tip Color")
    root_mix = scalar(material, "HT Root Mix", 1.0, GROUP_SYNC_ROOT, 1, -3160, -1120, "Root color blend strength; 0 disables the Root stage")
    root_range = scalar(material, "HT Root Range", 0.0, GROUP_SYNC_ROOT, 2, -2960, -1120, "Root extent driven by RFAOS.G Factor")
    root_random = scalar(material, "HT Root Random Influence", 0.0, GROUP_SYNC_ROOT, 3, -2760, -1120, "Random/ID variation on root extent")
    root_brightness = scalar(material, "HT Root Random Brightness", 0.5, GROUP_SYNC_ROOT, 4, -2560, -1120, "Random/ID bias for root")
    root_blend_mode = scalar(material, "Root Blend Mode", 0.0, GROUP_SYNC_ROOT, 6, -2360, -1120, "0 Normal, 1 Multiply, 2 Overlay, 3 Soft Light, 4 Add")
    tip_mix = scalar(material, "HT Tip Mix", 1.0, GROUP_SYNC_TIP, 1, -3160, -920, "Tip color blend strength; 0 disables the Tip stage")
    tip_range = scalar(material, "HT Tip Range", 1.0, GROUP_SYNC_TIP, 2, -2960, -920, "Tip extent driven by RFAOS.G Factor")
    tip_random = scalar(material, "HT Tip Random Influence", 0.0, GROUP_SYNC_TIP, 3, -2760, -920, "Random/ID variation on tip extent")
    tip_brightness = scalar(material, "HT Tip Random Brightness", 0.0, GROUP_SYNC_TIP, 4, -2560, -920, "Random/ID bias for tip")
    tip_blend_mode = scalar(material, "Tip Blend Mode", 0.0, GROUP_SYNC_TIP, 6, -2360, -920, "0 Normal, 1 Multiply, 2 Overlay, 3 Soft Light, 4 Add")

    zero = constant(material, 0.0, -2480, -760)
    eps = constant(material, 0.001, -2480, -700)
    one = constant(material, 1.0, -2480, -560)
    root_range_enabled = positive_gate(material, root_range, zero, one, -2320, -1370)
    root_range_safe = binary(material, "MaterialExpressionMax", root_range, eps, -2320, -1120)
    root_ratio = binary(material, "MaterialExpressionDivide", rfaos_factor, root_range_safe, -2120, -1240)
    root_inverse = unary(material, "MaterialExpressionOneMinus", root_ratio, -1920, -1240)
    root_saturate = unary(material, "MaterialExpressionSaturate", root_inverse, -1730, -1240)
    # HairShaderMain Map Range performs safe division. Root Range=0 therefore
    # resolves to its To Min value (1), rather than disabling the root layer.
    root_saturate = lerp(material, one, root_saturate, root_range_enabled, -1510, -1370)
    root_id_add = binary(material, "MaterialExpressionAdd", id_driver, root_brightness, -2320, -930)
    root_extent = lerp(material, one, root_id_add, root_random, -2070, -930)
    root_weight = binary(material, "MaterialExpressionMultiply", root_saturate, root_extent, -1320, -1180)
    root_weight = binary(material, "MaterialExpressionMultiply", root_weight, root_mix, -1120, -1180)
    root_map_influence = scalar(material, "Root Map Influence", 0.0, GROUP_SYNC_ROOT, 5, -1700, -900, "Optional IRD.G root mask; 0 preserves the RFAOS.G vertex mask")
    root_map = lerp(material, one, ird, root_map_influence, -1460, -900, "", "G")
    root_weight = binary(material, "MaterialExpressionMultiply", root_weight, root_map, -900, -1120)
    root_weight = unary(material, "MaterialExpressionSaturate", root_weight, -700, -1120)
    root_result = blend_stage(material, system_color_result, root_color, root_weight, root_blend_mode, -820, -1500)

    tip_range_enabled = positive_gate(material, tip_range, zero, one, -2320, -810)
    tip_start = binary(material, "MaterialExpressionSubtract", one, tip_range, -2320, -680)
    tip_delta = binary(material, "MaterialExpressionSubtract", rfaos_factor, tip_start, -2100, -680)
    tip_range_safe = binary(material, "MaterialExpressionMax", tip_range, eps, -2100, -520)
    tip_ratio = binary(material, "MaterialExpressionDivide", tip_delta, tip_range_safe, -1880, -680)
    tip_saturate = unary(material, "MaterialExpressionSaturate", tip_ratio, -1680, -680)
    tip_saturate = binary(material, "MaterialExpressionMultiply", tip_saturate, tip_range_enabled, -1480, -790)
    tip_id_add = binary(material, "MaterialExpressionAdd", id_driver, tip_brightness, -2320, -350)
    tip_extent = lerp(material, one, tip_id_add, tip_random, -2070, -350)
    tip_weight = binary(material, "MaterialExpressionMultiply", tip_saturate, tip_extent, -1260, -620)
    tip_weight = binary(material, "MaterialExpressionMultiply", tip_weight, tip_mix, -1060, -620)
    tip_map_influence = scalar(material, "Tip Map Influence", 0.0, GROUP_SYNC_TIP, 5, -1500, -500, "Optional OneMinus(IRD.G) tip mask; 0 preserves the RFAOS.G vertex mask")
    tip_texture_mask = unary(material, "MaterialExpressionOneMinus", ird, -1300, -450, "G")
    tip_map = lerp(material, one, tip_texture_mask, tip_map_influence, -1120, -450)
    tip_weight = binary(material, "MaterialExpressionMultiply", tip_weight, tip_map, -880, -520)
    tip_weight = unary(material, "MaterialExpressionSaturate", tip_weight, -860, -620)
    hair_tool_color = blend_stage(material, root_result, tip_color, tip_weight, tip_blend_mode, -620, -1180)

    id_tint = vector(material, "ID Tint Color", (1, 1, 1, 1), GROUP_SYNC_ID, 1, -420, -860, "Optional tint selected by the blended vertex/texture ID source")
    id_tint_influence = scalar(material, "ID Tint Influence", 0.0, GROUP_SYNC_ID, 2, -420, -660, "Strength of the ID tint; 0 disables the ID tint stage")
    id_blend_mode = scalar(material, "ID Blend Mode", 1.0, GROUP_SYNC_ID, 3, -220, -660, "0 Normal, 1 Multiply, 2 Overlay, 3 Soft Light, 4 Add")
    id_mask = binary(material, "MaterialExpressionMultiply", id_driver, id_tint_influence, -180, -760)
    id_result = blend_stage(material, hair_tool_color, id_tint, id_mask, id_blend_mode, 20, -1080)

    depth_map_influence = scalar(material, "Depth Map Influence", 1.0, GROUP_SYNC_DEPTH, 0, -420, -420, "0 uses packed Depth vertex data; 1 uses IRD.B")
    depth_driver = lerp(material, rfaos_depth, ird, depth_map_influence, -180, -440, "", "B")
    depth_tint = vector(material, "Depth Tint Color", (0.85, 0.85, 0.85, 1), GROUP_SYNC_DEPTH, 1, -420, -220, "Optional depth tint")
    depth_influence = scalar(material, "Depth Tint Influence", 0.0, GROUP_SYNC_DEPTH, 2, -220, -220, "Strength of the blended vertex/texture depth source; 0 disables the Depth tint stage")
    depth_blend_mode = scalar(material, "Depth Blend Mode", 2.0, GROUP_SYNC_DEPTH, 3, -20, -220, "0 Normal, 1 Multiply, 2 Overlay, 3 Soft Light, 4 Add")
    depth_mask = binary(material, "MaterialExpressionMultiply", depth_driver, depth_influence, 40, -420)
    depth_result = blend_stage(material, id_result, depth_tint, depth_mask, depth_blend_mode, 240, -500)

    comment(material, "05 | SURFACE + FLOW\nORM and RFAOS.B feed Hair BSDF surface values; Flow supplies tangent direction.", 1120, -700, 1850, 1250)
    ao_strength = scalar(material, "AO Strength", 1.0, GROUP_SYNC_AO, 0, 1320, -420, "Strength of the combined vertex and texture AO; 0 disables AO")
    ao_vertex_influence = scalar(material, "AO Vertex Influence", 1.0, GROUP_SYNC_AO, 1, 1320, -300, "Strength of RFAOS.B vertex AO")
    ao_texture_influence = scalar(material, "AO Texture Influence", 1.0, GROUP_SYNC_AO, 2, 1320, -180, "Strength of ORM.R texture AO")
    ao_color_influence = scalar(material, "AO Color Influence", 0.0, GROUP_SYNC_AO, 3, 1320, -60, "Optional AO contribution to Base Color; 0 keeps AO out of color")
    ao_blend_mode = scalar(material, "AO Blend Mode", 1.0, GROUP_SYNC_AO, 4, 1320, 60, "0 Normal, 1 Multiply, 2 Overlay, 3 Soft Light, 4 Add")
    ao_vertex = lerp(material, one, rfaos_ao, ao_vertex_influence, 1540, -420)
    ao_texture = lerp(material, one, orm, ao_texture_influence, 1540, -260, "", "R")
    ao_combined = binary(material, "MaterialExpressionMultiply", ao_vertex, ao_texture, 1760, -320)
    ao_result = lerp(material, one, ao_combined, ao_strength, 1960, -320)
    # blendFunc declares both color operands as Vector3. Explicitly broadcast
    # the scalar AO mask to RGB; implicit scalar promotion leaves the custom
    # Overlay/SoftLight HLSL with invalid .g/.b swizzles.
    ao_rg = append(material, ao_result, ao_result, 1960, -520)
    ao_rgb = append(material, ao_rg, ao_result, 2140, -520)
    final_color = blend_stage(material, depth_result, ao_rgb, ao_color_influence, ao_blend_mode, 2320, -920)
    rough_mult = scalar(material, "Roughness Multiplier", 1.0, GROUP_UNREAL_SURFACE, 0, 1320, 180, "Multiplies ORM.G roughness")
    rough_min = scalar(material, "Roughness Minimum", 0.08, GROUP_SYNC_AO, 5, 1520, -100, "Minimum roughness from Blender SpecRoughness")
    rough_scaled = binary(material, "MaterialExpressionMultiply", orm, rough_mult, 1760, -80, "G")
    roughness = binary(material, "MaterialExpressionMax", rough_scaled, rough_min, 1980, -80)
    specular_base = scalar(material, "Hair Specular", 0.5, GROUP_UNREAL_SURFACE, 1, 1320, 120, "Base Hair BSDF specular")
    metallic_to_spec = scalar(material, "ORM Metallic To Specular", 0.0, GROUP_UNREAL_SURFACE, 2, 1520, 120, "Hair BSDF has no metallic input; optionally map ORM.B to Specular")
    specular = lerp(material, specular_base, orm, metallic_to_spec, 1980, 120, "", "B")
    scatter = scalar(material, "Scatter", 1.0, GROUP_UNREAL_SURFACE, 3, 1320, 320, "Hair BSDF scatter")
    backlit = scalar(material, "Backlit", 0.0, GROUP_UNREAL_SURFACE, 4, 1520, 320, "Hair BSDF backlight strength")

    flow_flip = scalar(material, "Flow Flip Green", 0.0, GROUP_UNREAL_SURFACE, 5, -360, 280, "0 keeps Flow.G, 1 flips it")
    flow_g_inv = unary(material, "MaterialExpressionOneMinus", flow, -140, 400, "G")
    flow_g = lerp(material, flow, flow_g_inv, flow_flip, 80, 360, "G")
    two = constant(material, 2.0, 300, 520)
    minus_one = constant(material, -1.0, 300, 660)
    flow_r2 = binary(material, "MaterialExpressionMultiply", flow, two, 300, 220, "R")
    flow_g2 = binary(material, "MaterialExpressionMultiply", flow_g, two, 300, 380)
    flow_r_decoded = binary(material, "MaterialExpressionAdd", flow_r2, minus_one, 520, 220)
    flow_g_decoded = binary(material, "MaterialExpressionAdd", flow_g2, minus_one, 520, 380)
    flow_rg = append(material, flow_r_decoded, flow_g_decoded, 740, 300)
    zero = constant(material, 0.0, 740, 500)
    flow_xyz = append(material, flow_rg, zero, 960, 300)
    flow_tangent = unary(
        material,
        "MaterialExpressionNormalize",
        flow_xyz,
        1160,
        300,
        input_name="VectorInput",
    )

    hair_bsdf = make(material, "MaterialExpressionSubstrateHairBSDF", 2400, -540)
    connect(final_color, "", hair_bsdf, "BaseColor")
    connect(scatter, "", hair_bsdf, "Scatter")
    connect(specular, "", hair_bsdf, "Specular")
    connect(roughness, "", hair_bsdf, "Roughness")
    connect(backlit, "", hair_bsdf, "Backlit")
    connect(flow_tangent, "", hair_bsdf, "Tangent")
    connect_property(hair_bsdf, "", "MP_FRONT_MATERIAL")
    connect_property(ao_result, "", "MP_AMBIENT_OCCLUSION")

    comment(material, "06 | OPACITY\nRL_Hair-compatible strength, multiplier, power, threshold branch and temporal dither.", -4300, 1220, 3450, 1650)
    alpha_multiplier = scalar(material, "Alpha Multiplier", 1.5, GROUP_UNREAL_OPACITY, 3, -4020, 2320, "RL_Hair alpha pre-multiplier")
    alpha_power = scalar(material, "Alpha Power", 0.7, GROUP_UNREAL_OPACITY, 4, -3820, 2320, "RL_Hair alpha exponent")
    opacity_multiplier = scalar(material, "Opacity Multiplier", 1.0, GROUP_UNREAL_OPACITY, 1, -3620, 2320, "RL_Hair opacity multiplier")
    opacity_strength = scalar(material, "Opacity Strength", 1.0, GROUP_UNREAL_OPACITY, 0, -3420, 2320, "0 bypasses opacity texture, 1 uses it")
    opacity_value = scalar(material, "Opacity", 1.0, GROUP_UNREAL_OPACITY, 2, -3220, 2320, "Global opacity amount")
    opacity_min = scalar(material, "Increase Opacity Mask Min Value", 0.666, GROUP_UNREAL_OPACITY, 5, -3020, 2320, "Threshold selecting the RL_Hair opacity increase branch")
    alpha_multiplied = binary(material, "MaterialExpressionMultiply", opacity, alpha_multiplier, -4020, 2020, "R")
    alpha_shaped = power(material, alpha_multiplied, alpha_power, -3800, 2020)
    alpha_adjusted = binary(material, "MaterialExpressionMultiply", alpha_shaped, opacity_multiplier, -3580, 2020)
    adjusted_strength = lerp(material, one, alpha_adjusted, opacity_strength, -3340, 2020)
    raw_strength = lerp(material, one, opacity, opacity_strength, -3340, 1760, "", "R")
    opacity_pow_exp = constant(material, 0.7, -3120, 1940)
    opacity_power = power(material, opacity_value, opacity_pow_exp, -2920, 1940)
    high_branch = binary(material, "MaterialExpressionMultiply", adjusted_strength, opacity_power, -2700, 2020)
    low_branch = binary(material, "MaterialExpressionMultiply", raw_strength, opacity_value, -2700, 1780)
    opacity_if = make(material, "MaterialExpressionIf", -2440, 1920)
    connect(opacity_value, "", opacity_if, "A")
    connect(opacity_min, "", opacity_if, "B")
    connect(high_branch, "", opacity_if, "A > B")
    connect(high_branch, "", opacity_if, "A == B")
    connect(low_branch, "", opacity_if, "A < B")

    dither_function = EAL.load_asset(DITHER_FUNCTION)
    if dither_function is None:
        raise RuntimeError(f"DitherTemporalAA material function missing: {DITHER_FUNCTION}")
    dither = make(material, "MaterialExpressionMaterialFunctionCall", -2140, 1920)
    set_prop(dither, "material_function", dither_function)
    connect(opacity_if, "", dither, "Alpha Threshold")
    connect_property(dither, "Result", "MP_OPACITY_MASK")

    pdo_strength = scalar(material, "Pixel Depth Offset", 0.0, GROUP_UNREAL_OPACITY, 6, -1900, 2460, "Uses inverse IRD.B depth")
    depth_inverse = unary(material, "MaterialExpressionOneMinus", ird, -1880, 2260, "B")
    pdo = binary(material, "MaterialExpressionMultiply", depth_inverse, pdo_strength, -1660, 2300)
    set_prop(pdo, "desc", "HT HairCards: Pixel Depth Offset")

    result["expressions"] = len(MEL.get_material_expressions(material))


def create_instances(master):
    if not HANDOFF_JSON.exists():
        result["warnings"].append(f"Handoff JSON missing: {HANDOFF_JSON}")
        return
    entries = load_handoff_entries()
    EAL.make_directory(MI_FOLDER)
    for entry in entries:
        if not str(entry.get("name", "")).startswith("M_HT_"):
            continue
        name = entry["material_instance_name"]
        path = f"{MI_FOLDER}/{name}"
        if EAL.does_asset_exist(path):
            mi = EAL.load_asset(path)
            status = "updated"
        else:
            mi = ASSET_TOOLS.create_asset(
                name,
                MI_FOLDER,
                unreal.MaterialInstanceConstant,
                unreal.MaterialInstanceConstantFactoryNew(),
            )
            status = "created"
        if mi is None:
            result["errors"].append(f"Could not create/load MI: {path}")
            continue
        MEL.set_material_instance_parent(mi, master)
        for texture_entry in entry.get("textures", []):
            texture = EAL.load_asset(f"/Game/Textures/{texture_entry['asset_name']}")
            if texture:
                MEL.set_material_instance_texture_parameter_value(mi, texture_entry["param"], texture)
        hair_tool = entry.get("hair_tool") or {}
        # Legacy handoffs intentionally leave some controls under Unreal's
        # ownership. Contract v3 lists only Blender-authoritative parameters;
        # SystemColor itself is per-vertex RGB and has no MI color override.
        sync_parameters = set(hair_tool.get("sync_parameters") or [])
        for param, value in (hair_tool.get("scalar_parameters") or {}).items():
            if param in INSTANCE_PRESERVED_SCALAR_PARAMETERS and param not in sync_parameters:
                continue
            MEL.set_material_instance_scalar_parameter_value(mi, param, float(value))
        for param, value in (hair_tool.get("vector_parameters") or {}).items():
            if param in INSTANCE_PRESERVED_VECTOR_PARAMETERS and param not in sync_parameters:
                continue
            rgba = list(value)
            while len(rgba) < 4:
                rgba.append(1.0)
            MEL.set_material_instance_vector_parameter_value(
                mi,
                param,
                unreal.LinearColor(float(rgba[0]), float(rgba[1]), float(rgba[2]), float(rgba[3])),
            )
        MEL.update_material_instance(mi)
        EAL.save_asset(path, only_if_is_dirty=False)
        result["instances"].append({"path": path, "status": status, "texture_set": entry["textures"][0]["asset_name"].rsplit("_", 1)[0]})


try:
    if UPDATE_TEXTURE_SETTINGS:
        handoff_entries = load_handoff_entries()
        texture_names = {
            texture["asset_name"]
            for entry in handoff_entries
            for texture in entry.get("textures", [])
        }
        for texture_name in sorted(texture_names):
            ensure_texture_settings(texture_name, not texture_name.lower().endswith("_opacity"))

    master = create_or_load_master()
    build_master(master)
    compile_errors = [str(item) for item in (MEL.recompile_material(master) or [])]
    result["compile_errors"] = compile_errors
    if compile_errors:
        raise RuntimeError(
            "Material compilation failed:\n" + "\n".join(compile_errors)
        )
    EAL.save_asset(MASTER_PATH, only_if_is_dirty=False)
    pdo_raw = unreal.CodexMaterialToolsLibrary.connect_material_pixel_depth_offset(
        MASTER_PATH,
        "HT HairCards: Pixel Depth Offset",
        0,
        True,
    )
    pdo_json = first_json(pdo_raw)
    result["pixel_depth_offset"] = json.loads(pdo_json) if pdo_json else repr(pdo_raw)
    for prefix, width, height in (
        ("01 | TEXTURES + UV", 1900, 1650),
        ("HTUE RGB NANITE-SAFE PAYLOAD V3", 1750, 980),
        ("02-07 | ORDERED COLOR STACK", 4050, 2050),
        ("05 | SURFACE + FLOW", 1850, 1250),
        ("06 | OPACITY", 3450, 1650),
    ):
        comment_raw = unreal.CodexMaterialToolsLibrary.configure_material_comment_box(
            MASTER_PATH,
            prefix,
            width,
            height,
            True,
        )
        comment_json = first_json(comment_raw)
        result["comment_boxes"].append(
            json.loads(comment_json) if comment_json else repr(comment_raw)
        )
    if UPDATE_INSTANCES:
        create_instances(master)
except Exception as exc:
    result["errors"].append(f"{exc}\n{traceback.format_exc()}")

RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
unreal.log("CODEX_CREATE_HT_HAIRCARDS_MASTER_DONE")
if result["errors"]:
    raise RuntimeError("HT HairCards master creation failed; see result JSON")
