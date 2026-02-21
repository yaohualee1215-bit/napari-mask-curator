# napari-mask-curator

A napari plugin for **manual curation and selection of the best cell/nuclei masks**
produced by segmentation software (e.g., Cellpose, Baysor, parameter sweeps).
It is designed for workflows where you generate multiple candidate masks and then
**quickly pick the best instance per cell** inside napari with full traceability.

## Key features

- **Follow → Work**: create a working copy (`*.mask__work`) from a chosen candidate mask layer.
- **Click-to-collect**: when Collect is ON, click objects in `__work` to copy them into `manual_best`.
- **Always NEW ID**: every collected object gets a **new unique label ID** in `manual_best` (no merge ambiguity).
- **Undo**: revert the last collect action (restores `manual_best` and the corresponding `__work` region).
- **Overlap prune**: remove candidate objects from `__work` if they overlap too much with `manual_best`.
- **Compact save**: on save, relabel `manual_best` to **consecutive IDs (1..N)**.
- **Fast logging**: writes **JSONL + TSV** logs for every action (traceability).
- **Blink selected layer**: quick visibility toggle for visual comparison.

> This plugin is intended for **manual selection after segmentation**.
> It does not train models or run segmentation.

---

## Compatibility

- **Python:** 3.10–3.13 (tested on 3.11)
- **napari:** 0.6.x (tested on 0.6.4–0.6.6)
- **Masks:** Labels layers (integer label images), commonly saved as TIFF (LZW ok)

---

## Requirements for mask layers

### Recommended naming
The plugin is optimized for mask layer names containing:

- `.mask` (e.g. `d80_cp0_fl0.4.mask`)

This helps the plugin identify candidate mask layers and create:

- `d80_cp0_fl0.4.mask__work` (working copy)

> If your layer name does **not** contain `.mask`, you can still use it,
> but `.mask` is recommended for consistent workflows and automation.

### Layer type
Candidate masks must be a napari **Labels** layer (integer labels; background = 0).

---

## Typical workflow (recommended)

1. **Start napari** and load:
   - a background image (optional), e.g. DAPI
   - one or more candidate mask layers (**Labels**), preferably named `*.mask`

2. Open the plugin:
   - **Plugins → Mask Curator → Mask Curation**

3. Select a candidate Labels layer (e.g. `d80_cp0_fl0.4.mask`) in the layer list.

4. Click **Follow → Work + Prune**
   - Creates `*.mask__work`
   - Hides the original `.mask` layer (so you curate in the work layer)

5. Turn on **Collect**
   - Click **Collect: OFF (Alt-A)** to toggle ON/OFF
   - When ON, click on objects in `__work` to collect into `manual_best`

6. Optional:
   - **Undo (Alt-U)** to revert the last collect
   - **Prune Now (Alt-P)** to remove candidates overlapping curated objects
   - **Reset Work (Alt-R)** to restore `__work` from original `.mask` and then prune again

7. Save:
   - Click **Save manual_best (Alt-S)**
   - Writes outputs and logs (see below)

---

## Controls

### Buttons
- **Follow → Work + Prune**
- **Collect: ON/OFF (Alt-A)**
- **Undo (Alt-U)**
- **Prune Now (Alt-P)**
- **Reset Work (Alt-R)**
- **Save manual_best (Alt-S)**
- **Blink Selected Layer**

### Keyboard shortcuts (default)
To avoid conflicts with napari built-ins, this plugin uses **Alt-** shortcuts:

- **Alt-A**: Toggle Collect
- **Alt-P**: Prune Now
- **Alt-R**: Reset Work
- **Alt-U**: Undo
- **Alt-S**: Save

> If you want to change hotkeys, edit the bindings in the plugin source.

---

## Parameters

- **Overlap prune thr (keep if < thr)**  
  Candidate objects in `__work` are removed if their overlap ratio with `manual_best` is **≥ thr**.

- **ROI pad (px)**  
  Padding around the clicked object’s bounding box used for fast local updates.
  Increasing this can be safer; smaller values can be faster.

---

## Outputs

### When saving, the plugin writes:

- `manual_best_ID_uint32.tif` — curated Labels with compact consecutive IDs (1..N)
- `manual_best_black_uint8.tif` — binary mask (0/255)
- `manual_best_id_map_old_to_new.tsv` — mapping from pre-save IDs to compact IDs

### Logs (traceability)

Logs are written to **the current working directory** (the folder you launched napari from):

- `curation_logs/curation_<timestamp>.jsonl`
- `curation_logs/curation_<timestamp>.tsv`

If you want logs to go somewhere else, launch napari from that directory:
```bash
cd /path/to/your/project
napari
## Installation

### From PyPI
```bash
pip install napari-mask-curator
