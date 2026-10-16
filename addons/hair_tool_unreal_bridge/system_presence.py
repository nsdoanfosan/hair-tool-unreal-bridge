"""Keep SystemColor existence per geometry component through Hair Tool joins.

A named attribute on curves becomes zero-filled on an uncolored source mesh
when Profile joins them. Object-wide availability cannot distinguish that
missing value from authored black. Stamp existence before that join instead.
The existing Send2UE v3 missing-color sentinel carries the same information.
"""
import bpy

ATTRIBUTE = '_htue_system_color_present'
ENCODED_PROPERTY = '_htue_system_presence_encoded'
PREFIX = 'HTUE System Presence '


def ensure_surface_color(group):
    """Inherit the authored strand color on an otherwise uncolored guide mesh.

    Keep the mesh sent to Capture Curve Attributes unchanged: sampling the
    generated cards back onto that input would introduce a dependency cycle.
    Only the independent mesh branch entering the final Join is augmented.
    """
    name = 'HTUE Source Mesh SystemColor'
    if group.nodes.get(name):
        return False
    mesh = group.nodes.get(PREFIX + 'Mesh')
    cards = group.nodes.get('Set Material')
    join = group.nodes.get('Join Geometry')
    if not mesh or not cards or not join:
        return False
    links = [l for l in mesh.outputs['Geometry'].links if l.to_node == join]
    if len(links) != 1:
        return False
    target = links[0].to_socket
    attr = group.nodes[ PREFIX + 'Mesh Source']
    position = group.nodes.new('GeometryNodeInputPosition')
    position.name = name + ' Position'
    sample = group.nodes.new('GeometryNodeSampleNearestSurface')
    sample.data_type = 'FLOAT_COLOR'
    sample.name = name + ' Sample'
    source_exists = group.nodes.new('GeometryNodeSampleNearestSurface')
    source_exists.data_type = 'FLOAT'
    source_exists.name = name + ' Available'
    for node in (sample, source_exists):
        group.links.new(cards.outputs['Geometry'], node.inputs['Mesh'])
        group.links.new(position.outputs[0], node.inputs['Sample Position'])
    group.links.new(attr.outputs['Attribute'], sample.inputs['Value'])
    group.links.new(attr.outputs['Exists'], source_exists.inputs['Value'])
    absent = group.nodes.new('FunctionNodeBooleanMath')
    absent.operation = 'NOT'
    group.links.new(attr.outputs['Exists'], absent.inputs[0])
    valid = group.nodes.new('FunctionNodeBooleanMath')
    valid.operation = 'AND'
    group.links.new(absent.outputs[0], valid.inputs[0])
    group.links.new(source_exists.outputs['Value'], valid.inputs[1])
    selected = group.nodes.new('FunctionNodeBooleanMath')
    selected.operation = 'AND'
    group.links.new(valid.outputs[0], selected.inputs[0])
    group.links.new(sample.outputs['Is Valid'], selected.inputs[1])
    original_presence = group.nodes.new('GeometryNodeInputNamedAttribute')
    original_presence.data_type = 'FLOAT'
    original_presence.inputs['Name'].default_value = ATTRIBUTE
    present = group.nodes.new('ShaderNodeMath')
    present.operation = 'MAXIMUM'
    group.links.new(original_presence.outputs['Attribute'], present.inputs[0])
    group.links.new(selected.outputs[0], present.inputs[1])
    stamp = group.nodes.new('GeometryNodeStoreNamedAttribute')
    stamp.name = name + ' Presence'
    stamp.data_type = 'FLOAT'
    stamp.domain = 'POINT'
    stamp.inputs['Name'].default_value = ATTRIBUTE
    group.links.new(mesh.outputs['Geometry'], stamp.inputs['Geometry'])
    group.links.new(present.outputs[0], stamp.inputs['Value'])
    color = group.nodes.new('GeometryNodeStoreNamedAttribute')
    color.name = name
    color.label = 'Uncolored source mesh inherits authored strand color'
    color.data_type = 'FLOAT_COLOR'
    color.domain = 'POINT'
    color.inputs['Name'].default_value = 'SystemColor'
    group.links.new(stamp.outputs['Geometry'], color.inputs['Geometry'])
    group.links.new(selected.outputs[0], color.inputs['Selection'])
    group.links.new(sample.outputs['Value'], color.inputs['Value'])
    group.links.remove(links[0])
    group.links.new(color.outputs['Geometry'], target)
    return True


def ensure_object(obj):
    encoded = bool(getattr(getattr(obj, 'data', None), 'attributes', None)
                   and obj.data.attributes.get(ATTRIBUTE) is not None)
    changed = False
    for modifier in obj.modifiers:
        if modifier.type != 'NODES' or not modifier.show_viewport:
            continue
        group = modifier.node_group
        if group is None or group.library or not group.name.startswith('Hair_System_Profile'):
            continue
        separate = group.nodes.get('Separate Components')
        capture = group.nodes.get('Capture Curve Attributes')
        if separate is None or capture is None:
            continue
        for component in ('Mesh', 'Curve'):
            source = separate.outputs.get(component)
            if source is None:
                continue
            name = PREFIX + component
            store = group.nodes.get(name)
            if store is None:
                destinations = [link.to_socket for link in source.links]
                if not destinations:
                    continue
                attr = group.nodes.new('GeometryNodeInputNamedAttribute')
                attr.data_type = 'FLOAT_COLOR'
                attr.name = name + ' Source'
                attr.inputs['Name'].default_value = 'SystemColor'
                store = group.nodes.new('GeometryNodeStoreNamedAttribute')
                store.name = name
                store.label = 'Preserve SystemColor existence before joining'
                store.data_type = 'FLOAT'
                store.domain = 'POINT'
                store.inputs['Name'].default_value = ATTRIBUTE
                # Join Geometry accepts multiple links. Replacing its source
                # requires removing the old link, otherwise meshes duplicate.
                for link in tuple(source.links):
                    group.links.remove(link)
                group.links.new(source, store.inputs['Geometry'])
                group.links.new(attr.outputs['Exists'], store.inputs['Value'])
                for target in destinations:
                    group.links.new(store.outputs['Geometry'], target)
                changed = True
            encoded = True
        if obj.type == 'MESH':
            changed = ensure_surface_color(group) or changed
    value = float(encoded)
    if obj.get(ENCODED_PROPERTY) != value:
        obj[ENCODED_PROPERTY] = value
        changed = True
    if changed:
        obj.update_tag(refresh={'DATA', 'OBJECT'})
    return changed


def link_gate(material, object_gate, target_socket, top_node, link_unique):
    tree = material.node_tree
    marker = top_node(tree, 'ShaderNodeAttribute', 'HTUE System Component Presence')
    marker.attribute_type = 'GEOMETRY'
    marker.attribute_name = ATTRIBUTE
    encoded = top_node(tree, 'ShaderNodeAttribute', 'HTUE System Presence Encoded')
    encoded.attribute_type = 'OBJECT'
    encoded.attribute_name = ENCODED_PROPERTY
    mix = top_node(tree, 'ShaderNodeMixRGB', 'HTUE System Presence Gate')
    mix.blend_type = 'MIX'
    link_unique(tree, encoded.outputs['Fac'], mix.inputs[0])
    link_unique(tree, object_gate, mix.inputs[1])
    link_unique(tree, marker.outputs['Fac'], mix.inputs[2])
    link_unique(tree, mix.outputs['Color'], target_socket)
