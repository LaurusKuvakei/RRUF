import bpy
import json
import os
import ast
import importlib
import bpy.utils.previews
from bpy_extras.io_utils import ExportHelper, ImportHelper
from . import rruf_core

if "bpy" in locals():
    importlib.reload(rruf_core)

# Global dictionary to safely store custom icons and prevent memory leaks
custom_icons_dict = {}

# <<< SECTION 1: CONFIGURATION & DYNAMIC RELOCATION >>>

PANEL_CONFIGS = {
    'Tool':        {'space': 'VIEW_3D',  'region': 'UI', 'context': '', 'cat': 'Tool'},
    'Animation':   {'space': 'VIEW_3D',  'region': 'UI', 'context': '', 'cat': 'Animation'},
    'RRUF':        {'space': 'VIEW_3D',  'region': 'UI', 'context': '', 'cat': 'RRUF'},
}

TAB_ITEMS = (
    ('Tool', "Tool", "Standard Tool"),
    ('Animation', "Animation", "Standard Animation"),
    ('RRUF', "RRUF", "Default"),
)

def _update_panel_locations(self, context):
    # Dynamically relocates active UI panels across different viewport tabs based on user preference.
    panels = [rruf_core.RRUF_PT_Main, RRUF_PT_Lite_Toolset]
    cfg = PANEL_CONFIGS.get(self.pref_category_name, PANEL_CONFIGS['Tool'])
    
    for p in panels:
        # Phase 1: State Verification. Check if the class is currently loaded in Blender's RNA registry.
        is_registered = hasattr(bpy.types, p.__name__)
        
        # Phase 2: Unregistration. Blender does not allow modifying bl_category on live classes.
        # The class must be temporarily destroyed to unlock the layout properties.
        if is_registered:
            bpy.utils.unregister_class(p)
            
        # Phase 3: Property Injection. Overwrite the routing variables.
        p.bl_space_type = cfg['space']
        p.bl_region_type = cfg['region']
        p.bl_category = cfg['cat']
        p.bl_context = cfg['context']
        
        # Phase 4: Re-registration. Rebuild the class within the new target tab.
        if is_registered:
            bpy.utils.register_class(p)
            
    rruf_core.limited_redraw()


# <<< SECTION 2: LITE IMPORT/EXPORT HELPERS >>>
def _get_root_category_tag(coll):
    walker = coll
    while walker:
        ctx = rruf_core._get_root_context(walker.name)
        if ctx:
            for primary_tag, cfg in rruf_core.TAG_CONFIG.items():
                if cfg.get("root_context") == ctx: 
                    return f"({primary_tag})"
        if walker.parent: 
            walker = walker.parent
        else: 
            break
    return None

def _serialize_collection_properties(coll):
    # Extracts custom property values and their associated UI configuration metadata (min/max/default).
    prop_storage = {}
    
    def clean_data(data):
        # Normalizes float precision and array structures to ensure clean JSON serialization.
        if isinstance(data, float): return round(data, 4)
        if hasattr(data, "__len__") and not isinstance(data, str):
            return [round(x, 4) if isinstance(x, float) else x for x in data]
        return data

    for key in coll.keys():
        if key == "_RNA_UI": continue
        val = coll[key]
        
        # Isolate valid primitives and arrays, rejecting complex Blender objects or pointers.
        if isinstance(val, (int, float, str, bool)) or (hasattr(val, "__len__") and not isinstance(val, str)):
            try:
                # Access the underlying RNA data layer to grab slider limits and default values.
                cfg = coll.id_properties_ui(key).as_dict()
                ui_cfg = {k: clean_data(v) for k, v in cfg.items()}
            except: 
                ui_cfg = {}
            prop_storage[key] = {"value": clean_data(val), "config": ui_cfg}
            
    return prop_storage

def _deserialize_collection_properties(coll, prop_data):
    for key, data in prop_data.items():
        coll[key] = data.get("value")
        
        config = data.get("config", {})
        if config:
            try:
                ui_mgr = coll.id_properties_ui(key)
                banned_keys = {"is_overridable_library"}
                dynamic_payload = {k: v for k, v in config.items() if k not in banned_keys}
                
                if dynamic_payload:
                    ui_mgr.update(**dynamic_payload)
            except Exception as e: 
                print(f"RRUF Lite Deserialization Error for '{key}': {e}")

