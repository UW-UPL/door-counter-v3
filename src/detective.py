import os
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
import numpy as np
import time

class Device:
    MAX_HISTORY = 100

    def __init__(self, mac: str, name: str, sound_file: str, share_presence: bool):
        self.mac = mac
        self.name = name
        self.sound_file = sound_file
        self.share_presence = share_presence

        # [(rssi, timestamps), (rssi, timestamp), ...]
        self.history = deque()

    def add_signal(self, rssi):
        self.history.append((rssi, datetime.now()))

    def get_history(self):
        return self.history

class Detective:
    def __init__(self):
        self.lock = threading.Lock()

        # all devices that we have recently observed
        # mac -> Device()
        self.devices = {}

        # number of devices that the tof sensor thinks is in the room
        self.tof_size = 0

        # devices that the detective thinks are currently in the room
        self.active_devices = []

        self._running = True
        self._gc_thread = threading.Thread(target=self._gc_loop,  daemon=True)
        self._gc_thread.start()

    # called by ToF when it detects an entrance
    def enter(self):
        self.lock.acquire()

        self.tof_size += 1

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
        #   periodically looks at devices and tries to refactor active_devices based on up-to-date data
        
        now = datetime.now()
        best_score = 0
        candidate = None

        MIN_SCORE_THRESHOLD = 0.30

        WEIGHT_RECENCY = 0.35
        WEIGHT_TREND = 0.35
        WEIGHT_RSSI = 0.20
        WEIGHT_CONSISTENCY = 0.10

        for mac, device in self.devices.items():
            print("ANALYZING DEVICE")
            print(mac)
            if device in self.active_devices:
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
            recent_readings = list(history)[-10:]

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
            scaled = (np.mean(rssis[-3]) - (-90)) / ((-30)-(-90))
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

            print(total)
            if total > best_score and total > MIN_SCORE_THRESHOLD:
                best_score = total
                candidate = device
        
        if candidate != None:
            self.active_devices.append(candidate)

        self.lock.release()

        #  if len(self.active_devices) > self.tof_size: GC will figure it out
    
    # called by ToF when it detects an exit
    def exit(self):
        # give some time for the person to walk away
        time.sleep(5)

        self.lock.acquire()
        self.tof_size -= 1
        self.lock.release()
        # GC will figure out who to kick in a bit

    def add_sighting(self, device: Device, rssi: int):
        if device.mac in self.devices:
            device.add_signal(rssi)
        else:
            self.devices[device.mac] = device
            device.add_signal(rssi)
    
    
    def _gc_loop(self):
        #   runs every 5 seconds
        #   
        # if len(active_devices) > tof_size:
        #   score all active devices based on "in-room" confidence heuristic
        #   remove the lowest scoring devices until len == tof_size
        #
        # if len(active_devices) < tof_size:
        #   score all non-active devices
        #   promote the highest-scoring devices until len == tof_size
        #   only promote if the score > threshold, otherwise we can leave slot empty (unknown person)
        #
        # always do replace phase (even if sizes match):
        #   find the lowest scoring active device
        #   find the highest scoring non-active device (if any)
        #   if non-active score >> actiive score, swap them
        #   this handles when wrong devices are added or a better candidate emerged

        pass