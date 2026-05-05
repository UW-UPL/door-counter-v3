from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import cv2
import numpy as np
from filterpy.kalman import KalmanFilter
from scipy.optimize import linear_sum_assignment
from skimage.morphology import h_maxima


_CENTRAL_TZ = ZoneInfo("America/Chicago")


@dataclass
class Config:
    rows: int = 180
    cols: int = 240
    border: int = 8

    depth_min_mm: float = 500.0
    depth_max_mm: float = 3800.0

    floor_percentile: float = 50.0

    h_clip_mm: float = 3000.0
    h_blur_sigma: float = 1.5

    fg_height_mm: float = 800.0
    open_kernel: int = 3
    close_kernel: int = 5
    min_blob_area: int = 400

    h_prominence_mm: float = 250.0
    head_min_peak_band_mm: float = 1500.0
    head_nms_px: int = 30
    min_head_area: int = 150
    max_head_area: int = 8000
    min_head_peak_mm: float = 1400.0

    gate_px: float = 35.0
    gate_px_recover: float = 50.0
    min_hits: int = 3
    max_age: int = 15

    line_a: int = 72
    line_b: int = 168


def calibrate_floor(frames, cfg):
    valid = (frames >= cfg.depth_min_mm) & (frames <= cfg.depth_max_mm)
    masked = np.where(valid, frames, np.inf).astype(np.float32)
    if cfg.floor_percentile == 50.0:
        floor = np.median(masked, axis=0)
    else:
        masked_nan = np.where(valid, frames, np.nan).astype(np.float32)
        floor = np.nanpercentile(masked_nan, cfg.floor_percentile, axis=0)
    bad = ~np.isfinite(floor)
    if bad.any():
        good = floor[~bad]
        fallback = float(np.median(good)) if good.size else 2500.0
        floor[bad] = fallback
    return floor.astype(np.float32)


class Preprocessor:
    def __init__(self, floor_ref, cfg):
        self.floor = floor_ref
        self.cfg = cfg
        self._prev = []
        rows, cols = floor_ref.shape
        self._border_mask = np.ones((rows, cols), dtype=bool)
        b = cfg.border
        if b > 0:
            self._border_mask[:b, :] = False
            self._border_mask[-b:, :] = False
            self._border_mask[:, :b] = False
            self._border_mask[:, -b:] = False

    def __call__(self, depth):
        cfg = self.cfg
        depth = depth.astype(np.float32, copy=False)
        clean = np.where(
            (depth >= cfg.depth_min_mm) & (depth <= cfg.depth_max_mm),
            depth, self.floor,
        )
        self._prev.append(clean)
        if len(self._prev) > 3:
            self._prev.pop(0)
        if len(self._prev) == 1:
            med = self._prev[0]
        else:
            med = np.median(np.stack(self._prev, axis=0), axis=0)
        valid = (med >= cfg.depth_min_mm) & (med <= cfg.depth_max_mm) & self._border_mask
        height = np.where(valid, self.floor - med, 0.0)
        height = np.clip(height, 0.0, cfg.h_clip_mm)
        return height.astype(np.uint16), valid


@dataclass
class Detection:
    y: float
    x: float
    peak_h_mm: float
    area: int


def _weighted_centroid(H, mask):
    ys, xs = np.where(mask)
    if len(ys) == 0:
        return 0.0, 0.0
    w = H[ys, xs].astype(np.float32)
    w_sum = float(w.sum())
    if w_sum <= 0:
        return float(ys.mean()), float(xs.mean())
    return float((ys * w).sum() / w_sum), float((xs * w).sum() / w_sum)


def _find_head_peaks(H_smooth, blob_mask, cfg):
    H_blob = np.where(blob_mask, H_smooth, 0).astype(np.uint16)
    hm = h_maxima(H_blob, int(cfg.h_prominence_mm))
    if not hm.any():
        y, x = np.unravel_index(H_blob.argmax(), H_blob.shape)
        return [(int(y), int(x), float(H_blob[y, x]))]

    n, labels = cv2.connectedComponents(hm.astype(np.uint8), connectivity=8)
    peaks = []
    for i in range(1, n):
        region = labels == i
        ys, xs = np.where(region)
        cy, cx = ys.mean(), xs.mean()
        k = np.argmin((ys - cy) ** 2 + (xs - cx) ** 2)
        py, px = int(ys[k]), int(xs[k])
        peak_h = float(H_blob[py, px])
        if peak_h < cfg.head_min_peak_band_mm:
            continue
        peaks.append((py, px, peak_h))

    peaks.sort(key=lambda p: -p[2])
    kept = []
    nms_sq = cfg.head_nms_px ** 2
    for p in peaks:
        y, x, h = p
        ok = True
        for ky, kx, _ in kept:
            if (y - ky) ** 2 + (x - kx) ** 2 < nms_sq:
                ok = False
                break
        if ok:
            kept.append(p)
    return kept


