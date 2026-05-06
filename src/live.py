import argparse
import os
import queue
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Optional

import cv2
import numpy as np

import ArducamDepthCamera as ac

from counter import (
    Config,
    CountEvent,
    DoorwayCounter,
    calibrate_floor,
    draw_overlay,
)


MAX_DISTANCE = 4000
RESET_HOUR = 6
CLIP_PRE_FRAMES = 90
CLIP_POST_FRAMES = 90

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY_SOUND = os.path.join(_REPO_ROOT, "sounds", "custom", "oliver.wav")


def init_audio():
    try:
        # import pygame
        # pygame.mixer.init()
        # return pygame
        return None
    except Exception as e:
        print(f"audio init failed: {e}")
        return None


def play_sound(pg, path):
    if pg is None:
        return
    try:
        # pg.mixer.music.load(path)
        # pg.mixer.music.play()
        pass
    except Exception as e:
        print(f"could not play {path}: {e}")


class ClipWriter:
    def __init__(self, floor, floor_path_abs, fps=30.0):
        self.floor = floor
        self.floor_path_abs = floor_path_abs
        self.fps = fps
        self.fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self.q = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def open_clip(self, clip_path, npz_path, pre_imgs, pre_depths, pre_ts,
                  label, tid):
        self.q.put(("open", clip_path, npz_path,
                    pre_imgs, pre_depths, pre_ts, label, tid))

    def stream_frame(self, img, depth, ts):
        self.q.put(("frame", img, depth, ts))

    def add_trigger(self, label, tid):
        self.q.put(("trigger", label, tid))

    def close_clip(self):
        self.q.put(("close",))

    def shutdown(self):
        self.q.put(("close",))
        self.q.put(None)
        self._thread.join(timeout=30)

    def _finalize(self, writer, path, npz_path, raw_frames, raw_ts, triggers_log):
        writer.release()
        print(f"  closed clip: {path}", flush=True)
        if not raw_frames or not npz_path:
            return
        try:
            np.savez_compressed(
                npz_path,
                frames=np.stack(raw_frames, axis=0),
                wall_timestamps=np.array(raw_ts, dtype=np.float64),
                floor=self.floor,
                floor_path=self.floor_path_abs,
                triggers=np.array(
                    triggers_log,
                    dtype=[("frame", "i4"), ("label", "U12"),
                           ("track_id", "i4")],
                ),
            )
            print(f"  saved raw depth: {npz_path} "
                  f"({len(raw_frames)} frames)", flush=True)
        except Exception as e:
            print(f"  ! failed saving raw depth {npz_path}: {e}", flush=True)

    def _run(self):
        writer = None
        path = None
        npz_path = None
        raw_frames = []
        raw_ts = []
        triggers_log = []
        while True:
            msg = self.q.get()
            if msg is None:
                return
            tag = msg[0]
            try:
                if tag == "open":
                    if writer is not None:
                        self._finalize(writer, path, npz_path,
                                       raw_frames, raw_ts, triggers_log)
                        writer = None
                    (_, path, npz_path, pre_imgs, pre_depths, pre_ts,
                     label, tid) = msg
                    if not pre_imgs:
                        continue
                    Hpx, Wpx = pre_imgs[0].shape[:2]
                    w = cv2.VideoWriter(path, self.fourcc, self.fps, (Wpx, Hpx))
                    if not w.isOpened():
                        print(f"  ! could not open clip {path}", flush=True)
                        writer = None
                        continue
                    for f in pre_imgs:
                        w.write(f)
                    writer = w
                    raw_frames = list(pre_depths)
                    raw_ts = list(pre_ts)
                    triggers_log = [(len(raw_frames) - 1, label, tid)]
                    print(f"  recording clip -> {path}", flush=True)
                elif tag == "frame":
                    if writer is None:
                        continue
                    _, img, depth, ts = msg
                    writer.write(img)
                    raw_frames.append(depth)
                    raw_ts.append(ts)
                elif tag == "trigger":
                    if writer is None:
                        continue
                    _, label, tid = msg
                    triggers_log.append((len(raw_frames) - 1, label, tid))
                elif tag == "close":
                    if writer is None:
                        continue
                    self._finalize(writer, path, npz_path,
                                   raw_frames, raw_ts, triggers_log)
                    writer = None
                    path = None
                    npz_path = None
                    raw_frames = []
                    raw_ts = []
                    triggers_log = []
            except Exception as e:
                print(f"  ! clip worker error on {tag}: {e}", flush=True)


