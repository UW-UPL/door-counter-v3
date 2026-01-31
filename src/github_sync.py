#!/usr/bin/env python3
"""
GitHub Sync Service

Runs every 20 seconds:
1. Exports pending devices (last 10 min) to data/pending.json
2. Pulls latest claims.json from GitHub
3. Merges any matched claims into the DB
4. Pushes updated pending.json to GitHub
"""

import json
import subprocess
import time
import os
from datetime import datetime
from device_manager import get_pending_devices, complete_device

PENDING_JSON = "./data/pending.json"
CLAIMS_JSON = "./data/claims.json"
SYNC_INTERVAL = 20  # seconds
PENDING_WINDOW = 10  # minutes


def export_pending():
    """Export pending devices to JSON for GitHub."""
    pending = get_pending_devices(minutes=PENDING_WINDOW)

    os.makedirs(os.path.dirname(PENDING_JSON), exist_ok=True)
    with open(PENDING_JSON, 'w') as f:
        json.dump({
            "exported_at": datetime.now().isoformat(),
            "window_minutes": PENDING_WINDOW,
            "pending": pending
        }, f, indent=2)

    return len(pending)


def load_claims() -> list:
    """Load claims from claims.json."""
    if not os.path.exists(CLAIMS_JSON):
        return []

    with open(CLAIMS_JSON, 'r') as f:
        data = json.load(f)

    return data.get("claims", [])


def process_claims():
    """Match claims against DB and complete devices."""
    claims = load_claims()
    processed = 0

    for claim in claims:
        passkey = claim.get("passkey")
        paired_at = claim.get("paired_at")
        name = claim.get("name")
        sound_file = claim.get("sound_file")

        if not all([passkey, paired_at, name, sound_file]):
            print(f"Skipping incomplete claim: {claim}")
            continue

        if complete_device(passkey, paired_at, name, sound_file):
            print(f"Completed device: {name} ({passkey})")
            processed += 1

    return processed


def git_pull():
    """Pull latest from GitHub."""
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
    """Commit and push pending.json to GitHub."""
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

    # Pull latest (gets new claims)
    if git_pull():
        # Process any new claims
        processed = process_claims()
        if processed:
            print(f"Processed {processed} claim(s)")

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
    print(f"  Claims file: {CLAIMS_JSON}")

    while True:
        try:
            sync_cycle()
        except Exception as e:
            print(f"Sync error: {e}")

        time.sleep(SYNC_INTERVAL)


if __name__ == "__main__":
    main()