def _serialize_armature_structure(armature, include_bones, include_props):
    tree_data = []
    for coll in armature.collections_all:
        node = {
            "name": coll.name, 
            "parent": coll.parent.name if coll.parent else None,
            "root_tag": _get_root_category_tag(coll)
        }
        if include_bones: node["bones"] = [b.name for b in coll.bones]
        if include_props:
            props = _serialize_collection_properties(coll)
            if props: node["props"] = props
        tree_data.append(node)
    return {"meta": {"source": armature.name}, "tree": tree_data}

def _deserialize_armature_structure(armature, snapshot_data, props):
    # Reconstructs armature collection hierarchies and properties from a JSON snapshot.
    # Operates destructively based on user-defined rebuild flags.
    if not isinstance(snapshot_data, dict) or "tree" not in snapshot_data: 
        return False

    # Phase 1: Context Resolution. Map the user's UI selection (Vis/Props/Snaps/Internals) 
    # to the corresponding internal root tags.
    category_gate = {}
    for tag_name, cfg in rruf_core.TAG_CONFIG.items():
        ctx = cfg.get("root_context")
        if ctx == rruf_core.CTX_VIS:     category_gate[f"({tag_name})"] = props.overwrite_vis
        elif ctx == rruf_core.CTX_PROP:  category_gate[f"({tag_name})"] = props.overwrite_props
        elif ctx == rruf_core.CTX_SNAP:  category_gate[f"({tag_name})"] = props.overwrite_snaps
        elif ctx == rruf_core.CTX_CWI:   category_gate[f"({tag_name})"] = props.overwrite_internals
    
    active_targets = [tag for tag, active in category_gate.items() if active]
    total_wipe_requested = len(active_targets) == 4 and props.use_rebuild

    # Phase 2: Topology Wiping. Purge existing collections if the rebuild flag is active.
    # Executes either a total armature wipe or a targeted category wipe.
    if total_wipe_requested:
        to_remove = list(armature.collections_all)
        for c in reversed(to_remove):
            try: armature.collections.remove(c)
            except: pass
    elif props.use_rebuild:
        to_remove = []
        for c in armature.collections_all:
            root_tag = _get_root_category_tag(c)
            if root_tag in active_targets:
                to_remove.append(c)
        for c in reversed(to_remove):
            try: armature.collections.remove(c)
            except: pass

    bpy.context.view_layer.update() 

    # Phase 3: Root Initialization. Ensure base category collections exist before parenting.
    created_map = {c.name: c for c in armature.collections_all}
    for tag in active_targets:
        if tag not in created_map:
            created_map[tag] = armature.collections.new(name=tag)

    # Phase 4: Hierarchy Reconstruction. Generate missing child collections mapped to active categories.
    for node in snapshot_data["tree"]:
        nm = str(node["name"])
        root_tag = node.get("root_tag")
        
        if root_tag not in active_targets:
            continue

        if nm not in created_map:
            c = armature.collections.new(name=nm)
            created_map[nm] = c
            
    bpy.context.view_layer.update()

    # Phase 5: Data Restoration. Re-establish parent-child links, assign bones, and inject custom properties.
    for node in snapshot_data["tree"]:
        nm = node["name"]
        root_tag = node.get("root_tag")

        if root_tag not in active_targets:
            continue
            
        if nm not in created_map: continue
        c = created_map[nm]

        if p := node.get("parent"):
            if p in created_map:
                try: c.parent = created_map[p]
                except: pass
        
        if "bones" in node:
            for b in node["bones"]:
                if bone := armature.bones.get(b): 
                    c.assign(bone)
                    
        if "props" in node: 
            _deserialize_collection_properties(c, node["props"])
            
    return True


# <<< SECTION 3: OPERATORS >>>
class RRUF_OT_enable(bpy.types.Operator):
    bl_idname = "rruf.enable"
    bl_label = "Enable RRUF"
    bl_description = "Initialize RRUF data on this armature"
    def execute(self, context):
        context.active_object.data[rruf_core.RRUF_TRIGGER_KEY] = True
        return {'FINISHED'}

