import bpy
import gpu
from bl_ui.properties_render import RenderButtonsPanel
from bl_ui.properties_output import RenderOutputButtonsPanel
import numpy as np
from ..ui.panels.dream_texture import optimization_panels
from .node_tree import DreamTexturesNodeTree
from ..engine import node_executor
from .annotations import openpose
import time
from threading import Event

class DreamTexturesRenderEngine(bpy.types.RenderEngine):
    """A custom Dream Textures render engine, that uses Stable Diffusion and scene data to render images, instead of as a pass on top of Cycles."""

    bl_idname = "DREAM_TEXTURES"
    bl_label = "Dream Textures"
    bl_use_preview = False
    # bl_use_gpu_context = True

    def __init__(self):
        pass

    def __del__(self):
        pass

    def render(self, depsgraph):
        scene = depsgraph.scene

        def prepare_result(result):
            if len(result.shape) == 2:
                return np.concatenate(
                    (
                        np.stack((result,)*3, axis=-1),
                        np.ones((*result.shape, 1))
                    ),
                    axis=-1
                )
            else:
                return result
        
        result = self.begin_result(0, 0, scene.render.resolution_x, scene.render.resolution_y)
        layer = result.layers[0].passes["Combined"]
        self.update_result(result)

        try:
            progress = 0
            def node_begin(node):
                self.update_stats("Node", node.name)
            def node_update(response):
                if isinstance(response, np.ndarray):
                    node_result = prepare_result(response)
                    layer.rect = node_result.reshape(-1, node_result.shape[-1])
                    self.update_result(result)
            def node_end(_):
                nonlocal progress
                progress += 1
                self.update_progress(progress / len(scene.dream_textures_render_engine.node_tree.nodes))
            node_result = node_executor.execute(scene.dream_textures_render_engine.node_tree, depsgraph, node_begin=node_begin, node_update=node_update, node_end=node_end)
            node_result = prepare_result(node_result)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            raise error

        layer.rect = node_result.reshape(-1, node_result.shape[-1])
        self.end_result(result)
    
    def view_update(self, context, depsgraph):
        region = context.region
        view3d = context.space_data
        scene = depsgraph.scene

        # Get viewport dimensions
        dimensions = region.width, region.height

        if not self.scene_data:
            # First time initialization
            self.scene_data = []
            first_time = True

            # Loop over all datablocks used in the scene.
            for datablock in depsgraph.ids:
                pass
        else:
            first_time = False

            # Test which datablocks changed
            for update in depsgraph.updates:
                print("Datablock updated: ", update.id.name)

            # Test if any material was added, removed or changed.
            if depsgraph.id_type_updated('MATERIAL'):
                print("Materials updated")

        # Loop over all object instances in the scene.
        if first_time or depsgraph.id_type_updated('OBJECT'):
            for instance in depsgraph.object_instances:
                pass

    # For viewport renders, this method is called whenever Blender redraws
    # the 3D viewport. The renderer is expected to quickly draw the render
    # with OpenGL, and not perform other expensive work.
    # Blender will draw overlays for selection and editing on top of the
    # rendered image automatically.
    def view_draw(self, context, depsgraph):
        region = context.region
        scene = depsgraph.scene

        # Get viewport dimensions
        dimensions = region.width, region.height

        # Bind shader that converts from scene linear to display space,
        gpu.state.blend_set('ALPHA_PREMULT')
        self.bind_display_space_shader(scene)

        if not self.draw_data or self.draw_data.dimensions != dimensions:
            self.draw_data = CustomDrawData(dimensions)

        self.draw_data.draw()

        self.unbind_display_space_shader()
        gpu.state.blend_set('NONE')

class NewEngineNodeTree(bpy.types.Operator):
    bl_idname = "dream_textures.new_engine_node_tree"
    bl_label = "New Node Tree"

    def execute(self, context):
        bpy.ops.node.new_node_tree(type="DreamTexturesNodeTree")
        return {'FINISHED'}

def draw_device(self, context):
    scene = context.scene
    layout = self.layout
    layout.use_property_split = True
    layout.use_property_decorate = False

    if context.engine == DreamTexturesRenderEngine.bl_idname:
        layout.template_ID(scene.dream_textures_render_engine, "node_tree", text="Node Tree", new=NewEngineNodeTree.bl_idname)

def _poll_node_tree(self, value):
    return value.bl_idname == "DreamTexturesNodeTree"
