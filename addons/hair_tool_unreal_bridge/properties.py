import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, StringProperty

from . import schema


def _update_settings(self, _context):
    if not self.initialized:
        return
    material = self.id_data
    if not isinstance(material, bpy.types.Material):
        return
    from . import contract, nodes

    nodes.sync_material(material)
    contract.persist_material_contract(material)


def color_property(name, default):
    return FloatVectorProperty(
        name=name,
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=default,
        update=_update_settings,
    )


def unit_property(name, default=0.0):
    return FloatProperty(name=name, min=0.0, max=1.0, default=default, update=_update_settings)


def blend_property(name, default="NORMAL"):
    return EnumProperty(
        name=name,
        items=schema.BLEND_MODE_ITEMS,
        default=default,
        update=_update_settings,
    )


class HTUE_MaterialSettings(bpy.types.PropertyGroup):
    initialized: BoolProperty(default=False, options={"HIDDEN"})
    texture_root: StringProperty(
        name="Texture Root",
        subtype="DIR_PATH",
        default=str(schema.DEFAULT_TEXTURE_ROOT),
        update=_update_settings,
    )
    texture_set: StringProperty(name="Texture Set", default="", update=_update_settings)

    base_color: color_property("HT Base Color", (0.02, 0.02, 0.02, 1.0))

    root_color: color_property("HT Root Color", (0.299, 0.115, 0.037, 1.0))
    root_mix: unit_property("HT Root Mix", 1.0)
    root_range: unit_property("HT Root Range", 0.0)
    root_random_influence: unit_property("HT Root Random Influence", 0.0)
    root_random_brightness: FloatProperty(
        name="HT Root Random Brightness",
        min=-1.0,
        max=1.0,
        default=0.5,
        update=_update_settings,
    )
    root_map_influence: unit_property("Root Map Influence", 0.0)
    root_blend_mode: blend_property("Root Blend Mode", "NORMAL")

    tip_color: color_property("HT Tip Color", (0.784, 0.499, 0.303, 1.0))
    tip_mix: unit_property("HT Tip Mix", 1.0)
    tip_range: unit_property("HT Tip Range", 1.0)
    tip_random_influence: unit_property("HT Tip Random Influence", 0.0)
    tip_random_brightness: FloatProperty(
        name="HT Tip Random Brightness",
        min=-1.0,
        max=1.0,
        default=0.0,
        update=_update_settings,
    )
    tip_map_influence: unit_property("Tip Map Influence", 0.0)
    tip_blend_mode: blend_property("Tip Blend Mode", "NORMAL")

    id_map_influence: unit_property("ID Map Influence", 0.0)
    id_tint_color: color_property("ID Tint Color", (1.0, 1.0, 1.0, 1.0))
    id_tint_influence: unit_property("ID Tint Influence", 0.0)
    id_blend_mode: blend_property("ID Blend Mode", "MULTIPLY")

    depth_map_influence: unit_property("Depth Map Influence", 1.0)
    depth_tint_color: color_property("Depth Tint Color", (0.85, 0.85, 0.85, 1.0))
    depth_tint_influence: unit_property("Depth Tint Influence", 0.0)
    depth_blend_mode: blend_property("Depth Blend Mode", "OVERLAY")

    system_color_01: color_property("System Color 01", (0.0, 0.0, 0.0, 1.0))
    system_color_02: color_property("System Color 02", (1.0, 1.0, 1.0, 1.0))
    system_color_influence: unit_property("System Color Influence", 1.0)
    system_mask_contrast: FloatProperty(
        name="System Mask Contrast",
        min=0.0,
        max=8.0,
        default=1.0,
        update=_update_settings,
    )
    system_mask_bias: FloatProperty(
        name="System Mask Bias",
        min=-1.0,
        max=1.0,
        default=0.0,
        update=_update_settings,
    )
    system_mask_invert: unit_property("System Mask Invert", 0.0)
    system_blend_mode: blend_property("System Blend Mode", "ADD")

    ao_strength: unit_property("AO Strength", 1.0)
    ao_vertex_influence: unit_property("AO Vertex Influence", 1.0)
    ao_texture_influence: unit_property("AO Texture Influence", 1.0)
    ao_color_influence: unit_property("AO Color Influence", 0.0)
    ao_blend_mode: blend_property("AO Blend Mode", "MULTIPLY")
    roughness_minimum: unit_property("Roughness Minimum", 0.08)


CLASSES = (HTUE_MaterialSettings,)
