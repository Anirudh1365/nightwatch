"""config.py -- all settings come from environment variables."""
import os
from zoneinfo import ZoneInfo

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./nightwatch.db")
# MySQL example: mysql+mysqlconnector://root:password@localhost:3306/nightwatch

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
TZ = ZoneInfo(os.getenv("CAMPUS_TZ", "Asia/Dubai"))

QR_SLOT_SECONDS = int(os.getenv("QR_SLOT_SECONDS", "15"))   # QR changes this often
ENFORCE_NETWORK = os.getenv("ENFORCE_NETWORK", "true").lower() == "true"
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")   # used in parent email links

SPOT_CHECKS_PER_NIGHT = int(os.getenv("SPOT_CHECKS_PER_NIGHT", "5"))
FLAG_WINDOW_NIGHTS = 14
FLAG_MAX_ABSENCES = 3
FLAG_MAX_MANUAL = 5
