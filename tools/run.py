# TOOL: replays a .npz through counter and reports per-frame timing
import argparse
import os
import sys
import time

import numpy as np

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

from tof.counter import Config, DoorwayCounter, calibrate_floor


def run(path, verbose=False):
    data = np.load(path)
    frames = data["frames"]
    ts = data.get("timestamps")
    print(f"Loaded {os.path.basename(path)}: {frames.shape[0]} frames "
          f"@ {frames.shape[1]}x{frames.shape[2]}")

    cfg = Config()
    t0 = time.perf_counter()
    floor = calibrate_floor(frames, cfg)
    t_cal = time.perf_counter() - t0
    print(f"Floor calibration: {t_cal*1000:.0f} ms "
          f"(median={np.median(floor):.0f} mm, min={floor.min():.0f}, max={floor.max():.0f})")

    dc = DoorwayCounter(floor, cfg)
    per_frame_ms = []
    events = []
    for i, depth in enumerate(frames):
        t0 = time.perf_counter()
        _, dets, tracks, evs = dc.step(depth)
        per_frame_ms.append((time.perf_counter() - t0) * 1000)
        events.extend(evs)
        if verbose and dets:
            det_str = ", ".join(
                f"(y={d.y:.0f},x={d.x:.0f},h={d.peak_h_mm:.0f})" for d in dets
            )
            conf_tracks = [t for t in tracks if t.confirmed]
            print(f"  f{i:3d}: {len(dets)} det [{det_str}] "
                  f"{len(conf_tracks)} confirmed tracks")
        for ev in evs:
            when = f"t={ts[i] - ts[0]:5.2f}s" if ts is not None else f"frame={i}"
            print(f"  * {when}  {ev.direction.upper():5s} track#{ev.track_id}")

    entries, exits = dc.totals
    print()
    print(f"TOTAL: {entries} entries, {exits} exits  "
          f"(occupancy delta = {entries - exits:+d})")
    print(f"Compute: mean {np.mean(per_frame_ms):.2f} ms/frame, "
          f"p95 {np.percentile(per_frame_ms, 95):.2f} ms, "
          f"max {np.max(per_frame_ms):.2f} ms")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    run(args.path, args.verbose)


if __name__ == "__main__":
    main()
