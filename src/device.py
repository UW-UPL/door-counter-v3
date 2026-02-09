import threading
from collections import deque
from datetime import datetime


class Device:
    MAX_HISTORY = 100

    def __init__(self, mac: str, name: str, sound_file: str, share_presence: bool):
        self.mac = mac
        self.name = name
        self.sound_file = sound_file
        self.share_presence = share_presence

        self.lock = threading.Lock()

        # [(rssi, timestamp), (rssi, timestamp), ...]
        self.history = deque()

    def add_signal(self, rssi):
        with self.lock:
            self.history.append((rssi, datetime.now()))
            # Keep history bounded
            if len(self.history) > self.MAX_HISTORY:
                self.history.popleft()

    def get_history(self):
        with self.lock:
            return list(self.history)
