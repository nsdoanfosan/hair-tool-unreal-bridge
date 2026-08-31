import addon_utils
import bpy
import math
import sys
import types


addon_utils.enable("hair_tool_unreal_bridge", default_set=False, persistent=False)

from hair_tool_unreal_bridge import export_masks


weight_group = export_masks.ensure_mask_group("WEIGHT")
pdo_group = export_masks.ensure_mask_group("PIXEL_DEPTH_OFFSET")

# The vertex-color extension may add only its own registries and node groups.
# It must never mutate Hair Tool's core Factor/SystemColor definitions.
core_factor = bpy.data.node_groups.new("HD_FactorSet", "GeometryNodeTree")
core_factor.nodes.new("GeometryNodeStoreNamedAttribute").inputs[
    "Name"
].default_value = "Factor"
core_color = bpy.data.node_groups.new("HD_Color", "GeometryNodeTree")
core_color.nodes.new("GeometryNodeStoreNamedAttribute").inputs[
    "Name"
].default_value = "SystemColor"


def group_signature(group):
    return (
        tuple(sorted((node.name, node.bl_idname) for node in group.nodes)),
        tuple(
            sorted(
                (
                    link.from_node.name,
                    link.from_socket.name,
                    link.to_node.name,
                    link.to_socket.name,
                )
                for link in group.links
            )
        ),
    )


core_before = (group_signature(core_factor), group_signature(core_color))
fake_hair_tool = types.ModuleType("hair_tool")
fake_hair_tool.__path__ = []
fake_material_operators = types.ModuleType("hair_tool.material_operators")
fake_material_operators.DEFAULT_PREVIEW_ATTR_NAMES = ["Factor", "SystemColor"]
fake_hair_baking = types.ModuleType("hair_tool.hair_baking")
fake_hair_baking.__path__ = []
fake_shared = types.ModuleType("hair_tool.hair_baking.hair_geometry_nodes_shared")
fake_shared.deformer_labels = {"HD_FactorSet": "Set Factor", "HD_Color": "Set System Color"}
fake_shared.node_configs = {}
fake_shared.hair_deformer_groups = ("HD_FactorSet", "HD_Color")
fake_shared.not_mutable_nodes = ["HD_FactorSet", "HD_Color"]
fake_shared.remap_curve_root_to_tip_node_gr_name = "RootToTip"
fake_hair_tool.material_operators = fake_material_operators
fake_hair_baking.hair_geometry_nodes_shared = fake_shared
sys.modules["hair_tool"] = fake_hair_tool
sys.modules["hair_tool.material_operators"] = fake_material_operators
sys.modules["hair_tool.hair_baking"] = fake_hair_baking
sys.modules["hair_tool.hair_baking.hair_geometry_nodes_shared"] = fake_shared
assert export_masks.install_runtime_integration()
assert core_before == (group_signature(core_factor), group_signature(core_color))
assert "HTUE_Export_Weight" in fake_shared.deformer_labels
assert "HTUE_Export_PixelDepthOffset" in fake_shared.deformer_labels

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
