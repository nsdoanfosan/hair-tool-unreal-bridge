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


def _custom_property_state(obj, key):
    return (key in obj, obj.get(key))


def _restore_custom_property(obj, key, state):
    existed, value = state
    if existed:
        obj[key] = value
    elif key in obj:
        del obj[key]


class HTUE_OT_AssignSelectedToExport(bpy.types.Operator):
    bl_idname = "htue.assign_selected_to_export"
    bl_label = "Link Selected Hair to Export Collection"
    bl_description = (
        "Place each selected Hair Tool hierarchy under the chosen Empty and link "
        "only its selected render output to the Export collection; this does not "
        "run Send to Unreal"
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
        layout.label(text="Keeps each Hair Tool parent chain together", icon="LINKED")
        layout.label(text="Only selected outputs join Export; world transforms stay unchanged")

    def execute(self, context):
        collection = deformer_sync.export_collection()
        target = bpy.data.objects.get(self.target_empty)
        valid_targets = deformer_sync.export_empties()
        if collection is None or target not in valid_targets:
            self.report({"ERROR"}, "The chosen Export Empty is no longer available")
            return {"CANCELLED"}
        if not target.is_editable:
            self.report({"ERROR"}, f"{target.name} is not editable")
            return {"CANCELLED"}
        selected = deformer_sync.selected_hair_tool_outputs(context)
        if not selected:
            self.report({"ERROR"}, "No render-enabled Hair Tool outputs are selected")
            return {"CANCELLED"}

        roots = {deformer_sync.hair_system_hierarchy_root(obj) for obj in selected}
        if any(obj is None or not obj.is_editable for obj in (*selected, *roots)):
            self.report({"ERROR"}, "The selected Hair Tool hierarchy is not editable")
            return {"CANCELLED"}
        selected_set = set(selected)
        conflicts = []
        for other in collection.objects:
            if (
                other in selected_set
                or not deformer_sync.is_hair_tool_output(other)
                or other.hide_render
                or not other.visible_get(view_layer=context.view_layer)
                or deformer_sync.hair_system_hierarchy_root(other) not in roots
                or deformer_sync.export_target(other) == target
            ):
                continue
            conflicts.append(other.name)
        if conflicts:
            self.report(
                {"ERROR"},
                "Select all active outputs in the same Hair Tool hierarchy: "
                + ", ".join(conflicts[:3]),
            )
            return {"CANCELLED"}

        object_states = {
            obj: {
                "linked": collection in obj.users_collection,
                "target": _custom_property_state(
                    obj, deformer_sync.EXPORT_TARGET_PROPERTY
                ),
                "link_marker": _custom_property_state(
                    obj, deformer_sync.EXPORT_LINK_ADDED_PROPERTY
                ),
            }
            for obj in selected
        }
        root_states = {
            root: {
                "parent": root.parent,
                "matrix_world": root.matrix_world.copy(),
                "moved": _custom_property_state(
                    root, deformer_sync.EXPORT_HIERARCHY_MOVED_PROPERTY
                ),
                "original_parent": _custom_property_state(
                    root, deformer_sync.EXPORT_ORIGINAL_PARENT_PROPERTY
                ),
            }
            for root in roots
        }
        ao_modifier_states = [
            (
                obj,
                modifier,
                {
                    "show_viewport": modifier.show_viewport,
                    "show_render": modifier.show_render,
                    "values": {
                        identifier: _custom_property_state(modifier, identifier)
                        for identifier in (
                            *(
                                value[0]
                                for value in deformer_sync.AO_MODIFIER_FIELDS.values()
                            ),
                            "Input_7",
                            "Input_16",
                        )
                    },
                },
            )
            for obj in selected
            for modifier in deformer_sync.ao_modifiers(obj)
        ]
        target_ao_state = deformer_sync.ao_bake_settings_state(target)
        created_ao_modifiers = []
        try:
            for obj in selected:
                if collection not in obj.users_collection:
                    collection.objects.link(obj)
                    obj[deformer_sync.EXPORT_LINK_ADDED_PROPERTY] = True
                obj[deformer_sync.EXPORT_TARGET_PROPERTY] = target
            for obj in selected:
                deformer_sync.move_hair_system_under_empty(obj, target)
            deformer_sync.initialize_ao_bake_settings(target)
            for obj in selected:
                modifier = deformer_sync.ensure_per_system_ao_modifier(obj, target)
                if modifier is not None:
                    created_ao_modifiers.append((obj, modifier))
            if created_ao_modifiers and bpy.context.view_layer is not None:
                bpy.context.view_layer.update()
        except Exception as exc:
            for obj, modifier in created_ao_modifiers:
                if modifier.name in obj.modifiers:
                    obj.modifiers.remove(modifier)
            for _obj, modifier, state in ao_modifier_states:
                for identifier, value_state in state["values"].items():
                    _restore_custom_property(modifier, identifier, value_state)
                modifier.show_viewport = state["show_viewport"]
                modifier.show_render = state["show_render"]
            deformer_sync.restore_ao_bake_settings(target, target_ao_state)
            for root, state in root_states.items():
                root.parent = state["parent"]
                root.matrix_world = state["matrix_world"]
                _restore_custom_property(
                    root,
                    deformer_sync.EXPORT_HIERARCHY_MOVED_PROPERTY,
                    state["moved"],
                )
                _restore_custom_property(
                    root,
                    deformer_sync.EXPORT_ORIGINAL_PARENT_PROPERTY,
                    state["original_parent"],
                )
            for obj, state in object_states.items():
                _restore_custom_property(
                    obj, deformer_sync.EXPORT_TARGET_PROPERTY, state["target"]
                )
                _restore_custom_property(
                    obj,
                    deformer_sync.EXPORT_LINK_ADDED_PROPERTY,
                    state["link_marker"],
                )
                if not state["linked"] and collection in obj.users_collection:
                    collection.objects.unlink(obj)
            self.report({"ERROR"}, f"Export collection link was not changed: {exc}")
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Placed {len(roots)} Hair Tool hierarchy(s) under {target.name} and linked {len(selected)} selected output(s) to Export; Send to Unreal was not run",
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
        cleared = []
        roots = set()
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
            deformer_sync.remove_bridge_ao_modifiers(obj)
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
                cleared.append(obj)
                roots.add(deformer_sync.hair_system_hierarchy_root(obj))
        for root in roots:
            still_assigned = any(
                candidate not in cleared
                and deformer_sync.is_hair_tool_output(candidate)
                and deformer_sync.EXPORT_TARGET_PROPERTY in candidate
                and deformer_sync.hair_system_hierarchy_root(candidate) == root
                for candidate in bpy.data.objects
            )
            if not still_assigned:
                deformer_sync.restore_hair_system_hierarchy(root)
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
