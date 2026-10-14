# RRUF - Reactive Rig UI Framework.
# Copyright (C) 2025-2026 Laurus Kuvakei
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import os
import importlib
import traceback
from collections import OrderedDict
import bpy.utils.previews


# On reload these names already exist from the previous import.
if "rruf_core" in locals():
    importlib.reload(rruf_core)  # noqa: F821
    importlib.reload(rruf_editor)  # noqa: F821
else:
    from . import rruf_core
    from . import rruf_editor

_PREVIEW_COLLECTIONS = {}
_MAX_PREVIEW_CACHE_SIZE = 500


# Keep rruf_core.py usable as a standalone script embedded in a .blend.
# The installed extension adds editor synchronization, debug polling, and
# preview-cache separation by wrapping this package's own core functions.
# This keeps extension-specific behavior out of the standalone core.
#
# Patches are applied once and originals are retained for restoration.
# Only RRUF's own module and classes are patched; Blender internals and
# other add-ons are not modified here.
# Restore patches after core cleanup so timer removal uses the function
# object that was actually registered.
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

# Debug mode relaxes the Pose Mode requirement only. Keep the armature and
# RRUF marker checks because drawing still expects configured rig data.
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

# Editor previews may parse names that are never committed to the rig.
# Keep those results in a separate bounded cache so they do not evict
# useful engine entries. The preview flag identifies editor queries
# without adding an editor dependency to the standalone core.
def _patch_syntax_cache_separation():
    engine = rruf_core.rruf_engine
    if not isinstance(getattr(engine, "preview_syntax_cache", None), OrderedDict):
        engine.preview_syntax_cache = OrderedDict()

    def build_parse(original_parse):
        def patched_parse(self, raw_name, tree_ctx=None, depth=0, is_editor_preview=False):
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

# Extend the installed core's timer with editor synchronization while
# keeping the standalone embedded core independent of the editor.
# Synchronization runs only when Show Editor is enabled and the active
# collection differs from the last collection loaded into the composer.
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


def _apply_panel_config(cls, tab_key):
    cfg = rruf_editor.PANEL_CONFIGS.get(tab_key, rruf_editor.PANEL_CONFIGS['Animation'])
    cls.bl_space_type = cfg['space']
    cls.bl_region_type = cfg['region']
    cls.bl_category = cfg['cat']
    cls.bl_context = cfg['context']

def _configure_panel_classes(prefs):
    _apply_panel_config(rruf_core.RRUF_PT_Main, prefs.pref_category_name)
    for p in (RRUF_PT_Setup_Main, rruf_editor.RRUF_PT_Setup):
        _apply_panel_config(p, prefs.pref_setup_category_name)

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

    _configure_panel_classes(self)

    for p in reversed(core_panels):
        try: bpy.utils.register_class(p)
        except Exception: pass

    for p in setup_panels:
        try: bpy.utils.register_class(p)
        except Exception: pass

    rruf_core.limited_redraw()

def _update_engine_constants(self, context):
    rruf_core.MAX_UI_NESTING = self.pref_max_nesting
    rruf_core.POLLING_RATE_HZ = self.pref_polling_rate


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
        return bool(context.object and context.object.type == 'ARMATURE' and context.object.data)

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
    pref_polling_rate: bpy.props.FloatProperty(
        name="Polling Rate",
        description="How many times per second (Hz) RRUF checks the active rig for changes. "
                    "Higher values react faster but use more CPU",
        default=4.0, min=0.5, max=16.0, update=_update_engine_constants
    )
    pref_max_nesting: bpy.props.IntProperty(name="Depth", default=8, min=1, max=16, update=_update_engine_constants)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.prop(self, "pref_show_setup_panel", toggle=True, icon='TOOL_SETTINGS')
        if self.pref_show_setup_panel:
            col.prop(self, "pref_setup_category_name")
        col.separator()
        col.prop(self, "pref_category_name")
        col.prop(self, "pref_polling_rate")
        col.prop(self, "pref_max_nesting")

        layout.separator()
        self._draw_keymap(context, layout)

        layout.separator()
        row = layout.row()
        row.scale_y = 1.2
        row.operator("rruf.reset_prefs", icon='FILE_REFRESH')

    # Show the popup shortcut registered by the core so it can be rebound or
    # disabled here. It is drawn without Blender's remove (X) button, because a
    # removed add-on shortcut cannot be re-added from the preferences. If it
    # was removed anyway (older RRUF version, or the Keymap editor), offer a
    # restore button. Edits go to the user keyconfig, which Blender saves with
    # the user preferences, so the core's registration code stays unchanged.
    def _draw_keymap(self, context, layout):
        box = layout.box()
        box.label(text="Shortcut", icon='KEYINGSET')

        kmi = _find_popup_kmi(context)
        if kmi is None:
            if context.window_manager.keyconfigs.user is None:
                box.label(text="Shortcuts are unavailable in background mode", icon='INFO')
                return
            row = box.row()
            row.label(text="Quick Menu shortcut was removed", icon='INFO')
            row.operator("rruf.restore_shortcut", icon='LOOP_BACK')
            return

        row = box.row(align=True)
        row.prop(kmi, "active", text="")
        sub = row.row(align=True)
        sub.active = kmi.active
        sub.label(text="Quick Menu (Pose Mode)")
        sub.prop(kmi, "type", text="", full_event=True)