def _split_blob(H, H_smooth, blob_mask, cfg):
    peaks = _find_head_peaks(H_smooth, blob_mask, cfg)
    peaks = [p for p in peaks if p[2] >= cfg.min_head_peak_mm]
    if not peaks:
        return []

    if len(peaks) == 1:
        py, px, peak_h = peaks[0]
        area = int(blob_mask.sum())
        cy, cx = _weighted_centroid(H, blob_mask)
        return [Detection(y=cy, x=cx, peak_h_mm=peak_h, area=area)]

    markers = np.zeros(H.shape, dtype=np.int32)
    for i, (y, x, _) in enumerate(peaks, start=1):
        markers[y, x] = i
    bg_idx = np.where(~blob_mask)
    if len(bg_idx[0]) > 0:
        markers[bg_idx[0][0], bg_idx[1][0]] = len(peaks) + 1

    inv = 255 - cv2.normalize(
        np.where(blob_mask, H_smooth, 0).astype(np.uint16),
        None, 0, 255, cv2.NORM_MINMAX,
    ).astype(np.uint8)
    inv_bgr = cv2.cvtColor(inv, cv2.COLOR_GRAY2BGR)
    cv2.watershed(inv_bgr, markers)

    dets = []
    for i, (py, px, peak_h) in enumerate(peaks, start=1):
        region = (markers == i) & blob_mask
        area = int(region.sum())
        if area < cfg.min_head_area:
            continue
        cy, cx = _weighted_centroid(H, region)
        dets.append(Detection(y=cy, x=cx, peak_h_mm=peak_h, area=area))
    return dets


def detect_heads(height, valid, cfg):
    fg = ((height > cfg.fg_height_mm) & valid).astype(np.uint8)
    if fg.sum() == 0:
        return []
    k_open = np.ones((cfg.open_kernel, cfg.open_kernel), np.uint8)
    k_close = np.ones((cfg.close_kernel, cfg.close_kernel), np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, k_open)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, k_close)

    H_smooth = cv2.GaussianBlur(
        height.astype(np.float32), (0, 0), cfg.h_blur_sigma
    ).astype(np.uint16)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(fg, 8)
    out = []
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < cfg.min_blob_area:
            continue
        blob_mask = labels == i
        out.extend(_split_blob(height, H_smooth, blob_mask, cfg))
    return out


def _make_kf(init_y, init_x):
    kf = KalmanFilter(dim_x=4, dim_z=2)
    dt = 1.0
    kf.F = np.array([
        [1, 0, dt, 0],
        [0, 1, 0, dt],
        [0, 0, 1, 0],
        [0, 0, 0, 1],
    ], dtype=np.float32)
    kf.H = np.array([
        [1, 0, 0, 0],
        [0, 1, 0, 0],
    ], dtype=np.float32)
    kf.P *= 10.0
    kf.R *= 4.0
    kf.Q = np.eye(4, dtype=np.float32)
    kf.Q[2, 2] = kf.Q[3, 3] = 4.0
    kf.x = np.array([[init_y], [init_x], [0], [0]], dtype=np.float32)
    return kf


@dataclass
class Track:
    track_id: int
    kf: KalmanFilter
    hits: int = 0
    age: int = 0
    time_since_update: int = 0
    confirmed: bool = False
    history: List[Tuple[float, float]] = field(default_factory=list)
    last_zone: str = "?"
    zone_seq: List[str] = field(default_factory=list)
    counted: bool = False

    @property
    def pos(self):
        return float(self.kf.x[0, 0]), float(self.kf.x[1, 0])


