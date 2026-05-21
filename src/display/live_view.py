import os
import threading
import time

import cv2
import numpy as np

from display.st7789 import ST7789, WIDTH, HEIGHT
from services import logger


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FLOOR_PATH = os.path.join(_REPO_ROOT, "data", "floor.npy")

H_CLIP_MM = 3000.0
DEPTH_MAX_MM = 4000.0
WAIT_TIMEOUT_S = 0.1

# how often we re-init the panel as a watchdog. ST7789 can drift after days of SPI
# traffic, this resyncs RAMWR + reloads init regs. costs ~250 ms once per hour, one
# dropped frame, counter thread is unaffected
REINIT_INTERVAL_S = 3600.0
# if a display push raises we want to recover, but not spin retrying flat out if the
# panel is genuinely dead. small backoff between recovery attempts
REINIT_BACKOFF_S = 1.0


def _bgr_to_rgb565(bgr: np.ndarray) -> bytes:
    b = bgr[..., 0].astype(np.uint16) >> 3
    g = bgr[..., 1].astype(np.uint16) >> 2
    r = bgr[..., 2].astype(np.uint16) >> 3
    rgb565 = (r << 11) | (g << 5) | b
    return rgb565.byteswap().tobytes()


def _render(depth: np.ndarray, floor: np.ndarray) -> bytes:
    d = depth.astype(np.float32)
    valid = (d > 0) & (d <= DEPTH_MAX_MM)
    height = np.where(valid, floor - d, 0.0)
    height = np.clip(height, 0.0, H_CLIP_MM)
    vis = (height * (255.0 / H_CLIP_MM)).astype(np.uint8)
    bgr = cv2.applyColorMap(vis, cv2.COLORMAP_JET)
    bgr = cv2.resize(bgr, (WIDTH, HEIGHT), interpolation=cv2.INTER_NEAREST)
    return _bgr_to_rgb565(bgr)


class LiveView:
    def __init__(self, floor_path: str = FLOOR_PATH):
        self._floor_path = floor_path
        self._floor = None
        self._lcd = None
        self._latest = None
        self._latest_id = 0
        self._last_render_id = -1
        self._lock = threading.Lock()
        self._new_frame = threading.Event()

    def update_frame(self, depth: np.ndarray):
        try:
            with self._lock:
                self._latest = depth
                self._latest_id += 1
            self._new_frame.set()
        except Exception:
            pass

    def run(self, shutdown_event: threading.Event):
        if not os.path.exists(self._floor_path):
            logger.error(f"floor reference missing: {self._floor_path} -- live view disabled")
            return
        try:
            self._floor = np.load(self._floor_path).astype(np.float32)
        except Exception as e:
            logger.error(f"failed to load floor reference: {e} -- live view disabled")
            return

        try:
            self._lcd = ST7789()
        except Exception as e:
            logger.error(f"display init failed, live view disabled: {e}")
            return

        try:
            self._lcd.clear()
        except Exception:
            pass
        logger.log("Live view started")

        first_frame_logged = False
        # hourly watchdog clock. anchored to whatever time the display thread came up
        last_reinit_at = time.monotonic()
        # set when a display push raises, next loop iteration will try to recover
        # the panel before rendering again
        needs_reinit = False
        try:
            while not shutdown_event.is_set():
                self._new_frame.wait(timeout=WAIT_TIMEOUT_S)
                self._new_frame.clear()

                # hourly preventative re-init. cheap insurance against drift, the
                # counter thread doesn't care if we block the SPI bus for 250 ms
                now = time.monotonic()
                if not needs_reinit and now - last_reinit_at >= REINIT_INTERVAL_S:
                    try:
                        self._lcd.reinit()
                        last_reinit_at = now
                    except Exception as e:
                        logger.error(f"periodic display reinit failed: {e}")
                        needs_reinit = True

                # recovery path. previous push blew up, try to bring the panel back
                # before we attempt another frame. if reinit itself fails, sleep a
                # beat so we dont spin if the hardware is genuinely gone
                if needs_reinit:
                    try:
                        self._lcd.reinit()
                        needs_reinit = False
                        last_reinit_at = now
                        logger.log("display recovered after error")
                    except Exception as e:
                        logger.error(f"display reinit failed: {e}")
                        time.sleep(REINIT_BACKOFF_S)
                        continue

                with self._lock:
                    latest_id = self._latest_id
                    d = self._latest

                if d is not None and latest_id != self._last_render_id:
                    try:
                        self._lcd.display(_render(d, self._floor))
                        self._last_render_id = latest_id
                        if not first_frame_logged:
                            logger.log(f"first frame rendered (shape={d.shape})")
                            first_frame_logged = True
                    except Exception as e:
                        # panel might now be desynced (partial RAMWR write, glitched
                        # cmd byte, etc). flag for recovery on next loop iteration
                        logger.error(f"display push failed: {e}")
                        needs_reinit = True
        finally:
            try:
                self._lcd.close()
            except Exception:
                pass
            logger.log("Live view stopped")
