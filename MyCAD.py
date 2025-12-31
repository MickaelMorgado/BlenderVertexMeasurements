bl_info = {
    "name": "Selected Vert-to-Vert Distances (GPU Screen-Space Text)",
    "author": "ChatGPT - CAD Sketcher Inspired Text Display",
    "version": (17, 0),
    "blender": (4, 4, 0),
    "location": "View3D > Sidebar (N) > Overlay Test",
    "description": "Realtime distances with GPU lines + CAD Sketcher inspired screen-space text",
    "category": "3D View",
}

import bpy
from mathutils import Vector, Matrix
import gpu
from gpu_extras.batch import batch_for_shader
import bmesh
import json
import blf
from bpy_extras import view3d_utils

_gpu_pairs = []          # list[(va, vb, dist)]
_draw_handler = None
_handler_registered = False
_last_vertex_positions = {}  # Cache for vertex positions to detect changes
_update_timer = None  # Timer for frequent updates

_TEXT_COLLECTION_NAME = "WorldDistancesText"
FONT_ID = 0


# ========= CAD Sketcher inspired value_placement =========

def value_placement(context, world_pos):
    """Calculate screen position for text display (inspired by CAD Sketcher)"""
    region = context.region
    rv3d = context.space_data.region_3d

    # Convert 3D world position to 2D screen coordinates
    screen_pos = view3d_utils.location_3d_to_region_2d(region, rv3d, world_pos)
    return screen_pos


# ========= text collection helpers =========

def _get_text_collection(create=False):
    col = bpy.data.collections.get(_TEXT_COLLECTION_NAME)
    if not col and create:
        col = bpy.data.collections.new(_TEXT_COLLECTION_NAME)
        bpy.context.scene.collection.children.link(col)
    return col


def _clear_distance_objects():
    col = _get_text_collection(False)
    if not col:
        return
    for obj in list(col.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


# ========= global clear =========

def distance_overlay_global_clear():
    global _draw_handler, _gpu_pairs, _handler_registered, _last_vertex_positions, _update_timer

    _gpu_pairs = []
    _last_vertex_positions = {}
    _clear_distance_objects()

    if _draw_handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_draw_handler, 'WINDOW')
        except:
            pass
        _draw_handler = None

    if _update_timer is not None:
        try:
            bpy.app.timers.unregister(_update_timer)
        except:
            pass
        _update_timer = None

    if _handler_registered:
        try:
            bpy.app.handlers.depsgraph_update_post.remove(distance_depsgraph_update)
        except ValueError:
            pass
        _handler_registered = False

    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


# ========= selection helpers =========

def _collect_selected_world_verts_object_mode(obj, max_vertices, verts_out):
    """Object Mode: use all verts from obj (world-space)."""
    mat = obj.matrix_world
    for v in obj.data.vertices:
        verts_out.append(mat @ v.co)
        if len(verts_out) >= max_vertices:
            return


def _collect_selected_world_verts_edit_mode(obj, max_vertices, per_obj_verts, global_verts):
    """Edit Mode: selected BMVerts, keep BMVert + world coord."""
    bm = bmesh.from_edit_mesh(obj.data)
    mat = obj.matrix_world

    selected_bm_verts = set()

    for v in bm.verts:
        if v.select:
            selected_bm_verts.add(v)
    for e in bm.edges:
        if e.select:
            selected_bm_verts.update(e.verts)
    for f in bm.faces:
        if f.select:
            selected_bm_verts.update(f.verts)

    if not selected_bm_verts:
        return

    local_list = []
    for v in selected_bm_verts:
        world_co = mat @ v.co
        local_list.append((v, world_co))
        global_verts.append(world_co)
        if len(global_verts) >= max_vertices:
            break

    if local_list:
        per_obj_verts.append((obj, bm, local_list))


# ========= pair collection =========