class Tracker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.tracks = []
        self._next_id = 1

    def update(self, detections):
        for t in self.tracks:
            t.kf.predict()
            t.age += 1
            t.time_since_update += 1

        if self.tracks and detections:
            cost = np.full((len(self.tracks), len(detections)), 1e6, dtype=np.float32)
            for i, t in enumerate(self.tracks):
                ty, tx = t.pos
                for j, d in enumerate(detections):
                    dist = ((ty - d.y) ** 2 + (tx - d.x) ** 2) ** 0.5
                    if dist <= self.cfg.gate_px:
                        cost[i, j] = dist
            row_idx, col_idx = linear_sum_assignment(cost)
            assigned_t = set()
            assigned_d = set()
            for i, j in zip(row_idx, col_idx):
                if cost[i, j] >= 1e6:
                    continue
                t = self.tracks[i]
                d = detections[j]
                t.kf.update(np.array([[d.y], [d.x]], dtype=np.float32))
                t.hits += 1
                t.time_since_update = 0
                t.history.append((d.y, d.x))
                if t.hits >= self.cfg.min_hits:
                    t.confirmed = True
                assigned_t.add(i)
                assigned_d.add(j)
        else:
            assigned_t = set()
            assigned_d = set()

        unmatched_t = [i for i in range(len(self.tracks)) if i not in assigned_t]
        unmatched_d = [j for j in range(len(detections)) if j not in assigned_d]
        if unmatched_t and unmatched_d:
            cost2 = np.full(
                (len(unmatched_t), len(unmatched_d)), 1e6, dtype=np.float32,
            )
            for i, ti in enumerate(unmatched_t):
                t = self.tracks[ti]
                if not t.confirmed or t.counted:
                    continue
                if t.time_since_update == 0 or t.time_since_update > 5:
                    continue
                if not t.history:
                    continue
                last_y, last_x = t.history[-1]
                for j, dj in enumerate(unmatched_d):
                    d = detections[dj]
                    dist = ((last_y - d.y) ** 2 + (last_x - d.x) ** 2) ** 0.5
                    if dist <= self.cfg.gate_px_recover:
                        cost2[i, j] = dist
            if (cost2 < 1e6).any():
                ri, ci = linear_sum_assignment(cost2)
                for i, j in zip(ri, ci):
                    if cost2[i, j] >= 1e6:
                        continue
                    ti = unmatched_t[i]
                    dj = unmatched_d[j]
                    t = self.tracks[ti]
                    d = detections[dj]
                    t.kf.update(np.array([[d.y], [d.x]], dtype=np.float32))
                    t.hits += 1
                    t.time_since_update = 0
                    t.history.append((d.y, d.x))
                    if t.hits >= self.cfg.min_hits:
                        t.confirmed = True
                    assigned_t.add(ti)
                    assigned_d.add(dj)

        for j, d in enumerate(detections):
            if j in assigned_d:
                continue
            new = Track(track_id=self._next_id, kf=_make_kf(d.y, d.x))
            new.hits = 1
            new.history.append((d.y, d.x))
            self._next_id += 1
            self.tracks.append(new)

        self.tracks = [
            t for t in self.tracks if t.time_since_update <= self.cfg.max_age
        ]
        return list(self.tracks)


@dataclass
class CountEvent:
    frame_idx: int
    track_id: int
    direction: str
    ordinal: int = 0


class ZoneCounter:
    ENTRY_SEQ = ("A", "M", "B")
    EXIT_SEQ = ("B", "M", "A")

    def __init__(self, cfg):
        self.cfg = cfg
        self.entries = 0
        self.exits = 0

    def zone_of(self, x):
        if x < self.cfg.line_a:
            return "A"
        if x >= self.cfg.line_b:
            return "B"
        return "M"

    def update(self, tracks, frame_idx):
        events = []
        for t in tracks:
            if not t.confirmed:
                continue
            if t.time_since_update != 0:
                continue
            _, x = t.pos
            zone = self.zone_of(x)
            if zone == t.last_zone:
                continue
            t.zone_seq.append(zone)
            t.last_zone = zone
            seq = tuple(t.zone_seq[-3:])
            if seq == self.ENTRY_SEQ:
                self.entries += 1
                t.counted = True
                events.append(CountEvent(frame_idx, t.track_id, "entry",
                                         ordinal=self.entries))
            elif seq == self.EXIT_SEQ:
                self.exits += 1
                t.counted = True
                events.append(CountEvent(frame_idx, t.track_id, "exit",
                                         ordinal=self.exits))
        return events


