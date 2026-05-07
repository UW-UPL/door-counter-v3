# Overhead ToF doorway exit/entry counter XD

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
    #frame geometry
    rows: int = 180 # height of depth frame in pixels 
    cols: int = 240 # width of depth frame in pixels
    border: int = 8 # px to chop of every edge (lens distortion)

    #floor params 
    depth_min_mm: float = 500.0 # anything closer than 0.5m is bogus
    depth_max_mm: float = 3800.0 # anything further than 3.8 m is bogus too

    floor_percentile: float = 50.0

    #height map
    h_clip_mm: float = 3000.0 # cap heights at 3m
    h_blur_sigma: float = 1.5 # gaussian blur of H before peak finding

    #foreground
    fg_height_mm: float = 800.0 # only pixels > 80cm above floor count
    open_kernel: int = 3 # 3x3 erode then dilate kills speckles
    close_kernel: int = 5 # 5x5 dilate then erode kills tiny holes
    min_blob_area: int = 400 # blobs > 400px area get tossed 

    # head splitting (h-maxima on smoothed H)
    h_prominence_mm: float = 250.0 # head-to-shoulder drop is ~250mm
    head_min_peak_band_mm: float = 1500.0 # peaks under this are not heads
    head_nms_px: int = 30 # heads cannot be closer than ~20cm to each other
    min_head_area: int = 150 # head too small
    max_head_area: int = 8000 # head too big lol
    min_head_peak_mm: float = 1400.0 #final head height must be this tall

    #tracking
    gate_px: float = 35.0 # max distance for a match
    gate_px_recover: float = 50.0 # gate for recovery
    # the above is used by the second-pass last measured re-association
    # its for confirmed/uncounted recently lost tracks sized to cover
    # a missed frame at a walking pace (~15px) so that it can recover
    min_hits: int = 3 # frames of matches before track confirmed
    max_age: int = 15 # frames a track can survive without a match

    #zone tripwires (lines along the column axis)
    line_a: int = 72 # cols < 72 -> Commons (zone A)
    line_b: int = 168 # cols >= 168 -> UPL (zone b)


def calibrate_floor(frames: np.ndarray, cfg: Config) -> np.ndarray:
    # computes a per pixel floor reference in mm from a sequence of frames
    # uses the median because of multi-path noise
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

