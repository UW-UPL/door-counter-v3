import asyncio
import signal
import yaml
from ble_scanner import BLEDetective, BLEScanner
from audio_player import AudioPlayer
from device_manager import get_tracked_devices, get_device_by_mac


class UPLJingleSystem:
    def __init__(self, config_path: str = "./config.yaml"):
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)

        self.audio_player = AudioPlayer(
            default_sounds_dir=self.config.get('audio', {}).get('default_sounds_dir', './sounds/default'),
            custom_sounds_dir=self.config.get('audio', {}).get('custom_sounds_dir', './sounds/custom')
        )

        self.ble_detective = BLEDetective(
            rssi_window_size=self.config.get('ble', {}).get('rssi_window_size', 10),
            spike_threshold=self.config.get('ble', {}).get('spike_threshold', 15),
            entry_cooldown=self.config.get('ble', {}).get('entry_cooldown', 300)
        )

        self.ble_scanner = BLEScanner(self.ble_detective)

        # Wire up callback
        self.ble_detective.set_entry_callback(self._on_entry_detected)

        self.running = False

    def _on_entry_detected(self, mac: str, device: dict):
        if device and device.get("name"):
            print(f"Identified: {device['name']}")
        else:
            print(f"Identified: {mac}")

        # TODO: Only play when ToF sensor confirms door entry
        # self.audio_player.play_entry(device)

    async def run(self):
        self.running = True

        print("\n" + "="*50)
        print("UPL Door Jingle System Starting...")
        print("="*50 + "\n")

        # Show tracked devices
        tracked_devices = get_tracked_devices()
        if tracked_devices:
            print(f"Tracking {len(tracked_devices)} opted-in device(s):")
            for device in tracked_devices:
                print(f"  - {device['name']} ({device['mac']})")
            print()
        else:
            print("No opted-in devices to track (need name + sound_file set)\n")

        # Start BLE scanner
        task = asyncio.create_task(self.ble_scanner.start(), name="BLE")

        print("All systems operational\n")

        try:
            await task
        except asyncio.CancelledError:
            print("\nShutting down...")
        finally:
            await self.shutdown()

    async def shutdown(self):
        print("Cleaning up...")
        self.ble_scanner.stop()
        self.audio_player.stop()
        print("Shutdown complete")


def main():
    system = UPLJingleSystem()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def signal_handler(sig, frame):
        print("\nInterrupt received...")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        loop.run_until_complete(system.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
        print("\nGoodbye")


if __name__ == "__main__":
    main()
