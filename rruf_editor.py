# RRUF - Reactive Rig UI Framework.
# Copyright (C) 2025-2026 Laurus Kuvakei
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import os
import json
import traceback
import re
from bpy_extras.io_utils import ExportHelper, ImportHelper
from . import rruf_core
preview_collections = {}

_NAME_WITH_DISCRIMINATOR_PATTERN = re.compile(r'\{([^}]+)\}(?:\[([^\]]+)\])?')

# Reactive Rig UI Framework (Editor Module)
# Copyright (C) 2025-2026 Laurus Kuvakei


_TOOLTIPS = {
    "SYNC":           "Manually load the active collection's name and tags into the Editor",
    "ACTION":         "Add, remove, or modify tags for the current collection",
    "COMMIT":         "Confirm and apply the specific parameter to the active tag",
    "ENABLE":         "Initialize RRUF data on this armature",
    "EXPORT":         "Save the current UI layout and property values to a JSON snapshot",
    "IMPORT":         "Import a JSON snapshot to rebuild the UI structure",
    "IMPORT_WARN":    "WARNING: This will wipe specific current bone collections and rebuild them from the file",
    "PRESET_WARN":    "WARNING: This will wipe all current bone collections and load the selected preset",
    "EMBED":          "Embed the rruf_core.py engine into this blend file for portable distribution",
    "ICON_GRID":      "Select this icon for the collection",
    "COPY_PROPS":     "Copy all custom properties from this collection to the clipboard",
    "PASTE_PROPS":    "Paste copied properties to this collection (overwrites existing keys)",
    "CLEAR_CLIP":     "Clear the property clipboard",
    "REORDER":        "Move this tag block",
    "BOARD":          "Display contents in a static board (always open, no toggle)",
    "PROP_SELECT":    "Load this property into the Property Composer",
    "PROP_APPLY":     "Rename the property with the new name, order and flags, and update every driver and animation curve that uses it",
    "PROP_MOVE":      "Move this property and renumber the collection's properties in order, updating drivers and animation curves",
}

_PROP_FLAG_ORDER = ("x", "^", "-")

PANEL_CONFIGS = {
    'RRUF':      {'space': 'VIEW_3D', 'region': 'UI', 'context': '', 'cat': 'RRUF'},
    'Animation': {'space': 'VIEW_3D', 'region': 'UI', 'context': '', 'cat': 'Animation'},
    'View':      {'space': 'VIEW_3D', 'region': 'UI', 'context': '', 'cat': 'View'},
    'Tool':      {'space': 'VIEW_3D', 'region': 'UI', 'context': '', 'cat': 'Tool'},
    'Item':      {'space': 'VIEW_3D', 'region': 'UI', 'context': '', 'cat': 'Item'},
}

TAB_ITEMS = (
    ('RRUF', "RRUF", "Default"),
    ('Animation', "Animation", "Standard Animation"),
    ('View', "View", "Standard View"),
    ('Tool', "Tool", "Standard Tool"),
    ('Item', "Item", "Standard Item"),
)


def get_active_bone_collection(armature):
    if armature.collections.active:
        return armature.collections.active
    return None

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

def _add_tag_to_buffer(current_buffer, tag):
    full_tag = f"({tag})"
    clean_buf = current_buffer.replace(" ", "").strip()
    if full_tag not in clean_buf:
        return (clean_buf + full_tag)
    return clean_buf

def _remove_tag_from_buffer(current_buffer, tag_key):
    chunks = [m.group(0) for m in rruf_core._TAG_TUPLE_PATTERN.finditer(current_buffer.replace(" ", ""))]
    if tag_key in chunks: chunks.remove(tag_key)
    return "".join(chunks)

def _format_parameter_tag(tag, str_val, bool_val, icon_val):
    param_val = ""
    if tag.upper() in ("ICON", "IC"):
        if icon_val: param_val = icon_val
    elif tag.upper() in ("HIDE", "H"):
        if bool_val: param_val = "ALW"

    full_tag = f"({tag})"
    if param_val:
        full_tag += f"[{param_val}]"

    return full_tag

def sync_ui_props_to_collection(props, coll):
    parsed = rruf_core.rruf_engine.parse_collection_syntax(coll.name, is_editor_preview=True)

    is_master = rruf_core._get_root_context(coll.name) is not None

    if parsed["clean_name"]:
        props.display_name = parsed["clean_name"]
        m = _NAME_WITH_DISCRIMINATOR_PATTERN.search(coll.name)
        props.discriminator = (m.group(2) or "").strip() if m else ""
        remain = rruf_core._EXPLICIT_NAME_PATTERN.sub("", coll.name).strip()
        props.tag_buffer = remain.replace(" ", "")
    elif is_master:
        props.display_name = ""
        props.discriminator = ""
        props.tag_buffer = coll.name.replace(" ", "")
    else:
        props.display_name = coll.name
        props.discriminator = ""
        props.tag_buffer = ""

    props.active_tag_to_configure = ""
    props.last_target_name = coll.name

def _sorted_prop_keys(coll):
    keys = [k for k in coll.keys() if k != "_RNA_UI"]
    return sorted(keys, key=lambda k: rruf_core._prop_sort_key(rruf_core._parse_prop_key(k)))

def _build_prop_key(order, flags, name):
    prefix = (str(order) if order else "") + "".join(f for f in _PROP_FLAG_ORDER if f in flags)
    return f"{{{prefix}}}{name}" if prefix else name

def _is_numeric_prop(val):
    return isinstance(val, (int, float)) and not isinstance(val, bool)

def _load_prop_into_editor(props, coll, key):
    order, flags, clean = rruf_core._parse_prop_key(key)
    props.prop_coll_name, props.prop_key = coll.name, key
    props.prop_name = clean
    props.prop_order = order or 0
    props.prop_hide, props.prop_toggle, props.prop_invert = 'x' in flags, '^' in flags, '-' in flags

def _prop_path_pattern(coll_name, key):
    lit = lambda t: re.escape(bpy.utils.escape_identifier(t))
    return re.compile(r'(collections(?:_all)?\[(["\'])' + lit(coll_name) + r'\2\]\[)(["\'])' + lit(key) + r'\3\]')

# Driver references to this armature can live on other IDs in the same file,
# including embedded node trees. Visit each animated ID once; later filtering
# limits edits to paths and targets belonging to the selected armature.
def _iter_animated_ids():
    seen = set()
    for attr in dir(bpy.data):
        try: data_coll = getattr(bpy.data, attr)
        except Exception: continue
        if not isinstance(data_coll, bpy.types.bpy_prop_collection): continue
        for id_block in data_coll:
            if not isinstance(id_block, bpy.types.ID): continue
            for candidate in (id_block, getattr(id_block, "node_tree", None)):
                if candidate is None or candidate.as_pointer() in seen: continue
                seen.add(candidate.as_pointer())
                if getattr(candidate, "animation_data", None): yield candidate

