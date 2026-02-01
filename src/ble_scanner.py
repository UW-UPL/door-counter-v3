import asyncio
from bleak import BleakScanner
from device_manager import get_tracked_devices

CACHE_REFRESH_INTERVAL = 5  # seconds


class BLEScanner:
    def __init__(self):
        self.running = False
        self.tracked_by_mac = {}

    async def refresh_cache(self):
        """Periodically refresh the tracked devices cache."""

        while self.running:
            tracked = get_tracked_devices()
            self.tracked_by_mac = {d['mac']: d for d in tracked}

            await asyncio.sleep(CACHE_REFRESH_INTERVAL)

    async def start(self):
        self.running = True
        print("Starting BLE scanner...")
        
        def callback(device, advertisement_data):
            mac = device.address.upper()
            if mac in self.tracked_by_mac:
                rssi = advertisement_data.rssi
                name = self.tracked_by_mac[mac].get('name', mac)
                print(f"[{name}] RSSI: {rssi} dBm")

        # Start cache refresh task
        refresh_task = asyncio.create_task(self.refresh_cache())

        async with BleakScanner(callback) as scanner:
            print("BLE scanner active - tracking devices")
            while self.running:
                await asyncio.sleep(0.1)

        refresh_task.cancel()

    def stop(self):
        self.running = False
        print("BLE scanner stopped")
