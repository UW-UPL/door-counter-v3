import asyncio
from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, Optional, Callable
from bleak import BleakScanner
from device_manager import get_tracked_devices, get_device_by_mac


class BLEDetective:
    def __init__(self, rssi_window_size: int = 10, spike_threshold: int = 15,
                 entry_cooldown: int = 300):
        self.rssi_window_size = rssi_window_size
        self.spike_threshold = spike_threshold
        self.entry_cooldown = entry_cooldown

        # Track recent RSSI history for each device
        self.rssi_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=rssi_window_size)
        )

        self.last_entry_time: Dict[str, datetime] = {}
        self.in_upl: set = set()
        self.on_entry_callback: Optional[Callable] = None

    def set_entry_callback(self, callback: Callable[[str, Optional[Dict]], None]):
        self.on_entry_callback = callback

    def update_rssi(self, mac: str, rssi: int):
        mac = mac.upper()
        history = self.rssi_history[mac]

        # Detect spike (significant RSSI increase = person just walked in)
        if len(history) >= 3:
            avg_previous = sum(history) / len(history)
            spike = rssi - avg_previous

            if spike >= self.spike_threshold:
                self._handle_potential_entry(mac, rssi, spike)

        history.append(rssi)

    def _handle_potential_entry(self, mac: str, rssi: int, spike: float):
        now = datetime.now()

        # Check cooldown to prevent duplicate triggers
        if mac in self.last_entry_time:
            time_since_last = (now - self.last_entry_time[mac]).total_seconds()
            if time_since_last < self.entry_cooldown:
                return

        self.last_entry_time[mac] = now
        self.in_upl.add(mac)

        device = get_device_by_mac(mac)

        print(f"ENTRY DETECTED!")
        print(f"  MAC: {mac}")
        print(f"  RSSI: {rssi} dBm (spike: +{spike:.1f})")
        if device and device.get("name"):
            print(f"  Name: {device['name']}")
        print()

        if self.on_entry_callback:
            self.on_entry_callback(mac, device)

    def mark_exit(self, mac: str):
        mac = mac.upper()
        self.in_upl.discard(mac)
        print(f"EXIT: {mac}")

    def is_in_upl(self, mac: str) -> bool:
        return mac.upper() in self.in_upl


class BLEScanner:
    def __init__(self, detective: BLEDetective):
        self.detective = detective
        self.running = False

    async def start(self):
        self.running = True
        print("Starting BLE scanner...")

        def callback(device, advertisement_data):
            # Linux kernel resolves rotating MACs to Identity MAC for us
            tracked = get_tracked_devices()
            if not tracked:
                return

            tracked_by_mac = {d['mac']: d for d in tracked}
            mac = device.address.upper()
            if mac in tracked_by_mac:
                rssi = advertisement_data.rssi
                name = tracked_by_mac[mac].get('name', mac)
                print(f"[{name}] RSSI: {rssi} dBm")
                self.detective.update_rssi(mac, rssi)

        async with BleakScanner(callback) as scanner:
            print("BLE scanner active - tracking devices")
            while self.running:
                await asyncio.sleep(0.1)

    def stop(self):
        self.running = False
        print("BLE scanner stopped")
