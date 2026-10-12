"""Read-only validation of a saved result; run in a disposable Blender process."""
import json
from pathlib import Path
import sys

import bpy


def saved_state():
    # Only scene-rooted objects persist. Orphan objects can reference further
    # orphan guides, giving them users>0 without a saved root.
    scene_objects = {obj for scene in bpy.data.scenes for obj in scene.objects}
    screens = []
    for screen in sorted(bpy.data.screens, key=lambda s: s.name):
        areas = []
        for area in screen.areas:
            views = []
            for space in area.spaces:
                if space.type == 'VIEW_3D':
                    region = space.region_3d
                    views.append([space.lens, space.clip_start, space.clip_end,
                                  region.view_perspective, region.view_distance,
                                  list(region.view_location), list(region.view_rotation),
                                  space.shading.type, space.shading.light,
                                  space.shading.color_type, space.show_region_ui])
            areas.append([area.type, area.ui_type, area.x, area.y,
                          area.width, area.height, views])
        screens.append([screen.name, areas])
    return {
        'screens': screens,
        'workspaces': sorted(w.name for w in bpy.data.workspaces),
        'scenes': sorted([s.name, s.frame_current] for s in bpy.data.scenes),
        'slots': sorted([o.name, [s.material.name if s.material else None
                               for s in o.material_slots]] for o in bpy.data.objects
                        if o in scene_objects or o.use_fake_user),
    }


if __name__ == '__main__':
    source, expected_path = sys.argv[sys.argv.index('--') + 1:]
    expected = json.loads(Path(expected_path).read_text(encoding='utf-8'))
    bpy.ops.wm.open_mainfile(filepath=source, load_ui=True)
    actual = saved_state()
    if actual['slots'] != expected['slots']:
        old = dict(expected['slots'])
        new = dict(actual['slots'])
        print('SLOT_DIFFERENCES ' + json.dumps({
            name: {'live': old.get(name), 'saved': new.get(name)}
            for name in old.keys() | new.keys() if old.get(name) != new.get(name)
        }, ensure_ascii=False))
    assert actual == expected, [key for key in expected if actual[key] != expected[key]]
    for name in ('M_HT_Default_Material_01', 'M_HT_Default_Material_short_01',
                 'M_HT_Default_Material_short_02'):
        material = bpy.data.materials[name]
        assert material.use_fake_user and material.node_tree
        contract = json.loads(material['htue_contract_json'])
        assert contract['material_instance_path'] == (
            '/Game/Material/HairTool/MI/MI_' + name.removeprefix('M_'))
    print('HTUE_SAVED_SHARED_MATERIALS_UI_OK')
