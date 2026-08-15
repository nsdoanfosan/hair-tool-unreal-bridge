import json
from pathlib import Path


CONTRACT_VERSION = 3
CONTRACT_SCHEMA = "htue.material.v3"
CONTRACT_PROPERTY = "htue_contract_json"
LEGACY_STATE_PROPERTY = "htue_legacy_state_json"
BRIDGE_NODE_NAME = "HTUE Hair Material Bridge"
BRIDGE_GROUP_PREFIX = "HTUE_ColorBridge"
SHADER_CLONE_PREFIX = "HTUE_HairShaderMain"
INTERNAL_STACK_NODE_NAME = "HTUE Color Stack"
ORIGINAL_SHADER_GROUP_PROPERTY = "htue_original_shader_group"
AUGMENTED_LINKS_PROPERTY = "htue_augmented_hair_links"

TARGET_TEXTURE_SETS = {
    "M_HT_Default_Material_01": "Hair_Long_01",
    "M_HT_Default_Material_blow_01": "Hair_Blow_01",
    "M_HT_Default_Material_short_01": "Hair_Short_01",
    "M_HT_Default_Material_short_02": "Hair_Short_02",
}

DEFAULT_TEXTURE_ROOT = Path(
    r"D:\OneDrive\Forestportfolio\Characters\MainCharacter\03_Hair\texture"
)

TEXTURE_SUFFIXES = {
    "Flow Map": "flow",
    "IRD Map": "IRD",
    "ORM Map": "ORM",
    "Opacity Map": "Opacity",
}

BLEND_MODE_VALUES = {
    "NORMAL": 0.0,
    "MULTIPLY": 1.0,
    "OVERLAY": 2.0,
    "SOFT_LIGHT": 3.0,
    "ADD": 4.0,
}

BLEND_MODE_ITEMS = (
    ("NORMAL", "Normal", "Linear interpolation; a full mask replaces the previous color", 0),
    ("MULTIPLY", "Multiply", "Multiply the previous color by the layer color", 1),
    ("OVERLAY", "Overlay", "Overlay contrast while retaining the previous color", 2),
    ("SOFT_LIGHT", "Soft Light", "Apply a softer contrast-preserving blend", 3),
    ("ADD", "Add", "Add the layer color; matches legacy HairShaderMain SystemColor", 4),
)

VECTOR_FIELDS = {
    "base_color": "HT Base Color",
    "root_color": "HT Root Color",
    "tip_color": "HT Tip Color",
    "id_tint_color": "ID Tint Color",
    "depth_tint_color": "Depth Tint Color",
}

SCALAR_FIELDS = {
    "root_mix": "HT Root Mix",
    "root_range": "HT Root Range",
    "root_random_influence": "HT Root Random Influence",
    "root_random_brightness": "HT Root Random Brightness",
    "root_map_influence": "Root Map Influence",
    "root_blend_mode": "Root Blend Mode",
    "tip_mix": "HT Tip Mix",
    "tip_range": "HT Tip Range",
    "tip_random_influence": "HT Tip Random Influence",
    "tip_random_brightness": "HT Tip Random Brightness",
    "tip_map_influence": "Tip Map Influence",
    "tip_blend_mode": "Tip Blend Mode",
    "id_map_influence": "ID Map Influence",
    "id_tint_influence": "ID Tint Influence",
    "id_blend_mode": "ID Blend Mode",
    "depth_map_influence": "Depth Map Influence",
    "depth_tint_influence": "Depth Tint Influence",
    "depth_blend_mode": "Depth Blend Mode",
    "system_color_influence": "System Color Influence",
    "system_blend_mode": "System Blend Mode",
    "ao_strength": "AO Strength",
    "ao_vertex_influence": "AO Vertex Influence",
    "ao_texture_influence": "AO Texture Influence",
    "ao_color_influence": "AO Color Influence",
    "ao_blend_mode": "AO Blend Mode",
    "roughness_minimum": "Roughness Minimum",
}

BLEND_FIELDS = {
    "root_blend_mode",
    "tip_blend_mode",
    "id_blend_mode",
    "depth_blend_mode",
    "system_blend_mode",
    "ao_blend_mode",
}

def material_instance_path(material_name):
    base_name = material_name[2:] if material_name.startswith("M_") else material_name
    return f"/Game/Material/HairTool/MI/MI_{base_name}"


def texture_entries(texture_root, texture_set):
    root = Path(texture_root)
    entries = []
    for parameter_name, suffix in TEXTURE_SUFFIXES.items():
        source_path = root / f"{texture_set}_{suffix}.tga"
        entries.append(
            {
                "param": parameter_name,
                "asset_name": source_path.stem,
                "file": source_path.as_posix(),
                "virtual_texture_streaming": parameter_name != "Opacity Map",
            }
        )
    return entries


