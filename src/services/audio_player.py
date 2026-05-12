import os
import random
from typing import Optional, Dict
# import pygame (allegedly pygame is the most robust way to play audio w python lol)
# None of the audio features are implemented. Speakers are not hooked up to the pi

class AudioPlayer:
    def __init__(self, default_sounds_dir: str = "./sounds/default",
                 custom_sounds_dir: str = "./sounds/custom"):
        self.default_sounds_dir = default_sounds_dir
        self.custom_sounds_dir = custom_sounds_dir

        # pygame.mixer.init()
        print("Audio player initialized")

        self.default_sounds = self._load_default_sounds()

    def _load_default_sounds(self):
        if not os.path.exists(self.default_sounds_dir):
            print(f"Warning: Default sounds directory not found: {self.default_sounds_dir}")
            return []

        sounds = [
            os.path.join(self.default_sounds_dir, f)
            for f in os.listdir(self.default_sounds_dir)
            if f.endswith(('.mp3', '.wav', '.ogg'))
        ]

        print(f"Loaded {len(sounds)} default jingles")
        return sounds

    def play_entry(self, device: Optional[Dict] = None):
        sound_path = None

        # Try custom sound if device has one
        if device and device.get("sound_file"):
            custom_path = os.path.join(self.custom_sounds_dir, device["sound_file"])
            if os.path.exists(custom_path):
                sound_path = custom_path
                print(f"Playing custom jingle for {device.get('name', 'user')}")
            else:
                print(f"Warning: Custom sound not found: {custom_path}, using default")

        # Fall back to random default
        if not sound_path:
            if self.default_sounds:
                sound_path = random.choice(self.default_sounds)
                print("Playing random default jingle")
            else:
                print("Warning: No sounds available to play")
                return

        try:
            # pygame.mixer.music.load(sound_path)
            # pygame.mixer.music.play()
            print(f"  {os.path.basename(sound_path)}")
        except Exception as e:
            print(f"Failed to play sound: {e}")

    def stop(self):
        # pygame.mixer.music.stop()
        # pygame.mixer.quit()
        print("Audio player stopped")
