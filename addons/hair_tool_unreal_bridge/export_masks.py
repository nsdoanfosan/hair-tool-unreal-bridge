"""Hair Tool export-only mask deformers and preview integration.

The authored attributes stay separate in Blender. Send to Unreal performs the
final vertex-color packing on its disposable evaluated export mesh.
"""

import bpy
from bpy.props import EnumProperty
import sys


GROUP_VERSION = 1
GROUP_MARKER = "_htue_export_mask_group"
GROUP_VERSION_PROPERTY = "_htue_export_mask_version"

MASK_DEFINITIONS = {
    "WEIGHT": {
        "label": "Weight",
        "deformer_label": "Export Weight (Vertex G)",
        "group_name": "HTUE_Export_Weight",
        "attribute_name": "ChaosWeight",
        "vertex_channel": "G",
        "default": 0.0,
    },
    "PIXEL_DEPTH_OFFSET": {
        "label": "Pixel Depth Offset",
        "deformer_label": "Export Pixel Depth Offset (Vertex R)",
        "group_name": "HTUE_Export_PixelDepthOffset",
        "attribute_name": "HairPixelDepthOffset",
        "vertex_channel": "R",
        "default": 1.0,
    },
}

MASK_TYPE_ITEMS = tuple(
    (
        key,
        definition["label"],
        (
            f"Create {definition['attribute_name']} for Unreal vertex color "
            f"{definition['vertex_channel']}"
        ),
        index,
    )
    for index, (key, definition) in enumerate(MASK_DEFINITIONS.items())
)

_patched_deformer_names = set()
_patched_preview_names = set()


def _hair_tool_modules():
    if bpy.app.background and "hair_tool" not in sys.modules:
        return None, None
    try:
        from hair_tool import material_operators
        from hair_tool.hair_baking import hair_geometry_nodes_shared
    except Exception as exc:
        print(f"HTUE Hair Tool runtime integration unavailable: {exc}")
        return None, None
    return hair_geometry_nodes_shared, material_operators


def _clear_node_group(node_group):
    node_group.nodes.clear()
    node_group.interface.clear()


