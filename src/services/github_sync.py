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

RECOVERY_THRESHOLD = 3
_consecutive_failures = 0

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


def git_recover():
    logger.error("Git sync wedged; attempting auto-recovery")
    subprocess.run(["git", "rebase", "--abort"], capture_output=True, timeout=15)
    subprocess.run(["git", "merge", "--abort"], capture_output=True, timeout=15)
    for lock in (".git/index.lock", ".git/objects/maintenance.lock"):
        try:
            os.remove(lock)
        except OSError:
            pass
    fetched = subprocess.run(["git", "fetch", "origin", "main"],
                             capture_output=True, text=True, timeout=60)
    if fetched.returncode != 0:
        logger.error(f"Recovery fetch failed: {fetched.stderr.strip()}")
        return False
    reset = subprocess.run(["git", "reset", "--hard", "origin/main"],
                           capture_output=True, text=True, timeout=30)
    if reset.returncode != 0:
        logger.error(f"Recovery reset failed: {reset.stderr.strip()}")
        return False
    logger.log("Git sync auto-recovered to origin/main")
    return True


def sync_cycle():
    global _consecutive_failures
    logger.debug(f"Starting sync cycle...")

    # Pull latest (gets new registrations)
    pulled = git_pull()
    if pulled:
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
    pushed = git_push()

    if pulled and pushed:
        _consecutive_failures = 0
        return

    _consecutive_failures += 1
    logger.error(f"Sync degraded ({_consecutive_failures} consecutive failure(s))")
    if _consecutive_failures >= RECOVERY_THRESHOLD:
        if git_recover():
            _consecutive_failures = 0


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