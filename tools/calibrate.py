# TOOL: captures a floor reference (.npy) for the ToF counter
import argparse
import os
import sys
import time

import numpy as np

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

import ArducamDepthCamera as ac

from tof.counter import Config, calibrate_floor
from tof.camera_loop import _grab, _open_camera

DEFAULT_OUT = os.path.join(_REPO_ROOT, "data", "floor.npy")


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


def main():
    ap = argparse.ArgumentParser(description="Capture floor reference for the ToF counter.")
    ap.add_argument("out", nargs="?", default=DEFAULT_OUT,
                    help=f"output .npy path (default: {DEFAULT_OUT})")
    ap.add_argument("--frames", type=int, default=120)
    args = ap.parse_args()
    cmd_calibrate(args.out, args.frames)


if __name__ == "__main__":
    main()
