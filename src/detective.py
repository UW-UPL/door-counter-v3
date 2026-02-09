import threading
from datetime import datetime, timedelta
import numpy as np
import time
import logger
from device import Device
from ble_scanner import BLEScanner

#   LOCK ORDERING:
#       scanner lock -> detective lock -> device lock

#   its really easy to circular lock the scanner and detective 😭


class Detective:
    def __init__(self, scanner: BLEScanner):
        self.lock = threading.Lock()

        # Reference to BLEScanner (set by scanner's __init__)
        self.scanner = scanner

        # number of devices that the tof sensor thinks is in the room
        self.tof_size = 0

        # devices that the detective thinks are currently in the room
        self.active_set = set()

        self._running = True
        self._gc_thread = threading.Thread(target=self._gc_loop, daemon=True)
        self._gc_thread.start()

    # called by ToF when it detects an entrance
    # returns the Device that was
    def enter(self) -> Device | None:
        with self.scanner.lock:
            devices_snapshot = list(self.scanner.devices.items())

        with self.lock:
            self.tof_size += 1
            active_set_snapshot = self.active_set.copy()

        #   Heuristic:
        #
        #   Recency bias: Devices that just sent a signal are considered more favorably
        #
        #   RSSI Trend: Someone walking in shows increasing RSSI
        #
        #   Absolute RSSI strength: Absolute RSSI is somewhat useful, but a fitness tracker
        #           might produce significantly weaker signals than a phone purely because of hardware
        #
        #    Signal consistency: Due to how flakely BLE is, devices with
        #           regular pings are more reliable candidates
        #
        #   All of these factors are weighted and compared to a score threshold.
        #
        #   The goal is to not choose false positives.
        #   A device can later prove that it's inside the room. We have a thread that
        #   periodically looks at devices and tries to refactor active_set based on up-to-date data

        # Process devices without holding the lock
        now = datetime.now()
        best_score = 0
        candidate = None

        MIN_SCORE_THRESHOLD = 0.30

        WEIGHT_RECENCY = 0.10
        WEIGHT_TREND = 0.30
        WEIGHT_RSSI = 0.40
        WEIGHT_CONSISTENCY = 0.20

        for mac, device in devices_snapshot:
            logger.log(f"ANALYZING DEVICE: {mac} | {device.name}")

            if device in active_set_snapshot:
                continue

            history = device.get_history()
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
            time_window = timedelta(seconds=30)
            recent_readings = []
            for rssi, ts in history:
                if now - ts <= time_window:
                    recent_readings.append((rssi, ts))

            if len(recent_readings) >= 3: # only try to analyze trend if we have enough readings
                rssis, timestamps = zip(*recent_readings)
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

            # SIGNAL CONSISTENCY
            consistency_score = 0.5 # default
            recent = []
            # filter to last 30 seconds
            for rssi, time in history:
                if (now - time).total_seconds() <= 30:
                    recent.append((rssi, time))

            if len(recent) >= 2:
                expected_pings = 20
                actual_pings = len(recent)
                consistency_score = np.clip(actual_pings / expected_pings, 0, 1)

            total = WEIGHT_RECENCY * recency_score + WEIGHT_TREND * trend_score + WEIGHT_RSSI * rssi_score + WEIGHT_CONSISTENCY * consistency_score

            logger.log(f"Score for {device.name}: {total}")

            if total > best_score and total > MIN_SCORE_THRESHOLD:
                best_score = total
                candidate = device
        
        with self.lock:
            if candidate is not None and candidate not in self.active_set:
                self.active_set.add(candidate)

        return candidate

        #  if len(self.active_set) > self.tof_size: GC will figure it out
    
    # called by ToF when it detects an exit
    def exit(self):
        # give some time for the person to walk away
        time.sleep(5)

        with self.lock:
            self.tof_size = max(self.tof_size-1, 0)
        
        # GC will figure out who to kick in a bit

    def _gc_loop(self):
        #   runs every 5 seconds

        while self._running:
            time.sleep(5)

            # for easier reasoning, we hold all the locks while we gc
            with self.scanner.lock:
                with self.lock:
                    # edge case, if a device is deleted from the scanner's set (likely due to it being deleted from DB)
                    #   we should also remove it from active set
                    tracked_devices = set(self.scanner.devices.values())
                    self.active_set &= tracked_devices

                    # active set is too large
                    if len(self.active_set) > self.tof_size:
                        scores = []
                        for device in self.active_set:
                            s = self.score(device)
                            scores.append((device, s))

                        scores.sort(key=lambda tup: tup[1], reverse=True) # desc

                        while len(self.active_set) > self.tof_size:
                            device, _ = scores.pop()
                            self.active_set.remove(device)

                    # active set is too small, try to promote a device
                    if len(self.active_set) < self.tof_size:
                        scores = []
                        for device in list(self.scanner.devices.values()):
                            if device not in self.active_set:
                                s = self.score(device)
                                scores.append((device, s))

                        scores.sort(key=lambda tup: tup[1]) # asc

                        while len(self.active_set) < self.tof_size:
                            if len(scores) == 0:
                                break

                            device, s = scores.pop()
                            if s < 0.5: # some threshold we will probably change later
                                break

                            self.active_set.add(device)

                    # replace phase?
            
        # always do replace phase (even if sizes match):
        #   find the lowest scoring active device
        #   find the highest scoring non-active device (if any)
        #   if non-active score >> actiive score, swap them
        #   this handles when wrong devices are added or a better candidate emerged
        #
        # get rid of devices from self.devices that are a bit old (must not be in active_set)
        pass

    def score(self, device: Device):
        # black box for generating a in-room score for a device
        return 0