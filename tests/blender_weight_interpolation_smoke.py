"""Regression for byte-color overflow when Hair Tool smooths ChaosWeight.

Run with Blender --background --factory-startup --python <this file>.
The test enables only this add-on with default_set=False and never saves prefs.
"""

import addon_utils
import bpy
import json
import math


assert bpy.app.background, "Run this regression in a disposable background Blender"
assert addon_utils.enable(
    "hair_tool_unreal_bridge", default_set=False, persistent=False
) is not None

from hair_tool_unreal_bridge import export_masks


CONTROL_WEIGHTS = (0.0, 0.0, 0.0, 0.08, 0.4, 1.0)
weight_group = export_masks.ensure_mask_group("WEIGHT")


def new_wrapper(name):
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.interface.new_socket(
        name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
    )
    tree.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )
    return tree


def link_signature(tree):
    return sorted(
        (
            link.from_node.name,
            link.from_socket.identifier,
            link.to_node.name,
            link.to_socket.identifier,
        )
        for link in tree.links
    )


# Simulate the old saved group while it is in use. Migration must preserve the
# owning Hair Tool graph, its socket identifiers and the user's unlinked value.
owner = new_wrapper("HTUE_WEIGHT_MIGRATION_OWNER")
group_input = owner.nodes.new("NodeGroupInput")
group_output = owner.nodes.new("NodeGroupOutput")
linked_instance = owner.nodes.new("GeometryNodeGroup")
linked_instance.node_tree = weight_group
linked_instance.label = "Existing user weight"
constant_instance = owner.nodes.new("GeometryNodeGroup")
constant_instance.node_tree = weight_group
constant_instance.inputs["Influence Range"].default_value = 0.625
input_value = owner.nodes.new("ShaderNodeValue")
input_value.outputs[0].default_value = 0.375
owner.links.new(group_input.outputs[0], linked_instance.inputs["Geometry"])
owner.links.new(input_value.outputs[0], linked_instance.inputs["Influence Range"])
owner.links.new(linked_instance.outputs["Geometry"], group_output.inputs[0])

old_store = weight_group.nodes["Store ChaosWeight"]
old_store.data_type = "BYTE_COLOR"
weight_group[export_masks.GROUP_VERSION_PROPERTY] = 1
before_group_pointer = weight_group.as_pointer()
before_interface = [
    (item.as_pointer(), item.identifier, item.name, item.in_out)
    for item in weight_group.interface.items_tree
    if item.item_type == "SOCKET"
]
before_links = link_signature(owner)
assert export_masks.ensure_mask_group("WEIGHT") == weight_group
assert weight_group.as_pointer() == before_group_pointer
assert before_interface == [
    (item.as_pointer(), item.identifier, item.name, item.in_out)
    for item in weight_group.interface.items_tree
    if item.item_type == "SOCKET"
]
assert link_signature(owner) == before_links
assert constant_instance.inputs["Influence Range"].default_value == 0.625
assert linked_instance.label == "Existing user weight"
assert weight_group.nodes["Store ChaosWeight"].data_type == "FLOAT_COLOR"
assert weight_group[export_masks.GROUP_VERSION_PROPERTY] >= 2