class DreamTexturesRenderEngineProperties(bpy.types.PropertyGroup):
    node_tree: bpy.props.PointerProperty(type=DreamTexturesNodeTree, name="Node Tree", poll=_poll_node_tree)

def engine_panels():
    bpy.types.RENDER_PT_output.COMPAT_ENGINES.add(DreamTexturesRenderEngine.bl_idname)
    bpy.types.RENDER_PT_color_management.COMPAT_ENGINES.add(DreamTexturesRenderEngine.bl_idname)
    bpy.types.DATA_PT_lens.COMPAT_ENGINES.add(DreamTexturesRenderEngine.bl_idname)
    def get_prompt(context):
        return context.scene.dream_textures_engine_prompt
    class RenderPanel(bpy.types.Panel, RenderButtonsPanel):
        COMPAT_ENGINES = {DreamTexturesRenderEngine.bl_idname}
        def draw(self, context):
            self.layout.use_property_decorate = True
    class OutputPanel(bpy.types.Panel, RenderOutputButtonsPanel):
        COMPAT_ENGINES = {DreamTexturesRenderEngine.bl_idname}

        def draw(self, context):
            self.layout.use_property_decorate = True

    # Render Properties
    yield from optimization_panels(RenderPanel, 'engine', get_prompt, "")

    # Output Properties
    class FormatPanel(OutputPanel):
        """Create a subpanel for format options"""
        bl_idname = f"DREAM_PT_dream_panel_format_engine"
        bl_label = "Format"

        def draw(self, context):
            super().draw(context)
            layout = self.layout
            layout.use_property_split = True

            col = layout.column(align=True)
            col.prop(context.scene.render, "resolution_x")
            col.prop(context.scene.render, "resolution_y", text="Y")
    yield FormatPanel

    # Bone properties
    class OpenPoseArmaturePanel(bpy.types.Panel):
        bl_idname = "DREAM_PT_dream_textures_armature_openpose"
        bl_label = "OpenPose"
        bl_space_type = 'PROPERTIES'
        bl_region_type = 'WINDOW'
        bl_context = "data"

        @classmethod
        def poll(cls, context):
            return context.armature
        
        def draw_header(self, context):
            bone = context.bone or context.edit_bone
            if bone:
                self.layout.prop(bone.dream_textures_openpose, "enabled", text="")

        def draw(self, context):
            layout = self.layout

            armature = context.armature

            p = armature.dream_textures_openpose

            row = layout.row()
            row.prop(p, "EAR_L", toggle=True)
            row.prop(p, "EYE_L", toggle=True)
            row.prop(p, "EYE_R", toggle=True)
            row.prop(p, "EAR_R", toggle=True)
            layout.prop(p, "NOSE", toggle=True)
            row = layout.row()
            row.prop(p, "SHOULDER_L", toggle=True)
            row.prop(p, "CHEST", toggle=True)
            row.prop(p, "SHOULDER_R", toggle=True)
            row = layout.row()
            row.prop(p, "ELBOW_L", toggle=True)
            row.separator()
            row.prop(p, "HIP_L", toggle=True)
            row.prop(p, "HIP_R", toggle=True)
            row.separator()
            row.prop(p, "ELBOW_R", toggle=True)
            row = layout.row()
            row.prop(p, "HAND_L", toggle=True)
            row.separator()
            row.prop(p, "KNEE_L", toggle=True)
            row.prop(p, "KNEE_R", toggle=True)
            row.separator()
            row.prop(p, "HAND_R", toggle=True)
            row = layout.row()
            row.prop(p, "FOOT_L", toggle=True)
            row.prop(p, "FOOT_R", toggle=True)

    yield OpenPoseArmaturePanel
    class OpenPoseBonePanel(bpy.types.Panel):
        bl_idname = "DREAM_PT_dream_textures_bone_openpose"
        bl_label = "OpenPose"
        bl_space_type = 'PROPERTIES'
        bl_region_type = 'WINDOW'
        bl_context = "bone"

        @classmethod
        def poll(cls, context):
            return context.bone and context.scene.render.engine == 'DREAM_TEXTURES'
        
        def draw_header(self, context):
            bone = context.bone
            if bone:
                self.layout.prop(bone.dream_textures_openpose, "enabled", text="")

        def draw(self, context):
            layout = self.layout
            layout.use_property_split = True

            bone = context.bone

            layout.enabled = bone.dream_textures_openpose.enabled
            layout.prop(bone.dream_textures_openpose, "bone")
            layout.prop(bone.dream_textures_openpose, "side")

    yield OpenPoseBonePanel