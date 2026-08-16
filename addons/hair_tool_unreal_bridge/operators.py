import bpy
from bpy.props import EnumProperty

from . import contract, deformer_sync, nodes, schema


def _active_material(context):
    material = getattr(context, "material", None)
    if material is None and context.object is not None:
        material = context.object.active_material
    return material


_EXPORT_EMPTY_ITEM_CACHE = []


def _export_empty_items(_self, _context):
    global _EXPORT_EMPTY_ITEM_CACHE
    _EXPORT_EMPTY_ITEM_CACHE = [
        (
            target.name,
            target.name,
            f"Group selected Hair Tool outputs in {target.name}",
            "OUTLINER_OB_EMPTY",
            index,
        )
        for index, target in enumerate(deformer_sync.export_empties())
    ]
    return _EXPORT_EMPTY_ITEM_CACHE


class HTUE_OT_AssignSelectedToExport(bpy.types.Operator):
    bl_idname = "htue.assign_selected_to_export"
    bl_label = "Link Selected Hair to Export Collection"
    bl_description = (
        "Link only the selected render-enabled Hair Tool outputs to the Export "
        "collection and choose their asset Empty; this does not run Send to Unreal"
    )
    bl_options = {"REGISTER", "UNDO"}

    target_empty: EnumProperty(name="Export Empty", items=_export_empty_items)

    @classmethod
    def poll(cls, context):
        if not deformer_sync.selected_hair_tool_outputs(context):
            cls.poll_message_set(
                "Select visible, render-enabled Hair Tool output objects"
            )
            return False
        if not deformer_sync.export_empties():
            cls.poll_message_set("Add an Empty directly to the Export collection")
            return False
        return True

    def invoke(self, context, _event):
        targets = deformer_sync.export_empties()
        selected = deformer_sync.selected_hair_tool_outputs(context)
        if not selected:
            self.report({"ERROR"}, "Select at least one render-enabled Hair Tool output")
            return {"CANCELLED"}
        if not targets:
            self.report({"ERROR"}, "No Empty exists directly in the Export collection")
            return {"CANCELLED"}
        current_targets = {
            target.name
            for target in (deformer_sync.export_target(obj) for obj in selected)
            if target in targets
        }
        self.target_empty = (
            next(iter(current_targets))
            if len(current_targets) == 1
            else targets[0].name
        )
        if len(targets) == 1:
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self, width=440)

    def draw(self, context):
        layout = self.layout
        selected = deformer_sync.selected_hair_tool_outputs(context)
        layout.label(text=f"Selected Hair Tool outputs: {len(selected)}")
        for obj in selected[:3]:
            layout.label(text=obj.name, icon="OBJECT_DATA")
        if len(selected) > 3:
            layout.label(text=f"+ {len(selected) - 3} more")
        layout.prop(self, "target_empty")
        layout.label(text="Adds an Export collection link only", icon="LINKED")
        layout.label(text="Parents, transforms, and existing collections stay unchanged")

    def execute(self, context):
        collection = deformer_sync.export_collection()
        target = bpy.data.objects.get(self.target_empty)
        valid_targets = deformer_sync.export_empties()
        if collection is None or target not in valid_targets:
            self.report({"ERROR"}, "The chosen Export Empty is no longer available")
            return {"CANCELLED"}
        selected = deformer_sync.selected_hair_tool_outputs(context)
        if not selected:
            self.report({"ERROR"}, "No render-enabled Hair Tool outputs are selected")
            return {"CANCELLED"}
        for obj in selected:
            if collection not in obj.users_collection:
                collection.objects.link(obj)
                obj[deformer_sync.EXPORT_LINK_ADDED_PROPERTY] = True
            obj[deformer_sync.EXPORT_TARGET_PROPERTY] = target
        self.report(
            {"INFO"},
            f"Linked {len(selected)} selected Hair Tool output(s) to the Export collection under {target.name}; Send to Unreal was not run",
        )
        return {"FINISHED"}


class HTUE_OT_RemoveSelectedFromExport(bpy.types.Operator):
    bl_idname = "htue.remove_selected_from_export"
    bl_label = "Clear Selected Export Collection Link"
    bl_description = (
        "Clear selected Hair Tool Empty targets and remove only Export collection "
        "links added by this panel"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        collection = deformer_sync.export_collection()
        return bool(
            collection
            and any(
                deformer_sync.is_hair_tool_output(obj)
                and (
                    deformer_sync.EXPORT_TARGET_PROPERTY in obj
                    or deformer_sync.EXPORT_LINK_ADDED_PROPERTY in obj
                )
                for obj in context.selected_objects
            )
        )

    def execute(self, context):
        collection = deformer_sync.export_collection()
        if collection is None:
            return {"CANCELLED"}
        changed = 0
        skipped = []
        preserved = []
        for obj in context.selected_objects:
            if not deformer_sync.is_hair_tool_output(obj):
                continue
            had_assignment = deformer_sync.EXPORT_TARGET_PROPERTY in obj
            link_added = bool(obj.get(deformer_sync.EXPORT_LINK_ADDED_PROPERTY))
            if (
                link_added
                and collection in obj.users_collection
                and len(obj.users_collection) == 1
            ):
                skipped.append(obj.name)
                continue
            if link_added and collection in obj.users_collection:
                collection.objects.unlink(obj)
            if deformer_sync.EXPORT_LINK_ADDED_PROPERTY in obj:
                del obj[deformer_sync.EXPORT_LINK_ADDED_PROPERTY]
            if had_assignment:
                del obj[deformer_sync.EXPORT_TARGET_PROPERTY]
            if had_assignment and not link_added and collection in obj.users_collection:
                preserved.append(obj.name)
            if had_assignment or link_added:
                changed += 1
        if skipped:
            self.report(
                {"WARNING"},
                "Skipped because Export is the only collection: "
                + ", ".join(skipped[:3]),
            )
        elif preserved:
            self.report(
                {"INFO"},
                "Cleared the Empty target and kept pre-existing Export collection links for: "
                + ", ".join(preserved[:3]),
            )
        else:
            self.report({"INFO"}, f"Cleared {changed} selected Export collection link(s)")
        return {"FINISHED"}


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
    HTUE_OT_AssignSelectedToExport,
    HTUE_OT_RemoveSelectedFromExport,
    HTUE_OT_SetupActiveMaterial,
    HTUE_OT_SetupFourMaterials,
    HTUE_OT_RestoreActiveMaterial,
    HTUE_OT_RefreshContract,
    HTUE_OT_BuildCombinedAOPreview,
    HTUE_OT_RemoveCombinedAOPreview,
)
