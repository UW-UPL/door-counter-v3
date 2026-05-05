import asyncio
import threading
from bleak import BleakScanner
from db_manager import get_tracked_devices
from device import Device
import logger
from detective import Detective

CACHE_REFRESH_INTERVAL = 5  # seconds


class BLEScanner:
    def __init__(self, shutdown_event: threading.Event):
        self.running = False
        self.shutdown_event = shutdown_event
        self.detective = Detective(self, shutdown_event)

        # all tracked devices from database
        # refreshes periodically
        self.lock = threading.Lock()
        self.devices = {}

    
    # periodically refresh from database
    async def refresh_cache(self):
        while self.running:
            tracked = get_tracked_devices()
            tracked_macs = set(d["mac"] for d in tracked)

            with self.lock:
                # Update existing / add new devices
                for d in tracked:
                    mac = d["mac"]
                    if mac in self.devices:
                        device = self.devices[mac]
                        device.name = d["name"]
                        device.sound_file = d["sound_file"]
                        device.share_presence = d["share_presence"]
                    else:
                        self.devices[mac] = Device(mac, d["name"], d["sound_file"], d["share_presence"])

                # Remove devices no longer in database
                removed_macs = set(self.devices.keys()) - tracked_macs
                for mac in removed_macs:
                    del self.devices[mac]
                    logger.log(f"Removed device {mac} from tracking")

            await asyncio.sleep(CACHE_REFRESH_INTERVAL)

    async def start(self):
        self.running = True
        logger.log("Starting BLE scanner...")

        def callback(ble_device, advertisement_data):
            mac = ble_device.address.upper()
            with self.lock:
                device = self.devices.get(mac)

            if device is not None:
                logger.debug(f"{device.name} {advertisement_data.rssi}")
                device.add_signal(advertisement_data.rssi)

        refresh_task = asyncio.create_task(self.refresh_cache())

        try:
            async with BleakScanner(callback):
                logger.log("BLE scanner active - tracking devices")
                while self.running and not self.shutdown_event.is_set():
                    await asyncio.sleep(0.5)
        finally:
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass
            logger.log("BLE scanner stopped")


def main(shutdown_event: threading.Event, detective_holder: list):
    scanner = BLEScanner(shutdown_event)
    detective_holder[0] = scanner.detective
    asyncio.run(scanner.start())
