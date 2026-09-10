"""Keep hidden Hair Tool guides and their editable output selection consistent."""

import bpy
from bpy.app.handlers import persistent


_MSGBUS_OWNER = object()
_CHANGING_SELECTION = False


def _shared_module():
    try:
        from hair_tool.hair_baking import hair_geometry_nodes_shared
    except ImportError:
        return None
    return hair_geometry_nodes_shared


def output_for_hidden_guide(context, source=None):
    """Resolve only one visible output that directly references a hidden guide.

    Visible guides remain editable. Multiple dependants require the user's
    explicit choice in Hair Object Users; names and hierarchy alone are not
    sufficient evidence to redirect a selection.
    """
    # Blender can expose a hidden Outliner active object only through the
    # View Layer, while context.object and context.active_object are None.
    source = source or context.active_object
    if source is None and context.view_layer is not None:
        source = context.view_layer.objects.active
    shared = _shared_module()
    if (
        source is None
        or source.type not in {"MESH", "CURVES"}
        or shared is None
        or context.view_layer is None
        or source.mode != "OBJECT"
        or source.visible_get(view_layer=context.view_layer)
        or shared.get_hair_sys_mod_list(source)
    ):
        return None

    candidates = []
    for obj in context.view_layer.objects:
        if (
            obj == source
            or obj.type not in {"MESH", "CURVES"}
            or not obj.visible_get(view_layer=context.view_layer)
            or not shared.get_hair_sys_mod_list(obj)
        ):
            continue
        setup = shared.get_hair_setup_mod(obj)
        if setup is None:
            continue
        for identifier in (shared.guide_mod_surface_input, shared.guide_mod_curve_input):
            try:
                referenced = setup.properties.inputs[identifier]["value"]
            except (AttributeError, KeyError, TypeError):
                try:
                    referenced = setup.get(identifier)
                except (AttributeError, TypeError):
                    referenced = None
            if referenced == source:
                candidates.append(obj)
                break
    return candidates[0] if len(candidates) == 1 else None


def synchronize_hidden_guide_selection(context=None):
    """Switch the actual active object, so all native operators share a target."""
    global _CHANGING_SELECTION
    context = context or bpy.context
    if _CHANGING_SELECTION or context.view_layer is None or len(context.selected_objects) > 1:
        return None
    # Include hidden selections, which context.selected_objects can omit.
    selected = [obj for obj in context.view_layer.objects if obj.select_get(view_layer=context.view_layer)]
    if len(selected) > 1:
        return None
    source = context.active_object or context.view_layer.objects.active
    target = output_for_hidden_guide(context, source)
    if target is None:
        return None
    _CHANGING_SELECTION = True
    try:
        target.select_set(True, view_layer=context.view_layer)
        context.view_layer.objects.active = target
        source.select_set(False, view_layer=context.view_layer)
        target.ht_props.surface_props.active_obj_index = context.scene.objects.find(target.name)
        _shared_module().clear_hair_object_cache()
        for area in context.screen.areas if context.screen else ():
            area.tag_redraw()
    finally:
        _CHANGING_SELECTION = False
    return target


def _selection_changed():
    synchronize_hidden_guide_selection()


def _subscribe():
    bpy.msgbus.clear_by_owner(_MSGBUS_OWNER)
    bpy.msgbus.subscribe_rna(
        key=(bpy.types.LayerObjects, "active"),
        owner=_MSGBUS_OWNER,
        args=(),
        notify=_selection_changed,
        options={"PERSISTENT"},
    )


@persistent
def _on_load(_unused):
    _subscribe()


def install_runtime_integration():
    if _shared_module() is None:
        return False
    _subscribe()
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    return True


def remove_runtime_integration():
    bpy.msgbus.clear_by_owner(_MSGBUS_OWNER)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
