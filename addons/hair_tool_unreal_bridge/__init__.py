bl_info = {
    "name": "Hair Tool Unreal Bridge",
    "author": "PARK / OpenAI Codex",
    "version": (0, 1, 0),
    "blender": (5, 1, 0),
    "location": "Material Properties > Hair Tool Unreal Bridge",
    "description": "Synchronize Hair Tool color layers, masks, blend modes and textures with Unreal",
    "category": "Material",
}

import bpy
from bpy.props import PointerProperty

from . import operators, properties, ui


CLASSES = properties.CLASSES + operators.CLASSES + ui.CLASSES


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Material.htue_settings = PointerProperty(type=properties.HTUE_MaterialSettings)


def unregister():
    if hasattr(bpy.types.Material, "htue_settings"):
        del bpy.types.Material.htue_settings
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
