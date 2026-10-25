"""Input-mask evaluation and non-destructive v1/v2 export-group migration.

Run with Blender --background --factory-startup --python <this file>.
Never saves a blend file or user preferences.
"""

import addon_utils
import bpy
import json

assert bpy.app.background
assert addon_utils.enable(
    "hair_tool_unreal_bridge", default_set=False, persistent=False
) is not None
from hair_tool_unreal_bridge import export_masks


def links(tree):
    return sorted(
        (l.from_node.name, l.from_socket.identifier,
         l.to_node.name, l.to_socket.identifier)
        for l in tree.links
    )


receipts = []
for mask_type, definition in export_masks.MASK_DEFINITIONS.items():
    group = export_masks.ensure_mask_group(mask_type)
    tree = bpy.data.node_groups.new("InputMaskTest_" + mask_type, "GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    src = tree.nodes.new("NodeGroupInput")
    dst = tree.nodes.new("NodeGroupOutput")
    node = tree.nodes.new("GeometryNodeGroup")
    node.node_tree = group
    constant = tree.nodes.new("GeometryNodeGroup")
    constant.node_tree = group
    constant.inputs["Influence Range"].default_value = 0.625
    value = tree.nodes.new("ShaderNodeValue")
    value.outputs[0].default_value = 0.8
    attribute = tree.nodes.new("GeometryNodeInputNamedAttribute")
    attribute.data_type = "FLOAT"
    attribute.inputs["Name"].default_value = "TestMask"
    tree.links.new(src.outputs[0], node.inputs["Geometry"])
    tree.links.new(value.outputs[0], node.inputs["Influence Range"])
    tree.links.new(node.outputs["Geometry"], dst.inputs[0])
    direct_obj = bpy.data.objects.new("DirectMaskModifier_" + mask_type, bpy.data.meshes.new("DirectMaskMesh"))
    bpy.context.scene.collection.objects.link(direct_obj)
    direct = direct_obj.modifiers.new("Direct Mask", "NODES")
    direct.node_group = group

    # Recreate each shipped legacy interface while owner nodes already use it.
    for version in (1, 2):
        old_mask = next(i for i in group.interface.items_tree
                        if i.item_type == "SOCKET" and i.name == "Mask")
        group.interface.remove(old_mask)
        group.nodes.remove(group.nodes["HTUE Apply Input Mask"])
        for channel in range(3):
            group.links.new(group.nodes["HTUE Export Mask Input"].outputs["Influence Range"],
                            group.nodes["HTUE Grayscale Export Mask"].inputs[channel])
        group[export_masks.GROUP_VERSION_PROPERTY] = version
        group.nodes["Store " + definition["attribute_name"]].data_type = "BYTE_COLOR"
        before_interface = [(i.identifier, i.name, i.in_out) for i in group.interface.items_tree]
        before_links = links(tree)
        pointer = group.as_pointer()
        export_masks.ensure_mask_group(mask_type)
        assert group.as_pointer() == pointer
        assert before_interface == [(i.identifier, i.name, i.in_out)
                                    for i in group.interface.items_tree if i.name != "Mask"]
        assert links(tree) == before_links
        assert constant.inputs["Influence Range"].default_value == 0.625
        assert node.inputs["Mask"].default_value == 1.0
        assert constant.inputs["Mask"].default_value == 1.0
        mask_socket = next(i for i in group.interface.items_tree
                           if i.item_type == "SOCKET" and i.name == "Mask")
        assert direct.properties.inputs[mask_socket.identifier]["value"] == 1.0
        assert group.nodes["Store " + definition["attribute_name"]].data_type == definition.get("attribute_type", "BYTE_COLOR")

    # A user-authored input mask survives repeated runtime installation/migration.
    tree.links.new(attribute.outputs["Attribute"], node.inputs["Mask"])
    constant.inputs["Mask"].default_value = 0.35
    direct.properties.inputs[mask_socket.identifier]["value"] = 0.0
    stable_interface = [(i.identifier, i.name) for i in group.interface.items_tree]
    stable_links = links(tree)
    for _ in range(3):
        export_masks.ensure_mask_group(mask_type)
    assert stable_interface == [(i.identifier, i.name) for i in group.interface.items_tree]
    assert stable_links == links(tree)
    assert abs(constant.inputs["Mask"].default_value - 0.35) < 1e-6
    assert direct.properties.inputs[mask_socket.identifier]["value"] == 0.0

    mesh = bpy.data.meshes.new("InputMaskMesh_" + mask_type)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    masks = mesh.attributes.new("TestMask", "FLOAT", "POINT")
    for item, v in zip(masks.data, (0.0, 0.5, 1.0)):
        item.value = v
    factor = mesh.attributes.new("Factor", "FLOAT", "POINT")
    for item in factor.data:
        item.value = 0.4
    color = mesh.attributes.new("SystemColor", "FLOAT_COLOR", "POINT")
    for item in color.data:
        item.color = (0.1, 0.2, 0.3, 1.0)
    obj = bpy.data.objects.new("InputMaskObject_" + mask_type, mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new("Input Mask", "NODES")
    modifier.node_group = tree
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    result = evaluated.to_mesh()
    try:
        values = [i.color[0] for i in result.color_attributes[definition["attribute_name"]].data]
        tolerance = 0.005 if definition.get("attribute_type", "BYTE_COLOR") == "BYTE_COLOR" else 1e-6
        assert all(abs(a - b) < tolerance for a, b in zip(values, (0, 0.4, 0.8))), values
        assert len(result.vertices) == 3 and len(result.polygons) == 1
        assert all(abs(i.value - 0.4) < 1e-6 for i in result.attributes["Factor"].data)
        assert all(abs(i.color[0] - 0.1) < 1e-6 for i in result.color_attributes["SystemColor"].data)
        receipts.append({"type": mask_type, "values": values, "legacy_versions": [1, 2]})
    finally:
        evaluated.to_mesh_clear()

print("HTUE_EXPORT_INPUT_MASKS_OK " + json.dumps(receipts))
