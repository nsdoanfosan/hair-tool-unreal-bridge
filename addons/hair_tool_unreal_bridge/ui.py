import bpy

from . import schema


def _settings_layout(layout):
    layout.use_property_split = True
    layout.use_property_decorate = False
    return layout


def _properties(layout, settings, names):
    for name in names:
        layout.prop(settings, name)


class _HTUEPanel:
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


class HTUE_PT_MaterialBridge(_HTUEPanel, bpy.types.Panel):
    bl_label = "Hair Tool Unreal Bridge"
    bl_idname = "HTUE_PT_material_bridge"

    def draw_header(self, context):
        settings = context.material.htue_settings
        self.layout.label(text="", icon="CHECKMARK" if settings.initialized else "UNLINKED")

    def draw(self, context):
        layout = _settings_layout(self.layout)
        material = context.material
        settings = material.htue_settings
        if not settings.initialized:
            column = layout.column(align=True)
            column.operator("htue.setup_active_material", icon="NODETREE")
            column.operator("htue.setup_four_materials", icon="MATERIAL_DATA")
            return

        status = layout.box()
        status.label(text="Hair Tool Deformers drive masks", icon="LINKED")
        status.label(text="Legacy HairShaderMain blending is ignored")
        status.label(text="Base > System > Root > Tip > ID > Depth > AO")
        status.label(text="Unreal contract v3  |  31 synchronized parameters")
        row = layout.row(align=True)
        row.operator("htue.refresh_contract", text="Refresh Hooks + Unreal", icon="FILE_REFRESH")
        row.operator("htue.restore_active_material", text="Restore", icon="LOOP_BACK")


class HTUE_PT_Source(_HTUEPanel, bpy.types.Panel):
    bl_label = "01  Source & Textures"
    bl_idname = "HTUE_PT_source"
    bl_parent_id = "HTUE_PT_material_bridge"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = _settings_layout(self.layout)
        layout.prop(context.material.htue_settings, "texture_set")
        layout.prop(context.material.htue_settings, "texture_root")


class HTUE_PT_Base(_HTUEPanel, bpy.types.Panel):
    bl_label = "02  Base"
    bl_idname = "HTUE_PT_base"
    bl_parent_id = "HTUE_PT_material_bridge"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        _settings_layout(self.layout).prop(context.material.htue_settings, "base_color")


class HTUE_PT_System(_HTUEPanel, bpy.types.Panel):
    bl_label = "03  System Color  |  Set System Color"
    bl_idname = "HTUE_PT_system"
    bl_parent_id = "HTUE_PT_material_bridge"

    def draw(self, context):
        layout = _settings_layout(self.layout)
        settings = context.material.htue_settings
        info = layout.box()
        info.label(text="Source: Hair Tool Deformer SystemColor.RGB", icon="COLOR")
        info.label(text="Unreal: UV1.RG + UV3.G  |  Alpha ignored")
        _properties(layout, settings, (
            "system_color_influence", "system_blend_mode",
        ))


class HTUE_PT_Root(_HTUEPanel, bpy.types.Panel):
    bl_label = "04  Root  |  Set Factor + IRD.G"
    bl_idname = "HTUE_PT_root"
    bl_parent_id = "HTUE_PT_material_bridge"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = _settings_layout(self.layout)
        settings = context.material.htue_settings
        if settings.root_range == 0.0:
            layout.label(text="Range 0 uses the full Root layer (Hair Tool behavior)", icon="INFO")
        _properties(layout, settings, (
            "root_color", "root_mix", "root_range", "root_random_influence",
            "root_random_brightness", "root_map_influence", "root_blend_mode",
        ))


class HTUE_PT_Tip(_HTUEPanel, bpy.types.Panel):
    bl_label = "05  Tip  |  Set Factor + OneMinus(IRD.G)"
    bl_idname = "HTUE_PT_tip"
    bl_parent_id = "HTUE_PT_material_bridge"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = _settings_layout(self.layout)
        _properties(layout, context.material.htue_settings, (
            "tip_color", "tip_mix", "tip_range", "tip_random_influence",
            "tip_random_brightness", "tip_map_influence", "tip_blend_mode",
        ))


class HTUE_PT_ID(_HTUEPanel, bpy.types.Panel):
    bl_label = "06  ID  |  Random + IRD.R"
    bl_idname = "HTUE_PT_id"
    bl_parent_id = "HTUE_PT_material_bridge"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = _settings_layout(self.layout)
        _properties(layout, context.material.htue_settings, (
            "id_map_influence", "id_tint_color", "id_tint_influence", "id_blend_mode",
        ))


class HTUE_PT_Depth(_HTUEPanel, bpy.types.Panel):
    bl_label = "07  Depth  |  Hair Tool Depth + IRD.B"
    bl_idname = "HTUE_PT_depth"
    bl_parent_id = "HTUE_PT_material_bridge"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = _settings_layout(self.layout)
        _properties(layout, context.material.htue_settings, (
            "depth_map_influence", "depth_tint_color", "depth_tint_influence", "depth_blend_mode",
        ))


class HTUE_PT_AO(_HTUEPanel, bpy.types.Panel):
    bl_label = "08  AO & Roughness  |  Hair Tool AO + ORM.R"
    bl_idname = "HTUE_PT_ao"
    bl_parent_id = "HTUE_PT_material_bridge"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = _settings_layout(self.layout)
        _properties(layout, context.material.htue_settings, (
            "ao_strength", "ao_vertex_influence", "ao_texture_influence",
            "ao_color_influence", "ao_blend_mode", "roughness_minimum",
        ))


CLASSES = (
    HTUE_PT_MaterialBridge,
    HTUE_PT_Source,
    HTUE_PT_Base,
    HTUE_PT_System,
    HTUE_PT_Root,
    HTUE_PT_Tip,
    HTUE_PT_ID,
    HTUE_PT_Depth,
    HTUE_PT_AO,
)
