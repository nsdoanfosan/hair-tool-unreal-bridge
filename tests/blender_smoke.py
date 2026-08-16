import addon_utils
import bpy


addon_utils.enable("hair_tool_unreal_bridge", default_set=False, persistent=False)

import hair_tool_unreal_bridge as addon
from hair_tool_unreal_bridge import contract, nodes, schema


assert addon.migrate_bridge_ui_on_load in bpy.app.handlers.load_post
assert hasattr(bpy.types.Object, "htue_ao_settings")
assert bpy.types.HTUE_PT_sidebar.bl_category == "HT Unreal"
assert bpy.types.HTUE_PT_sidebar_ao.bl_parent_id == "HTUE_PT_sidebar"
assert bpy.types.HTUE_PT_sidebar_maintenance.bl_parent_id == "HTUE_PT_sidebar"
assert bpy.types.HTUE_PT_sidebar_maintenance.bl_options == {"DEFAULT_CLOSED"}
assert bpy.types.HTUE_OT_refresh_contract.bl_label == "Refresh Hair Tool Connections"
assert (
    bpy.types.HTUE_OT_restore_active_material.bl_label
    == "Remove Bridge and Restore Original Material"
)


def input_socket(tree, name, socket_type, default):
    socket = tree.interface.new_socket(name=name, in_out="INPUT", socket_type=socket_type)
    socket.default_value = default


hair_group = bpy.data.node_groups.new("HairShaderMain_Smoke", "ShaderNodeTree")
for name, default in (
    ("Base Color", (0.02, 0.02, 0.02, 1.0)),
    ("Root Color", (0.3, 0.1, 0.03, 1.0)),
    ("Tip Color", (0.8, 0.5, 0.3, 1.0)),
    ("Debug Color", (0.0, 0.0, 0.0, 1.0)),
    ("Depth Tint", (0.0, 0.0, 0.0, 1.0)),
    ("Depth [Map]", (0.0, 0.0, 0.0, 1.0)),
    ("Vert Color AO [Map]", (1.0, 1.0, 1.0, 1.0)),
):
    input_socket(hair_group, name, "NodeSocketColor", default)
for name, default in (
    ("Root Color Mix Factor", 1.0),
    ("Root Color Range", 0.2),
    ("Root Texture Overaly", 0.0),
    ("Root  Texture Brightness", 0.5),
    ("Tip Color Mix Factor", 1.0),
    ("Tip Color Range", 0.3),
    ("Tip Texture Overlay", 0.0),
    ("Tip  Texture Brightness", 0.0),
    ("Debug  Color  Mix", 1.0),
    ("Depth Mix Factor", 0.0),
    ("AO Mix Factor", 0.0),
    ("SpecRoughness", 0.08),
    ("Factor [Map]", 0.0),
    ("Random Id [Map]", 0.0),
):
    input_socket(hair_group, name, "NodeSocketFloat", default)

group_input = hair_group.nodes.new("NodeGroupInput")
final_color = hair_group.nodes.new("NodeReroute")
final_color.name = "Hair Tool Final Color"
final_color.label = "Color"
hair_group.links.new(group_input.outputs["Base Color"], final_color.inputs[0])

material = bpy.data.materials.new("M_HT_Default_Material_01")
material.use_nodes = True
shader = material.node_tree.nodes.new("ShaderNodeGroup")
shader.name = "HairShaderMain"
shader.node_tree = hair_group

normal_group = bpy.data.node_groups.new("HTool_Normal", "ShaderNodeTree")
normal_input = normal_group.interface.new_socket(
    name="Flip Backface Normal", in_out="INPUT", socket_type="NodeSocketFloat"
)
normal_input.default_value = 0.5
normal_group.interface.new_socket(
    name="Result", in_out="OUTPUT", socket_type="NodeSocketVector"
)
normal_node = material.node_tree.nodes.new("ShaderNodeGroup")
normal_node.name = "Hair Tool Normal"
normal_node.node_tree = normal_group