def _open_camera():
    cam = ac.ArducamCamera()
    if cam.open(ac.Connection.CSI, 0) != 0:
        raise RuntimeError("camera open failed")
    if cam.start(ac.FrameType.DEPTH) != 0:
        cam.close()
        raise RuntimeError("camera start failed")
    cam.setControl(ac.Control.RANGE, MAX_DISTANCE)
    return cam


def _grab(cam):
    frame = cam.requestFrame(2000)
    if frame is None or not isinstance(frame, ac.DepthData):
        return None
    depth = frame.depth_data.copy()
    cam.releaseFrame(frame)
    return depth


def cmd_calibrate(out_path, n_frames):
    cam = _open_camera()
    print(f"Clear the doorway. Capturing {n_frames} frames for floor calibration...")
    time.sleep(3)
    frames = []
    try:
        while len(frames) < n_frames:
            d = _grab(cam)
            if d is not None:
                frames.append(d)
                if len(frames) % 20 == 0:
                    print(f"  {len(frames)}/{n_frames}")
    finally:
        cam.stop()
        cam.close()

    stacked = np.stack(frames, axis=0)
    cfg = Config()
    floor = calibrate_floor(stacked, cfg)
    np.save(out_path, floor)
    print(f"Saved floor reference to {out_path} "
          f"(median={np.median(floor):.0f} mm)")