def _build_mask_group(definition):
    """Build one geometry pass-through that stores a grayscale color field."""
    name = definition["group_name"]
    node_group = bpy.data.node_groups.get(name)
    if node_group is not None and not node_group.get(GROUP_MARKER):
        raise RuntimeError(
            f'Node group "{name}" already exists and is not owned by Unreal Bridge.'
        )
    if node_group is None:
        node_group = bpy.data.node_groups.new(name, "GeometryNodeTree")
    elif (
        int(node_group.get(GROUP_VERSION_PROPERTY, 0)) == GROUP_VERSION
        and node_group.nodes.get(f"Store {definition['attribute_name']}") is not None
    ):
        return node_group
    else:
        _clear_node_group(node_group)

    node_group[GROUP_MARKER] = definition["attribute_name"]
    node_group.description = (
        f"Export-only {definition['label']} attribute for Unreal vertex color "
        f"{definition['vertex_channel']}"
    )
    if hasattr(node_group, "is_modifier"):
        node_group.is_modifier = True

    geometry_input = node_group.interface.new_socket(
        name="Geometry",
        in_out="INPUT",
        socket_type="NodeSocketGeometry",
    )
    geometry_input.description = "Hair Tool geometry passed through unchanged"
    influence_input = node_group.interface.new_socket(
        name="Influence Range",
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    influence_input.default_value = definition["default"]
    influence_input.min_value = 0.0
    influence_input.max_value = 1.0
    influence_input.description = (
        "Hair Tool input mask; defaults to an editable Root-to-Tip curve"
    )
    geometry_output = node_group.interface.new_socket(
        name="Geometry",
        in_out="OUTPUT",
        socket_type="NodeSocketGeometry",
    )
    geometry_output.description = "Geometry carrying the export-only color attribute"

    group_input = node_group.nodes.new("NodeGroupInput")
    group_input.name = "HTUE Export Mask Input"
    group_input.location = (-520.0, 40.0)
    group_output = node_group.nodes.new("NodeGroupOutput")
    group_output.name = "HTUE Export Mask Output"
    group_output.location = (300.0, 40.0)

    combine = node_group.nodes.new("FunctionNodeCombineColor")
    combine.name = "HTUE Grayscale Export Mask"
    combine.mode = "RGB"
    combine.location = (-270.0, -120.0)
    combine.inputs[3].default_value = 1.0

    store = node_group.nodes.new("GeometryNodeStoreNamedAttribute")
    store.name = f"Store {definition['attribute_name']}"
    store.label = definition["attribute_name"]
    store.data_type = "BYTE_COLOR"
    store.domain = "POINT"
    store.location = (20.0, 40.0)
    store.inputs["Selection"].default_value = True
    store.inputs["Name"].default_value = definition["attribute_name"]

    links = node_group.links
    links.new(group_input.outputs["Geometry"], store.inputs["Geometry"])
    for channel_index in range(3):
        links.new(group_input.outputs["Influence Range"], combine.inputs[channel_index])
    links.new(combine.outputs["Color"], store.inputs["Value"])
    links.new(store.outputs["Geometry"], group_output.inputs["Geometry"])
    node_group[GROUP_VERSION_PROPERTY] = GROUP_VERSION
    return node_group


def ensure_mask_group(mask_type):
    try:
        definition = MASK_DEFINITIONS[mask_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported export mask type: {mask_type}") from exc
    node_group = _build_mask_group(definition)
    install_runtime_integration()
    return node_group


def install_runtime_integration():
    """Extend Hair Tool registries without editing Hair Tool's installed files."""
    shared, material_operators = _hair_tool_modules()
    if shared is None:
        return False

    for definition in MASK_DEFINITIONS.values():
        attribute_name = definition["attribute_name"]
        if attribute_name not in material_operators.DEFAULT_PREVIEW_ATTR_NAMES:
            material_operators.DEFAULT_PREVIEW_ATTR_NAMES.append(attribute_name)
            _patched_preview_names.add(attribute_name)

        group_name = definition["group_name"]
        if bpy.data.node_groups.get(group_name) is None:
            continue
        shared.deformer_labels[group_name] = definition["deformer_label"]
        shared.node_configs[group_name] = {
            "default": definition["default"],
            "default2": 1.0 if definition["default"] == 0.0 else 0.0,
            "remap": shared.remap_curve_root_to_tip_node_gr_name,
        }
        if group_name not in shared.hair_deformer_groups:
            shared.hair_deformer_groups = (*shared.hair_deformer_groups, group_name)
        if group_name not in shared.not_mutable_nodes:
            shared.not_mutable_nodes.append(group_name)
        _patched_deformer_names.add(group_name)
    return True


def remove_runtime_integration():
    shared, material_operators = _hair_tool_modules()
    if shared is None:
        return

    for group_name in tuple(_patched_deformer_names):
        shared.deformer_labels.pop(group_name, None)
        shared.node_configs.pop(group_name, None)
        shared.hair_deformer_groups = tuple(
            name for name in shared.hair_deformer_groups if name != group_name
        )
        while group_name in shared.not_mutable_nodes:
            shared.not_mutable_nodes.remove(group_name)
    _patched_deformer_names.clear()

    for attribute_name in tuple(_patched_preview_names):
        while attribute_name in material_operators.DEFAULT_PREVIEW_ATTR_NAMES:
            material_operators.DEFAULT_PREVIEW_ATTR_NAMES.remove(attribute_name)
    _patched_preview_names.clear()


def _active_hair_tool_state(context):
    obj = context.object
    if obj is None:
        return None, None, None
    shared, _material_operators = _hair_tool_modules()
    if shared is None or not hasattr(obj, "ht_props"):
        return obj, None, None
    hair_nodes = getattr(obj.ht_props, "hair_nodes", None)
    systems = getattr(hair_nodes, "hair_systems", None)
    if hair_nodes is None or systems is None or not systems:
        return obj, hair_nodes, None
    modifier = shared.get_hair_mod_by_idx(obj, hair_nodes.system_index)
    return obj, hair_nodes, modifier


def active_mask_state(context):
    _obj, _hair_nodes, modifier = _active_hair_tool_state(context)
    existing = set()
    if modifier is not None and modifier.node_group is not None:
        existing = {
            node.node_tree.name
            for node in modifier.node_group.nodes
            if node.type == "GROUP" and node.node_tree is not None
        }
    return {
        mask_type: definition["group_name"] in existing
        for mask_type, definition in MASK_DEFINITIONS.items()
    }


class HTUE_OT_AddExportMaskDeformer(bpy.types.Operator):
    bl_idname = "htue.add_export_mask_deformer"
    bl_label = "Add Hair Export Mask Deformer"
    bl_description = (
        "Add an export-only Hair Tool deformer; the final vertex channel is packed "
        "only on Send to Unreal's temporary mesh"
    )
    bl_options = {"REGISTER", "UNDO"}

    mask_type: EnumProperty(name="Mask", items=MASK_TYPE_ITEMS, default="WEIGHT")

    @classmethod
    def poll(cls, context):
        _obj, hair_nodes, modifier = _active_hair_tool_state(context)
        return hair_nodes is not None and modifier is not None

    def execute(self, context):
        definition = MASK_DEFINITIONS[self.mask_type]
        obj, hair_nodes, modifier = _active_hair_tool_state(context)
        if obj is None or hair_nodes is None or modifier is None:
            self.report({"ERROR"}, "Select an editable Hair Tool output system")
            return {"CANCELLED"}

        node_group = ensure_mask_group(self.mask_type)
        existing = next(
            (
                node
                for node in modifier.node_group.nodes
                if node.type == "GROUP" and node.node_tree == node_group
            ),
            None,
        )
        if existing is not None:
            self.report(
                {"INFO"},
                f"{definition['label']} already exists on the active Hair Tool system",
            )
            return {"FINISHED"}

        shared, _material_operators = _hair_tool_modules()
        node = shared.add_hair_deformer(obj, node_group.name)
        node.label = (
            f"{definition['label']} -> Vertex {definition['vertex_channel']}"
        )
        node[GROUP_MARKER] = definition["attribute_name"]
        context.view_layer.update()
        self.report(
            {"INFO"},
            (
                f"Added {definition['attribute_name']} -> Unreal vertex color "
                f"{definition['vertex_channel']}"
            ),
        )
        return {"FINISHED"}


class HTUE_OT_PreviewExportMask(bpy.types.Operator):
    bl_idname = "htue.preview_export_mask"
    bl_label = "Preview Hair Export Mask"
    bl_description = "Preview the evaluated export mask using Hair Tool's attribute viewer"
    bl_options = {"REGISTER"}

    mask_type: EnumProperty(name="Mask", items=MASK_TYPE_ITEMS, default="WEIGHT")

    @classmethod
    def poll(cls, context):
        _obj, _hair_nodes, modifier = _active_hair_tool_state(context)
        return modifier is not None

    def execute(self, context):
        install_runtime_integration()
        attribute_name = MASK_DEFINITIONS[self.mask_type]["attribute_name"]
        try:
            return bpy.ops.htool.preview_attribute(
                "INVOKE_DEFAULT",
                attribute_name=attribute_name,
            )
        except RuntimeError as exc:
            self.report({"ERROR"}, f"Hair Tool preview could not start: {exc}")
            return {"CANCELLED"}


class HTUE_PT_SidebarExportMasks(bpy.types.Panel):
    bl_label = "Unreal Export Masks"
    bl_idname = "HTUE_PT_sidebar_export_masks"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Unreal Bridge"
    bl_parent_id = "HTUE_PT_sidebar"
    bl_order = -1

    def draw(self, context):
        layout = self.layout
        layout.label(text="Vertex Color: R PDO / G Cloth Weight", icon="COLOR")
        layout.label(text="Original SystemColor / Factor / Depth stay unchanged")

        _obj, hair_nodes, modifier = _active_hair_tool_state(context)
        if hair_nodes is None or modifier is None:
            layout.label(text="Select an editable Hair Tool output", icon="INFO")
            return

        state = active_mask_state(context)
        for mask_type, definition in MASK_DEFINITIONS.items():
            box = layout.box()
            header = box.row(align=True)
            header.label(
                text=(
                    f"{definition['label']}  ->  Vertex "
                    f"{definition['vertex_channel']}"
                ),
                icon="CHECKMARK" if state[mask_type] else "UNLINKED",
            )
            row = box.row(align=True)
            add = row.operator(
                "htue.add_export_mask_deformer",
                text="Exists" if state[mask_type] else "Add Deformer",
                icon="CHECKMARK" if state[mask_type] else "ADD",
            )
            add.mask_type = mask_type
            preview = row.operator(
                "htue.preview_export_mask",
                text="Preview",
                icon="LIGHT",
            )
            preview.mask_type = mask_type
            box.label(text=f"Attribute: {definition['attribute_name']}")


OPERATOR_CLASSES = (
    HTUE_OT_AddExportMaskDeformer,
    HTUE_OT_PreviewExportMask,
)

PANEL_CLASSES = (HTUE_PT_SidebarExportMasks,)
