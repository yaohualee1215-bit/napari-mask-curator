A napari dock widget for **manual curation of segmentation masks**.
It is designed for workflows where you generate multiple candidate masks (e.g., Cellpose/Baysor/parameter sweeps),
and then **quickly pick the best instance per cell/nucleus** inside napari with full traceability.

## Key features

- **Follow → Work**: create a working copy `*.mask__work` from your selected mask layer, so the original stays untouched.
- **Collect (Always NEW ID)**: click an object in `__work` to copy it into `manual_best` with a **new unique label ID**.
- **Undo (U / Alt-U)**: revert the last collect and restore the **exact pre-click state**.
- **Overlap prune (P / Alt-P)**: remove candidate objects in `__work` that overlap too much with `manual_best` (avoid duplicates).
- **Compact save (S / Alt-S)**: before saving, relabel `manual_best` to **consecutive IDs (1..N)**.
- **Fast logging**: JSONL + TSV logs of every action (for methods papers / traceability).
- **Blink**: toggle selected layer visibility at 0.5s to compare masks quickly.

## Compatibility

- **Python:** 3.10–3.13 (tested on 3.11)
- **napari:** 0.6.x (tested on 0.6.6)

## What input masks should look like

- Input mask should be a **Labels** layer.
- The layer name is expected to contain **`.mask`** (example: `d290_cp0_fl0.2.mask`).
  - This is how the plugin identifies which layers are “candidate masks”.

> Tip: if your mask is a LZW-compressed TIFF and napari fails to open it, install `imagecodecs`:
>
> `conda install -c conda-forge imagecodecs`

## Typical workflow

1. **Open napari**
2. Load:
   - A background image (optional), e.g. DAPI
   - A candidate mask Labels layer, e.g. `d290_cp0_fl0.2.mask`
3. Open the panel: **Plugins → Mask Curator → Mask Curation**
4. Select your `*.mask` layer and click **Follow → Work + Prune**
   - Creates `*.mask__work`
   - Creates/uses `manual_best`
5. Click **Collect: OFF (A)** to switch it to **Collect: ON**
6. **Left-click** objects in `__work` to collect into `manual_best`
7. If you mis-click, press **Undo** (or **U / Alt-U**)
8. Press **Prune Now** (or **P / Alt-P**) to prune overlaps
9. Press **Save manual_best** (or **S / Alt-S**) to export final curated masks with compact IDs.

## Hotkeys

- **Alt-A**: Toggle Collect ON/OFF
- **Alt-U**: Undo last collect
- **Alt-P**: Prune Now
- **Alt-R**: Reset Work
- **Alt-S**: Save manual_best

(Buttons are also provided for all actions.)

## Parameters (panel)

- **Overlap prune thr**: candidates are removed if overlap ratio **≥ thr**
- **ROI pad(px)**: padding around object bbox used for fast local updates

## Outputs

When saving, the plugin writes:

- `manual_best_ID_uint32.tif` — curated Labels with compact consecutive IDs (1..N)
- `manual_best_black_uint8.tif` — binary mask (0/255)
- `manual_best_id_map_old_to_new.tsv` — mapping from pre-save IDs to compact IDs

Logs (traceability):

- `curation_logs/curation_<timestamp>.jsonl`
- `curation_logs/curation_<timestamp>.tsv`

## Installation

### From PyPI
```bash
pip install napari-mask-curator
