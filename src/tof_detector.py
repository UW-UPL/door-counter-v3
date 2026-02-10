# Prereqs: pip3 install adafruit-circuitpython-vl53l1x pygame

import time
import argparse
import board
import adafruit_vl53l1x
import pygame
from collections import deque
import threading
import logger

from detective import Detective

# Config
DISTANCE_MODE = 2
TIMING_BUDGET_MS = 33 # ms per measurement per zone
ROI_WIDTH = 8 # SPADs wide per zone
ROI_HEIGHT = 16 # SPADs tall per zone (should always be 16)
ZONE_CENTERS = [167, 231]
THRESHOLD_PERCENT = 80 # % of floor dist
MIN_THRESHOLD_CM = 20 # ignore closer to this (ignore door)
CALIBRATION_SAMPLES = 20 # Readings per zone during calibration
MIN_DIST_FILTER_SIZE = 3 # rolling minimum filter window
CROSSING_TIMEOUT_S = 5.0 # abandon crossing if takes longer than


# TODO: Change to either random from bank / custom
# Temp default entry sound
ENTRY_SOUND = "../sounds/custom/oliver.wav"


# Min Distance Filter
# Sliding window of last N readings and returns the minimum (closest obj)
class MinDistFilter:
    def __init__(self, window_size: int):
        self.buf = deque(maxlen=window_size)

    def update(self, distance_cm: float) -> float:
        self.buf.append(distance_cm)
        return min(self.buf)

    def reset(self):
        self.buf.clear()
    
# State Machine
# The sensor alternates between two zones (Commons and UPL). At any moment,
# each zone is either NOBODY or SOMEONE. We encode the combined state as:
#
#   State 0 = nobody in either zone
#   State 1 = someone in Commons zone only     (Commons contributes +1)
#   State 2 = someone in UPL zone only         (UPL contributes +2)
#   State 3 = someone in BOTH zones            (1 + 2 = 3)
#
# When a person walks IN (from Commons → UPL):
#   - They hit the Commons zone first  → state goes 0 → 1
#   - They're in both zones            → state goes 1 → 3
#   - They leave the Commons zone      → state goes 3 → 2
#   - They leave the UPL zone          → state goes 2 → 0
#   Entry sequence: 0 → 1 → 3 → 2 → 0
# For exit it is just the reverse

# From my research this algorithm is used for most bidirectional
# occupancy mgmt systems with minor variations between them.

NOBODY = 0
SOMEONE = 1

STATE_NAMES = {
    0: "NOBODY",
    1: "COMMONS_ONLY",
    2: "UPL_ONLY",
    3: "BOTH",
}

ZONE_NAMES = ["COMMONS", "UPL"]

class PeopleCounter:
    def __init__(self, threshold_z0_cm: float, threshold_z1_cm: float, min_threshold_cm: float = 0):
        self.thresholds = [threshold_z0_cm, threshold_z1_cm]
        self.min_threshold = min_threshold_cm
        self.path_track = [0, 0, 0, 0]
        self.filling_size = 1
        self.prev_status = [NOBODY, NOBODY]
        self.people_count = 0
        self.crossing_start_time = 0.0
        #self.detective = detective

    def process(self, distance_cm: float, zone: int) -> int:
        if (distance_cm is not None
                and distance_cm > self.min_threshold
                and distance_cm < self.thresholds[zone]):
            current = SOMEONE
        else:
            current = NOBODY

        if current == self.prev_status[zone]:
            return 0

        other_zone = 1 - zone
        combined = 0
        if zone == 0:
            if current == SOMEONE:
                combined += 1
            if self.prev_status[other_zone] == SOMEONE:
                combined += 2
        else:
            if current == SOMEONE:
                combined += 2
            if self.prev_status[other_zone] == SOMEONE:
                combined += 1

        self.prev_status[zone] = current

        if self.filling_size < 4:
            self.filling_size += 1

        if self.prev_status[0] == NOBODY and self.prev_status[1] == NOBODY:
            change = 0

            if self.filling_size == 4:
                p = self.path_track
                # Entry pattern:
                if p[1] == 1 and p[2] == 3 and p[3] == 2:
                    change = 1
                # Exit pattern:
                elif p[1] == 2 and p[2] == 3 and p[3] == 1:
                    change = -1

            # cannot go negative :)
            self.people_count = max(0, self.people_count + change)
            self.filling_size = 1
            self.crossing_start_time = 0.0
            return change
        else:
            self.path_track[self.filling_size - 1] = combined

            if self.crossing_start_time == 0.0:
                self.crossing_start_time = time.time()
            return 0

    def check_timeout(self, timeout_s: float) -> bool:
        if self.crossing_start_time == 0.0:
            return False
        if time.time() - self.crossing_start_time > timeout_s:
            self.filling_size = 1
            self.prev_status = [NOBODY, NOBODY]
            self.crossing_start_time = 0.0
            return True
        return False

