"""Exercise whole-scene Dry/Wet preview with partial and empty selections."""
import addon_utils
import bpy
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"addons"))
assert addon_utils.enable("hair_tool_unreal_bridge",default_set=False) is not None
from hair_tool_unreal_bridge import deformer_sync,ui

def group(name):
    g=bpy.data.node_groups.new(name,'GeometryNodeTree')
    g.interface.new_socket(name='Geometry',in_out='INPUT',socket_type='NodeSocketGeometry')
    g.interface.new_socket(name='Geometry',in_out='OUTPUT',socket_type='NodeSocketGeometry')
    n=g.nodes.new('NodeGroupInput')
    out=g.nodes.new('NodeGroupOutput')
    g.links.new(n.outputs['Geometry'],out.inputs['Geometry'])
    return g

mesh=bpy.data.meshes.new("WetPreviewTest")
o=bpy.data.objects.new("WetPreviewTest",mesh)
bpy.context.collection.objects.link(o)
for name,prefix in [('Setup','Hair_System_Setup_Test'),('Wet - Strand Filter','Hair_System_Main.Wet_Test'),('Profile','Hair_System_Profile_Test')]:
    m=o.modifiers.new(name,'NODES')
    m.node_group=group(prefix)
wet=o.modifiers['Wet - Strand Filter']
flag=wet.node_group.nodes.new('FunctionNodeInputBool')
flag.name='Use Children'
flag.boolean=False
wet.show_viewport=False
wet.show_render=True
other=o.copy()
bpy.context.collection.objects.link(other)
other.hide_set(True)
other_wet=other.modifiers[wet.name]
other_wet.show_render=False
plain_curve=bpy.data.objects.new("Unrelated object name",bpy.data.curves.new("Curve","CURVE"))
bpy.context.collection.objects.link(plain_curve)
plain_wet=plain_curve.modifiers.new("wEt adjustment","SMOOTH")
unmatched=plain_curve.modifiers.new("Regular adjustment","SMOOTH")
unmatched.show_viewport=True
other_scene=bpy.data.scenes.new("Other Scene")
external=o.copy()
other_scene.collection.objects.link(external)
external_wet=external.modifiers[wet.name]
for obj in bpy.context.selected_objects:obj.select_set(False)
for selected in (True,False):
    o.select_set(selected)
    bpy.context.view_layer.objects.active=o if selected else None
    assert ui.HTUE_PT_Sidebar.poll(bpy.context)
    assert set(deformer_sync.scene_wet_filters(bpy.context))=={wet,other_wet,plain_wet}
    for value in (True,False):
        assert bpy.ops.htue.set_wet_preview(enabled=value)=={'FINISHED'}
        assert wet.show_viewport==other_wet.show_viewport==plain_wet.show_viewport==value
        assert unmatched.show_viewport
        assert wet.show_render and not other_wet.show_render and not external_wet.show_viewport

class Layout:
    def __init__(self):self.labels=[];self.buttons=[]
    def box(self):return self
    def row(self,**kwargs):return self
    def label(self,*,text,**kwargs):self.labels.append(text)
    def operator(self,name,**kwargs):
        assert name=='htue.set_wet_preview'
        button=SimpleNamespace()
        self.buttons.append((kwargs['text'],button))
        return button

layout=Layout()
ui._draw_wet_preview(layout,bpy.context)
assert 'Wet Preview' in layout.labels
assert [(label,button.enabled) for label,button in layout.buttons]==[('Dry',False),('Wet',True)]
print('WET_PREVIEW_PASS: whole scene, hidden and unselected hair, no active object, independent render flags')

