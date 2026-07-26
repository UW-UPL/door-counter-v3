# Saves short depth clips around detections so we can go back and
# watch the last motion the camera saw. Same .npz format as
# tools/record.py, so tools/replay.py and visualize.py can open them
#
# Threading rules (the old recorder died for breaking these):
# the camera thread only appends copies to a ring buffer and hands
# finished clips to a bounded queue. A separate writer thread does
# the compression and disk IO. If the queue is full the clip is
# dropped, the tracker is never blocked.
import os
import queue
import threading
import time
from collections import deque

import numpy as np

from services import logger


class ClipRecorder:
    def __init__(self, out_dir, keep=30, pre_s=3.0, post_s=3.0,
                 stride=2, fps=30.0, max_s=20.0):
        self.out_dir = out_dir
        self.keep = keep
        self.stride = stride # keep every Nth frame, 2 -> 15 fps clips
        kept_fps = fps / stride
        self._pre = max(1, int(pre_s * kept_fps))
        self._post = max(1, int(post_s * kept_fps))
        # hard length cap so back-to-back triggers can't grow a clip
        # (and its memory) without bound
        self._max = max(self._pre + self._post, int(max_s * kept_fps))
        self._ring = deque(maxlen=self._pre)
        self._frame_i = 0
        self._active = None  # (label, items, frames_left)
        self._q = queue.Queue(maxsize=4)
        os.makedirs(out_dir, exist_ok=True)
        threading.Thread(target=self._writer, name="clip_writer",
                         daemon=True).start()

    # camera thread side

    def on_frame(self, depth):
        self._frame_i += 1
        if self._frame_i % self.stride:
            return
        item = (time.monotonic(), time.time(),
                np.clip(depth, 0, 65535).astype(np.uint16))
        self._ring.append(item)
        if self._active is None:
            return
        label, items, left = self._active
        items.append(item)
        if left > 1 and len(items) < self._max:
            self._active = (label, items, left - 1)
            return
        self._active = None
        try:
            self._q.put_nowait((label, items))
        except queue.Full:
            logger.warn(f"clip queue full, dropped clip {label}")

    def trigger(self, label):
        # already recording? adopt the newer label (events are more
        # specific than a bare track confirm) and extend the tail
        if self._active is not None:
            _, items, _ = self._active
            self._active = (label, items, self._post)
        else:
            self._active = (label, list(self._ring), self._post)

    # writer thread side

    def _writer(self):
        while True:
            label, items = self._q.get()
            try:
                self._save(label, items)
            except Exception as e:
                logger.error(f"clip save failed: {e}")

    def _save(self, label, items):
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.out_dir, f"{ts}_{label}.npz")
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            np.savez_compressed(
                fh,
                frames=np.stack([f for _, _, f in items]),
                timestamps=np.array([t for t, _, _ in items]),
                wall_timestamps=np.array([w for _, w, _ in items]),
                label=np.array(label),
            )
        os.replace(tmp, path)
        logger.log(f"saved clip {os.path.basename(path)} ({len(items)} frames)")
        self._prune()

    def _prune(self):
        clips = sorted(f for f in os.listdir(self.out_dir)
                       if f.endswith(".npz"))
        for old in clips[:-self.keep]:
            try:
                os.remove(os.path.join(self.out_dir, old))
            except OSError:
                pass
