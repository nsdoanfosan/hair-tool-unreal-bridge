"""Run in factory-startup background Blender; never save preferences."""
import sys
from pathlib import Path

import bpy
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
from hair_tool_unreal_bridge import uv_compat as uv

assert bpy.app.background


def fixture(name, reads, layout):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    owner_mesh = bpy.data.meshes.new(name + " UV")
    owner_mesh.vertices.add(1)
    owner = bpy.data.objects.new(name + " UV", owner_mesh)
    for attr_name, values in layout.items():
        attr = owner_mesh.attributes.new(attr_name, uv.ATTRS[attr_name], "POINT")
        if attr_name == "UV_Matrix":
            attr.data[0].value = values
        else:
            attr.data[0].vector = values
    tree = bpy.data.node_groups.new(name + " Profile", "GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    socket = tree.interface.new_socket(name="UV Owner", in_out="INPUT", socket_type="NodeSocketObject")
    identifier = socket.identifier
    group_input = tree.nodes.new("NodeGroupInput")
    group_output = tree.nodes.new("NodeGroupOutput")
    info = tree.nodes.new("GeometryNodeObjectInfo")
    tree.links.new(group_input.outputs["UV Owner"], info.inputs["Object"])
    # Real nested reader evaluates coordinates from the unlinked UV owner.
    nested = bpy.data.node_groups.new(name + " Sample", "GeometryNodeTree")
    nested.interface.new_socket(name="Vector", in_out="OUTPUT", socket_type="NodeSocketVector")
    nested_out = nested.nodes.new("NodeGroupOutput")
    for name_read in reads:
        reader = nested.nodes.new("GeometryNodeInputNamedAttribute")
        reader.data_type = "FLOAT_VECTOR" if name_read != "UV_Matrix" else "FLOAT4X4"
        reader.inputs["Name"].default_value = name_read
        if name_read == "UV_Start":
            nested.links.new(reader.outputs["Attribute"], nested_out.inputs["Vector"])
    group = tree.nodes.new("GeometryNodeGroup")
    group.node_tree = nested
    sample = tree.nodes.new("GeometryNodeSampleIndex")
    sample.data_type = "FLOAT_VECTOR"
    sample.domain = "POINT"
    tree.links.new(info.outputs["Geometry"], sample.inputs["Geometry"])
    tree.links.new(group.outputs["Vector"], sample.inputs["Value"])
    store = tree.nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = "FLOAT_VECTOR"
    store.domain = "POINT"
    store.inputs["Name"].default_value = "UVMapGN"
    tree.links.new(sample.outputs["Value"], store.inputs["Value"])
    tree.links.new(group_input.outputs["Geometry"], store.inputs["Geometry"])
    tree.links.new(store.outputs["Geometry"], group_output.inputs["Geometry"])
    mod = obj.modifiers.new("Profile", "NODES")
    mod.node_group = tree
    uv.set_input_value(mod, identifier, owner)
    return obj, mod, identifier, owner


def evaluated_uv(obj):
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return tuple(evaluated.data.attributes["UVMapGN"].data[0].vector)


layout = uv._matrix((.2, .8), (.4, .1))
obj, mod, ident, original = fixture("Legacy reader", ["UV_Start", "UV_End"], {"UV_Matrix": layout})
assert uv.audit([obj])["repairable_profiles"] == 1
assert evaluated_uv(obj) == (0, 0, 0)
original_data = original.data
node_pointer = mod.node_group.as_pointer()
second, second_mod, second_id, unused = fixture("Shared", ["UV_Start"], {"UV_Matrix": layout})
uv.set_input_value(second_mod, second_id, original)
assert uv.repair_profile(obj, mod, ident)["status"] == "repaired"
assert abs(evaluated_uv(obj)[0] - .2) < 1e-6
assert abs(evaluated_uv(obj)[1] - .8) < 1e-6
assert uv.input_value(mod, ident) != original
assert uv.input_value(second_mod, second_id) == original
assert original.data == original_data and original.data.attributes.get("UV_Start") is None
assert mod.node_group.as_pointer() == node_pointer
assert uv.input_value(mod, ident)["htue_uv_original"] == original
counts = (len(bpy.data.objects), len(bpy.data.meshes))
assert uv.repair_profile(obj, mod, ident)["status"] == "skipped"
assert counts == (len(bpy.data.objects), len(bpy.data.meshes))

# Reverse migration, mismatched formats, unsupported transforms and invalid data.
modern = fixture("Modern", ["UV_Matrix"], {"UV_Start": (.2, .8), "UV_End": (.4, .1)})
assert uv.repair_profile(*modern[:3])["status"] == "repaired"
assert uv._close(uv.input_value(modern[1], modern[2]).data.attributes["UV_Matrix"].data[0].value, layout)
conflict = fixture("Conflict", ["UV_Start", "UV_End"], {"UV_Matrix": layout, "UV_Start": (.9, .9)})
assert not uv.inspect_profile(*conflict[:3])["repairable"]
rotation = Matrix.Rotation(.3, 4, "Z")
rotated = fixture("Rotation", ["UV_Start"], {"UV_Matrix": rotation})
assert not uv.inspect_profile(*rotated[:3])["repairable"]
modern_rotated = fixture("Modern rotation", ["UV_Matrix"], {"UV_Matrix": rotation})
assert not uv.inspect_profile(*modern_rotated[:3])["issues"]
empty = fixture("Missing owner", ["UV_Start"], {})
uv.set_input_value(empty[1], empty[2], None)
assert uv.inspect_profile(*empty[:3])["issues"][0]["code"] == "UV_OWNER_MISSING"
invalid = fixture("Wrong type", ["UV_Start"], {})
invalid[3].data.attributes.new("UV_Matrix", "FLOAT", "POINT")
assert uv.inspect_profile(*invalid[:3])["issues"][0]["code"] == "INVALID_ATTRIBUTE"

# Failure after binding restores the source and cleans candidate datablocks.
before_counts = (len(bpy.data.objects), len(bpy.data.meshes))
real_inspect = uv.inspect_profile
def fail_after_bind(target, modifier, identifier):
    if uv.input_value(modifier, identifier) != original:
        raise RuntimeError("injected validation failure")
    return real_inspect(target, modifier, identifier)
uv.inspect_profile = fail_after_bind
try:
    try:
        uv.repair_profile(second, second_mod, second_id)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected rollback")
finally:
    uv.inspect_profile = real_inspect
assert uv.input_value(second_mod, second_id) == original
assert before_counts == (len(bpy.data.objects), len(bpy.data.meshes))

for cls in uv.CLASSES:
    bpy.utils.register_class(cls)
assert bpy.ops.htue.audit_hair_uv() == {"FINISHED"}
assert bpy.data.texts.get(uv.REPORT_NAME)
for cls in reversed(uv.CLASSES):
    bpy.utils.unregister_class(cls)

# Verify the complete package registration contract without saving preferences.
import addon_utils
assert addon_utils.enable("hair_tool_unreal_bridge", default_set=False) is not None
assert bpy.types.HTUE_PT_hair_uv_compat
addon_utils.disable("hair_tool_unreal_bridge", default_set=False)
print("UV_COMPAT_REGRESSIONS_PASS")
