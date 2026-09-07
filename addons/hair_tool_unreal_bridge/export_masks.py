"""Hair Tool export-only mask deformers and preview integration.

The authored attributes stay separate in Blender. Send to Unreal performs the
final vertex-color packing on its disposable evaluated export mesh.
"""

import bpy
from bpy.props import EnumProperty
import sys


GROUP_VERSION = 2
GROUP_MARKER = "_htue_export_mask_group"
GROUP_VERSION_PROPERTY = "_htue_export_mask_version"

MASK_DEFINITIONS = {
    "WEIGHT": {
        "label": "Weight",
        "deformer_label": "Export Weight (Vertex G)",
        "group_name": "HTUE_Export_Weight",
        "attribute_name": "ChaosWeight",
        "attribute_type": "FLOAT_COLOR",
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
_patched_add_targets = {}
_patched_menu_type = None
_original_add_hair_deformer = None


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
        int(node_group.get(GROUP_VERSION_PROPERTY, 0)) in {1, GROUP_VERSION}
        and node_group.nodes.get(f"Store {definition['attribute_name']}") is not None
    ):
        # Upgrade storage in place: rebuilding an in-use group interface drops
        # Hair Tool's Geometry/Influence Range links and authored mask inputs.
        store = node_group.nodes[f"Store {definition['attribute_name']}"]
        attribute_type = definition.get("attribute_type", "BYTE_COLOR")
        if store.data_type != attribute_type:
            store.data_type = attribute_type
        node_group[GROUP_VERSION_PROPERTY] = GROUP_VERSION
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
    # Catmull-Rom interpolation can undershoot below zero at the start of a
    # weight ramp. BYTE_COLOR wraps that negative lobe to near-white during
    # Curve to Mesh. Keep signed float data until the exporter's mask validation
    # and final RFAOS byte packing; both readers already support FLOAT_COLOR.
    store.data_type = definition.get("attribute_type", "BYTE_COLOR")
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


def _node_key(node):
    try:
        return node.as_pointer()
    except (AttributeError, ReferenceError):
        return id(node)


def _remove_unreachable_export_nodes(node_tree, shared, group_name):
    """Remove Bridge-owned debris left by an interrupted Hair Tool insertion."""
    connected = {
        _node_key(node)
        for node in shared.get_deformer_nodes_list(node_tree, with_setup_node=True)
        if node is not None
    }
    removed = []
    for node in list(node_tree.nodes):
        node_group = getattr(node, "node_tree", None)
        if (
            node.type != "GROUP"
            or node_group is None
            or node_group.name != group_name
            or not node_group.get(GROUP_MARKER)
            or _node_key(node) in connected
        ):
            continue
        removed.append(node.name)
        node_tree.nodes.remove(node)
    return removed


def _prepare_hair_tool_insertion(obj, shared, group_name):
    """Synchronize Hair Tool's RNA list with its real connected node chain."""
    hair_nodes = obj.ht_props.hair_nodes
    system_index = hair_nodes.system_index
    modifier = shared.get_hair_mod_by_idx(obj, system_index)
    if modifier is None or modifier.node_group is None:
        raise RuntimeError("The active Hair Tool subsystem has no Geometry Nodes tree")

    node_tree = modifier.node_group
    _remove_unreachable_export_nodes(node_tree, shared, group_name)
    owners = [obj]
    for candidate in bpy.data.objects:
        if (
            candidate is obj
            or candidate.type not in {"MESH", "CURVES"}
            or not hasattr(candidate, "ht_props")
        ):
            continue
        if any(
            hair_modifier.node_group == node_tree
            for hair_modifier in shared.get_hair_sys_mod_list(candidate)
        ):
            owners.append(candidate)
    for owner in owners:
        shared.fix_hair_systems_ht_props(owner)

    if system_index >= len(hair_nodes.hair_systems):
        raise RuntimeError("Hair Tool could not rebuild the active subsystem metadata")
    connected = [
        node
        for node in shared.get_deformer_nodes_list(
            node_tree, with_setup_node=True
        )
        if node is not None
    ]
    if not connected:
        raise RuntimeError("Hair Tool could not find the active Deformer chain")

    system = hair_nodes.hair_systems[system_index]
    system.deformer_index = min(max(0, system.deformer_index), len(connected) - 1)
    return node_tree


def _snapshot_insertion_state(node_tree, shared):
    nodes = list(node_tree.nodes)
    links = [(link.from_socket, link.to_socket) for link in node_tree.links]
    locations = [(_node_key(node), node.location.copy()) for node in nodes]
    systems = []
    for system in shared.get_linked_hair_systems(node_tree):
        systems.append((system, len(system.deformers), system.deformer_index))
    return {
        "node_keys": {_node_key(node) for node in nodes},
        "links": links,
        "locations": locations,
        "systems": systems,
    }


def _restore_insertion_state(node_tree, snapshot):
    for node in list(node_tree.nodes):
        if _node_key(node) not in snapshot["node_keys"]:
            node_tree.nodes.remove(node)

    for from_socket, to_socket in snapshot["links"]:
        if not any(
            link.from_socket == from_socket and link.to_socket == to_socket
            for link in node_tree.links
        ):
            node_tree.links.new(from_socket, to_socket)

    locations = dict(snapshot["locations"])
    for node in node_tree.nodes:
        location = locations.get(_node_key(node))
        if location is not None:
            node.location = location

    for system, count, active_index in snapshot["systems"]:
        while len(system.deformers) > count:
            system.deformers.remove(len(system.deformers) - 1)
        while len(system.deformers) < count:
            system.deformers.add()
        system.deformer_index = min(max(0, active_index), max(0, count - 1))


def _safe_add_hair_deformer(obj, new_node_type):
    """Repair stale Hair Tool metadata before adding a Bridge Deformer."""
    export_group_names = {
        definition["group_name"] for definition in MASK_DEFINITIONS.values()
    }
    if new_node_type not in export_group_names:
        return _original_add_hair_deformer(obj, new_node_type)

    shared, _material_operators = _hair_tool_modules()
    if shared is None or _original_add_hair_deformer is None:
        raise RuntimeError("Hair Tool Deformer integration is unavailable")

    node_tree = _prepare_hair_tool_insertion(obj, shared, new_node_type)
    snapshot = _snapshot_insertion_state(node_tree, shared)
    try:
        return _original_add_hair_deformer(obj, new_node_type)
    except Exception as exc:
        _restore_insertion_state(node_tree, snapshot)
        raise RuntimeError(
            f'Could not insert Hair Tool Deformer "{new_node_type}"; '
            "the previous node chain was restored"
        ) from exc


def _install_safe_add_hair_deformer(shared):
    global _original_add_hair_deformer

    current = getattr(shared, "add_hair_deformer", None)
    if not callable(current):
        return
    if current is not _safe_add_hair_deformer:
        _original_add_hair_deformer = current

    modules = [shared]
    try:
        from hair_tool.hair_baking import hair_geometry_nodes
    except Exception:
        hair_geometry_nodes = None
    if hair_geometry_nodes is not None:
        modules.append(hair_geometry_nodes)

    for module in modules:
        previous = getattr(module, "add_hair_deformer", None)
        if previous is _safe_add_hair_deformer:
            continue
        if callable(previous):
            _patched_add_targets[module] = previous
            module.add_hair_deformer = _safe_add_hair_deformer


def _remove_safe_add_hair_deformer():
    global _original_add_hair_deformer

    for module, original in tuple(_patched_add_targets.items()):
        if getattr(module, "add_hair_deformer", None) is _safe_add_hair_deformer:
            module.add_hair_deformer = original
    _patched_add_targets.clear()
    _original_add_hair_deformer = None


def _draw_hair_tool_export_menu(self, _context):
    self.layout.separator()
    self.layout.menu(HTUE_MT_HairToolExportMasks.bl_idname, icon="EXPORT")


def _install_hair_tool_menu():
    global _patched_menu_type

    if _patched_menu_type is not None:
        return
    menu_type = getattr(bpy.types, "HT_MT_HairDeformerColorMenu", None)
    if menu_type is None:
        return
    menu_type.append(_draw_hair_tool_export_menu)
    _patched_menu_type = menu_type


def _remove_hair_tool_menu():
    global _patched_menu_type

    if _patched_menu_type is None:
        return
    try:
        _patched_menu_type.remove(_draw_hair_tool_export_menu)
    except (AttributeError, RuntimeError):
        pass
    _patched_menu_type = None


def install_runtime_integration():
    """Extend Hair Tool registries without editing Hair Tool's installed files."""
    shared, material_operators = _hair_tool_modules()
    if shared is None:
        return False

    for definition in MASK_DEFINITIONS.values():
        _build_mask_group(definition)
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
    _install_safe_add_hair_deformer(shared)
    _install_hair_tool_menu()
    return True


def remove_runtime_integration():
    _remove_hair_tool_menu()
    _remove_safe_add_hair_deformer()
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


def _connected_export_group_names(modifier):
    shared, _material_operators = _hair_tool_modules()
    if shared is None or modifier is None or modifier.node_group is None:
        return set()
    return {
        node.node_tree.name
        for node in shared.get_deformer_nodes_list(
            modifier.node_group, with_setup_node=False
        )
        if node is not None and node.node_tree is not None
    }


def active_mask_state(context):
    _obj, _hair_nodes, modifier = _active_hair_tool_state(context)
    existing = _connected_export_group_names(modifier)
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
        if node_group.name in _connected_export_group_names(modifier):
            self.report(
                {"INFO"},
                f"{definition['label']} already exists on the active Hair Tool system",
            )
            return {"FINISHED"}

        shared, _material_operators = _hair_tool_modules()
        try:
            node = shared.add_hair_deformer(obj, node_group.name)
        except (AttributeError, IndexError, RuntimeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
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


class HTUE_MT_HairToolExportMasks(bpy.types.Menu):
    bl_idname = "HTUE_MT_hair_tool_export_masks"
    bl_label = "Unreal Export Masks"

    def draw(self, _context):
        layout = self.layout
        for mask_type, definition in MASK_DEFINITIONS.items():
            operator = layout.operator(
                HTUE_OT_AddExportMaskDeformer.bl_idname,
                text=definition["deformer_label"],
                icon="COLOR",
            )
            operator.mask_type = mask_type


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

MENU_CLASSES = (HTUE_MT_HairToolExportMasks,)

PANEL_CLASSES = (HTUE_PT_SidebarExportMasks,)