def collect_vertex_pairs(max_mm, max_vertices, max_pairs, neighbor_depth,
                         locked, locked_sets_json):
    """
    - Object Mode: global shortest pairs over verts of selected meshes.
    - Edit Mode:
        - If locked: use stored per-object vertex indices.
        - Else: use current selection and adjacency steps.
    """
    verts_global = []
    per_obj_edit = []  # list of (obj, bm, [(BMVert, world_co), ...])

    active = bpy.context.view_layer.objects.active
    in_edit = active and active.type == 'MESH' and active.mode == 'EDIT'

    # Locked branch: rebuild from stored sets
    locked_sets = []
    if locked and locked_sets_json:
        try:
            locked_sets = json.loads(locked_sets_json) or []
        except Exception:
            locked_sets = []

    if locked_sets:
        for entry in locked_sets:
            obj_name = entry.get("obj")
            indices = entry.get("verts") or []
            obj = bpy.data.objects.get(obj_name)
            if not obj or obj.type != 'MESH':
                continue

            mat = obj.matrix_world
            mesh = obj.data

            bm = bmesh.new()
            bm.from_mesh(mesh)
            bm.verts.ensure_lookup_table()

            local_list = []
            for idx in indices:
                if 0 <= idx < len(bm.verts):
                    v = bm.verts[idx]
                    world_co = mat @ v.co
                    local_list.append((v, world_co))
                    verts_global.append(world_co)
                    if len(verts_global) >= max_vertices:
                        break

            if local_list:
                per_obj_edit.append((obj, bm, local_list))
            else:
                bm.free()

    # Normal collection when not locked or lock invalid
    if not verts_global and not per_obj_edit:
        for obj in bpy.context.selected_objects:
            if obj.type != 'MESH':
                continue

            if in_edit and obj.mode == 'EDIT':
                _collect_selected_world_verts_edit_mode(obj, max_vertices, per_obj_edit, verts_global)
            else:
                _collect_selected_world_verts_object_mode(obj, max_vertices, verts_global)

            if len(verts_global) >= max_vertices:
                break

    # if nothing at all, nothing to draw
    if not verts_global and not per_obj_edit:
        return []

    pairs = []

    # 1) global shortest pairs (only if at least 2 verts exist)
    n = len(verts_global)
    if n >= 2:
        for i in range(n):
            for j in range(i + 1, n):
                a = verts_global[i]
                b = verts_global[j]
                d = (a - b).length
                if d <= max_mm:
                    pairs.append((a, b, d))

    # 2) adjacency: walk BMVert.link_edges per mesh (for edit/locked sets)
    if neighbor_depth > 0 and per_obj_edit:
        for obj, bm, vert_list in per_obj_edit:
            mat = obj.matrix_world

            for bm_vert, world_co in vert_list:
                visited = {bm_vert}
                frontier = {bm_vert}

                for _step in range(neighbor_depth):
                    next_frontier = set()
                    for v in frontier:
                        for e in v.link_edges:
                            other = e.other_vert(v)
                            if other in visited:
                                continue
                            visited.add(other)
                            next_frontier.add(other)

                            a = world_co
                            b = mat @ other.co
                            d = (a - b).length
                            if d <= max_mm:
                                pairs.append((a, b, d))
                    frontier = next_frontier
                    if not frontier:
                        break

            # free BM if we created it from object data (locked branch)
            if obj.mode != 'EDIT':
                bm.free()

    # deduplicate by endpoints
    unique = {}
    for a, b, d in pairs:
        if a < b:
            key = tuple(round(x, 6) for x in (*a, *b))
        else:
            key = tuple(round(x, 6) for x in (*b, *a))
        if key not in unique or d < unique[key][2]:
            unique[key] = (a, b, d)

    pairs = list(unique.values())
    pairs.sort(key=lambda x: x[2])
    return pairs[:max_pairs]


# ========= text objects (3D fallback) =========

def update_text_objects():
    """Update 3D text objects at line midpoints (fallback)"""
    col = _get_text_collection(True)
    _clear_text_objects()

    for idx, (va, vb, dist_mm) in enumerate(_gpu_pairs):
        txt_data = bpy.data.curves.new(name=f"WD_Text_{idx}", type='FONT')
        txt_data.body = f"{dist_mm:.2f} mm"
        txt_data.align_x = 'CENTER'
        txt_data.align_y = 'CENTER'
        txt_data.size = 0.05  # Smaller size for better positioning

        txt_obj = bpy.data.objects.new(f"WD_TextObj_{idx}", txt_data)
        mid = (va + vb) * 0.5
        txt_obj.location = mid

        # Simple upright orientation (no billboarding for now)
        txt_obj.rotation_euler = (0, 0, 0)  # Face upward in world space
        col.objects.link(txt_obj)