factor = material.node_tree.nodes.new("ShaderNodeAttribute")
factor.name = "Hair Tool Factor"
factor.attribute_name = "Factor"
random = material.node_tree.nodes.new("ShaderNodeAttribute")
random.name = "Hair Tool Random"
random.attribute_name = "Random"
system = material.node_tree.nodes.new("ShaderNodeAttribute")
system.name = "Hair Tool SystemColor"
system.attribute_name = "SystemColor"
ao = material.node_tree.nodes.new("ShaderNodeAttribute")
ao.name = "Hair Tool AO"
ao.attribute_name = "AO"
material.node_tree.links.new(factor.outputs["Factor"], shader.inputs["Factor [Map]"])
material.node_tree.links.new(random.outputs["Factor"], shader.inputs["Random Id [Map]"])
material.node_tree.links.new(system.outputs["Color"], shader.inputs["Debug Color"])
material.node_tree.links.new(ao.outputs["Color"], shader.inputs["Vert Color AO [Map]"])

mesh = bpy.data.meshes.new("HTUE_Deformer_Source")
mesh.from_pydata([(0, 0, 0), (1, 0, 0)], [], [])
mesh.materials.append(material)
system_colors = mesh.attributes.new("SystemColor", "BYTE_COLOR", "POINT")
system_colors.data[0].color = (0.1, 0.2, 0.3, 0.0)
system_colors.data[1].color = (0.8, 0.6, 0.4, 1.0)
source_object = bpy.data.objects.new("HTUE_Deformer_Source", mesh)
bpy.context.scene.collection.objects.link(source_object)

stack = nodes.setup_material(material)
assert stack is not None
assert shader.node_tree != hair_group
assert shader.node_tree.name.startswith(schema.SHADER_CLONE_PREFIX)
assert hair_group.nodes["Hair Tool Final Color"].inputs[0].links[0].from_node == group_input
assert shader.inputs["Factor [Map]"].links[0].from_node == factor
assert shader.inputs["Random Id [Map]"].links[0].from_node == random
assert shader.inputs["Debug Color"].links[0].from_node == system
assert shader.inputs["Vert Color AO [Map]"].links[0].from_node == ao
assert shader.inputs["Root Color Mix Factor"].default_value == 1.0
assert not shader.inputs["Root Color Mix Factor"].is_linked
assert stack.inputs["System Attribute Color"].links[0].from_node.bl_idname == "NodeGroupInput"
for bridge_owned_input in (
    "HT Base Color",
    "HT Root Color",
    "HT Root Mix",
    "HT Tip Color",
    "HT Tip Mix",
    "Depth Tint Influence",
    "System Color Influence",
    "AO Color Influence",
):
    assert not stack.inputs[bridge_owned_input].is_linked, bridge_owned_input
assert shader.node_tree.nodes["Hair Tool Final Color"].inputs[0].links[0].from_node == stack
assert shader.inputs.get("HTUE System Mask") is None
assert material.htue_settings.initialized
assert normal_node.inputs["Flip Backface Normal"].default_value == 1.0
assert contract.validate_material(material) == []
saved_v3 = schema.loads_contract(material[schema.CONTRACT_PROPERTY])
assert saved_v3["version"] == 3
assert not saved_v3["hair_tool"]["vertex_uv_payload"]["system_color_alpha_used"]

material.htue_settings.root_blend_mode = "OVERLAY"
assert stack.inputs["Root Blend Mode"].default_value == 2.0
saved = schema.loads_contract(material[schema.CONTRACT_PROPERTY])
assert saved["hair_tool"]["scalar_parameters"]["Root Blend Mode"] == 2.0

