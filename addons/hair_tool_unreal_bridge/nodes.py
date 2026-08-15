import json
from pathlib import Path

import bpy

from . import contract, schema


REQUIRED_HAIR_INPUTS = {
    "Base Color",
    "Root Color",
    "Root Color Mix Factor",
    "Tip Color",
    "Tip Color Mix Factor",
    "Debug Color",
    "Debug  Color  Mix",
}

MANAGED_TOP_LEVEL_NODES = (
    schema.BRIDGE_NODE_NAME,
    "HTUE Random",
    "HTUE Factor",
    "HTUE AO",
    "HTUE SystemColor",
    "HTUE Depth",
    "HTUE IRD Map",
    "HTUE ORM Map",
    "HTUE UV",
)

def find_hair_shader(material):
    if not material.use_nodes or material.node_tree is None:
        return None
    for node in material.node_tree.nodes:
        if node.bl_idname != "ShaderNodeGroup" or node.node_tree is None:
            continue
        if REQUIRED_HAIR_INPUTS.issubset({socket.name for socket in node.inputs}):
            return node
    return None


def _new_socket(tree, name, in_out, socket_type, default=None, min_value=None, max_value=None):
    socket = tree.interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)
    if default is not None and hasattr(socket, "default_value"):
        socket.default_value = default
    if min_value is not None and hasattr(socket, "min_value"):
        socket.min_value = min_value
    if max_value is not None and hasattr(socket, "max_value"):
        socket.max_value = max_value
    return socket


def _set_or_link(tree, socket, value):
    if hasattr(value, "bl_idname") and value.bl_idname.startswith("NodeSocket"):
        tree.links.new(value, socket)
    else:
        socket.default_value = value


def _math(tree, operation, a, b=None, *, name="", clamp=False):
    node = tree.nodes.new("ShaderNodeMath")
    node.operation = operation
    node.name = name or node.name
    node.use_clamp = clamp
    _set_or_link(tree, node.inputs[0], a)
    if b is not None:
        _set_or_link(tree, node.inputs[1], b)
    return node.outputs[0]


def _mix_float(tree, factor, a, b, *, name=""):
    node = tree.nodes.new("ShaderNodeMix")
    node.data_type = "FLOAT"
    node.name = name or node.name
    _set_or_link(tree, node.inputs[0], factor)
    _set_or_link(tree, node.inputs[2], a)
    _set_or_link(tree, node.inputs[3], b)
    return node.outputs[0]


def _combine_rgb(tree, red, green, blue, name):
    node = tree.nodes.new("ShaderNodeCombineColor")
    node.mode = "RGB"
    node.name = name
    node.label = name.replace("HTUE Stage ", "")
    for socket, value in zip(node.inputs[:3], (red, green, blue)):
        _set_or_link(tree, socket, value)
    return node.outputs[0]


def _blend_channel(tree, base, layer, factor, mode, name):
    """Match Reallusion blendFunc exactly for one color channel."""
    multiply = _math(tree, "MULTIPLY", base, layer, name=f"{name} Multiply")

    overlay_low = _math(tree, "MULTIPLY", base, layer)
    overlay_low = _math(tree, "MULTIPLY", overlay_low, 2.0)
    inverse_base = _math(tree, "SUBTRACT", 1.0, base)
    inverse_layer = _math(tree, "SUBTRACT", 1.0, layer)
    overlay_high = _math(tree, "MULTIPLY", inverse_base, inverse_layer)
    overlay_high = _math(tree, "MULTIPLY", overlay_high, 2.0)
    overlay_high = _math(tree, "SUBTRACT", 1.0, overlay_high)
    overlay_low_gate = _math(tree, "LESS_THAN", base, 0.5)
    overlay = _mix_float(tree, overlay_low_gate, overlay_high, overlay_low)

    twice_layer = _math(tree, "MULTIPLY", layer, 2.0)
    one_minus_twice_layer = _math(tree, "SUBTRACT", 1.0, twice_layer)
    soft_low_a = _math(tree, "MULTIPLY", base, layer)
    soft_low_a = _math(tree, "MULTIPLY", soft_low_a, 2.0)
    base_squared = _math(tree, "MULTIPLY", base, base)
    soft_low_b = _math(tree, "MULTIPLY", base_squared, one_minus_twice_layer)
    soft_low = _math(tree, "ADD", soft_low_a, soft_low_b)

    twice_layer_minus_one = _math(tree, "SUBTRACT", twice_layer, 1.0)
    square_root_base = _math(tree, "SQRT", base)
    soft_high_a = _math(tree, "MULTIPLY", square_root_base, twice_layer_minus_one)
    one_minus_layer = _math(tree, "SUBTRACT", 1.0, layer)
    soft_high_b = _math(tree, "MULTIPLY", base, one_minus_layer)
    soft_high_b = _math(tree, "MULTIPLY", soft_high_b, 2.0)
    soft_high = _math(tree, "ADD", soft_high_a, soft_high_b)
    soft_low_gate = _math(tree, "LESS_THAN", layer, 0.5)
    soft_light = _mix_float(tree, soft_low_gate, soft_high, soft_low)

    add = _math(tree, "ADD", base, layer, name=f"{name} Add")

    below_add = _math(tree, "LESS_THAN", mode, 3.5)
    selected = _mix_float(tree, below_add, add, soft_light)
    below_soft = _math(tree, "LESS_THAN", mode, 2.5)
    selected = _mix_float(tree, below_soft, selected, overlay)
    below_overlay = _math(tree, "LESS_THAN", mode, 1.5)
    selected = _mix_float(tree, below_overlay, selected, multiply)
    normal_gate = _math(tree, "LESS_THAN", mode, 0.5)
    selected = _mix_float(tree, normal_gate, selected, layer)
    return _mix_float(tree, factor, base, selected, name=name)


