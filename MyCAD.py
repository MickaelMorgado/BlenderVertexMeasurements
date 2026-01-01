bl_info = {
    "name": "Selected Vert-to-Vert Distances (GPU Screen-Space Text)",
    "author": "MyCAD",
    "version": (17, 0),
    "blender": (4, 4, 0),
    "location": "View3D > Sidebar (N) > MyCAD",
    "description": "Easy extrusion with grid snapping",
    "category": "3D View",
}

import bpy
import bmesh
from mathutils import Vector, Matrix
import math

class MyCADProperties(bpy.types.PropertyGroup):
    x_step: bpy.props.FloatProperty(name="X Step", default=1.0, min=0.01)
    y_step: bpy.props.FloatProperty(name="Y Step", default=1.0, min=0.01)
    z_step: bpy.props.FloatProperty(name="Z Step", default=1.0, min=0.01)
    extrude_center: bpy.props.FloatVectorProperty(size=3, default=(0,0,0))
    x_offset: bpy.props.FloatProperty(default=0.0, update=lambda self, context: extrude_axis('X', self.x_offset, context))
    y_offset: bpy.props.FloatProperty(default=0.0, update=lambda self, context: extrude_axis('Y', self.y_offset, context))
    z_offset: bpy.props.FloatProperty(default=0.0, update=lambda self, context: extrude_axis('Z', self.z_offset, context))
    extruded_verts_x: bpy.props.StringProperty(default="")
    extruded_verts_y: bpy.props.StringProperty(default="")
    extruded_verts_z: bpy.props.StringProperty(default="")
    current_extrusion_x: bpy.props.FloatProperty(default=0.0)
    current_extrusion_y: bpy.props.FloatProperty(default=0.0)
    current_extrusion_z: bpy.props.FloatProperty(default=0.0)
    is_active: bpy.props.BoolProperty(default=False)

def extrude_axis(axis, offset, context):
    props = context.scene.mycad_props
    obj = context.active_object
    if not obj or obj.mode != 'EDIT' or obj.type != 'MESH':
        return
    bm = bmesh.from_edit_mesh(obj.data)
    selected_faces = [f for f in bm.faces if f.select]
    if not selected_faces:
        return
    direction = {'X': Vector((1,0,0)), 'Y': Vector((0,1,0)), 'Z': Vector((0,0,1))}[axis]
    step = getattr(props, f"{axis.lower()}_step")
    verts_attr = f"extruded_verts_{axis.lower()}"
    current_attr = f"current_extrusion_{axis.lower()}"
    verts_str = getattr(props, verts_attr)
    if not verts_str:
        # first time, extrude
        extruded = bmesh.ops.extrude_face_region(bm, geom=selected_faces)
        verts_indices = [v.index for v in extruded['geom'] if isinstance(v, bmesh.types.BMVert)]
        verts_str = ','.join(str(i) for i in verts_indices)
        setattr(props, verts_attr, verts_str)
        setattr(props, current_attr, 0.0)
    else:
        verts_indices = [int(i) for i in verts_str.split(',') if i.strip()]
    current_extrusion = getattr(props, current_attr)
    offset *= 0.1  # reduce sensitivity
    new_extrusion = round(offset / step) * step
    move_by = new_extrusion - current_extrusion
    if abs(move_by) > 0.0001:
        verts = [bm.verts[i] for i in verts_indices if i < len(bm.verts)]
        bmesh.ops.translate(bm, verts=verts, vec=direction * move_by)
        setattr(props, current_attr, new_extrusion)
    bmesh.update_edit_mesh(obj.data)

class MyCADPanel(bpy.types.Panel):
    bl_label = "MyCAD"
    bl_idname = "OBJECT_PT_mycad"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MyCAD"

    def draw(self, context):
        layout = self.layout
        props = context.scene.mycad_props
        layout.prop(props, "x_step")
        layout.prop(props, "y_step")
        layout.prop(props, "z_step")
        layout.operator("object.mycad_extrude")