#pre-processing
class Preprocessor:
    # temporal 3-frame median + height-map computation
    def __init__(self, floor_ref: np.ndarray, cfg: Config) -> None:
        self.floor = floor_ref
        self.cfg = cfg
        self._prev = [] # rolling buffer of last 3 clean frames
        rows, cols = floor_ref.shape
        self._border_mask = np.ones((rows, cols), dtype=bool)
        b = cfg.border
        if b > 0:
            self._border_mask[:b, :] = False
            self._border_mask[-b:, :] = False
            self._border_mask[:, :b] = False
            self._border_mask[:, -b:] = False

    def __call__(self, depth: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        cfg = self.cfg
        depth = depth.astype(np.float32, copy=False)
        # replace invalid pixels w FLOOR VALUE
        clean = np.where(
            (depth >= cfg.depth_min_mm) & (depth <= cfg.depth_max_mm),
            depth, self.floor,
        )
        self._prev.append(clean)
        if len(self._prev) > 3:
            self._prev.pop(0)
        # first frame ever, median not possible
        if len(self._prev) == 1:
            med = self._prev[0]
        else:
            med = np.median(np.stack(self._prev, axis=0), axis=0)
        valid = (med >= cfg.depth_min_mm) & (med <= cfg.depth_max_mm) & self._border_mask
        height = np.where(valid, self.floor - med, 0.0)
        height = np.clip(height, 0.0, cfg.h_clip_mm)
        return height.astype(np.uint16), valid


#HEAD DETECTION
@dataclass
class Detection:
    y: float # which row?
    x: float # which col?
    peak_h_mm: float # how tall?
    area: int


def _weighted_centroid(H: np.ndarray, mask: np.ndarray) -> Tuple[float, float]:
    ys, xs = np.where(mask)
    # empty mask should never happen
    if len(ys) == 0:
        return 0.0, 0.0
    # these are the height vals at every true pixel of the mask
    w = H[ys, xs].astype(np.float32)
    w_sum = float(w.sum()) # total weight
    if w_sum <= 0:
        return float(ys.mean()), float(xs.mean())
    # The weighted centroid formula:
    # cy = sum(y_i * w_i) / sum(w_i)
    # cx = sum(x_i * w_i) / sum(w_i)
    return float((ys * w).sum() / w_sum), float((xs * w).sum() / w_sum)
    """
    Why WEIGHTED CENTROIDS you may be wondering?
    A weighted centroid lets some points essentially pull harder than others.
    Each point i has a weight w_i and the formula becomes cy = sum(y_i * w_i) / sum(w_i)
    its like physics center of mass if some atoms are heavier. this matters because if I
    were to take the naive plain centroid an arm sticking out (or backpack) drags the centroid 
    sideways. But if I weigh by height the head (hopefully tallest part) dominates.
    But why do we care? The Kalman filter eats this centroid every frame, if it jumps
    around due to an arm moving, kalman thinks the person is darting sideways and will
    start mispredicting. Anchoring the centroid to the head means stable measurements.
    """
    #TODO: This has been causing trouble when the sensor picks up an opening door
    # It pulls any tracker off someones head towards the door... will fix later

#H-MAXIMA
def _find_head_peaks(H_smooth: np.ndarray, blob_mask: np.ndarray, cfg: Config) -> List[Tuple[int, int, float]]:
    # find head peaks inside a blob via h-maxima on the smoothed height surface
    H_blob = np.where(blob_mask, H_smooth, 0).astype(np.uint16)
    hm = h_maxima(H_blob, int(cfg.h_prominence_mm))
    if not hm.any():
        # Flat plateau, return the blob's global maximum as the single peak
        y, x = np.unravel_index(H_blob.argmax(), H_blob.shape)
        return [(int(y), int(x), float(H_blob[y, x]))]
    # One regional max = one head, take the highest pixel inside each
    # region as peak location
    n, labels = cv2.connectedComponents(hm.astype(np.uint8), connectivity=8)
    peaks = []
    for i in range(1, n):
        region = labels == i
        ys, xs = np.where(region)
        # pick the pixel in the region closest to the region centroid
        # this is the stable "head apex" location
        cy, cx = ys.mean(), xs.mean()
        k = np.argmin((ys - cy) ** 2 + (xs - cx) ** 2)
        py, px = int(ys[k]), int(xs[k])
        peak_h = float(H_blob[py, px])
        if peak_h < cfg.head_min_peak_band_mm:
            continue
        peaks.append((py, px, peak_h))
    # NMS: keep highest peaks that are > head_nms_px apart
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
    """
    What is H-MAXIMA and why am I using it?
    Imagine the height map as a topographic map where peaks are mountains, some
    of them are tall and others are small bumps on the side of bigger mountains.
    Prominence of a peak = how far down do I have to descend before I can climb
    to a HIGHER peak? If a bump is on the slope of a mountain, its prominence is 
    small (just height of the bump above the slope). If a peak is tallest in the 
    whole map, its prominence equals its height. h_maxima(H, h) returns only the 
    peaks whos prominence is at least h. rn h is 250 mm so we only keep peaks w
    at least 250mm of vertical isolation. This solves the most FRUSTRATING issue
    faced historically with the other sensors: is this one person, or is this two
    people? Two adult heads with shoulders touching look like one big blob to 
    simple threshold based methods. But two real heads have a 'saddle' between them
    at their shoulder height. Through testing I believe ~250mm is the right h dist.
    h-maxima with h=250 sees that saddle and reports TWO peaks.
    """

#WATERSHED
def _split_blob(H: np.ndarray, H_smooth: np.ndarray, blob_mask: np.ndarray, cfg: Config) -> List[Detection]:
    # Split one cononected foreground blob into per-head detections
    peaks = _find_head_peaks(H_smooth, blob_mask, cfg)
    peaks = [p for p in peaks if p[2] >= cfg.min_head_peak_mm]
    if not peaks:
        return []
    # if it's just one person skip watershed
    if len(peaks) == 1:
        py, px, peak_h = peaks[0]
        area = int(blob_mask.sum())
        cy, cx = _weighted_centroid(H, blob_mask)
        return [Detection(y=cy, x=cx, peak_h_mm=peak_h, area=area)]
    # if 2+ peaks: watershed
    markers = np.zeros(H.shape, dtype=np.int32)
    for i, (y, x, _) in enumerate(peaks, start=1):
        markers[y, x] = i
        # ^^ plant a single seed pixel for each head. label 1,2,3...
    bg_idx = np.where(~blob_mask)
    if len(bg_idx[0]) > 0:
        markers[bg_idx[0][0], bg_idx[1][0]] = len(peaks) + 1
        # ^^ plant one background marker outside blob to prevent flood
    # INVERT, tall heads are now low and the floor is high
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
        # head too small, ignore
        if area < cfg.min_head_area:
            continue
        cy, cx = _weighted_centroid(H, region)
        dets.append(Detection(y=cy, x=cx, peak_h_mm=peak_h, area=area))
    return dets
    """
    What is WATERSHED segmentation?
    Imagine the height map flipped upsidown so the peaks become pits. Then
    imagine rain falling so water collects in each pit. As it rains more the 
    pits fill up, eventually two neighboring pits would overflow into each 
    other, that imaginary line where they would touch is the WATERSHED LINE.
    This is what splits one seemingly big blob, into "hey these are two people
    and this is where they split." Here every h-maxima peak becomes a pit/seed
    that floods outwards. Every blob pixel gets assigned to exactly one head
    """

def detect_heads(height: np.ndarray, valid: np.ndarray, cfg: Config) -> List[Detection]:
    fg = ((height > cfg.fg_height_mm) & valid).astype(np.uint8)
    # empty doorway skip everything
    if fg.sum() == 0:
        return []
    k_open = np.ones((cfg.open_kernel, cfg.open_kernel), np.uint8)
    k_close = np.ones((cfg.close_kernel, cfg.close_kernel), np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, k_open)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, k_close)

    # smoothed height for peak finding only
    # original H used for area/mask
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

