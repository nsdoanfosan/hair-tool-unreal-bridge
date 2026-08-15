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


def _new_socket(
    tree,
    name,
    in_out,
    socket_type,
    default=None,
    min_value=None,
    max_value=None,
    *,
    hide_value=False,
):
    socket = tree.interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)
    if default is not None and hasattr(socket, "default_value"):
        socket.default_value = default
    if min_value is not None and hasattr(socket, "min_value"):
        socket.min_value = min_value
    if max_value is not None and hasattr(socket, "max_value"):
        socket.max_value = max_value
    if hasattr(socket, "hide_value"):
        socket.hide_value = hide_value
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
    for name in (
        "Random",
        "Factor",
        "AO Vertex",
        "AO Vertex Available",
        "Depth Vertex",
        "System Attribute Available",
    ):
        _new_socket(group, name, "INPUT", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _new_socket(
        group,
        "System Attribute Color",
        "INPUT",
        "NodeSocketColor",
        (0.0, 0.0, 0.0, 1.0),
    )
    _new_socket(group, "IRD Map", "INPUT", "NodeSocketColor", (0.0, 0.0, 0.0, 1.0))
    _new_socket(group, "ORM Map", "INPUT", "NodeSocketColor", (1.0, 1.0, 0.0, 1.0))
    for field, name in schema.VECTOR_FIELDS.items():
        _new_socket(
            group,
            name,
            "INPUT",
            "NodeSocketColor",
            tuple(getattr(settings, field)),
            hide_value=True,
        )
    for field, name in schema.SCALAR_FIELDS.items():
        value = (
            schema.BLEND_MODE_VALUES[getattr(settings, field)]
            if field in schema.BLEND_FIELDS
            else float(getattr(settings, field))
        )
        _new_socket(group, name, "INPUT", "NodeSocketFloat", value, hide_value=True)
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

    # The bridge owns the color stack. Hair Tool contributes evaluated
    # deformer attributes only; its legacy HairShaderMain blend result is not
    # chained into this output. The order is deliberately identical to the
    # Unreal master: Base -> System -> Root -> Tip -> ID -> Depth -> AO.
    color = i["HT Base Color"]
    system_influence = _math(
        group,
        "MULTIPLY",
        i["System Color Influence"],
        i["System Attribute Available"],
        name="HTUE System Effective Influence",
    )
    color = _blend(
        group,
        "HTUE Stage System",
        color,
        i["System Attribute Color"],
        system_influence,
        i["System Blend Mode"],
    )

    # HairShaderMain's Map Range uses safe division. With From Min and From
    # Max both zero, Root Range=0 resolves to To Min (1), not to a disabled
    # mask. Preserve that behavior so Set Factor continues to match Hair Tool.
    root_delta = _math(group, "SUBTRACT", i["HT Root Range"], i["Factor"])
    root_safe = _math(group, "MAXIMUM", i["HT Root Range"], eps)
    root_ranged_mask = _clamp01(group, _math(group, "DIVIDE", root_delta, root_safe))
    root_range_enabled = _math(
        group,
        "GREATER_THAN",
        i["HT Root Range"],
        0.0,
        name="HTUE Root Range Enabled",
    )
    root_mask = _mix_float(
        group,
        root_range_enabled,
        one,
        root_ranged_mask,
        name="HTUE Root Range Zero Is Full",
    )
    root_random_value = _math(group, "ADD", i["Random"], i["HT Root Random Brightness"])
    root_random = _mix_float(group, i["HT Root Random Influence"], one, root_random_value)
    root_texture = _mix_float(group, i["Root Map Influence"], one, ird_g)
    root_weight = _math(group, "MULTIPLY", root_mask, i["HT Root Mix"])
    root_weight = _math(group, "MULTIPLY", root_weight, root_random)
    root_weight = _clamp01(group, _math(group, "MULTIPLY", root_weight, root_texture))
    color = _blend(
        group,
        "HTUE Stage Root",
        color,
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

    ao_vertex_influence = _math(
        group, "MULTIPLY", i["AO Vertex Influence"], i["AO Vertex Available"]
    )
    ao_vertex = _mix_float(group, ao_vertex_influence, one, i["AO Vertex"])
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
    settings.roughness_minimum = _legacy_socket_value(
        shader, "SpecRoughness", settings.roughness_minimum
    )
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


def _socket_value(socket):
    value = socket.default_value
    if hasattr(value, "__len__") and not isinstance(value, str):
        return tuple(float(component) for component in value)
    return float(value)


def _capture_shader_inputs(material, tree, shader):
    """Capture every Hair Tool group input while excluding obsolete HTUE links."""
    result = {}
    managed = set(MANAGED_TOP_LEVEL_NODES)
    raw_augmented = material.get(schema.AUGMENTED_LINKS_PROPERTY)
    augmented = {
        (item.get("input"), item.get("node"), item.get("output"))
        for item in (json.loads(str(raw_augmented)) if raw_augmented else [])
    }
    for socket in shader.inputs:
        result[socket.name] = {
            "default": _socket_value(socket),
            "links": [
                (link.from_node.name, link.from_socket.name)
                for link in socket.links
                if link.from_node.name not in managed
                and (socket.name, link.from_node.name, link.from_socket.name) not in augmented
            ],
        }
    return result


def _restore_shader_inputs(tree, shader, state):
    for socket_name, socket_state in state.items():
        socket = shader.inputs.get(socket_name)
        if socket is None:
            continue
        for link in list(socket.links):
            tree.links.remove(link)
        socket.default_value = socket_state["default"]
        for node_name, output_name in socket_state["links"]:
            node = tree.nodes.get(node_name)
            output = node.outputs.get(output_name) if node else None
            if output is not None:
                tree.links.new(output, socket)


def _group_input_output(group, socket_name):
    candidates = []
    for node in group.nodes:
        if node.bl_idname != "NodeGroupInput":
            continue
        socket = node.outputs.get(socket_name)
        if socket is not None:
            candidates.append(socket)
    if not candidates:
        raise RuntimeError(f"HairShaderMain input was not found: {socket_name}")
    return next((socket for socket in candidates if socket.is_linked), candidates[0])


def _ensure_clone_input(group, name, socket_type):
    for item in group.interface.items_tree:
        if (
            getattr(item, "item_type", None) == "SOCKET"
            and getattr(item, "in_out", None) == "INPUT"
            and item.name == name
        ):
            item.hide_value = True
            return item
    return _new_socket(group, name, "INPUT", socket_type, hide_value=True)


def _install_stack_in_clone(clone, stack_group):
    """Replace only the legacy color result inside a per-material group copy."""
    for name, socket_type in (
        ("HTUE IRD Map", "NodeSocketColor"),
        ("HTUE ORM Map", "NodeSocketColor"),
    ):
        _ensure_clone_input(clone, name, socket_type)

    stack = clone.nodes.get(schema.INTERNAL_STACK_NODE_NAME)
    if stack is None or stack.bl_idname != "ShaderNodeGroup":
        if stack is not None:
            clone.nodes.remove(stack)
        stack = clone.nodes.new("ShaderNodeGroup")
        stack.name = schema.INTERNAL_STACK_NODE_NAME
    stack.label = "HTUE synchronized color stack (legacy blend result replacement)"
    stack.node_tree = stack_group

    deformer_links = {
        "Random Id [Map]": "Random",
        "Factor [Map]": "Factor",
        "Depth [Map]": "Depth Vertex",
        "Vert Color AO [Map]": "AO Vertex",
        "Debug Color": "System Attribute Color",
    }
    for source_name, target_name in deformer_links.items():
        _link_unique(clone, _group_input_output(clone, source_name), stack.inputs[target_name])
    for source_name, target_name in (
        ("HTUE IRD Map", "IRD Map"),
        ("HTUE ORM Map", "ORM Map"),
    ):
        _link_unique(clone, _group_input_output(clone, source_name), stack.inputs[target_name])

    color_target = next(
        (
            node
            for node in clone.nodes
            if node.bl_idname == "NodeReroute" and node.label == "Color"
        ),
        None,
    )
    if color_target is None:
        raise RuntimeError("HairShaderMain final Color reroute was not found")
    _link_unique(clone, stack.outputs["Color"], color_target.inputs[0])
    stack["htue_replaces_legacy_color_blends"] = True
    return stack


def _find_attribute_node(tree, attribute_name):
    return next(
        (
            node
            for node in tree.nodes
            if node.bl_idname == "ShaderNodeAttribute"
            and node.attribute_name == attribute_name
            and not node.name.startswith("HTUE ")
        ),
        None,
    )


def _ensure_hair_attribute_links(material, shader):
    """Fill only missing Hair Tool attribute inputs; never replace existing links."""
    tree = material.node_tree
    raw_augmented = material.get(schema.AUGMENTED_LINKS_PROPERTY)
    augmented = list(json.loads(str(raw_augmented)) if raw_augmented else [])
    specs = (
        ("Factor [Map]", "Factor", "Factor", "HTUE Factor"),
        ("Random Id [Map]", "Random", "Color", "HTUE Random"),
        ("Debug Color", "SystemColor", "Color", "HTUE SystemColor"),
        ("Vert Color AO [Map]", "AO", "Color", "HTUE AO"),
        ("Depth [Map]", "Depth", "Factor", "HTUE Depth"),
    )
    nodes = {}
    for input_name, attribute_name, output_name, fallback_name in specs:
        socket = shader.inputs.get(input_name)
        if socket is None or socket.is_linked:
            continue
        node = _find_attribute_node(tree, attribute_name)
        if node is None:
            node = _top_node(tree, "ShaderNodeAttribute", fallback_name)
            node.attribute_name = attribute_name
        output = node.outputs.get(output_name)
        if output is None:
            continue
        tree.links.new(output, socket)
        record = {"input": input_name, "node": node.name, "output": output_name}
        if record not in augmented:
            augmented.append(record)
        nodes[attribute_name] = node
    material[schema.AUGMENTED_LINKS_PROPERTY] = json.dumps(augmented, sort_keys=True)
    return nodes


def _remove_obsolete_top_level_nodes(tree):
    groups = []
    for name in MANAGED_TOP_LEVEL_NODES:
        node = tree.nodes.get(name)
        if node is None:
            continue
        if getattr(node, "node_tree", None) is not None:
            groups.append(node.node_tree)
        tree.nodes.remove(node)
    for group in groups:
        if group.users == 0:
            bpy.data.node_groups.remove(group)


def setup_material(material):
    shader = find_hair_shader(material)
    if shader is None:
        raise RuntimeError(f"{material.name}: HairShaderMain-compatible node was not found")
    settings = material.htue_settings
    if not settings.initialized:
        initialise_settings(material, shader)
    _save_legacy_state(material, shader)
    from . import deformer_sync

    ao_vertex_available = deformer_sync.has_evaluated_source_attribute(material, "AO")
    system_attribute_available = deformer_sync.has_evaluated_source_attribute(
        material, "SystemColor"
    )

    tree = material.node_tree
    input_state = _capture_shader_inputs(material, tree, shader)

    original_name = str(material.get(schema.ORIGINAL_SHADER_GROUP_PROPERTY) or "")
    original_group = bpy.data.node_groups.get(original_name) if original_name else None
    if original_group is None or original_group.name.startswith(schema.SHADER_CLONE_PREFIX):
        if not shader.node_tree.name.startswith(schema.SHADER_CLONE_PREFIX):
            original_group = shader.node_tree
            material[schema.ORIGINAL_SHADER_GROUP_PROPERTY] = original_group.name
        else:
            raise RuntimeError(f"{material.name}: original HairShaderMain group is unavailable")

    old_clone = shader.node_tree if shader.node_tree.name.startswith(schema.SHADER_CLONE_PREFIX) else None
    shader.node_tree = original_group
    if old_clone is not None and old_clone.users == 0:
        bpy.data.node_groups.remove(old_clone)
    _remove_obsolete_top_level_nodes(tree)

    stack_name = f"{schema.BRIDGE_GROUP_PREFIX}::{material.name}"
    old_stack = bpy.data.node_groups.get(stack_name)
    if old_stack is not None and old_stack.users == 0:
        bpy.data.node_groups.remove(old_stack)
    stack_group = bpy.data.node_groups.new(stack_name, "ShaderNodeTree")
    _build_group(stack_group, settings)

    clone = original_group.copy()
    clone.name = f"{schema.SHADER_CLONE_PREFIX}::{material.name}"
    stack = _install_stack_in_clone(clone, stack_group)
    stack.inputs["AO Vertex Available"].default_value = float(ao_vertex_available)
    stack.inputs["System Attribute Available"].default_value = float(
        system_attribute_available
    )
    shader.node_tree = clone
    _restore_shader_inputs(tree, shader, input_state)

    uv = _top_node(tree, "ShaderNodeTexCoord", "HTUE UV")
    ird = _top_node(tree, "ShaderNodeTexImage", "HTUE IRD Map")
    orm = _top_node(tree, "ShaderNodeTexImage", "HTUE ORM Map")
    ird.image = _image(settings.texture_root, settings.texture_set, "IRD Map")
    orm.image = _image(settings.texture_root, settings.texture_set, "ORM Map")
    _link_unique(tree, uv.outputs["UV"], ird.inputs["Vector"])
    _link_unique(tree, uv.outputs["UV"], orm.inputs["Vector"])
    _ensure_hair_attribute_links(material, shader)
    _link_unique(tree, ird.outputs["Color"], shader.inputs["HTUE IRD Map"])
    _link_unique(tree, orm.outputs["Color"], shader.inputs["HTUE ORM Map"])

    sync_material(material)
    contract.persist_material_contract(material)
    return stack


def sync_material(material):
    settings = material.htue_settings
    shader = find_hair_shader(material)
    if shader is None or shader.node_tree is None:
        return
    stack = shader.node_tree.nodes.get(schema.INTERNAL_STACK_NODE_NAME)
    if stack is None or stack.node_tree is None:
        return
    for field, name in schema.VECTOR_FIELDS.items():
        socket = stack.inputs.get(name)
        if socket is not None:
            socket.default_value = tuple(getattr(settings, field))
    for field, name in schema.SCALAR_FIELDS.items():
        socket = stack.inputs.get(name)
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
    shader = tree.nodes.get(state.get("shader_node")) if state else find_hair_shader(material)
    clone = shader.node_tree if shader and shader.node_tree.name.startswith(schema.SHADER_CLONE_PREFIX) else None
    stack_group = None
    if clone is not None:
        stack = clone.nodes.get(schema.INTERNAL_STACK_NODE_NAME)
        stack_group = stack.node_tree if stack and stack.node_tree else None
    original_name = str(material.get(schema.ORIGINAL_SHADER_GROUP_PROPERTY) or "")
    original_group = bpy.data.node_groups.get(original_name)
    if shader and original_group:
        shader.node_tree = original_group
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
    raw_augmented = material.get(schema.AUGMENTED_LINKS_PROPERTY)
    for link_state in json.loads(str(raw_augmented)) if raw_augmented else []:
        socket = shader.inputs.get(link_state.get("input")) if shader else None
        if socket is None:
            continue
        for link in list(socket.links):
            if (
                link.from_node.name == link_state.get("node")
                and link.from_socket.name == link_state.get("output")
            ):
                tree.links.remove(link)
    for name in MANAGED_TOP_LEVEL_NODES:
        node = tree.nodes.get(name)
        if node is not None:
            tree.nodes.remove(node)
    for group in (clone, stack_group):
        if group is not None and group.users == 0:
            bpy.data.node_groups.remove(group)
    material.htue_settings.initialized = False
    for key in (
        schema.CONTRACT_PROPERTY,
        schema.LEGACY_STATE_PROPERTY,
        schema.ORIGINAL_SHADER_GROUP_PROPERTY,
        schema.AUGMENTED_LINKS_PROPERTY,
    ):
        if key in material:
            del material[key]
