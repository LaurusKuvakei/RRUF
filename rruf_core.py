"""
RRUF - Reactive Rig UI Framework.
Copyright (C) 2025-2026 Laurus Kuvakei

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
import bpy
import re
import traceback
import mathutils
import functools
from bpy.app.handlers import persistent
from collections import namedtuple, OrderedDict

RRUFNodeData = namedtuple('RRUFNodeData', ['node', 'coll', 'label', 'is_valid', 'has_children', 'has_overflow'])


RRUF_TRIGGER_KEY = "RRUF_KEY"
# Presence flag in driver_namespace (value is always True, not an owner).
# "King of the hill": while any RRUF core is active, every other start yields,
# including a re-run of the same embedded core. Protects animators who append
# several rigs, each carrying its own embedded copy. The extension always
# takes priority over embedded cores.
RRUF_GLOBAL_LOCK = "RRUF_GLOBAL_LOCK"
MAX_UI_NESTING = 8
_MAX_SYNTAX_CACHE_SIZE = 2000
POLLING_RATE_HZ = 4.0
POPUP_WIDTH = 450
POPUP_KEYBIND = {"type": 'R', "value": 'PRESS', "ctrl": True, "shift": True, "alt": False}

UI_LOCATION_INDEX = 0
_UI_LOCS = [
    ('VIEW_3D',   'UI',     'Tool',      ''),
    ('VIEW_3D',   'UI',     'Animation', ''),
    ('VIEW_3D',   'UI',     'RRUF',   ''),
]
UI_SPACE_TYPE, UI_REGION_TYPE, UI_CATEGORY, UI_CONTEXT = _UI_LOCS[UI_LOCATION_INDEX]

_DYNAMIC_PARAM = "__DYNAMIC__"

CTX_VIS = "VIS"
CTX_PROP = "PROP"
CTX_SNAP = "SNAP"
CTX_CWI = "CWI"

TAG_CONFIG = {
    "DI": {"aliases": ["DISPLAYS"],  "params": None, "validator": None, "node_key": "k_displays", "root_context": CTX_VIS},
    "SE": {"aliases": ["SETTINGS"],  "params": None, "validator": None, "node_key": "k_settings", "root_context": CTX_PROP},
    "SN": {"aliases": ["SNAPS"],     "params": None, "validator": None, "node_key": "k_snaps",    "root_context": CTX_SNAP},
    "IN": {"aliases": ["INTERNALS"], "params": None, "validator": None, "node_key": "k_internals","root_context": CTX_CWI},

    "i":  {"aliases": ["INLINE"], "params": None, "validator": None, "node_key": "k_inline", "contexts": [CTX_VIS, CTX_SNAP]},
    "b":  {"aliases": ["BOARD"],  "params": None, "validator": None, "node_key": "k_board",  "contexts": [CTX_VIS, CTX_PROP, CTX_SNAP]},
    "j":  {"aliases": ["JOIN"],   "params": None, "validator": None, "node_key": "k_join",   "contexts": [CTX_VIS, CTX_PROP, CTX_SNAP]},
    "l":  {"aliases": ["LINK"],   "params": None, "validator": None, "node_key": "k_link",   "contexts": [CTX_PROP]},
    "s":  {"aliases": ["SKIP"],   "params": None, "validator": None, "node_key": "k_skip",   "is_skip": True, "contexts": [CTX_VIS, CTX_PROP, CTX_SNAP]},
    "t":  {"aliases": ["TO"],     "params": None, "validator": None, "node_key": "k_to",     "contexts": [CTX_SNAP]},

    "h":  {"aliases": ["HIDE"],   "params": "ALW", "validator": None, "node_key": "k_hide", "contexts": [CTX_VIS, CTX_PROP, CTX_SNAP]},
    "ic": {"aliases": ["ICON"],   "params": _DYNAMIC_PARAM, "validator": lambda x: x in _get_valid_icons(), "node_key": "k_icon_name", "contexts": [CTX_VIS, CTX_PROP, CTX_SNAP]},
}

_TAG_RESOLVER = {}
_ALL_VALID_TAGS = []
for primary_tag, config in TAG_CONFIG.items():
    _TAG_RESOLVER[primary_tag] = config
    _ALL_VALID_TAGS.append(primary_tag)

    if "aliases" in config:
        for alias in config["aliases"]:
            _TAG_RESOLVER[alias] = config
            _ALL_VALID_TAGS.append(alias)

_ROOT_TAGS = {f"({tag})": config["root_context"] for tag, config in _TAG_RESOLVER.items() if "root_context" in config}
_SKIP_TAGS = {f"({tag})" for tag, config in _TAG_RESOLVER.items() if config.get("is_skip")}

def _get_root_context(raw_name):
    for tag, ctx in _ROOT_TAGS.items():
        if tag in raw_name: return ctx
    return None

_ALL_VALID_TAGS.sort(key=len, reverse=True)

_EXPLICIT_NAME_PATTERN = re.compile(r'\{([^}]+)\}(?:\[[^\]]+\])?')

_PROP_PREFIX_PATTERN = re.compile(r'\{([^}]+)\}\s*')
_PROP_GROUP_PATTERN = re.compile(r'\s*(\d*)(.*)', re.S)

def _parse_prop_key(key):
    order, flags = None, set()
    for group in _PROP_PREFIX_PATTERN.findall(key):
        digits, rest = _PROP_GROUP_PATTERN.match(group).groups()
        if digits and order is None: order = int(digits)
        flags.update(c.lower() for c in rest if not c.isspace())
    return order, flags, _PROP_PREFIX_PATTERN.sub("", key).strip()

def _prop_sort_key(parsed):
    order, _, clean = parsed
    return (order is None, order or 0, clean.lower())

_TAG_TUPLE_PATTERN = re.compile(r'\((%s)\)(?:\s*\[([^\]]+)\])?' % "|".join(map(re.escape, _ALL_VALID_TAGS)))

_TIPS = {
    "SNAP":             ("Snap Hierarchy", "Aligns source bones to target bones based on hierarchy."),
    "SEL_REPLACE":      ("Replace", "Selects bones in the collection and replaces the current selection."),
    "SEL_ADD":          ("Add", "Adds bones in the collection to the current selection."),
    "VIS":              ("Vis", "Toggles viewport visibility for the collection."),
    "SOLO":             ("Solo", "Isolates the collection in the viewport."),
    "SYM":              ("Symmetrize", "Copies custom properties to the linked partner."),
    "SYM_ALL":          ("Symmetrize All", "Symmetrizes all linked property pairs in the rig."),
    "SYM_FOLDER":       ("Symmetrize Folder", "Symmetrizes linked property pairs within the folder and sub-folders."),
    "RESET":            ("Reset", "Resets custom properties to default values."),
    "PROP_TOGGLE":      ("Toggle", "Switches the property between 0 and 1."),
    "DUMMY":            ("", "Indicates active global symmetrization."),
    "EXPAND_ALL":       ("Expand/Collapse", "Expands or collapses all collections in the panel."),
    "RST_SEL_LOC":      ("Loc", "Resets location for selected bones."),
    "RST_SEL_ROT":      ("Rot", "Resets rotation for selected bones."),
    "RST_SEL_SCALE":    ("Scl", "Resets scale for selected bones."),
    "RST_SEL_ALL":      ("All", "Resets all transforms for selected bones."),
    "RST_RIG_LOC":      ("Loc", "Resets location for all visible bones in the rig."),
    "RST_RIG_ROT":      ("Rot", "Resets rotation for all visible bones in the rig."),
    "RST_RIG_SCALE":    ("Scl", "Resets scale for all visible bones in the rig."),
    "RST_RIG_ALL":      ("All", "Resets all transforms for all visible bones in the rig."),
    "SNAP_CURS_TO_SEL": ("Cur->Sel", "Snaps the 3D cursor to the active bone."),
    "SNAP_CURS_TO_SEL_LOC": ("Loc", "Snaps the 3D cursor location to the active bone."),
    "SNAP_CURS_TO_SEL_ROT": ("Rot", "Snaps the 3D cursor rotation to the active bone."),
    "SNAP_CURS_TO_SEL_ALL": ("All", "Snaps the 3D cursor location and rotation to the active bone."),
    "ZERO_AXIS":        ("Zero Axis", "Resets the specified cursor axis to 0.0."),
    "VIS_SEARCH":       ("Search Bone", "Filters visibility groups by bone name."),
    "VIS_SELECT":       ("Select Bone", "Selects the search result and sets it as the active bone."),
    "VIS_ISOLATE":      ("Isolate Search", "Filters the UI to display only collections containing the search result."),
    "VIS_GET_ACTIVE":   ("Get Active", "Populates the search field with the active viewport selection."),
    "VIS_SHOW_ALL":     ("Show All", "Unhides all visibility collections."),
    "VIS_UNSOLO_ALL":   ("Unsolo All", "Disables solo mode across all collections."),
}

RRUF_MANUAL_DATA = [
    ("Workflow Panel", 'TOOL_SETTINGS', [
        ("POSE POSITION: Toggles rig between Pose and Rest state.", 'ARMATURE_DATA', []),
        ("NAVIGATION: Viewport behavior overrides:", 'VIEW3D', [
            ("Orbit Around Selection", 'CENTER_ONLY'),
            ("Zoom to Mouse Position", 'MOUSE_MOVE'),
            ("Auto Depth Calculation", 'DRIVER_DISTANCE')
        ]),
        ("RESETS: Transform clearing options:", 'LOOP_BACK', [
            ("Target Selected Bones Only", 'RESTRICT_SELECT_OFF'),
            ("Target All Visible Bones", 'OUTLINER_OB_ARMATURE'),
            ("Clear Location", 'CON_LOCLIKE'),
            ("Clear Rotation", 'CON_ROTLIKE'),
            ("Clear Scale", 'CON_SIZELIKE'),
            ("Clear All Transforms", 'LOOP_BACK')
        ])
    ]),
    ("Visibility Panel", 'HIDE_OFF', [
        ("SEARCH & FILTER: Find and isolate bones:", 'VIEWZOOM', [
            ("Get Active: Pulls viewport selection to search", 'EYEDROPPER'),
            ("Isolate Search: Filter the UI to matches", 'OUTLINER_COLLECTION'),
            ("Clear active search", 'X')
        ]),
        ("GLOBAL UTILITIES: Batch operations:", 'FULLSCREEN_ENTER', [
            ("Show All Hidden Collections", 'HIDE_OFF'),
            ("Unsolo All Collections", 'SOLO_OFF'),
            ("Expand / Collapse All Folders", 'FULLSCREEN_ENTER')
        ]),
        ("COLLECTION CONTROLS: Per-folder operations:", 'BONE_DATA', [
            ("Replace Selection", 'RESTRICT_SELECT_OFF'),
            ("Add to Selection", 'ADD'),
            ("Toggle Visibility", 'HIDE_ON'),
            ("Toggle Solo Isolation", 'SOLO_ON')
        ])
    ]),
    ("Properties Panel", 'PROPERTIES', [
        ("FILTER: Search properties or collection labels.", 'VIEWZOOM', [
            ("Clear active search", 'X')
        ]),
        ("SYMMETRIZE ALL: Batch-copy linked .L and .R pairs.", 'MOD_MIRROR', []),
        ("COLLECTION CONTROLS: Localized operations:", 'SETTINGS', [
            ("Symmetrize Specific Partner", 'TRIA_RIGHT'),
            ("Reset Folder to Defaults", 'LOOP_BACK')
        ])
    ]),
    ("Snapping Panel", 'SNAP_ON', [
        ("SEARCH & FILTER: Isolate specific snap groups.", 'VIEWZOOM', [
            ("Clear active search", 'X')
        ]),
        ("CURSOR UTILS: Bulk alignment & manual coords:", 'CURSOR', [
            ("Snap Cursor to Selection", 'CON_LOCLIKE'),
            ("Snap Selection to Cursor", 'SNAP_PEEL_OBJECT'),
            ("Manual Transform Popover", 'GIZMO')
        ]),
        ("SNAP GROUPS: Execute source-to-target alignment:", 'GROUP_BONE', [
            ("Snap Location Only", 'CON_LOCLIKE'),
            ("Snap Rotation Only", 'CON_ROTLIKE'),
            ("Snap Scale Only", 'CON_SIZELIKE'),
            ("Snap All Channels", 'SNAP_ON')
        ])
    ])
]


class RRUF_Core_Engine:
    def __init__(self):
        self.data = {}
        self.gatekeeper = {}
        self.search = {}

        self.tick_count = 0
        self.err_count = 0
        self.k_cache = {}
        self.syntax_cache = OrderedDict()

    def get_ui_data(self, session_uid):
        if session_uid in self.data:
            return self.data[session_uid]
        return {
            "node_map": {}, "vis_display": [], "snap_groups": {}, "snap_layout": [],
            "props_others": [], "has_links": False, "found_settings": False,
            "root_collection_names": [], "mch_cwi": True
        }

    def update_ui_data(self, session_uid, packet):
        self.data[session_uid] = packet

    def check_gatekeeper(self, session_uid, current_state):
        if self.gatekeeper.get(session_uid) != current_state:
            self.gatekeeper[session_uid] = current_state
            return True
        return False

    def get_search_cache(self, session_uid):
        if session_uid not in self.search:
            self.search[session_uid] = {'vis': None, 'props': None, 'snaps': None}
        return self.search[session_uid]

    def reset_search_cache(self, session_uid):
        self.search[session_uid] = {'vis': None, 'props': None, 'snaps': None}

    def garbage_collect(self, live_uids):
        dead_keys = [k for k in self.data.keys() if k not in live_uids]
        for k in dead_keys:
            self.data.pop(k, None)
            self.gatekeeper.pop(k, None)
            self.search.pop(k, None)
            self.k_cache.pop(k, None)

    def purge_all(self):
        self.data.clear()
        self.gatekeeper.clear()
        self.search.clear()
        self.syntax_cache.clear()
        self.k_cache.clear()

    def scan_hierarchy(self, armature, old_packet=None):
        old_node_map = old_packet.get("node_map", {}) if old_packet else {}
        old_hashes = old_packet.get("hashes", {}) if old_packet else {}
        node_map = {}
        new_hashes = {}
        roots = []
        traversal_order = []
        stack = []
        mch_cwi = False

        for c in reversed(armature.collections):
            if c.parent is None:
                ctx = _get_root_context(c.name)
                if ctx == CTX_CWI:
                    mch_cwi = True
                if ctx in (CTX_VIS, CTX_PROP, CTX_SNAP):
                    stack.append((c, None, 0, ctx))

        while stack:
            b_coll, parent_id, depth, tree_ctx = stack.pop()
            raw_name = b_coll.name

            if tree_ctx is None:
                tree_ctx = _get_root_context(raw_name)
            if tree_ctx == CTX_CWI:
                continue

            traversal_order.append(raw_name)
            rev_children = list(reversed(b_coll.children))
            child_names = tuple(b_coll.children.keys())
            parsed_props = {}

            for k in b_coll.keys():
                parsed = _parse_prop_key(k)
                if 'x' in parsed[1]: continue
                parsed_props[k] = parsed

            prop_keys = tuple(sorted(parsed_props, key=lambda k: _prop_sort_key(parsed_props[k])))
            bone_names = tuple(b_coll.bones.keys())
            linked_colls = [(val.name, '-' in _parse_prop_key(key)[1]) for key, val in b_coll.items() if isinstance(val, bpy.types.Collection)]

            state_hash = hash((
                b_coll.is_visible, b_coll.is_solo, parent_id, depth, tree_ctx,
                prop_keys, bone_names, child_names, tuple(linked_colls)
            ))
            new_hashes[raw_name] = state_hash
            is_clean = (raw_name in old_hashes and old_hashes[raw_name] == state_hash and raw_name in old_node_map)

            if is_clean:
                node = old_node_map[raw_name].copy()
                node["is_dirty"] = False
                # Reuse parsed fields, but rebuild derived relationships and
                # layouts. Sibling order or surrounding nodes may have changed
                # even when this node's hash is unchanged; retaining these
                # fields would leave stale property links or snap groups.
                node["children"] = []
                node["all_descendants"] = []
                node["link_meta"] = None
                node["ui_layout"] = []
                node.pop("is_snap_group", None)
            else:
                node = {
                    "name": raw_name, "parent": parent_id, "depth": depth,
                    "children": [], "all_descendants": [],
                    "is_visible": b_coll.is_visible, "is_solo": b_coll.is_solo,
                    "label": raw_name, "is_valid": True, "ui_layout": [],
                    "link_meta": None, "tree_type": tree_ctx, "is_dirty": True,
                    "clean_props": {k: parsed_props[k][2] for k in prop_keys},
                    "valid_props": prop_keys,
                    "toggle_props": frozenset(k for k in prop_keys if '^' in parsed_props[k][1]),
                    "linked_outliner_colls": linked_colls
                }

            node_map[raw_name] = node

            if parent_id:
                if parent_id in node_map:
                    node_map[parent_id]["children"].append(raw_name)
            else:
                roots.append(raw_name)

            if any(tag in raw_name for tag in _SKIP_TAGS): continue

            if depth < MAX_UI_NESTING:
                for child in rev_children:
                    stack.append((child, raw_name, depth + 1, node["tree_type"]))
            elif len(rev_children) > 0:
                node["overflow"] = True

        for raw_name in reversed(traversal_order):
            node = node_map[raw_name]
            node["all_descendants"] = []
            for child_id in node["children"]:
                node["all_descendants"].append(child_id)
                node["all_descendants"].extend(node_map.get(child_id, {}).get("all_descendants", []))

        return {
            "node_map": node_map, "hashes": new_hashes,
            "root_collection_names": roots, "traversal_order": traversal_order,
            "mch_cwi": mch_cwi
        }

    def parse_collection_syntax(self, raw_name, tree_ctx=None, depth=0):
        cache_key = (raw_name, tree_ctx, depth)
        if cache_key in self.syntax_cache:
            self.syntax_cache.move_to_end(cache_key)
            return self.syntax_cache[cache_key]

        baked_tags = {
            cfg["node_key"]: (None if cfg.get("params") == _DYNAMIC_PARAM else False)
            for cfg in TAG_CONFIG.values() if "node_key" in cfg
        }

        remain = raw_name
        clean = ""

        m = _EXPLICIT_NAME_PATTERN.search(remain)
        if m:
            clean = m.group(1).strip()
            remain = remain[:m.start()] + remain[m.end():]

        tuples = _TAG_TUPLE_PATTERN.findall(remain)
        remain = _TAG_TUPLE_PATTERN.sub("", remain).strip()

        valid = not bool(remain)
        err = f"SYNTAX ERROR: Unparsable string detected = [ {remain} ]" if remain else None

        if remain: clean = ""

        for f, p in tuples:
            f = f.strip()
            rules = _TAG_RESOLVER.get(f)
            if not rules: continue

            p = p.strip() if p else ""
            if p: p = p[1:-1] if (p.startswith("'") and p.endswith("'")) else p.upper()

            c = rules.get("params")
            msg = None

            if "root_context" in rules and depth > 0:
                msg = f"SYNTAX ERROR: Root tag ({f}) is only allowed on Layer 0"

            elif tree_ctx and "contexts" in rules and tree_ctx not in rules["contexts"]:
                msg = f"SYNTAX ERROR: {f} not allowed in {tree_ctx} context"

            elif c is None and p:
                msg = f"SYNTAX ERROR: {f} takes no parameters"

            elif c is not None and p:
                is_dyn = (c == _DYNAMIC_PARAM) or (isinstance(c, set) and _DYNAMIC_PARAM in c)
                if not is_dyn:
                    if (isinstance(c, set) and p not in c) or (isinstance(c, str) and p != c):
                        msg = f"SYNTAX ERROR: '{p}' is not valid for {f}"

            if not msg and rules.get("validator"):
                if not p: msg = f"SYNTAX ERROR: {f} missing parameter"
                elif not rules["validator"](p): msg = f"SYNTAX ERROR: '{p}' invalid"

            if msg:
                valid = False
                err = msg
            elif "node_key" in rules:
                baked_tags[rules["node_key"]] = p if (c is not None and p) else True

        result = {
            "id": raw_name, "clean_name": clean,
            "is_valid": valid, "error_msg": err, **baked_tags
        }
        self.syntax_cache[cache_key] = result
        if len(self.syntax_cache) > _MAX_SYNTAX_CACHE_SIZE:
            self.syntax_cache.popitem(last=False)
        return result

    def process_parsed_data(self, scan_result):
        nm = scan_result.get("node_map", {})
        for raw_name, node in nm.items():
            if not node.get("is_dirty", True): continue

            parsed = self.parse_collection_syntax(raw_name, node.get("tree_type"), node.get("depth", 0))
            node.update({
                "label": parsed["error_msg"] if not parsed["is_valid"] else parsed["clean_name"],
                "is_valid": parsed["is_valid"],
                "k_inline": parsed.get("k_inline", False),
                "k_link": parsed.get("k_link", False),
                "k_join": parsed.get("k_join", False),
                "k_hide": parsed.get("k_hide", False),
                "k_to": parsed.get("k_to", False),
                "k_skip": parsed.get("k_skip", False),
                "k_displays": parsed.get("k_displays", False),
                "k_settings": parsed.get("k_settings", False),
                "k_snaps": parsed.get("k_snaps", False),
                "k_internals": parsed.get("k_internals", False),
                "k_board": parsed.get("k_board", False),
                "k_icon_name": parsed.get("k_icon_name"),
                "is_dirty": False
            })
        return scan_result

    # Poll the active RRUF armature for changes that affect the generated UI.
    # Panel drawing reads the prepared cache; hierarchy scanning, parsing, and
    # layout preparation happen here when the tracked state changes.
    def tick(self, context):
        self.tick_count += 1

        # Periodically discard cached data for armatures that no longer exist.
        # Parser entries use names rather than armature IDs and have a separate
        # cache size limit.
        # Intervals here are counted in ticks, not seconds: while no RRUF rig
        # is active the timer polls more slowly, so they take longer in real time.
        if self.tick_count % max(1, int(POLLING_RATE_HZ * 10)) == 0:
            live_uids = {a.session_uid for a in bpy.data.armatures}
            self.garbage_collect(live_uids)

        if not context or not context.active_object or context.active_object.type != 'ARMATURE' or not context.active_object.data:
            return False
        obj = context.active_object
        if not obj.data.get(RRUF_TRIGGER_KEY):
            return False

        arm, arm_key = obj.data, obj.data.session_uid

        # Check collection names, visibility, solo state, and parents every tick.
        # This still traverses the collections, but unchanged state avoids the
        # more expensive hierarchy scan and layout rebuild.
        fast_st = tuple((c.name, c.is_visible, c.is_solo, c.parent) for c in arm.collections_all)

        # Refresh property keys, linked collection names, and bone assignments
        # about once per second at any Polling Rate.
        # Property values are read directly by Blender's UI controls, so moving
        # a slider does not require rebuilding the interface.
        if self.tick_count % max(1, int(POLLING_RATE_HZ)) == 0 or arm_key not in self.k_cache:
            self.k_cache[arm_key] = tuple(
                (tuple((k, v.name) if isinstance(v, bpy.types.Collection) else k for k, v in c.items()),
                 tuple(c.bones.keys()))
                for c in arm.collections_all)

        # Rescan only when the combined structural and membership state changes.
        # The scan reuses unchanged nodes; per-node hashes then determine whether
        # the prepared UI and search caches need to be rebuilt.
        if self.check_gatekeeper(arm_key, hash((fast_st, self.k_cache[arm_key]))):
            old_packet = self.get_ui_data(arm_key)
            raw_packet = self.scan_hierarchy(arm, old_packet)

            if raw_packet.get("hashes", {}) != (old_packet.get("hashes", {}) if old_packet else {}) or (old_packet.get("mch_cwi", True) if old_packet else True) != raw_packet.get("mch_cwi", True):
                self.reset_search_cache(arm_key)

                # Apply RRUF's hide/solo relationships and linked scene-collection
                # visibility before preparing the layout. These rules intentionally
                # write collection state and may trigger a follow-up tick to settle it.
                stabilized = _guard_enforcer(arm, old_packet, self.process_parsed_data(raw_packet))
                self.update_ui_data(arm_key, _run_ui_data_preparation(stabilized))
                self.err_count = 0
                return True

        self.err_count = 0
        return False

rruf_engine = RRUF_Core_Engine()


@functools.lru_cache(maxsize=1)
def _get_valid_icons():
    try:
        items = bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items
        return set(items.keys())
    except (AttributeError, KeyError, TypeError):
        return set()

IS_ADDON = __name__ != "__main__"

def get_bone_collection(armature, collection_name):
    if not armature or not collection_name: return None
    return armature.collections_all.get(collection_name)

def _select_bone(arm_obj, data_bone):
    if pb := arm_obj.pose.bones.get(data_bone.name): pb.select = True

def _get_composed_matrix(source_matrix, target_matrix, snap_mode):
    loc_s, rot_s, scl_s = source_matrix.decompose()
    loc_t, rot_t, scl_t = target_matrix.decompose()

    final_loc = loc_t if snap_mode[0] else loc_s
    final_rot = rot_t if snap_mode[1] else rot_s
    final_scl = scl_t if snap_mode[2] else scl_s

    return mathutils.Matrix.LocRotScale(final_loc, final_rot, final_scl)

def _bone_depth(pb):
    depth, p = 0, getattr(pb, "parent", None)
    while p is not None: depth += 1; p = getattr(p, "parent", None)
    return depth

def _capture_locked(pb):
    rot_path = _get_rotation_data_path(pb.rotation_mode)
    return rot_path, tuple(pb.location), tuple(getattr(pb, rot_path)), tuple(pb.scale)

def _restore_locked(pb, saved):
    rot_path, loc, rot, scl = saved
    for i, locked in enumerate(pb.lock_location):
        if locked: pb.location[i] = loc[i]
    for i, locked in enumerate(pb.lock_scale):
        if locked: pb.scale[i] = scl[i]
    rot_val = getattr(pb, rot_path)
    if rot_path == "rotation_euler":
        for i, locked in enumerate(pb.lock_rotation):
            if locked: rot_val[i] = rot[i]
    else:
        for i, locked in enumerate(pb.lock_rotation):
            if locked: rot_val[i + 1] = rot[i + 1]
        if pb.lock_rotations_4d and pb.lock_rotation_w: rot_val[0] = rot[0]

# Assigning a pose matrix directly does not enforce transform locks.
# Restore locked channels before evaluating children so they inherit the
# constrained result rather than the temporary unrestricted transform.
def _set_pose_matrix(pb, matrix):
    saved = _capture_locked(pb)
    pb.matrix = matrix
    _restore_locked(pb, saved)

# Key only the requested transform channels when Blender's Auto Keying is
# enabled. Use the bone's rotation representation and the available/needed
# insertion preferences; property toggles and symmetry do not use this helper.
def _autokey_pose_bones(context, pose_bones, snap_mode):
    if not getattr(context.scene.tool_settings, "use_keyframe_insert_auto", False): return
    edit = getattr(getattr(context, "preferences", None), "edit", None)
    options = set()
    if getattr(edit, "use_keyframe_insert_available", False): options.add('INSERTKEY_AVAILABLE')
    if getattr(edit, "use_keyframe_insert_needed", False): options.add('INSERTKEY_NEEDED')
    for pb in pose_bones:
        paths = []
        if snap_mode[0]: paths.append("location")
        if snap_mode[1]: paths.append(_get_rotation_data_path(pb.rotation_mode))
        if snap_mode[2]: paths.append("scale")
        for path in paths:
            try: pb.keyframe_insert(path, group=pb.name, options=options)
            except RuntimeError: pass

# Pair selection is independent of hierarchy, but transform application is not.
# Apply parents before children and evaluate between depth levels: assigning
# a child against an outdated parent evaluation can leave it offset after snap.
# Both group snapping and Selection to Cursor use this ordering.
def _apply_by_hierarchy(context, items, apply_func):
    levels = {}
    for item in items: levels.setdefault(_bone_depth(item[0]), []).append(item)
    for depth in sorted(levels):
        for item in levels[depth]: apply_func(*item)
        context.view_layer.update()

def _get_even_splits(layout, item_count):
    if item_count == 1: return [layout]
    splits = []

    current_split = layout.row(align=True).split(factor=1.0 / item_count, align=True)
    for k in range(item_count):
        splits.append(current_split)
        if k < item_count - 1:
            current_split = current_split.split(factor=1.0 / (item_count - (k + 1)), align=True)
    return splits

def _iter_layout_nodes(layout_list, nm):
    for item in layout_list:
        if isinstance(item, str):
            node = nm.get(item)
            if node: yield item, node
            if node and node.get("ui_layout"): yield from _iter_layout_nodes(node["ui_layout"], nm)
        elif isinstance(item, list):
            yield from _iter_layout_nodes(item, nm)

def limited_redraw():
    if bpy.app.background:
        return

    try:
        wm = bpy.context.window_manager
        if not wm or not hasattr(wm, "windows"):
            return

        for window in wm.windows:
            if not window or not window.screen:
                continue
            for area in window.screen.areas:
                # The panel is also shown in the Properties editor (Tool tab).
                if area and area.type in (UI_SPACE_TYPE, 'PROPERTIES'):
                    for region in area.regions:
                        if region and region.type in (UI_REGION_TYPE, 'WINDOW'):
                            region.tag_redraw()

    except Exception as e:
        print(f"RRUF UI Redraw Warning: {e}")

def _get_rotation_data_path(rotation_mode):
    if rotation_mode == 'QUATERNION': return "rotation_quaternion"
    if rotation_mode == 'AXIS_ANGLE': return "rotation_axis_angle"
    return "rotation_euler"

def _sync_collection_properties(coll_a, coll_b, sync_a_to_b):
    src, tgt = (coll_a, coll_b) if sync_a_to_b else (coll_b, coll_a)
    for k in src.keys():
        if k not in tgt or isinstance(src[k], bpy.types.Collection): continue
        if 'x' in _parse_prop_key(k)[1]: continue
        val = src[k]
        if hasattr(val, "to_dict"): val = val.to_dict()
        elif hasattr(val, "to_list"): val = val.to_list()
        tgt[k] = val

# Read the scene collections from the bone collection's own properties.
# Looking them up by name would pick the wrong one when a linked library
# brings a collection with the same name as a local one.
def _sync_outliner_collections(b_coll, is_visible):
    for key, item in b_coll.items():
        if not isinstance(item, bpy.types.Collection): continue
        is_inverted = '-' in _parse_prop_key(key)[1]

        target_hidden = is_visible if is_inverted else (not is_visible)

        if item.hide_viewport != target_hidden or item.hide_render != target_hidden:
            item.hide_viewport = target_hidden
            item.hide_render = target_hidden


def _cascade_solo_state(armature, scan_result, start_coll_name, new_state):
    start_collection = get_bone_collection(armature, start_coll_name)
    if not start_collection: return
    node_map = scan_result.get("node_map", {})
    to_toggle_names = []
    stack = [start_coll_name]

    while stack:
        current_name = stack.pop()
        to_toggle_names.append(current_name)
        node = node_map.get(current_name)
        if node: stack.extend(node.get("children", []))

    for name in to_toggle_names:
        coll = get_bone_collection(armature, name)
        if not coll: continue
        final_state = new_state
        node_data = node_map.get(name, {})

        if new_state and node_data.get("k_hide") and not coll.is_visible:
            final_state = False

        if coll.is_solo != final_state: coll.is_solo = final_state
        if node_data: node_data["is_solo"] = final_state


def _get_matches_generic(context, edit_text, cache_key, harvest_callback):
    obj = context.object
    if not obj or obj.type != 'ARMATURE': return []
    uid = obj.data.session_uid
    cache = rruf_engine.get_search_cache(uid)

    if cache[cache_key] is None:
        cache[cache_key] = sorted(list(harvest_callback(obj)))
    query = edit_text.lower()
    return [item for item in cache[cache_key] if query in item.lower()]

def _get_vis_matches(self, context, edit_text):
    def harvest(obj):
        ui_data = rruf_engine.get_ui_data(obj.data.session_uid)
        nm = ui_data.get("node_map", {})
        roots = ui_data.get("root_collection_names", [])
        allowed_bones = set()

        def harvest_bones(node_id):
            node = nm.get(node_id)
            if not node: return
            include_bones = True

            hide_val = node.get("k_hide")
            if hide_val and (hide_val == "ALW" or not node.get("is_visible", True)):
                include_bones = False

            coll = get_bone_collection(obj.data, node_id)
            if coll and include_bones:
                for b in coll.bones: allowed_bones.add(b.name)
            for child_id in node.get("children", []):
                harvest_bones(child_id)

        for r_name in roots:
            root_node = nm.get(r_name)
            if root_node and root_node.get("tree_type") == CTX_VIS:
                harvest_bones(r_name)
        return allowed_bones
    return _get_matches_generic(context, edit_text, 'vis', harvest)

def _get_prop_matches(self, context, edit_text):
    def harvest(obj):
        ui_data = rruf_engine.get_ui_data(obj.data.session_uid)
        nm = ui_data.get("node_map", {})
        candidates = set()
        for item, node in _iter_layout_nodes(ui_data.get("props_others", []), nm):
            coll = get_bone_collection(obj.data, item)
            if coll:
                col_label = node.get('label', item)
                clean_props = node.get("clean_props", {})
                for k in node.get("valid_props", []):
                    clean_k = clean_props.get(k, k)
                    candidates.add(f"{clean_k} ({col_label})")
        return candidates
    return _get_matches_generic(context, edit_text, 'props', harvest)

def _get_snap_matches(self, context, edit_text):
    def harvest(obj):
        ui_data = rruf_engine.get_ui_data(obj.data.session_uid)
        nm = ui_data.get("node_map", {})
        candidates = set()
        for item, node in _iter_layout_nodes(ui_data.get("snap_layout", []), nm):
            if node.get("is_snap_group"):
                label = node.get('label', item)
                parent_id = node.get("parent")
                parent_label = nm[parent_id].get("label", parent_id) if parent_id and parent_id in nm else "Root"
                candidates.add(f"{label} ({parent_label})")
        return candidates
    return _get_matches_generic(context, edit_text, 'snaps', harvest)


def _helper_generate_layout(node_names, node_map):
    layout_rows = []
    current_row_buffer = []
    for name in node_names:
        current_row_buffer.append(name)
        node = node_map.get(name, {})
        if not node.get("k_join"):
            layout_rows.append(current_row_buffer)
            current_row_buffer = []
    if current_row_buffer: layout_rows.append(current_row_buffer)
    return layout_rows

def _helper_prop_linker(node_names, node_map):
    i = 0
    while i < len(node_names):
        curr_name = node_names[i]
        curr_node = node_map.get(curr_name)
        if not curr_node:
            i += 1; continue

        if curr_node.get("k_link") and i + 1 < len(node_names):
            next_name = node_names[i + 1]
            next_node = node_map.get(next_name)
            if next_node:
                curr_node["link_meta"] = {"partner": next_name, "is_source": True}
                next_node["link_meta"] = {"partner": curr_name, "is_source": False}
                i += 2; continue
        i += 1

def _run_ui_data_preparation(scan_result):
    nm = scan_result.get("node_map", {})
    roots = scan_result.get("root_collection_names", [])
    data = {
        "vis_display": [], "snap_groups": {}, "snap_layout": [],
        "props_others": [], "has_links": False, "found_settings": False
    }

    def is_ui_visible(node_id):
        node = nm.get(node_id)
        if not node: return False
        hide_val = node.get("k_hide")
        if hide_val == "ALW": return False
        if hide_val is True and not node["is_visible"]: return False
        if node.get("k_skip"): return False
        return True

    raw_vis, raw_props, raw_snaps = [], [], []

    for r_name in roots:
        node = nm[r_name]
        valid_children = [c for c in node["children"] if is_ui_visible(c)]
        tree_ctx = node.get("tree_type")
        if tree_ctx == CTX_VIS: raw_vis.extend(valid_children)
        elif tree_ctx == CTX_PROP: raw_props.extend(valid_children)
        elif tree_ctx == CTX_SNAP: raw_snaps.extend(valid_children)

    data["vis_display"] = _helper_generate_layout(raw_vis, nm)
    data["props_others"] = _helper_generate_layout(raw_props, nm)
    if raw_props: data["found_settings"] = True

    _helper_prop_linker(raw_props, nm)

    def process_snap_group(group_id):
        node = nm.get(group_id)
        if not node: return
        children = node["children"]
        pair_list = []
        i = 0
        while i < len(children):
            curr_id = children[i]
            curr_node = nm.get(curr_id)
            if curr_node and curr_node.get("k_to") and i + 1 < len(children):
                pair_list.append((curr_id, children[i + 1]))
                i += 2; continue
            i += 1

        if pair_list:
            data["snap_groups"][group_id] = pair_list
            node["is_snap_group"] = True
        else:
            valid_snap_children = [c for c in children if is_ui_visible(c)]
            node["ui_layout"] = _helper_generate_layout(valid_snap_children, nm)
            for c in valid_snap_children: process_snap_group(c)

    valid_raw_snaps = [s for s in raw_snaps if is_ui_visible(s)]
    data["snap_layout"] = _helper_generate_layout(valid_raw_snaps, nm)
    for r in valid_raw_snaps: process_snap_group(r)

    def prepare_recursive(node_id):
        node = nm[node_id]
        valid_children = [c for c in node["children"] if nm.get(c, {}).get("tree_type") == node["tree_type"] and is_ui_visible(c)]
        if node["tree_type"] == CTX_PROP: _helper_prop_linker(valid_children, nm)
        node["ui_layout"] = _helper_generate_layout(valid_children, nm)
        for c in valid_children: prepare_recursive(c)

    for r in raw_vis + raw_props + valid_raw_snaps: prepare_recursive(r)

    for node in nm.values():
        if node.get("link_meta"): data["has_links"] = True; break

    scan_result.update(data)
    return scan_result

def _diff_enforcer_states(old_packet, new_packet, manifest):
    if not old_packet: return {}
    keys = manifest.get("monitored_keys", [])
    diff_log = {}
    old_map, new_map = old_packet["node_map"], new_packet["node_map"]

    for name, new_node in new_map.items():
        if old_node := old_map.get(name):
            changes = {k: new_node[k] for k in keys if new_node[k] != old_node[k]}
            if changes: diff_log[name] = changes
    return diff_log

def _guard_hide_state(arm, change_log, node_map, current_packet, iteration_order):
    did_act = False
    for coll_name in iteration_order:
        node_data = node_map.get(coll_name)

        if not node_data or not node_data.get("k_hide"): continue

        parent_id = node_data.get("parent")
        parent_is_solo = False
        if parent_id and node_map.get(parent_id, {}).get("is_solo"):
            parent_is_solo = True

        changes = change_log.get(coll_name, {})

        if (new_vis := changes.get("is_visible")) is not None:
            if parent_is_solo and new_vis != node_data.get("is_solo"):
                _cascade_solo_state(arm, current_packet, coll_name, new_vis)
                did_act = True

        if not node_data.get("is_visible") and node_data.get("is_solo"):
            _cascade_solo_state(arm, current_packet, coll_name, False)
            did_act = True
    return did_act

def _guard_solo_state(arm, change_log, node_map, current_packet, iteration_order):
    did_act = False
    for coll_name in iteration_order:
        node_data = node_map.get(coll_name)
        if not node_data or not node_data.get("children"): continue

        curr_solo = node_data.get("is_solo")

        relevant_children = [node_map[cid] for cid in node_data["children"] if cid in node_map and not (node_map[cid].get("k_hide") and not node_map[cid].get("is_visible"))]

        if not relevant_children: continue

        all_children_solo = all(c.get("is_solo") for c in relevant_children)
        no_children_solo = not any(c.get("is_solo") for c in relevant_children)

        if curr_solo and no_children_solo:
            if change_log.get(coll_name, {}).get("is_solo") is not True:
                _cascade_solo_state(arm, current_packet, coll_name, False)
                did_act = True
        elif not curr_solo and all_children_solo:
            _cascade_solo_state(arm, current_packet, coll_name, True)
            did_act = True
    return did_act

def _guard_scene_collection(arm, node_map):
    for coll_name, node in node_map.items():
        linked_targets = node.get("linked_outliner_colls", [])
        if linked_targets and (b_coll := get_bone_collection(arm, coll_name)):
            _sync_outliner_collections(b_coll, b_coll.is_visible)

def _guard_enforcer(arm, old_packet, current_packet):
    node_map = current_packet.get("node_map", {})
    traversal_order = current_packet.get("traversal_order", [])
    if not node_map or not traversal_order: return current_packet

    change_log = _diff_enforcer_states(old_packet, current_packet, {"monitored_keys": ["is_visible", "is_solo"]})
    act_h = _guard_hide_state(arm, change_log, node_map, current_packet, traversal_order)
    act_s_up = _guard_solo_state(arm, change_log, node_map, current_packet, reversed(traversal_order))

    if act_h or act_s_up:
        for coll in arm.collections_all:
            if node := node_map.get(coll.name):
                node["is_visible"], node["is_solo"] = coll.is_visible, coll.is_solo

    _guard_scene_collection(arm, node_map)
    return current_packet


_IDLE_INTERVAL = 0.5

# Keep one polling timer for this core instance. The configured rate is
# independent of the scene frame rate. Returning None stops this timer.
def rruf_main_loop_timer():
    try:
        ctx = getattr(bpy, "context", None)
        if rruf_engine.tick(ctx):
            limited_redraw()
        interval = 1.0 / POLLING_RATE_HZ
        # Poll less often while no RRUF armature is active. tick() already
        # returns early in that case; this only reduces idle wake-ups.
        obj = getattr(ctx, "active_object", None) if ctx else None
        if not (obj and obj.type == 'ARMATURE' and obj.data and obj.data.get(RRUF_TRIGGER_KEY)):
            return max(interval, _IDLE_INTERVAL)
        return interval
    except Exception:
        traceback.print_exc()
        rruf_engine.err_count += 1
        # Stop after repeated failures to avoid an endless stream of tracebacks.
        # A completed active-armature update resets the error counter.
        # In extension mode the killswitch clears caches but leaves registration
        # intact; disable and re-enable RRUF to restart the timer.
        if rruf_engine.err_count > 5:
            print("RRUF: Too many consecutive errors. Stopping the update timer.")
            _rruf_killswitch_engage(None)
            return None
        return 1.0 / POLLING_RATE_HZ

@persistent
def _rruf_killswitch_engage(dummy):
    # Clear cached rig data before loading another file.
    # An embedded core belongs to the current file, so it also stops its timer
    # and removes its registrations. The installed extension remains active
    # across file loads and only needs its caches cleared.
    # This handler also serves as the shutdown entry point during takeover.
    rruf_engine.purge_all()
    if not IS_ADDON:
        _remove_popup_keymap()
        if bpy.app.timers.is_registered(rruf_main_loop_timer):
            bpy.app.timers.unregister(rruf_main_loop_timer)
        for c in reversed(_CLASSES):
            try: bpy.utils.unregister_class(c)
            except Exception: pass
        if hasattr(bpy.types.Armature, "rruf"): del bpy.types.Armature.rruf
        if hasattr(bpy.types.WindowManager, "rruf_active_tab"): del bpy.types.WindowManager.rruf_active_tab
        if hasattr(bpy.types.WindowManager, "rruf_show_workflow"): del bpy.types.WindowManager.rruf_show_workflow
        bpy.app.driver_namespace.pop(RRUF_GLOBAL_LOCK, None)
        _rruf_killswitch_defuser()
        # print("RRUF: Embedded core unregistered.")  # debug

# Remove RRUF shutdown handlers, walking backward so removals do not skip
# entries. Name matching also finds handlers from older embedded copies.
# This only removes handlers and clears this engine's caches; it does not
# stop another instance's timer or unregister its classes.
# During takeover, dismiss other instances before calling this function.
def _rruf_killswitch_defuser():
    defused = False
    for h_list in (bpy.app.handlers.load_pre, bpy.app.handlers.load_post):
        for i in range(len(h_list) - 1, -1, -1):
            func = h_list[i]
            if func == _rruf_killswitch_engage or getattr(func, "__name__", "") == "_rruf_killswitch_engage":
                h_list.pop(i)
                defused = True
    # if defused: print("RRUF: Killswitch defused.")  # debug
    rruf_engine.purge_all()


class RRUF_OperatorMixin:
    _metadata_key = ""

    @classmethod
    def poll(cls, context):
        return (context.active_object and
                context.active_object.type == 'ARMATURE' and
                context.mode == 'POSE')

    @classmethod
    def description(cls, context, properties):
        if not cls._metadata_key: return ""
        meta = _TIPS.get(cls._metadata_key)
        if meta and isinstance(meta, tuple): return meta[1]
        return ""

class RRUF_OT_get_active_bone_to_search(bpy.types.Operator, RRUF_OperatorMixin):
    bl_idname = "rruf.get_active_bone_to_search"
    bl_label = "Get Active"
    _metadata_key = "VIS_GET_ACTIVE"
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        if pb := context.active_pose_bone:
            context.object.data.rruf.vis_search = pb.name
        return {'FINISHED'}

class RRUF_OT_zero_cursor_axis(bpy.types.Operator, RRUF_OperatorMixin):
    bl_idname = "rruf.zero_cursor_axis"
    bl_label = "Zero Axis"
    bl_options = {'UNDO'}
    _metadata_key = "ZERO_AXIS"

    axis: bpy.props.IntProperty()
    transform_type: bpy.props.StringProperty(default='ROT')

    def execute(self, context):
        cursor = context.scene.cursor
        if self.transform_type == 'LOC':
            val = cursor.location.copy()
            if not 0 <= self.axis < len(val): return {'CANCELLED'}
            val[self.axis] = 0.0
            cursor.location = val
        else:
            data_path = _get_rotation_data_path(cursor.rotation_mode)
            val = getattr(cursor, data_path).copy()
            if not 0 <= self.axis < len(val): return {'CANCELLED'}
            is_quat_w = cursor.rotation_mode == 'QUATERNION' and self.axis == 0
            val[self.axis] = 1.0 if is_quat_w else 0.0
            setattr(cursor, data_path, val)
        return {'FINISHED'}

class RRUF_OT_reset_pose_transforms(bpy.types.Operator):
    bl_idname = "rruf.reset_pose_transforms"
    bl_label = "Reset Pose"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.active_object and
                context.active_object.type == 'ARMATURE' and
                context.mode == 'POSE')

    subset: bpy.props.EnumProperty(items=[('SELECTED', "Selected", ""), ('ALL', "All", "")], default='SELECTED')
    clear_type: bpy.props.EnumProperty(items=[('LOC', "Location", ""), ('ROT', "Rotation", ""), ('SCALE', "Scale", ""), ('ALL', "All", "")], default='ALL')

    @classmethod
    def description(cls, context, properties):
        mode_prefix = "RST_SEL" if properties.subset == 'SELECTED' else "RST_RIG"
        key = f"{mode_prefix}_{properties.clear_type}"
        return _TIPS.get(key, ("", "Reset Pose Transforms"))[1]

    def execute(self, context):
        def run_clear_cmds():
            if self.clear_type == 'LOC': bpy.ops.pose.loc_clear()
            elif self.clear_type == 'ROT': bpy.ops.pose.rot_clear()
            elif self.clear_type == 'SCALE': bpy.ops.pose.scale_clear()
            elif self.clear_type == 'ALL': bpy.ops.pose.transforms_clear()

        if self.subset == 'SELECTED':
            if not context.selected_pose_bones:
                self.report({'WARNING'}, "No bones selected")
                return {'CANCELLED'}
            run_clear_cmds()
        elif self.subset == 'ALL':
            selected_names = [b.name for b in context.selected_pose_bones]
            bpy.ops.pose.select_all(action='SELECT')
            run_clear_cmds()
            bpy.ops.pose.select_all(action='DESELECT')
            for name in selected_names:
                if pb := context.object.pose.bones.get(name):
                    _select_bone(context.object, pb.bone)
        return {'FINISHED'}

class RRUF_OT_expand_all(bpy.types.Operator, RRUF_OperatorMixin):
    bl_idname = "rruf.expand_all"
    bl_label = "Expand/Collapse"
    _metadata_key = "EXPAND_ALL"
    bl_options = {'UNDO'}

    action: bpy.props.EnumProperty(items=[('EXPAND', "Expand", ""), ('COLLAPSE', "Collapse", "")], default='EXPAND')
    panel: bpy.props.EnumProperty(items=[('VIS', "Visibility", ""), ('PROPS', "Properties", ""), ('SNAP', "Snapping", "")], default='VIS')

    def execute(self, context):
        arm = context.object.data
        ui_data = rruf_engine.get_ui_data(arm.session_uid)
        nm = ui_data.get("node_map", {})
        allowed_collections = set()

        def harvest_from_layout(layout_list):
            if not layout_list: return
            for row in layout_list:
                if isinstance(row, list):
                    for item in row:
                        if isinstance(item, str):
                            allowed_collections.add(item)
                            node = nm.get(item)
                            if node and node.get("ui_layout"): harvest_from_layout(node["ui_layout"])

        if self.panel == 'VIS': harvest_from_layout(ui_data.get("vis_display", []))
        elif self.panel == 'PROPS': harvest_from_layout(ui_data.get("props_others", []))
        elif self.panel == 'SNAP': harvest_from_layout(ui_data.get("snap_layout", []))

        target_state = (self.action == 'EXPAND')
        for coll_name in allowed_collections:
            if coll := get_bone_collection(arm, coll_name):
                coll.is_expanded = target_state

        context.view_layer.update()
        return {'FINISHED'}

class RRUF_OT_snap_cursor_utils(bpy.types.Operator):
    bl_idname = "rruf.snap_cursor_utils"
    bl_label = "Cursor Snap"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.active_object and
                context.active_object.type == 'ARMATURE' and
                context.mode == 'POSE')

    action: bpy.props.EnumProperty(
        items=[
            ('CURS_TO_SEL_LOC', "Cursor -> Sel (Loc)", ""),
            ('CURS_TO_SEL_ROT', "Cursor -> Sel (Rot)", ""),
            ('CURS_TO_SEL_ALL', "Cursor -> Sel (All)", ""),
            ('SEL_TO_CURS_LOC', "Sel -> Cursor (Loc)", ""),
            ('SEL_TO_CURS_ROT', "Sel -> Cursor (Rot)", ""),
            ('SEL_TO_CURS_ALL', "Sel -> Cursor (All)", ""),
        ]
    )

    @classmethod
    def description(cls, context, properties):
        key = f"SNAP_{properties.action}"
        return _TIPS.get(key, ("", "Cursor Snap Operations"))[1]

    def execute(self, context):
        obj = context.active_object
        cursor = context.scene.cursor

        if self.action.startswith('CURS_TO_SEL'):
            pb = context.active_pose_bone
            if not pb:
                self.report({'WARNING'}, "No active bone")
                return {'CANCELLED'}
            world_matrix = obj.matrix_world @ pb.matrix
            loc, rot, _ = world_matrix.decompose()
            if 'LOC' in self.action or 'ALL' in self.action: cursor.location = loc
            if 'ROT' in self.action or 'ALL' in self.action:
                if cursor.rotation_mode == 'QUATERNION': cursor.rotation_quaternion = rot
                elif cursor.rotation_mode == 'AXIS_ANGLE':
                    axis, angle = rot.to_axis_angle()
                    cursor.rotation_axis_angle = (angle, *axis)
                else: cursor.rotation_euler = rot.to_euler(cursor.rotation_mode)
            return {'FINISHED'}

        bones = context.selected_pose_bones
        if not bones:
            self.report({'WARNING'}, "No bones selected")
            return {'CANCELLED'}

        if 'LOC' in self.action: snap_mode = (True, False, False)
        elif 'ROT' in self.action: snap_mode = (False, True, False)
        else: snap_mode = (True, True, False)

        mw = obj.matrix_world
        mw_inv = mw.inverted()
        target_matrix = cursor.matrix

        def apply(pb):
            composed_world = _get_composed_matrix(mw @ pb.matrix, target_matrix, snap_mode)
            _set_pose_matrix(pb, mw_inv @ composed_world)

        _apply_by_hierarchy(context, [(pb,) for pb in bones], apply)
        _autokey_pose_bones(context, bones, snap_mode)
        return {'FINISHED'}

class RRUF_OT_snap_hierarchy_batch(bpy.types.Operator, RRUF_OperatorMixin):
    bl_idname = "rruf.snap_hierarchy_batch"
    bl_label = "Snap Hierarchy"
    _metadata_key = "SNAP"
    bl_options = {'UNDO'}

    parent_collection_name: bpy.props.StringProperty()
    snap_loc: bpy.props.BoolProperty(default=True)
    snap_rot: bpy.props.BoolProperty(default=True)
    snap_scale: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        obj, data = context.active_object, context.active_object.data
        mode = (self.snap_loc, self.snap_rot, self.snap_scale)
        ui = rruf_engine.get_ui_data(data.session_uid)
        pairs = ui.get("snap_groups", {}).get(self.parent_collection_name, [])
        if not pairs: return {'CANCELLED'}

        bones = obj.pose.bones
        pre_sorted_pairs = []
        # Collection sibling order defines the source/target collection pairs.
        # Within each pair, alphabetical bone names define positional matching;
        # they do not determine the order in which transforms are applied.
        for s, t in pairs:
            sc, tc = get_bone_collection(data, s), get_bone_collection(data, t)
            if sc and tc:
                src_list = sorted(sc.bones, key=lambda b: b.name)
                tgt_list = sorted(tc.bones, key=lambda b: b.name)
                pre_sorted_pairs.append((src_list, tgt_list))

        if not pre_sorted_pairs: return {'CANCELLED'}

        items = []
        for srcs, tgts in pre_sorted_pairs:
            # A single target supports many sources snapping to one reference.
            # Otherwise zip pairs by position and leaves surplus bones untouched.
            for s, t in (((s, tgts[0]) for s in srcs) if len(tgts) == 1 else zip(srcs, tgts)):
                pb_s, pb_t = bones.get(s.name), bones.get(t.name)
                if pb_s and pb_t: items.append((pb_s, pb_t))
        if not items: return {'CANCELLED'}

        def apply(pb_s, pb_t):
            _set_pose_matrix(pb_s, _get_composed_matrix(pb_s.matrix, pb_t.matrix, mode))

        # Retain one pass per collection pair so evaluated dependencies across
        # pairs can settle. Parent-first application handles chains within a pass;
        # this pass count is not a general convergence guarantee for every rig.
        for _ in range(max(1, len(pre_sorted_pairs))):
            _apply_by_hierarchy(context, items, apply)

        _autokey_pose_bones(context, [pb_s for pb_s, _ in items], mode)
        return {'FINISHED'}

class RRUF_OT_select_collection(bpy.types.Operator, RRUF_OperatorMixin):
    bl_idname = "rruf.select_collection"
    bl_label = "Select Collection"
    bl_options = {'UNDO'}

    collection_name: bpy.props.StringProperty()
    replace: bpy.props.BoolProperty(default=True)

    @classmethod
    def description(cls, context, properties):
        return _TIPS.get("SEL_REPLACE" if properties.replace else "SEL_ADD", ("", ""))[1]

    def execute(self, ctx):
        arm_obj = ctx.active_object
        arm = arm_obj.data
        ui = rruf_engine.get_ui_data(arm.session_uid)
        node = ui.get("node_map", {}).get(self.collection_name, {})
        targets = [self.collection_name] + node.get("all_descendants", [])

        if self.replace: bpy.ops.pose.select_all(action='DESELECT')

        for t in targets:
            if c := get_bone_collection(arm, t):
                for b in c.bones: _select_bone(arm_obj, b)

        limited_redraw()
        return {'FINISHED'}

class RRUF_OT_vis_toggle(bpy.types.Operator, RRUF_OperatorMixin):
    bl_idname = "rruf.vis_toggle"
    bl_label = "Vis"
    _metadata_key = "VIS"
    bl_options = {'UNDO'}

    collection_name: bpy.props.StringProperty()

    def execute(self, ctx):
        arm = ctx.active_object.data
        if c := get_bone_collection(arm, self.collection_name):
            c.is_visible = not c.is_visible
            node = rruf_engine.get_ui_data(arm.session_uid).get("node_map", {}).get(self.collection_name, {})
            if node.get("linked_outliner_colls"):
                _sync_outliner_collections(c, c.is_visible)

        limited_redraw()
        return {'FINISHED'}

class RRUF_OT_vis_show_all(bpy.types.Operator, RRUF_OperatorMixin):
    bl_idname = "rruf.vis_show_all"
    bl_label = "Show All"
    _metadata_key = "VIS_SHOW_ALL"
    bl_options = {'UNDO'}

    def execute(self, context):
        arm = context.object.data
        nm = rruf_engine.get_ui_data(arm.session_uid).get("node_map", {})

        for node_id, node in nm.items():
            if node.get("tree_type") == CTX_VIS and not node.get("k_hide"):
                if c := get_bone_collection(arm, node_id):
                    if not c.is_visible:
                        c.is_visible = True
                        if node.get("linked_outliner_colls"):
                            _sync_outliner_collections(c, True)

        limited_redraw()
        return {'FINISHED'}

class RRUF_OT_vis_unsolo_all(bpy.types.Operator, RRUF_OperatorMixin):
    bl_idname = "rruf.vis_unsolo_all"
    bl_label = "Unsolo All"
    _metadata_key = "VIS_UNSOLO_ALL"
    bl_options = {'UNDO'}

    def execute(self, context):
        for coll in context.object.data.collections_all:
            if coll.is_solo: coll.is_solo = False
        rruf_main_loop_timer()
        limited_redraw()
        return {'FINISHED'}

class RRUF_OT_solo_toggle(bpy.types.Operator, RRUF_OperatorMixin):
    bl_idname = "rruf.solo_toggle"
    bl_label = "Solo"
    _metadata_key = "SOLO"
    bl_options = {'UNDO'}

    collection_name: bpy.props.StringProperty()

    def execute(self, ctx):
        arm = ctx.active_object.data
        if c := get_bone_collection(arm, self.collection_name):
            ui_data = rruf_engine.get_ui_data(arm.session_uid)
            _cascade_solo_state(arm, ui_data, self.collection_name, not c.is_solo)
            limited_redraw()
        return {'FINISHED'}

class RRUF_OT_prop_symmetrize(bpy.types.Operator, RRUF_OperatorMixin):
    bl_idname = "rruf.prop_symmetrize"
    bl_label = "Symmetrize"
    _metadata_key = "SYM"
    bl_options = {'UNDO'}

    sync_left_to_right: bpy.props.BoolProperty()
    l_name: bpy.props.StringProperty()
    r_name: bpy.props.StringProperty()

    def execute(self, ctx):
        arm = ctx.active_object.data
        l, r = get_bone_collection(arm, self.l_name), get_bone_collection(arm, self.r_name)
        if l and r: _sync_collection_properties(l, r, self.sync_left_to_right)
        ctx.active_object.update_tag()
        limited_redraw()
        return {'FINISHED'}

class RRUF_OT_prop_symmetrize_folder(bpy.types.Operator, RRUF_OperatorMixin):
    bl_idname = "rruf.prop_symmetrize_folder"
    bl_label = "Symmetrize Folder"
    _metadata_key = "SYM_FOLDER"
    bl_options = {'UNDO'}

    sync_left_to_right: bpy.props.BoolProperty()
    collection_name: bpy.props.StringProperty()

    def execute(self, ctx):
        arm = ctx.active_object.data
        ui_data = rruf_engine.get_ui_data(arm.session_uid)
        node_map = ui_data.get("node_map", {})
        node = node_map.get(self.collection_name, {})

        targets = [self.collection_name] + node.get("all_descendants", [])
        did_sync = False

        for name in targets:
            curr_node = node_map.get(name, {})
            meta = curr_node.get("link_meta")
            if meta and meta.get("is_source"):
                l = get_bone_collection(arm, name)
                r = get_bone_collection(arm, meta.get("partner"))
                if l and r:
                    _sync_collection_properties(l, r, self.sync_left_to_right)
                    did_sync = True

        if did_sync:
            ctx.active_object.update_tag()
            limited_redraw()
        return {'FINISHED'}

def _is_toggleable_value(val):
    return isinstance(val, (int, float)) and not isinstance(val, bool)

class RRUF_OT_prop_toggle(bpy.types.Operator, RRUF_OperatorMixin):
    bl_idname = "rruf.prop_toggle"
    bl_label = "Toggle"
    _metadata_key = "PROP_TOGGLE"
    bl_options = {'UNDO', 'INTERNAL'}

    collection_name: bpy.props.StringProperty()
    prop_key: bpy.props.StringProperty()

    def execute(self, ctx):
        coll = get_bone_collection(ctx.active_object.data, self.collection_name)
        if not coll or self.prop_key not in coll: return {'CANCELLED'}
        val = coll[self.prop_key]
        if not _is_toggleable_value(val): return {'CANCELLED'}

        new_val = 0 if val >= 0.5 else 1
        coll[self.prop_key] = float(new_val) if isinstance(val, float) else new_val
        ctx.active_object.update_tag()
        limited_redraw()
        return {'FINISHED'}

class RRUF_OT_reset(bpy.types.Operator, RRUF_OperatorMixin):
    bl_idname = "rruf.reset"
    bl_label = "Reset"
    _metadata_key = "RESET"
    bl_options = {'UNDO'}

    collection_name: bpy.props.StringProperty()

    def execute(self, ctx):
        arm = ctx.active_object.data
        ui_data = rruf_engine.get_ui_data(arm.session_uid)
        node = ui_data.get("node_map", {}).get(self.collection_name, {})
        targets = [self.collection_name] + node.get("all_descendants", [])
        did_reset = False

        for t_name in targets:
            if c := get_bone_collection(arm, t_name):
                for k in c.keys():
                    if 'x' not in _parse_prop_key(k)[1]:
                        try:
                            if (default_val := c.id_properties_ui(k).as_dict().get("default")) is not None:
                                cur_val = c[k]
                                if hasattr(cur_val, "__len__") and not isinstance(cur_val, (str, bytes)) and not hasattr(default_val, "__len__"):
                                    c[k] = [default_val] * len(cur_val)
                                else:
                                    c[k] = default_val
                                did_reset = True
                        except Exception: pass

        if did_reset:
            ctx.active_object.update_tag()
            limited_redraw()
            self.report({'INFO'}, f"Reset properties for {len(targets)} collections.")
        return {'FINISHED'}

class RRUF_OT_select_from_search(bpy.types.Operator, RRUF_OperatorMixin):
    bl_idname = "rruf.select_from_search"
    bl_label = "Select"
    _metadata_key = "VIS_SELECT"
    bl_options = {'UNDO'}

    def execute(self, context):
        obj, arm = context.object, context.object.data
        query = arm.rruf.vis_search.strip()
        if not query: return {'CANCELLED'}

        if target_pb := obj.pose.bones.get(query):
            bpy.ops.pose.select_all(action='DESELECT')
            _select_bone(obj, target_pb.bone)
            arm.bones.active = target_pb.bone
            return {'FINISHED'}

        self.report({'WARNING'}, f"Bone '{query}' not found")
        return {'CANCELLED'}

class RRUF_OT_popup_ui(bpy.types.Operator):
    bl_idname = "rruf.popup_ui"
    bl_label = "RRUF Quick Menu"
    bl_description = "Open the Reactive Rig UI at the mouse cursor"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return bool(context.mode == 'POSE' and obj and obj.type == 'ARMATURE' and obj.data and obj.data.get(RRUF_TRIGGER_KEY))

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=POPUP_WIDTH)

    def draw(self, context):
        layout = self.layout
        header = layout.row()
        header.alignment = 'CENTER'
        header.label(text="Reactive Rig UI", icon='OUTLINER_OB_ARMATURE')
        layout.separator()
        draw_rruf_main_ui(layout, context)

    def execute(self, context):
        return {'FINISHED'}


class RRUF_PT_Main(bpy.types.Panel):
    bl_idname = "RRUF_PT_Main"
    bl_label = "Reactive Rig UI"
    bl_space_type = UI_SPACE_TYPE
    bl_region_type = UI_REGION_TYPE
    bl_category = UI_CATEGORY
    bl_context = UI_CONTEXT

    @classmethod
    def poll(cls, context):
        obj = context.object
        return bool(context.mode == 'POSE' and obj and obj.type == 'ARMATURE' and obj.data and obj.data.get(RRUF_TRIGGER_KEY))

    def draw_header_preset(self, context):
        self.layout.popover(panel="RRUF_PT_manual_popover", text="", icon='INFO')

    def draw(self, context):
        draw_rruf_main_ui(self.layout, context)

class RRUF_PT_manual_popover(bpy.types.Panel):
    bl_label = "Info"
    bl_idname = "RRUF_PT_manual_popover"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_ui_units_x = 28

    def draw(self, context):
        layout = self.layout

        for panel_title, header_icon, categories in RRUF_MANUAL_DATA:
            header_box = layout.box()
            header_row = header_box.row()
            header_row.alignment = 'CENTER'
            header_row.label(text=panel_title, icon=header_icon)

            col = layout.column()
            col.separator(factor=0.5)

            for cat_text, cat_icon, sub_items in categories:
                col.label(text=cat_text, icon=cat_icon)

                if sub_items:
                    for sub_text, sub_icon in sub_items:
                        row = col.row(align=True)
                        row.separator(factor=2.0)
                        row.label(text=sub_text, icon=sub_icon)

            layout.separator(factor=2.0)

class RRUF_PT_cursor_transform_popover(bpy.types.Panel):
    bl_label = "Cursor Transform"
    bl_idname = "RRUF_PT_cursor_transform_popover"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'

    def draw(self, context):
        layout = self.layout
        cursor = context.scene.cursor
        rot_mode = cursor.rotation_mode
        split = layout.split(factor=0.5)

        col_loc = split.column(align=True)
        col_loc.label(text="Location", icon='CON_LOCLIKE')
        col_loc.separator()
        for i, char in zip([0, 1, 2], ["X", "Y", "Z"]):
            row = col_loc.row(align=True)
            row.prop(cursor, "location", index=i, text=char)
            op = row.operator("rruf.zero_cursor_axis", text="", icon='LOOP_BACK')
            op.axis = i
            op.transform_type = 'LOC'

        col_rot = split.column(align=True)
        col_rot.label(text=f"Rot: {rot_mode.replace('_', ' ').title()}", icon='ORIENTATION_GIMBAL')
        col_rot.separator()
        data_path = _get_rotation_data_path(rot_mode)

        if rot_mode in ('QUATERNION', 'AXIS_ANGLE'):
            components = [0, 1, 2, 3]; labels = ["W", "X", "Y", "Z"]
        else:
            components = [0, 1, 2]; labels = ["X", "Y", "Z"]

        for i, char in zip(components, labels):
            row = col_rot.row(align=True)
            row.prop(cursor, data_path, index=i, text=char)
            op = row.operator("rruf.zero_cursor_axis", text="", icon='LOOP_BACK')
            op.axis = i
            op.transform_type = 'ROT'


def draw_rruf_main_ui(layout, context):
    wm = context.window_manager
    ui_data = rruf_engine.get_ui_data(context.object.data.session_uid)

    if not ui_data.get("mch_cwi", True):
        box = layout.box()
        row = box.row(align=True)
        row.alert = True
        row.label(text="ACCESS DENIED", icon='LOCKED')
        box.label(text="Internal mechanism collection missing.", icon='ERROR')
        box.label(text="Add a root collection with the (IN) tag.", icon='INFO')
        return

    roots = ui_data.get("root_collection_names", [])
    nm = ui_data.get("node_map", {})
    has_vis = any(nm.get(r, {}).get("tree_type") == 'VIS' for r in roots)
    has_props = any(nm.get(r, {}).get("tree_type") == 'PROP' for r in roots)
    has_snaps = any(nm.get(r, {}).get("tree_type") == 'SNAP' for r in roots)

    if not (has_vis or has_props or has_snaps):
        box = layout.box()
        row = box.row(align=True)
        row.alert = True
        row.label(text="NO UI CONFIGURED", icon='INFO')
        box.label(text="No user-facing collections found.", icon='ERROR')
        box.label(text="Add a root collection with (DI), (SE), or (SN).", icon='INFO')
        return

    box = layout.box()
    row = box.row()
    icon = 'TRIA_DOWN' if wm.rruf_show_workflow else 'TRIA_RIGHT'
    row.prop(wm, "rruf_show_workflow", icon=icon, text="Workflow", emboss=False)

    if wm.rruf_show_workflow:
        inner_col = box.column()
        inner_col.separator(factor=0.5)
        draw_rruf_workflow(inner_col, context)

    layout.separator()
    row = layout.row(align=True)

    if has_vis: row.prop_enum(wm, "rruf_active_tab", 'VIS')
    if has_props: row.prop_enum(wm, "rruf_active_tab", 'PROPS')
    if has_snaps: row.prop_enum(wm, "rruf_active_tab", 'SNAP')

    layout.separator()
    tab = wm.rruf_active_tab

    if tab == 'VIS' and has_vis:
        if not ui_data.get('vis_display'): layout.label(text="No Visibility collections found.", icon='INFO')
        else: draw_rruf_vis(layout, context)
    elif tab == 'PROPS' and has_props:
        if not ui_data.get("found_settings", False): layout.label(text="No Properties collections found.", icon='INFO')
        else: draw_rruf_props(layout, context)
    elif tab == 'SNAP' and has_snaps:
        if not ui_data.get("snap_layout"): layout.label(text="No Snapping collection groups found.", icon='INFO')
        else: draw_rruf_snap(layout, context)

def draw_rruf_workflow(layout, context):
    obj = context.active_object
    arm = obj.data
    prefs = context.preferences.inputs

    row = layout.row(align=True)
    row.prop(arm, "pose_position", expand=True)
    row.separator(factor=0.5)
    row.prop(prefs, "use_rotate_around_active", text="", icon='CENTER_ONLY')
    row.prop(prefs, "use_zoom_to_mouse", text="", icon='MOUSE_MOVE')
    row.prop(prefs, "use_mouse_depth_navigate", text="", icon='DRIVER_DISTANCE')
    layout.separator()

    row = layout.row(align=True)
    split_resets = row.split(factor=0.5, align=True)

    for subset, icon_id in [('SELECTED', 'RESTRICT_SELECT_OFF'), ('ALL', 'OUTLINER_OB_ARMATURE')]:
        sub_row = split_resets.row(align=True)
        sub_row.alignment = 'CENTER'
        sub_row.label(text="", icon=icon_id)
        for t, i in [('LOC','CON_LOCLIKE'),('ROT','CON_ROTLIKE'),('SCALE','CON_SIZELIKE'),('ALL','LOOP_BACK')]:
            op = sub_row.operator("rruf.reset_pose_transforms", text="", icon=i)
            op.subset = subset
            op.clear_type = t

def draw_rruf_vis(layout, context):
    arm = context.object.data
    ui = rruf_engine.get_ui_data(arm.session_uid)
    row = layout.row(align=False)

    sub_left = row.row(align=True)
    sub_left.operator("rruf.get_active_bone_to_search", text="", icon='EYEDROPPER')
    sub_left.prop(arm.rruf, "vis_search", text="", icon='VIEWZOOM')

    sub_select = sub_left.row(align=True)
    sub_select.enabled = bool(arm.rruf.vis_search)
    sub_select.operator("rruf.select_from_search", text="", icon='RESTRICT_SELECT_OFF')
    sub_left.prop(arm.rruf, "vis_is_isolated", text="", toggle=True, icon='OUTLINER_COLLECTION')

    row.separator(factor=0.4)
    sub_right = row.row(align=True)
    sub_right.operator("rruf.vis_show_all", text="", icon='HIDE_OFF')
    sub_right.operator("rruf.vis_unsolo_all", text="", icon='SOLO_OFF')

    if not (arm.rruf.vis_search and arm.rruf.vis_is_isolated):
        row.separator(factor=0.4)
        _draw_expand_buttons(row, 'VIS')

    layout.separator()

    if arm.rruf.vis_search and arm.rruf.vis_is_isolated:
        _draw_vis_isolated(layout, arm, ui, arm.rruf.vis_search)
    else:
        _draw_ui_grid(layout, ui.get('vis_display', []), _draw_vis_recursive, arm, ui, depth=0)

def draw_rruf_props(layout, context):
    arm = context.object.data
    ui = rruf_engine.get_ui_data(arm.session_uid)
    row = layout.row(align=True)

    row.prop(arm.rruf, "prop_search", text="", icon='VIEWZOOM')
    if not arm.rruf.prop_search:
        _draw_expand_buttons(row, 'PROPS')

    layout.separator()

    if arm.rruf.prop_search:
        _draw_props_isolated(layout, arm, ui, arm.rruf.prop_search)
    else:
        _draw_ui_grid(layout, ui.get('props_others', []), _draw_props_recursive, arm, ui)

def draw_rruf_snap(layout, context):
    arm = context.object.data
    ui = rruf_engine.get_ui_data(arm.session_uid)
    row = layout.row(align=True)

    row.prop(arm.rruf, "snap_search", text="", icon='VIEWZOOM')
    if not arm.rruf.snap_search:
        _draw_expand_buttons(row, 'SNAP')

    layout.separator()

    if arm.rruf.snap_search:
        _draw_snap_isolated(layout, arm, ui, arm.rruf.snap_search)
    else:
        row_utils = layout.row(align=True)
        row_utils.alignment = 'RIGHT'
        row_utils.popover(panel="RRUF_PT_cursor_transform_popover", text="", icon='GIZMO')
        row_utils.separator(factor=0.5)

        grp_cur = row_utils.row(align=True)
        grp_cur.label(text="", icon='CURSOR')
        for a, i in [('CURS_TO_SEL_LOC','CON_LOCLIKE'),('CURS_TO_SEL_ROT','CON_ROTLIKE'),('CURS_TO_SEL_ALL','SNAP_ON')]:
            grp_cur.operator("rruf.snap_cursor_utils", text="", icon=i).action = a

        row_utils.separator(factor=1.0)
        grp_sel = row_utils.row(align=True)
        grp_sel.label(text="", icon='SNAP_PEEL_OBJECT')
        for a, i in [('SEL_TO_CURS_LOC','CON_LOCLIKE'),('SEL_TO_CURS_ROT','CON_ROTLIKE'),('SEL_TO_CURS_ALL','SNAP_ON')]:
            grp_sel.operator("rruf.snap_cursor_utils", text="", icon=i).action = a

        layout.separator()
        _draw_ui_grid(layout, ui.get('snap_layout', []), _draw_snap_recursive, arm, ui)


def _draw_node_header(layout, node, coll, lbl, is_valid, show_expand=False, is_board=False, default_icon='FILE_FOLDER', center_header=False, pre_draw_func=None):
    box = layout.box()
    row = box.row(align=True)

    if center_header: row.alignment = 'CENTER'
    if not is_valid: row.alert = True
    if pre_draw_func: pre_draw_func(row)

    ic = node.get("k_icon_name") or default_icon
    if is_board or not show_expand:
        row.label(text=lbl, icon=ic)
    else:
        row.prop(coll, "is_expanded", text=lbl, icon=ic)

    return box, row

def _draw_overflow_error(layout):
    err_box = layout.box()
    err_row = err_box.row(align=True)
    err_row.alert = True
    err_row.label(text="Max Depth Exceeded", icon='ERROR')

def _draw_ui_grid(layout, items, draw_func, armature, ui_data, **kwargs):
    col = layout.column(align=True)
    for g in items:
        if isinstance(g, list):
            splits = _get_even_splits(col, len(g))
            for item, split_layout in zip(g, splits):
                draw_func(split_layout, armature, item, ui_data, **kwargs)

def _draw_expand_buttons(layout, panel_type):
    row = layout.row(align=True)
    op_col = row.operator("rruf.expand_all", text="", icon='FULLSCREEN_EXIT')
    op_col.action = 'COLLAPSE'; op_col.panel = panel_type
    op_exp = row.operator("rruf.expand_all", text="", icon='FULLSCREEN_ENTER')
    op_exp.action = 'EXPAND'; op_exp.panel = panel_type

def _draw_vis_action_buttons(layout_row, node_id, node, coll):
    op_replace = layout_row.operator("rruf.select_collection", text="", icon='RESTRICT_SELECT_OFF')
    op_replace.collection_name = node_id
    op_replace.replace = True

    op_add = layout_row.operator("rruf.select_collection", text="", icon='ADD')
    op_add.collection_name = node_id
    op_add.replace = False

    if not node.get("k_hide"):
        layout_row.operator("rruf.vis_toggle", text="",
                      icon='HIDE_OFF' if coll.is_visible else 'HIDE_ON').collection_name = node_id

    layout_row.operator("rruf.solo_toggle", text="",
                  icon='SOLO_ON' if coll.is_solo else 'SOLO_OFF').collection_name = node_id

def _get_common_node_data(armature, ui_data, node_id):
    node = ui_data.get("node_map", {}).get(node_id)
    if not node: return None
    coll = get_bone_collection(armature, node_id)
    if not coll: return None

    return RRUFNodeData(
        node=node, coll=coll, label=node.get("label", node_id),
        is_valid=node.get("is_valid", True), has_children=bool(node.get("ui_layout")),
        has_overflow=node.get("overflow", False)
    )

def _setup_node_container(layout, armature, ui_data, node_id, default_icon='FILE_FOLDER', force_expand=False, center_header=False, pre_draw_func=None):
    data = _get_common_node_data(armature, ui_data, node_id)
    if not data: return None
    node, coll, lbl, is_valid, has_children, has_overflow = data
    is_board, is_inline = node.get("k_board"), node.get("k_inline")
    show_expand = force_expand or has_children or has_overflow

    if is_inline or is_board: center_header = False

    box, row = _draw_node_header(layout, node, coll, lbl, is_valid, show_expand, is_board, default_icon, center_header, pre_draw_func)
    should_draw_children = True if is_board else (show_expand and coll.is_expanded)

    return {
        "node": node, "coll": coll, "box": box, "row": row,
        "should_draw_children": should_draw_children, "has_children": has_children,
        "has_overflow": has_overflow, "is_board": is_board, "is_inline": is_inline
    }

def _draw_children_grid(container_layout, node, armature, ui_data, draw_func, **kwargs):
    if node.get("ui_layout"):
        for row_group in node.get("ui_layout", []):
            if isinstance(row_group, list):
                splits = _get_even_splits(container_layout, len(row_group))
                for item, split_layout in zip(row_group, splits):
                    draw_func(split_layout, armature, item, ui_data, **kwargs)
    if node.get("overflow", False): _draw_overflow_error(container_layout)

def _draw_vis_recursive(layout, armature, node_id, ui_data, depth=0):
    if depth > MAX_UI_NESTING: return
    node = ui_data.get("node_map", {}).get(node_id)
    if not node: return

    show_expand = bool(node.get("ui_layout")) or node.get("overflow", False)
    default_icon = 'FILE_FOLDER' if node.get("k_board") else ('COLLECTION_NEW' if show_expand else 'BONE_DATA')
    ctx = _setup_node_container(layout, armature, ui_data, node_id, default_icon=default_icon, center_header=True)
    if not ctx: return

    is_inline = ctx["is_board"] or ctx["node"].get("k_inline")
    if is_inline:
        btns = ctx["row"].row(align=True)
        if ctx["is_board"]: btns.alignment = 'RIGHT'
    else:
        btns = ctx["box"].row(align=True)
        btns.alignment = 'CENTER'

    _draw_vis_action_buttons(btns, node_id, ctx["node"], ctx["coll"])

    if ctx["should_draw_children"]:
        inner = ctx["box"].column(align=True)
        _draw_children_grid(inner, ctx["node"], armature, ui_data, _draw_vis_recursive, depth=depth + 1)

def _draw_prop_slider(layout, coll, node, node_id, key, text):
    val = coll.get(key)
    prop_path = f'["{bpy.utils.escape_identifier(key)}"]'
    if key in node.get("toggle_props", ()) and _is_toggleable_value(val):
        row = layout.row(align=True)
        op = row.operator("rruf.prop_toggle", text="", icon='CHECKBOX_HLT' if val >= 0.5 else 'CHECKBOX_DEHLT')
        op.collection_name, op.prop_key = node_id, key
        row.prop(coll, prop_path, text=text)
    else:
        layout.prop(coll, prop_path, text=text)

def _draw_props_recursive(layout, armature, node_id, ui_data):
    node = ui_data.get("node_map", {}).get(node_id)
    if not node: return
    link = node.get("link_meta")
    has_children = bool(node.get("ui_layout")) or node.get("overflow", False)
    default_icon = 'LINKED' if link else ('FILE_FOLDER' if has_children else 'SETTINGS')

    contains_links = False
    if has_children:
        for t in node.get("all_descendants", []):
            if ui_data.get("node_map", {}).get(t, {}).get("link_meta"):
                contains_links = True
                break

    def pre_draw_reset(r):
        r.operator("rruf.reset", text="", icon='LOOP_BACK').collection_name = node_id

    ctx = _setup_node_container(layout, armature, ui_data, node_id, default_icon=default_icon, force_expand=True, pre_draw_func=pre_draw_reset)
    if not ctx: return

    header_buttons = ctx["row"].row(align=True)
    header_buttons.alignment = 'RIGHT'

    if contains_links:
        op_r2l = header_buttons.operator("rruf.prop_symmetrize_folder", text="", icon='TRIA_RIGHT')
        op_r2l.sync_left_to_right = False
        op_r2l.collection_name = node_id

        op_l2r = header_buttons.operator("rruf.prop_symmetrize_folder", text="", icon='TRIA_LEFT')
        op_l2r.sync_left_to_right = True
        op_l2r.collection_name = node_id

    if link:
        src = link["is_source"]
        op = ctx["row"].operator("rruf.prop_symmetrize", text="", icon='TRIA_LEFT' if src else 'TRIA_RIGHT')
        op.sync_left_to_right = src
        op.l_name, op.r_name = (node_id, link["partner"]) if src else (link["partner"], node_id)

    if ctx["should_draw_children"]:
        col = ctx["box"].column(align=True)
        keys = ctx["node"].get("valid_props", ())

        if keys:
            clean_props = ctx["node"].get("clean_props", {})
            for k in keys:
                if k in ctx["coll"]:
                    _draw_prop_slider(col, ctx["coll"], ctx["node"], node_id, k, clean_props.get(k, k))

        if ctx["has_children"] or ctx["has_overflow"]:
            if keys: col.separator()
            _draw_children_grid(col, ctx["node"], armature, ui_data, _draw_props_recursive)
        elif not keys:
            col.label(text="No properties.", icon='INFO')

def _draw_snap_recursive(layout, armature, node_id, ui_data):
    node = ui_data.get("node_map", {}).get(node_id)
    if not node: return

    if node.get("is_snap_group"):
        data = _get_common_node_data(armature, ui_data, node_id)
        if not data: return
        _, _, lbl, is_valid, _, _ = data
        is_inline = node.get("k_inline")

        if is_inline:
            container = layout.row(align=True)
            lbl_row = btn_row = container
        else:
            container = layout.column(align=True)
            lbl_row = container.row(align=True)
            lbl_row.alignment = 'CENTER'
            btn_row = container.row(align=True)
            btn_row.alignment = 'CENTER'

        if not is_valid: lbl_row.alert = True
        ic = node.get("k_icon_name")
        lbl_row.label(text=lbl, icon=ic if ic else 'GROUP_BONE')
        _draw_snap_batch_buttons(btn_row, node_id)
    else:
        ctx = _setup_node_container(layout, armature, ui_data, node_id)
        if not ctx: return
        if ctx["should_draw_children"]:
            inner = ctx["box"].column(align=True)
            _draw_children_grid(inner, ctx["node"], armature, ui_data, _draw_snap_recursive)

def _draw_snap_batch_buttons(layout_row, node_id):
    for icon, loc, rot, scale in [
        ('CON_LOCLIKE', True, False, False),
        ('CON_ROTLIKE', False, True, False),
        ('CON_SIZELIKE', False, False, True),
        ('SNAP_ON', True, True, True)
    ]:
        op = layout_row.operator("rruf.snap_hierarchy_batch", text="", icon=icon)
        op.parent_collection_name = node_id
        op.snap_loc, op.snap_rot, op.snap_scale = loc, rot, scale

def _draw_isolated_generic(layout, armature, ui_data, query, layout_key, match_and_draw_func):
    nm = ui_data.get("node_map", {})
    clean_query = query.lower()
    any_found = False

    for node_id, node in _iter_layout_nodes(ui_data.get(layout_key, []), nm):
        if match_and_draw_func(layout, armature, node_id, node, clean_query, nm):
            any_found = True

    if not any_found: layout.label(text="No matching results found.", icon='INFO')

def _draw_vis_isolated(layout, armature, ui_data, query):
    def draw_match(layout, arm, node_id, node, q, nm):
        coll = get_bone_collection(arm, node_id)
        if coll and any(q in b.name.lower() for b in coll.bones):
            box = layout.box()
            row = box.row(align=True)
            row.label(text=node.get("label", node_id), icon=node.get("k_icon_name") or 'BONE_DATA')
            btns = row.row(align=True)
            _draw_vis_action_buttons(btns, node_id, node, coll)
            return True
        return False

    _draw_isolated_generic(layout, armature, ui_data, query, "vis_display", draw_match)

def _draw_props_isolated(layout, armature, ui_data, query):
    def draw_match(layout, arm, node_id, node, q, nm):
        coll = get_bone_collection(arm, node_id)
        if not coll: return False
        node_label = node.get('label', node_id)
        clean_props = node.get("clean_props", {})

        matches = []
        for k in node.get("valid_props", []):
            clean_k = clean_props.get(k, k)
            full_match_str = f"{clean_k} ({node_label})".lower()

            if q in full_match_str or q in k.lower() or q in clean_k.lower() or q in node_label.lower():
                matches.append(k)

        if matches:
            box = layout.box()
            row = box.row()
            row.label(text=node_label, icon='FILE_FOLDER')
            op = row.operator("rruf.reset", text="", icon='LOOP_BACK')
            op.collection_name = node_id
            for k in matches: _draw_prop_slider(box, coll, node, node_id, k, clean_props.get(k, k))
            return True
        return False

    _draw_isolated_generic(layout, armature, ui_data, query, "props_others", draw_match)

def _draw_snap_isolated(layout, armature, ui_data, query):
    def draw_match(layout, arm, node_id, node, q, nm):
        if node.get("is_snap_group"):
            label = node.get("label", node_id)
            parent_id = node.get("parent")
            parent_label = nm[parent_id].get("label", parent_id) if parent_id and parent_id in nm else "Root"

            full_match_str = f"{label} ({parent_label})".lower()

            if q in full_match_str or q in label.lower() or q in parent_label.lower():
                box = layout.box()
                head = box.row()
                head.label(text=parent_label, icon='FILE_FOLDER')
                row = box.row(align=True)
                row.label(text=label, icon=node.get("k_icon_name") or 'GROUP_BONE')
                btn_row = row.row(align=True)
                _draw_snap_batch_buttons(btn_row, node_id)
                return True
        return False

    _draw_isolated_generic(layout, armature, ui_data, query, "snap_layout", draw_match)


class RRUF_ArmatureSettings(bpy.types.PropertyGroup):
    vis_search: bpy.props.StringProperty(
        name="Search Bone", search=_get_vis_matches, description=_TIPS["VIS_SEARCH"][1]
    )
    prop_search: bpy.props.StringProperty(
        name="Filter Settings", search=_get_prop_matches, description="Filter Properties"
    )
    snap_search: bpy.props.StringProperty(
        name="Filter Snaps", search=_get_snap_matches, description="Filter Snap Groups"
    )
    vis_is_isolated: bpy.props.BoolProperty(
        name="Isolate Search", default=False, description=_TIPS["VIS_ISOLATE"][1]
    )

_CLASSES = (
    RRUF_ArmatureSettings,
    RRUF_PT_Main,
    RRUF_PT_manual_popover,
    RRUF_OT_popup_ui,
    RRUF_OT_reset_pose_transforms,
    RRUF_OT_expand_all,
    RRUF_OT_snap_hierarchy_batch,
    RRUF_OT_snap_cursor_utils,
    RRUF_OT_zero_cursor_axis,
    RRUF_PT_cursor_transform_popover,
    RRUF_OT_select_collection,
    RRUF_OT_vis_toggle,
    RRUF_OT_vis_show_all,
    RRUF_OT_vis_unsolo_all,
    RRUF_OT_solo_toggle,
    RRUF_OT_prop_symmetrize,
    RRUF_OT_prop_symmetrize_folder,
    RRUF_OT_reset,
    RRUF_OT_prop_toggle,
    RRUF_OT_get_active_bone_to_search,
    RRUF_OT_select_from_search
)

def _remove_popup_keymap():
    wm = bpy.context.window_manager
    kc = getattr(wm, "keyconfigs", None)
    if kc and kc.addon and (km := kc.addon.keymaps.get('Pose')):
        for k in [item for item in km.keymap_items if item.idname == RRUF_OT_popup_ui.bl_idname]:
            km.keymap_items.remove(k)

# The installed RRUF extension takes priority over an already-running
# embedded RRUF core. Ask that core to shut down before registering this one,
# so its timer, classes, properties, handler, and lock are cleaned up.
# Removing only its handler would leave its persistent timer running.
# Match by function name because embedded copies have distinct function
# objects and may come from an older RRUF version.
def _rruf_dismiss_other_instances():
    for h_list in (bpy.app.handlers.load_pre, bpy.app.handlers.load_post):
        for func in list(h_list):
            if func is not _rruf_killswitch_engage and getattr(func, "__name__", "") == "_rruf_killswitch_engage":
                try: func(None)
                except Exception: pass

# Roll back registration if startup fails partway through, particularly
# the global lock that would otherwise block later embedded cores.
def register():
    try:
        _register_body()
    except Exception:
        try: unregister()
        except Exception: pass
        raise

def _register_body():
    _rruf_dismiss_other_instances()
    _rruf_killswitch_defuser()
    # Mark this core active so embedded copies yield instead of starting
    # another engine. This is a shared presence flag, not an ownership token.
    bpy.app.driver_namespace[RRUF_GLOBAL_LOCK] = True

    bpy.types.WindowManager.rruf_active_tab = bpy.props.EnumProperty(
        name="Menu Tab",
        items=[
            ('VIS', "Visibility", "Show Visibility Settings", 'HIDE_OFF', 0),
            ('PROPS', "Properties", "Show Property Settings", 'PROPERTIES', 1),
            ('SNAP', "Snapping", "Show Snapping Settings", 'SNAP_ON', 2)
        ],
        default='VIS'
    )
    bpy.types.WindowManager.rruf_show_workflow = bpy.props.BoolProperty(
        name="Show Workflow", default=False
    )

    for c in _CLASSES:
        bpy.utils.register_class(c)

    bpy.types.Armature.rruf = bpy.props.PointerProperty(type=RRUF_ArmatureSettings)

    wm = bpy.context.window_manager
    kc = getattr(wm, "keyconfigs", None)
    if kc and kc.addon:
        _remove_popup_keymap()
        km = kc.addon.keymaps.get('Pose')
        if not km: km = kc.addon.keymaps.new(name='Pose', space_type='EMPTY')
        km.keymap_items.new(RRUF_OT_popup_ui.bl_idname, **POPUP_KEYBIND)

    if not bpy.app.timers.is_registered(rruf_main_loop_timer):
        bpy.app.timers.register(rruf_main_loop_timer, first_interval=0.25, persistent=True)

    if _rruf_killswitch_engage not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_rruf_killswitch_engage)

def unregister():
    _rruf_killswitch_defuser()
    _remove_popup_keymap()

    if RRUF_GLOBAL_LOCK in bpy.app.driver_namespace:
        del bpy.app.driver_namespace[RRUF_GLOBAL_LOCK]

    if bpy.app.timers.is_registered(rruf_main_loop_timer):
        bpy.app.timers.unregister(rruf_main_loop_timer)

    for c in reversed(_CLASSES):
        try: bpy.utils.unregister_class(c)
        except Exception: pass

    if hasattr(bpy.types.Armature, "rruf"): del bpy.types.Armature.rruf
    if hasattr(bpy.types.WindowManager, "rruf_active_tab"): del bpy.types.WindowManager.rruf_active_tab
    if hasattr(bpy.types.WindowManager, "rruf_show_workflow"): del bpy.types.WindowManager.rruf_show_workflow

if __name__ == "__main__":
    # Embedded cores run only when no installed RRUF is enabled and no core
    # holds the global lock. Yielding registers nothing and schedules no retry.
    # If the extension is later disabled, reload the file or run this script
    # again to start the embedded core.
    # Enabled add-ons and extensions are listed in the preferences, which
    # survive file loads; no add-on directory rescan is needed. The lock alone
    # is not enough: Blender clears driver_namespace before running this text.
    import bpy

    is_global_active = False
    try:
        candidates = [m for m in bpy.context.preferences.addons.keys() if m.split('.')[-1] in ("rruf", "rruf_core")]
    except Exception as e:
        print(f"RRUF Core: Add-on check failed ({e}). Relying on the global lock.")
        candidates = []

    if candidates:
        is_global_active = True
        # print(f"RRUF Core: Global version detected ({candidates[0]}). Embedded engine yielding")  # debug

    if not is_global_active:
        if RRUF_GLOBAL_LOCK in bpy.app.driver_namespace:
            pass  # print("RRUF: Global lock detected. Embedded instance yielding.")  # debug
        else:
            try:
                _rruf_dismiss_other_instances()
                _rruf_killswitch_defuser()
                unregister()
            except Exception: pass

            try:
                register()
            except Exception as e:
                print(f"RRUF Core: Embedded registration failed ({e}). Nothing was left registered.")
            # else:  # debug
            #     print("RRUF Core: Active (Embedded Mode)")
            # print("""
#                   ####                 
#                 ########               
#               ####    ###              
#             #####      ####            
#           #####    ##   ####           
#          ### ##  #####    ###          
#        #### ### #####      ###         
#       ### ####  #####   ### ####       
#      ###  ###    ###   ##### ####      
#      ####               #### ####      
#      ############   #############      
#         #######       ########         
#                ##   ##                 
#                #     ##                
#              ###    ####               
#             ##       ####              
#             #############              
#              ###########                """)