# Support layered/slotted actions as well as the legacy fcurves API.
# When a slot is supplied, inspect its channel bag only so other armatures
# using different slots in the same action retain their animation paths.
def _iter_action_fcurves(action, slot=None):
    layers = getattr(action, "layers", None)
    if layers:
        for layer in layers:
            for strip in layer.strips:
                if slot is not None:
                    bag = strip.channelbag(slot)
                    if bag: yield from bag.fcurves
                else:
                    for bag in getattr(strip, "channelbags", ()):
                        yield from bag.fcurves
    elif getattr(action, "fcurves", None) is not None:
        yield from action.fcurves

# A property rename changes its RNA path. Repair exact collection/key matches
# in drivers targeting this armature and in its active/NLA action curves.
# Read-only paths are counted as skipped. This does not rewrite expressions,
# unused actions, or references stored in other .blend files.
def _fix_prop_paths(armature, coll_name, old_key, new_key):
    pattern = _prop_path_pattern(coll_name, old_key)
    new_lit = bpy.utils.escape_identifier(new_key)
    replace = lambda path: pattern.sub(lambda m: f"{m.group(1)}{m.group(3)}{new_lit}{m.group(3)}]", path)
    drivers = curves = skipped = 0

    for id_block in _iter_animated_ids():
        for fc in id_block.animation_data.drivers:
            fixed = failed = False
            try:
                if id_block == armature and pattern.search(fc.data_path):
                    fc.data_path = replace(fc.data_path); fixed = True
            except Exception: failed = True
            for var in fc.driver.variables:
                for t in var.targets:
                    tid = t.id
                    if tid is None: continue
                    if not (tid == armature or (isinstance(tid, bpy.types.Object) and tid.data == armature)): continue
                    if t.data_path and pattern.search(t.data_path):
                        try: t.data_path = replace(t.data_path); fixed = True
                        except Exception: failed = True
            if fixed:
                try: fc.driver.is_valid = fc.is_valid = True
                except Exception: pass
                drivers += 1
            if failed: skipped += 1

    anim = armature.animation_data
    if anim:
        # Carry each action's armature slot through to curve iteration rather
        # than rewriting all slots of a shared action.
        sources = [(anim.action, getattr(anim, "action_slot", None))]
        sources += [(st.action, getattr(st, "action_slot", None)) for tr in anim.nla_tracks for st in tr.strips]
        seen = set()
        for action, slot in sources:
            if not action: continue
            tag = (action.as_pointer(), slot.as_pointer() if slot else 0)
            if tag in seen: continue
            seen.add(tag)
            for fc in _iter_action_fcurves(action, slot):
                if pattern.search(fc.data_path):
                    try: fc.data_path = replace(fc.data_path); curves += 1
                    except Exception: skipped += 1
    return drivers, curves, skipped

# Blender custom-property keys are replaced rather than renamed in place.
# Preserve the value, UI metadata, and library-overridable flag before removing
# the old key, then repair supported driver and animation references.
def _rename_prop(armature, coll, old_key, new_key):
    val = coll[old_key]
    if hasattr(val, "to_dict"): val = val.to_dict()
    elif hasattr(val, "to_list"): val = val.to_list()
    coll[new_key] = val
    try: coll.id_properties_ui(new_key).update_from(coll.id_properties_ui(old_key))
    except Exception: pass
    try:
        esc_old, esc_new = bpy.utils.escape_identifier(old_key), bpy.utils.escape_identifier(new_key)
        if coll.is_property_overridable_library(f'["{esc_old}"]'):
            coll.property_overridable_library_set(f'["{esc_new}"]', True)
    except Exception: pass
    del coll[old_key]
    return _fix_prop_paths(armature, coll.name, old_key, new_key)

def _used_root_contexts(data, exclude=None):
    return {ctx for c in data.collections_all
            if c != exclude and (ctx := rruf_core._get_root_context(c.name))}

def _get_valid_tags(data, active_coll, current_tags):
    context_root = None
    walker = active_coll

    while walker:
        ctx = rruf_core._get_root_context(walker.name)
        if ctx:
            context_root = ctx
            break
        if walker.parent:
            walker = walker.parent
        else:
            break

    parsed = rruf_core.rruf_engine.parse_collection_syntax(current_tags, context_root, is_editor_preview=True)
    existing = {f for f, cfg in rruf_core.TAG_CONFIG.items() if parsed.get(cfg["node_key"])}
    valid = []

    masters = [k for k, v in rruf_core.TAG_CONFIG.items() if "root_context" in v]

    if any(x in existing for x in masters):
        return []

    if context_root is None:
        if active_coll.parent is None:
            used = _used_root_contexts(data, exclude=active_coll)
            valid.extend(m for m in masters if rruf_core.TAG_CONFIG[m]["root_context"] not in used)
    else:
        is_skipped = any(rruf_core.TAG_CONFIG[t].get("is_skip") for t in existing if t in rruf_core.TAG_CONFIG)

        if not is_skipped:
            for tag, cfg in rruf_core.TAG_CONFIG.items():
                if "contexts" in cfg and context_root in cfg["contexts"]:
                    valid.append(tag)

    return [f for f in valid if f not in existing]

def _serialize_collection_properties(coll):
    prop_storage = {}
    def clean_data(data):
        if isinstance(data, float): return round(data, 4)
        if hasattr(data, "__len__") and not isinstance(data, str):
            return [round(x, 4) if isinstance(x, float) else x for x in data]
        return data

    for key in coll.keys():
        if key == "_RNA_UI": continue
        val = coll[key]
        if isinstance(val, bpy.types.Collection):
            prop_storage[key] = {"value": None, "collection": val.name}
            continue
        if isinstance(val, (int, float, str, bool)) or (hasattr(val, "__len__") and not isinstance(val, str)):
            try:
                cfg = coll.id_properties_ui(key).as_dict()
                ui_cfg = {k: clean_data(v) for k, v in cfg.items()}
            except Exception: ui_cfg = {}
            prop_storage[key] = {"value": clean_data(val), "config": ui_cfg}
    return prop_storage

def _deserialize_collection_properties(coll, prop_data):
    skipped = 0
    for key, data in prop_data.items():
        if "collection" in data:
            if target := bpy.data.collections.get(str(data["collection"])):
                coll[key] = target
            else:
                print(f"RRUF Editor: outliner collection '{data['collection']}' for '{key}' not found; skipped.")
                skipped += 1
            continue
        try:
            coll[key] = data.get("value")
        except (TypeError, KeyError, ValueError, OverflowError) as e:
            print(f"RRUF Editor: property '{key}' on '{coll.name}' skipped: {e}")
            skipped += 1
            continue

        config = data.get("config", {})
        if config:
            try:
                ui_mgr = coll.id_properties_ui(key)

                banned_keys = {"is_overridable_library"}
                dynamic_payload = {k: v for k, v in config.items() if k not in banned_keys}

                if dynamic_payload:
                    ui_mgr.update(**dynamic_payload)

            except Exception as e:
                print(f"RRUF Editor Deserialization Error for '{key}': {e}")
    return skipped

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

