# Prereqs: pip3 install adafruit-circuitpython-vl53l1x pygame

import time
import argparse
import board
import adafruit_vl53l1x
import pygame
from collections import deque
import threading
import struct
import logger

#from detective import Detective

# Config
# Switched from Mode 2 (Long) to Mode 1 (Short) because at 92cm mounting height,
# Long mode's guaranteed range on 17% reflectance targets (like waxed concrete)
# is only ~80cm — literally below our floor distance. Short mode guarantees
# 136cm and has way better ambient light immunity.
DISTANCE_MODE = 1

TIMING_BUDGET_MS = 33 # ms per measurement per zone
ROI_WIDTH = 8 # SPADs wide per zone
ROI_HEIGHT = 16 # SPADs tall per zone (should always be 16)

# Updated zone centers per ST engineer recommendation.
# SPAD 175 gives better signal return than 167 for the front zone.
ZONE_CENTERS = [175, 231]

THRESHOLD_PERCENT = 80 # % of floor dist
MIN_THRESHOLD_CM = 20 # ignore closer to this (ignore door)
CALIBRATION_SAMPLES = 20 # Readings per zone during calibration

# Increased from 3 to 10 per ST UM2600 Section 6.2.
# A window of 10 makes brief detections "sticky" — if a dark-clothed person
# produces even 1 valid reading in 10 cycles, it persists long enough to
# bridge the overlap between zones.
MIN_DIST_FILTER_SIZE = 10

# Reduced from 5.0s to 2.5s. A normal crossing takes 300-500ms.
# 5 seconds allowed unrelated events to be grouped.
CROSSING_TIMEOUT_S = 2.5

# Debounce period after a counted crossing to prevent double-counting
# from lingering minimum-filter values.
DEBOUNCE_S = 0.5

# Manual fallback threshold for when auto-calibration fails
# (common on waxed concrete). Based on mounting height.
MOUNTING_HEIGHT_CM = 92.0
MANUAL_THRESHOLD_CM = MOUNTING_HEIGHT_CM * (THRESHOLD_PERCENT / 100.0)

# Signal rate threshold for dark clothing detection.
# Default (1024 kcps) is too aggressive for dark fabric at close range —
# dark synthetics return only 30-200 kcps. Lowering this lets those
# weak-but-valid returns through.
SIGNAL_THRESHOLD_KCPS = 300


# TODO: Change to either random from bank / custom
# Temp default entry sound
ENTRY_SOUND = "../sounds/custom/oliver.wav"


# Low-level register write for signal rate threshold.
# The Adafruit CircuitPython library doesn't expose this setting,
# so we write directly to the VL53L1X register over I2C.
#
# IMPORTANT: We do NOT touch the sigma threshold (register 0x0064).
# The default sigma threshold is 90mm (0x0168 in 14.2 fixed-point format),
# verified from the Adafruit library's init sequence at bytes 0x64-0x65.
# Sigma is a MAXIMUM — readings with uncertainty ABOVE it are rejected.
# Lowering sigma makes the sensor MORE restrictive, which causes it to
# reject legitimate floor readings at 92cm (which have 40-80mm sigma on
# waxed concrete) and only accept internal cross-talk reflections at ~2cm.
def write_sensor_thresholds(i2c_device, signal_kcps=SIGNAL_THRESHOLD_KCPS):
    """
    Lower the signal rate threshold to improve detection of dark/low-
    reflectivity targets like black jackets.

    Signal rate register: 0x0066-0x0067 (9.7 fixed-point format)
    Default: 0x0080 = 128 → 1000 kcps
    Our value: 0x0026 = 38 → ~300 kcps
    """
    try:
        # Signal rate: convert kcps to 9.7 fixed-point
        # Formula: value = (kcps / 1000) * 128
        signal_val = int((signal_kcps / 1000.0) * 128)
        signal_bytes = struct.pack(">H", signal_val)

        # Write signal rate threshold (0x0066)
        buf = bytes([0x00, 0x66]) + signal_bytes
        while not i2c_device.try_lock():
            pass
        i2c_device.writeto(0x29, buf)
        i2c_device.unlock()

        logger.log(f"Sensor signal threshold set: {signal_kcps}kcps "
                   f"(reg=0x{signal_val:04X}), sigma=default 90mm (untouched)")
    except Exception as e:
        logger.error(f"Could not write sensor thresholds: {e}")
        logger.log("Continuing with default thresholds (dark clothing "
                   "detection may be reduced)")


