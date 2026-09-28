"""auth.py -- password hashing + signed login tokens + role checks."""
import bcrypt
from fastapi import Depends, Header, HTTPException
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy.orm import Session

from config import SECRET_KEY
from db import get_db
from models import Student, Staff

_signer = URLSafeTimedSerializer(SECRET_KEY, salt="login")
TOKEN_MAX_AGE = 12 * 3600


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def check_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def make_token(kind: str, user_id: int) -> str:
    return _signer.dumps({"k": kind, "id": user_id})


def _read_token(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not logged in")
    try:
        return _signer.loads(authorization[7:], max_age=TOKEN_MAX_AGE)
    except SignatureExpired:
        raise HTTPException(401, "Session expired, log in again")
    except BadSignature:
        raise HTTPException(401, "Invalid session")


def current_student(authorization: str | None = Header(None),
                    db: Session = Depends(get_db)) -> Student:
    data = _read_token(authorization)
    if data["k"] != "student":
        raise HTTPException(403, "Students only")
    s = db.get(Student, data["id"])
    if not s:
        raise HTTPException(401, "Account not found")
    return s


def staff_with(*roles: str):
    """Dependency: logged-in staff member whose role is in `roles`."""
    def dep(authorization: str | None = Header(None),
            db: Session = Depends(get_db)) -> Staff:
        data = _read_token(authorization)
        if data["k"] != "staff":
            raise HTTPException(403, "Staff only")
        st = db.get(Staff, data["id"])
        if not st or st.role not in roles:
            raise HTTPException(403, "Not allowed for your role")
        return st
    return dep


def require_block_access(staff: Staff, block_id: int):
    """Chief warden sees every block; everyone else only their own."""
    if staff.role != "chief" and staff.block_id != block_id:
        raise HTTPException(403, "You can only access your own block")
