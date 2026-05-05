import os
import threading
import time

import live
import logger
from audio_player import AudioPlayer

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(detective_holder, shutdown_event: threading.Event, args=None):
    while detective_holder[0] is None and not shutdown_event.is_set():
        time.sleep(0.1)
    if shutdown_event.is_set():
        return
    detective = detective_holder[0]

    floor = getattr(args, "floor", None) or os.path.join(_REPO_ROOT, "floor.npy")
    if not os.path.isabs(floor):
        floor = os.path.join(_REPO_ROOT, floor)

    initial = getattr(args, "initial_count", 0)
    show = getattr(args, "show", False)
    save_video = getattr(args, "save_video", None)
    clips_dir = getattr(args, "clips_dir", None)
    count_file = getattr(args, "count_file", None)
    no_audio = getattr(args, "no_audio", False)

    if not os.path.exists(floor):
        logger.error(f"floor reference not found: {floor}")
        logger.error("run `python live.py calibrate floor.npy` first")
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

    try:
        live.cmd_run(
            floor_path=floor,
            show=show,
            save_video=save_video,
            initial=initial,
            count_file=count_file,
            clips_dir=clips_dir,
            audio=False,
            shutdown_event=shutdown_event,
            on_event=on_event,
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