# Min Distance Filter
# Sliding window of last N readings and returns the minimum (closest obj)
# Improvement: None/invalid readings are now skipped instead of inserted,
# preserving the current minimum. This prevents a single bad reading from
# resetting the detection window.
class MinDistFilter:
    def __init__(self, window_size: int):
        self.buf = deque(maxlen=window_size)
        self.last_valid = None

    def update(self, distance_cm: float) -> float:
        """Add a valid reading and return the rolling minimum."""
        self.buf.append(distance_cm)
        self.last_valid = min(self.buf)
        return self.last_valid

    def current(self) -> float:
        """Return current minimum without adding a new reading.
        Used when sensor returns None — we hold the last state."""
        if self.last_valid is not None:
            return self.last_valid
        if self.buf:
            return min(self.buf)
        return float('inf')

    def reset(self):
        self.buf.clear()
        self.last_valid = None


# State Machine with Tiered Crossing Confidence
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
#
# From my research this algorithm is used for most bidirectional
# occupancy mgmt systems with minor variations between them.
#
# NEW: Three confidence tiers to handle missed state 3
# (the #1 cause of the 30% miss rate):
#
#   HIGH:   Full sequence with state 3 (standard ST algorithm)
#   MEDIUM: 0→1→0 followed by 0→2→0 within a time window
#           (state 3 was missed but zone order indicates direction)
#   PARTIAL: 0→1→3→0 or 0→2→3→0 (exit-side detection missed,
#           direction inferred from first single-zone state)

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
        self.path_track = [0, 0, 0, 0, 0, 0] # extra slots for longer paths
        self.filling_size = 1
        self.prev_status = [NOBODY, NOBODY]
        self.people_count = 0
        self.crossing_start_time = 0.0
        self.last_crossing_time = 0.0 # debounce timer

        # Medium-confidence partial crossing tracking.
        # Stores the zone that was seen in the last "partial" event
        # (a single-zone blip that returned to NOBODY without state 3).
        self.partial_zone = None
        self.partial_time = 0.0
        self.partial_timeout_s = 2.0 # max gap between partial halves

    def process(self, distance_cm: float, zone: int) -> int:
        """Process a distance reading for the given zone.
        Returns: +1 for entry, -1 for exit, 0 for no event."""

        # Determine if someone is present in this zone
        if (distance_cm is not None
                and distance_cm > self.min_threshold
                and distance_cm < self.thresholds[zone]):
            current = SOMEONE
        else:
            current = NOBODY

        # No state change — nothing to do
        if current == self.prev_status[zone]:
            return 0

        # Compute combined state (both zones together)
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

        if self.filling_size < len(self.path_track):
            self.filling_size += 1

        # Both zones are now empty — evaluate the completed crossing
        if self.prev_status[0] == NOBODY and self.prev_status[1] == NOBODY:
            change = self._evaluate_crossing()

            # Debounce — ignore crossings too close together
            now = time.time()
            if change != 0 and (now - self.last_crossing_time) < DEBOUNCE_S:
                change = 0

            if change != 0:
                self.people_count = max(0, self.people_count + change)
                self.last_crossing_time = now

            self.filling_size = 1
            self.crossing_start_time = 0.0
            return change
        else:
            # Record the state in the path tracker
            if self.filling_size - 1 < len(self.path_track):
                self.path_track[self.filling_size - 1] = combined

            if self.crossing_start_time == 0.0:
                self.crossing_start_time = time.time()
            return 0

    def _evaluate_crossing(self) -> int:
        """Evaluate a completed crossing sequence and return direction.

        Returns +1 (entry), -1 (exit), or 0 (unknown/invalid).
        Uses tiered confidence: HIGH > PARTIAL > MEDIUM.
        """
        p = self.path_track
        n = self.filling_size

        # === HIGH CONFIDENCE: Full sequences with state 3 ===
        if n >= 4:
            # Look for the canonical patterns anywhere in the path
            for i in range(1, n - 2):
                if p[i] == 1 and p[i+1] == 3 and p[i+2] == 2:
                    self._clear_partial()
                    return 1   # Entry: Commons → Both → UPL
                if p[i] == 2 and p[i+1] == 3 and p[i+2] == 1:
                    self._clear_partial()
                    return -1  # Exit: UPL → Both → Commons

        # === PARTIAL WITH BOTH: State 3 seen but exit-side missed ===
        # Patterns like 0→1→3→0 or 0→2→3→0
        if n >= 3:
            for i in range(1, n - 1):
                if p[i] == 1 and p[i+1] == 3:
                    self._clear_partial()
                    return 1   # Started in Commons, hit Both → entry
                if p[i] == 2 and p[i+1] == 3:
                    self._clear_partial()
                    return -1  # Started in UPL, hit Both → exit
                if p[i] == 3 and p[i+1] == 2:
                    self._clear_partial()
                    return 1   # Hit Both, ended in UPL → entry
                if p[i] == 3 and p[i+1] == 1:
                    self._clear_partial()
                    return -1  # Hit Both, ended in Commons → exit

        # === MEDIUM CONFIDENCE: Single-zone blip, no state 3 ===
        # This happens when the person moves too fast for the sensor
        # to catch both zones simultaneously. We track two consecutive
        # single-zone events and infer direction from their order.
        now = time.time()
        seen_zone = None
        if n >= 2:
            # What single zone was triggered?
            for i in range(1, n):
                if p[i] == 1:
                    seen_zone = 0  # Commons only
                    break
                elif p[i] == 2:
                    seen_zone = 1  # UPL only
                    break

        if seen_zone is not None:
            if (self.partial_zone is not None
                    and (now - self.partial_time) < self.partial_timeout_s):
                # We have a previous partial — check if zones differ
                if self.partial_zone != seen_zone:
                    # Two different zones triggered in sequence!
                    if self.partial_zone == 0 and seen_zone == 1:
                        self._clear_partial()
                        return 1   # Commons then UPL → entry
                    elif self.partial_zone == 1 and seen_zone == 0:
                        self._clear_partial()
                        return -1  # UPL then Commons → exit
                # Same zone twice or expired — update partial
                self.partial_zone = seen_zone
                self.partial_time = now
            else:
                # First partial or previous expired — store it
                self.partial_zone = seen_zone
                self.partial_time = now

        return 0

    def _clear_partial(self):
        """Reset the medium-confidence partial tracker."""
        self.partial_zone = None
        self.partial_time = 0.0

    def check_timeout(self, timeout_s: float) -> bool:
        """Check if a crossing-in-progress has timed out.

        Instead of just discarding, we attempt to evaluate the partial
        path before resetting.
        """
        if self.crossing_start_time == 0.0:
            return False
        if time.time() - self.crossing_start_time > timeout_s:
            # Try to salvage direction from whatever states we captured
            change = self._evaluate_crossing()
            if change != 0:
                now = time.time()
                if (now - self.last_crossing_time) >= DEBOUNCE_S:
                    self.people_count = max(0, self.people_count + change)
                    self.last_crossing_time = now

            self.filling_size = 1
            self.prev_status = [NOBODY, NOBODY]
            self.crossing_start_time = 0.0
            return True
        return False

