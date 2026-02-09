import sys
import threading

import ble_scanner
import bt_service
import github_sync
import tof_detector
import logger


def main():
    threading.current_thread().name = "main"

    shutdown_event = threading.Event()
    detective_holder = [None]

    threads = [
        threading.Thread(target=ble_scanner.main, args=(shutdown_event, detective_holder), name="ble_scanner"),
        threading.Thread(target=bt_service.main, args=(shutdown_event,), name="bt_service"),
        threading.Thread(target=github_sync.main, args=(shutdown_event,), name="github_sync"),
        threading.Thread(target=tof_detector.main, args=(detective_holder, shutdown_event), name="tof_detector"),
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
