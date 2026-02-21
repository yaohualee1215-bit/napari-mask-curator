from __future__ import annotations

import contextlib
import json
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile as tiff
from napari import current_viewer
from napari.viewer import Viewer
from qtpy.QtCore import QTimer
from qtpy.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

OUT_NAME = "manual_best"
WORK_SUFFIX = "__work"


def _now() -> str:
    return time.strftime("%H:%M:%S")


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _layer_names(viewer: Viewer) -> list[str]:
    return [ly.name for ly in viewer.layers]


def _is_labels(layer) -> bool:
    # avoid importing napari.layers.Labels for compatibility
    return layer is not None and layer.__class__.__name__ == "Labels"


def _is_mask_labels_layer(layer) -> bool:
    return _is_labels(layer) and (
        ".mask" in layer.name or layer.name.endswith(WORK_SUFFIX)
    )


def _selected_layer(viewer: Viewer):
    try:
        sel = viewer.layers.selection
        if sel:
            return list(sel)[0]
    except Exception:
        pass
    try:
        return viewer.layers.selection.active
    except Exception:
        return None


def _bbox_from_bool(mask: np.ndarray):
    ys, xs = np.where(mask)
    if ys.size == 0:
        return None
    return int(ys.min()), int(ys.max() + 1), int(xs.min()), int(xs.max() + 1)


def _pad_window(bbox, H: int, W: int, pad: int):
    y0, y1, x0, x1 = bbox
    return (
        max(0, y0 - pad),
        min(H, y1 + pad),
        max(0, x0 - pad),
        min(W, x1 + pad),
    )


class FastLogger:
    def __init__(
        self,
        out_dir: str = "curation_logs",
        batch_size: int = 300,
        interval_ms: int = 120,
    ):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(exist_ok=True)
        self.session_id = time.strftime("%Y%m%d_%H%M%S")
        self.jsonl_path = self.out_dir / f"curation_{self.session_id}.jsonl"
        self.tsv_path = self.out_dir / f"curation_{self.session_id}.tsv"

        self.q = deque()
        self.batch_size = int(batch_size)

        self.cols = [
            "time",
            "event",
            "target_id",
            "src_layer",
            "base_layer",
            "src_label",
            "overlap_pixels",
            "area",
            "win_y0",
            "win_y1",
            "win_x0",
            "win_x1",
        ]

        # keep handles open (fast), close on widget closeEvent
        self.f_jsonl = open(
            self.jsonl_path, "a", encoding="utf-8", buffering=1
        )
        self.f_tsv = open(self.tsv_path, "a", encoding="utf-8", buffering=1)
        if self.tsv_path.stat().st_size == 0:
            self.f_tsv.write("\t".join(self.cols) + "\n")

        self.timer = QTimer()
        self.timer.setInterval(int(interval_ms))
        self.timer.timeout.connect(self.flush_some)
        self.timer.start()
        print(
            f"[{_now()}] [LOG] streaming -> {self.jsonl_path} / {self.tsv_path}"
        )

    def push(self, row: dict):
        self.q.append(row)

    def flush_some(self):
        if not self.q:
            return
        n = min(self.batch_size, len(self.q))
        pop = self.q.popleft
        fj, ft = self.f_jsonl, self.f_tsv
        cols = self.cols
        for _ in range(n):
            r = pop()
            fj.write(json.dumps(r, ensure_ascii=False) + "\n")
            win = r.get("win", [None, None, None, None])
            rr = {
                "time": r.get("time", ""),
                "event": r.get("event", ""),
                "target_id": r.get("target_id", ""),
                "src_layer": r.get("src_layer", ""),
                "base_layer": r.get("base_layer", ""),
                "src_label": r.get("src_label", ""),
                "overlap_pixels": r.get("overlap_pixels", ""),
                "area": r.get("area", ""),
                "win_y0": win[0],
                "win_y1": win[1],
                "win_x0": win[2],
                "win_x1": win[3],
            }
            ft.write("\t".join(str(rr.get(c, "")) for c in cols) + "\n")

    def flush_all(self):
        while self.q:
            self.flush_some()

    def close(self):
        try:
            self.timer.stop()
        except Exception:
            pass
        try:
            self.flush_all()
        except Exception:
            pass
        try:
            self.f_jsonl.close()
            self.f_tsv.close()
        except Exception:
            pass