def _validate_snapshot(snapshot_data):
    if not isinstance(snapshot_data, dict) or "tree" not in snapshot_data:
        return "Not an RRUF snapshot (missing 'tree')."
    tree = snapshot_data["tree"]
    if not isinstance(tree, list):
        return "'tree' must be a list."
    if not tree:
        return "Snapshot 'tree' is empty."
    valid_root_tags = {f"({k})" for k, v in rruf_core.TAG_CONFIG.items() if "root_context" in v}
    has_categorized_node = False
    for i, node in enumerate(tree):
        if not isinstance(node, dict):
            return f"Entry {i} is not an object."
        name = node.get("name")
        if not isinstance(name, str) or not name or "\x00" in name or len(name.encode("utf-8")) > 63:
            return f"Entry {i} has an invalid 'name' (must be 1-63 UTF-8 bytes without null characters)."
        parent = node.get("parent")
        if parent is not None and (not isinstance(parent, str) or not parent or "\x00" in parent):
            return f"'{name}': 'parent' must be null or a non-empty string."
        root_tag = node.get("root_tag")
        if root_tag is not None:
            if not isinstance(root_tag, str) or root_tag not in valid_root_tags:
                return f"'{name}': unknown 'root_tag' '{root_tag}'."
            has_categorized_node = True
        if "bones" in node:
            if not isinstance(node["bones"], list):
                return f"'{name}': 'bones' must be a list."
            for bone_index, bone_name in enumerate(node["bones"]):
                if not isinstance(bone_name, str) or not bone_name or "\x00" in bone_name:
                    return (f"'{name}': bone entry {bone_index} must be "
                            "a non-empty name without null characters.")
        if "props" in node:
            if not isinstance(node["props"], dict):
                return f"'{name}': 'props' must be an object."
            for key, entry in node["props"].items():
                if not key or len(key.encode("utf-8")) > 63:
                    return f"'{name}': property key '{key[:20]}' must be 1-63 bytes."
                if not isinstance(entry, dict):
                    return f"'{name}': property '{key}' must be an object."
    if not has_categorized_node:
        return "Snapshot contains no valid RRUF category collections."
    return None

def _deserialize_armature_structure(armature, snapshot_data, props, context=None):
    # Apply the same snapshot shape, root-tag, and key checks for direct callers
    # and operators before Rebuild removes collections.
    if _validate_snapshot(snapshot_data) is not None:
        return False

    category_gate = {}
    for tag_name, cfg in rruf_core.TAG_CONFIG.items():
        ctx = cfg.get("root_context")
        if ctx == rruf_core.CTX_VIS:     category_gate[f"({tag_name})"] = props.overwrite_vis
        elif ctx == rruf_core.CTX_PROP:  category_gate[f"({tag_name})"] = props.overwrite_props
        elif ctx == rruf_core.CTX_SNAP:  category_gate[f"({tag_name})"] = props.overwrite_snaps
        elif ctx == rruf_core.CTX_CWI:   category_gate[f"({tag_name})"] = props.overwrite_internals

    active_targets = [tag for tag, active in category_gate.items() if active]
    total_wipe_requested = len(active_targets) == 4 and props.use_rebuild

    if total_wipe_requested:
        # Selecting every category with Rebuild enabled is an intentional full
        # replacement: remove all bone collections, including non-RRUF branches.
        # The import dialog and user guide disclose this destructive scope.
        to_remove = list(armature.collections_all)
        for c in reversed(to_remove):
            try: armature.collections.remove(c)
            except Exception: pass
    elif props.use_rebuild:
        to_remove = []
        for c in armature.collections_all:
            root_tag = _get_root_category_tag(c)
            if root_tag in active_targets:
                to_remove.append(c)

        for c in reversed(to_remove):
            try: armature.collections.remove(c)
            except Exception: pass

    bl_ctx = context or bpy.context
    bl_ctx.view_layer.update()
    skipped_props = 0

    created_map = {c.name: c for c in armature.collections_all}
    # Short and long root aliases identify the same category. Reuse an existing
    # category root rather than creating a duplicate, and map imported children
    # to it even when the snapshot uses the other spelling.
    existing_roots = {rruf_core._get_root_context(c.name): c for c in armature.collections
                      if rruf_core._get_root_context(c.name)}
    for tag in active_targets:
        ctx = rruf_core._get_root_context(tag)
        if ctx in existing_roots:
            created_map[tag] = existing_roots[ctx]
        elif tag not in created_map:
            created_map[tag] = existing_roots[ctx] = armature.collections.new(name=tag)

    for node in snapshot_data["tree"]:
        nm = str(node["name"])
        root_tag = node.get("root_tag")

        if root_tag not in active_targets:
            continue

        if node.get("parent") is None and (ctx := rruf_core._get_root_context(nm)) in existing_roots:
            created_map[nm] = existing_roots[ctx]
            continue

        if nm not in created_map:
            c = armature.collections.new(name=nm)
            created_map[nm] = c

    bl_ctx.view_layer.update()

    for node in snapshot_data["tree"]:
        nm = str(node["name"])
        root_tag = node.get("root_tag")

        if root_tag not in active_targets:
            continue

        if nm not in created_map: continue
        c = created_map[nm]

        if p := node.get("parent"):
            p = str(p)
            if p in created_map:
                try: c.parent = created_map[p]
                except Exception: pass

        if "bones" in node:
            for b in node["bones"]:
                if bone := armature.bones.get(b):
                    c.assign(bone)

        if "props" in node:
            skipped_props += _deserialize_collection_properties(c, node["props"]) or 0

    return skipped_props


class _RRUF_ArmaturePoll:
    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == 'ARMATURE' and obj.data)

class RRUF_Icon_Item(bpy.types.PropertyGroup):
    icon_name: bpy.props.StringProperty()

