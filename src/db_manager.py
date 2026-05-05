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
            completed_at DATETIME
        )
    """) # goofy ahh sqlite doesn't have boolean types

    conn.execute("DROP TABLE IF EXISTS tof_count")
    conn.execute("""
        CREATE TABLE tof_count (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            count INTEGER NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO tof_count (id, count, updated_at) VALUES (1, 0, ?)",
        (datetime.now().isoformat(),),
    )

    conn.commit()

def set_tof_count(count: int):
    """update the tof_size count"""
    conn = _get_connection()
    conn.execute(
        "UPDATE tof_count SET count = ?, updated_at = ? WHERE id = 1",
        (count, datetime.now().isoformat()),
    )
    conn.commit()

def get_tof_count() -> int:
    """returns the current tof count"""
    conn = _get_connection()
    cursor = conn.execute("SELECT count FROM tof_count WHERE id = 1")
    row = cursor.fetchone()
    return row["count"] if row else 0

def get_in_room() -> List[str]:
    """returns the names currently in the room"""
    conn = _get_connection()
    try:
        cursor = conn.execute("SELECT name FROM in_room")
        return [row["name"] for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        return []

def set_in_room(names: List[str]):
    """replace the in_room table with the given names"""
    conn = _get_connection()
    conn.execute("DROP TABLE IF EXISTS in_room")
    conn.execute("""
        CREATE TABLE in_room (
            name TEXT NOT NULL
        )
    """)
    conn.executemany(
        "INSERT INTO in_room (name) VALUES (?)",
        [(name,) for name in names],
    )
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
        WHERE name IS NOT NULL
    """)

    return [dict(row) for row in cursor.fetchall()]

def get_pending_devices(minutes: int = 10) -> List[Dict]:
    """get pending devices (within the last n minutes)"""
    conn = _get_connection()
    cursor = conn.execute("""
        SELECT passkey, paired_at
        FROM devices
        WHERE name IS NULL
        AND paired_at > datetime('now', 'localtime', ? || ' minutes')
    """, (f"-{minutes}",))
    
    return [dict(row) for row in cursor.fetchall()]

def complete_device(passkey: str, paired_at: str, name: str, sound_file: str | None, share_presence: bool) -> bool:
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