# Auto Calibrate
def calibrate(sensor, zone_centers, n_samples=20, timeout_s=10.0):
    averages = []
    zone_names = ["COMMONS (zone 0)", "UPL (zone 1)"]

    for z, center in enumerate(zone_centers):
        logger.log(f"Measuring {zone_names[z]} (center={center})...")
        sensor.roi_center = center
        time.sleep(0.01)
        if sensor.data_ready:
            _ = sensor.distance
            sensor.clear_interrupt()

        readings = []
        start = time.time()

        while len(readings) < n_samples:
            if time.time() - start > timeout_s:
                logger.warn(f"Timeout after {len(readings)} samples")
                break

            reading_start = time.time()
            while not sensor.data_ready:
                if time.time() - reading_start > 0.2:
                    break
                time.sleep(0.001)

            if sensor.data_ready:
                d = sensor.distance
                sensor.clear_interrupt()

                if d is not None and d > 0:
                    readings.append(d)

            time.sleep(0.005)

        if readings:
            avg = sum(readings) / len(readings)
            logger.log(f"Got {len(readings)} samples: avg={avg:.1f}cm, min={min(readings)}cm, max={max(readings)}cm")
            averages.append(avg)
        else:
            logger.error("No readings! Using default 200cm")
            averages.append(230.0)

    return averages

# Audio
def init_audio():
    try:
        pygame.mixer.init()
        logger.log("Audio initialized")
        return True
    except Exception as e:
        logger.error(f"Audio init failed: {e}")
        return False

def play_sound(filepath):
    try:
        pygame.mixer.music.load(filepath)
        pygame.mixer.music.play()
    except Exception as e:
        logger.error(f"Could not play {filepath}: {e}")

