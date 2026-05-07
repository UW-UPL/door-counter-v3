import time
from datetime import datetime, timedelta

import numpy as np
import ArducamDepthCamera as ac

from tof.counter import Config, DoorwayCounter

MAX_DISTANCE = 4000
RESET_HOUR = 6


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


def _next_reset(now):
    target = now.replace(hour=RESET_HOUR, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target


def cmd_run(floor_path, show=False, save_video=None, scale=3,
            initial=0, count_file=None, clips_dir=None,
            audio=True, shutdown_event=None, on_event=None,
            on_reset=None):
    floor = np.load(floor_path)
    cfg = Config()
    dc = DoorwayCounter(floor, cfg)
    initial_count = initial

    def occupancy():
        return initial_count + dc.totals[0] - dc.totals[1]

    cam = _open_camera()
    print(f"Counter running. Initial occupancy = {initial_count}. "
          f"Daily reset at {RESET_HOUR:02d}:00. Press Ctrl-C to stop.")

    last_event = None
    pending_ids = set()
    frame_count = 0
    t0 = time.monotonic()
    last_print = t0
    reset_at = _next_reset(datetime.now())

    try:
        while True:
            if shutdown_event is not None and shutdown_event.is_set():
                break
            d = _grab(cam)
            if d is None:
                continue
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
        print()
        print(f"FINAL: entries={dc.totals[0]} exits={dc.totals[1]} "
              f"occupancy={occupancy()}")
