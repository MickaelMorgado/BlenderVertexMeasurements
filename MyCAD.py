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

class MyCADPanel(bpy.types.Panel):
    bl_label = "MyCAD"
    bl_idname = "OBJECT_PT_mycad"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MyCAD"

    def draw(self, context):
        layout = self.layout
        layout.operator("object.mycad_extrude")


def register():
    bpy.utils.register_class(MyCADPanel)


def unregister():
    bpy.utils.unregister_class(MyCADPanel)


if __name__ == "__main__":
    register()