# Main Logic
def main(detective_holder: List[Detective], shutdown_event: threading.Event, args=None):

    # we need to wait for detective to be initialized
    # i dunno a better way to do this concurrently
    while detective_holder[0] is None and not shutdown_event.is_set():
        time.sleep(0.1)

    if shutdown_event.is_set():
        return

    detective = detective_holder[0]

    # If no args provided, parse with defaults
    if args is None:
        parser = argparse.ArgumentParser(description="UPL Doorbell People Counter")
        parser.add_argument("--debug", action="store_true",
                            help="Print every zone reading and state transition")
        parser.add_argument("--no-audio", action="store_true",
                            help="Disable sound playback")
        parser.add_argument("--threshold", type=int, default=THRESHOLD_PERCENT,
                            help=f"Threshold %% of floor distance (default: {THRESHOLD_PERCENT})")
        parser.add_argument("--status-interval", type=float, default=0,
                            help="Print periodic status every N seconds (0=off)")
        parser.add_argument("--initial-count", type=int, default=0,
                            help="Initial people count (default: 0)")
        args = parser.parse_args([])

    audio_ok = False
    if not args.no_audio:
        audio_ok = init_audio()

    logger.log("Initializing VL53L1X sensor...")
    i2c = board.I2C()
    sensor = adafruit_vl53l1x.VL53L1X(i2c)

    sensor.distance_mode = DISTANCE_MODE
    sensor.timing_budget = TIMING_BUDGET_MS
    sensor.roi_xy = (ROI_WIDTH, ROI_HEIGHT)

    sensor.start_ranging()
    logger.log(f"Sensor configured: mode={DISTANCE_MODE}, budget={TIMING_BUDGET_MS}ms, ROI={ROI_WIDTH}x{ROI_HEIGHT}")

    logger.log("=" * 40)
    logger.log("CALIBRATING - Keep the doorway CLEAR!")
    logger.log("=" * 40)
    floor_distances = calibrate(sensor, ZONE_CENTERS, CALIBRATION_SAMPLES)
    threshold_pct = args.threshold / 100.0
    thresholds = [d * threshold_pct for d in floor_distances]

    logger.log(f"Floor distances: zone0={floor_distances[0]:.1f}cm, zone1={floor_distances[1]:.1f}cm")
    logger.log(f"Thresholds ({args.threshold}%): zone0={thresholds[0]:.1f}cm, zone1={thresholds[1]:.1f}cm")
    logger.log(f"Min threshold: {MIN_THRESHOLD_CM}cm")

    counter = PeopleCounter(thresholds[0], thresholds[1], MIN_THRESHOLD_CM)
    counter.people_count = args.initial_count
    filters = [MinDistFilter(MIN_DIST_FILTER_SIZE),
               MinDistFilter(MIN_DIST_FILTER_SIZE)]
    current_zone = 0
    sensor.roi_center = ZONE_CENTERS[current_zone]
    time.sleep(0.01)
    if sensor.data_ready:
        _ = sensor.distance
        sensor.clear_interrupt()

    logger.log("=" * 40)
    logger.log("PEOPLE COUNTER ACTIVE")
    if args.initial_count > 0:
        logger.log(f"Starting count: {args.initial_count}")
    if args.debug:
        logger.log("DEBUG MODE ON - showing all readings")
    logger.log("=" * 40)
    last_status_time = time.time()
    # Main Loop
    try:
        while not shutdown_event.is_set():
            if sensor.data_ready:
                raw = sensor.distance
                sensor.clear_interrupt()
                if raw is not None and raw > 0:
                    filtered = filters[current_zone].update(raw)
                else:
                    filtered = floor_distances[current_zone]

                # Debug
                if args.debug:
                    is_person = (filtered > MIN_THRESHOLD_CM
                                 and filtered < thresholds[current_zone])
                    status_str = "SOMEONE" if is_person else "NOBODY "

                    combined = 0
                    if current_zone == 0:
                        if is_person:
                            combined += 1
                        if counter.prev_status[1] == SOMEONE:
                            combined += 2
                    else:
                        if is_person:
                            combined += 2
                        if counter.prev_status[0] == SOMEONE:
                            combined += 1

                    path_slice = counter.path_track[:counter.filling_size]
                    raw_str = f"{raw:>5}" if raw is not None else " None"
                    print(f"  [{ZONE_NAMES[current_zone]:>7s}] "
                          f"raw={raw_str}cm filt={filtered:>5.1f}cm → {status_str} | "
                          f"State: {STATE_NAMES.get(combined, '?'):>12s} ({combined}) | "
                          f"Path: {path_slice}")

                result = counter.process(filtered, current_zone)
                if result == 1:
                    logger.log(f">>> ENTRY detected! Room count: {counter.people_count}")

                    # enter will return the candidate device that it thinks has entered the room
                    # will either be of type Device or None
                    device = counter.detective.enter()

                    if audio_ok:
                        play_sound(ENTRY_SOUND)
                elif result == -1:
                    logger.log(f"<<< EXIT detected! Room count: {counter.people_count}")

                    # exit doesn't return anything, it just lets the detective
                    # know that an exit happened
                    counter.detective.exit()

                next_zone = 1 - current_zone
                sensor.roi_center = ZONE_CENTERS[next_zone]
                current_zone = next_zone

                time.sleep(0.01)

            else:
                time.sleep(0.001)

            if counter.check_timeout(CROSSING_TIMEOUT_S):
                if args.debug:
                    logger.debug("Crossing abandoned, state machine reset")

            if args.status_interval > 0:
                now = time.time()
                if now - last_status_time > args.status_interval:
                    logger.log(f"[STATUS] Count: {counter.people_count} | "
                               f"commons={filters[0].buf[-1] if filters[0].buf else '?'}cm, "
                               f"upl={filters[1].buf[-1] if filters[1].buf else '?'}cm")
                    last_status_time = now

    finally:
        sensor.stop_ranging()
        logger.log("=" * 40)
        logger.log(f"ToF detector stopped. Final count: {counter.people_count}")
        logger.log("=" * 40)
