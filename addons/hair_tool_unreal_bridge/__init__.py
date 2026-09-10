bl_info = {
    "name": "Unreal Material Bridge",
    "author": "PARK / OpenAI Codex",
    "version": (0, 9, 5),
    "blender": (5, 1, 0),
    "location": "3D View > Unreal Bridge; Material Properties > Unreal Material Bridge",
    "description": "Synchronize Hair Tool materials and preview M_LayerBlend height from Unreal",
    "category": "Material",
}

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, PointerProperty

from . import export_masks, hair_system_compat, layerblend_preview, material_compat, operators, profile_sync, properties, ui, uv_compat


CLASSES = (
    properties.CLASSES
    + operators.CLASSES
    + export_masks.OPERATOR_CLASSES
    + export_masks.MENU_CLASSES
    + ui.CLASSES
    + export_masks.PANEL_CLASSES
    + layerblend_preview.CLASSES
    + uv_compat.CLASSES
)


def initialize_export_ao_after_register():
    """Run after Blender releases the restricted registration data context."""
    from . import deformer_sync

    material_compat.install_runtime_integration()
    deformer_sync.initialize_existing_export_ao_settings()
    profile_sync.on_load()
    export_masks.install_runtime_integration()
    hair_system_compat.install_runtime_integration()


@persistent
def migrate_bridge_ui_on_load(_unused):
    """Upgrade saved bridge node interfaces without touching Hair Tool itself."""
    from . import deformer_sync, nodes

    material_compat.install_runtime_integration()
    deformer_sync.initialize_existing_export_ao_settings()

    for material in bpy.data.materials:
        if not getattr(material.htue_settings, "initialized", False):
            continue
        try:
            nodes.setup_material(material)
        except Exception as exc:
            print(f"HTUE UI migration skipped for {material.name}: {exc}")
    profile_sync.on_load()
    export_masks.install_runtime_integration()
    hair_system_compat.install_runtime_integration()
    layerblend_preview.notify_materials_synchronized(immediate=False)


@persistent
def flush_profiles_before_save(_unused):
    profile_sync.sync_all_materials_now()


@persistent
def mark_deformer_sources_on_update(_scene, depsgraph):
    """Debounce Hair Tool attribute-presence checks after Geometry changes."""
    # Hair Tool can reload its modules independently of the Bridge. Repair its
    # function hooks before the next interactive edit; the healthy path is read-only.
    material_compat.install_runtime_integration()
    profile_sync.mark_deformer_sources_dirty(depsgraph)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Material.htue_settings = PointerProperty(type=properties.HTUE_MaterialSettings)
    bpy.types.Object.htue_ao_settings = PointerProperty(type=properties.HTUE_AOBakeSettings)
    bpy.types.Object.umb_layerblend_preview = PointerProperty(
        type=layerblend_preview.UMB_LayerBlendPreviewSettings
    )
    bpy.types.Scene.umb_layerblend_auto_sync = BoolProperty(
        name="M_LayerBlend Material Auto Sync",
        description=(
            "Automatically maintain lightweight Height previews on every current-Scene "
            "mesh that uses an M_LayerBlend material"
        ),
        default=True,
        update=layerblend_preview.update_scene_auto_sync,
    )
    if migrate_bridge_ui_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(migrate_bridge_ui_on_load)
    if flush_profiles_before_save not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(flush_profiles_before_save)
    if mark_deformer_sources_on_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(mark_deformer_sources_on_update)
    if not bpy.app.timers.is_registered(initialize_export_ao_after_register):
        bpy.app.timers.register(initialize_export_ao_after_register, first_interval=0.0)
    profile_sync.register_auto_sync()
    layerblend_preview.register_auto_sync()


def unregister():
    hair_system_compat.remove_runtime_integration()
    material_compat.remove_runtime_integration()
    export_masks.remove_runtime_integration()
    layerblend_preview.unregister_auto_sync()
    profile_sync.unregister_auto_sync()
    if bpy.app.timers.is_registered(initialize_export_ao_after_register):
        bpy.app.timers.unregister(initialize_export_ao_after_register)
    if migrate_bridge_ui_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(migrate_bridge_ui_on_load)
    if flush_profiles_before_save in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(flush_profiles_before_save)
    if mark_deformer_sources_on_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(mark_deformer_sources_on_update)
    if hasattr(bpy.types.Material, "htue_settings"):
        del bpy.types.Material.htue_settings
    if hasattr(bpy.types.Object, "htue_ao_settings"):
        del bpy.types.Object.htue_ao_settings
    if hasattr(bpy.types.Object, "umb_layerblend_preview"):
        del bpy.types.Object.umb_layerblend_preview
    if hasattr(bpy.types.Scene, "umb_layerblend_auto_sync"):
        del bpy.types.Scene.umb_layerblend_auto_sync
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
