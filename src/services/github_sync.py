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
import threading
import time
import os
from datetime import datetime
from services import logger
from services.db_manager import get_pending_devices, complete_device, get_tof_count, get_in_room

PENDING_JSON = "./data/pending.json"
COUNT_JSON = "./data/count.json"
REGISTRATIONS = "./data/registrations.json"
SYNC_INTERVAL = 20  # seconds
PENDING_WINDOW = 10  # minutes

def export_pending():
    pending = get_pending_devices(minutes=PENDING_WINDOW)

    os.makedirs(os.path.dirname(PENDING_JSON), exist_ok=True)
    with open(PENDING_JSON, 'w') as f:
        json.dump(pending, f, indent=2)

    return len(pending)


def export_count():
    count = max(get_tof_count(), 0)
    names = get_in_room()

    os.makedirs(os.path.dirname(COUNT_JSON), exist_ok=True)
    with open(COUNT_JSON, 'w') as f:
        json.dump({"count": count, "names": names}, f, indent=2)

    return count


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
        sound_file = reg.get("sound_file", None)
        share_presence = reg.get("share_presence", False)

        if not all([passkey, paired_at, name]):
            logger.warn(f"Skipping incomplete registration: {reg}")
            continue

        if complete_device(passkey, paired_at, name, sound_file, share_presence):
            logger.log(f"Completed device: {name} ({passkey})")
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
            logger.error(f"Git pull failed: {result.stderr}")
            return False
        return True
    except Exception as e:
        logger.error(f"Git pull error: {e}")
        return False


def git_push():
    try:
        # Stage pending.json and count.json
        subprocess.run(["git", "add", PENDING_JSON, COUNT_JSON], check=True, timeout=10)

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
            ["git", "commit", "-m", f"Update pending devices ({datetime.now().strftime('%b %d, %I:%M %p')})"],
            check=True,
            timeout=10
        )

        # Push
        subprocess.run(["git", "push"], check=True, timeout=30)
        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Git push failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Git error: {e}")
        return False


def sync_cycle():
    logger.debug(f"Starting sync cycle...")

    # Pull latest (gets new registrations)
    if git_pull():
        # Process any new registrations
        processed = process_registrations()
        if processed:
            logger.log(f"Processed {processed} registration(s)")

    # Export current pending devices
    count = export_pending()
    logger.debug(f"Exported {count} pending device(s)")

    # Export current tof count
    tof = export_count()
    logger.debug(f"Exported tof count: {tof}")

    # Push updates
    git_push()


def main(shutdown_event: threading.Event):
    logger.log("GitHub Sync Service started")
    logger.log(f"  Sync interval: {SYNC_INTERVAL}s, Pending window: {PENDING_WINDOW} min")

    while not shutdown_event.is_set():
        try:
            sync_cycle()
        except Exception as e:
            logger.error(f"Sync error: {e}")

        shutdown_event.wait(timeout=SYNC_INTERVAL)

    logger.log("GitHub sync stopped")