import addon_utils
import bpy
import math


addon_utils.enable("hair_tool_unreal_bridge", default_set=False, persistent=False)

from hair_tool_unreal_bridge import export_masks


weight_group = export_masks.ensure_mask_group("WEIGHT")
pdo_group = export_masks.ensure_mask_group("PIXEL_DEPTH_OFFSET")

mesh = bpy.data.meshes.new("HTUE_EXPORT_MASK_SOURCE")
mesh.from_pydata(
    [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
    [],
    [(0, 1, 2)],
)
mesh.update()

factor = mesh.attributes.new("Factor", "FLOAT", "POINT")
system_color = mesh.attributes.new("SystemColor", "FLOAT_COLOR", "POINT")
weight_input = mesh.attributes.new("TestWeightInput", "FLOAT", "POINT")
pdo_input = mesh.attributes.new("TestPdoInput", "FLOAT", "POINT")
for index, value in enumerate((0.0, 0.5, 1.0)):
    factor.data[index].value = value
    system_color.data[index].color = (value, 0.25, 0.75, 1.0)
    weight_input.data[index].value = value
    pdo_input.data[index].value = 1.0 - value

source = bpy.data.objects.new("HTUE_EXPORT_MASK_SOURCE", mesh)
bpy.context.scene.collection.objects.link(source)

wrapper = bpy.data.node_groups.new("HTUE_EXPORT_MASK_WRAPPER", "GeometryNodeTree")
wrapper.interface.new_socket(
    name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
)
wrapper.interface.new_socket(
    name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
)
group_input = wrapper.nodes.new("NodeGroupInput")
group_output = wrapper.nodes.new("NodeGroupOutput")
weight_attribute = wrapper.nodes.new("GeometryNodeInputNamedAttribute")
weight_attribute.data_type = "FLOAT"
weight_attribute.inputs["Name"].default_value = "TestWeightInput"
pdo_attribute = wrapper.nodes.new("GeometryNodeInputNamedAttribute")
pdo_attribute.data_type = "FLOAT"
pdo_attribute.inputs["Name"].default_value = "TestPdoInput"
weight = wrapper.nodes.new("GeometryNodeGroup")
weight.node_tree = weight_group
pdo = wrapper.nodes.new("GeometryNodeGroup")
pdo.node_tree = pdo_group

wrapper.links.new(group_input.outputs["Geometry"], weight.inputs["Geometry"])
wrapper.links.new(weight_attribute.outputs["Attribute"], weight.inputs["Influence Range"])
wrapper.links.new(weight.outputs["Geometry"], pdo.inputs["Geometry"])
wrapper.links.new(pdo_attribute.outputs["Attribute"], pdo.inputs["Influence Range"])
wrapper.links.new(pdo.outputs["Geometry"], group_output.inputs["Geometry"])

modifier = source.modifiers.new("HTUE Export Masks", "NODES")
modifier.node_group = wrapper
bpy.context.view_layer.objects.active = source
source.select_set(True)
bpy.context.view_layer.update()

evaluated = source.evaluated_get(bpy.context.evaluated_depsgraph_get())
evaluated_mesh = bpy.data.meshes.new_from_object(evaluated)


def red_values(attribute_name):
    attribute = evaluated_mesh.attributes[attribute_name]
    assert attribute.data_type == "BYTE_COLOR"
    assert attribute.domain == "POINT"
    return [item.color[0] for item in attribute.data]


for actual, expected in zip(red_values("ChaosWeight"), (0.0, 0.5, 1.0)):
    assert math.isclose(actual, expected, abs_tol=1.0 / 255.0 + 1.0e-6)
for actual, expected in zip(red_values("HairPixelDepthOffset"), (1.0, 0.5, 0.0)):
    assert math.isclose(actual, expected, abs_tol=1.0 / 255.0 + 1.0e-6)

assert "Factor" in evaluated_mesh.attributes
assert "SystemColor" in evaluated_mesh.attributes
assert "ChaosWeight" not in mesh.attributes
assert "HairPixelDepthOffset" not in mesh.attributes
assert [item.value for item in factor.data] == [0.0, 0.5, 1.0]

print("HTUE_EXPORT_MASKS_SMOKE_OK")