def _blend(tree, name, base, layer, factor, mode):
    base_channels = _separate(tree, base, f"{name} Base")
    layer_channels = _separate(tree, layer, f"{name} Layer")
    result = [
        _blend_channel(
            tree,
            base_channels[index],
            layer_channels[index],
            factor,
            mode,
            f"{name} {channel}",
        )
        for index, channel in enumerate("RGB")
    ]
    return _combine_rgb(tree, *result, name)


def _clamp01(tree, value, name=""):
    return _math(tree, "MULTIPLY", value, 1.0, name=name, clamp=True)


def _separate(tree, color, name):
    node = tree.nodes.new("ShaderNodeSeparateColor")
    node.mode = "RGB"
    node.name = name
    tree.links.new(color, node.inputs[0])
    return node.outputs[0], node.outputs[1], node.outputs[2]


def _gray(tree, value, name):
    node = tree.nodes.new("ShaderNodeCombineColor")
    node.mode = "RGB"
    node.name = name
    for index in range(3):
        tree.links.new(value, node.inputs[index])
    return node.outputs[0]


def _build_group(group, settings):
    group.nodes.clear()
    group.interface.clear()
    for name in ("Random", "Factor", "AO Vertex", "System Mask", "Depth Vertex"):
        _new_socket(group, name, "INPUT", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _new_socket(group, "IRD Map", "INPUT", "NodeSocketColor", (0.0, 0.0, 0.0, 1.0))
    _new_socket(group, "ORM Map", "INPUT", "NodeSocketColor", (1.0, 1.0, 0.0, 1.0))
    for field, name in schema.VECTOR_FIELDS.items():
        _new_socket(group, name, "INPUT", "NodeSocketColor", tuple(getattr(settings, field)))
    for field, name in schema.SCALAR_FIELDS.items():
        value = (
            schema.BLEND_MODE_VALUES[getattr(settings, field)]
            if field in schema.BLEND_FIELDS
            else float(getattr(settings, field))
        )
        _new_socket(group, name, "INPUT", "NodeSocketFloat", value)
    _new_socket(group, "Color", "OUTPUT", "NodeSocketColor")
    _new_socket(group, "Ambient Occlusion", "OUTPUT", "NodeSocketFloat")

    group_in = group.nodes.new("NodeGroupInput")
    group_in.name = "HTUE Inputs"
    group_in.location = (-2200, 0)
    group_out = group.nodes.new("NodeGroupOutput")
    group_out.name = "HTUE Outputs"
    group_out.location = (1800, 0)
    i = group_in.outputs

    ird_r, ird_g, ird_b = _separate(group, i["IRD Map"], "HTUE IRD Channels")
    orm_r, _orm_g, _orm_b = _separate(group, i["ORM Map"], "HTUE ORM Channels")

    eps = 0.001
    one = 1.0
    root_delta = _math(group, "SUBTRACT", i["HT Root Range"], i["Factor"])
    root_safe = _math(group, "MAXIMUM", i["HT Root Range"], eps)
    root_mask = _clamp01(group, _math(group, "DIVIDE", root_delta, root_safe))
    root_random_value = _math(group, "ADD", i["Random"], i["HT Root Random Brightness"])
    root_random = _mix_float(group, i["HT Root Random Influence"], one, root_random_value)
    root_texture = _mix_float(group, i["Root Map Influence"], one, ird_g)
    root_weight = _math(group, "MULTIPLY", root_mask, i["HT Root Mix"])
    root_weight = _math(group, "MULTIPLY", root_weight, root_random)
    root_weight = _clamp01(group, _math(group, "MULTIPLY", root_weight, root_texture))
    color = _blend(
        group,
        "HTUE Stage Root",
        i["HT Base Color"],
        i["HT Root Color"],
        root_weight,
        i["Root Blend Mode"],
    )

    tip_start = _math(group, "SUBTRACT", one, i["HT Tip Range"])
    tip_delta = _math(group, "SUBTRACT", i["Factor"], tip_start)
    tip_safe = _math(group, "MAXIMUM", i["HT Tip Range"], eps)
    tip_mask = _clamp01(group, _math(group, "DIVIDE", tip_delta, tip_safe))
    tip_random_value = _math(group, "ADD", i["Random"], i["HT Tip Random Brightness"])
    tip_random = _mix_float(group, i["HT Tip Random Influence"], one, tip_random_value)
    tip_texture_mask = _math(group, "SUBTRACT", one, ird_g)
    tip_texture = _mix_float(group, i["Tip Map Influence"], one, tip_texture_mask)
    tip_weight = _math(group, "MULTIPLY", tip_mask, i["HT Tip Mix"])
    tip_weight = _math(group, "MULTIPLY", tip_weight, tip_random)
    tip_weight = _clamp01(group, _math(group, "MULTIPLY", tip_weight, tip_texture))
    color = _blend(
        group,
        "HTUE Stage Tip",
        color,
        i["HT Tip Color"],
        tip_weight,
        i["Tip Blend Mode"],
    )

    id_driver = _mix_float(group, i["ID Map Influence"], i["Random"], ird_r)
    id_weight = _clamp01(group, _math(group, "MULTIPLY", id_driver, i["ID Tint Influence"]))
    color = _blend(
        group,
        "HTUE Stage ID",
        color,
        i["ID Tint Color"],
        id_weight,
        i["ID Blend Mode"],
    )

    depth_driver = _mix_float(group, i["Depth Map Influence"], i["Depth Vertex"], ird_b)
    depth_weight = _clamp01(
        group, _math(group, "MULTIPLY", depth_driver, i["Depth Tint Influence"])
    )
    color = _blend(
        group,
        "HTUE Stage Depth",
        color,
        i["Depth Tint Color"],
        depth_weight,
        i["Depth Blend Mode"],
    )

    centered = _math(group, "SUBTRACT", i["System Mask"], 0.5)
    contrasted = _math(group, "MULTIPLY", centered, i["System Mask Contrast"])
    recentered = _math(group, "ADD", contrasted, 0.5)
    biased = _clamp01(group, _math(group, "ADD", recentered, i["System Mask Bias"]))
    inverted = _math(group, "SUBTRACT", one, biased)
    system_mask = _mix_float(group, i["System Mask Invert"], biased, inverted)
    system_color = _blend(
        group,
        "HTUE Select System Color",
        i["System Color 01"],
        i["System Color 02"],
        system_mask,
        0.0,
    )
    color = _blend(
        group,
        "HTUE Stage System",
        color,
        system_color,
        i["System Color Influence"],
        i["System Blend Mode"],
    )

    ao_vertex = _mix_float(group, i["AO Vertex Influence"], one, i["AO Vertex"])
    ao_texture = _mix_float(group, i["AO Texture Influence"], one, orm_r)
    ao_product = _clamp01(group, _math(group, "MULTIPLY", ao_vertex, ao_texture))
    ao = _mix_float(group, i["AO Strength"], one, ao_product)
    ao_gray = _gray(group, ao, "HTUE AO Gray")
    color = _blend(
        group,
        "HTUE Stage AO",
        color,
        ao_gray,
        i["AO Color Influence"],
        i["AO Blend Mode"],
    )

    group.links.new(color, group_out.inputs["Color"])
    group.links.new(ao, group_out.inputs["Ambient Occlusion"])


def _legacy_socket_value(shader, name, fallback):
    socket = shader.inputs.get(name)
    if socket is None:
        return fallback
    value = socket.default_value
    if hasattr(value, "__len__") and not isinstance(value, str):
        return tuple(float(component) for component in value)
    return float(value)


def initialise_settings(material, shader):
    settings = material.htue_settings
    settings.texture_set = schema.TARGET_TEXTURE_SETS.get(material.name, settings.texture_set)
    settings.base_color = _legacy_socket_value(shader, "Base Color", settings.base_color)
    settings.root_color = _legacy_socket_value(shader, "Root Color", settings.root_color)
    settings.root_mix = _legacy_socket_value(shader, "Root Color Mix Factor", settings.root_mix)
    settings.root_range = _legacy_socket_value(shader, "Root Color Range", settings.root_range)
    settings.root_random_influence = _legacy_socket_value(
        shader, "Root Texture Overaly", settings.root_random_influence
    )
    settings.root_random_brightness = _legacy_socket_value(
        shader, "Root  Texture Brightness", settings.root_random_brightness
    )
    settings.tip_color = _legacy_socket_value(shader, "Tip Color", settings.tip_color)
    settings.tip_mix = _legacy_socket_value(shader, "Tip Color Mix Factor", settings.tip_mix)
    settings.tip_range = _legacy_socket_value(shader, "Tip Color Range", settings.tip_range)
    settings.tip_random_influence = _legacy_socket_value(
        shader, "Tip Texture Overlay", settings.tip_random_influence
    )
    settings.tip_random_brightness = _legacy_socket_value(
        shader, "Tip  Texture Brightness", settings.tip_random_brightness
    )
    settings.depth_tint_color = _legacy_socket_value(shader, "Depth Tint", settings.depth_tint_color)
    settings.depth_tint_influence = _legacy_socket_value(
        shader, "Depth Mix Factor", settings.depth_tint_influence
    )
    settings.ao_color_influence = _legacy_socket_value(shader, "AO Mix Factor", 0.0)
    settings.roughness_minimum = _legacy_socket_value(
        shader, "SpecRoughness", settings.roughness_minimum
    )
    system_colors = schema.SYSTEM_COLOR_INITIAL.get(material.name)
    if system_colors:
        settings.system_color_01, settings.system_color_02 = system_colors
    settings.system_color_influence = _legacy_socket_value(
        shader, "Debug  Color  Mix", settings.system_color_influence
    )
    settings.initialized = True


def _save_legacy_state(material, shader):
    if material.get(schema.LEGACY_STATE_PROPERTY):
        return
    socket_names = (
        "Base Color",
        "Root Color Mix Factor",
        "Tip Color Mix Factor",
        "Debug  Color  Mix",
        "Depth Mix Factor",
        "AO Mix Factor",
    )
    state = {"shader_node": shader.name, "sockets": {}}
    for name in socket_names:
        socket = shader.inputs.get(name)
        if socket is None:
            continue
        default = socket.default_value
        if hasattr(default, "__len__") and not isinstance(default, str):
            default = [float(component) for component in default]
        else:
            default = float(default)
        state["sockets"][name] = {
            "default": default,
            "links": [
                {"node": link.from_node.name, "socket": link.from_socket.name}
                for link in socket.links
            ],
        }
    material[schema.LEGACY_STATE_PROPERTY] = json.dumps(state, sort_keys=True)


def _image(texture_root, texture_set, parameter_name):
    suffix = schema.TEXTURE_SUFFIXES[parameter_name]
    path = Path(texture_root) / f"{texture_set}_{suffix}.tga"
    if not path.is_file():
        return None
    image = bpy.data.images.load(str(path), check_existing=True)
    image.colorspace_settings.name = "Non-Color"
    return image


def _top_node(tree, bl_idname, name):
    node = tree.nodes.get(name)
    if node is None or node.bl_idname != bl_idname:
        if node is not None:
            tree.nodes.remove(node)
        node = tree.nodes.new(bl_idname)
        node.name = name
    return node


def _link_unique(tree, output_socket, input_socket):
    for link in list(input_socket.links):
        tree.links.remove(link)
    tree.links.new(output_socket, input_socket)


def setup_material(material):
    shader = find_hair_shader(material)
    if shader is None:
        raise RuntimeError(f"{material.name}: HairShaderMain-compatible node was not found")
    settings = material.htue_settings
    if not settings.initialized:
        initialise_settings(material, shader)
    _save_legacy_state(material, shader)

    group_name = f"{schema.BRIDGE_GROUP_PREFIX}::{material.name}"
    group = bpy.data.node_groups.get(group_name)
    if group is None:
        group = bpy.data.node_groups.new(group_name, "ShaderNodeTree")
    _build_group(group, settings)

    tree = material.node_tree
    bridge = _top_node(tree, "ShaderNodeGroup", schema.BRIDGE_NODE_NAME)
    bridge.node_tree = group
    bridge.label = "Blender / Unreal synchronized color stack"
    bridge.location = (shader.location.x - 420, shader.location.y + 120)

    attribute_specs = {
        "HTUE Random": "Random",
        "HTUE Factor": "Factor",
        "HTUE AO": "AO",
        "HTUE SystemColor": "SystemColor",
        "HTUE Depth": "Depth",
    }
    attributes = {}
    for node_name, attribute_name in attribute_specs.items():
        node = _top_node(tree, "ShaderNodeAttribute", node_name)
        node.attribute_name = attribute_name
        attributes[node_name] = node

    uv = _top_node(tree, "ShaderNodeTexCoord", "HTUE UV")
    ird = _top_node(tree, "ShaderNodeTexImage", "HTUE IRD Map")
    orm = _top_node(tree, "ShaderNodeTexImage", "HTUE ORM Map")
    ird.image = _image(settings.texture_root, settings.texture_set, "IRD Map")
    orm.image = _image(settings.texture_root, settings.texture_set, "ORM Map")
    _link_unique(tree, uv.outputs["UV"], ird.inputs["Vector"])
    _link_unique(tree, uv.outputs["UV"], orm.inputs["Vector"])

    _link_unique(tree, attributes["HTUE Random"].outputs["Factor"], bridge.inputs["Random"])
    _link_unique(tree, attributes["HTUE Factor"].outputs["Factor"], bridge.inputs["Factor"])
    _link_unique(tree, attributes["HTUE AO"].outputs["Factor"], bridge.inputs["AO Vertex"])
    _link_unique(
        tree, attributes["HTUE SystemColor"].outputs["Alpha"], bridge.inputs["System Mask"]
    )
    _link_unique(tree, attributes["HTUE Depth"].outputs["Factor"], bridge.inputs["Depth Vertex"])
    _link_unique(tree, ird.outputs["Color"], bridge.inputs["IRD Map"])
    _link_unique(tree, orm.outputs["Color"], bridge.inputs["ORM Map"])
    _link_unique(tree, bridge.outputs["Color"], shader.inputs["Base Color"])

    for name in (
        "Root Color Mix Factor",
        "Tip Color Mix Factor",
        "Debug  Color  Mix",
        "Depth Mix Factor",
        "AO Mix Factor",
    ):
        socket = shader.inputs.get(name)
        if socket is not None:
            socket.default_value = 0.0

    sync_material(material)
    contract.persist_material_contract(material)
    return bridge


def sync_material(material):
    settings = material.htue_settings
    bridge = material.node_tree.nodes.get(schema.BRIDGE_NODE_NAME) if material.node_tree else None
    if bridge is None or bridge.node_tree is None:
        return
    for field, name in schema.VECTOR_FIELDS.items():
        socket = bridge.inputs.get(name)
        if socket is not None:
            socket.default_value = tuple(getattr(settings, field))
    for field, name in schema.SCALAR_FIELDS.items():
        socket = bridge.inputs.get(name)
        if socket is not None:
            socket.default_value = (
                schema.BLEND_MODE_VALUES[getattr(settings, field)]
                if field in schema.BLEND_FIELDS
                else float(getattr(settings, field))
            )


def restore_material(material):
    tree = material.node_tree
    if tree is None:
        return
    raw_state = material.get(schema.LEGACY_STATE_PROPERTY)
    state = json.loads(str(raw_state)) if raw_state else None
    bridge = tree.nodes.get(schema.BRIDGE_NODE_NAME)
    group = bridge.node_tree if bridge and bridge.node_tree else None
    if state:
        shader = tree.nodes.get(state.get("shader_node")) or find_hair_shader(material)
        if shader:
            for name, socket_state in (state.get("sockets") or {}).items():
                socket = shader.inputs.get(name)
                if socket is None:
                    continue
                for link in list(socket.links):
                    tree.links.remove(link)
                socket.default_value = socket_state.get("default")
                for link_state in socket_state.get("links") or []:
                    source_node = tree.nodes.get(link_state.get("node"))
                    source_socket = source_node.outputs.get(link_state.get("socket")) if source_node else None
                    if source_socket:
                        tree.links.new(source_socket, socket)
    for name in MANAGED_TOP_LEVEL_NODES:
        node = tree.nodes.get(name)
        if node is not None:
            tree.nodes.remove(node)
    if group is not None and group.users == 0:
        bpy.data.node_groups.remove(group)
    material.htue_settings.initialized = False
    for key in (schema.CONTRACT_PROPERTY, schema.LEGACY_STATE_PROPERTY):
        if key in material:
            del material[key]
