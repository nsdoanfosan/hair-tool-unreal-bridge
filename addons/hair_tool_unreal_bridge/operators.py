import bpy

from . import contract, deformer_sync, nodes, schema


def _active_material(context):
    material = getattr(context, "material", None)
    if material is None and context.object is not None:
        material = context.object.active_material
    return material


class HTUE_OT_SetupActiveMaterial(bpy.types.Operator):
    bl_idname = "htue.setup_active_material"
    bl_label = "Set Up Active Hair Material"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        material = _active_material(context)
        if material is None:
            self.report({"ERROR"}, "No active material")
            return {"CANCELLED"}
        try:
            nodes.setup_material(material)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"HTUE bridge set up: {material.name}")
        return {"FINISHED"}


class HTUE_OT_SetupFourMaterials(bpy.types.Operator):
    bl_idname = "htue.setup_four_materials"
    bl_label = "Set Up hair_sibuki_08 Materials"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, _context):
        missing = []
        configured = []
        for name in schema.TARGET_TEXTURE_SETS:
            material = bpy.data.materials.get(name)
            if material is None:
                missing.append(name)
                continue
            try:
                nodes.setup_material(material)
                configured.append(name)
            except Exception as exc:
                self.report({"ERROR"}, f"{name}: {exc}")
                return {"CANCELLED"}
        if missing:
            self.report({"WARNING"}, "Missing: " + ", ".join(missing))
        self.report({"INFO"}, f"Configured {len(configured)} Hair Tool materials")
        return {"FINISHED"}


class HTUE_OT_RestoreActiveMaterial(bpy.types.Operator):
    bl_idname = "htue.restore_active_material"
    bl_label = "Remove Bridge and Restore Original Material"
    bl_description = (
        "Remove this material's Unreal Bridge shader additions and restore "
        "the original Hair Tool node group, socket values, and links"
    )
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        material = _active_material(context)
        if material is None:
            return {"CANCELLED"}
        nodes.restore_material(material)
        self.report({"INFO"}, f"Restored original Hair Tool inputs: {material.name}")
        return {"FINISHED"}


class HTUE_OT_RefreshContract(bpy.types.Operator):
    bl_idname = "htue.refresh_contract"
    bl_label = "Refresh Hair Tool Connections"
    bl_description = (
        "Recheck Hair Tool attributes and refresh the Blender shader hooks "
        "and Unreal export contract; this does not export to Unreal"
    )

    def execute(self, context):
        material = _active_material(context)
        if material is None or not material.htue_settings.initialized:
            self.report({"ERROR"}, "Set up the active material first")
            return {"CANCELLED"}
        nodes.setup_material(material)
        _data, transport = contract.refresh_material_contract(material)
        errors = contract.validate_material(material)
        if errors:
            self.report({"ERROR"}, "; ".join(errors[:3]))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Unreal contract is valid: {material.name}; {transport['transport']}",
        )
        return {"FINISHED"}


class HTUE_OT_BuildCombinedAOPreview(bpy.types.Operator):
    bl_idname = "htue.build_combined_ao_preview"
    bl_label = "Build / Refresh AO Preview"
    bl_description = "Build the joined display mesh with the selected Hair Tool AO mode"

    def execute(self, context):
        material = _active_material(context)
        if material is None or not material.htue_settings.initialized:
            self.report({"ERROR"}, "Set up the active material first")
            return {"CANCELLED"}
        try:
            result = deformer_sync.build_combined_ao_preview(material, context.object)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        for slot in result["preview"].material_slots:
            if slot.material and getattr(slot.material.htue_settings, "initialized", False):
                nodes.refresh_deformer_availability(slot.material)
        stats = result["stats"]
        mode_name = (
            "Combined"
            if stats.get("source") == "combined_export_geometry"
            else "Per System"
        )
        self.report(
            {"INFO"},
            f"{mode_name} AO preview: {len(result['sources'])} systems; "
            f"AO {stats.get('minimum', 1.0):.3f}-{stats.get('maximum', 1.0):.3f}",
        )
        return {"FINISHED"}


class HTUE_OT_RemoveCombinedAOPreview(bpy.types.Operator):
    bl_idname = "htue.remove_combined_ao_preview"
    bl_label = "Return to Live Hair Tool"
    bl_description = "Remove the cached joined preview and restore editable Hair Tool outputs"

    def execute(self, context):
        material = _active_material(context)
        if material is None or not material.htue_settings.initialized:
            return {"CANCELLED"}
        result = deformer_sync.remove_combined_ao_preview(context.object)
        for target in bpy.data.materials:
            if getattr(target.htue_settings, "initialized", False):
                nodes.refresh_deformer_availability(target)
        self.report(
            {"INFO"},
            f"Restored {len(result['restored'])} live Hair Tool systems",
        )
        return {"FINISHED"}


CLASSES = (
    HTUE_OT_SetupActiveMaterial,
    HTUE_OT_SetupFourMaterials,
    HTUE_OT_RestoreActiveMaterial,
    HTUE_OT_RefreshContract,
    HTUE_OT_BuildCombinedAOPreview,
    HTUE_OT_RemoveCombinedAOPreview,
)