# ========= 3D mesh lines =========

def update_mesh_lines():
    """Create 3D mesh lines for distance visualization"""
    col = _get_text_collection(True)

    # Clear existing line objects
    for obj in list(col.objects):
        if obj.name.startswith("WD_Line_"):
            bpy.data.objects.remove(obj, do_unlink=True)

    # Create or get grey material for lines
    mat_name = "WD_DistanceLine_Material"
    mat = bpy.data.materials.get(mat_name)
    if not mat:
        mat = bpy.data.materials.new(name=mat_name)
        mat.diffuse_color = (0.5, 0.5, 0.5, 1.0)  # Grey color
        mat.use_nodes = False  # Use legacy material

    for idx, (va, vb, dist_mm) in enumerate(_gpu_pairs):
        # Create mesh for line
        mesh = bpy.data.meshes.new(name=f"WD_LineMesh_{idx}")
        mesh.from_pydata([va, vb], [(0, 1)], [])
        mesh.update()

        line_obj = bpy.data.objects.new(f"WD_Line_{idx}", mesh)
        line_obj.data.materials.append(mat)  # Assign grey material
        col.objects.link(line_obj)


# ========= vertex position tracking =========

def get_current_vertex_positions():
    """Get current world positions of all relevant vertices"""
    positions = {}
    settings = getattr(bpy.context.scene, "distance_settings", None)
    if not settings:
        return positions

    active = bpy.context.view_layer.objects.active
    in_edit = active and active.type == 'MESH' and active.mode == 'EDIT'

    # Collect positions based on current mode and settings
    locked_sets = []
    if settings.lock_selection and settings.locked_sets_json:
        try:
            locked_sets = json.loads(settings.locked_sets_json) or []
        except Exception:
            locked_sets = []

    if locked_sets:
        for entry in locked_sets:
            obj_name = entry.get("obj")
            indices = entry.get("verts") or []
            obj = bpy.data.objects.get(obj_name)
            if not obj or obj.type != 'MESH':
                continue

            mat = obj.matrix_world
            mesh = obj.data

            if in_edit and obj.mode == 'EDIT':
                bm = bmesh.from_edit_mesh(mesh)
                bm.verts.ensure_lookup_table()
                for idx in indices:
                    if 0 <= idx < len(bm.verts):
                        v = bm.verts[idx]
                        world_co = mat @ v.co
                        positions[f"{obj_name}.{idx}"] = world_co
            else:
                for idx in indices:
                    if 0 <= idx < len(mesh.vertices):
                        v = mesh.vertices[idx]
                        world_co = mat @ v.co
                        positions[f"{obj_name}.{idx}"] = world_co
    else:
        for obj in bpy.context.selected_objects:
            if obj.type != 'MESH':
                continue

            mat = obj.matrix_world

            if in_edit and obj.mode == 'EDIT':
                bm = bmesh.from_edit_mesh(obj.data)
                for v in bm.verts:
                    if v.select:
                        world_co = mat @ v.co
                        positions[f"{obj.name}.{v.index}"] = world_co
            else:
                for v in obj.data.vertices:
                    world_co = mat @ v.co
                    positions[f"{obj.name}.{v.index}"] = world_co

    return positions


def positions_changed(old_positions, new_positions, threshold=0.001):
    """Check if vertex positions have changed significantly"""
    if set(old_positions.keys()) != set(new_positions.keys()):
        return True  # Different set of vertices

    for key in old_positions:
        if key not in new_positions:
            return True
        old_pos = old_positions[key]
        new_pos = new_positions[key]
        if (old_pos - new_pos).length > threshold:
            return True
    return False


# ========= update handlers =========

