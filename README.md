# RRUF — Reactive Rig UI Framework

RRUF builds a rig interface from an armature's bone collection names and custom properties. Tags in the names define the layout, visibility groups, property controls, and snapping pairs. The interface updates as you edit that structure.

Use the main panel to work with a configured rig. Use the Collection Editor to build or adjust its interface.

**Requires Blender 4.2 or newer.**

- [Download](https://github.com/LaurusKuvakei/RRUF/releases/latest)
- [Report a problem](https://github.com/LaurusKuvakei/RRUF/issues)

## Contents

- [Installation and panel locations](#installation-and-panel-locations)
- [Quick start](#quick-start)
- [How collection names work](#how-collection-names-work)
- [Main panel](#main-panel)
- [Collection tags](#collection-tags)
- [Custom properties](#custom-properties)
- [Setting up snapping](#setting-up-snapping)
- [Collection Editor](#collection-editor)
- [Presets and snapshots](#presets-and-snapshots)
- [Embedding the core](#embedding-the-core)
- [Preferences](#preferences)
- [Troubleshooting](#troubleshooting)

## Installation and panel locations

Install RRUF from Blender Extensions, or download the release ZIP from this repository. To install a ZIP, open **Edit → Preferences → Get Extensions**, use the menu in the upper-right corner, and choose **Install from Disk**. Select the ZIP and enable the extension.

By default, RRUF has two locations in the 3D Viewport sidebar. Press **N** to open the sidebar.

| Location | Purpose | When it appears |
| --- | --- | --- |
| **Tool → Reactive Rig UI** | Visibility, properties, snapping, and workflow tools | In Pose Mode, with an RRUF-enabled armature selected |
| **RRUF → Reactive Rig UI Framework → Collection Editor** | Building and editing the interface | With an armature selected and **Show Editor** enabled in RRUF's preferences |

To show the editor, find RRUF in **Preferences → Add-ons**, expand its settings, and enable **Show Editor**. It is off by default so an animator can use the rig without seeing the setup tools.

You can change both panel locations in the extension preferences.

## Quick start

### Start with the generic preset

1. Select the armature you want to configure.
2. Enable **Show Editor** in RRUF's preferences.
3. Open the **RRUF** sidebar tab and click **Enable RRUF for this armature**.
4. In the Collection Editor, find **Internal Presets** and select `rruf_generic.json`.
5. Review **Import Options**. For an existing rig, leave **Rebuild** off unless you intend to replace its collection structure.
6. Click the import button beside the preset selector and confirm the load.
7. Assign your rig's bones to the relevant collections using Blender's Bone Collections tools. Adjust the preset's collection names and properties to suit the rig.
8. Switch to **Pose Mode** and open **Tool → Reactive Rig UI**.

The generic preset provides a starting interface structure. Property controls affect the rig according to the drivers or other connections you set up, and snapping depends on the bones assigned to its source and target collections.

### Build a minimal interface yourself

Create these bone collections on an RRUF-enabled armature:

| Collection name | Parent | Purpose |
| --- | --- | --- |
| `(IN)` | None | Required internal category; it may be empty |
| `(DI)` | None | Visibility category |
| `{Controls}(i)` | `(DI)` | A visibility row for your control bones |

Assign some control bones to `{Controls}(i)`. In Pose Mode, the **Visibility** tab will show that collection with selection, visibility, and solo buttons.

Add `(SE)` when you want property groups, and `(SN)` when you want snapping groups. You only need the categories you plan to use, plus `(IN)`.

## How collection names work

A normal UI collection name has a label followed by optional tags:

```text
{Arm Controls}(i)(j)
```

`{Arm Controls}` is the text shown in the interface. `(i)` changes the arrangement of its controls, and `(j)` places it beside the next collection in the same row.

Root collections identify a category and sit at the top level of the armature's collection hierarchy:

| Root tag | Long form | Category |
| --- | --- | --- |
| `(DI)` | `(DISPLAYS)` | Visibility |
| `(SE)` | `(SETTINGS)` | Properties |
| `(SN)` | `(SNAPS)` | Snapping |
| `(IN)` | `(INTERNALS)` | Internal collections, excluded from the generated UI |

Use one root per category. Parent the UI collections beneath the appropriate root. Their children belong to the same category. Collections under `(IN)` can use ordinary names because RRUF does not draw that branch.

Short and long tags have the same meaning. Their spelling matters: use uppercase root tags, lowercase short modifiers, and uppercase long modifiers, as shown in the tables below.

### Repeated labels

Bone collection names must be unique within an armature. To show the same label more than once, add a discriminator immediately after the label:

```text
{FK}[Left](i)
{FK}[Right](i)
```

Both display as **FK**, while their full names remain different. In the editor, the discriminator is the field marked **#** beside **Name**.

Avoid relying on Blender's automatic `.001` suffix for UI collections; RRUF treats that suffix as unrecognized syntax. Collection names have a 63-byte limit, so keep labels and tag combinations short, especially when using multibyte characters.

## Main panel

The main panel is the animator-facing interface. The category buttons appear according to the roots present in the rig. Hover over an icon to read its tooltip. The **Info** button in the header provides a compact control reference.

In Pose Mode, **Ctrl+Shift+R** opens the RRUF Quick Menu, which presents the same rig interface in a popup.

### Workflow

Expand **Workflow** at the top of the panel to access common pose and viewport controls.

- **Pose / Rest** switches the armature's display between its posed state and rest state.
- The navigation buttons control **Orbit Around Selection**, **Zoom to Mouse Position**, and **Auto Depth Calculation**. These change Blender's navigation preferences.
- The reset buttons clear location, rotation, scale, or all transforms. One group targets selected bones; the other targets the visible bones Blender can select in Pose Mode.

Transform resets change the pose. They are separate from resetting custom property values in the Properties tab.

### Visibility

This tab displays the collections beneath `(DI)`.

Each collection has controls to replace the current bone selection, add its bones to the selection, toggle visibility, and toggle solo. Selection includes bones in that collection and its scanned descendants. Solo also applies through its descendants, with hidden `(h)` collections excluded when appropriate.

At the top:

- **Search Bone** finds bones in the visibility layout.
- **Get Active**, shown as an eyedropper, fills the search with the active pose bone's name.
- The selection button selects the bone named in the search.
- **Isolate Search** filters the displayed UI to matching collections. It does not itself change collection visibility.
- **Show All** reveals visibility collections, while respecting hidden `(h)` collections.
- **Unsolo All** clears collection solo states.
- **Expand / Collapse** opens or closes the visibility folders.

### Properties

This tab displays custom properties stored on the bone collections beneath `(SE)`. Expand a group to see its controls. Use the search field to find properties by their displayed names or collection labels.

The reset button restores available default values for a collection and its descendants. Defaults come from each property's Blender settings. Properties marked hidden with `{x}` are left unchanged.

Collections paired with `(l)` show buttons for copying property values between partners. A folder containing linked pairs also offers buttons to synchronize the pairs within that folder. Only properties with matching full keys on both sides are copied; hidden properties and scene-collection references are excluded.

This symmetry copies property values. To mirror a pose, use the appropriate pose tools for your rig. Changing a value in one linked collection does not automatically copy it to the partner; use the synchronization button when needed.

### Snapping

This tab displays snapping groups beneath `(SN)`. Each configured group has buttons for **Location**, **Rotation**, **Scale**, and **All**. The selected channels of its source bones are aligned with the paired target bones.

Use the search field to find a group. The cursor tools above the groups provide:

- **Cursor to Selection:** place or orient the 3D cursor from the active pose bone.
- **Selection to Cursor:** place or orient selected pose bones from the 3D cursor.
- **Cursor Transform:** edit cursor coordinates and reset individual components.

The cursor operations offer location, rotation, or both; they do not transfer scale. Group snapping offers scale separately.

Snapping respects the bones' transform locks. With Blender's Auto Keying enabled, group snapping and Selection to Cursor insert keys for the affected channels. Turning an IK/FK property on or off is a separate operation; use your rig's intended snapping and switching sequence.

## Collection tags

Add these tags after a collection label. The editor offers tags appropriate for the collection's category.

| Tag | Long form | Available in | What it does |
| --- | --- | --- | --- |
| `(i)` | `(INLINE)` | Visibility, Snapping | Places action buttons beside the label. In Visibility, this makes a compact collection row. |
| `(b)` | `(BOARD)` | All three UI categories | Makes a board whose contents stay open instead of using a collapsible folder. |
| `(j)` | `(JOIN)` | All three UI categories | Joins the collection with the next sibling in the same UI row. |
| `(l)` | `(LINK)` | Properties | Pairs the collection with the next displayed sibling for property-value synchronization. |
| `(s)` | `(SKIP)` | All three UI categories | Excludes the collection and its descendants from RRUF's generated interface. |
| `(t)` | `(TO)` | Snapping | Marks a source collection to snap to the next sibling. Its parent becomes a snapping group. |
| `(h)` | `(HIDE)` | All three UI categories | Hides the UI entry when the bone collection is not visible. |
| `(h)[ALW]` | `(HIDE)[ALW]` | All three UI categories | Always hides the UI entry. |
| `(ic)[BONE_DATA]` | `(ICON)[BONE_DATA]` | All three UI categories | Uses the named Blender icon. Select an icon through the editor's icon picker. |

`(s)` stops RRUF from processing the branch. `(h)` controls whether an entry is displayed according to visibility, and `(h)[ALW]` keeps it out of the displayed layout. These tags do not delete collections or bones.

### Examples

| Name | Result |
| --- | --- |
| `{Body}(b)` | An open board named Body |
| `{Arm L}(i)(j)` followed by `{Arm R}(i)` | Two compact visibility entries beside each other |
| `{Extra Controls}(h)` | An entry that disappears when its collection is hidden |
| `{Helper Data}(s)` | A branch excluded from RRUF |
| `{Controls}(ic)[BONE_DATA]` | A collection entry with a bone icon |

Order matters for `(j)`, `(l)`, and `(t)`: they use the following sibling, not a collection found by a `.L` or `.R` name. Arrange the collections in Blender's Bone Collections list accordingly. For property links, keep both partners visible and adjacent in the displayed layout.

## Custom properties

Store a rig control's custom property on the relevant **bone collection** beneath `(SE)`. RRUF displays it using Blender's property settings, including its default and numeric range. Connect that property to your rig using drivers or the setup appropriate to your rig.

For a basic IK switch, select the settings collection, add a property with Blender's **Custom Properties** controls, and edit its settings: name `IK_SWITCH`, type Float, default 0, minimum 0, and maximum 1. Make sure the property belongs to the bone collection, rather than the armature object or an individual bone. Then select it in RRUF's Property Composer, give it order 1, enable **Toggle**, and click **Update Property**. Its full key becomes `{1^}IK_SWITCH`. Connect it to the relevant constraint influences or other controls using your rig's drivers.

Prefixes in the property key control its order and presentation:

| Prefix | Example key | Effect |
| --- | --- | --- |
| Number | `{1}IK_SWITCH` | Places the property in numeric order; the prefix is omitted from its displayed name. |
| `x` | `{x}INTERNAL_SETTING` | Hides the property from the main UI and excludes it from property resets and synchronization. |
| `^` | `{^}IK_SWITCH` | Adds a 0/1 toggle beside an integer or float control. |
| `-` | `{-}Geometry` | Inverts visibility synchronization when the property's value references a scene collection. |

You can combine prefixes: `{1^}IK_SWITCH` means order 1 with a 0/1 button. Separate groups such as `{1}{^}IK_SWITCH` also work. Unnumbered properties sort after numbered ones, alphabetically by their displayed names.

The 0/1 button sets values below 0.5 to 1 and values at or above 0.5 to 0. Use it for properties intended to switch between those values. It does not clamp to the property's configured range, perform IK/FK snapping, or synchronize a linked partner. The toggle, reset, and symmetry operators do not insert property keyframes themselves.

### Link properties for symmetry

Under `(SE)`, place `{Arm L}(l)` immediately before `{Arm R}`. Give both collections the same property keys, such as `{1^}IK_SWITCH` and `{2}FOLLOW`.

The partner buttons copy the matching values in the selected direction. Full keys must match, including any order or flag prefixes. If you rename or reorder properties on one partner, make the corresponding changes on the other.

### Link scene collection visibility

A custom property whose value references a Blender **scene collection** can synchronize that collection's viewport and render visibility with the bone collection. This is useful for geometry associated with a control group.

Use an ordinary property key for matching visibility, or a `-` prefix such as `{-}Geometry` for the opposite state. The prefix only has this effect when the value is a scene-collection reference; it does not invert a numeric slider.

For example, you can link an **Arm Controls** bone collection to an **Arm Geometry** collection in the Outliner. Hiding the controls' collection will also hide its associated geometry.

Set up the link through Blender's interface:

1. Select the armature and make the desired bone collection active in **Armature Data Properties → Bone Collections**.
2. In that bone collection's **Custom Properties**, add a new property and open its edit settings.
3. Name it `Geometry`, set its type to **Data-Block**, and choose **Collection** as the ID type. Confirm the settings.
4. Use the property's collection selector to choose the scene collection you want to control, such as **Arm Geometry**. This must be a collection, rather than an individual mesh object.
5. Toggle the bone collection's visibility to check that the selected scene collection follows it in both the viewport and renders.

For opposite visibility, select the reference property in RRUF's **Property Composer**, enable **Invert**, and click **Update Property**. This adds the `-` flag to its key. The scene collection will then be hidden when the bone collection is visible, and visible when it is hidden.

## Setting up snapping

Create a group under `(SN)`, then place source and target bone collections directly beneath it. Mark each source with `(t)` and put its target immediately after it.

For a single hand control:

| Collection name | Parent | Bone assignment |
| --- | --- | --- |
| `(SN)` | None | Category root |
| `{Left Hand}` | `(SN)` | Group container |
| `{Hand Control}(t)` | `{Left Hand}` | The control bone you want to move |
| `{Hand Reference}` | `{Left Hand}` | The reference bone to align it with |

The **Left Hand** group now has snap buttons. Clicking **Location** moves the control to the reference position; **Rotation** matches its rotation; **All** matches location, rotation, and scale.

You can place several adjacent source/target pairs in the same group. A parent containing a valid `(t)` pair acts as a snap group; use a separate parent folder to organize multiple groups.

One bone per source and target collection is the simplest setup. When the target collection has one bone, all bones in the source collection align to that target. Otherwise, RRUF sorts each side alphabetically by bone name and pairs them by position. Extra bones on a longer side are left untouched. Keep assignments and naming consistent when using multiple bones.

## Collection Editor

Select the armature and enable **Show Editor**. In Blender's **Armature Data Properties → Bone Collections**, make the collection you want to edit active. RRUF's editor follows that active collection.

Create collections, set their parents, arrange their order, and assign bones using Blender's Bone Collections tools. The RRUF editor then helps you compose their UI names and edit property prefixes.

### Collection Composer

1. Select a bone collection.
2. Enter its displayed label in **Name**. Enter the label itself, such as `Arm Controls`; the editor adds the braces.
3. Use **#** if you need a unique discriminator for a repeated label.
4. Click the available tag buttons to add the layout or behavior you want.
5. For **ICON**, choose an icon and click **Confirm**. For **HIDE**, choose whether it should be always hidden, then confirm. **Cancel** closes the parameter step without adding the tag.
6. Use the arrows beside existing tags to reorder them, or their X buttons to remove them.
7. Click **Update Collection** to apply the composed name.

The name and tag changes stay in the composer until **Update Collection** is pressed. The editor checks the final syntax, uniqueness, category, and name length. If it reports a problem, correct the fields before updating again.

To create a root, select a top-level collection and choose an available category tag such as **DISPLAYS**. Click **Update Collection**. Root names use their category tag; the normal label fields are not shown for them. A category already used elsewhere on the armature is not offered.

The **Deflate** and **Inflate** buttons beside Update Collection convert short and long tags across the entire armature. For example, `(DISPLAYS)` becomes `(DI)` when deflated. Inflate skips names that would exceed the 63-byte limit.

### Property Composer

This section edits properties that already exist on the active bone collection. Create the properties and configure their values, ranges, and defaults using Blender's custom-property tools first.

1. Click a property in the list to load it.
2. Edit **Name** and the order field **#**. Order 0 leaves the property unnumbered.
3. Enable **Hide**, **Toggle**, or **Invert** as appropriate. Toggle is available for integer and float values; Invert is available for scene-collection references.
4. Click **Update Property** to apply the new key.

The arrows beside list entries move a property up or down and renumber the collection's properties. Hidden properties are listed here too, so you can edit or reveal them.

RRUF attempts to update affected driver paths and property animation paths in the current file when a property is renamed or reordered. It reports paths it could not update. Finish this setup before using the rig in linked animation files: paths in other files, unused actions, or expressions containing literal property paths are not all covered. Property renaming and reordering are disabled for linked armature data and library overrides.

### Property Clipboard

Select a collection and click **Copy Properties**. Select the destination collection and click **Paste Properties**. RRUF copies property values and their interface settings; existing keys with the same names are overwritten. The clear button empties RRUF's property clipboard.

Use this to give a linked property pair matching controls. Driver connections are part of your rig setup and are not created by this clipboard operation.

## Presets and snapshots

### Internal Presets

Choose `rruf_generic.json` in **Internal Presets** and click its import button. Preset loading uses the same category and Rebuild choices as snapshot importing, so review **Import Options** before confirming.

You can customize the resulting collections and properties, then export your own snapshot for reuse.

### Export Options

**Export Snapshot** writes a JSON file containing the collection names, hierarchy, and categories.

- **Bones** includes bone assignments by name.
- **Properties** includes custom property values and their interface settings.

A snapshot stores interface setup data, not the complete rig or its animation. Importing bone assignments uses bones already present in the destination armature; matching names are needed.

### Import Options

Choose which categories to import with **Vis**, **Props**, **Snaps**, and **Internals**, then click **Import Snapshot** and confirm before choosing the file.

With **Rebuild off**, RRUF reuses matching collection names, creates missing collections, and applies imported hierarchy, assignments, and properties. Collections outside the matching imported structure remain. This is not an exact replacement: for example, existing bone memberships are not first cleared.

With **Rebuild on**, RRUF removes collections in the selected categories before importing. **If all four categories are selected, every bone collection on the armature is removed first, including collections outside RRUF's categories.** This removes collections and their stored properties and memberships, not the armature's bones. Save a copy of your file before rebuilding a configured rig.

RRUF checks the snapshot before rebuilding. If values cannot be restored, it reports the skipped properties. Scene-collection references also need a matching collection name in the destination file; unresolved references are skipped.

## Embedding the core

**Engine Injection → Embed Core into .Blend** copies RRUF's core into a text block named `rruf_core.py` in the current file. Save the `.blend` afterward.

The embedded core provides the animator-facing interface on a computer where the extension is not installed. The Collection Editor is part of the extension and is not included in that embedded core.

There are two ways to run it:

- **Manually:** open Blender's Text Editor, select the `rruf_core.py` text block, and use **Run Script**.
- **On file load:** allow automatic script execution for a file you trust. The embedded text is registered to run when Blender opens the file.

If the RRUF extension is already active, the embedded core yields to it. Embedding again replaces the existing `rruf_core.py` text block. The embedded copy does not update when the installed extension updates; embed it again when you want the newer core.

## Preferences

| Setting | Use |
| --- | --- |
| **Show Editor** | Shows the setup panel. Off by default. |
| **Setup Tab** | Chooses where the Collection Editor is placed; shown when Show Editor is enabled. |
| **Main Tab** | Chooses where the animator-facing panel is placed. |
| **FPS** | How often RRUF checks for interface changes. Default 4; this is separate from the scene's animation frame rate. |
| **Depth** | Maximum collection nesting processed for the UI. Default 8. Simplify deep layouts or increase this setting if needed. |
| **Reset to Defaults** | Restores RRUF's extension preferences. |

Panel destinations include the **Tool**, **RRUF**, **Animation**, **View**, and **Item** sidebar tabs, or **Properties → Constraints**.

## Troubleshooting

| What you see | What to check |
| --- | --- |
| No main panel | Select an RRUF-enabled armature, enter Pose Mode, and check the configured Main Tab. |
| No editor | Enable Show Editor in the extension preferences and select an armature. |
| **ACCESS DENIED** | Add a top-level `(IN)` or `(INTERNALS)` collection. It may be empty. |
| **NO UI CONFIGURED** | Add `(DI)`, `(SE)`, or `(SN)` at the top level, with UI collections beneath it. |
| A category is empty | Add child collections under its root. Properties need custom properties on the collections; snapping needs source/target pairs. |
| **SYNTAX ERROR** | Check braces, tag spelling, tag/category compatibility, icon names, and unintended `.001` suffixes. |
| A collection disappears | Check `(s)`, `(h)`, `(h)[ALW]`, its parent, and whether a search filter is active. |
| **Max Depth Exceeded** | Reduce collection nesting or increase Depth in the extension preferences. |
| Property symmetry misses a control | Ensure both partners are adjacent in the displayed layout and have the same full property key, including prefixes. |
| Snapping moves the wrong bone | Check sibling order, `(t)` placement, assignments, and alphabetical pairing when there are multiple bones. |
| A preset or snapshot does not assign bones | The destination armature must already contain bones with matching names. |
| Embedded UI does not start | Run its text block manually, or allow automatic execution for the trusted file. Check whether the installed extension is already providing the UI. |

If the interface stops updating after repeated runtime errors, disable and re-enable RRUF and check Blender's console. When reporting a problem, include your Blender and RRUF versions, the steps to reproduce it, and any error text. A small example file is useful when you can share one.

For Blender's own collection tools, see the [Bone Collections documentation](https://docs.blender.org/manual/en/4.2/animation/armatures/properties/bone_collections.html), including assignment and custom properties.

## License and development

RRUF's Python code and project files are licensed under **GNU GPL version 3 or later**. The included `icon.png` is **CC0**. See `LICENSE.md` and `LICENSE_ASSETS.md`.

RRUF has been under development since October 2025 and has been used in several public projects during that time. It was developed with help from Gemini Pro.
