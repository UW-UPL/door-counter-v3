import subprocess
import sys
import threading
import time
import argparse

from ble import ble_scanner, bt_service
from display.live_view import LiveView
from services import github_sync, logger
from tof import occupancy


def prepare_bluetooth():
    logger.log("Restarting bluetooth service...")
    subprocess.run(
        ["sudo", "systemctl", "restart", "bluetooth"],
        check=True,
    )
    time.sleep(2)

    logger.log("Warming up adapter with bluetoothctl scan le...")
    subprocess.run(
        ["bluetoothctl", "--timeout", "3", "scan", "le"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main():
    threading.current_thread().name = "main"

    parser = argparse.ArgumentParser(description="UPL Door Counter")
    parser.add_argument("--no-audio", action="store_true",
                        help="Disable sound playback on entry")
    parser.add_argument("--initial-count", type=int, default=0,
                        help="Initial people count (default: 0)")
    args = parser.parse_args()

    prepare_bluetooth()

    shutdown_event = threading.Event()
    detective_holder = [None]
    live_view = LiveView()

    threads = [
        threading.Thread(target=ble_scanner.main, args=(shutdown_event, detective_holder), name="ble_scanner", daemon=True),
        threading.Thread(target=bt_service.main, args=(shutdown_event,), name="bt_service", daemon=True),
        threading.Thread(target=github_sync.main, args=(shutdown_event,), name="github_sync", daemon=True),
        threading.Thread(target=live_view.run, args=(shutdown_event,), name="display", daemon=True),
        threading.Thread(target=occupancy.main, args=(detective_holder, shutdown_event, args, live_view.update_frame), name="occupancy", daemon=True),
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