def _next_reset(now):
    target = now.replace(hour=RESET_HOUR, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target


def _write_count_file(path, occupancy, entries, exits, initial):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(f"count={occupancy}\n")
        f.write(f"initial={initial}\n")
        f.write(f"entries={entries}\n")
        f.write(f"exits={exits}\n")
        f.write(f"updated={datetime.now().isoformat(timespec='seconds')}\n")
    os.replace(tmp, path)


def cmd_run(floor_path, show=False, save_video=None, scale=3,
            initial=0, count_file=None, clips_dir=None,
            audio=True, shutdown_event=None, on_event=None,
            on_reset=None):
    floor = np.load(floor_path)
    floor_path_abs = os.path.abspath(floor_path)
    cfg = Config()
    dc = DoorwayCounter(floor, cfg)
    initial_count = initial

    def occupancy():
        return initial_count + dc.totals[0] - dc.totals[1]

    pg = init_audio() if audio else None

    if clips_dir:
        os.makedirs(clips_dir, exist_ok=True)

    vw = None
    if save_video:
        W = cfg.cols * scale
        H = cfg.rows * scale + 60
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        vw = cv2.VideoWriter(save_video, fourcc, 30.0, (W, H))
        if not vw.isOpened():
            raise RuntimeError(f"cannot open {save_video}")
        print(f"Saving annotated video -> {save_video}")

    pre_ring = deque(maxlen=CLIP_PRE_FRAMES)
    depth_ring = deque(maxlen=CLIP_PRE_FRAMES)
    ts_ring = deque(maxlen=CLIP_PRE_FRAMES)

    clip_sink = None
    if clips_dir is not None:
        clip_sink = ClipWriter(floor, floor_path_abs)
    clip_active = False
    clip_remaining = 0

    cam = _open_camera()
    print(f"Counter running. Initial occupancy = {initial_count}. "
          f"Daily reset at {RESET_HOUR:02d}:00. Press Ctrl-C to stop"
          + (" (or q in the window)" if show else "") + ".")
    if show:
        cv2.namedWindow("doorway", cv2.WINDOW_NORMAL)

    if count_file:
        _write_count_file(count_file, occupancy(),
                          dc.totals[0], dc.totals[1], initial_count)

    last_event = None
    pending_ids = set()
    frame_count = 0
    t0 = time.monotonic()
    last_print = t0
    last_count_write = t0
    reset_at = _next_reset(datetime.now())

    try:
        while True:
            if shutdown_event is not None and shutdown_event.is_set():
                break
            d = _grab(cam)
            if d is None:
                continue
            now_epoch = time.time()
            height, dets, tracks, evs = dc.step(d)
            if evs:
                last_event = evs[-1]
            for ev in evs:
                if on_event is not None:
                    on_event(ev)
                    continue
                ts = time.monotonic() - t0
                print(f"[{ts:6.2f}s] {ev.direction.upper():5s} "
                      f"track#{ev.track_id}  "
                      f"occupancy={occupancy()}  "
                      f"(entries={dc.totals[0]} exits={dc.totals[1]})")
                if ev.direction == "entry" and pg is not None:
                    play_sound(pg, ENTRY_SOUND)

            current_ids = {t.track_id for t in dc.tracker.tracks}
            incomplete_ids = pending_ids - current_ids
            pending_ids = {
                t.track_id for t in dc.tracker.tracks
                if t.confirmed and not t.counted
            }
            for tid in incomplete_ids:
                ts = time.monotonic() - t0
                print(f"[{ts:6.2f}s] INCOMPLETE track#{tid}  "
                      f"(confirmed head, no entry/exit)")

            now_dt = datetime.now()
            if now_dt >= reset_at:
                dc.counter.entries = 0
                dc.counter.exits = 0
                initial_count = 0
                last_event = None
                reset_at = _next_reset(now_dt)
                print(f"[{now_dt:%Y-%m-%d %H:%M:%S}] daily reset "
                      f"-> occupancy=0 (next reset {reset_at:%Y-%m-%d %H:%M})")
                if on_reset is not None:
                    on_reset()
                if count_file:
                    _write_count_file(count_file, 0, 0, 0, 0)

            need_render = show or (vw is not None) or (clips_dir is not None)
            img = None
            if need_render:
                img = draw_overlay(
                    height, dets, tracks, dc.totals, cfg,
                    scale=scale, last_event=last_event,
                    frame_idx=frame_count,
                    timestamp_epoch=now_epoch,
                    initial=initial_count,
                )
                if vw is not None:
                    vw.write(img)
                if show:
                    cv2.imshow("doorway", img)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break

            triggers = [(ev.direction, ev.track_id) for ev in evs]
            triggers.extend(("incomplete", tid) for tid in incomplete_ids)
            if clip_sink is not None and img is not None:
                depth_copy = d.copy()
                pre_ring.append(img)
                depth_ring.append(depth_copy)
                ts_ring.append(now_epoch)
                if clip_active:
                    clip_sink.stream_frame(img, depth_copy, now_epoch)
                    clip_remaining -= 1
                if triggers:
                    if not clip_active:
                        label, tid = triggers[0]
                        ts_name = datetime.now().strftime("%Y%m%d_%H%M%S")
                        base = f"{ts_name}_{label}_track{tid}"
                        clip_sink.open_clip(
                            os.path.join(clips_dir, f"{base}.mp4"),
                            os.path.join(clips_dir, f"{base}.npz"),
                            list(pre_ring), list(depth_ring), list(ts_ring),
                            label, tid,
                        )
                        clip_active = True
                        clip_remaining = CLIP_POST_FRAMES
                        for label, tid in triggers[1:]:
                            clip_sink.add_trigger(label, tid)
                    else:
                        clip_remaining = CLIP_POST_FRAMES
                        for label, tid in triggers:
                            clip_sink.add_trigger(label, tid)
                if clip_active and clip_remaining <= 0:
                    clip_sink.close_clip()
                    clip_active = False

            if count_file:
                now = time.monotonic()
                if evs or (now - last_count_write > 10.0):
                    _write_count_file(count_file, occupancy(),
                                      dc.totals[0], dc.totals[1],
                                      initial_count)
                    last_count_write = now

            frame_count += 1
            now = time.monotonic()
            if now - last_print > 2.0:
                fps = frame_count / (now - t0)
                conf = sum(1 for t in tracks if t.confirmed)
                print(f"  .. {fps:5.1f} FPS  confirmed_tracks={conf}  "
                      f"occupancy={occupancy()}  "
                      f"entries={dc.totals[0]} exits={dc.totals[1]}")
                last_print = now
    except KeyboardInterrupt:
        pass
    finally:
        cam.stop()
        cam.close()
        if vw is not None:
            vw.release()
        if clip_sink is not None:
            clip_sink.shutdown()
        if show:
            cv2.destroyAllWindows()
        if count_file:
            _write_count_file(count_file, occupancy(),
                              dc.totals[0], dc.totals[1], initial_count)
        print()
        print(f"FINAL: entries={dc.totals[0]} exits={dc.totals[1]} "
              f"occupancy={occupancy()}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("calibrate")
    c.add_argument("out")
    c.add_argument("--frames", type=int, default=120)
    r = sub.add_parser("run")
    r.add_argument("floor")
    r.add_argument("--show", action="store_true")
    r.add_argument("--save-video", default=None)
    r.add_argument("--scale", type=int, default=3)
    r.add_argument("--initial", type=int, default=0)
    r.add_argument("--count-file", default=None)
    r.add_argument("--save-clips", dest="clips_dir", default=None)
    r.add_argument("--no-audio", action="store_true")
    args = ap.parse_args()
    if args.cmd == "calibrate":
        cmd_calibrate(args.out, args.frames)
    else:
        cmd_run(args.floor, show=args.show,
                save_video=args.save_video, scale=args.scale,
                initial=args.initial,
                count_file=args.count_file,
                clips_dir=args.clips_dir,
                audio=not args.no_audio)


if __name__ == "__main__":
    main()
