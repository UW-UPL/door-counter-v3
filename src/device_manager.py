import json
import os
from datetime import datetime
from typing import List, Dict, Optional
import threading

DEVICES_FILE = "./data/devices.json"
_lock = threading.Lock()


def _load_devices() -> Dict:
    if not os.path.exists(DEVICES_FILE):
        return {"devices": []}
    with open(DEVICES_FILE, 'r') as f:
        return json.load(f)


def _save_devices(data: Dict):
    os.makedirs(os.path.dirname(DEVICES_FILE), exist_ok=True)
    with open(DEVICES_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def store_device(mac_address: str, passkey: str):
    with _lock:
        data = _load_devices()

        # Update if exists, otherwise add new
        for device in data["devices"]:
            if device["mac"].upper() == mac_address.upper():
                device["passkey"] = passkey
                device["timestamp"] = datetime.now().isoformat()
                _save_devices(data)
                return

        new_device = {
            "mac": mac_address.upper(),
            "passkey": passkey,
            "timestamp": datetime.now().isoformat(),
            "name": None,
            "sound_file": None
        }
        data["devices"].append(new_device)
        _save_devices(data)


def load_tracked_macs() -> List[str]:
    # Only track devices that have completed opt-in (name + sound_file set)
    with _lock:
        data = _load_devices()
        return [
            device["mac"].upper()
            for device in data["devices"]
            if device.get("name") and device.get("sound_file")
        ]


def get_device_by_mac(mac_address: str) -> Optional[Dict]:
    with _lock:
        data = _load_devices()
        for device in data["devices"]:
            if device["mac"].upper() == mac_address.upper():
                return device
        return None
