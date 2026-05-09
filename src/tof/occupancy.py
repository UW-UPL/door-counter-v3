import os
import threading
import time

from services import logger
# from services.audio_player import AudioPlayer
from tof import camera_loop

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FLOOR_PATH = os.path.join(_REPO_ROOT, "data", "floor.npy")
# COUNT_FILE = os.path.join(_REPO_ROOT, "count.txt")
# CLIPS_DIR = os.path.join(_REPO_ROOT, "recordings")


def main(detective_holder, shutdown_event: threading.Event, args=None, on_frame=None):
    while detective_holder[0] is None and not shutdown_event.is_set():
        time.sleep(0.1)
    if shutdown_event.is_set():
        return
    detective = detective_holder[0]

    initial = getattr(args, "initial_count", 0)
    # no_audio = getattr(args, "no_audio", False)

    if not os.path.exists(FLOOR_PATH):
        logger.error(f"floor reference not found: {FLOOR_PATH}")
        logger.error("run `python tools/calibrate.py` first")
        shutdown_event.set()
        return

    # audio_player = None
    # if not no_audio:
    #     try:
    #         audio_player = AudioPlayer(
    #             default_sounds_dir=os.path.join(_REPO_ROOT, "sounds", "default"),
    #             custom_sounds_dir=os.path.join(_REPO_ROOT, "sounds", "custom"),
    #         )
    #     except Exception as e:
    #         logger.error(f"audio init failed: {e}")

    people_count = initial
    for _ in range(initial):
        detective.enter()

    def on_event(ev):
        nonlocal people_count
        if ev.direction == "entry":
            people_count += 1
            device = detective.enter()
            if device is not None:
                logger.log(f">>> ENTRY detected! Room count: {people_count} | Device: {device.name}")
            else:
                logger.log(f">>> ENTRY detected! Room count: {people_count} | No device matched")
            # if audio_player is not None:
            #     d = None
            #     if device is not None:
            #         d = {"name": device.name, "sound_file": device.sound_file}
            #     audio_player.play_entry(d)
        else:
            people_count = max(0, people_count - 1)
            detective.exit()
            logger.log(f"<<< EXIT detected! Room count: {people_count}")

    def on_reset():
        nonlocal people_count
        people_count = 0
        detective.reset()
        logger.log("=== Daily reset! Room count: 0")

    try:
        camera_loop.cmd_run(
            floor_path=FLOOR_PATH,
            initial=initial,
            shutdown_event=shutdown_event,
            on_event=on_event,
            on_reset=on_reset,
            on_frame=on_frame,
        )
    except Exception as e:
        logger.error(f"occupancy thread crashed: {e}")
        shutdown_event.set()
    finally:
        # if audio_player is not None:
        #     try:
        #         audio_player.stop()
        #     except Exception:
        #         pass
        logger.log(f"Occupancy tracker stopped. Final count: {people_count}")