def setting_value(settings, field_name):
    value = getattr(settings, field_name)
    if field_name in BLEND_FIELDS:
        return BLEND_MODE_VALUES[str(value)]
    if hasattr(value, "__len__") and not isinstance(value, str):
        return [float(component) for component in value]
    return float(value)


def build_contract(material_name, settings):
    vectors = {
        unreal_name: setting_value(settings, field_name)
        for field_name, unreal_name in VECTOR_FIELDS.items()
    }
    scalars = {
        unreal_name: setting_value(settings, field_name)
        for field_name, unreal_name in SCALAR_FIELDS.items()
    }
    sync_parameters = sorted(set(vectors) | set(scalars))
    texture_set = str(settings.texture_set or TARGET_TEXTURE_SETS.get(material_name, ""))
    return {
        "schema": CONTRACT_SCHEMA,
        "version": CONTRACT_VERSION,
        "material_instance_path": material_instance_path(material_name),
        "create_if_missing": False,
        "manage_existing_material_instance": True,
        "material_instance_ownership": "pipeline",
        "textures": texture_entries(settings.texture_root, texture_set),
        "hair_tool": {
            "contract_version": CONTRACT_VERSION,
            "control_source_material": material_name,
            "layer_order": ["Base", "System", "Root", "Tip", "ID", "Depth", "AO"],
            "source_ownership": {
                "hair_tool": "evaluated deformer attributes only",
                "bridge": "colors, strengths and blend modes",
                "legacy_hair_shader_blending": "ignored",
            },
            "blend_mode_legend": {
                "0": "Normal",
                "1": "Multiply",
                "2": "Overlay",
                "3": "Soft Light",
                "4": "Add",
            },
            "vertex_color": {
                "name": "RFAOS",
                "R": "Random / ID vertex source",
                "G": "Factor / Root-Tip vertex source",
                "B": "Ambient AO vertex source",
                "A": "Reserved compatibility channel; SystemColor Alpha is ignored",
            },
            "vertex_uv_payload": {
                "version": 3,
                "encoding": "HTUE_RGB_TAGGED_UV",
                "UV1.RG": "SystemColor.RG in linear color space",
                "UV2.R": "6 + packed UNORM8 Random/Depth",
                "UV2.G": "Factor",
                "UV3.R": "6 + Ambient AO",
                "UV3.G": "SystemColor.B in linear color space",
                "system_color_source": "evaluated SystemColor.RGB",
                "system_color_alpha_used": False,
            },
            "texture_channels": {
                "IRD Map.R": "ID texture source",
                "IRD Map.G": "Root texture source; OneMinus is Tip texture source",
                "IRD Map.B": "Depth texture source",
                "ORM Map.R": "Ambient AO texture source",
            },
            "sync_parameters": sync_parameters,
            "vector_parameters": vectors,
            "scalar_parameters": scalars,
        },
    }


def dumps_contract(material_name, settings):
    return json.dumps(build_contract(material_name, settings), sort_keys=True, separators=(",", ":"))


def loads_contract(value):
    if not value:
        return None
    data = json.loads(str(value))
    if data.get("schema") != CONTRACT_SCHEMA:
        raise ValueError(f"Unsupported Hair Tool Unreal bridge schema: {data.get('schema')!r}")
    if int(data.get("version", 0)) != CONTRACT_VERSION:
        raise ValueError(f"Unsupported Hair Tool Unreal bridge version: {data.get('version')!r}")
    return data


def validate_contract(data):
    errors = []
    if data.get("schema") != CONTRACT_SCHEMA:
        errors.append("schema mismatch")
    if int(data.get("version", 0)) != CONTRACT_VERSION:
        errors.append("version mismatch")
    if not str(data.get("material_instance_path", "")).startswith("/Game/Material/HairTool/MI/MI_"):
        errors.append("material_instance_path is outside /Game/Material/HairTool/MI")
    hair_tool = data.get("hair_tool") or {}
    synced = set(hair_tool.get("sync_parameters") or [])
    provided = set((hair_tool.get("vector_parameters") or {})) | set(
        hair_tool.get("scalar_parameters") or {}
    )
    if synced != provided:
        errors.append("sync_parameters does not match the provided parameter set")
    vertex_uv_payload = hair_tool.get("vertex_uv_payload") or {}
    if int(vertex_uv_payload.get("version", 0)) != 3:
        errors.append("vertex_uv_payload must use version 3")
    if vertex_uv_payload.get("encoding") != "HTUE_RGB_TAGGED_UV":
        errors.append("vertex_uv_payload encoding mismatch")
    if vertex_uv_payload.get("system_color_alpha_used") is not False:
        errors.append("SystemColor Alpha must be disabled")
    texture_names = {entry.get("param") for entry in data.get("textures") or []}
    if texture_names != set(TEXTURE_SUFFIXES):
        errors.append("texture roles must be Flow Map, IRD Map, ORM Map and Opacity Map")
    return errors
