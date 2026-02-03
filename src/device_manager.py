import sqlite3
import os
from datetime import datetime
from typing import List, Dict
import threading

#   There is a devices sqlite database, pending.json, and registration.json 
#
#   When a new device pairs -> add to devices table (with empty name and sound_file)
#   Devices with missing fields are "pending devices"
#
#   Every 20 seconds, we export the last 10 minutes of "pending devices" to a json
#   and push to github
#
#   Users makes PRs to registration.json with their (passkey, timestamp) as a primary key,
#   along with additional info such as name and sound_file
#
#   We poll the changes to registration.json. When a PR is accepted,  complete_device() 
#   is called on new entries in registration.json -> fills in missing fields
#   The device is now "tracked" in the database

DB_PATH = "./data/devices.db"
_local = threading.local()

def _get_connection() -> sqlite3.Connection:
    if not hasattr(_local, "connection"):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _local.connection = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.connection.row_factory = sqlite3.Row
        _init_db(_local.connection)
    
    return _local.connection


def _init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mac TEXT UNIQUE NOT NULL,
            passkey TEXT NOT NULL,
            paired_at DATETIME NOT NULL,
            name TEXT,
            share_presence INTEGER DEFAULT 0,
            sound_file TEXT,
            show_location INTEGER DEFAULT 0,
            completed_at DATETIME
        )
    """) # goofy ahh sqlite doesn't have boolean types

    conn.commit()

def add_pending_device(mac_address: str, passkey: str):
    """stores a newly paired device"""

    conn = _get_connection()
    now = datetime.now().isoformat()

    conn.execute("""
        INSERT INTO devices (mac, passkey, paired_at)
        VALUES (?, ?, ?)
        ON CONFLICT(mac) DO UPDATE SET
            passkey = excluded.passkey,
            paired_at = excluded.paired_at
    """, (mac_address.upper(), passkey, now))

    conn.commit()

def get_tracked_devices() -> List[Dict]:
    """returns tracked devices"""

    conn = _get_connection()
    cursor = conn.execute("""
        SELECT mac, name, sound_file, share_presence FROM devices
        WHERE name IS NOT NULL AND sound_file IS NOT NULL
    """)

    return [dict(row) for row in cursor.fetchall()]

def get_pending_devices(minutes: int = 10) -> List[Dict]:
    """get pending devices (within the last n minutes)"""
    conn = _get_connection()
    cursor = conn.execute("""
        SELECT passkey, paired_at
        FROM devices
        WHERE name IS NULL
        AND paired_at > datetime('now', ? || ' minutes')
    """, (f"-{minutes}",))
    
    return [dict(row) for row in cursor.fetchall()]

def complete_device(passkey: str, paired_at: str, name: str, sound_file: str, share_presence: bool) -> bool:
    """turns a device from pending -> tracked"""
    if share_presence is True:
        sp = 1 
    else: 
        sp = 0
    
    conn = _get_connection()
    cursor = conn.execute("""
        UPDATE devices
        SET name = ?, sound_file = ?, completed_at = ?, share_presence = ?
        WHERE passkey = ? AND paired_at = ? AND name IS NULL
    """, (name, sound_file, datetime.now().isoformat(), sp, passkey, paired_at))
    conn.commit()

    return cursor.rowcount > 0

