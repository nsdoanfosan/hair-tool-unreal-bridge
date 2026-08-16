import math

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)

from . import schema


def _update_setting(field):
    def update(self, _context):
        if not self.initialized:
            return
        material = self.id_data
        if not isinstance(material, bpy.types.Material):
            return
        from . import contract, nodes

        nodes.sync_material_field(material, field)
        contract.persist_material_contract(material)

    return update


def color_property(field, name, default):
    return FloatVectorProperty(
        name=name,
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=default,
        update=_update_setting(field),
    )


def unit_property(field, name, default=0.0):
    return FloatProperty(
        name=name,
        min=0.0,
        max=1.0,
        default=default,
        update=_update_setting(field),
    )


def blend_property(field, name, default="NORMAL"):
    return EnumProperty(
        name=name,
        items=schema.BLEND_MODE_ITEMS,
        default=default,
        update=_update_setting(field),
    )


def _update_ao_bake_setting(self, _context):
    root = self.id_data
    if not isinstance(root, bpy.types.Object):
        return
    from . import deformer_sync

    for preview in deformer_sync._preview_objects(root):
        preview["_htue_combined_ao_preview_stale"] = True


class HTUE_AOBakeSettings(bpy.types.PropertyGroup):
    initialized: BoolProperty(default=False, options={"HIDDEN"})
    evaluation_mode: EnumProperty(
        name="AO Evaluation Mode",
        items=(
            (
                "PER_SYSTEM",
                "Per System",
                "Evaluate AO on each Hair Tool system, then preserve it while joining",
            ),
            (
                "COMBINED",
                "Combined",
                "Join the generated card geometry first, then evaluate AO once",
            ),
        ),
        default="PER_SYSTEM",
        update=_update_ao_bake_setting,
    )
    samples: IntProperty(
        name="Samples", min=1, max=64, default=8, update=_update_ao_bake_setting
    )
    base_color_value: FloatProperty(
        name="Base Color Value",
        min=0.0,
        max=1.0,
        default=0.0,
        update=_update_ao_bake_setting,
    )
    spread_angle: FloatProperty(
        name="Spread Angle",
        subtype="ANGLE",
        min=0.0,
        max=math.pi,
        default=math.radians(60.0),
        update=_update_ao_bake_setting,
    )
    blur_steps: IntProperty(
        name="Blur Steps", min=0, max=16, default=1, update=_update_ao_bake_setting
    )
    first_bounce_factor: FloatProperty(
        name="First Bounce Factor",
        min=0.0,
        max=2.0,
        default=0.6,
        update=_update_ao_bake_setting,
    )
    second_bounce_factor: FloatProperty(
        name="Second Bounce Factor",
        min=0.0,
        max=2.0,
        default=0.4,
        update=_update_ao_bake_setting,
    )
    combined_max_ray_distance: FloatProperty(
        name="Max Ray Distance",
        description=(
            "Maximum Hair Tool AO ray distance used only after the generated "
            "systems are joined in Combined mode"
        ),
        subtype="DISTANCE",
        min=0.000001,
        soft_max=0.1,
        default=0.011,
        precision=4,
        update=_update_ao_bake_setting,
    )
    use_custom_normals: BoolProperty(
        name="Use Custom Normals", default=False, update=_update_ao_bake_setting
    )


class HTUE_MaterialSettings(bpy.types.PropertyGroup):
    initialized: BoolProperty(default=False, options={"HIDDEN"})
    texture_root: StringProperty(
        name="Texture Root",
        subtype="DIR_PATH",
        default=str(schema.DEFAULT_TEXTURE_ROOT),
        update=_update_setting("texture_root"),
    )
    texture_set: StringProperty(
        name="Texture Set",
        default="",
        update=_update_setting("texture_set"),
    )

    base_color: color_property("base_color", "HT Base Color", (0.02, 0.02, 0.02, 1.0))

    root_color: color_property("root_color", "HT Root Color", (0.299, 0.115, 0.037, 1.0))
    root_mix: unit_property("root_mix", "HT Root Mix", 1.0)
    root_range: unit_property("root_range", "HT Root Range", 0.0)
    root_random_influence: unit_property(
        "root_random_influence", "HT Root Random Influence", 0.0
    )
    root_random_brightness: FloatProperty(
        name="HT Root Random Brightness",
        min=-1.0,
        max=1.0,
        default=0.5,
        update=_update_setting("root_random_brightness"),
    )
    root_map_influence: unit_property("root_map_influence", "Root Map Influence", 0.0)
    root_blend_mode: blend_property("root_blend_mode", "Root Blend Mode", "NORMAL")

    tip_color: color_property("tip_color", "HT Tip Color", (0.784, 0.499, 0.303, 1.0))
    tip_mix: unit_property("tip_mix", "HT Tip Mix", 1.0)
    tip_range: unit_property("tip_range", "HT Tip Range", 1.0)
    tip_random_influence: unit_property(
        "tip_random_influence", "HT Tip Random Influence", 0.0
    )
    tip_random_brightness: FloatProperty(
        name="HT Tip Random Brightness",
        min=-1.0,
        max=1.0,
        default=0.0,
        update=_update_setting("tip_random_brightness"),
    )
    tip_map_influence: unit_property("tip_map_influence", "Tip Map Influence", 0.0)
    tip_blend_mode: blend_property("tip_blend_mode", "Tip Blend Mode", "NORMAL")

    id_map_influence: unit_property("id_map_influence", "ID Map Influence", 0.0)
    id_tint_color: color_property(
        "id_tint_color", "ID Tint Color", (1.0, 1.0, 1.0, 1.0)
    )
    id_tint_influence: unit_property("id_tint_influence", "ID Tint Influence", 0.0)
    id_blend_mode: blend_property("id_blend_mode", "ID Blend Mode", "MULTIPLY")

    depth_map_influence: unit_property("depth_map_influence", "Depth Map Influence", 1.0)
    depth_tint_color: color_property(
        "depth_tint_color", "Depth Tint Color", (0.85, 0.85, 0.85, 1.0)
    )
    depth_tint_influence: unit_property(
        "depth_tint_influence", "Depth Tint Influence", 0.0
    )
    depth_blend_mode: blend_property(
        "depth_blend_mode", "Depth Blend Mode", "OVERLAY"
    )

    system_color_influence: unit_property(
        "system_color_influence", "System Color Influence", 1.0
    )
    system_blend_mode: blend_property("system_blend_mode", "System Blend Mode", "ADD")

    ao_strength: unit_property("ao_strength", "AO Strength", 1.0)
    ao_vertex_influence: unit_property("ao_vertex_influence", "AO Vertex Influence", 1.0)
    ao_texture_influence: unit_property(
        "ao_texture_influence", "AO Texture Influence", 1.0
    )
    ao_color_influence: unit_property("ao_color_influence", "AO Color Influence", 0.0)
    ao_blend_mode: blend_property("ao_blend_mode", "AO Blend Mode", "MULTIPLY")
    roughness_minimum: unit_property("roughness_minimum", "Roughness Minimum", 0.08)


CLASSES = (HTUE_AOBakeSettings, HTUE_MaterialSettings)