#KALMAN TRACKER
def _make_kf(init_y: float, init_x: float) -> KalmanFilter:
    # constant velocity 2D: state = [y,x,vy,vx]
    kf = KalmanFilter(dim_x=4, dim_z=2)
    #time step in frames not seconds so velocity is px/framef
    dt = 1.0
    kf.F = np.array([
        [1, 0, dt, 0],
        [0, 1, 0, dt],
        [0, 0, 1, 0],
        [0, 0, 0, 1],
    ], dtype=np.float32)
    # what is the next state if no new measurements
    # standard textbook constant-velocity model
    kf.H = np.array([
        [1, 0, 0, 0],
        [0, 1, 0, 0],
    ], dtype=np.float32)
    # P = covariance of my state estimate 
    kf.P *= 10.0
    # R = measurement noise covariance
    kf.R *= 4.0
    # Q is process noise so how much can the state change
    # frame-to-frame due to stuff my model doesn't capture
    kf.Q = np.eye(4, dtype=np.float32)
    kf.Q[2, 2] = kf.Q[3, 3] = 4.0
    kf.x = np.array([[init_y], [init_x], [0], [0]], dtype=np.float32)
    return kf


@dataclass
class Track:
    track_id: int
    kf: KalmanFilter
    hits: int = 0 # no. of frames matched in a row
    age: int = 0 # no. of frames since track created
    time_since_update: int = 0 # frames since lasst successful match
    confirmed: bool = False # has it hit min_hits
    history: List[Tuple[float, float]] = field(default_factory=list)
    last_zone: str = "?"
    zone_seq: List[str] = field(default_factory=list)
    counted: bool = False

    @property
    def pos(self) -> Tuple[float, float]:
        return float(self.kf.x[0, 0]), float(self.kf.x[1, 0])

    """
    What is a Kalman filter? I'm tracking a person moving through
    the doorway, each frame I get a noisy measurement of their
    position. I want a smooth trustworthy estimate of where they 
    are AND where are they going. The kalman filter does both every 
    frame in two steps: predict step (use the motion model constant 
    velocity where do I expect them to be) and update step (when an 
    actual measurement arrives, combine prediction + measurement).
    """

