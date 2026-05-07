import threading

COLORS = {
    "ble_scanner": "\033[96m",
    "bt_service": "\033[94m",
    "github_sync": "\033[95m",
    "occupancy": "\033[93m",
    "detective": "\033[92m",
    "gc": "\033[92m",
    "main": "\033[97m",
}

RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
DIM = "\033[2m"


def _get_prefix():
    name = threading.current_thread().name
    color = COLORS[name]
    return f"{color}[{name}]{RESET}"


def log(msg):
    print(f"{_get_prefix()} {msg}", flush=True)


def error(msg):
    print(f"{_get_prefix()} {RED}ERROR: {msg}{RESET}", flush=True)


def warn(msg):
    print(f"{_get_prefix()} {YELLOW}WARNING: {msg}{RESET}", flush=True)


def debug(msg):
    print(f"{_get_prefix()} {DIM}{msg}{RESET}", flush=True)