def evaluate_weight(mask_group, suffix):
    mesh = bpy.data.meshes.new("HTUE_WEIGHT_INPUT_" + suffix)
    mesh.from_pydata(
        [(float(index), 0.0, 0.0) for index in range(len(CONTROL_WEIGHTS))],
        [(index, index + 1) for index in range(len(CONTROL_WEIGHTS) - 1)],
        [],
    )
    input_attribute = mesh.attributes.new("TestWeightInput", "FLOAT", "POINT")
    for item, value in zip(input_attribute.data, CONTROL_WEIGHTS):
        item.value = value
    obj = bpy.data.objects.new("HTUE_WEIGHT_" + suffix, mesh)
    bpy.context.scene.collection.objects.link(obj)

    tree = new_wrapper("HTUE_WEIGHT_CURVE_TO_MESH_" + suffix)
    nodes = tree.nodes
    links = tree.links
    source = nodes.new("NodeGroupInput")
    output = nodes.new("NodeGroupOutput")
    to_curve = nodes.new("GeometryNodeMeshToCurve")
    read_weight = nodes.new("GeometryNodeInputNamedAttribute")
    read_weight.data_type = "FLOAT"
    read_weight.inputs["Name"].default_value = "TestWeightInput"
    export_weight = nodes.new("GeometryNodeGroup")
    export_weight.node_tree = mask_group
    spline_type = nodes.new("GeometryNodeCurveSplineType")
    spline_type.spline_type = "CATMULL_ROM"
    resolution = nodes.new("GeometryNodeSetSplineResolution")
    resolution.inputs["Resolution"].default_value = 2
    profile = nodes.new("GeometryNodeCurvePrimitiveLine")
    profile.inputs["Start"].default_value = (0.0, -0.05, 0.0)
    profile.inputs["End"].default_value = (0.0, 0.05, 0.0)
    to_mesh = nodes.new("GeometryNodeCurveToMesh")

    links.new(source.outputs["Geometry"], to_curve.inputs["Mesh"])
    links.new(to_curve.outputs["Curve"], export_weight.inputs["Geometry"])
    links.new(read_weight.outputs["Attribute"], export_weight.inputs["Influence Range"])
    links.new(export_weight.outputs["Geometry"], spline_type.inputs["Curve"])
    links.new(spline_type.outputs["Curve"], resolution.inputs["Curve"])
    links.new(resolution.outputs["Curve"], to_mesh.inputs["Curve"])
    links.new(profile.outputs["Curve"], to_mesh.inputs["Profile Curve"])

    # Exercise Blender's real scalar Clamp on the evaluated float mask. Both the
    # debug shader and exporter must clamp at this late stage, after smoothing.
    evaluated_weight = nodes.new("GeometryNodeInputNamedAttribute")
    evaluated_weight.data_type = "FLOAT_COLOR"
    evaluated_weight.inputs["Name"].default_value = "ChaosWeight"
    separate = nodes.new("FunctionNodeSeparateColor")
    separate.mode = "RGB"
    clamp = nodes.new("ShaderNodeClamp")
    clamp.inputs["Min"].default_value = 0.0
    clamp.inputs["Max"].default_value = 1.0
    store_clamped = nodes.new("GeometryNodeStoreNamedAttribute")
    store_clamped.data_type = "FLOAT"
    store_clamped.domain = "POINT"
    store_clamped.inputs["Name"].default_value = "TestClampedWeight"
    links.new(to_mesh.outputs["Mesh"], store_clamped.inputs["Geometry"])
    links.new(evaluated_weight.outputs["Attribute"], separate.inputs["Color"])
    links.new(separate.outputs["Red"], clamp.inputs["Value"])
    links.new(clamp.outputs["Result"], store_clamped.inputs["Value"])
    links.new(store_clamped.outputs["Geometry"], output.inputs["Geometry"])

    modifier = obj.modifiers.new("Weight Interpolation Regression", "NODES")
    modifier.node_group = tree
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    evaluated_mesh = evaluated.to_mesh(
        preserve_all_data_layers=True, depsgraph=depsgraph
    )
    assert evaluated_mesh is not None, (
        f"Missing mesh output for {suffix}: type={evaluated.type}, "
        f"data={evaluated.data}, nodes={link_signature(tree)}"
    )
    try:
        positions = [tuple(vertex.co) for vertex in evaluated_mesh.vertices]
        raw = [item.color[0] for item in evaluated_mesh.attributes["ChaosWeight"].data]
        clamped = [
            item.value for item in evaluated_mesh.attributes["TestClampedWeight"].data
        ]
        data_type = evaluated_mesh.attributes["ChaosWeight"].data_type
    finally:
        evaluated.to_mesh_clear()
    return positions, raw, clamped, data_type


legacy_group = weight_group.copy()
legacy_group.name = "HTUE_WEIGHT_LEGACY_BYTE_CONTROL"
legacy_group.nodes["Store ChaosWeight"].data_type = "BYTE_COLOR"
legacy = evaluate_weight(legacy_group, "LEGACY")
fixed = evaluate_weight(weight_group, "FLOAT")
assert legacy[3] == "BYTE_COLOR"
assert fixed[3] == "FLOAT_COLOR"
assert legacy[0] == fixed[0], "Changing mask storage changed hair geometry"
assert len(fixed[0]) == 22, "Expected 11 longitudinal rings with 2 profile vertices"

plateau_indices = [
    index for index, position in enumerate(fixed[0]) if 1.0 < position[0] < 2.0
]
assert plateau_indices, "Missing evaluated point between zero plateau controls"
assert max(legacy[1][index] for index in plateau_indices) > 0.9, (
    "Legacy control did not reproduce the near-white byte overflow"
)
assert all(-0.01 < fixed[1][index] < 0.0 for index in plateau_indices), (
    "FLOAT_COLOR must retain the small cubic undershoot, without wrapping"
)
assert all(fixed[2][index] == 0.0 for index in plateau_indices)

ordered = sorted(range(len(fixed[0])), key=lambda index: fixed[0][index][0])
safe_values = [fixed[2][index] for index in ordered]
assert all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in safe_values)
assert all(a <= b + 1.0e-6 for a, b in zip(safe_values, safe_values[1:])), safe_values
assert safe_values[0] == 0.0 and safe_values[-1] == 1.0

print(
    "HTUE_WEIGHT_INTERPOLATION_SMOKE_OK "
    + json.dumps(
        {
            "vertices": len(fixed[0]),
            "legacy_plateau_peak": max(legacy[1][index] for index in plateau_indices),
            "float_minimum": min(fixed[1]),
            "clamped_range": [min(safe_values), max(safe_values)],
            "migration_preserved_interface_and_links": True,
        },
        sort_keys=True,
    )
)
