bl_info = {
    "name": "Unreal Material Bridge",
    "author": "PARK / OpenAI Codex",
    "version": (0, 8, 0),
    "blender": (5, 1, 0),
    "location": "3D View > Unreal Bridge; Material Properties > Unreal Material Bridge",
    "description": "Synchronize Hair Tool materials and preview M_LayerBlend height from Unreal",
    "category": "Material",
}

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, PointerProperty

from . import layerblend_preview, operators, properties, ui


CLASSES = (
    properties.CLASSES
    + operators.CLASSES
    + ui.CLASSES
    + layerblend_preview.CLASSES
)


def initialize_export_ao_after_register():
    """Run after Blender releases the restricted registration data context."""
    from . import deformer_sync

    deformer_sync.initialize_existing_export_ao_settings()


@persistent
def migrate_bridge_ui_on_load(_unused):
    """Upgrade saved bridge node interfaces without touching Hair Tool itself."""
    from . import deformer_sync, nodes, schema

    deformer_sync.initialize_existing_export_ao_settings()

    for material_name in schema.TARGET_TEXTURE_SETS:
        material = bpy.data.materials.get(material_name)
        if material is None or not getattr(material.htue_settings, "initialized", False):
            continue
        try:
            nodes.setup_material(material)
        except Exception as exc:
            print(f"HTUE UI migration skipped for {material_name}: {exc}")
    layerblend_preview.notify_materials_synchronized(immediate=False)


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
    if not bpy.app.timers.is_registered(initialize_export_ao_after_register):
        bpy.app.timers.register(initialize_export_ao_after_register, first_interval=0.0)
    layerblend_preview.register_auto_sync()


def unregister():
    layerblend_preview.unregister_auto_sync()
    if bpy.app.timers.is_registered(initialize_export_ao_after_register):
        bpy.app.timers.unregister(initialize_export_ao_after_register)
    if migrate_bridge_ui_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(migrate_bridge_ui_on_load)
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
