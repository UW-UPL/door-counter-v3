import asyncio
import signal
import yaml
from ble_scanner import BLEScanner
from audio_player import AudioPlayer
from device_manager import get_tracked_devices
from detective import Detective

class UPLJingleSystem:
    def __init__(self, config_path: str = "./config.yaml"):
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f) or {}

        self.audio_player = AudioPlayer(
            default_sounds_dir=self.config.get('audio', {}).get('default_sounds_dir', './sounds/default'),
            custom_sounds_dir=self.config.get('audio', {}).get('custom_sounds_dir', './sounds/custom')
        )
        self.detective = Detective()
        self.ble_scanner = BLEScanner(self.detective)

        self.running = False

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