class RRUF_OT_convert_syntax(bpy.types.Operator):
    bl_idname = "rruf.convert_syntax"
    bl_label = "Convert Syntax"
    bl_description = "Convert rig tags between Deflated (DI) and Inflated (DISPLAYS) formats"
    bl_options = {'UNDO'}

    target_mode: bpy.props.EnumProperty(
        items=[
            ('DEFLATE', "Deflate Tags", "Compress full names into micro-codes (e.g., DISPLAYS -> DI)"), 
            ('INFLATE', "Inflate Tags", "Expand micro-codes into full names (e.g., DI -> DISPLAYS)")
        ],
        default='DEFLATE'
    )

    def execute(self, context):
        # Scans and converts collection name strings between legacy (inflated) and modern (deflated) macro syntax.
        arm = context.active_object.data
        changes_made = 0
        skipped_collections = 0
        
        # Phase 1: Iterative Scanning. Traverse all collections in the armature hierarchy.
        for coll in arm.collections_all:
            new_name = coll.name
            
            # Phase 2: String Resolution. Evaluate current names against the internal macro configuration dictionary.
            for primary, config in rruf_core.TAG_CONFIG.items():
                if "aliases" in config:
                    legacy_str = f"({config['aliases'][0]})"
                    modern_str = f"({primary})"
                    
                    if self.target_mode == 'DEFLATE':
                        if legacy_str in new_name:
                            new_name = new_name.replace(legacy_str, modern_str)
                    elif self.target_mode == 'INFLATE':
                        if modern_str in new_name:
                            new_name = new_name.replace(modern_str, legacy_str)
                            
            # Phase 3: Application & Validation. Apply updated strings while enforcing Blender's internal constraints.
            if new_name != coll.name:
                # Blender imposes a strict hard-coded 63-character byte limit on Collection names.
                # Abort inflation on specific nodes if expansion causes buffer overflow.
                if self.target_mode == 'INFLATE' and len(new_name) > 63:
                    self.report({'WARNING'}, f"Skipping '{coll.name}': Inflated name exceeds 63 char limit.")
                    skipped_collections += 1
                    continue

                coll.name = new_name
                changes_made += 1
                
        # Phase 4: Cache Purge. Force the engine to re-evaluate the entire tree since macro keys have mutated.
        if changes_made > 0:
            rruf_core.rruf_engine.purge_all()
            context.active_object.update_tag()
            msg = f"Successfully {self.target_mode.lower()}d {changes_made} collections!"
            if skipped_collections > 0:
                msg += f" (Skipped {skipped_collections} due to length limits)"
            self.report({'INFO'}, msg)
        elif skipped_collections > 0:
            self.report({'WARNING'}, f"Skipped {skipped_collections} collections due to 63 char name limit.")
        else:
            self.report({'INFO'}, "Rig is already in the target format.")
            
        return {'FINISHED'}

class RRUF_OT_export_snapshot(bpy.types.Operator, ExportHelper):
    bl_idname = "rruf.export_snapshot"
    bl_label = "Export Snapshot"
    bl_description = "Save the current UI layout and property values to a JSON snapshot"
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})
    
    def invoke(self, context, event):
        obj = context.active_object
        self.filepath = f"{obj.name.replace(' ', '_')}_RRUF_Snapshot.json" if obj else "rruf.json"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}
        
    def execute(self, context):
        props = context.scene.rruf_lite_io
        data = _serialize_armature_structure(context.active_object.data, props.include_bones, props.include_props)
        with open(self.filepath, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)
        return {'FINISHED'}

