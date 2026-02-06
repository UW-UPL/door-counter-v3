import os
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, auto
import numpy as np
import matplotlib.pyplot as plt


class State(Enum):
    ABSENT = auto()
    PRESENT = auto()
    WEAK = auto()

class Device:
    MAX_HISTORY = 100

    # Presence detection tuning
    ABSENCE_TIMEOUT = 30
    PRESENCE_CHECK_INTERVAL = 10
    THRESHOLD_DBM = -60

    def __init__(self, mac: str, name: str, sound_file: str, share_presence: bool):
        self.mac = mac
        self.name = name
        self.sound_file = sound_file
        self.share_presence = share_presence

        # Presence detection state
        self.state = State.ABSENT

        # [(rssi, timestamps), (rssi, timestamp), ...]
        self.history = deque()

        # presence monitoring thread
        self._stop_event = threading.Event()
        self._presence_thread = threading.Thread(target=self._monitor_presence, daemon=True)
        self._presence_thread.start()

    def _monitor_presence(self):
        while not self._stop_event.is_set():
            if self.history:
                now = datetime.now()
                if (now - self.history[-1][1]).total_seconds() > self.ABSENCE_TIMEOUT:
                    if self.state != State.ABSENT:
                        print("ABSENT " + str(-1))
                        self.state = State.ABSENT
                
            self._stop_event.wait(self.PRESENCE_CHECK_INTERVAL)

    def stop(self):
        self._stop_event.set()
        self._presence_thread.join()

    def add_sighting(self, rssi: int):
        now = datetime.now()
        self.history.append((rssi, now))

        if rssi > self.THRESHOLD_DBM:
            print("PRESENT: " + str(rssi))
            self.state = State.PRESENT
        else:
            self.state = State.WEAK
            print("WEAK: " + str(rssi))

        # Plot history as line graph
        # if len(self.history) > 0:
        #     rssi_values = np.array([h[0] for h in self.history])
        #     timestamps = [h[1] for h in self.history]
        #     plt.clf()
        #     plt.plot(timestamps, rssi_values, 'b-')
        #     plt.xlabel('Time')
        #     plt.ylabel('RSSI (dBm)')
        #     plt.title(f'RSSI History - {self.name}')
        #     plt.axhline(y=self.THRESHOLD_DBM, color='r', linestyle='--', label='Threshold')
        #     plt.gcf().autofmt_xdate()
        #     plt.legend()
        #     plt.pause(0.01)
        #     plt.draw()
