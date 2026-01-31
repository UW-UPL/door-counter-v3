#!/usr/bin/env python3
"""
GitHub Sync Service

Runs every 20 seconds:
1. Exports pending devices (last 10 min) to data/pending.json
2. Pulls latest registrations.json from GitHub
3. Merges any matched registrations into the DB
4. Pushes updated pending.json to GitHub
"""

import json
import subprocess
import time
import os
from datetime import datetime
from device_manager import get_pending_devices, complete_device

PENDING_JSON = "./data/pending.json"
REGISTRATIONS = "./data/registrations.json"
SYNC_INTERVAL = 20  # seconds
PENDING_WINDOW = 10  # minutes

def export_pending():
    pending = get_pending_devices(minutes=PENDING_WINDOW)

    os.makedirs(os.path.dirname(PENDING_JSON), exist_ok=True)
    with open(PENDING_JSON, 'w') as f:
        json.dump(pending, f, indent=2)

    return len(pending)


def load_registrations() -> list:
    if not os.path.exists(REGISTRATIONS):
        return []

    with open(REGISTRATIONS, 'r') as f:
        data = json.load(f)

    return data.get("registrations", [])


def process_registrations():
    registrations = load_registrations()
    processed = 0

    for reg in registrations:
        passkey = reg.get("passkey")
        paired_at = reg.get("paired_at")
        name = reg.get("name")
        sound_file = reg.get("sound_file")

        if not all([passkey, paired_at, name, sound_file]):
            print(f"Skipping incomplete registration: {reg}")
            continue

        if complete_device(passkey, paired_at, name, sound_file):
            print(f"Completed device: {name} ({passkey})")
            processed += 1

    return processed


def git_pull():
    try:
        result = subprocess.run(
            ["git", "pull", "--rebase"],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            print(f"Git pull failed: {result.stderr}")
            return False
        return True
    except Exception as e:
        print(f"Git pull error: {e}")
        return False


def git_push():
    try:
        # Stage pending.json
        subprocess.run(["git", "add", PENDING_JSON], check=True, timeout=10)

        # Check if there are changes to commit
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            capture_output=True
        )

        if result.returncode == 0:
            # No changes to commit
            return True

        # Commit
        subprocess.run(
            ["git", "commit", "-m", "Update pending devices"],
            check=True,
            timeout=10
        )

        # Push
        subprocess.run(["git", "push"], check=True, timeout=30)
        return True

    except subprocess.CalledProcessError as e:
        print(f"Git push failed: {e}")
        return False
    except Exception as e:
        print(f"Git error: {e}")
        return False


def sync_cycle():
    """Run one sync cycle."""
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Starting sync cycle...")

    # Pull latest (gets new registrations)
    if git_pull():
        # Process any new registrations
        processed = process_registrations()
        if processed:
            print(f"Processed {processed} registration(s)")

    # Export current pending devices
    count = export_pending()
    print(f"Exported {count} pending device(s)")

    # Push updates
    git_push()


def main():
    print("GitHub Sync Service started")
    print(f"  Sync interval: {SYNC_INTERVAL}s")
    print(f"  Pending window: {PENDING_WINDOW} minutes")
    print(f"  Pending file: {PENDING_JSON}")
    print(f"  Registrations file: {REGISTRATIONS}")

    while True:
        try:
            sync_cycle()
        except Exception as e:
            print(f"Sync error: {e}")

        time.sleep(SYNC_INTERVAL)


if __name__ == "__main__":
    main()
