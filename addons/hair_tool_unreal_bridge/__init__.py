bl_info = {
    "name": "Hair Tool Unreal Bridge",
    "author": "PARK / OpenAI Codex",
    "version": (0, 3, 0),
    "blender": (5, 1, 0),
    "location": "Material Properties > Hair Tool Unreal Bridge",
    "description": "Synchronize Hair Tool color layers, masks, blend modes and textures with Unreal",
    "category": "Material",
}

import bpy
from bpy.app.handlers import persistent
from bpy.props import PointerProperty

from . import operators, properties, ui


CLASSES = properties.CLASSES + operators.CLASSES + ui.CLASSES


@persistent
def migrate_bridge_ui_on_load(_unused):
    """Upgrade saved bridge node interfaces without touching Hair Tool itself."""
    from . import nodes, schema

    for material_name in schema.TARGET_TEXTURE_SETS:
        material = bpy.data.materials.get(material_name)
        if material is None or not getattr(material.htue_settings, "initialized", False):
            continue
        try:
            nodes.setup_material(material)
        except Exception as exc:
            print(f"HTUE UI migration skipped for {material_name}: {exc}")


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Material.htue_settings = PointerProperty(type=properties.HTUE_MaterialSettings)
    if migrate_bridge_ui_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(migrate_bridge_ui_on_load)


def unregister():
    if migrate_bridge_ui_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(migrate_bridge_ui_on_load)
    if hasattr(bpy.types.Material, "htue_settings"):
        del bpy.types.Material.htue_settings
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
