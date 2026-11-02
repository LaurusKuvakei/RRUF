# RRUF - Reactive Rig UI Framework.
# Copyright (C) 2025-2026 Laurus Kuvakei
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from importlib.resources import files as _pkg_files
import bpy.utils.previews


from . import rruf_core
from . import rruf_editor

_PREVIEW_COLLECTIONS = {}


class RRUF_PT_Setup_Main(bpy.types.Panel):
    """Parent panel for the RRUF editor tools."""
    bl_idname = "RRUF_PT_Setup_Main"
    bl_label = "Reactive Rig UI Framework"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RRUF"

    @classmethod
    def poll(cls, context):
        if not context.preferences.addons[__package__].preferences.pref_show_setup_panel:
            return False
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

    pref_show_setup_panel: bpy.props.BoolProperty(
        name="Show Editor",
        description="Show the Collection Editor used to build the rig UI",
        default=False
    )
    pref_polling_rate: bpy.props.FloatProperty(
        name="Polling Rate",
        description="How many times per second (Hz) RRUF checks the active rig for changes. "
                    "Higher values react faster but use more CPU",
        default=4.0, min=2.0, max=8.0
    )
    pref_max_nesting: bpy.props.IntProperty(name="Depth", default=8, min=1, max=16)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.prop(self, "pref_show_setup_panel", toggle=True, icon='TOOL_SETTINGS')
        col.separator()
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
        rruf_core.limited_redraw()

        try:
            _reset_popup_shortcut(context)
        except Exception as e:
            self.report({'WARNING'}, f"Could not reset the shortcut: {e}")

        self.report({'INFO'}, "RRUF Preferences and shortcut reset to defaults.")
        return {'FINISHED'}


def register():
    bpy.utils.register_class(RRUF_UR_Preferences)
    bpy.utils.register_class(RRUF_OT_reset_prefs)
    bpy.utils.register_class(RRUF_OT_restore_shortcut)
    rruf_core.register()
    bpy.utils.register_class(RRUF_PT_Setup_Main)
    rruf_editor.register()

    pcoll = bpy.utils.previews.new()
    _PREVIEW_COLLECTIONS["main"] = pcoll
    lp = _pkg_files(__package__) / "icon.png"
    if lp.is_file(): pcoll.load("rruf_logo", str(lp), 'IMAGE')
    rruf_editor.preview_collections = _PREVIEW_COLLECTIONS

def unregister():
    for p in _PREVIEW_COLLECTIONS.values(): bpy.utils.previews.remove(p)
    _PREVIEW_COLLECTIONS.clear()
    _remove_user_popup_items()
    rruf_editor.unregister()
    bpy.utils.unregister_class(RRUF_PT_Setup_Main)
    rruf_core.unregister()
    bpy.utils.unregister_class(RRUF_OT_restore_shortcut)
    bpy.utils.unregister_class(RRUF_OT_reset_prefs)
    bpy.utils.unregister_class(RRUF_UR_Preferences)
