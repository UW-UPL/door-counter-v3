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

    # Parse tof_detector arguments
    parser = argparse.ArgumentParser(description="UPL Door Counter")
    parser.add_argument("--debug", action="store_true",
                        help="Print every zone reading and state transition")
    parser.add_argument("--no-audio", action="store_true",
                        help="Disable sound playback")
    parser.add_argument("--threshold", type=int, default=80,
                        help="Threshold %% of floor distance (default: 80)")
    parser.add_argument("--status-interval", type=float, default=0,
                        help="Print periodic status every N seconds (0=off)")
    parser.add_argument("--initial-count", type=int, default=0,
                        help="Initial people count (default: 0)")
    parser.add_argument("--manual-threshold", action="store_true",
                        help="Skip auto-calibration and use manual threshold based on mounting height")
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
