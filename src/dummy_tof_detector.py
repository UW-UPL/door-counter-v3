import sys
import select
import threading
import time
import logger


def main(detective_holder, shutdown_event: threading.Event, args=None):
    logger.log("Dummy ToF detector started (keyboard mode)")
    logger.log("  'e' + Enter = ENTRY, 'x' + Enter = EXIT")

    # Wait for detective to be initialized
    while detective_holder[0] is None and not shutdown_event.is_set():
        time.sleep(0.1)

    if shutdown_event.is_set():
        return

    detective = detective_holder[0]

    people_count = 0
    if args is not None and hasattr(args, "initial_count"):
        people_count = args.initial_count

    while not shutdown_event.is_set():
        ready, _, _ = select.select([sys.stdin], [], [], 1.0)
        if not ready:
            continue
        line = sys.stdin.readline().strip().lower()
        if line == "e":
            people_count += 1
            device = detective.enter()
            if device:
                logger.log(f">>> ENTRY detected! Room count: {people_count} | Device: {device.name}")
            else:
                logger.log(f">>> ENTRY detected! Room count: {people_count} | No device matched")
        elif line == "x":
            people_count = max(0, people_count - 1)
            detective.exit()
            logger.log(f"<<< EXIT detected! Room count: {people_count}")

    logger.log(f"Dummy ToF detector stopped. Final count: {people_count}")
