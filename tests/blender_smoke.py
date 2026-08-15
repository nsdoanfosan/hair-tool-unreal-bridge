import addon_utils
import bpy


addon_utils.enable("hair_tool_unreal_bridge", default_set=False, persistent=False)

from hair_tool_unreal_bridge import contract, nodes, schema


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
):
    input_socket(hair_group, name, "NodeSocketFloat", default)

material = bpy.data.materials.new("M_HT_Default_Material_01")
material.use_nodes = True
shader = material.node_tree.nodes.new("ShaderNodeGroup")
shader.name = "HairShaderMain"
shader.node_tree = hair_group

bridge = nodes.setup_material(material)
assert bridge is not None
assert bridge.inputs.get("System Color 01") is not None
assert bridge.inputs.get("System Color 02") is not None
assert shader.inputs["Root Color Mix Factor"].default_value == 0.0
assert shader.inputs["Tip Color Mix Factor"].default_value == 0.0
assert material.htue_settings.initialized
assert contract.validate_material(material) == []

material.htue_settings.root_blend_mode = "OVERLAY"
assert bridge.inputs["Root Blend Mode"].default_value == 2.0
assert bridge.node_tree.nodes.get("HTUE Stage Root") is not None
saved = schema.loads_contract(material[schema.CONTRACT_PROPERTY])
assert saved["hair_tool"]["scalar_parameters"]["Root Blend Mode"] == 2.0

nodes.restore_material(material)
assert not material.htue_settings.initialized
assert shader.inputs["Root Color Mix Factor"].default_value == 1.0
assert material.node_tree.nodes.get(schema.BRIDGE_NODE_NAME) is None

print("HTUE_BLENDER_SMOKE_OK")
