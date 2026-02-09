import asyncio
import threading
from bleak import BleakScanner
from device_manager import get_tracked_devices
from detective import Detective, Device

CACHE_REFRESH_INTERVAL = 5  # seconds


class BLEScanner:
    def __init__(self, detective: Detective, shutdown_event: threading.Event):
        self.running = False
        self.detective = detective
        self.shutdown_event = shutdown_event

        # all tracked devices from database
        # refreshes periodically
        self.devices = {}

    async def refresh_cache(self):
        """periodically refresh cache from database"""
        while self.running:
            tracked = get_tracked_devices()
            for d in tracked:
                mac = d["mac"]
                if mac in self.devices:
                    device = self.devices[mac]
                    device.name = d["name"]
                    device.sound_file = d["sound_file"]
                    device.share_presence = d["share_presence"]
                else:
                    self.devices[mac] = Device(mac, d["name"], d["sound_file"], d["share_presence"])
            await asyncio.sleep(CACHE_REFRESH_INTERVAL)

    async def start(self):
        self.running = True
        print("Starting BLE scanner...")

        def callback(ble_device, advertisement_data):
            mac = ble_device.address.upper()
            if mac in self.devices:
                print(self.devices[mac].name, advertisement_data.rssi)
                self.detective.add_sighting(self.devices[mac], advertisement_data.rssi)

        refresh_task = asyncio.create_task(self.refresh_cache())

        try:
            async with BleakScanner(callback):
                print("BLE scanner active - tracking devices")
                while self.running and not self.shutdown_event.is_set():
                    await asyncio.sleep(0.1)
        finally:
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass
            print("BLE scanner stopped")


def main(detective: Detective, shutdown_event: threading.Event):
    scanner = BLEScanner(detective, shutdown_event)
    asyncio.run(scanner.start())
