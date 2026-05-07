# TOOL: records raw depth frames from the ToF camera to a .npz for offline replay
import os
import sys
import time
import threading

import numpy as np

import ArducamDepthCamera as ac


MAX_DISTANCE = 4000
RECORDING_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "recordings",
)


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else None

    cam = ac.ArducamCamera()
    ret = cam.open(ac.Connection.CSI, 0)
    if ret != 0:
        print("Failed to open camera. Error code:", ret)
        return

    ret = cam.start(ac.FrameType.DEPTH)
    if ret != 0:
        print("Failed to start camera. Error code:", ret)
        cam.close()
        return

    cam.setControl(ac.Control.RANGE, MAX_DISTANCE)

    os.makedirs(RECORDING_DIR, exist_ok=True)

    input("Press Enter to START recording...")
    print("Recording... press Enter (or Ctrl+C) to STOP.")

    frames = []
    timestamps = []
    wall_timestamps = []
    stop = threading.Event()

    def wait_for_stop():
        try:
            input()
        except EOFError:
            pass
        stop.set()

    threading.Thread(target=wait_for_stop, daemon=True).start()

    try:
        while not stop.is_set():
            frame = cam.requestFrame(2000)
            if frame is not None and isinstance(frame, ac.DepthData):
                depth_buf = frame.depth_data
                cam.releaseFrame(frame)
                frames.append(depth_buf.copy())
                timestamps.append(time.monotonic())
                wall_timestamps.append(time.time())
    except KeyboardInterrupt:
        pass
    finally:
        cam.stop()
        cam.close()

    if not frames:
        print("No frames captured.")
        return

    ts = time.strftime("%Y%m%d_%H%M%S")
    name = f"{ts}_{label}.npz" if label else f"{ts}.npz"
    path = os.path.join(RECORDING_DIR, name)
    np.savez(
        path,
        frames=np.stack(frames),
        timestamps=np.array(timestamps),
        wall_timestamps=np.array(wall_timestamps),
    )
    duration = timestamps[-1] - timestamps[0]
    print(f"Saved {len(frames)} frames ({duration:.1f}s) to {path}")


if __name__ == "__main__":
    main()