# Auto Calibrate
# If calibration fails (common on waxed concrete), falls back to
# MANUAL_THRESHOLD_CM instead of a hardcoded 230cm default.
def calibrate(sensor, zone_centers, n_samples=20, timeout_s=10.0):
    averages = []
    zone_names = ["COMMONS (zone 0)", "UPL (zone 1)"]

    for z, center in enumerate(zone_centers):
        logger.log(f"Measuring {zone_names[z]} (center={center})...")
        sensor.roi_center = center

        # Wait for ROI change to take effect, then flush
        time.sleep(0.02)
        if sensor.data_ready:
            _ = sensor.distance
            sensor.clear_interrupt()
        # Discard one more reading to ensure we're on the new ROI
        time.sleep(0.05)
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
            # Use mounting height as fallback instead of arbitrary 230cm.
            # This gives a usable threshold even when the waxed concrete
            # floor reflects nothing back.
            logger.error(f"No readings for zone {z}! Using manual "
                         f"fallback: {MOUNTING_HEIGHT_CM}cm")
            averages.append(MOUNTING_HEIGHT_CM)

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
def main(detective_holder, shutdown_event: threading.Event, args=None):

    # we need to wait for detective to be initialized
    # i dunno a better way to do this concurrently
    #while detective_holder[0] is None and not shutdown_event.is_set():
    #    time.sleep(0.1)

    if shutdown_event.is_set():
        return

    #detective = detective_holder[0]

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
        parser.add_argument("--manual-threshold", action="store_true",
                            help="Skip auto-calibration and use manual threshold based on mounting height")
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

    # Write low-level signal rate threshold for dark clothing detection
    # before starting ranging. Only signal rate — sigma stays at default 90mm.
    write_sensor_thresholds(i2c, SIGNAL_THRESHOLD_KCPS)

    sensor.start_ranging()
    logger.log(f"Sensor configured: mode={DISTANCE_MODE} (SHORT), budget={TIMING_BUDGET_MS}ms, ROI={ROI_WIDTH}x{ROI_HEIGHT}")

    # Calibration
    if getattr(args, 'manual_threshold', False):
        # Skip auto-calibration entirely — use mounting height
        logger.log("=" * 40)
        logger.log("USING MANUAL THRESHOLD (no calibration)")
        logger.log("=" * 40)
        floor_distances = [MOUNTING_HEIGHT_CM, MOUNTING_HEIGHT_CM]
    else:
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

    # Initial flush — wait for ROI to take effect and discard the first
    # (stale) reading.
    time.sleep(0.02)
    if sensor.data_ready:
        _ = sensor.distance
        sensor.clear_interrupt()

    # Track whether the next reading should be discarded after a zone
    # switch (it contains stale data from the previous ROI).
    discard_next = True

    logger.log("=" * 40)
    logger.log("PEOPLE COUNTER ACTIVE")
    logger.log(f"  Crossing timeout:  {CROSSING_TIMEOUT_S}s")
    logger.log(f"  Debounce:          {DEBOUNCE_S}s")
    logger.log(f"  Min filter window: {MIN_DIST_FILTER_SIZE}")
    logger.log(f"  Signal threshold:  {SIGNAL_THRESHOLD_KCPS} kcps")
    logger.log(f"  Sigma threshold:   90 mm (default, untouched)")
    if args.initial_count > 0:
        logger.log(f"  Starting count:    {args.initial_count}")
    if args.debug:
        logger.log("  DEBUG MODE ON — showing all readings")
    logger.log("=" * 40)

    last_status_time = time.time()

    # Main Loop
    try:
        while not shutdown_event.is_set():
            if sensor.data_ready:
                raw = sensor.distance
                sensor.clear_interrupt()

                # Discard the first reading after a zone switch — it's stale
                # data from the previous ROI.
                if discard_next:
                    discard_next = False
                    # Don't process this reading at all. The sensor is now
                    # ranging on the correct ROI; the *next* data_ready will
                    # be valid.
                    time.sleep(0.001)
                    continue

                # Handle None/invalid readings by holding the last valid value
                # instead of injecting floor distance (which would reset the
                # min filter).
                if raw is not None and raw > 0:
                    filtered = filters[current_zone].update(raw)
                else:
                    filtered = filters[current_zone].current()

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
                    filt_str = f"{filtered:>5.1f}" if filtered != float('inf') else "  inf"
                    print(f"  [{ZONE_NAMES[current_zone]:>7s}] "
                          f"raw={raw_str}cm filt={filt_str}cm → {status_str} | "
                          f"State: {STATE_NAMES.get(combined, '?'):>12s} ({combined}) | "
                          f"Path: {path_slice}")

                result = counter.process(filtered, current_zone)
                if result == 1:
                    logger.log(f">>> ENTRY detected! Room count: {counter.people_count}")

                    # enter will return the candidate device that it thinks has entered the room
                    # will either be of type Device or None
                    #device = counter.detective.enter()

                    if audio_ok:
                        play_sound(ENTRY_SOUND)
                elif result == -1:
                    logger.log(f"<<< EXIT detected! Room count: {counter.people_count}")

                    # exit doesn't return anything, it just lets the detective
                    # know that an exit happened
                    #counter.detective.exit()

                # Zone switch with proper timing. Sequence: finish processing →
                # switch ROI center → mark next reading as stale → small delay
                # for the ROI change to take effect in the sensor firmware.
                next_zone = 1 - current_zone
                sensor.roi_center = ZONE_CENTERS[next_zone]
                current_zone = next_zone
                discard_next = True

                # Small delay for ROI center change to register.
                # ST reference code uses ~10ms here.
                time.sleep(0.01)

            else:
                time.sleep(0.001)

            if counter.check_timeout(CROSSING_TIMEOUT_S):
                if args.debug:
                    # check_timeout now attempts to salvage direction from
                    # partial paths before resetting
                    logger.debug("Crossing timeout — state machine reset (partial path evaluated)")

            if args.status_interval > 0:
                now = time.time()
                if now - last_status_time > args.status_interval:
                    c_dist = f"{filters[0].buf[-1]:.0f}" if filters[0].buf else "?"
                    u_dist = f"{filters[1].buf[-1]:.0f}" if filters[1].buf else "?"
                    logger.log(f"[STATUS] Count: {counter.people_count} | "
                               f"commons={c_dist}cm, upl={u_dist}cm")
                    last_status_time = now

    finally:
        sensor.stop_ranging()
        logger.log("=" * 40)
        logger.log(f"ToF detector stopped. Final count: {counter.people_count}")
        logger.log("=" * 40)