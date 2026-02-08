import asyncio
from bleak import BleakScanner
from device_manager import get_tracked_devices
from detective import Detective
from detective import Device

CACHE_REFRESH_INTERVAL = 5  # seconds


class BLEScanner:
    def __init__(self, detective: Detective):
        self.running = False
        self.detective = detective
        
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

        async with BleakScanner(callback) as scanner:
            print("BLE scanner active - tracking devices")
            while self.running:
                await asyncio.sleep(0.1)

        refresh_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass

    def stop(self):
        self.running = False
        print("BLE scanner stopped")