def distance_update():
    """Update distances and redraw"""
    global _gpu_pairs

    settings = getattr(bpy.context.scene, "distance_settings", None)
    if not settings:
        return

    # Recalculate distances
    _gpu_pairs = collect_vertex_pairs(
        settings.max_mm,
        settings.max_vertices,
        settings.max_pairs,
        settings.neighbor_depth,
        settings.lock_selection,
        settings.locked_sets_json,
    )

    # Update 3D mesh lines (using BLF for text overlay)
    update_mesh_lines()

    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def distance_depsgraph_update(scene, depsgraph):
    distance_update()


def distance_frame_update(scene):
    """Frame change handler for real-time updates during transforms"""
    distance_update()


# ========= GPU draw callback =========

def draw_callback_gpu():
    # GPU lines commented out - now using 3D mesh objects for lines
    # Keep screen-space BLF text with distance-based opacity

    if not _gpu_pairs:
        return

    region = bpy.context.region
    rv3d = bpy.context.space_data.region_3d
    if not region or not rv3d:
        return

    # Get camera position for distance calculation
    camera_pos = rv3d.view_matrix.inverted().translation

    # Draw GPU screen-space text (BLF) with distance-based opacity
    text_size = 16  # Like CAD Sketcher text_size

    blf.size(FONT_ID, text_size)

    for va, vb, dist_mm in _gpu_pairs:
        # Create same data structure as working test
        world_pos = (va + vb) * 0.5  # 3D midpoint
        text = f"{dist_mm:.2f} mm"

        # Calculate distance-based opacity (stronger when closer)
        distance = (world_pos - camera_pos).length - 1.5
        # Fade from 1.0 (close) to 0.6 (far), over 20 units
        alpha = max(0.2, 1.0 - distance / 5.0)
        blf.color(FONT_ID, 1.0, 1.0, 1.0, alpha)

        # Use exact same positioning logic as working test
        screen_pos = value_placement(bpy.context, world_pos)
        if not screen_pos:
            continue

        width, height = blf.dimensions(FONT_ID, text)
        margin = text_size / 4  # Same as working test

        x = screen_pos.x - width / 2
        y = screen_pos.y + margin

        blf.position(FONT_ID, x, y, 0)
        blf.draw(FONT_ID, text)


# ========= properties =========

class DistanceSettings(bpy.types.PropertyGroup):
    max_mm: bpy.props.FloatProperty(
        name="Max Distance (mm)",
        description="Only pairs with distance <= this value (assuming 1 BU = 1 mm)",
        default=100.0,
        min=0.01,
        max=10000.0,
        soft_min=1.0,
        soft_max=500.0,
        step=1.0,
        precision=2,
    )
    max_vertices: bpy.props.IntProperty(
        name="Max Vertices",
        description="Maximum vertices sampled from meshes / elements",
        default=100,
        min=1,
        max=10000,
        soft_min=10,
        soft_max=500,
    )
    max_pairs: bpy.props.IntProperty(
        name="Max Pairs",
        description="Maximum pairs to display (after neighbor expansion)",
        default=10,
        min=1,
        max=10000,
        soft_min=1,
        soft_max=500,
    )
    neighbor_depth: bpy.props.IntProperty(
        name="Adjacency Steps (Edit)",
        description="In Edit Mode, show distances along edges up to this many steps from each selected vertex",
        default=1,
        min=0,
        max=5,
    )
    lock_selection: bpy.props.BoolProperty(
        name="Lock Selection",
        description="Use stored vertices instead of current selection",
        default=False,
    )
    locked_sets_json: bpy.props.StringProperty(
        name="Locked Sets JSON",
        description="Serialized list of locked selections per object",
        default="[]",
    )
    locked_count: bpy.props.IntProperty(
        name="Locked Count",
        description="Total number of locked vertices across all objects",
        default=0,
        min=0,
    )


# ========= lock operator =========

