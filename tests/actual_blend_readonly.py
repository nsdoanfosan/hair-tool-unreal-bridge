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
    data, deformer = contract.refresh_material_contract(material)
    results[material_name] = {
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
        "deformer": deformer,
    }

assert all(item["existing_links_preserved"] for item in results.values())
assert all(item["attribute_inputs_connected"] for item in results.values())
assert all(not item["contract_errors"] for item in results.values())
assert all(item["legacy_color_result_replaced"] for item in results.values())
print("HTUE_ACTUAL_BLEND_READONLY=" + json.dumps(results, sort_keys=True))
