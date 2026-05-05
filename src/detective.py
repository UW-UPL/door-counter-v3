import threading
from datetime import datetime, timedelta
import numpy as np
import time
import heapq
import logger
from typing import TYPE_CHECKING
from device import Device
from db_manager import set_tof_count, set_in_room
import logger
if TYPE_CHECKING:
	from ble_scanner import BLEScanner

#   LOCK ORDERING:
#       scanner lock -> detective lock -> device lock

#   its really easy to circular lock the scanner and detective 😭


class Detective:
    def __init__(self, scanner: "BLEScanner", shutdown_event: threading.Event | None = None):
        self.lock = threading.Lock()

        # Reference to BLEScanner (set by scanner's __init__)
        self.scanner = scanner

        # number of devices that the tof sensor thinks is in the room
        self.tof_size = 0
        set_tof_count(self.tof_size)

        # devices that the detective thinks are currently in the room
        self.active_set = set()

        self._shutdown_event = shutdown_event or threading.Event()
        self._gc_thread = threading.Thread(name="gc", target=self._gc_loop, daemon=True)
        self._gc_thread.start()

    # called by ToF when it detects an entrance
    # returns the Device that was
    def enter(self) -> Device | None:
        with self.scanner.lock:
            with self.lock:
                self.tof_size += 1
                set_tof_count(self.tof_size)

                logger.log("ENTER CALLED")
                #   Heuristic:
                #
                #   Recency bias: Devices that just sent a signal are considered more favorably
                #
                #   RSSI Trend: Someone walking in shows increasing RSSI
                #
                #   Absolute RSSI strength: Absolute RSSI is somewhat useful, but a fitness tracker
                #           might produce significantly weaker signals than a phone purely because of hardware
                #
                #
                #   All of these factors are weighted and compared to a score threshold.
                #
                #   The goal is to not choose false positives.
                #   A device can later prove that it's inside the room. We have a thread that
                #   periodically looks at devices and tries to refactor active_set based on up-to-date data

                now = datetime.now()
                best_score = 0
                candidate = None

                MIN_SCORE_THRESHOLD = 0.25

                WEIGHT_RECENCY = 0.20
                WEIGHT_TREND = 0.40
                WEIGHT_RSSI = 0.40

                for mac, device in self.scanner.devices.items():

                    if device in self.active_set:
                        continue
                    
                    # full history can contain stale data from previous presence,
                    # truncate to the last 60 seconds
                    full_history = device.get_history()
                    history = []
                    for rssi, ts in full_history:
                        if (now - ts).total_seconds() <= 60:
                            history.append((rssi, ts))
                    
                    # if there was no history in the last 60 seconds, do not consider the device
                    if len(history) == 0:
                        continue

                    # RECENCY BIAS
                    _, latest_time = history[-1]
                    seconds_ago = (now - latest_time).total_seconds()
                        # exponential decay
                        # e^(-1) = 0.36
                    recency_score = np.exp(-(seconds_ago / 15.0))

                    # RSSI TREND
                    trend_score = 0.5 # default of 0.5 (neutral)

                    if len(history) >= 5: # only try to analyze trend if we have enough readings
                        rssis, timestamps = zip(*history)
                        t0 = timestamps[0]
                        times = []
                        for ts in timestamps:
                            times.append((ts - t0).total_seconds())

                        # Linear regression ahhh
                        # cov(time, rssi) / var(time)
                        time_variance = np.var(times)
                        if time_variance > 0:
                            slope = np.cov(times, rssis)[0, 1] / time_variance
                            trend_score = np.clip(0.5 + slope / 4.0, 0, 1)

                    # ABSOLUTE RSSI
                        # between -30 and -90
                        # (MEAN_RECENT-(-90)) / ((−30)−(−90))
                    # Use last 3 readings from history
                    recent_rssis = [rssi for rssi, _ in history[-3:]]
                    scaled = (np.mean(recent_rssis) - (-90)) / ((-30)-(-90))
                    rssi_score = np.clip(scaled, 0, 1)

                    total = WEIGHT_RECENCY * recency_score + WEIGHT_TREND * trend_score + WEIGHT_RSSI * rssi_score

                    logger.log(f"Score for {device.name}: {total}")

                    if total > best_score and total > MIN_SCORE_THRESHOLD:
                        best_score = total
                        candidate = device
                
                if candidate is not None:
                    self.active_set.add(candidate)

                #  if len(self.active_set) > self.tof_size: GC will figure it out

                return candidate
    
    # called by ToF when it detects an exit
    def exit(self):
        with self.lock:
            self.tof_size = max(self.tof_size-1, 0)
            set_tof_count(self.tof_size)
        
        # GC will figure out who to kick in a bit

    def _gc_loop(self):
        

        def score(device: Device):
            now = datetime.now()

            # truncate to last 5 minutes
            full_history = device.get_history()
            history = []
            for rssi, ts in full_history:
                if (now - ts).total_seconds() <= 300:
                    history.append((rssi, ts))

            if len(history) == 0:
                return 0.0

            #   Heuristic: While the enter heuristic tries to capture devices that have just walked in, 
            #       the score heuristic operates over a longer time window and tries to find devices that
            #       weren't added to the active set during enter, but provide sufficient evidence that
            #       they are in the room later. It also attempts to detect people who have walked away
            #       (negative RSSI trend) or were walking towards (positive RSSI trend) the room.
            #
            #   Consistency: Over the last 5 minutes have we seen a signal every 30 seconds from the device?
            #
            #   Strength: If we have seen signals, how strong are they?
            #   
            #   RSSI Trend: Has a device been seen walking away (lesser score) or walking towards (greater score)
            #       the room some time during the 5 minute window?
            #
            #   All of these factors are weighted and compared to a score threshold.
            
            WEIGHT_CONSISTENCY = 0.35
            WEIGHT_STRENGTH = 0.35
            WEIGHT_TREND = 0.30

            # RSSI TREND
            #   The person either ended their RSSI signals by walking away at some point
            #   during the time window, or they had walked towards the room inside the
            #   time window.

            trend = 0.5

            def compute_slope(readings):
                if len(readings) < 5:
                    return 0.0
                rssis, timestamps = zip(*readings)
                t0 = timestamps[0]
                times = [(ts - t0).total_seconds() for ts in timestamps]
                time_variance = np.var(times)
                if time_variance > 0:
                    return np.cov(times, rssis)[0, 1] / time_variance
                return 0.0

            # Check the final 30 seconds before the last reading for negative slope (walking away)
            if len(history) > 0:
                last_reading_time = history[-1][1]
                last_30s = []
                for r, ts in history:
                    if (last_reading_time - ts).total_seconds() <= 30:
                        last_30s.append((r, ts))
                away_slope = compute_slope(last_30s)
            else:
                away_slope = 0.0

            # Check sliding 30 second windows for max positive slope (walking towards)
            max_towards_slope = 0.0
            for window_start in range(0, 270, 10):
                window = []
                for r, ts in history:
                    seconds_ago = (now - ts).total_seconds()
                    if window_start <= seconds_ago <= window_start + 30:
                        window.append((r, ts))

                if len(window) >= 5:
                    slope = compute_slope(window)
                    if slope > max_towards_slope:
                        max_towards_slope = slope

            # Walking away at end dominates
            SLOPE_THRESHOLD = 0.6 # dBm/sec
            if away_slope < -SLOPE_THRESHOLD: # penalize (normalize slope to 0-0.5 range)
                trend = np.clip(0.5 + away_slope / 4.0, 0, 0.5)
            elif max_towards_slope > SLOPE_THRESHOLD: # boost (normalize slope to 0.5-1.0 range)
                trend = np.clip(0.5 + max_towards_slope / 4.0, 0.5, 1.0)

            bins = [[] for _ in range(10)]
            for rssi, ts in history:
                seconds_ago = (now - ts).total_seconds()
                bin_idx = int(seconds_ago // 60)
                if bin_idx < 10:
                    bins[bin_idx].append(rssi)
            
            bin_means = []
            for b in bins:
                if len(b) > 0:
                    bin_means.append(np.mean(b))

            # CONSISTENCY
            consistency = len(bin_means) / 10

            # STRENGTH
            strength = 0.0
            if len(bin_means) > 0:
                avg_rssi = np.mean(bin_means)
                strength = (avg_rssi - (-90)) / ((-30) - (-90))
                strength = np.clip(strength, 0, 1)

            total = WEIGHT_CONSISTENCY * consistency + WEIGHT_STRENGTH * strength + WEIGHT_TREND * trend
            logger.log(
                f"  score {device.name}: total={total:.2f} "
                f"(consistency={consistency:.2f} strength={strength:.2f} trend={trend:.2f}, "
                f"readings={len(history)})"
            )
            return total

        #  runs every 10 seconds
        while not self._shutdown_event.is_set():
            if self._shutdown_event.wait(10):
                break
            logger.log("GC RUNNING")

            # for easier reasoning, we hold all the locks while we gc
            with self.scanner.lock:
                with self.lock:
                    # edge case, if a device is deleted from the scanner's set (likely due to it being deleted from DB)
                    #   we should also remove it from active set
                    tracked_devices = set(self.scanner.devices.values())
                    self.active_set &= tracked_devices

                    device_scores = {device: score(device) for device in tracked_devices}

                    # if a device shows no history within the last 5 minutes
                    # explicitly boot it no matter what
                    stale = set()
                    for d in self.active_set:
                        if device_scores.get(d, 0) == 0.0:
                            stale.add(d)
                    if len(stale) > 0:
                        self.active_set -= stale
                        for d in stale:
                            logger.log(f"Booted stale device: {d.name}")

                    # active set is too large
                    if len(self.active_set) > self.tof_size:
                        active_heap = [(device_scores[device], device) for device in self.active_set]
                        heapq.heapify(active_heap)

                        while len(self.active_set) > self.tof_size:
                            _, device = heapq.heappop(active_heap)
                            self.active_set.remove(device)

                    # active set is too small
                    if len(self.active_set) < self.tof_size:
                        non_active_heap = [(-device_scores[device], device) for device in tracked_devices if device not in self.active_set]
                        heapq.heapify(non_active_heap)

                        while len(self.active_set) < self.tof_size:
                            if len(non_active_heap) == 0:
                                break

                            neg_score, device = heapq.heappop(non_active_heap)
                            device_score = -neg_score
                            if device_score < 0.35: # some threshold we will probably change later
                                break

                            self.active_set.add(device)

                    # replacement phase always runs
                    #   keep swapping lowest active with highest non active while significantly better
                    if len(self.active_set) > 0:
                        # min heap for active devices
                        active_heap = [(device_scores[device], device) for device in self.active_set]
                        
                        heapq.heapify(active_heap)

                        # max heap for non active devices
                        non_active_heap = [(-device_scores[device], device) for device in tracked_devices if device not in self.active_set]
                        heapq.heapify(non_active_heap)

                        REPLACEMENT_THRESHOLD = 0.10

                        while len(active_heap) > 0 and len(non_active_heap) > 0:
                            min_active_score, min_active_device = active_heap[0]
                            neg_max_non_active_score, max_non_active_device = non_active_heap[0]
                            max_non_active_score = -neg_max_non_active_score

                            # Swap if non active score is significantly better
                            if max_non_active_score > min_active_score + REPLACEMENT_THRESHOLD:
                                # Remove from heaps
                                heapq.heappop(active_heap)
                                heapq.heappop(non_active_heap)

                                # Update active set
                                self.active_set.remove(min_active_device)
                                self.active_set.add(max_non_active_device)

                                logger.log(f"Replaced {min_active_device.name} (score: {min_active_score:.2f}) " f"with {max_non_active_device.name} (score: {max_non_active_score:.2f})")

                                # swapped devices now switch heaps
                                heapq.heappush(non_active_heap, (-min_active_score, min_active_device))
                                heapq.heappush(active_heap, (max_non_active_score, max_non_active_device))
                            else:
                                break
        
            with self.lock:
                logger.log(
                    f"BLE active set: {len(self.active_set)} device(s), "
                    f"ToF count: {self.tof_size}, tracked: {len(self.scanner.devices)}"
                )
                for device in self.active_set:
                    logger.log(f"   in-room: {device.name}")

                set_in_room([device.name for device in self.active_set])
        
        