class VIEW3D_OT_lock_world_distances(bpy.types.Operator):
    bl_idname = "view3d.lock_world_distances_selection"
    bl_label = "Lock Current Selection"
    bl_description = "Store current Edit Mode vertex selections (across all edited meshes) as the locked set"

    def execute(self, context):
        scene = context.scene
        settings = scene.distance_settings

        active = context.view_layer.objects.active
        if not active or active.type == 'MESH' and active.mode == 'EDIT':
            self.report({'WARNING'}, "At least one mesh in Edit Mode required to lock selection")
            return {'CANCELLED'}

        locked_sets = []
        total_verts = 0

        for obj in context.selected_objects:
            if obj.type != 'MESH' or obj.mode != 'EDIT':
                continue

            bm = bmesh.from_edit_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            indices = [v.index for v in bm.verts if v.select]

            if indices:
                locked_sets.append({
                    "obj": obj.name,
                    "verts": indices,
                })
                total_verts += len(indices)

        if not locked_sets:
            self.report({'WARNING'}, "No selected vertices on any edited mesh to lock")
            return {'CANCELLED'}

        settings.locked_sets_json = json.dumps(locked_sets)
        settings.locked_count = total_verts
        settings.lock_selection = True

        self.report({'INFO'}, f"Locked {total_verts} vertices on {len(locked_sets)} object(s)")
        return {'FINISHED'}


# ========= toggle operator =========

class VIEW3D_OT_toggle_world_distances(bpy.types.Operator):
    bl_idname = "view3d.toggle_world_distances_text_gpu"
    bl_label = "Realtime Distances On / Off"
    bl_description = "Toggle realtime distances using GPU lines + CAD Sketcher inspired screen-space text"

    running: bpy.props.BoolProperty(default=False)

    def execute(self, context):
        global _draw_handler, _gpu_pairs, _handler_registered

        scene = context.scene
        settings = scene.distance_settings

        if self.running:
            distance_overlay_global_clear()
            self.running = False
            return {'FINISHED'}

        distance_overlay_global_clear()

        _gpu_pairs = collect_vertex_pairs(
            settings.max_mm,
            settings.max_vertices,
            settings.max_pairs,
            settings.neighbor_depth,
            settings.lock_selection,
            settings.locked_sets_json,
        )
        if not _gpu_pairs:
            self.report(
                {'WARNING'},
                "No distance pairs found. Check: mesh selection, vertex selection in Edit mode, locked selection, or increase 'Max Distance (mm)' threshold."
            )
            return {'CANCELLED'}

        # Initialize position cache
        global _last_vertex_positions
        _last_vertex_positions = get_current_vertex_positions()

        if _draw_handler is None:
            _draw_handler = bpy.types.SpaceView3D.draw_handler_add(
                draw_callback_gpu, (), 'WINDOW', 'POST_PIXEL'
            )

        if not _handler_registered:
            bpy.app.handlers.depsgraph_update_post.append(distance_depsgraph_update)
            _handler_registered = True

        # Start frequent update timer for real-time feedback
        global _update_timer
        if _update_timer is None:
            _update_timer = bpy.app.timers.register(distance_update, first_interval=0.1, persistent=True)

        self.running = True
        return {'FINISHED'}


# ========= panel =========

class VIEW3D_PT_world_distances(bpy.types.Panel):
    bl_label = "World Distances"
    bl_idname = "VIEW3D_PT_world_distances"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Overlay Test"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.distance_settings

        layout.prop(settings, "max_mm")
        layout.prop(settings, "max_vertices")
        layout.prop(settings, "max_pairs")
        layout.prop(settings, "neighbor_depth")

        row = layout.row(align=True)
        row.prop(settings, "lock_selection", text="Lock Selection")
        row.operator("view3d.lock_world_distances_selection", text="", icon='PINNED')

        if settings.lock_selection:
            layout.label(text=f"Locked verts: {settings.locked_count}")

        layout.label(text="GPU screen-space text (CAD Sketcher style)")
        layout.operator("view3d.toggle_world_distances_text_gpu", icon='FONT_DATA')


# ========= register =========

classes = (
    DistanceSettings,
    VIEW3D_OT_lock_world_distances,
    VIEW3D_OT_toggle_world_distances,
    VIEW3D_PT_world_distances,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.distance_settings = bpy.props.PointerProperty(type=DistanceSettings)

def unregister():
    if hasattr(bpy.types.Scene, "distance_settings"):
        del bpy.types.Scene.distance_settings
    distance_overlay_global_clear()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
