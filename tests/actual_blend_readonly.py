import json

import addon_utils
import bpy


addon_utils.enable("hair_tool_unreal_bridge", default_set=False, persistent=False)

from hair_tool_unreal_bridge import contract, nodes, schema


results = {}
for material_name in schema.TARGET_TEXTURE_SETS:
    material = bpy.data.materials[material_name]
    shader = nodes.find_hair_shader(material)
    tracked_inputs = (
        "Factor [Map]",
        "Random Id [Map]",
        "Debug Color",
        "Vert Color AO [Map]",
    )
    before = {
        name: [f"{link.from_node.name}.{link.from_socket.name}" for link in shader.inputs[name].links]
        for name in tracked_inputs
    }
    stack = nodes.setup_material(material)
    shader = nodes.find_hair_shader(material)
    after = {
        name: [f"{link.from_node.name}.{link.from_socket.name}" for link in shader.inputs[name].links]
        for name in tracked_inputs
    }
    data, transport = contract.refresh_material_contract(material)
    results[material_name] = {
        "unreal_backface_normals": [
            float(node.inputs[schema.BACKFACE_NORMAL_INPUT].default_value)
            for node in material.node_tree.nodes
            if node.bl_idname == "ShaderNodeGroup"
            and node.node_tree is not None
            and node.node_tree.name == schema.HAIR_TOOL_NORMAL_GROUP
            and node.inputs.get(schema.BACKFACE_NORMAL_INPUT) is not None
            and not node.inputs[schema.BACKFACE_NORMAL_INPUT].is_linked
        ],
        "existing_links_preserved": all(
            not before[name] or before[name] == after[name]
            for name in tracked_inputs
        ),
        "attribute_inputs_connected": all(after[name] for name in tracked_inputs),
        "filled_inputs": [name for name in tracked_inputs if not before[name] and after[name]],
        "before": before,
        "after": after,
        "clone": shader.node_tree.name,
        "legacy_color_result_replaced": bool(stack.get("htue_replaces_legacy_color_blends")),
        "legacy_material_blends_disconnected": all(
            not stack.inputs[name].is_linked
            for name in (
                "HT Base Color",
                "HT Root Color",
                "HT Root Mix",
                "HT Tip Color",
                "HT Tip Mix",
                "System Color Influence",
                "AO Color Influence",
            )
        ),
        "native_mix_values": {
            name: float(shader.inputs[name].default_value)
            for name in (
                "Root Color Mix Factor",
                "Tip Color Mix Factor",
                "Debug  Color  Mix",
                "Depth Mix Factor",
                "AO Mix Factor",
            )
        },
        "contract_errors": schema.validate_contract(data),
        "contract_version": data["version"],
        "system_color_alpha_socket_removed": shader.inputs.get("HTUE System Mask") is None,
        "system_color_transport": transport,
    }

assert all(item["existing_links_preserved"] for item in results.values())
assert all(item["attribute_inputs_connected"] for item in results.values())
assert all(not item["contract_errors"] for item in results.values())
assert all(item["legacy_color_result_replaced"] for item in results.values())
assert all(item["legacy_material_blends_disconnected"] for item in results.values())
assert all(item["contract_version"] == 3 for item in results.values())
assert all(
    item["unreal_backface_normals"]
    and all(
        value == schema.UNREAL_BACKFACE_NORMAL_VALUE
        for value in item["unreal_backface_normals"]
    )
    for item in results.values()
)
assert all(item["system_color_alpha_socket_removed"] for item in results.values())
print("HTUE_ACTUAL_BLEND_READONLY=" + json.dumps(results, sort_keys=True))