class MyCADExtrudeOperator(bpy.types.Operator):
    bl_idname = "object.mycad_extrude"
    bl_label = "MyCAD Extrude"

    def execute(self, context):
        obj = context.active_object
        if obj.mode != 'EDIT' or obj.type != 'MESH':
            self.report({'ERROR'}, "Must be in edit mode with mesh selected")
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(obj.data)
        selected_faces = [f for f in bm.faces if f.select]
        if not selected_faces:
            self.report({'ERROR'}, "No faces selected")
            return {'CANCELLED'}
        center = sum((obj.matrix_world @ f.calc_center_median() for f in selected_faces), Vector()) / len(selected_faces)
        props = context.scene.mycad_props
        props.extrude_center = center
        props.extruded_verts_x = ""
        props.extruded_verts_y = ""
        props.extruded_verts_z = ""
        props.current_extrusion_x = 0.0
        props.current_extrusion_y = 0.0
        props.current_extrusion_z = 0.0
        props.x_offset = 0.0
        props.y_offset = 0.0
        props.z_offset = 0.0
        props.is_active = True
        # Disable built-in extrude gizmo to avoid conflict
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                space = area.spaces.active
                if space.type == 'VIEW_3D':
                    space.show_gizmo_mesh_edit_extrude = False
        return {'FINISHED'}

class MyCADGizmoGroup(bpy.types.GizmoGroup):
    bl_idname = "gizmogroup.mycad"
    bl_label = "MyCAD Gizmo Group"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D', 'PERSISTENT'}

    @classmethod
    def poll(cls, context):
        return (context.scene.mycad_props.is_active and
                context.active_object and
                context.active_object.mode == 'EDIT' and
                context.active_object.type == 'MESH')

    def setup(self, context):
        props = context.scene.mycad_props
        center = props.extrude_center
        # X gizmo
        gizmo_x = self.gizmos.new("GIZMO_GT_arrow_3d")
        gizmo_x.target_set_prop("offset", props, "x_offset")
        gizmo_x.color = (1, 0, 0, 1)
        gizmo_x.color_highlight = (1, 0.5, 0.5, 1)
        gizmo_x.alpha = 1.0
        gizmo_x.alpha_highlight = 1.0
        gizmo_x.scale_basis = 5.0
        gizmo_x.matrix_basis = Matrix.Translation(center) @ Matrix.Rotation(0, 4, 'X')
        # Y gizmo
        gizmo_y = self.gizmos.new("GIZMO_GT_arrow_3d")
        gizmo_y.target_set_prop("offset", props, "y_offset")
        gizmo_y.color = (0, 1, 0, 1)
        gizmo_y.color_highlight = (0.5, 1, 0.5, 1)
        gizmo_y.alpha = 1.0
        gizmo_y.alpha_highlight = 1.0
        gizmo_y.scale_basis = 5.0
        gizmo_y.matrix_basis = Matrix.Translation(center) @ Matrix.Rotation(math.pi/2, 4, 'Z')
        # Z gizmo
        gizmo_z = self.gizmos.new("GIZMO_GT_arrow_3d")
        gizmo_z.target_set_prop("offset", props, "z_offset")
        gizmo_z.color = (0, 0, 1, 1)
        gizmo_z.color_highlight = (0.5, 0.5, 1, 1)
        gizmo_z.alpha = 1.0
        gizmo_z.alpha_highlight = 1.0
        gizmo_z.scale_basis = 5.0
        gizmo_z.matrix_basis = Matrix.Translation(center) @ Matrix.Rotation(math.pi/2, 4, 'Y')

    def refresh(self, context):
        props = context.scene.mycad_props
        center = props.extrude_center
        for gizmo in self.gizmos:
            gizmo.matrix_basis.translation = center

def register():
    bpy.utils.register_class(MyCADProperties)
    bpy.types.Scene.mycad_props = bpy.props.PointerProperty(type=MyCADProperties)
    bpy.utils.register_class(MyCADPanel)
    bpy.utils.register_class(MyCADExtrudeOperator)
    bpy.utils.register_class(MyCADGizmoGroup)

def unregister():
    bpy.utils.unregister_class(MyCADGizmoGroup)
    bpy.utils.unregister_class(MyCADExtrudeOperator)
    bpy.utils.unregister_class(MyCADPanel)
    del bpy.types.Scene.mycad_props
    bpy.utils.unregister_class(MyCADProperties)

if __name__ == "__main__":
    register()
