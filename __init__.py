# RRUF - Reactive Rig UI Framework.
# Copyright (C) 2025-2026 Laurus Kuvakei

import bpy
import os
import importlib
import sys
import traceback
from collections import OrderedDict
import bpy.utils.previews


if "rruf_core" in locals():
    importlib.reload(rruf_core)
    importlib.reload(rruf_editor)
else:
    from . import rruf_core
    from . import rruf_editor

_PREVIEW_COLLECTIONS = {}
_MAX_PREVIEW_CACHE_SIZE = 500


_ORIGINALS = {}
_MISSING = object()

def _install_patch(owner, attr, build_replacement):
    key = (owner, attr)
    if key in _ORIGINALS:
        return
    _ORIGINALS[key] = vars(owner).get(attr, _MISSING)
    setattr(owner, attr, build_replacement(getattr(owner, attr)))

def _restore_patches():
    for (owner, attr), raw in _ORIGINALS.items():
        if raw is _MISSING:
            try: delattr(owner, attr)
            except AttributeError: pass
        else:
            setattr(owner, attr, raw)
    _ORIGINALS.clear()

def _patch_core_poll():
    def build(original_poll):
        @classmethod
        def rruf_debug_poll(cls, context):
            try:
                prefs = context.preferences.addons[__package__].preferences
                if prefs.pref_debug_mode:
                    obj = context.object
                    return bool(obj and obj.type == 'ARMATURE' and obj.data
                                and obj.data.get(rruf_core.RRUF_TRIGGER_KEY))
            except Exception:
                pass
            return original_poll(context)
        return rruf_debug_poll

    _install_patch(rruf_core.RRUF_PT_Main, "poll", build)

def _patch_syntax_cache_separation():
    engine = rruf_core.rruf_engine
    if not isinstance(getattr(engine, "preview_syntax_cache", None), OrderedDict):
        engine.preview_syntax_cache = OrderedDict()

    editor_module_name = rruf_editor.__name__

    def build_parse(original_parse):
        def patched_parse(self, raw_name, tree_ctx=None, depth=0):
            try:
                is_editor_preview = sys._getframe(1).f_globals.get("__name__") == editor_module_name
            except Exception:
                is_editor_preview = False

            if not is_editor_preview:
                return original_parse(self, raw_name, tree_ctx, depth)

            cache_key = (raw_name, tree_ctx, depth)
            preview_cache = self.preview_syntax_cache
            if cache_key in preview_cache:
                preview_cache.move_to_end(cache_key)
                return preview_cache[cache_key]

            was_already_engine_cached = cache_key in self.syntax_cache
            result = original_parse(self, raw_name, tree_ctx, depth)

            if not was_already_engine_cached:
                self.syntax_cache.pop(cache_key, None)
                preview_cache[cache_key] = result
                if len(preview_cache) > _MAX_PREVIEW_CACHE_SIZE:
                    preview_cache.popitem(last=False)

            return result
        return patched_parse

    def build_purge(original_purge):
        def patched_purge(self):
            original_purge(self)
            cache = getattr(self, "preview_syntax_cache", None)
            if cache is not None:
                cache.clear()
        return patched_purge

    _install_patch(rruf_core.RRUF_Core_Engine, "parse_collection_syntax", build_parse)
    _install_patch(rruf_core.RRUF_Core_Engine, "purge_all", build_purge)

def _patch_main_loop():
    def build(original_loop):
        def rruf_unified_main_loop():
            interval = original_loop()

            try:
                prefs = bpy.context.preferences.addons[__package__].preferences
                if prefs.pref_show_setup_panel:
                    obj = bpy.context.active_object
                    if obj and obj.type == 'ARMATURE' and obj.data.get(rruf_core.RRUF_TRIGGER_KEY):
                        coll = rruf_editor.get_active_bone_collection(obj.data)
                        props = bpy.context.scene.rruf_editor

                        if coll and props.last_target_name != coll.name:
                            rruf_editor.sync_ui_props_to_collection(props, coll)
                            rruf_core.limited_redraw()
            except Exception:
                pass

            return interval
        return rruf_unified_main_loop

    _install_patch(rruf_core, "rruf_main_loop_timer", build)


def _update_panel_locations(self, context):
    core_panels = [
        rruf_core.RRUF_PT_Main
    ]

    setup_panels = [
        RRUF_PT_Setup_Main,
        rruf_editor.RRUF_PT_Setup
    ]

    for p in core_panels + setup_panels:
        try: bpy.utils.unregister_class(p)
        except Exception: pass

    def _apply_config(cls, tab_key):
        cfg = rruf_editor.PANEL_CONFIGS.get(tab_key, rruf_editor.PANEL_CONFIGS['Animation'])
        cls.bl_space_type = cfg['space']
        cls.bl_region_type = cfg['region']
        cls.bl_category = cfg['cat']
        cls.bl_context = 'constraint' if tab_key == 'Constraints' else ''

    for p in core_panels:
        _apply_config(p, self.pref_category_name)

    for p in setup_panels:
        _apply_config(p, self.pref_setup_category_name)

    for p in reversed(core_panels):
        try: bpy.utils.register_class(p)
        except Exception: pass

    for p in setup_panels:
        try: bpy.utils.register_class(p)
        except Exception: pass

    rruf_core.limited_redraw()

def _update_engine_constants(self, context):
    rruf_core.MAX_UI_NESTING = self.pref_max_nesting
    rruf_core.FIXED_FPS = self.pref_refresh_rate