@dataclass
class UndoRecord:
    work_layer: str
    src_label: int
    new_id: int
    # windows are padded ROI used by the collect operation
    y0: int
    y1: int
    x0: int
    x1: int
    # manual_best window before click
    manual_before: np.ndarray
    # work window before click
    work_before: np.ndarray


def _compact_labels(arr: np.ndarray):
    """Relabel >0 ids to 1..N in ascending order. Return (new_arr, mapping dict)."""
    flat = arr.ravel()
    ids = np.unique(flat)
    ids = ids[ids > 0]
    if ids.size == 0:
        return arr, {}
    new_ids = np.arange(1, ids.size + 1, dtype=np.int64)
    # mapping vector
    mapping_vec = np.zeros(int(ids.max()) + 1, dtype=np.int64)
    mapping_vec[ids] = new_ids
    out = mapping_vec[arr.astype(np.int64)]
    mapping = {
        int(o): int(n)
        for o, n in zip(ids.tolist(), new_ids.tolist(), strict=False)
    }
    return out.astype(np.uint32, copy=False), mapping


class MaskCuratorPanel(QWidget):
    def __init__(self, viewer: Viewer):
        super().__init__()
        self.viewer = viewer

        self.PARAM = {
            "overlap_prune_thr": 0.70,
            "window_pad": 30,
            "blink_interval_s": 0.5,
        }

        self.collect_mode = False
        self.undo_stack: list[UndoRecord] = []
        self.logger = FastLogger()

        self._blink = {"on": False, "layer_name": None}
        self.blink_timer = QTimer()
        self.blink_timer.setInterval(
            int(self.PARAM["blink_interval_s"] * 1000)
        )
        self.blink_timer.timeout.connect(self._blink_tick)

        self._global_click = self._on_mouse_drag  # keep reference
        self.viewer.mouse_drag_callbacks.append(self._global_click)

        self._build_ui()
        self._bind_hotkeys_once()

    # ---------- UI ----------
    def _build_ui(self):
        root = QVBoxLayout(self)

        title = QLabel("Mask Curator")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        root.addWidget(title)

        # threshold row
        row_thr = QHBoxLayout()
        row_thr.addWidget(QLabel("Overlap prune thr (keep if < thr):"))
        self.spin_thr = QDoubleSpinBox()

        self.spin_thr.setFixedWidth(120)
        self.spin_thr.setRange(0.0, 1.0)
        self.spin_thr.setSingleStep(0.05)
        self.spin_thr.setValue(float(self.PARAM["overlap_prune_thr"]))
        self.spin_thr.valueChanged.connect(
            lambda v: self.PARAM.__setitem__("overlap_prune_thr", float(v))
        )
        row_thr.addWidget(self.spin_thr)
        root.addLayout(row_thr)

        # pad row
        row_pad = QHBoxLayout()
        row_pad.addWidget(QLabel("ROI pad(px):"))
        self.spin_pad = QSpinBox()

        self.spin_pad.setFixedWidth(120)
        self.spin_pad.setRange(0, 500)
        self.spin_pad.setValue(int(self.PARAM["window_pad"]))
        self.spin_pad.valueChanged.connect(
            lambda v: self.PARAM.__setitem__("window_pad", int(v))
        )
        row_pad.addWidget(self.spin_pad)
        root.addLayout(row_pad)

        self.status = QLabel(
            "Select a Labels mask layer (.mask) then click Follow → Work."
        )

        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.status.setStyleSheet("font-family: Menlo, monospace;")
        root.addWidget(self.status)

        self.btn_follow = QPushButton("Follow → Work + Prune")
        self.btn_toggle = QPushButton("Collect: OFF (A)")
        self.btn_undo = QPushButton("Undo (U)")
        self.btn_prune = QPushButton("Prune Now (P)")
        self.btn_reset = QPushButton("Reset Work (R)")
        self.btn_save = QPushButton("Save manual_best (S)")
        self.btn_blink = QPushButton("Blink Selected Layer")

        self.btn_follow.clicked.connect(self.on_follow)
        self.btn_toggle.clicked.connect(self.on_toggle_collect)
        self.btn_undo.clicked.connect(self.on_undo)
        self.btn_prune.clicked.connect(self.on_prune)
        self.btn_reset.clicked.connect(self.on_reset)
        self.btn_save.clicked.connect(self.on_save)
        self.btn_blink.clicked.connect(self.toggle_blink)

        root.addWidget(self.btn_follow)
        root.addWidget(self.btn_toggle)
        root.addWidget(self.btn_undo)
        root.addWidget(self.btn_prune)
        root.addWidget(self.btn_reset)
        root.addWidget(self.btn_save)
        root.addWidget(self.btn_blink)

    def _set_status(self, txt: str):
        self.status.setText(txt)

    # ---------- hotkeys ----------
    def _bind_hotkeys_once(self):
        # avoid rebinding every time a widget opens
        if getattr(self, "_hotkeys_bound", False):
            return
        self.viewer.bind_key("Alt-A", overwrite=True)(
            lambda v: self.on_toggle_collect()
        )
        self.viewer.bind_key("Alt-P", overwrite=True)(
            lambda v: self.on_prune()
        )
        self.viewer.bind_key("Alt-R", overwrite=True)(
            lambda v: self.on_reset()
        )
        self.viewer.bind_key("Alt-S", overwrite=True)(lambda v: self.on_save())
        self.viewer.bind_key("Alt-U", overwrite=True)(lambda v: self.on_undo())
        self._hotkeys_bound = True

    # ---------- layers ----------
    def ensure_manual_best(self, shape):
        if OUT_NAME in _layer_names(self.viewer):
            out = self.viewer.layers[OUT_NAME]
            if not _is_labels(out):
                raise RuntimeError(f"{OUT_NAME} exists but is not Labels.")
            if out.data.shape != shape:
                raise RuntimeError(
                    f"{OUT_NAME} shape {out.data.shape} != {shape}"
                )
            return out
        arr = np.zeros(shape, dtype=np.uint32)
        return self.viewer.add_labels(arr, name=OUT_NAME)

    def backup_manual_best_if_exists(self):
        if OUT_NAME not in _layer_names(self.viewer):
            return
        lb = self.viewer.layers[OUT_NAME]
        if not _is_labels(lb):
            return
        ts = time.strftime("%Y%m%d_%H%M%S")
        name = f"{OUT_NAME}_backup_{ts}"
        self.viewer.add_labels(lb.data.copy(), name=name)
        print(f"[{_now()}] [BACKUP] created layer: {name}")

    def get_or_make_work(self, src_layer):
        if src_layer.name.endswith(WORK_SUFFIX):
            return src_layer
        work_name = src_layer.name + WORK_SUFFIX
        if work_name in _layer_names(self.viewer):
            w = self.viewer.layers[work_name]
            if not _is_labels(w):
                raise RuntimeError(f"{work_name} exists but is not Labels.")
            src_layer.visible = False
            w.visible = True
            return w
        w = self.viewer.add_labels(src_layer.data.copy(), name=work_name)
        src_layer.visible = False
        w.visible = True
        print(f"[{_now()}] [WORK] created {work_name} (original hidden)")
        return w

    def auto_follow_to_work(self):
        sel = _selected_layer(self.viewer)
        if not _is_mask_labels_layer(sel):
            return None
        work = (
            self.get_or_make_work(sel)
            if not sel.name.endswith(WORK_SUFFIX)
            else sel
        )
        try:
            self.viewer.layers.selection.clear()
            self.viewer.layers.selection.add(work)
        except Exception:
            pass
        return work

    # ---------- prune ----------
    def prune_work_by_manual_overlap(self, work_layer, thr: float) -> int:
        if OUT_NAME not in _layer_names(self.viewer):
            return 0
        manual = self.viewer.layers[OUT_NAME].data
        w = work_layer.data
        if manual.shape != w.shape:
            raise RuntimeError("manual_best shape != work shape")

        w_flat = w.ravel().astype(np.int64, copy=False)
        max_id = int(w_flat.max())
        if max_id == 0:
            return 0

        fg = w_flat > 0
        area = np.bincount(w_flat[fg], minlength=max_id + 1)

        ov_fg = (manual.ravel() > 0) & fg
        ov = np.bincount(w_flat[ov_fg], minlength=max_id + 1)

        ratio = np.zeros_like(ov, dtype=np.float32)
        valid = area > 0
        ratio[valid] = ov[valid] / area[valid]

        prune_ids = np.where(ratio >= thr)[0]
        prune_ids = prune_ids[prune_ids != 0]
        if prune_ids.size == 0:
            return 0

        keep = np.ones(max_id + 1, dtype=np.uint8)
        keep[prune_ids] = 0
        w2 = (w.astype(np.int64, copy=False) * keep[w]).astype(
            w.dtype, copy=False
        )

        work_layer.data = w2
        work_layer.refresh()
        return int(prune_ids.size)

    # ---------- collect/undo ----------
    def collect_always_newid(self, work_layer, position):
        out = self.ensure_manual_best(work_layer.data.shape)

        y = int(round(position[-2]))
        x = int(round(position[-1]))
        H, W = work_layer.data.shape[-2], work_layer.data.shape[-1]
        if y < 0 or x < 0 or y >= H or x >= W:
            return

        src_label = int(work_layer.data[y, x])
        if src_label == 0:
            return

        obj = work_layer.data == src_label
        bbox = _bbox_from_bool(obj)
        if bbox is None:
            return

        pad = int(self.PARAM["window_pad"])
        y0, y1, x0, x1 = _pad_window(bbox, H, W, pad)

        # snapshot windows for undo
        manual_before = out.data[y0:y1, x0:x1].copy()
        work_before = work_layer.data[y0:y1, x0:x1].copy()

        obj_win = obj[y0:y1, x0:x1]
        overlap_pixels = int((manual_before[obj_win] > 0).sum())
        area = int(obj_win.sum())

        new_id = int(out.data.max()) + 1

        manual_after = manual_before.copy()
        manual_after[obj_win] = np.uint32(new_id)
        out.data[y0:y1, x0:x1] = manual_after
        out.refresh()

        # remove from work
        w = work_layer.data
        w[obj] = 0
        work_layer.refresh()

        self.undo_stack.append(
            UndoRecord(
                work_layer=work_layer.name,
                src_label=src_label,
                new_id=new_id,
                y0=y0,
                y1=y1,
                x0=x0,
                x1=x1,
                manual_before=manual_before,
                work_before=work_before,
            )
        )

        self.logger.push(
            {
                "time": _ts(),
                "event": "collect_newid",
                "target_id": int(new_id),
                "src_layer": work_layer.name,
                "base_layer": (
                    work_layer.name[: -len(WORK_SUFFIX)]
                    if work_layer.name.endswith(WORK_SUFFIX)
                    else work_layer.name
                ),
                "src_label": int(src_label),
                "overlap_pixels": int(overlap_pixels),
                "area": int(area),
                "win": [int(y0), int(y1), int(x0), int(x1)],
                "params": dict(self.PARAM),
            }
        )

    def on_undo(self):
        if not self.undo_stack:
            self._set_status("Undo: empty")
            return
        u = self.undo_stack.pop()

        if OUT_NAME not in _layer_names(self.viewer):
            self._set_status("Undo failed: manual_best not found")
            return
        manual = self.viewer.layers[OUT_NAME]
        if not _is_labels(manual):
            self._set_status("Undo failed: manual_best is not Labels")
            return

        if u.work_layer not in _layer_names(self.viewer):
            self._set_status(
                f"Undo failed: work layer '{u.work_layer}' not found"
            )
            return
        work = self.viewer.layers[u.work_layer]
        if not _is_labels(work):
            self._set_status("Undo failed: work layer is not Labels")
            return

        y0, y1, x0, x1 = u.y0, u.y1, u.x0, u.x1
        manual.data[y0:y1, x0:x1] = u.manual_before
        manual.refresh()

        work.data[y0:y1, x0:x1] = u.work_before
        work.refresh()

        self.logger.push(
            {
                "time": _ts(),
                "event": "undo_collect",
                "target_id": int(u.new_id),
                "src_layer": u.work_layer,
                "base_layer": (
                    u.work_layer[: -len(WORK_SUFFIX)]
                    if u.work_layer.endswith(WORK_SUFFIX)
                    else u.work_layer
                ),
                "src_label": int(u.src_label),
                "overlap_pixels": "",
                "area": "",
                "win": [int(y0), int(y1), int(x0), int(x1)],
                "params": dict(self.PARAM),
            }
        )

        self._set_status("Undo: restored pre-click state")

    # ---------- callbacks ----------
    def _on_mouse_drag(self, viewer: Viewer, event):
        if not self.collect_mode:
            return
        if event.button != 1:
            return
        work = self.auto_follow_to_work()
        if work is None:
            return
        self.collect_always_newid(work, event.position)

    # ---------- buttons ----------
    def on_follow(self):
        sel = _selected_layer(self.viewer)
        if not _is_mask_labels_layer(sel):
            self._set_status(
                "Follow: select a Labels mask layer (name contains .mask)"
            )
            return
        # manual_best backup if exists, then reuse
        self.backup_manual_best_if_exists()
        work = self.get_or_make_work(sel)
        removed = self.prune_work_by_manual_overlap(
            work, float(self.PARAM["overlap_prune_thr"])
        )
        self._set_status(f"Follow OK: {work.name} | pruned={removed}")

    def on_toggle_collect(self):
        self.collect_mode = not self.collect_mode
        self.btn_toggle.setText(
            f"Collect: {'ON' if self.collect_mode else 'OFF'} (A)"
        )
        if self.collect_mode:
            work = self.auto_follow_to_work()
            if work is not None:
                removed = self.prune_work_by_manual_overlap(
                    work, float(self.PARAM["overlap_prune_thr"])
                )
                self._set_status(
                    f"Collect ON | {work.name} | pruned={removed}"
                )
            else:
                self._set_status("Collect ON | (select a .mask Labels layer)")
        else:
            self._set_status("Collect OFF")

    def on_prune(self):
        sel = _selected_layer(self.viewer)
        if sel is None:
            self._set_status("Prune: no layer selected")
            return
        work = (
            sel
            if sel.name.endswith(WORK_SUFFIX)
            else self.auto_follow_to_work()
        )
        if work is None:
            self._set_status("Prune: select a work layer or .mask layer")
            return
        removed = self.prune_work_by_manual_overlap(
            work, float(self.PARAM["overlap_prune_thr"])
        )
        self._set_status(
            f"Prune OK: removed={removed} thr={self.PARAM['overlap_prune_thr']}"
        )

    def on_reset(self):
        sel = _selected_layer(self.viewer)
        if not _is_mask_labels_layer(sel):
            self._set_status("Reset: select a .mask or __work Labels layer")
            return
        if sel.name.endswith(WORK_SUFFIX):
            base = sel.name[: -len(WORK_SUFFIX)]
            if base not in _layer_names(self.viewer):
                self._set_status("Reset: base layer not found")
                return
            src = self.viewer.layers[base]
            sel.data = src.data.copy()
            sel.refresh()
            removed = self.prune_work_by_manual_overlap(
                sel, float(self.PARAM["overlap_prune_thr"])
            )
            self._set_status(f"Reset OK: {sel.name} | pruned={removed}")
            return

        work = self.get_or_make_work(sel)
        work.data = sel.data.copy()
        work.refresh()
        removed = self.prune_work_by_manual_overlap(
            work, float(self.PARAM["overlap_prune_thr"])
        )
        self._set_status(f"Reset OK: {work.name} | pruned={removed}")

    def on_save(self):
        if OUT_NAME not in _layer_names(self.viewer):
            self._set_status("Save: manual_best not found")
            return
        lb = self.viewer.layers[OUT_NAME]
        if not _is_labels(lb):
            self._set_status("Save: manual_best is not Labels")
            return

        # compact to 1..N and write mapping
        compact, mapping = _compact_labels(
            lb.data.astype(np.int64, copy=False)
        )
        lb.data = compact
        lb.refresh()

        out_id = Path("manual_best_ID_uint32.tif")
        out_blk = Path("manual_best_black_uint8.tif")
        out_map = Path("manual_best_id_map_old_to_new.tsv")

        tiff.imwrite(
            out_id, lb.data.astype(np.uint32, copy=False), compression="zlib"
        )
        black = (lb.data > 0).astype(np.uint8) * 255
        tiff.imwrite(out_blk, black, compression="zlib")

        with open(out_map, "w", encoding="utf-8") as f:
            f.write("old_id\tnew_id\n")
            for k in sorted(mapping):
                f.write(f"{k}\t{mapping[k]}\n")

        self._set_status(
            f"Saved: {out_id.name}, {out_blk.name}, {out_map.name}"
        )

    # ---------- blink ----------
    def _blink_tick(self):
        name = self._blink["layer_name"]
        if not name or name not in _layer_names(self.viewer):
            self.stop_blink(restore=False)
            return
        layer = self.viewer.layers[name]
        layer.visible = not layer.visible

    def start_blink(self):
        sel = _selected_layer(self.viewer)
        if sel is None:
            return
        self._blink["on"] = True
        self._blink["layer_name"] = sel.name
        self.blink_timer.start()
        print(f"[{_now()}] [BLINK] start: {sel.name}")

    def stop_blink(self, restore=True):
        if self.blink_timer.isActive():
            self.blink_timer.stop()
        if (
            restore
            and self._blink["layer_name"]
            and self._blink["layer_name"] in _layer_names(self.viewer)
        ):
            self.viewer.layers[self._blink["layer_name"]].visible = True
        self._blink["on"] = False
        self._blink["layer_name"] = None
        print(f"[{_now()}] [BLINK] stop")

    def toggle_blink(self):
        if self._blink["on"]:
            self.stop_blink(restore=True)
        else:
            self.start_blink()

    # ---------- cleanup ----------

    def _cleanup(self):
        """Detach callbacks/timers so the widget can be reopened safely."""
        # stop blink timer if any
        with contextlib.suppress(Exception):
            if hasattr(self, "blink_timer") and self.blink_timer.isActive():
                self.blink_timer.stop()

        # remove mouse callbacks
        with contextlib.suppress(Exception):
            if (
                hasattr(self, "_on_mouse_drag")
                and self._on_mouse_drag in self.viewer.mouse_drag_callbacks
            ):
                self.viewer.mouse_drag_callbacks.remove(self._on_mouse_drag)

        # turn off collect mode (avoid leaving half-state)
        with contextlib.suppress(Exception):
            self.collect_mode = False
            if hasattr(self, "btn_toggle"):
                self.btn_toggle.setText("Collect: OFF (Alt-A)")

        # NOTE: we do NOT unbind viewer keys here because bind_key modifies global keymap.
        # Using Alt-* makes collisions unlikely; leaving them is usually acceptable.
        # If you want hard-unbind, we can implement it by restoring previous keymap entries.

    def closeEvent(self, event):
        self._cleanup()
        try:
            self.stop_blink(restore=True)
        except Exception:
            pass
        try:
            if self._global_click in self.viewer.mouse_drag_callbacks:
                self.viewer.mouse_drag_callbacks.remove(self._global_click)
        except Exception:
            pass
        try:
            self.logger.close()
        except Exception:
            pass
        super().closeEvent(event)


def make_mask_curation_widget() -> QWidget:
    viewer = current_viewer()
    return MaskCuratorPanel(viewer)