class RRUF_Editor_Props(bpy.types.PropertyGroup):
    last_target_name: bpy.props.StringProperty(default="")
    display_name: bpy.props.StringProperty(name="Name")
    discriminator: bpy.props.StringProperty(
        name="Discriminator",
        description="Optional suffix that keeps collection names unique without changing the displayed label (e.g. {FK}[2])"
    )
    tag_buffer: bpy.props.StringProperty(name="Flags")
    active_tag_to_configure: bpy.props.StringProperty(default="")
    param_buffer_str: bpy.props.StringProperty(name="Label")
    icon_collection: bpy.props.CollectionProperty(type=RRUF_Icon_Item)
    icon_index: bpy.props.IntProperty()
    icon_search_filter: bpy.props.StringProperty(name="Search", options={'TEXTEDIT_UPDATE'})
    param_buffer_bool: bpy.props.BoolProperty(name="Value")
    clipboard_data: bpy.props.StringProperty(default="")
    clipboard_source_name: bpy.props.StringProperty(default="")

    prop_coll_name: bpy.props.StringProperty(default="")
    prop_key: bpy.props.StringProperty(default="")
    prop_name: bpy.props.StringProperty(name="Name")
    prop_order: bpy.props.IntProperty(name="Order", description="0 = no number (sorted last, by name)", min=0, max=999)
    prop_hide: bpy.props.BoolProperty(name="Hide", description="{x}: hide the property from the interface")
    prop_toggle: bpy.props.BoolProperty(name="Toggle", description="{^}: draw a 0/1 switch next to the slider")
    prop_invert: bpy.props.BoolProperty(name="Invert", description="{-}: invert the visibility sync with the linked outliner collection")

    overwrite_vis: bpy.props.BoolProperty(name="Vis", default=True)
    overwrite_props: bpy.props.BoolProperty(name="Props", default=True)
    overwrite_snaps: bpy.props.BoolProperty(name="Snaps", default=True)
    overwrite_internals: bpy.props.BoolProperty(name="Internals", default=True)

    use_rebuild: bpy.props.BoolProperty(
        name="Rebuild",
        default=False,
        description="Active: Wipe ALL selected categories before import. Inactive: Selective overwrite."
    )

    include_bones: bpy.props.BoolProperty(name="Bones", default=True)
    include_props: bpy.props.BoolProperty(name="Properties", default=True)

class RRUF_OT_select_icon_grid(bpy.types.Operator):
    bl_idname = "rruf.select_icon_grid"
    bl_label = "Select Icon"
    bl_description = _TOOLTIPS["ICON_GRID"]
    icon_name: bpy.props.StringProperty()
    index: bpy.props.IntProperty()
    def execute(self, context):
        context.scene.rruf_editor.icon_index = self.index
        return {'FINISHED'}

class RRUF_OT_sync_editor(_RRUF_ArmaturePoll, bpy.types.Operator):
    bl_idname = "rruf.sync_editor"
    bl_label = "Load Active Collection"
    bl_description = _TOOLTIPS["SYNC"]
    def execute(self, context):
        arm = context.active_object.data
        coll = get_active_bone_collection(arm)
        if not coll: return {'CANCELLED'}
        sync_ui_props_to_collection(context.scene.rruf_editor, coll)
        return {'FINISHED'}

class RRUF_OT_convert_syntax(_RRUF_ArmaturePoll, bpy.types.Operator):
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
        arm = context.active_object.data
        changes_made = 0
        skipped_collections = 0

        for coll in arm.collections_all:
            new_name = coll.name

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

            if new_name != coll.name:
                if self.target_mode == 'INFLATE' and len(new_name.encode("utf-8")) > 63:
                    self.report({'WARNING'}, f"Skipping '{coll.name}': Inflated name exceeds 63 byte limit.")
                    skipped_collections += 1
                    continue
                clash = arm.collections_all.get(new_name)
                if clash is not None and clash != coll:
                    self.report({'WARNING'}, f"Skipping '{coll.name}': '{new_name}' already exists.")
                    skipped_collections += 1
                    continue

                coll.name = new_name
                changes_made += 1

        if changes_made > 0:
            rruf_core.rruf_engine.purge_all()
            context.active_object.update_tag()
            msg = f"Successfully {self.target_mode.lower()}d {changes_made} collections!"
            if skipped_collections > 0:
                msg += f" (Skipped {skipped_collections} due to length limits)"
            self.report({'INFO'}, msg)
        elif skipped_collections > 0:
            self.report({'WARNING'}, f"Skipped {skipped_collections} collections due to 63 byte name limit.")
        else:
            self.report({'INFO'}, "Rig is already in the target format.")

        return {'FINISHED'}

class RRUF_OT_reorder_tag(bpy.types.Operator):
    bl_idname = "rruf.reorder_tag"
    bl_label = "Reorder"
    bl_description = _TOOLTIPS["REORDER"]
    direction: bpy.props.EnumProperty(items=[('UP', "Up", ""), ('DOWN', "Down", "")])
    index: bpy.props.IntProperty()
    def execute(self, context):
        props = context.scene.rruf_editor
        chunks = [m.group(0) for m in rruf_core._TAG_TUPLE_PATTERN.finditer(props.tag_buffer)]
        if not chunks: return {'CANCELLED'}
        idx = self.index
        if not 0 <= idx < len(chunks): return {'CANCELLED'}
        if self.direction == 'UP' and idx > 0:
            chunks[idx], chunks[idx-1] = chunks[idx-1], chunks[idx]
        elif self.direction == 'DOWN' and idx < len(chunks) - 1:
            chunks[idx], chunks[idx+1] = chunks[idx+1], chunks[idx]
        else: return {'CANCELLED'}
        props.tag_buffer = "".join(chunks)
        return {'FINISHED'}

