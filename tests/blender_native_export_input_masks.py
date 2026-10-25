"""Run in an admitted live Hair Tool session; uses temporary data only.

The Bridge must already be reloaded and its export groups upgraded. This tests
the actual native UI drawing and operators without editing authored deformers.
"""

import bpy
from hair_tool.hair_baking import hair_geometry_nodes_shared as shared
from hair_tool.hair_baking import hair_geometry_nodes_ui as hair_ui
from hair_tool_unreal_bridge import export_masks

initial_groups = {g.as_pointer() for g in bpy.data.node_groups}
initial_meshes = {m.as_pointer() for m in bpy.data.meshes}
initial_objects = {o.as_pointer() for o in bpy.data.objects}
native_receipts = []


class LayoutProbe:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.enabled = True

    def row(self, **_kw):
        return self

    def prop(self, *_args, **_kw):
        pass

    def context_pointer_set(self, key, value):
        self.events.append((key, value.name))

    def menu(self, menu, **_kw):
        self.events.append((menu, self.enabled))


def evaluate(obj, name):
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return [i.color[0] for i in mesh.color_attributes[name].data]
    finally:
        evaluated.to_mesh_clear()


try:
    for kind, definition in export_masks.MASK_DEFINITIONS.items():
        tree = bpy.data.node_groups.new("HTUE_NATIVE_INPUT_MASK_TEST", "GeometryNodeTree")
        tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        src = tree.nodes.new("NodeGroupInput")
        dst = tree.nodes.new("NodeGroupOutput")
        # Native source-based masks expect Hair Tool's source geometry node.
        source = tree.nodes.new("GeometryNodeSeparateComponents")
        source.name = shared.separate_parent_components_node_name
        tree.links.new(src.outputs[0], source.inputs[0])
        export = tree.nodes.new("GeometryNodeGroup")
        export.node_tree = bpy.data.node_groups[definition["group_name"]]
        export.inputs["Influence Range"].default_value = 0.8
        tree.links.new(src.outputs[0], export.inputs["Geometry"])
        tree.links.new(export.outputs[0], dst.inputs[0])
        probe = LayoutProbe()
        hair_ui.HTool_PT_Main().draw_masked_input(export.inputs["Mask"], probe)
        assert ("HTOOL_MT_AddInputMaskMenu", True) in probe.events, probe.events

        mesh = bpy.data.meshes.new("HTUE_NATIVE_INPUT_MASK_TEST")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        attribute = mesh.attributes.new("HTUE_TestMask", "FLOAT", "POINT")
        for item, value in zip(attribute.data, (0, 0.5, 1)):
            item.value = value
        obj = bpy.data.objects.new("HTUE_NATIVE_INPUT_MASK_TEST", mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.hide_render = True
        obj.hide_set(True)
        modifier = obj.modifiers.new("Input Mask Test", "NODES")
        modifier.node_group = tree
        operator_args = dict(node_tree=tree.name, node_name=export.name, input_name="Mask")
        assert bpy.ops.geohair.add_input_mask(
            **operator_args, mask_type=shared.curve_attr_node_gr_name
        ) == {"FINISHED"}
        attr_node = next(n for n in tree.nodes if n.type == "GROUP"
                         and n.node_tree.name == shared.curve_attr_node_gr_name)
        attr_node.inputs["Mask"].default_value = "HTUE_TestMask"
        values = evaluate(obj, definition["attribute_name"])
        tolerance = 0.005 if kind == "PIXEL_DEPTH_OFFSET" else 1e-6
        assert all(abs(a - b) < tolerance for a, b in zip(values, (0, 0.4, 0.8))), values
        # Add an actual second native mask, then remove it through the UI's op.
        assert bpy.ops.geohair.add_input_mask(
            **operator_args, mask_type=shared.invert_node_gr_name
        ) == {"FINISHED"}
        inverted = evaluate(obj, definition["attribute_name"])
        assert all(abs(a - b) < tolerance for a, b in zip(inverted, (0.8, 0.4, 0))), inverted
        invert_node = next(n for n in tree.nodes if n.type == "GROUP"
                           and n.node_tree.name == shared.invert_node_gr_name)
        assert bpy.ops.geohair.remove_input_mask(
            node_tree=tree.name, node_name=invert_node.name
        ) == {"FINISHED"}
        restored = evaluate(obj, definition["attribute_name"])
        assert all(abs(a - b) < tolerance for a, b in zip(restored, values))
        # Native source/texture/procedural masks can all be inserted on Mask.
        added = []
        for mask in (shared.vgroup_mask_node_gr_name, shared.vcolor_mask_node_gr_name,
                     shared.texture_mask_node_gr_name, shared.texture_noise_node_gr_name,
                     shared.randomize_mask_node_gr_name):
            assert bpy.ops.geohair.add_input_mask(**operator_args, mask_type=mask) == {"FINISHED"}
            added.append(mask)
        native_receipts.append(dict(type=kind, menu_enabled=True, values=values,
                                    inverted=inverted, other_masks=added))
finally:
    for obj in list(bpy.data.objects):
        if obj.as_pointer() not in initial_objects:
            bpy.data.objects.remove(obj, do_unlink=True)
    for tree in list(bpy.data.node_groups):
        if tree.as_pointer() not in initial_groups:
            bpy.data.node_groups.remove(tree, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.as_pointer() not in initial_meshes:
            bpy.data.meshes.remove(mesh, do_unlink=True)

result = {"native_input_masks": native_receipts, "temporary_data_removed": True}