class Tracker:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.tracks = []
        self._next_id = 1

    #PREDICT
    def update(self, detections: List[Detection]) -> List[Track]:
        for t in self.tracks:
            # kalman predict step
            t.kf.predict()
            t.age += 1
            t.time_since_update += 1

        if self.tracks and detections:
            cost = np.full((len(self.tracks), len(detections)), 1e6, dtype=np.float32)
            for i, t in enumerate(self.tracks):
                # kalman predicted position for this track
                ty, tx = t.pos
                for j, d in enumerate(detections):
                    dist = ((ty - d.y) ** 2 + (tx - d.x) ** 2) ** 0.5
                    if dist <= self.cfg.gate_px:
                        cost[i, j] = dist
            # HUNGARIAN algo: given the cost matrix return the assignment
            # that minimizes total cost. one-to-one matching
            # scipy gives me the algorithm in one line.
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
                    # been around for 3+ frames so it's real
                    t.confirmed = True
                assigned_t.add(i)
                assigned_d.add(j)
        else:
            assigned_t = set()
            assigned_d = set()
        # SECOND PASS RE-ASSOCIATION for unmatched tracks
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
                # skip tracks that were just matched or been gone too long
                # recovery makes sense for a missed frame or two
                if t.time_since_update == 0 or t.time_since_update > 5:
                    continue
                if not t.history:
                    continue
                # last observed pos. not kalman
                last_y, last_x = t.history[-1]
                for j, dj in enumerate(unmatched_d):
                    d = detections[dj]
                    dist = ((last_y - d.y) ** 2 + (last_x - d.x) ** 2) ** 0.5
                    if dist <= self.cfg.gate_px_recover:
                        cost2[i, j] = dist
            if (cost2 < 1e6).any():
                # only run Hungarian if any pair is in range
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
        # new tracks for unassigned detections
        for j, d in enumerate(detections):
            if j in assigned_d:
                # alr assigned
                continue
            new = Track(track_id=self._next_id, kf=_make_kf(d.y, d.x))
            new.hits = 1
            new.history.append((d.y, d.x))
            # every track gets a unique ID forever
            self._next_id += 1
            self.tracks.append(new)
        # remove stale tracks if unmatched for 15 frames
        self.tracks = [
            t for t in self.tracks if t.time_since_update <= self.cfg.max_age
        ]
        return list(self.tracks)


@dataclass
class CountEvent:
    frame_idx: int
    track_id: int
    direction: str # entry or exit
    ordinal: int = 0 # cout of THIS direction eg (entry #5)