class RRUF_PT_Setup_Main(bpy.types.Panel):
    """The Master Parent Panel for all Developer Tools"""
    bl_idname = "RRUF_PT_Setup_Main"
    bl_label = "Reactive Rig UI Framework"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RRUF"

    @classmethod
    def poll(cls, context):
        try:
            pref = context.preferences.addons[__package__].preferences
            if not pref.pref_show_setup_panel:
                return False
        except Exception:
            pass
        return context.object and context.object.type == 'ARMATURE'

    def draw(self, context):
        layout = self.layout
        data = context.object.data

        if not data.get(rruf_core.RRUF_TRIGGER_KEY, False):
            pcoll = rruf_editor.preview_collections.get("main")
            if pcoll and "rruf_logo" in pcoll:
                row = layout.row()
                row.alignment = 'CENTER'
                row.template_icon(icon_value=pcoll["rruf_logo"].icon_id, scale=8.0)

            layout.separator()
            button_row = layout.row()
            button_row.alignment = 'CENTER'
            button_row.operator("rruf.enable", icon='PLAY', text="Enable RRUF for this armature")

class RRUF_UR_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    pref_debug_mode: bpy.props.BoolProperty(
        name="Debug Mode",
        description="Show UI outside of Pose Mode (requires rig data)",
        default=False
    )
    pref_show_setup_panel: bpy.props.BoolProperty(
        name="Show Editor",
        description="Show the Collection Editor used to build the rig UI",
        default=False
    )
    pref_setup_category_name: bpy.props.EnumProperty(
        name="Setup Tab", items=rruf_editor.TAB_ITEMS, default="RRUF", update=_update_panel_locations
    )
    pref_category_name: bpy.props.EnumProperty(
        name="Main Tab", items=rruf_editor.TAB_ITEMS, default="Tool", update=_update_panel_locations
    )
    pref_refresh_rate: bpy.props.FloatProperty(name="FPS", default=4.0, min=0.5, max=16.0, update=_update_engine_constants)
    pref_max_nesting: bpy.props.IntProperty(name="Depth", default=8, min=1, max=16, update=_update_engine_constants)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.prop(self, "pref_show_setup_panel", toggle=True, icon='TOOL_SETTINGS')
        if self.pref_show_setup_panel:
            col.prop(self, "pref_setup_category_name")
        col.separator()
        col.prop(self, "pref_category_name")
        # col.prop(self, "pref_debug_mode")
        col.prop(self, "pref_refresh_rate")
        col.prop(self, "pref_max_nesting")

        layout.separator()
        row = layout.row()
        row.scale_y = 1.2
        row.operator("rruf.reset_prefs", icon='FILE_REFRESH')

class RRUF_OT_reset_prefs(bpy.types.Operator):
    bl_idname = "rruf.reset_prefs"
    bl_label = "Reset to Defaults"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences

        for prop in prefs.bl_rna.properties:
            if not prop.is_readonly:
                prefs.property_unset(prop.identifier)
        try:
            _update_panel_locations(prefs, context)
            _update_engine_constants(prefs, context)
        except Exception as e:
            self.report({'WARNING'}, f"Could not refresh UI after reset: {e}")

        self.report({'INFO'}, "RRUF Preferences reset to defaults.")
        return {'FINISHED'}


def register():
    try:
        _register_body()
    except Exception:
        try:
            unregister()
        except Exception:
            traceback.print_exc()
        raise


def _register_body():
    _patch_main_loop()
    _patch_core_poll()
    _patch_syntax_cache_separation()

    rruf_core.register()

    bpy.utils.register_class(RRUF_PT_Setup_Main)

    rruf_editor.register()

    bpy.utils.register_class(RRUF_UR_Preferences)
    bpy.utils.register_class(RRUF_OT_reset_prefs)

    pcoll = bpy.utils.previews.new()
    _PREVIEW_COLLECTIONS["main"] = pcoll
    lp = os.path.join(os.path.dirname(__file__), "icon.png")
    if os.path.exists(lp): pcoll.load("rruf_logo", lp, 'IMAGE')
    rruf_editor.preview_collections = _PREVIEW_COLLECTIONS

    try:
        _update_panel_locations(bpy.context.preferences.addons[__package__].preferences, bpy.context)
        _update_engine_constants(bpy.context.preferences.addons[__package__].preferences, bpy.context)
    except Exception:
        pass

def unregister():
    errors = []

    def cleanup(action):
        try:
            action()
        except Exception as e:
            errors.append(e)
            traceback.print_exc()

    for p in list(_PREVIEW_COLLECTIONS.values()):
        cleanup(lambda p=p: bpy.utils.previews.remove(p))
    _PREVIEW_COLLECTIONS.clear()

    for cls in (RRUF_OT_reset_prefs, RRUF_UR_Preferences):
        if cls.is_registered:
            cleanup(lambda cls=cls: bpy.utils.unregister_class(cls))

    cleanup(rruf_editor.unregister)
    if RRUF_PT_Setup_Main.is_registered:
        cleanup(lambda: bpy.utils.unregister_class(RRUF_PT_Setup_Main))
    cleanup(rruf_core.unregister)
    cleanup(_restore_patches)
    if hasattr(rruf_core.rruf_engine, "preview_syntax_cache"):
        del rruf_core.rruf_engine.preview_syntax_cache
    if errors:
        raise errors[0]

if __name__ == "__main__":
    register()
