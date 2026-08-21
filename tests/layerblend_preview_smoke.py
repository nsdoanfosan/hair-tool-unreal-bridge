from __future__ import annotations

import addon_utils
import json
from pathlib import Path
import sys
import tempfile

import bpy


def script_args():
    return sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []


arguments = script_args()
repo = Path(arguments[arguments.index("--repo") + 1]).resolve()
sys.path.insert(0, str(repo / "addons"))

addon_utils.enable("hair_tool_unreal_bridge", default_set=False, persistent=False)
from hair_tool_unreal_bridge import layerblend_contract, layerblend_preview


bpy.ops.wm.read_factory_settings(use_empty=True)
addon_utils.enable("hair_tool_unreal_bridge", default_set=False, persistent=False)

with tempfile.TemporaryDirectory(prefix="umb_layerblend_preview_") as temporary:
    root = Path(temporary)
    report_directory = root / "reports" / "unreal"
    report_directory.mkdir(parents=True)
    report_path = report_directory / "unreal_tiling_verify_smoke.json"
    report_path.write_text(
        json.dumps(
            {
                "material_instances": [
                    {
                        "manifest_material": "M_LayerBlend_Test",
                        "asset_class": "MaterialInstanceConstant",
                        "asset_path": "/Game/Material/AssetSurface/MI/LayerBlend/MI_LayerBlend_Test",
                        "parent": "/Game/Material/AssetSurface/Master/M_LayerBlend",
                        "preserved_after": {
                            "scalars": {"Height": 1.0, "Height_Strengh": 0.1},
                            "static_switches": {"VertexColor_HeightBlend": True},
                        },
                        "direct_scalar_overrides_after": [
                            {
                                "parameter": "Height_Strengh",
                                "association": "LayerParameter",
                                "index": 0,
                                "value": 0.5,
                            }
                        ],
                        "height_preview_after": {
                            "displacement_scaling": {"magnitude": 8.0, "center": 0.0}
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    image = bpy.data.images.new("T_LayerBlend_Test_height", width=4, height=4)
    image.pixels = [1.0, 1.0, 1.0, 1.0] * 16
    image.pack()

    material = bpy.data.materials.new("M_LayerBlend_Test")
    material.use_nodes = True
    material["tiling_master"] = "M_LayerBlend"
    material["tiling_report_directory"] = str(report_directory)
    height_node = material.node_tree.nodes.new("ShaderNodeTexImage")
    height_node.name = layerblend_contract.HEIGHT_NODE_NAME
    height_node.image = image

    mesh = bpy.data.meshes.new("LayerBlendPreviewSmokeMesh")
    mesh.from_pydata(
        [(-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.materials.append(material)
    uv = mesh.uv_layers.new(name="UVMap")
    for item, coordinate in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        item.uv = coordinate
    color = mesh.color_attributes.new(name="Color", type="FLOAT_COLOR", domain="POINT")
    for item in color.data:
        item.color = (0.5, 0.0, 0.0, 1.0)

    obj = bpy.data.objects.new("LayerBlendPreviewSmoke", mesh)
    bpy.context.scene.collection.objects.link(obj)
    second = bpy.data.objects.new("LayerBlendPreviewSmoke_Unselected", mesh.copy())
    second.location.x = 3.0
    bpy.context.scene.collection.objects.link(second)

    # Group Pro mesh groups keep their visible members in an unlinked Collection
    # supplied to the GPro_Instance Geometry Nodes modifier. Those members are
    # intentionally absent from scene.objects.
    grouped_collection = bpy.data.collections.new("LayerBlendPreviewSmoke_GroupProCollection")
    grouped = bpy.data.objects.new("LayerBlendPreviewSmoke_Grouped", mesh.copy())
    grouped_collection.objects.link(grouped)
    group_host = bpy.data.objects.new(
        "LayerBlendPreviewSmoke_GroupProHost",
        bpy.data.meshes.new("LayerBlendPreviewSmoke_GroupProHostMesh"),
    )
    bpy.context.scene.collection.objects.link(group_host)
    group_tree = bpy.data.node_groups.new("GPro_Instance", "GeometryNodeTree")
    group_tree.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )
    group_tree.interface.new_socket(
        name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
    )
    collection_socket = group_tree.interface.new_socket(
        name="Collection", in_out="INPUT", socket_type="NodeSocketCollection"
    )
    group_input = group_tree.nodes.new("NodeGroupInput")
    group_output = group_tree.nodes.new("NodeGroupOutput")
    group_tree.links.new(group_input.outputs["Geometry"], group_output.inputs["Geometry"])
    group_modifier = group_host.modifiers.new("GPro_Instance", "NODES")
    group_modifier.node_group = group_tree
    try:
        group_modifier.properties.inputs[collection_socket.identifier]["value"] = grouped_collection
    except (AttributeError, KeyError, TypeError):
        group_modifier[collection_socket.identifier] = grouped_collection
    # Even if a Group Pro host carries the same material slots, it must not
    # receive a second Height pass after the instanced members.
    group_host.data.materials.append(material)
    stale_host_preview = group_host.modifiers.new(
        layerblend_preview.MODIFIER_NAME, "NODES"
    )
    stale_host_preview[layerblend_preview.MODIFIER_MARKER] = True

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    second.select_set(False)

    # Saved files and Group Pro cache copies can lose modifier custom
    # properties.  Name and generated-group markers must still identify and
    # consolidate those legacy preview modifiers.
    legacy_group_a = bpy.data.node_groups.new("LegacyPreviewA", "GeometryNodeTree")
    legacy_group_a[layerblend_preview.GROUP_MARKER] = True
    legacy_a = obj.modifiers.new(layerblend_preview.MODIFIER_NAME, "NODES")
    legacy_a.node_group = legacy_group_a
    legacy_group_b = bpy.data.node_groups.new("LegacyPreviewB", "GeometryNodeTree")
    legacy_group_b[layerblend_preview.GROUP_MARKER] = True
    legacy_b = obj.modifiers.new(layerblend_preview.MODIFIER_NAME, "NODES")
    legacy_b.node_group = legacy_group_b

    before_modifier_types = [
        modifier.type
        for modifier in obj.modifiers
        if modifier not in {legacy_a, legacy_b}
    ]
    summary = layerblend_preview.sync_scene_previews(force=True)
    assert grouped.name not in bpy.context.scene.objects
    assert summary["candidates"] == 3
    assert summary["synchronized"] == 3
    assert summary["instanced_objects"] == 1
    assert summary["instanced_collections"] == 1
    assert summary["group_pro_hosts"] == 1
    assert summary["group_pro_hosts_skipped"] == 1
    assert summary["removed"] == 1
    assert summary["duplicates_removed"] == 1
    assert not summary["errors"]
    contract_data = layerblend_contract.loads_contract(
        obj[layerblend_contract.OBJECT_CONTRACT_PROPERTY]
    )
    modifier = layerblend_preview._preview_modifier(obj)
    second_modifier = layerblend_preview._preview_modifier(second)
    grouped_modifier = layerblend_preview._preview_modifier(grouped)
    assert modifier is not None
    assert second_modifier is not None
    assert grouped_modifier is not None
    assert layerblend_preview._preview_modifier(group_host) is None
    assert modifier.node_group == second_modifier.node_group
    assert modifier.node_group == grouped_modifier.node_group
    assert modifier.show_viewport
    assert not modifier.show_render
    assert modifier.show_in_editmode
    assert second_modifier.show_in_editmode
    assert len(layerblend_preview._preview_modifiers(obj)) == 1
    assert [m.type for m in obj.modifiers if m != modifier] == before_modifier_types
    assert not any(m.type == "SUBSURF" for m in obj.modifiers)
    assert contract_data["preview_only"]
    assert contract_data["export_geometry_unchanged"]
    assert contract_data["material_driven_sync"]
    assert obj.get(layerblend_preview.SOURCE_SIGNATURE_PROPERTY)
    assert contract_data["materials"][0]["scaling_source"] == "unreal_report"

    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    evaluated = obj.evaluated_get(depsgraph)
    evaluated_mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        z_values = [vertex.co.z for vertex in evaluated_mesh.vertices]
        assert z_values
        # 1.0 texture * 0.5 layer * 1.0 master * 0.5 Color.R * 8 cm = 2 cm.
        assert max(abs(value - 0.02) for value in z_values) < 1.0e-5, z_values
    finally:
        evaluated.to_mesh_clear()

    states = layerblend_preview.suspend_height_previews()
    assert len(states) == 3
    assert not modifier.show_viewport
    assert not second_modifier.show_viewport
    assert not grouped_modifier.show_viewport
    assert not modifier.show_render
    layerblend_preview.auto_sync_timer()
    assert not modifier.show_viewport
    assert not second_modifier.show_viewport
    assert not grouped_modifier.show_viewport
    assert set(layerblend_preview.restore_height_previews(states)) == {
        obj.name,
        second.name,
        grouped.name,
    }
    assert modifier.show_viewport
    assert second_modifier.show_viewport
    assert grouped_modifier.show_viewport
    assert not modifier.show_render
    assert modifier.show_in_editmode

    modifier.show_on_cage = True
    layerblend_preview.sync_scene_previews(force=True)
    assert modifier.show_in_editmode
    assert modifier.show_on_cage

    duplicate = obj.modifiers.new(layerblend_preview.MODIFIER_NAME, "NODES")
    duplicate.node_group = modifier.node_group
    assert len(layerblend_preview._preview_modifiers(obj)) == 2
    modifier.show_in_editmode = False
    keeper, duplicates_removed = layerblend_preview._consolidate_preview_modifiers(obj)
    assert keeper == modifier
    assert duplicates_removed == 1
    assert modifier.show_in_editmode

    duplicate = obj.modifiers.new(layerblend_preview.MODIFIER_NAME, "NODES")
    duplicate.node_group = modifier.node_group

    assert layerblend_preview.remove_preview(obj)
    assert layerblend_preview._preview_modifier(obj) is None
    assert layerblend_preview._preview_modifier(second) is not None
    assert layerblend_contract.OBJECT_CONTRACT_PROPERTY not in obj
    assert layerblend_preview.remove_preview(second)
    assert layerblend_preview.remove_preview(grouped)
    empty_states = layerblend_preview.suspend_height_previews()
    assert empty_states == []
    assert layerblend_preview._auto_sync_is_suspended()
    assert layerblend_preview.restore_height_previews(empty_states) == []
    assert not layerblend_preview._auto_sync_is_suspended()
    print("UMB_LAYERBLEND_PREVIEW_SMOKE=OK")