class RRUF_OT_editor_action(_RRUF_ArmaturePoll, bpy.types.Operator):
    bl_idname = "rruf.editor_action"
    bl_label = "Editor Action"
    bl_description = _TOOLTIPS["ACTION"]
    action: bpy.props.EnumProperty(
        items=[('ADD_TAG', "Add", ""), ('REMOVE_TAG', "Remove", ""),
               ('COMMIT', "Commit", ""), ('CANCEL_PARAM', "Cancel", "")]
    )
    tag_key: bpy.props.StringProperty()

    def execute(self, context):
        props = context.scene.rruf_editor
        if self.action == 'ADD_TAG':
            tag = self.tag_key

            config = rruf_core.TAG_CONFIG.get(tag)
            if not config: return {'CANCELLED'}

            if "root_context" in config:
                props.tag_buffer = f"({tag})"
                return {'FINISHED'}

            if config.get("params") is not None:
                props.active_tag_to_configure = tag
                props.param_buffer_str = ""; props.param_buffer_bool = False

                if tag.upper() in ("ICON", "IC") and len(props.icon_collection) == 0:
                    valid = rruf_core._get_valid_icons()
                    for ic in sorted(list(valid)):
                        item = props.icon_collection.add(); item.icon_name = ic
            else:
                props.tag_buffer = _add_tag_to_buffer(props.tag_buffer, tag)

        elif self.action == 'REMOVE_TAG':
            props.tag_buffer = _remove_tag_from_buffer(props.tag_buffer, self.tag_key)

        elif self.action == 'COMMIT':
            arm = context.active_object.data
            coll = get_active_bone_collection(arm)
            if coll:
                raw_buffer = props.tag_buffer.replace(" ", "").strip()
                tag_chunks = [m.group(0) for m in rruf_core._TAG_TUPLE_PATTERN.finditer(raw_buffer)]
                cleaned_tags = "".join(tag_chunks)

                is_master = rruf_core._get_root_context(cleaned_tags) is not None

                if is_master:
                    root_ctx = rruf_core._get_root_context(cleaned_tags)
                    if root_ctx in _used_root_contexts(arm, exclude=coll):
                        self.report({'WARNING'}, f"A {cleaned_tags} root collection already exists. Only one per category.")
                        return {'CANCELLED'}
                    final_name = cleaned_tags
                else:
                    disp = props.display_name.strip()
                    disc = props.discriminator.strip()

                    if not disp:
                        self.report({'WARNING'}, "Name is empty. Enter a label before updating.")
                        return {'CANCELLED'}
                    if any(ch in disp for ch in "{}"):
                        self.report({'WARNING'}, "Name cannot contain '{' or '}'.")
                        return {'CANCELLED'}
                    if any(ch in disc for ch in "[]"):
                        self.report({'WARNING'}, "Discriminator cannot contain '[' or ']'.")
                        return {'CANCELLED'}

                    disc_part = f"[{disc}]" if disc else ""
                    final_name = f"{{{disp}}}{disc_part}{cleaned_tags}"

                depth, walker = 0, coll
                while walker.parent: depth += 1; walker = walker.parent
                tree_ctx = rruf_core._get_root_context(walker.name) if depth else None
                check = rruf_core.rruf_engine.parse_collection_syntax(final_name, tree_ctx, depth, is_editor_preview=True)
                if not check["is_valid"]:
                    self.report({'WARNING'}, check["error_msg"] or "Invalid name.")
                    return {'CANCELLED'}

                name_bytes = len(final_name.encode("utf-8"))
                if name_bytes > 63:
                    self.report({'WARNING'}, f"Name too long ({name_bytes}/63 bytes). Shorten the label or remove tags.")
                    return {'CANCELLED'}

                clash = arm.collections_all.get(final_name)
                if clash is not None and clash != coll:
                    self.report({'WARNING'}, f"'{final_name}' already exists. Use a discriminator to keep it unique.")
                    return {'CANCELLED'}

                old_name = coll.name
                coll.name = final_name

                rruf_core.rruf_engine.purge_all()
                props.last_target_name = coll.name
                # This operator also handles temporary composer actions that
                # should not create undo steps. Push undo only when COMMIT
                # applies the composed name to the actual bone collection.
                bpy.ops.ed.undo_push(message="RRUF Commit")

                self.report({'INFO'}, f"Updated collection: {old_name} -> {final_name}")
            else:
                self.report({'WARNING'}, "No active bone collection selected.")
                return {'CANCELLED'}

        elif self.action == 'CANCEL_PARAM':
            props.active_tag_to_configure = ""

        return {'FINISHED'}

class RRUF_OT_commit_param(bpy.types.Operator):
    bl_idname = "rruf.commit_param"
    bl_label = "Confirm"
    bl_description = _TOOLTIPS["COMMIT"]
    def execute(self, context):
        props = context.scene.rruf_editor
        tag = props.active_tag_to_configure
        if not tag: return {'CANCELLED'}
        icon_val = ""

        if tag.upper() in ("ICON", "IC"):
            if 0 <= props.icon_index < len(props.icon_collection):
                icon_val = props.icon_collection[props.icon_index].icon_name
            else: return {'CANCELLED'}
        full_tag = _format_parameter_tag(tag, props.param_buffer_str, props.param_buffer_bool, icon_val)
        props.tag_buffer = (props.tag_buffer.replace(" ", "").strip() + full_tag)
        props.active_tag_to_configure = ""
        return {'FINISHED'}

def _get_editing_prop(context):
    props = context.scene.rruf_editor
    coll = get_active_bone_collection(context.active_object.data)
    if coll and props.prop_coll_name == coll.name and props.prop_key in coll:
        return props, coll
    return props, None

def _report_paths(op, verb, drivers, curves, skipped):
    msg = f"{verb}. Updated {drivers} driver(s) and {curves} animation curve(s)."
    if skipped:
        op.report({'WARNING'}, f"{msg} {skipped} could not be updated (linked or read-only data).")
    else:
        op.report({'INFO'}, msg)

class RRUF_OT_prop_select(_RRUF_ArmaturePoll, bpy.types.Operator):
    bl_idname = "rruf.prop_select"
    bl_label = "Select Property"
    bl_description = _TOOLTIPS["PROP_SELECT"]
    bl_options = {'INTERNAL'}
    key: bpy.props.StringProperty()

    def execute(self, context):
        coll = get_active_bone_collection(context.active_object.data)
        if not coll or self.key not in coll: return {'CANCELLED'}
        _load_prop_into_editor(context.scene.rruf_editor, coll, self.key)
        return {'FINISHED'}

class RRUF_OT_prop_apply(_RRUF_ArmaturePoll, bpy.types.Operator):
    bl_idname = "rruf.prop_apply"
    bl_label = "Update Property"
    bl_description = _TOOLTIPS["PROP_APPLY"]
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == 'ARMATURE' and obj.data and not obj.data.library and not obj.data.override_library)

    def execute(self, context):
        props, coll = _get_editing_prop(context)
        if not coll:
            self.report({'WARNING'}, "No property loaded for the active collection.")
            return {'CANCELLED'}

        name = props.prop_name.strip()
        if any(ch in name for ch in "{}"):
            self.report({'WARNING'}, "Name cannot contain '{' or '}'."); return {'CANCELLED'}

        val = coll[props.prop_key]
        flags = set()
        if props.prop_hide: flags.add("x")
        if props.prop_toggle and _is_numeric_prop(val): flags.add("^")
        if props.prop_invert and isinstance(val, bpy.types.Collection): flags.add("-")
        if not name and not props.prop_order and not flags:
            self.report({'WARNING'}, "An empty name needs a number or a flag (e.g. {1})."); return {'CANCELLED'}
        new_key = _build_prop_key(props.prop_order, flags, name)

        if new_key == props.prop_key: return {'CANCELLED'}
        if (n := len(new_key.encode("utf-8"))) > 63:
            self.report({'WARNING'}, f"Key too long ({n}/63 bytes)."); return {'CANCELLED'}
        if new_key in coll:
            self.report({'WARNING'}, f"'{new_key}' already exists in this collection."); return {'CANCELLED'}

        drivers, curves, skipped = _rename_prop(context.active_object.data, coll, props.prop_key, new_key)
        _load_prop_into_editor(props, coll, new_key)
        rruf_core.rruf_engine.purge_all()
        _report_paths(self, f"Renamed to '{new_key}'", drivers, curves, skipped)
        return {'FINISHED'}

