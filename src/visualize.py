import argparse

import cv2
import numpy as np

from counter import Config, DoorwayCounter, calibrate_floor, draw_overlay


def render(path, out_path, scale=3, fps=30.0):
    data = np.load(path)
    frames = data["frames"]
    wall_ts = None
    if "wall_timestamps" in data.files:
        ts_arr = data["wall_timestamps"]
        if ts_arr.size == frames.shape[0] and ts_arr.size > 0:
            wall_ts = ts_arr
    elif "timestamps" in data.files:
        ts_arr = data["timestamps"]
        if (ts_arr.size == frames.shape[0] and ts_arr.size > 0
                and float(ts_arr[0]) > 1e9):
            wall_ts = ts_arr
    cfg = Config()
    print(f"Calibrating floor from {frames.shape[0]} frames...")
    floor = calibrate_floor(frames, cfg)
    dc = DoorwayCounter(floor, cfg)

    W = cfg.cols * scale
    H = cfg.rows * scale + 60
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(out_path, fourcc, fps, (W, H))
    if not vw.isOpened():
        raise RuntimeError(f"Cannot open {out_path} for writing")

    last_event = None
    for i, depth in enumerate(frames):
        height, dets, tracks, evs = dc.step(depth)
        if evs:
            last_event = evs[-1]
        ts_epoch = float(wall_ts[i]) if wall_ts is not None else None
        img = draw_overlay(
            height, dets, tracks, dc.totals, cfg,
            scale=scale, last_event=last_event, frame_idx=i,
            timestamp_epoch=ts_epoch,
        )
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
