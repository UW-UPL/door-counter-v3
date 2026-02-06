#Prereqs: pip3 install adafruit-circuitpython-vl53l1x pygame

import time
import argparse
import board
import adafruit_vl53l1x
import pygame
from collections import deque

# Config
TIMING_BUDGET_MS = 33 # ms per measurement per zone
ROI_WIDTH = 8 # SPADs wide per zone 
ROI_HEIGHT = 16 # SPADs tall per zone (should always be 16)
ZONE_CENTERS = [167, 231]
THRESHOLD_PERCENT = 80 # % of floor dist
MIN_THRESHOLD = 20 # ignore closer to this (ignore door)
CALIBRATION_SAMPLES = 20 # Readings per zone during calibration
MIN_DIST_FILTER_SIZE = 3 # rolling minimum filter window
CROSSING_TIMEOUT_S = 5.0 # abandon crossing if takes longer than


#TODO: Change to either random from bank / custom
#Temp default entry sound
ENTRY_SOUND = "sounds/custom/oliver.wav"


# Min Distance Filter
# Sliding window of last N readings and returns the minimum (closest obj)
class MinDistFilter:
    def __init__(self, window_size: int):
        self.buf = deque(maxlen=window_size)

    def update(self, distance_cm: float) -> float:
        self.buf.append(distance_cm)

    def reset(self):
        self.buf.clear()
    
# State Machine
# The sensor alternates between two zones Commons and UPL). At any moment,
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

class PeopleCounter:
    def __init__(self, threshold_z0_cm: float, threshold_z1_cm: float, min_threshold_cm: float = 0):
        self.thresholds = [threshold_z0_cm, threshold_z1_cm]
        self.min_threshold = min_threshold_cm
        self.path_track = [0, 0, 0, 0]
        self.filling_size = 1
        self.prev_status = [NOBODY, NOBODY]
        self.people_count = 0
        self.crossing_start_time = 0.0

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

            #cannot go negative :)
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

# Audio
def init_audio():
    try:
        pygame.mixer.init()
        print("Audio Setup!")
        return True
    except Exception as e:
        print(f"Audio FAILED: {e}")
        return False

def play_sound(filepath):
    try:
        pygame.mixer.music.load(filepath)
        pygame.mixer.music.play()
    except Exception as e:
        print(f"Could not play {filepath}: {e}")

# Main Logic
def main():
    args = parser.parse_args()

    audio_ok = False
    if not args.no_audio:
        audio_ok = init_audio()

    print("Initializing VL53L1X sensor...")
    i2c = board.I2C()
    sensor = adafruit_vl53l1x.VL53L1X(i2c)
    sensor.distance_mode = DISTANCE_MODE
    sensor.timing_budget = TIMING_BUDGET_MS
    sensor.roi_xy = (ROI_WIDTH, ROI_HEIGHT)
    sensor.start_ranging()
    print(f"Sensor configured: mode={DISTANCE_MODE}, "
          f"budget={TIMING_BUDGET_MS}ms, ROI={ROI_WIDTH}x{ROI_HEIGHT}")

    counter = PeopleCounter(thresholds[0], thresholds[1], MIN_THRESHOLD_CM)
    filters = [MinDistFilter(MIN_DIST_FILTER_SIZE),
               MinDistFilter(MIN_DIST_FILTER_SIZE)]
    current_zone = 0
    sensor.roi_center = ZONE_CENTERS[current_zone]
    time.sleep(0.01)
    if sensor.data_ready:
        _ = sensor.distance
        sensor.clear_interrupt()

    print("\n" + "=" * 50)
    print("PEOPLE COUNTER ACTIVE")
    last_status_time = time.time()
    # Main Loop
    try:
        while True:
            if sensor.data_ready:
                raw = sensor.distance
                sensor.clear_interrupt()
                if raw is not None and raw > 0:
                    filtered = filters[current_zone].update(raw)
                else:
                    filtered = floor_distances[current_zone]
                
                result = counter.process(filtered, current_zone)
                if result == 1:
                    print(f">>> ENTRY detected! Room count: {counter.people_count}")
                    #TODO: Call the BLE Detective HERE to determine ID
                    if audio_ok:
                        play_sound(ENTRY_SOUND)
                    elif result == -1:
                        print(f"<<< EXIT detected!  Room count: {counter.people_count}")

                    next_zone = 1 - current_zone
                    sensor.roi_center = ZONE_CENTERS[next_zone]
                    current_zone = next_zone

                    time.sleep(0.01)
                else:
                    time.sleep(0.01)

    except KeyboardInterrupt:
    sensor.stop_ranging()
    print("\n" + "=" * 50)
    print("STOPPED")
    print(f"  Final room count: {counter.people_count}")
    print("=" * 50)

if __name__ == "__main__":
    main()
                
