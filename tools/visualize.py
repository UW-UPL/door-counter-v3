'''
TOOL: takes the same input as run.py (a .npz recording) but instead
of printing events it renders an mp4 w the height map colored, 
zone lines drawn, detections / tracks overlaid to better viz the algo.
'''
import argparse
import os
import sys

import cv2
import numpy as np

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

# note we import draw_overlay, the vis function that lives in counter.py
from tof.counter import Config, DoorwayCounter, calibrate_floor, draw_overlay


def render(path, out_path, scale=3, fps=30.0):
    data = np.load(path)
    frames = data["frames"]
    wall_ts = None
    if "wall_timestamps" in data.files:
        ts_arr = data["wall_timestamps"]
        if ts_arr.size == frames.shape[0] and ts_arr.size > 0:
            wall_ts = ts_arr
    # this is for some older recordings (backwards compatability)
    elif "timestamps" in data.files:
        ts_arr = data["timestamps"]
        if (ts_arr.size == frames.shape[0] and ts_arr.size > 0
                and float(ts_arr[0]) > 1e9):
            wall_ts = ts_arr
    cfg = Config()
    print(f"Calibrating floor from {frames.shape[0]} frames...")
    floor = calibrate_floor(frames, cfg)
    dc = DoorwayCounter(floor, cfg)
    # scale up
    W = cfg.cols * scale
    H = cfg.rows * scale + 60
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(out_path, fourcc, fps, (W, H))
    if not vw.isOpened():
        raise RuntimeError(f"Cannot open {out_path} for writing")

    last_event = None
    for i, depth in enumerate(frames):
        # run the pipeline, return height map
        height, dets, tracks, evs = dc.step(depth)
        if evs:
            last_event = evs[-1]
        # pass wall clock timestamp
        ts_epoch = float(wall_ts[i]) if wall_ts is not None else None
        # RENDERING call, all vis. logic lives in draw_overlay
        img = draw_overlay(
            height, dets, tracks, dc.totals, cfg,
            scale=scale, last_event=last_event, frame_idx=i,
            timestamp_epoch=ts_epoch,
        )
        # encode this frame into the MP4
        vw.write(img)

    vw.release()
    ent, ex = dc.totals
    print(f"Wrote {out_path}  ({frames.shape[0]} frames, "
          f"{ent} entries, {ex} exits)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("out")
    ap.add_argument("--scale", type=int, default=3)
    args = ap.parse_args()
    render(args.path, args.out, scale=args.scale)


if __name__ == "__main__":
    main()