class RRUF_OT_prop_move(_RRUF_ArmaturePoll, bpy.types.Operator):
    bl_idname = "rruf.prop_move"
    bl_label = "Move Property"
    bl_description = _TOOLTIPS["PROP_MOVE"]
    bl_options = {'UNDO'}
    key: bpy.props.StringProperty()
    direction: bpy.props.EnumProperty(items=[('UP', "Up", ""), ('DOWN', "Down", "")])

    @classmethod
    def poll(cls, context):
        return RRUF_OT_prop_apply.poll(context)

    def execute(self, context):
        arm = context.active_object.data
        coll = get_active_bone_collection(arm)
        if not coll or self.key not in coll: return {'CANCELLED'}

        keys = _sorted_prop_keys(coll)
        i = keys.index(self.key)
        j = i - 1 if self.direction == 'UP' else i + 1
        if not 0 <= j < len(keys): return {'CANCELLED'}
        keys[i], keys[j] = keys[j], keys[i]

        renames = []
        for n, k in enumerate(keys, start=1):
            _, flags, clean = rruf_core._parse_prop_key(k)
            renames.append((k, _build_prop_key(n, flags & set(_PROP_FLAG_ORDER), clean)))

        new_keys = [nk for _, nk in renames]
        clean_names = [rruf_core._parse_prop_key(k)[2] for k in keys]
        if len(set(clean_names)) != len(clean_names):
            self.report({'WARNING'}, "Two properties share the same name; rename one first."); return {'CANCELLED'}
        if any(len(nk.encode("utf-8")) > 63 for nk in new_keys):
            self.report({'WARNING'}, "A renumbered key would exceed 63 bytes."); return {'CANCELLED'}

        props = context.scene.rruf_editor
        was_loaded = props.prop_coll_name == coll.name and props.prop_key
        drivers = curves = skipped = 0
        for old, new in renames:
            if old == new: continue
            d, c, sk = _rename_prop(arm, coll, old, new)
            drivers += d; curves += c; skipped += sk
            if was_loaded and props.prop_key == old: props.prop_key = new
        if was_loaded and props.prop_key in coll: _load_prop_into_editor(props, coll, props.prop_key)

        rruf_core.rruf_engine.purge_all()
        _report_paths(self, "Reordered", drivers, curves, skipped)
        return {'FINISHED'}

class RRUF_OT_enable(_RRUF_ArmaturePoll, bpy.types.Operator):
    bl_idname = "rruf.enable"
    bl_label = "Enable RRUF"
    bl_description = _TOOLTIPS["ENABLE"]
    bl_options = {'UNDO'}
    def execute(self, context):
        context.active_object.data[rruf_core.RRUF_TRIGGER_KEY] = True
        return {'FINISHED'}

class RRUF_OT_copy_props(_RRUF_ArmaturePoll, bpy.types.Operator):
    bl_idname = "rruf.copy_props"
    bl_label = "Copy Properties"
    bl_description = _TOOLTIPS["COPY_PROPS"]

    def execute(self, context):
        arm = context.active_object.data
        coll = get_active_bone_collection(arm)

        if not coll:
            self.report({'WARNING'}, "No active bone collection to copy from.")
            return {'CANCELLED'}

        props_data = _serialize_collection_properties(coll)
        if not props_data:
            self.report({'WARNING'}, f"No custom properties found in '{coll.name}'.")
            return {'CANCELLED'}

        scene_props = context.scene.rruf_editor
        scene_props.clipboard_data = json.dumps(props_data, ensure_ascii=False)
        scene_props.clipboard_source_name = coll.name

        self.report({'INFO'}, f"Copied {len(props_data)} properties from '{coll.name}'.")
        return {'FINISHED'}

class RRUF_OT_paste_props(_RRUF_ArmaturePoll, bpy.types.Operator):
    bl_idname = "rruf.paste_props"
    bl_label = "Paste Properties"
    bl_description = _TOOLTIPS["PASTE_PROPS"]
    bl_options = {'UNDO'}
    def execute(self, context):
        arm = context.active_object.data
        coll = get_active_bone_collection(arm)
        scene_props = context.scene.rruf_editor
        if not coll or not scene_props.clipboard_data: return {'CANCELLED'}
        try:
            _deserialize_collection_properties(coll, json.loads(scene_props.clipboard_data))
            rruf_core.rruf_engine.purge_all()
            rruf_core.limited_redraw()
            return {'FINISHED'}
        except Exception as e:
            self.report({'WARNING'}, f"Paste failed: {e}")
            return {'CANCELLED'}

class RRUF_OT_clear_clipboard(bpy.types.Operator):
    bl_idname = "rruf.clear_clipboard"
    bl_label = "Clear Clipboard"
    bl_description = _TOOLTIPS["CLEAR_CLIP"]
    def execute(self, context):
        context.scene.rruf_editor.clipboard_data = ""
        context.scene.rruf_editor.clipboard_source_name = ""
        return {'FINISHED'}

class RRUF_OT_export_snapshot(_RRUF_ArmaturePoll, bpy.types.Operator, ExportHelper):
    bl_idname = "rruf.export_snapshot"
    bl_label = "Export Snapshot"
    bl_description = _TOOLTIPS["EXPORT"]
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def invoke(self, context, event):
        obj = context.active_object
        self.filepath = f"{obj.name.replace(' ', '_')}_RRUF_Snapshot.json" if obj else "rruf.json"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        props = context.scene.rruf_editor
        data = _serialize_armature_structure(context.active_object.data, props.include_bones, props.include_props)
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
        except (OSError, UnicodeError) as e:
            self.report({'ERROR'}, f"Could not export snapshot: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}

class RRUF_OT_import_gatekeeper(_RRUF_ArmaturePoll, bpy.types.Operator):
    bl_idname = "rruf.import_gatekeeper"
    bl_label = "Confirm Import?"
    bl_description = _TOOLTIPS["IMPORT_WARN"]

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        props = context.scene.rruf_editor
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

class RRUF_OT_import_snapshot(_RRUF_ArmaturePoll, bpy.types.Operator, ImportHelper):
    bl_idname = "rruf.import_snapshot"
    bl_label = "Import Snapshot"
    bl_options = {'UNDO'}
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})
    def execute(self, context):
        props = context.scene.rruf_editor
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f: snapshot = json.load(f)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as e:
            self.report({'WARNING'}, f"Could not read snapshot: {e}")
            return {'CANCELLED'}

        if err := _validate_snapshot(snapshot):
            self.report({'WARNING'}, f"Invalid snapshot, nothing was changed: {err}")
            return {'CANCELLED'}

        result = _deserialize_armature_structure(context.active_object.data, snapshot, props, context=context)
        if result is False:
            return {'CANCELLED'}
        rruf_core.rruf_engine.purge_all()
        rruf_core.limited_redraw()
        if result:
            self.report({'WARNING'}, f"Imported. {result} propert{'y' if result == 1 else 'ies'} could not be restored (see console).")
        return {'FINISHED'}

