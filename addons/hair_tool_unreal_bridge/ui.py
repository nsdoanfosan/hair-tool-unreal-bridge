import bpy

from . import deformer_sync, schema


def _settings_layout(layout):
    layout.use_property_split = True
    layout.use_property_decorate = False
    return layout


def _properties(layout, settings, names):
    for name in names:
        layout.prop(settings, name)


def _active_material(context):
    material = getattr(context, "material", None)
    if material is None and context.object is not None:
        material = context.object.active_material
    return material


def _draw_ao_workflow(layout, context):
    root = deformer_sync._find_export_root(context.object)
    if root is None:
        layout.label(text="Select Hair Tool data under an export Empty", icon="INFO")
        return

    deformer_sync.ao_bake_configuration(root)
    settings = root.htue_ao_settings
    layout.label(text=f"Asset: {root.name}", icon="OUTLINER_OB_EMPTY")
    mode = layout.row(align=True)
    mode.prop(settings, "evaluation_mode", expand=True)
    if settings.evaluation_mode == "PER_SYSTEM":
        layout.label(text="AO per system, then generated cards are joined")
    else:
        layout.label(text="Generated cards are joined, then AO runs once", icon="ERROR")

    values = layout.box()
    values.use_property_split = True
    values.prop(settings, "samples")
    values.prop(settings, "spread_angle")
    values.prop(settings, "base_color_value")
    values.prop(settings, "blur_steps")
    values.prop(settings, "first_bounce_factor")
    values.prop(settings, "second_bounce_factor")
    if settings.evaluation_mode == "COMBINED":
        values.prop(settings, "combined_max_ray_distance")
        values.label(text="Shorter distance prevents cross-system over-occlusion")
    values.prop(settings, "use_custom_normals")

    state = deformer_sync.combined_ao_preview_state(context.object)
    if state["exists"]:
        stats = state["stats"]
        stale = bool(state["object"].get("_htue_combined_ao_preview_stale"))
        layout.label(
            text=(
                f"Preview: mean {stats.get('mean', 1.0):.3f}  |  "
                f"median {stats.get('median', 1.0):.3f}"
            ),
            icon="ERROR" if stale else "CHECKMARK",
        )
        if stale:
            layout.label(text="Settings changed — refresh preview", icon="FILE_REFRESH")
        row = layout.row(align=True)
        row.operator("htue.build_combined_ao_preview", text="Refresh", icon="FILE_REFRESH")
        row.operator("htue.remove_combined_ao_preview", text="Live Hair Tool", icon="LOOP_BACK")
    else:
        layout.operator(
            "htue.build_combined_ao_preview",
            text="Build AO Preview (Slower)",
            icon="MOD_NORMALEDIT",
        )


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
        status.label(text="Unreal two-sided backface normals matched")
        status.label(text="Legacy HairShaderMain blending is ignored")
        status.label(text="Base > System > Root > Tip > ID > Depth > AO")
        status.label(text="Unreal contract v3  |  31 synchronized parameters")
        layout.operator(
            "htue.refresh_contract",
            text="Refresh Hair Tool Connections",
            icon="FILE_REFRESH",
        )
        layout.label(text="Checks attributes and Unreal export data; does not export")


class HTUE_PT_Maintenance(_HTUEPanel, bpy.types.Panel):
    bl_label = "Maintenance / Advanced"
    bl_idname = "HTUE_PT_maintenance"
    bl_parent_id = "HTUE_PT_material_bridge"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Use only to stop using the bridge", icon="INFO")
        box.operator(
            "htue.restore_active_material",
            text="Remove Bridge / Restore Original",
            icon="LOOP_BACK",
        )
        box.label(text="Hair Tool deformers and Unreal assets are not deleted")


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
    bl_label = "04  Root  |  Set Factor + Random/IRD"
    bl_idname = "HTUE_PT_root"
    bl_parent_id = "HTUE_PT_material_bridge"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = _settings_layout(self.layout)
        settings = context.material.htue_settings
        if settings.root_range == 0.0:
            layout.label(text="Range 0 uses the full Root layer (Hair Tool behavior)", icon="INFO")
        layout.label(text="Random source: Hair Tool Random or IRD.R (ID Map Influence)")
        _properties(layout, settings, (
            "root_color", "root_mix", "root_range", "root_random_influence",
            "root_random_brightness", "root_map_influence", "root_blend_mode",
        ))


class HTUE_PT_Tip(_HTUEPanel, bpy.types.Panel):
    bl_label = "05  Tip  |  Set Factor + Random/IRD"
    bl_idname = "HTUE_PT_tip"
    bl_parent_id = "HTUE_PT_material_bridge"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = _settings_layout(self.layout)
        layout.label(text="Random source: Hair Tool Random or IRD.R (ID Map Influence)")
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
        workflow = layout.box()
        _draw_ao_workflow(workflow, context)
        _properties(layout, context.material.htue_settings, (
            "ao_strength", "ao_vertex_influence", "ao_texture_influence",
            "ao_color_influence", "ao_blend_mode", "roughness_minimum",
        ))


