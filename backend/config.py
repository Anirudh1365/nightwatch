"""config.py -- all settings come from environment variables."""
import os
from zoneinfo import ZoneInfo

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./nightwatch.db")
# MySQL example: mysql+mysqlconnector://root:password@localhost:3306/nightwatch

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
TZ = ZoneInfo(os.getenv("CAMPUS_TZ", "Asia/Dubai"))

QR_SLOT_SECONDS = int(os.getenv("QR_SLOT_SECONDS", "15"))   # QR changes this often
# Demo clock: shift the server's idea of "now" so a daytime demo can happen "at 23:45".
# Set by `python run_demo.py --pretend 23:45`. 0 = real time.
CLOCK_OFFSET_SECONDS = int(os.getenv("CLOCK_OFFSET_SECONDS", "0"))
ENFORCE_NETWORK = os.getenv("ENFORCE_NETWORK", "true").lower() == "true"
# Address ranges that count as "on campus Wi-Fi". Check-in from anywhere else (mobile data,
# home Wi-Fi) is refused. BITS-Student gave us 10.30.64.0/19, and hostel blocks were checked
# to also get 10.30.x addresses (29 Sep 2026); /16 covers them all.
# If the server ever runs in the cloud (e.g. next to the ERP), put the campus's public IP here.
CAMPUS_NETWORKS = [n.strip() for n in
                   os.getenv("CAMPUS_NETWORKS", "10.30.0.0/16").split(",") if n.strip()]
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")   # used in parent email links

SPOT_CHECKS_PER_NIGHT = int(os.getenv("SPOT_CHECKS_PER_NIGHT", "5"))
FLAG_WINDOW_NIGHTS = 14
FLAG_MAX_ABSENCES = 3
FLAG_MAX_MANUAL = 5
