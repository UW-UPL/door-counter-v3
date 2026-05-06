import os
import threading
import time

import live
import logger
from audio_player import AudioPlayer

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOOR_PATH = os.path.join(_REPO_ROOT, "floor.npy")
# COUNT_FILE = os.path.join(_REPO_ROOT, "count.txt")
# CLIPS_DIR = os.path.join(_REPO_ROOT, "recordings")


def main(detective_holder, shutdown_event: threading.Event, args=None):
    while detective_holder[0] is None and not shutdown_event.is_set():
        time.sleep(0.1)
    if shutdown_event.is_set():
        return
    detective = detective_holder[0]

    initial = getattr(args, "initial_count", 0)
    no_audio = getattr(args, "no_audio", False)

    if not os.path.exists(FLOOR_PATH):
        logger.error(f"floor reference not found: {FLOOR_PATH}")
        logger.error("run `python src/live.py calibrate floor.npy` first")
        shutdown_event.set()
        return

    audio_player = None
    if not no_audio:
        try:
            audio_player = AudioPlayer(
                default_sounds_dir=os.path.join(_REPO_ROOT, "sounds", "default"),
                custom_sounds_dir=os.path.join(_REPO_ROOT, "sounds", "custom"),
            )
        except Exception as e:
            logger.error(f"audio init failed: {e}")

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
            if audio_player is not None:
                d = None
                if device is not None:
                    d = {"name": device.name, "sound_file": device.sound_file}
                audio_player.play_entry(d)
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
        live.cmd_run(
            floor_path=FLOOR_PATH,
            initial=initial,
            # count_file=COUNT_FILE,
            # clips_dir=CLIPS_DIR,
            audio=False,
            shutdown_event=shutdown_event,
            on_event=on_event,
            on_reset=on_reset,
        )
    except Exception as e:
        logger.error(f"tof_detector crashed: {e}")
        shutdown_event.set()
    finally:
        if audio_player is not None:
            try:
                audio_player.stop()
            except Exception:
                pass
        logger.log(f"ToF detector stopped. Final count: {people_count}")