class ZoneCounter:
    # again, A is the Commons 
    # and B is the UPL
    # M is the zone between the two tripwires
    ENTRY_SEQ = ("A", "M", "B")
    EXIT_SEQ = ("B", "M", "A")

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.entries = 0
        self.exits = 0

    def zone_of(self, x: float) -> str:
        # given x coord return which zone its in
        if x < self.cfg.line_a:
            return "A"
        if x >= self.cfg.line_b:
            return "B"
        return "M"

    def update(self, tracks: List[Track], frame_idx: int) -> List[CountEvent]:
        events = []
        for t in tracks:
            if not t.confirmed:
                continue
            if t.time_since_update != 0:
                continue
            # only care about the x-axis
            _, x = t.pos
            zone = self.zone_of(x)
            # zone has not changed
            if zone == t.last_zone:
                continue
            t.zone_seq.append(zone)
            t.last_zone = zone
            # only check for full traversal when the zone just changed
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
        """
        Why two lines/tripwires instead of one?
        Using a single tripwire would be the naive approach and would
        likely cause more miscounts. if a tracks centroid jitters by a pixel
        near the line you might get multiple crossings for a walkthough.
        The three zone, two line solution I believe is most robust.
        """

class DoorwayCounter:
    def __init__(self, floor_ref: np.ndarray, cfg: Optional[Config] = None) -> None:
        self.cfg = cfg or Config()
        self.pre = Preprocessor(floor_ref, self.cfg)
        self.tracker = Tracker(self.cfg)
        self.counter = ZoneCounter(self.cfg)
        self.frame_idx = -1

    def step(self, depth: np.ndarray) -> Tuple[np.ndarray, List[Detection], List[Track], List[CountEvent]]:
        self.frame_idx += 1
        height, valid = self.pre(depth)
        dets = detect_heads(height, valid, self.cfg)
        tracks = self.tracker.update(dets)
        events = self.counter.update(tracks, self.frame_idx)
        return height, dets, tracks, events

    @property
    def totals(self) -> Tuple[int, int]:
        return self.counter.entries, self.counter.exits

#pretty colors
_TRACK_COLORS = [
    (255, 120, 120), (120, 255, 120), (120, 180, 255),
    (255, 200, 100), (220, 120, 255), (120, 255, 220),
]


def draw_overlay(height: np.ndarray, dets: List[Detection], tracks: List[Track],
                 totals: Tuple[int, int], cfg: Config, scale: int = 3,
                 last_event: Optional[CountEvent] = None, frame_idx: Optional[int] = None,
                 timestamp_epoch: Optional[float] = None, initial: int = 0) -> np.ndarray:
    # turns height map into colored image
    vis = np.clip(height.astype(np.float32) / cfg.h_clip_mm, 0, 1)
    vis = (vis * 255).astype(np.uint8)
    # JET colormap turns grayscale into BGR, blue is low red is high
    # Looks like a thermal cam... but it's just larp
    frame = cv2.applyColorMap(vis, cv2.COLORMAP_JET)
    # blow up to 720x540 with NEAREST (no smoothing) so it's blocky
    Wp, Hp = cfg.cols * scale, cfg.rows * scale
    frame = cv2.resize(frame, (Wp, Hp), interpolation=cv2.INTER_NEAREST)

    # draw the zone lines and labels
    ax, bx = cfg.line_a * scale, cfg.line_b * scale
    cv2.line(frame, (ax, 0), (ax, Hp), (255, 255, 255), 1)
    cv2.line(frame, (bx, 0), (bx, Hp), (255, 255, 255), 1)
    cv2.putText(frame, "A (commons)", (10, 20), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (255, 255, 255), 1)
    cv2.putText(frame, "M", ((ax + bx) // 2 - 6, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.putText(frame, "B (upl)", (bx + 10, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    # detect raw detections
    for d in dets:
        px, py = int(d.x * scale), int(d.y * scale)
        cv2.drawMarker(frame, (px, py), (255, 255, 255),
                       cv2.MARKER_CROSS, 14, 2)
        cv2.putText(frame, f"{int(d.peak_h_mm)}", (px + 6, py - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

    # draw the confirmed tracks
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
        # also draw the tail for the last 12 measured positions!
        pts = t.history[-12:]
        for a, b in zip(pts[:-1], pts[1:]):
            cv2.line(frame,
                     (int(a[1] * scale), int(a[0] * scale)),
                     (int(b[1] * scale), int(b[0] * scale)), col, 1)

    # draw bottom status bar with frames and epoch and current time
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