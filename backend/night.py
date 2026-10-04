"""night.py -- time windows, rotating QR codes, network check, spot checks."""
import hashlib
import hmac
import ipaddress
import random
import time
from datetime import datetime, date, timedelta

from config import TZ, SECRET_KEY, QR_SLOT_SECONDS, CAMPUS_NETWORKS, CLOCK_OFFSET_SECONDS, EARLY_WINDOW
from models import Block


def now() -> datetime:
    """Campus time (shifted by the demo clock, if one is set)."""
    return datetime.now(TZ) + timedelta(seconds=CLOCK_OFFSET_SECONDS)


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


def windows(block: Block, night: date) -> list[tuple[datetime, datetime]]:
    """All check-in windows for the night, in order: the early one (if set), then the block's main one."""
    wins = []
    if EARLY_WINDOW:
        s, e = EARLY_WINDOW.split("-")
        start, end = _at(night, s.strip()), _at(night, e.strip())
        if end <= start:
            end += timedelta(days=1)
        wins.append((start, end))
    wins.append(window(block, night))
    return wins


def classify(block: Block, moment: datetime) -> tuple[date, str]:
    """Returns (night, 'too_early' | 'on_time' | 'between' | 'late').
    'between' = after the early window closed, before the main one opens."""
    night = night_of(moment)
    wins = windows(block, night)
    if moment >= wins[-1][1]:
        return night, "late"
    if any(s <= moment < e for s, e in wins):
        return night, "on_time"
    return night, "too_early" if moment < wins[0][0] else "between"


def next_opening(block: Block, moment: datetime) -> datetime | None:
    """Start of the next check-in window after this moment, if any tonight."""
    return next((s for s, _ in windows(block, night_of(moment)) if moment < s), None)


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