def _local_core_text():
    # Only a local text can be replaced; a linked one belongs to its library.
    return next((t for t in bpy.data.texts if t.name == "rruf_core.py" and t.library is None), None)

class RRUF_OT_embed_core(bpy.types.Operator):
    bl_idname = "rruf.embed_core"
    bl_label = "Embed Core"
    bl_description = "Embed the rruf_core.py engine into this blend file"
    bl_options = {'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.alert = True
        col.label(text="Embedding Core Engine", icon='INFO')
        col.alert = False
        col.separator()
        col.label(text="This will package 'rruf_core.py' into the current .blend file")
        col.label(text="and register it to run automatically on file load.")
        old = _local_core_text()
        if old:
            col.separator()
            col.alert = True
            if "RRUF_GLOBAL_LOCK" in old.as_string():
                col.label(text="The existing 'rruf_core.py' text will be replaced.", icon='ERROR')
            else:
                col.label(text="A text named 'rruf_core.py' exists and is not an RRUF core.", icon='ERROR')
                col.label(text="It will be replaced. Rename it first to keep it.")
            col.alert = False
        col.separator()
        col.label(text="Are you sure you want to proceed?", icon='QUESTION')

    def execute(self, context):
        src_core = os.path.join(os.path.dirname(__file__), "rruf_core.py")
        try:
            with open(src_core, 'r', encoding='utf-8') as f:
                raw_code = f.read()

            name_core = "rruf_core.py"
            old = _local_core_text()
            if old: bpy.data.texts.remove(old)

            txt_core = bpy.data.texts.new(name_core)
            txt_core.write(raw_code)
            txt_core.use_module = True

        except Exception as e:
            self.report({'ERROR'}, f"Failed to embed rruf_core.py: {e}")
            return {'CANCELLED'}

        self.report({'INFO'}, "RRUF Core embedded successfully.")
        return {'FINISHED'}

# Keep references to dynamic enum strings after the callback returns.
# Blender's EnumProperty documentation warns that releasing these strings
# can cause incorrect behavior or crashes.
# Retain the current items here while refreshing the preset list.
_PRESET_ITEMS = []

def _get_json_presets_callback(self, context):
    path = os.path.dirname(__file__)
    files = [f for f in sorted(os.listdir(path)) if f.lower().endswith(".json")]
    _PRESET_ITEMS[:] = [(f, f, f"Load {f}") for f in files] or [('NONE', "No Presets", "")]
    return _PRESET_ITEMS

class RRUF_OT_load_internal_preset(_RRUF_ArmaturePoll, bpy.types.Operator):
    bl_idname = "rruf.load_internal_preset"
    bl_label = "Confirm Preset Load?"
    bl_options = {'UNDO'}

    def invoke(self, context, event):
        if context.window_manager.rruf_preset_list == 'NONE':
            self.report({'WARNING'}, "No preset selected.")
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        props = context.scene.rruf_editor
        wm = context.window_manager
        preset_name = wm.rruf_preset_list

        layout = self.layout
        col = layout.column(align=True)
        col.alert = True

        is_total = all([props.overwrite_vis, props.overwrite_props, props.overwrite_snaps, props.overwrite_internals])

        if props.use_rebuild:
            col.label(text=f"WARNING: Rebuilding to load '{preset_name}'", icon='ERROR')
            col.alert = False
            col.separator()
            if is_total:
                col.label(text="This will completely WIPE ALL bone collections")
                col.label(text="in this armature before loading the preset.")
            else:
                col.label(text="This will WIPE all collections within your selected")
                col.label(text="categories (Vis/Props/etc.) before loading.")
        else:
            col.label(text=f"Warning: Applying '{preset_name}'", icon='ERROR')
            col.alert = False
            col.separator()
            col.label(text="This will overwrite existing collections that match")
            col.label(text="the preset data in your active categories.")

        col.separator()
        col.label(text="Are you sure you want to proceed?", icon='QUESTION')

    def execute(self, context):
        wm = context.window_manager
        if wm.rruf_preset_list != 'NONE':
            fp = os.path.join(os.path.dirname(__file__), wm.rruf_preset_list)
            return bpy.ops.rruf.import_snapshot(filepath=fp)
        return {'CANCELLED'}


class RRUF_UL_icon_grid(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0, flt_flag=0):
        if self.layout_type == 'GRID':
            op = layout.operator("rruf.select_icon_grid", text="", icon=item.icon_name, emboss=False)
            op.icon_name = item.icon_name; op.index = index
        else: layout.label(text=item.icon_name, icon=item.icon_name)

    def filter_items(self, context, data, propname):
        props = context.scene.rruf_editor
        query = props.icon_search_filter.strip().upper()
        if not query: return [], []
        flt_flags = [self.bitflag_filter_item if query in item.icon_name else 0 for item in getattr(data, propname)]
        return flt_flags, []

def _draw_property_composer(layout, coll, props):
    keys = _sorted_prop_keys(coll)
    box = layout.box()
    box.label(text="Property Composer:", icon='PROPERTIES')
    if not keys:
        box.label(text="No properties in this collection.", icon='INFO')
        return

    loaded = props.prop_key if props.prop_coll_name == coll.name and props.prop_key in coll else ""
    col = box.column(align=True)
    for i, k in enumerate(keys):
        order, flags, clean = rruf_core._parse_prop_key(k)
        badges = "".join(f for f in _PROP_FLAG_ORDER if f in flags)
        label = f"{order or '-'}  {clean}" + (f"   {{{badges}}}" if badges else "")
        row = col.row(align=True)
        op = row.operator("rruf.prop_select", text=label, depress=(k == loaded), icon='HIDE_ON' if 'x' in flags else 'NONE')
        op.key = k
        sub = row.row(align=True)
        sub.enabled = i > 0
        op = sub.operator("rruf.prop_move", text="", icon='TRIA_UP'); op.key, op.direction = k, 'UP'
        sub = row.row(align=True)
        sub.enabled = i < len(keys) - 1
        op = sub.operator("rruf.prop_move", text="", icon='TRIA_DOWN'); op.key, op.direction = k, 'DOWN'

    if not loaded: return
    val = coll[loaded]
    edit = box.box()
    r = edit.row(align=True)
    split = r.split(factor=0.75, align=True)
    split.prop(props, "prop_name")
    split.prop(props, "prop_order", text="#")
    r = edit.row(align=True)
    r.prop(props, "prop_hide", toggle=True, icon='HIDE_ON')
    if _is_numeric_prop(val): r.prop(props, "prop_toggle", toggle=True, icon='CHECKBOX_HLT')
    if isinstance(val, bpy.types.Collection): r.prop(props, "prop_invert", toggle=True, icon='ARROW_LEFTRIGHT')
    edit.operator("rruf.prop_apply", text="Update Property", icon='FILE_TICK')

class RRUF_PT_Setup(bpy.types.Panel):
    bl_idname = "RRUF_PT_Setup"
    bl_label = "Collection Editor"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RRUF"
    bl_parent_id = "RRUF_PT_Setup_Main"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return bool(context.object and context.object.type == 'ARMATURE' and context.object.data and context.object.data.get(rruf_core.RRUF_TRIGGER_KEY))

    def draw(self, context):
        layout = self.layout
        data = context.object.data
        props = context.scene.rruf_editor

        active_coll = get_active_bone_collection(data)

        if not active_coll:
            layout.label(text="Select a Bone Collection to Edit", icon='INFO')
        else:
            box = layout.box()
            box.label(text="Collection Composer:", icon='OUTLINER_COLLECTION')

            parsed = rruf_core.rruf_engine.parse_collection_syntax(props.tag_buffer, is_editor_preview=True)
            is_master = any(parsed.get(cfg["node_key"]) for cfg in rruf_core.TAG_CONFIG.values() if "root_context" in cfg)

            if not is_master:
                name_row = box.row(align=True)
                name_split = name_row.split(factor=0.75, align=True)
                name_split.prop(props, "display_name")
                name_split.prop(props, "discriminator", text="#")

            if props.tag_buffer:
                tag_box = box.box()
                chunks = [m.group(0) for m in rruf_core._TAG_TUPLE_PATTERN.finditer(props.tag_buffer)]
                col = tag_box.column(align=True)
                for i, chunk in enumerate(chunks):
                    row = col.row(align=True)

                    tag_key = chunk.replace("(", "").replace(")", "").split("[")[0]
                    cfg = rruf_core.TAG_CONFIG.get(tag_key)

                    display_chunk = chunk
                    if cfg and "aliases" in cfg:
                        display_chunk = chunk.replace(tag_key, cfg["aliases"][0])

                    op = row.operator("rruf.editor_action", text=display_chunk, icon='X')
                    op.action = 'REMOVE_TAG'
                    op.tag_key = chunk

                    if i > 0:
                        op = row.operator("rruf.reorder_tag", text="", icon='TRIA_UP')
                        op.direction = 'UP'
                        op.index = i
                    if i < len(chunks) - 1:
                        op = row.operator("rruf.reorder_tag", text="", icon='TRIA_DOWN')
                        op.direction = 'DOWN'
                        op.index = i

            if props.active_tag_to_configure:
                wiz = box.box()
                f = props.active_tag_to_configure

                if f.upper() in ("ICON", "IC"):
                    wiz.prop(props, "icon_search_filter", icon='VIEWZOOM', text="")
                    wiz.template_list("RRUF_UL_icon_grid", "", props, "icon_collection", props, "icon_index", rows=5, type='GRID')

                elif f.upper() in ("HIDE", "H"):
                    wiz.prop(props, "param_buffer_bool", text="Always Hidden (ALW)", toggle=True)

                r = wiz.row(align=True)
                r.operator("rruf.commit_param", text="Confirm", icon='CHECKMARK')
                r.operator("rruf.editor_action", text="Cancel", icon='CANCEL').action = 'CANCEL_PARAM'

            else:
                valid = _get_valid_tags(data, active_coll, props.tag_buffer)
                grid = box.grid_flow(columns=3, align=True)
                for f in valid:
                    cfg = rruf_core.TAG_CONFIG.get(f, {})
                    display_label = cfg.get("aliases", [f])[0]

                    op = grid.operator("rruf.editor_action", text=f"({display_label})")
                    op.action = 'ADD_TAG'
                    op.tag_key = f

            commit_row = box.row(align=True)
            commit_row.operator("rruf.editor_action", text="Update Collection", icon='FILE_TICK').action = 'COMMIT'

            op_deflate = commit_row.operator("rruf.convert_syntax", text="", icon='FULLSCREEN_EXIT')
            op_deflate.target_mode = 'DEFLATE'

            op_inflate = commit_row.operator("rruf.convert_syntax", text="", icon='FULLSCREEN_ENTER')
            op_inflate.target_mode = 'INFLATE'

            _draw_property_composer(layout, active_coll, props)

        layout.separator()

        cb = layout.box()
        cb.label(text="Property Clipboard", icon='COPY_ID')
        cb.operator("rruf.copy_props", icon='COPYDOWN')
        if props.clipboard_data:
            r = cb.row(align=True)
            paste_text = f"Paste to {active_coll.name}" if active_coll else "Paste Properties"
            r.operator("rruf.paste_props", text=paste_text, icon='PASTEDOWN')
            r.operator("rruf.clear_clipboard", text="", icon='X')

        preset_box = layout.box()
        preset_box.label(text="Internal Presets", icon='ASSET_MANAGER')
        r_preset = preset_box.row(align=True)
        r_preset.prop(context.window_manager, "rruf_preset_list", text="")
        r_preset.operator("rruf.load_internal_preset", text="", icon='IMPORT')

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


classes = (
    RRUF_Icon_Item, RRUF_Editor_Props, RRUF_UL_icon_grid,
    RRUF_OT_select_icon_grid, RRUF_OT_sync_editor, RRUF_OT_reorder_tag,
    RRUF_OT_editor_action, RRUF_OT_commit_param, RRUF_OT_enable,
    RRUF_OT_copy_props, RRUF_OT_paste_props, RRUF_OT_clear_clipboard,
    RRUF_OT_export_snapshot, RRUF_OT_import_gatekeeper, RRUF_OT_import_snapshot,
    RRUF_OT_embed_core, RRUF_OT_load_internal_preset, RRUF_PT_Setup,
    RRUF_OT_convert_syntax, RRUF_OT_prop_select, RRUF_OT_prop_apply, RRUF_OT_prop_move
)

def register():
    for c in classes: bpy.utils.register_class(c)
    bpy.types.Scene.rruf_editor = bpy.props.PointerProperty(type=RRUF_Editor_Props)
    bpy.types.WindowManager.rruf_preset_list = bpy.props.EnumProperty(name="Presets", items=_get_json_presets_callback)

def unregister():
    errors = []
    for owner, name in ((bpy.types.WindowManager, "rruf_preset_list"),
                        (bpy.types.Scene, "rruf_editor")):
        if hasattr(owner, name):
            try:
                delattr(owner, name)
            except Exception as e:
                errors.append(e)
                traceback.print_exc()
    for c in reversed(classes):
        if c.is_registered:
            try:
                bpy.utils.unregister_class(c)
            except Exception as e:
                errors.append(e)
                traceback.print_exc()
    if errors:
        raise errors[0]