# Legacy HairShaderMain material controls are intentionally not pulled into the
# bridge contract. Deformer attribute links remain live independently.
bridge_root_mix = material.htue_settings.root_mix
shader.inputs["Root Color Mix Factor"].default_value = 0.35
contract.refresh_material_contract(material)
assert abs(material.htue_settings.root_mix - bridge_root_mix) < 1.0e-6
assert abs(stack.inputs["HT Root Mix"].default_value - bridge_root_mix) < 1.0e-6

# Bridge edits update only the replacement stack, never the legacy shader UI.
tracked_stack_values = {
    name: (
        tuple(stack.inputs[name].default_value)
        if hasattr(stack.inputs[name].default_value, "__len__")
        else float(stack.inputs[name].default_value)
    )
    for name in (*schema.VECTOR_FIELDS.values(), *schema.SCALAR_FIELDS.values())
}
material.htue_settings.root_mix = 0.65
assert abs(stack.inputs["HT Root Mix"].default_value - 0.65) < 1.0e-6
assert abs(shader.inputs["Root Color Mix Factor"].default_value - 0.35) < 1.0e-6
changed_stack_inputs = [
    name
    for name, before in tracked_stack_values.items()
    if before != (
        tuple(stack.inputs[name].default_value)
        if hasattr(stack.inputs[name].default_value, "__len__")
        else float(stack.inputs[name].default_value)
    )
]
assert changed_stack_inputs == ["HT Root Mix"], changed_stack_inputs

# Root Range=0 must use Hair Tool's safe-Map-Range fallback of one.
root_zero = stack.node_tree.nodes["HTUE Root Range Zero Is Full"]
assert root_zero.bl_idname == "ShaderNodeMix"

# The replacement stack is ordered Base > System > Root > Tip > ID > Depth > AO.
assert stack.node_tree.nodes["HTUE Stage Root Base"].inputs[0].links[0].from_node.name == "HTUE Stage System"
assert stack.node_tree.nodes["HTUE Stage Tip Base"].inputs[0].links[0].from_node.name == "HTUE Stage Root"
assert stack.node_tree.nodes["HTUE Stage ID Base"].inputs[0].links[0].from_node.name == "HTUE Stage Tip"
assert stack.node_tree.nodes["HTUE Stage Depth Base"].inputs[0].links[0].from_node.name == "HTUE Stage ID"
assert stack.node_tree.nodes["HTUE Stage AO Base"].inputs[0].links[0].from_node.name == "HTUE Stage Depth"

# Unreal shares the Random/ID source between Root, Tip, and ID. With
# ID Map Influence=1 this is IRD.R; with 0 it is Hair Tool's Random attribute.
assert (
    stack.node_tree.nodes["HTUE Root Random Value"].inputs[0].links[0].from_node.name
    == "HTUE ID Driver"
)
assert (
    stack.node_tree.nodes["HTUE Tip Random Value"].inputs[0].links[0].from_node.name
    == "HTUE ID Driver"
)

# Re-running setup is the load migration path and must keep native links.
addon.migrate_bridge_ui_on_load(None)
shader = material.node_tree.nodes["HairShaderMain"]
stack = shader.node_tree.nodes[schema.INTERNAL_STACK_NODE_NAME]
assert shader.inputs["Factor [Map]"].links[0].from_node == factor
assert shader.inputs["Debug Color"].links[0].from_node == system
assert normal_node.inputs["Flip Backface Normal"].default_value == 1.0

nodes.restore_material(material)
assert not material.htue_settings.initialized
assert shader.node_tree == hair_group
assert shader.inputs["Root Color Mix Factor"].default_value == 1.0
assert shader.inputs["Factor [Map]"].links[0].from_node == factor
assert shader.inputs["Debug Color"].links[0].from_node == system
assert normal_node.inputs["Flip Backface Normal"].default_value == 0.5
assert bpy.data.node_groups.get(f"{schema.SHADER_CLONE_PREFIX}::{material.name}") is None

print("HTUE_BLENDER_SMOKE_OK")