class RRUF_OT_import_gatekeeper(bpy.types.Operator):
    bl_idname = "rruf.import_gatekeeper"
    bl_label = "Confirm Import?"
    bl_description = "WARNING: This may wipe current bone collections and rebuild them from the file"
    
    def invoke(self, context, event): 
        return context.window_manager.invoke_props_dialog(self, width=380)
        
    def draw(self, context):
        props = context.scene.rruf_lite_io
        layout = self.layout
        col = layout.column(align=True)
        col.alert = True
        
        is_total = all([props.overwrite_vis, props.overwrite_props, props.overwrite_snaps, props.overwrite_internals])
        
        if props.use_rebuild:
            col.label(text="WARNING: Rebuild is ACTIVE", icon='ERROR')
            col.alert = False
            col.separator()
            if is_total:
                col.label(text="This will completely WIPE ALL bone collections")
                col.label(text="in this armature before importing the snapshot.")
            else:
                col.label(text="This will WIPE all collections within your selected")
                col.label(text="categories (Vis/Props/etc.) before importing.")
        else:
            col.label(text="Warning: Selective Import", icon='ERROR')
            col.alert = False
            col.separator()
            col.label(text="This will overwrite existing collections that match")
            col.label(text="the imported data in your active categories.")
            
        col.separator()
        col.label(text="Are you sure you want to proceed?", icon='QUESTION')

    def execute(self, context):
        bpy.ops.rruf.import_snapshot('INVOKE_DEFAULT')
        return {'FINISHED'}

class RRUF_OT_import_snapshot(bpy.types.Operator, ImportHelper):
    bl_idname = "rruf.import_snapshot"
    bl_label = "Import Snapshot"
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})
    
    def execute(self, context):
        props = context.scene.rruf_lite_io
        with open(self.filepath, 'r', encoding='utf-8') as f: snapshot = json.load(f)
        if _deserialize_armature_structure(context.active_object.data, snapshot, props):
            rruf_core.rruf_engine.purge_all()
            rruf_core.limited_redraw()
            bpy.ops.ed.undo_push(message="RRUF Import Snapshot")
            return {'FINISHED'}
        return {'CANCELLED'}

import os
import ast

class RRUF_OT_embed_core(bpy.types.Operator):
    bl_idname = "rruf.embed_core"
    bl_label = "Embed Core"
    bl_description = "Embed and clean the rruf_core.py engine into this blend file"
    
    def invoke(self, context, event): 
        return context.window_manager.invoke_props_dialog(self, width=380)
        
    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.alert = True
        col.label(text="Embedding Core Engine", icon='INFO')
        col.alert = False
        col.separator()
        col.label(text="This will package 'rruf_core.py' into the current .blend file.")
        col.label(text="All developer comments and empty lines will be stripped.")
        col.separator()
        col.label(text="Are you sure you want to proceed?", icon='QUESTION')

    def execute(self, context):
        # Extracts, minifies, and injects the standalone core engine directly into the active .blend file.
        src_core = os.path.join(os.path.dirname(__file__), "rruf_core.py")
        try:
            with open(src_core, 'r', encoding='utf-8') as f: 
                raw_code = f.read()
            
            # Phase 1: AST Processing. Parse the raw source into a syntax tree and unparse it back to a string.
            # This implicitly strips all developer comments and blank lines, shrinking the payload size.
            parsed_tree = ast.parse(raw_code)
            clean_code = ast.unparse(parsed_tree)
            
            # Phase 2: Blender Text Injection. Overwrite existing internal scripts to prevent duplication.
            name_core = "rruf_core.py"
            if name_core in bpy.data.texts: 
                bpy.data.texts.remove(bpy.data.texts[name_core])
            
            txt_core = bpy.data.texts.new(name_core)
            txt_core.write(clean_code)
            txt_core.use_module = True
            
        except Exception as e:
            self.report({'ERROR'}, f"Failed to embed rruf_core.py: {e}")
            return {'CANCELLED'}

        self.report({'INFO'}, "RRUF Core embedded and cleaned successfully.")
        return {'FINISHED'}


# <<< SECTION 4: UI & REGISTRATION >>>

def _patched_popup_draw(self, context):
    # Hijacks the core popup UI draw method to dynamically inject custom icon assets.
    # Standard add-on icons cannot be safely registered in the embedded core file,
    # necessitating this runtime monkey-patch.
    layout = self.layout
    header = layout.row()
    header.alignment = 'CENTER'

    # Attempt to retrieve the registered custom image matrix from the global dictionary.
    pcoll = custom_icons_dict.get("main")
    if pcoll and "rruf_logo" in pcoll:
        header.label(text="Reactive Rig UI", icon_value=pcoll["rruf_logo"].icon_id)
    else:
        # Fallback to internal Blender icon if the custom asset fails to load.
        header.label(text="Reactive Rig UI", icon='OUTLINER_OB_ARMATURE')
        
    layout.separator() 
    rruf_core.draw_rruf_main_ui(layout, context)