def _find_popup_kmi(context):
    kc = context.window_manager.keyconfigs.user
    km = kc.keymaps.get('Pose') if kc else None
    if not km:
        return None
    idname = rruf_core.RRUF_OT_popup_ui.bl_idname
    return next((k for k in km.keymap_items if k.idname == idname), None)


# Put the Quick Menu shortcut back to its default key: re-create it if it
# was removed, reset a rebound or disabled one, and drop duplicates.
# Returns False when no user keyconfig exists (background mode).
def _reset_popup_shortcut(context):
    kc = context.window_manager.keyconfigs.user
    if kc is None:
        return False
    km = kc.keymaps.get('Pose') or kc.keymaps.new(name='Pose', space_type='EMPTY')
    idname = rruf_core.RRUF_OT_popup_ui.bl_idname
    items = [k for k in km.keymap_items if k.idname == idname]
    # Prefer the item registered by the core: restoring it drops the user's
    # edits instead of adding a new entry to the user preferences.
    keep = next((k for k in items if not k.is_user_defined), None) or (items[0] if items else None)
    for k in items:
        if k is not keep:
            km.keymap_items.remove(k)
    if keep is not None and not keep.is_user_defined:
        km.restore_item_to_default(keep)
        keep.active = True
        return True
    if keep is None:
        # Removed with the X button: Blender keeps hiding the core's item
        # until the whole Pose keymap is reset, so add a user item instead.
        # unregister() removes it, so it never outlives the extension.
        keep = km.keymap_items.new(idname, **rruf_core.POPUP_KEYBIND)
    keep.active = True
    keep.oskey = False
    keep.any = False
    keep.key_modifier = 'NONE'
    for attr, value in rruf_core.POPUP_KEYBIND.items():
        setattr(keep, attr, value)
    return True


# Remove the user-defined Quick Menu items that Restore Shortcut may have
# added, so no shortcut to a missing operator stays in the user preferences.
def _remove_user_popup_items():
    wm = getattr(bpy.context, "window_manager", None)
    kc = wm.keyconfigs.user if wm else None
    km = kc.keymaps.get('Pose') if kc else None
    if not km:
        return
    idname = rruf_core.RRUF_OT_popup_ui.bl_idname
    for k in [k for k in km.keymap_items if k.idname == idname and k.is_user_defined]:
        km.keymap_items.remove(k)


class RRUF_OT_restore_shortcut(bpy.types.Operator):
    """Restore the Quick Menu shortcut to its default key"""
    bl_idname = "rruf.restore_shortcut"
    bl_label = "Restore Shortcut"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        if not _reset_popup_shortcut(context):
            return {'CANCELLED'}
        self.report({'INFO'}, "RRUF shortcut restored.")
        return {'FINISHED'}

class RRUF_OT_reset_prefs(bpy.types.Operator):
    bl_idname = "rruf.reset_prefs"
    bl_label = "Reset to Defaults"
    bl_options = {'REGISTER'}

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

        try:
            _reset_popup_shortcut(context)
        except Exception as e:
            self.report({'WARNING'}, f"Could not reset the shortcut: {e}")

        self.report({'INFO'}, "RRUF Preferences and shortcut reset to defaults.")
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

    bpy.utils.register_class(RRUF_UR_Preferences)
    bpy.utils.register_class(RRUF_OT_reset_prefs)
    bpy.utils.register_class(RRUF_OT_restore_shortcut)

    try:
        prefs = bpy.context.preferences.addons[__package__].preferences
        _configure_panel_classes(prefs)
        _update_engine_constants(prefs, bpy.context)
    except Exception:
        pass

    rruf_core.register()

    bpy.utils.register_class(RRUF_PT_Setup_Main)

    rruf_editor.register()

    pcoll = bpy.utils.previews.new()
    _PREVIEW_COLLECTIONS["main"] = pcoll
    lp = os.path.join(os.path.dirname(__file__), "icon.png")
    if os.path.exists(lp): pcoll.load("rruf_logo", lp, 'IMAGE')
    rruf_editor.preview_collections = _PREVIEW_COLLECTIONS

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

    cleanup(_remove_user_popup_items)

    for cls in (RRUF_OT_restore_shortcut, RRUF_OT_reset_prefs, RRUF_UR_Preferences):
        if cls.is_registered:
            cleanup(lambda cls=cls: bpy.utils.unregister_class(cls))

    cleanup(rruf_editor.unregister)
    if RRUF_PT_Setup_Main.is_registered:
        cleanup(lambda: bpy.utils.unregister_class(RRUF_PT_Setup_Main))
    cleanup(rruf_core.unregister)
    # Stop the core before restoring its original functions: its registered
    # timer is the patched function, and cleanup must use that same object.
    cleanup(_restore_patches)
    if hasattr(rruf_core.rruf_engine, "preview_syntax_cache"):
        del rruf_core.rruf_engine.preview_syntax_cache
    if errors:
        raise errors[0]

if __name__ == "__main__":
    register()
