import bpy

from . import schema


class HTUE_PT_MaterialBridge(bpy.types.Panel):
    bl_label = "Hair Tool Unreal Bridge"
    bl_idname = "HTUE_PT_material_bridge"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"

    @classmethod
    def poll(cls, context):
        material = context.material
        return material is not None and (
            material.name in schema.TARGET_TEXTURE_SETS
            or getattr(material.htue_settings, "initialized", False)
        )

    def draw(self, context):
        layout = self.layout
        material = context.material
        settings = material.htue_settings
        if not settings.initialized:
            layout.operator("htue.setup_active_material", icon="NODETREE")
            layout.operator("htue.setup_four_materials", icon="MATERIAL_DATA")
            return

        layout.label(text="Names and values below are identical in Unreal.", icon="LINKED")
        layout.prop(settings, "texture_set")
        layout.prop(settings, "texture_root")

        base = layout.box()
        base.label(text="Base")
        base.prop(settings, "base_color")

        root = layout.box()
        root.label(text="Root — RFAOS.G + IRD.G")
        for prop in ("root_color", "root_mix", "root_range", "root_random_influence", "root_random_brightness", "root_map_influence", "root_blend_mode"):
            root.prop(settings, prop)

        tip = layout.box()
        tip.label(text="Tip — RFAOS.G + OneMinus(IRD.G)")
        for prop in ("tip_color", "tip_mix", "tip_range", "tip_random_influence", "tip_random_brightness", "tip_map_influence", "tip_blend_mode"):
            tip.prop(settings, prop)

        identity = layout.box()
        identity.label(text="ID — RFAOS.R + IRD.R")
        for prop in ("id_map_influence", "id_tint_color", "id_tint_influence", "id_blend_mode"):
            identity.prop(settings, prop)

        depth = layout.box()
        depth.label(text="Depth — Depth attribute + IRD.B")
        for prop in ("depth_map_influence", "depth_tint_color", "depth_tint_influence", "depth_blend_mode"):
            depth.prop(settings, prop)

        system = layout.box()
        system.label(text="System Color — SystemColor alpha / RFAOS.A")
        for prop in ("system_color_01", "system_color_02", "system_color_influence", "system_mask_contrast", "system_mask_bias", "system_mask_invert", "system_blend_mode"):
            system.prop(settings, prop)

        ao = layout.box()
        ao.label(text="AO — RFAOS.B + ORM.R")
        for prop in ("ao_strength", "ao_vertex_influence", "ao_texture_influence", "ao_color_influence", "ao_blend_mode", "roughness_minimum"):
            ao.prop(settings, prop)

        row = layout.row(align=True)
        row.operator("htue.refresh_contract", icon="FILE_REFRESH")
        row.operator("htue.restore_active_material", icon="LOOP_BACK")


CLASSES = (HTUE_PT_MaterialBridge,)