class RRUF_Lite_IO_Props(bpy.types.PropertyGroup):
    include_bones: bpy.props.BoolProperty(name="Bones", default=True)
    include_props: bpy.props.BoolProperty(name="Properties", default=True)
    
    overwrite_vis: bpy.props.BoolProperty(name="Vis", default=True)
    overwrite_props: bpy.props.BoolProperty(name="Props", default=True)
    overwrite_snaps: bpy.props.BoolProperty(name="Snaps", default=True)
    overwrite_internals: bpy.props.BoolProperty(name="Internals", default=True)

    use_rebuild: bpy.props.BoolProperty(
        name="Rebuild", 
        default=False, 
        description="Active: Wipe ALL selected categories before import. Inactive: Selective overwrite."
    )

class RRUF_PT_Lite_Toolset(bpy.types.Panel):
    bl_idname = "RRUF_PT_Lite_Toolset"
    bl_label = "RRUF Toolset Lite"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"

    @classmethod
    def poll(cls, context):
        try:
            prefs = context.preferences.addons[__package__].preferences
            return prefs.enable_toolset_lite and context.object and context.object.type == 'ARMATURE'
        except Exception:
            return False

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        props = context.scene.rruf_lite_io

        if not obj.data.get(rruf_core.RRUF_TRIGGER_KEY):
            layout.operator("rruf.enable", icon='PLAY')
            return

        b_syn = layout.box()
        b_syn.label(text="Syntax Converter", icon='FONT_DATA') 
        r_syn = b_syn.row(align=True)
        r_syn.operator("rruf.convert_syntax", text="Deflate", icon='FULLSCREEN_EXIT').target_mode = 'DEFLATE'
        r_syn.operator("rruf.convert_syntax", text="Inflate", icon='FULLSCREEN_ENTER').target_mode = 'INFLATE'

        b_exp = layout.box()
        b_exp.label(text="Export Options", icon='EXPORT')
        r_exp_opts = b_exp.row(align=True)
        r_exp_opts.prop(props, "include_bones")
        r_exp_opts.prop(props, "include_props")
        b_exp.operator("rruf.export_snapshot", text="Export Snapshot", icon='FILE_TICK')

        b_imp = layout.box()
        b_imp.label(text="Import Options", icon='IMPORT')
        col = b_imp.column(align=True)
        col.prop(props, "overwrite_vis", text="Vis", icon='HIDE_OFF')
        col.prop(props, "overwrite_props", text="Props", icon='SETTINGS')
        col.prop(props, "overwrite_snaps", text="Snaps", icon='SNAP_ON')
        col.prop(props, "overwrite_internals", text="Internals", icon='NODE_COMPOSITING')
        
        is_total = all([props.overwrite_vis, props.overwrite_props, props.overwrite_snaps, props.overwrite_internals])
        
        sub = col.box()
        if props.use_rebuild:
            warn_row = sub.row()
            warn_row.alert = True 
            label_text = "DANGER: TOTAL ARMATURE WIPE!" if is_total else "DANGER: WIPE ACTIVE CATEGORIES!"
            warn_row.label(text=label_text, icon='ERROR')
            btn_row = sub.row()
            btn_row.alert = True
            btn_row.prop(props, "use_rebuild", text="Rebuild", icon='TRASH', toggle=True)
        else:
            sub.prop(props, "use_rebuild", text="Rebuild", icon='TRASH', toggle=True)
            
        b_imp.operator("rruf.import_gatekeeper", text="Import Snapshot", icon='FILE_FOLDER')

        layout.separator()
        b_embed = layout.box()
        b_embed.label(text="Engine Injection", icon='FILE_SCRIPT')
        b_embed.operator("rruf.embed_core", text="Embed Core into .Blend", icon='TEXT')


