'''
TOOL: renders the counter pipeline in the terminal (truecolor half
blocks, works over ssh). Same visuals as visualize.py, both use
draw_overlay from counter.py. Two modes:

  live:    sudo .venv/bin/python3 tools/liveview.py
           (stop door-counter.service first, the camera is exclusive,
            and start it again when you are done!)
  replay:  .venv/bin/python3 tools/liveview.py recordings/clips/<clip>.npz
           (safe anytime, does not touch the camera)
'''
import argparse
import os
import shutil
import sys
import time

import cv2
import numpy as np

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

from tof.counter import Config, DoorwayCounter, calibrate_floor, draw_overlay

FLOOR_PATH = os.path.join(_REPO_ROOT, "data", "floor.npy")
MAX_DISTANCE = 4000


def to_ansi(img, cols):
    # downsample a BGR image to terminal cells, two pixels per char
    # via the upper-half-block: fg colors the top px, bg the bottom
    h, w = img.shape[:2]
    rows = max(2, (cols * h // w) & ~1)
    small = cv2.resize(img, (cols, rows), interpolation=cv2.INTER_AREA)
    lines = []
    for y in range(0, rows, 2):
        top, bot = small[y], small[y + 1]
        cells = []
        for x in range(cols):
            tb, tg, tr = (int(v) for v in top[x])
            bb, bg, br = (int(v) for v in bot[x])
            cells.append(f"\x1b[38;2;{tr};{tg};{tb};48;2;{br};{bg};{bb}m▀")
        lines.append("".join(cells) + "\x1b[0m\x1b[K")
    return lines


def fit_cols(img_w, img_h):
    ts = shutil.get_terminal_size()
    by_width = ts.columns - 2
    by_height = (ts.lines - 4) * 2 * img_w // max(1, img_h)
    return max(20, min(110, by_width, by_height))


def show(dc, cfg, frames_iter, pace_fps=None):
    last_event = None
    t_render = 0.0
    sys.stdout.write("\x1b[2J")
    for i, depth in frames_iter:
        height, dets, tracks, evs = dc.step(depth)
        if evs:
            last_event = evs[-1]
        now = time.monotonic()
        # live mode: step the pipeline on every frame so tracking stays
        # correct, but only redraw ~10x a second
        if pace_fps is None and now - t_render < 0.1:
            continue
        t_render = now
        img = draw_overlay(height, dets, tracks, dc.totals, cfg,
                           scale=2, last_event=last_event, frame_idx=i)
        img = img[:cfg.rows * 2]  # crop the baked-in status bar
        lines = to_ansi(img, fit_cols(img.shape[1], img.shape[0]))
        ent, ex = dc.totals
        ev = (f"{last_event.direction} track{last_event.track_id}"
              if last_event else "none yet")
        sys.stdout.write(
            "\x1b[H" + "\n".join(lines) +
            f"\n\x1b[0mentries={ent} exits={ex} "
            f"occupancy(since start)={max(0, ent - ex)}  "
            f"last={ev}  frame={i}  Ctrl-C quits\x1b[K\n\x1b[J")
        sys.stdout.flush()
        if pace_fps:
            time.sleep(1.0 / pace_fps)


def live():
    import ArducamDepthCamera as ac

    floor = np.load(FLOOR_PATH)
    cfg = Config()
    dc = DoorwayCounter(floor, cfg)

    cam = ac.ArducamCamera()
    if cam.open(ac.Connection.CSI, 0) != 0:
        raise SystemExit("camera open failed. is the service holding it? "
                         "run: sudo systemctl stop door-counter")
    if cam.start(ac.FrameType.DEPTH) != 0:
        cam.close()
        raise SystemExit("camera start failed")
    cam.setControl(ac.Control.RANGE, MAX_DISTANCE)

    def frames():
        i = 0
        while True:
            f = cam.requestFrame(2000)
            if f is None or not isinstance(f, ac.DepthData):
                continue
            d = f.depth_data.copy()
            cam.releaseFrame(f)
            yield i, d
            i += 1

    try:
        show(dc, cfg, frames())
    except KeyboardInterrupt:
        pass
    finally:
        cam.stop()
        cam.close()
        print("\ncamera released. bring the counter back with: "
              "sudo systemctl start door-counter")


def replay(path, fps):
    data = np.load(path)
    frames = data["frames"]
    label = str(data["label"]) if "label" in data.files else ""
    cfg = Config()
    # same move as replay.py / visualize.py: build the floor from the
    # clip itself, the median sees mostly empty doorway
    floor = calibrate_floor(frames, cfg)
    dc = DoorwayCounter(floor, cfg)
    print(f"{os.path.basename(path)}  {label}  ({frames.shape[0]} frames)")
    try:
        show(dc, cfg, enumerate(frames), pace_fps=fps)
    except KeyboardInterrupt:
        pass
    ent, ex = dc.totals
    print(f"\ndone: {ent} entries, {ex} exits")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=None,
                    help=".npz clip to replay (omit for live camera)")
    ap.add_argument("--fps", type=float, default=15.0,
                    help="replay speed (clips are recorded at 15 fps)")
    args = ap.parse_args()
    if args.path:
        replay(args.path, args.fps)
    else:
        live()


if __name__ == "__main__":
    main()
