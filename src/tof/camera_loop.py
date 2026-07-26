import time
from datetime import datetime, timedelta
import numpy as np

# third party SDK that talks to the actual cam over CSI
import ArducamDepthCamera as ac
from tof.counter import Config, DoorwayCounter

# cam sdk setting in mm tells the sensor to not bother reporting 
# anything further than 4000 mm, our floor is ~2400 mm away
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

'''
Why C-style error code? 
The Arducam SDK uses C-style error code bc 
it is a thin Python wrapper around a C library
this is unidiomatic for Python, but OK here
'''

def _grab(cam):
    # ask the SDK for the next frame. timeout 2000 ms
    # returns None if no frame arrived (eg cam unplugged)
    frame = cam.requestFrame(2000)
    if frame is None or not isinstance(frame, ac.DepthData):
        return None
    depth = frame.depth_data.copy()
    cam.releaseFrame(frame)
    return depth


def _next_reset(now):
    target = now.replace(hour=RESET_HOUR, minute=0, second=0, microsecond=0)
    # if we alr passed 6 AM today, next reset is tmwrw
    if now >= target:
        target += timedelta(days=1)
    return target


def cmd_run(floor_path, initial=0, shutdown_event=None,
            on_event=None, on_reset=None, on_frame=None, recorder=None):
    # load floor reference array from the .npy
    floor = np.load(floor_path)
    cfg = Config() # default cfg (all dataacalss fields from counter.py)
    dc = DoorwayCounter(floor, cfg)
    initial_count = initial

    def occupancy():
        return initial_count + dc.totals[0] - dc.totals[1]

    # open up the hardware
    cam = _open_camera()
    print(f"Counter running. Initial occupancy = {initial_count}. "
          f"Daily reset at {RESET_HOUR:02d}:00. Press Ctrl-C to stop.")

    last_event = None
    pending_ids = set()
    clip_started = set()
    frame_count = 0
    t0 = time.monotonic()
    last_print = t0
    reset_at = _next_reset(datetime.now())

    try:
        while True:
            if shutdown_event is not None and shutdown_event.is_set():
                break
            d = _grab(cam)

            # cam failed to deliver frame in 2 seconds
            # skip and try again, non-fatal
            if d is None:
                continue
            if on_frame is not None:
                try:
                    on_frame(d)
                except Exception:
                    pass
            if recorder is not None:
                try:
                    recorder.on_frame(d)
                except Exception:
                    pass

            # call into counter.py, hands depth in and gets back
            # heightmap, head detections, active tracks and count events.
            height, dets, tracks, evs = dc.step(d)
            if evs:
                last_event = evs[-1]
            for ev in evs:
                if recorder is not None:
                    recorder.trigger(f"{ev.direction}_track{ev.track_id}")
                if on_event is not None:
                    on_event(ev)
                    continue
                ts = time.monotonic() - t0
                print(f"[{ts:6.2f}s] {ev.direction.upper():5s} "
                      f"track#{ev.track_id}  "
                      f"occupancy={occupancy()}  "
                      f"(entries={dc.totals[0]} exits={dc.totals[1]})")

            # start a clip the moment a track is confirmed
            if recorder is not None:
                for t in dc.tracker.tracks:
                    if t.confirmed and t.track_id not in clip_started:
                        clip_started.add(t.track_id)
                        recorder.trigger(f"track{t.track_id}")

            current_ids = {t.track_id for t in dc.tracker.tracks} #Alive track IDS
            incomplete_ids = pending_ids - current_ids #Dead track IDs
            pending_ids = {
                t.track_id for t in dc.tracker.tracks
                if t.confirmed and not t.counted
            }
            clip_started &= current_ids
            # log every incomplete track
            for tid in incomplete_ids:
                ts = time.monotonic() - t0
                print(f"[{ts:6.2f}s] INCOMPLETE track#{tid}  "
                      f"(confirmed head, no entry/exit)")
                if recorder is not None:
                    recorder.trigger(f"incomplete_track{tid}")

            now_dt = datetime.now()
            if now_dt >= reset_at:
                # RESET CONDITION
                # just crossed 6 AM zero it all
                dc.counter.entries = 0
                dc.counter.exits = 0
                initial_count = 0
                last_event = None
                # schedule the next reset
                reset_at = _next_reset(now_dt)
                print(f"[{now_dt:%Y-%m-%d %H:%M:%S}] daily reset "
                      f"-> occupancy=0 (next reset {reset_at:%Y-%m-%d %H:%M})")
                if on_reset is not None:
                    on_reset()

            frame_count += 1
            now = time.monotonic()
            # heartbeat, print stats every 2 seconds
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