class HTUE_PT_Sidebar(bpy.types.Panel):
    bl_label = "Hair Tool Unreal Bridge"
    bl_idname = "HTUE_PT_sidebar"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HT Unreal"

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def draw(self, context):
        layout = self.layout
        material = _active_material(context)
        root = deformer_sync._find_export_root(context.object)
        if root is not None:
            layout.label(text=root.name, icon="OUTLINER_OB_EMPTY")
        if material is None:
            layout.label(text="No active hair material", icon="INFO")
            return
        layout.label(text=material.name, icon="MATERIAL")
        if not getattr(material.htue_settings, "initialized", False):
            layout.operator("htue.setup_active_material", icon="NODETREE")
            return
        layout.operator(
            "htue.refresh_contract",
            text="Refresh Connections",
            icon="FILE_REFRESH",
        )
        layout.label(text="Rechecks Hair Tool attributes; does not export")
        layout.label(text="Hair Tool Deformer links remain active", icon="LINKED")


class HTUE_PT_SidebarMaintenance(bpy.types.Panel):
    bl_label = "Maintenance / Advanced"
    bl_idname = "HTUE_PT_sidebar_maintenance"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HT Unreal"
    bl_parent_id = "HTUE_PT_sidebar"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        material = _active_material(context)
        return bool(
            material
            and getattr(material.htue_settings, "initialized", False)
        )

    def draw(self, context):
        layout = self.layout
        layout.label(text="Use only to stop using the bridge", icon="INFO")
        layout.operator(
            "htue.restore_active_material",
            text="Remove Bridge / Restore Original",
            icon="LOOP_BACK",
        )
        layout.label(text="Deformers and Unreal assets stay intact")


class HTUE_PT_SidebarExport(bpy.types.Panel):
    bl_label = "Export Collection Link"
    bl_idname = "HTUE_PT_sidebar_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HT Unreal"
    bl_parent_id = "HTUE_PT_sidebar"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        all_selected = deformer_sync.selected_hair_tool_outputs(
            context,
            render_only=False,
        )
        selected = deformer_sync.selected_hair_tool_outputs(context)
        if not all_selected:
            layout.label(text="Select Hair Tool output objects", icon="INFO")
            return

        layout.label(
            text=f"Exportable: {len(selected)} / Selected Hair Tool: {len(all_selected)}"
        )
        if len(selected) != len(all_selected):
            layout.label(text="Hidden or render-disabled selections are skipped", icon="ERROR")

        active = context.view_layer.objects.active
        if active in all_selected:
            layout.label(text=f"Active: {active.name}", icon="OBJECT_DATA")
            collection = deformer_sync.export_collection()
            linked = bool(collection and collection in active.users_collection)
            target = deformer_sync.export_target(active)
            has_assignment = deformer_sync.EXPORT_TARGET_PROPERTY in active
            if has_assignment and target is None:
                layout.label(text="Saved Export Empty is unavailable; relink", icon="ERROR")
            elif linked and target is not None:
                layout.label(text=f"Export Empty: {target.name}", icon="OUTLINER_OB_EMPTY")
            elif linked:
                layout.label(text="Linked to Export collection; no Empty target", icon="LINKED")
            else:
                layout.label(text="Not linked to Export collection", icon="UNLINKED")
            if not deformer_sync.has_ao_modifier(active):
                layout.label(
                    text="Per-system AO unavailable (export still allowed)",
                    icon="INFO",
                )

        targets = deformer_sync.export_empties()
        if not targets:
            layout.label(text="No Empty exists in the Export collection", icon="ERROR")
            assign_text = "No Export Empty Available"
        elif len(targets) == 1:
            assign_text = f"Link to {targets[0].name}"
        else:
            assign_text = "Link Selected to Export Collection..."

        if len(selected) > 1:
            selected_targets = {
                target.name if target is not None else "Not linked"
                for target in (
                    deformer_sync.export_target(obj) for obj in selected
                )
            }
            status = next(iter(selected_targets)) if len(selected_targets) == 1 else "Mixed"
            layout.label(text=f"Selected Empty targets: {status}")

        assign_column = layout.column(align=True)
        assign_column.enabled = bool(selected and targets)
        assign_column.operator(
            "htue.assign_selected_to_export",
            text=assign_text,
            icon="LINKED",
        )
        has_added_link = any(
            bool(obj.get(deformer_sync.EXPORT_LINK_ADDED_PROPERTY))
            for obj in all_selected
        )
        remove_text = (
            "Unlink Selected from Export Collection"
            if has_added_link
            else "Clear Selected Export Empty Target"
        )
        layout.operator(
            "htue.remove_selected_from_export",
            text=remove_text,
            icon="UNLINKED",
        )
        layout.label(text="Collection link only - Send to Unreal is not run")


class HTUE_PT_SidebarAO(bpy.types.Panel):
    bl_label = "AO Evaluation & Preview"
    bl_idname = "HTUE_PT_sidebar_ao"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HT Unreal"
    bl_parent_id = "HTUE_PT_sidebar"

    def draw(self, context):
        layout = self.layout
        _draw_ao_workflow(layout, context)
        material = _active_material(context)
        if material is not None and getattr(material.htue_settings, "initialized", False):
            box = layout.box()
            box.use_property_split = True
            _properties(box, material.htue_settings, (
                "ao_strength", "ao_vertex_influence", "ao_texture_influence",
                "ao_color_influence", "ao_blend_mode",
            ))


CLASSES = (
    HTUE_PT_MaterialBridge,
    HTUE_PT_Maintenance,
    HTUE_PT_Source,
    HTUE_PT_Base,
    HTUE_PT_System,
    HTUE_PT_Root,
    HTUE_PT_Tip,
    HTUE_PT_ID,
    HTUE_PT_Depth,
    HTUE_PT_AO,
    HTUE_PT_Sidebar,
    HTUE_PT_SidebarExport,
    HTUE_PT_SidebarMaintenance,
    HTUE_PT_SidebarAO,
)
