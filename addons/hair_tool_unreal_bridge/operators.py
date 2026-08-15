import bpy

from . import contract, nodes, schema


class HTUE_OT_SetupActiveMaterial(bpy.types.Operator):
    bl_idname = "htue.setup_active_material"
    bl_label = "Set Up Active Hair Material"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        material = context.material
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
    bl_label = "Restore Original Hair Tool Nodes"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        material = context.material
        if material is None:
            return {"CANCELLED"}
        nodes.restore_material(material)
        self.report({"INFO"}, f"Restored original Hair Tool inputs: {material.name}")
        return {"FINISHED"}


class HTUE_OT_RefreshContract(bpy.types.Operator):
    bl_idname = "htue.refresh_contract"
    bl_label = "Refresh Unreal Contract"

    def execute(self, context):
        material = context.material
        if material is None or not material.htue_settings.initialized:
            self.report({"ERROR"}, "Set up the active material first")
            return {"CANCELLED"}
        nodes.setup_material(material)
        _data, deformer_result = contract.refresh_material_contract(material)
        errors = contract.validate_material(material)
        if errors:
            self.report({"ERROR"}, "; ".join(errors[:3]))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Unreal contract is valid: {material.name}; "
            f"SystemColor sources={deformer_result.get('source_objects', 0)}",
        )
        return {"FINISHED"}


class HTUE_OT_PullDeformerColors(bpy.types.Operator):
    bl_idname = "htue.pull_deformer_colors"
    bl_label = "Read Set System Color"
    bl_description = "Read Hair Tool SystemColor RGB values by Alpha class"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        material = context.material
        if material is None or not material.htue_settings.initialized:
            return {"CANCELLED"}
        _data, result = contract.refresh_material_contract(material)
        if not result.get("updated"):
            self.report({"WARNING"}, "No SystemColor attribute was found for this material")
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            "Read SystemColor from " + str(result.get("source_objects", 0)) + " source objects",
        )
        return {"FINISHED"}


CLASSES = (
    HTUE_OT_SetupActiveMaterial,
    HTUE_OT_SetupFourMaterials,
    HTUE_OT_RestoreActiveMaterial,
    HTUE_OT_RefreshContract,
    HTUE_OT_PullDeformerColors,
)
