"""night.py -- time windows, rotating QR codes, network check, spot checks."""
import hashlib
import hmac
import ipaddress
import random
import time
from datetime import datetime, date, timedelta

from config import TZ, SECRET_KEY, QR_SLOT_SECONDS, CAMPUS_NETWORKS
from models import Block


def now() -> datetime:
    return datetime.now(TZ)


def night_of(moment: datetime) -> date:
    """A 'night' runs from noon to noon, so 00:40 on the 2nd belongs to the 1st."""
    return (moment - timedelta(hours=12)).date()


def _at(d: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


def window(block: Block, night: date) -> tuple[datetime, datetime]:
    start = _at(night, block.window_start)
    end = _at(night, block.window_end)
    if end <= start:                 # e.g. 23:30 -> 00:00 crosses midnight
        end += timedelta(days=1)
    return start, end


def classify(block: Block, moment: datetime) -> tuple[date, str]:
    """Returns (night, 'too_early' | 'on_time' | 'late')."""
    night = night_of(moment)
    start, end = window(block, night)
    if moment < start:
        return night, "too_early"
    return night, "on_time" if moment < end else "late"


# ---------- rotating QR shown on the reception screen ----------
def _sign(msg: str) -> str:
    return hmac.new(SECRET_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()[:20]


def qr_token(block_id: int, at: float | None = None) -> tuple[str, int]:
    """Token for the current slot, plus seconds until it changes."""
    t = time.time() if at is None else at
    slot = int(t // QR_SLOT_SECONDS)
    remaining = QR_SLOT_SECONDS - int(t % QR_SLOT_SECONDS)
    return f"{block_id}.{slot}.{_sign(f'qr:{block_id}:{slot}')}", remaining


def qr_valid(token: str, block_id: int, at: float | None = None) -> bool:
    """Accepts the current slot and the one before (covers scanning delay)."""
    try:
        b, slot, sig = token.split(".")
        b, slot = int(b), int(slot)
    except ValueError:
        return False
    if b != block_id:
        return False
    t = time.time() if at is None else at
    current = int(t // QR_SLOT_SECONDS)
    if slot not in (current, current - 1):
        return False
    return hmac.compare_digest(sig, _sign(f"qr:{block_id}:{slot}"))


# ---------- campus Wi-Fi check (done by the server, not the phone) ----------
def on_campus_network(client_ip: str | None, networks: list[str] = CAMPUS_NETWORKS) -> bool:
    """True if the phone's connection comes from the campus Wi-Fi, not mobile data."""
    if not client_ip:
        return False
    try:
        ip = ipaddress.ip_address(client_ip)
        return any(ip in ipaddress.ip_network(n, strict=False) for n in networks)
    except ValueError:
        return False


# ---------- random spot checks: unpredictable to students, fixed per night ----------
def pick_spot_checks(student_ids: list[int], block_id: int, night: date, k: int) -> list[int]:
    seed = _sign(f"spot:{block_id}:{night.isoformat()}")
    rng = random.Random(seed)
    ids = sorted(student_ids)
    return sorted(rng.sample(ids, min(k, len(ids))))
