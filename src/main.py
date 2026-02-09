import sys
import threading

from detective import Detective
import ble_scanner
import bt_service
import github_sync

# tof_detector is not integrated yet - it has its own main() currently


def main():
    shutdown_event = threading.Event()
    detective = Detective()

    threads = [
        threading.Thread(target=ble_scanner.main, args=(detective, shutdown_event), name="ble_scanner"),
        threading.Thread(target=bt_service.main, args=(shutdown_event,), name="bt_service"),
        # threading.Thread(target=github_sync.main, args=(shutdown_event,), name="github_sync"),
    ]

    for t in threads:
        t.start()
        print(f"Started {t.name}")

    try:
        while True:
            for t in threads:
                t.join(timeout=1.0)
                if not t.is_alive() and not shutdown_event.is_set():
                    print(f"WARNING: {t.name} died unexpectedly")
    except KeyboardInterrupt:
        print("\nShutting down...")
        shutdown_event.set()

        for t in threads:
            t.join(timeout=10.0)
            if t.is_alive():
                print(f"WARNING: {t.name} did not shut down cleanly")

        print("Shutdown complete")
        sys.exit(0)


if __name__ == "__main__":
    main()