class RRUF_UR_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    pref_category_name: bpy.props.EnumProperty(
        name="Main Tab Location", 
        items=TAB_ITEMS, 
        default="Tool",
        update=_update_panel_locations
    )

    enable_toolset_lite: bpy.props.BoolProperty(
        name="Enable Toolset Lite",
        description="Show the stripped-down Toolset Panel (I/O, Syntax, Injection) in the UI",
        default=False,
        update=_update_panel_locations
    )

    def draw(self, context):
        layout = self.layout
        
        # --- UI Settings ---
        col = layout.column(align=True)
        col.prop(self, "pref_category_name")
        col.separator()
        col.prop(self, "enable_toolset_lite", toggle=True, icon='SETTINGS')
        
        # --- Hotkeys Section ---
        layout.separator()
        box = layout.box()
        box.label(text="Hotkeys", icon='KEYINGSET')
        
        wm = context.window_manager
        kc = getattr(wm, "keyconfigs", None)
        
        if kc and kc.user:
            km = kc.user.keymaps.get('Pose')
            if km:
                for kmi in km.keymap_items:
                    if kmi.idname == "rruf.popup_ui":
                        row = box.row()
                        row.prop(kmi, "active", text="") 
                        row.label(text="Quick Menu Popup")
                        row.prop(kmi, "type", full_event=True, text="") 
                        break


_CLASSES = (
    RRUF_Lite_IO_Props,
    RRUF_OT_enable,
    RRUF_OT_convert_syntax,
    RRUF_OT_export_snapshot,
    RRUF_OT_import_gatekeeper,
    RRUF_OT_import_snapshot,
    RRUF_OT_embed_core,
    RRUF_PT_Lite_Toolset,
    RRUF_UR_Preferences
)

def register():
    # --- 1. PRE-FLIGHT PANEL CONFIGURATION ---
    # Retrieve stored user preferences to determine panel locations before classes are pushed to the registry.
    cfg = PANEL_CONFIGS['Tool']
    try:
        prefs = bpy.context.preferences.addons[__package__].preferences
        if prefs:
            cfg = PANEL_CONFIGS.get(prefs.pref_category_name, PANEL_CONFIGS['Tool'])
    except (AttributeError, KeyError):
        pass

    # Pre-assign layout properties to avoid unregister/register thrashing during initialization.
    for p in [rruf_core.RRUF_PT_Main, RRUF_PT_Lite_Toolset]:
        p.bl_space_type = cfg['space']
        p.bl_region_type = cfg['region']
        p.bl_category = cfg['cat']
        p.bl_context = cfg['context']

    # --- 2. STANDARD REGISTRATION ---
    rruf_core.register()
    
    # Load PNG icon into a managed preview collection.
    # Storing this in a global dictionary prevents Blender from garbage-collecting the image pointer.
    pcoll = bpy.utils.previews.new()
    icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
    if os.path.exists(icon_path):
        pcoll.load("rruf_logo", icon_path, 'IMAGE')
    custom_icons_dict["main"] = pcoll
    
    # Cache the original core draw method and overwrite it with the local patched version.
    rruf_core.RRUF_OT_popup_ui._original_draw = rruf_core.RRUF_OT_popup_ui.draw
    rruf_core.RRUF_OT_popup_ui.draw = _patched_popup_draw

    for cls in _CLASSES: 
        bpy.utils.register_class(cls)
        
    bpy.types.Scene.rruf_lite_io = bpy.props.PointerProperty(type=RRUF_Lite_IO_Props)

def unregister():
    del bpy.types.Scene.rruf_lite_io
    
    for cls in reversed(_CLASSES): 
        bpy.utils.unregister_class(cls)
    
    if hasattr(rruf_core.RRUF_OT_popup_ui, "_original_draw"):
        rruf_core.RRUF_OT_popup_ui.draw = rruf_core.RRUF_OT_popup_ui._original_draw
        
    for pcoll in custom_icons_dict.values():
        try: bpy.utils.previews.remove(pcoll)
        except Exception: pass
    custom_icons_dict.clear()

    rruf_core.unregister()

if __name__ == "__main__":
    register()