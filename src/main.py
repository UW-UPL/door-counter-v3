import sys
import threading
import argparse

import ble_scanner
import bt_service
import github_sync
import tof_detector
import logger


def main():
    threading.current_thread().name = "main"

    parser = argparse.ArgumentParser(description="UPL Door Counter")
    parser.add_argument("--no-audio", action="store_true",
                        help="Disable sound playback on entry")
    parser.add_argument("--initial-count", type=int, default=0,
                        help="Initial people count (default: 0)")
    parser.add_argument("--floor", default="floor.npy",
                        help="Path to floor reference npy (default: floor.npy)")
    parser.add_argument("--show", action="store_true",
                        help="Open an OpenCV window with the live overlay")
    parser.add_argument("--save-video", default=None,
                        help="Path to an mp4 of the full annotated session")
    parser.add_argument("--save-clips", dest="clips_dir", default=None,
                        help="Directory to save a short mp4+npz for each event")
    parser.add_argument("--count-file", default=None,
                        help="File to keep updated with current occupancy + totals")
    args = parser.parse_args()

    shutdown_event = threading.Event()
    detective_holder = [None]

    threads = [
        threading.Thread(target=ble_scanner.main, args=(shutdown_event, detective_holder), name="ble_scanner"),
        threading.Thread(target=bt_service.main, args=(shutdown_event,), name="bt_service"),
        threading.Thread(target=github_sync.main, args=(shutdown_event,), name="github_sync"),
        threading.Thread(target=tof_detector.main, args=(detective_holder, shutdown_event, args), name="tof_detector"),
    ]

    for t in threads:
        t.start()
        logger.log(f"Started {t.name}")

    try:
        while True:
            for t in threads:
                t.join(timeout=1.0)
                if not t.is_alive() and not shutdown_event.is_set():
                    logger.warn(f"{t.name} died unexpectedly")
    except KeyboardInterrupt:
        logger.log("Shutting down...")
        shutdown_event.set()

        for t in threads:
            t.join(timeout=10.0)
            if t.is_alive():
                logger.warn(f"{t.name} did not shut down cleanly")

        logger.log("Shutdown complete")
        sys.exit(0)


if __name__ == "__main__":
    main()