class DoorwayCounter:
    def __init__(self, floor_ref, cfg=None):
        self.cfg = cfg or Config()
        self.pre = Preprocessor(floor_ref, self.cfg)
        self.tracker = Tracker(self.cfg)
        self.counter = ZoneCounter(self.cfg)
        self.frame_idx = -1

    def step(self, depth):
        self.frame_idx += 1
        height, valid = self.pre(depth)
        dets = detect_heads(height, valid, self.cfg)
        tracks = self.tracker.update(dets)
        events = self.counter.update(tracks, self.frame_idx)
        return height, dets, tracks, events

    @property
    def totals(self):
        return self.counter.entries, self.counter.exits


_TRACK_COLORS = [
    (255, 120, 120), (120, 255, 120), (120, 180, 255),
    (255, 200, 100), (220, 120, 255), (120, 255, 220),
]


def draw_overlay(height, dets, tracks, totals, cfg, scale=3,
                 last_event=None, frame_idx=None,
                 timestamp_epoch=None, initial=0):
    vis = np.clip(height.astype(np.float32) / cfg.h_clip_mm, 0, 1)
    vis = (vis * 255).astype(np.uint8)
    frame = cv2.applyColorMap(vis, cv2.COLORMAP_JET)
    Wp, Hp = cfg.cols * scale, cfg.rows * scale
    frame = cv2.resize(frame, (Wp, Hp), interpolation=cv2.INTER_NEAREST)

    ax, bx = cfg.line_a * scale, cfg.line_b * scale
    cv2.line(frame, (ax, 0), (ax, Hp), (255, 255, 255), 1)
    cv2.line(frame, (bx, 0), (bx, Hp), (255, 255, 255), 1)
    cv2.putText(frame, "A (commons)", (10, 20), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (255, 255, 255), 1)
    cv2.putText(frame, "M", ((ax + bx) // 2 - 6, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.putText(frame, "B (upl)", (bx + 10, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    for d in dets:
        px, py = int(d.x * scale), int(d.y * scale)
        cv2.drawMarker(frame, (px, py), (255, 255, 255),
                       cv2.MARKER_CROSS, 14, 2)
        cv2.putText(frame, f"{int(d.peak_h_mm)}", (px + 6, py - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

    for t in tracks:
        if not t.confirmed:
            continue
        col = _TRACK_COLORS[t.track_id % len(_TRACK_COLORS)]
        ty, tx = t.pos
        cx, cy = int(tx * scale), int(ty * scale)
        cv2.circle(frame, (cx, cy), 10, col, 2)
        label = f"#{t.track_id}" + (" *" if t.counted else "")
        cv2.putText(frame, label, (cx + 12, cy + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1)
        pts = t.history[-12:]
        for a, b in zip(pts[:-1], pts[1:]):
            cv2.line(frame,
                     (int(a[1] * scale), int(a[0] * scale)),
                     (int(b[1] * scale), int(b[0] * scale)), col, 1)

    bar = np.zeros((60, Wp, 3), dtype=np.uint8)
    ent, ex = totals
    fpref = f"f{frame_idx:4d}   " if frame_idx is not None else ""
    if timestamp_epoch is not None:
        ct = datetime.fromtimestamp(timestamp_epoch, _CENTRAL_TZ)
        ts_str = (f"{fpref}epoch={timestamp_epoch:.3f}   "
                  f"{ct.strftime('%-I:%M.%S %p')} CT")
    else:
        ts_str = fpref.rstrip() if fpref else ""
    if ts_str:
        cv2.putText(bar, ts_str, (10, 20), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (200, 200, 200), 1)
    count_now = initial + ent - ex
    cv2.putText(bar, f"entries={ent}  exits={ex}  count={count_now}",
                (10, 48), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (255, 255, 255), 1)
    if last_event is not None:
        c = (80, 255, 80) if last_event.direction == "entry" else (93, 11, 227)
        cv2.putText(
            bar, f"{last_event.direction.upper()} #{last_event.ordinal}",
            (Wp - 260, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.7, c, 2,
        )
    return np.vstack([frame, bar])
