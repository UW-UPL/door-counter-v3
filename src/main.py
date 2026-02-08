import asyncio
import signal
import sys
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
        ble_task = asyncio.create_task(self.ble_scanner.start(), name="BLE")
        keyboard_task = asyncio.create_task(self.keyboard_listener(), name="Keyboard")

        print("All systems operational")
        print("Press 'e' + Enter to simulate entrance, 'x' + Enter to simulate exit\n")

        try:
            await asyncio.gather(ble_task, keyboard_task)
        except asyncio.CancelledError:
            print("\nShutting down...")
        finally:
            await self.shutdown()

    async def keyboard_listener(self):
        """Listen for keyboard input to simulate ToF events"""
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while self.running:
            line = await reader.readline()
            key = line.decode().strip().lower()
            if key == 'e':
                print("\n[TEST] Simulating entrance...")
                self.detective.enter()
                print(f"[TEST] tof_size={self.detective.tof_size}, active_devices={[d.name for d in self.detective.active_devices]}\n")
            elif key == 'x':
                print("\n[TEST] Simulating exit...")
                self.detective.exit()
                print(f"[TEST] tof_size={self.detective.tof_size}, active_devices={[d.name for d in self.detective.active_devices]}\n")

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
