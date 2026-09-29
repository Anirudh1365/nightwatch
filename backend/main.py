"""main.py -- NightWatch API.

Run:  uvicorn main:app --reload
Docs: http://127.0.0.1:8000/docs
"""
import hashlib
import os
import secrets
from datetime import date, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, func, Integer as Integer_
from sqlalchemy.orm import Session

import night as nt
from auth import (check_password, current_student, make_token, staff_with,
                  require_block_access)
from config import (ENFORCE_NETWORK, CAMPUS_NETWORKS, BASE_URL, SPOT_CHECKS_PER_NIGHT,
                    FLAG_WINDOW_NIGHTS, FLAG_MAX_ABSENCES, FLAG_MAX_MANUAL)
from db import Base, engine, get_db
from models import Attendance, Block, GatePass, Outbox, Staff, Student

Base.metadata.create_all(engine)
app = FastAPI(title="NightWatch - Hostel Night Attendance")

MANUAL_REASONS = {"asleep", "sick", "phone_issue", "other"}


def local_now_naive() -> datetime:
    return nt.now().replace(tzinfo=None)


def send_email(db: Session, to: str, subject: str, body: str):
    """Demo: store in outbox + print. Swap for SMTP/Mailtrap later."""
    db.add(Outbox(to=to, subject=subject, body=body))
    print(f"\n--- EMAIL to {to}: {subject}\n{body}\n---")


def token_hash(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def excused_ids(db: Session, block_id: int, night: date) -> set[int]:
    rows = db.scalars(
        select(GatePass.student_id).join(Student)
        .where(Student.block_id == block_id, GatePass.pass_date == night,
               GatePass.status == "approved"))
    return set(rows)


# ============================================================ AUTH
class LoginIn(BaseModel):
    email: EmailStr
    password: str
    kind: str   # "student" or "staff"


@app.post("/api/login")
def login(data: LoginIn, db: Session = Depends(get_db)):
    Model = Student if data.kind == "student" else Staff
    user = db.scalar(select(Model).where(Model.email == data.email))
    if not user or not check_password(data.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    kind = "student" if data.kind == "student" else "staff"
    return {"token": make_token(kind, user.id), "name": user.name,
            "role": "student" if kind == "student" else user.role,
            "block_id": user.block_id}


# ============================================================ STUDENT
@app.get("/api/me")
def me(s: Student = Depends(current_student), db: Session = Depends(get_db)):
    tonight = nt.night_of(nt.now())
    rec = db.scalar(select(Attendance).where(Attendance.student_id == s.id,
                                             Attendance.night == tonight))
    start, end = nt.window(s.block, tonight)
    return {"name": s.name, "block": s.block.name, "room": s.room,
            "tonight": {"night": tonight, "window_start": start, "window_end": end,
                        "status": rec.status if rec else "not_marked",
                        "late": rec.late if rec else None}}


class CheckInIn(BaseModel):
    qr_token: str


def log_checkin(s: Student, ip: str | None, outcome: str):
    """One line per check-in attempt in the server terminal, so refusals are easy to explain."""
    print(f"[check-in] {nt.now():%H:%M:%S} {s.name} ({s.block.name}, {s.room}) from {ip or '?'}: {outcome}", flush=True)


@app.post("/api/checkin")
def checkin(data: CheckInIn, request: Request,
            s: Student = Depends(current_student), db: Session = Depends(get_db)):
    block = s.block
    ip = request.client.host if request.client else None
    if not nt.qr_valid(data.qr_token, block.id):
        log_checkin(s, ip, "REFUSED - QR expired or from another block")
        raise HTTPException(400, "QR code expired or not from your block. Scan the screen at your reception again.")
    if ENFORCE_NETWORK and not nt.on_campus_network(ip):
        log_checkin(s, ip, f"REFUSED - not on campus Wi-Fi (allowed: {', '.join(CAMPUS_NETWORKS)})")
        raise HTTPException(400, "Connect to the campus Wi-Fi (BITS-Student), not mobile data, and try again.")
    moment = nt.now()
    night, timing = nt.classify(block, moment)
    if timing == "too_early":
        start, _ = nt.window(block, night)
        log_checkin(s, ip, f"REFUSED - too early, opens {start:%H:%M}")
        raise HTTPException(400, f"Check-in opens at {start:%H:%M}.")

    rec = db.scalar(select(Attendance).where(Attendance.student_id == s.id,
                                             Attendance.night == night))
    if rec and rec.status == "present":
        log_checkin(s, ip, "already present")
        return {"message": "Already marked present tonight", "late": rec.late}
    if rec:   # taker marked absent earlier, student came back later
        rec.status, rec.late, rec.method = "present", True, "self"
        rec.reason = f"returned after being marked absent ({rec.reason or '-'})"
        rec.marked_at, rec.client_ip, rec.marked_by = local_now_naive(), ip, None
    else:
        rec = Attendance(student_id=s.id, night=night, status="present",
                         late=(timing == "late"), method="self",
                         marked_at=local_now_naive(), client_ip=ip)
        db.add(rec)
    db.commit()
    log_checkin(s, ip, "present" + (" (late)" if rec.late else ""))
    return {"message": "Marked present" + (" (late)" if rec.late else ""),
            "late": rec.late, "time": rec.marked_at}


# ============================================================ GATE PASSES
class PassIn(BaseModel):
    pass_date: date
    reason: str


@app.post("/api/passes")
def request_pass(data: PassIn, s: Student = Depends(current_student),
                 db: Session = Depends(get_db)):
    if data.pass_date < nt.now().date():
        raise HTTPException(400, "Pass date is in the past")
    dup = db.scalar(select(GatePass).where(
        GatePass.student_id == s.id, GatePass.pass_date == data.pass_date,
        GatePass.status.in_(["pending_parent", "pending_warden", "approved"])))
    if dup:
        raise HTTPException(400, "You already have a pass request for that day")
    raw = secrets.token_urlsafe(24)
    gp = GatePass(student_id=s.id, pass_date=data.pass_date, reason=data.reason,
                  parent_token_hash=token_hash(raw),
                  parent_token_expires=datetime.utcnow() + timedelta(hours=24))
    db.add(gp)
    db.flush()
    send_email(db, s.parent_email, f"Gate pass request from {s.name}",
               f"{s.name} ({s.block.name}, room {s.room}) requests a gate pass for "
               f"{data.pass_date}.\nReason: {data.reason}\n\n"
               f"Approve or reject: {BASE_URL}/parent.html?t={raw}\n"
               f"This link works once and expires in 24 hours.")
    db.commit()
    return {"id": gp.id, "status": gp.status}


@app.get("/api/passes/mine")
def my_passes(s: Student = Depends(current_student), db: Session = Depends(get_db)):
    rows = db.scalars(select(GatePass).where(GatePass.student_id == s.id)
                      .order_by(GatePass.pass_date.desc()))
    return [{"id": p.id, "date": p.pass_date, "reason": p.reason, "status": p.status}
            for p in rows]


def _pass_by_parent_token(db: Session, t: str) -> GatePass:
    gp = db.scalar(select(GatePass).where(GatePass.parent_token_hash == token_hash(t)))
    if not gp or gp.status != "pending_parent" or gp.parent_token_expires < datetime.utcnow():
        raise HTTPException(404, "This link is invalid, already used, or expired")
    return gp


@app.get("/api/parent/{t}")
def parent_view(t: str, db: Session = Depends(get_db)):
    gp = _pass_by_parent_token(db, t)
    return {"student": gp.student.name, "date": gp.pass_date, "reason": gp.reason}


class DecisionIn(BaseModel):
    decision: str   # approve / reject


@app.post("/api/parent/{t}")
def parent_decide(t: str, data: DecisionIn, db: Session = Depends(get_db)):
    gp = _pass_by_parent_token(db, t)
    gp.status = "pending_warden" if data.decision == "approve" else "rejected"
    gp.parent_decided_at = datetime.utcnow()
    gp.parent_token_hash = None          # single use
    db.commit()
    return {"status": gp.status}


@app.get("/api/blocks/{bid}/passes")
def block_passes(bid: int, status: str | None = None, day: date | None = None,
                 st: Staff = Depends(staff_with("warden", "chief", "guard")),
                 db: Session = Depends(get_db)):
    require_block_access(st, bid)
    q = select(GatePass).join(Student).where(Student.block_id == bid)
    if st.role == "guard":           # guards only see today's approved passes
        q = q.where(GatePass.status == "approved", GatePass.pass_date == nt.now().date())
    else:
        if status:
            q = q.where(GatePass.status == status)
        if day:
            q = q.where(GatePass.pass_date == day)
    return [{"id": p.id, "student_id": p.student_id, "student": p.student.name,
             "room": p.student.room, "date": p.pass_date, "reason": p.reason,
             "status": p.status} for p in db.scalars(q.order_by(GatePass.pass_date))]


@app.post("/api/passes/{pid}/decide")
def warden_decide(pid: int, data: DecisionIn,
                  st: Staff = Depends(staff_with("warden", "chief")),
                  db: Session = Depends(get_db)):
    gp = db.get(GatePass, pid)
    if not gp:
        raise HTTPException(404, "Pass not found")
    require_block_access(st, gp.student.block_id)
    if gp.status != "pending_warden":
        raise HTTPException(400, f"Pass is {gp.status}, not waiting for warden")
    gp.status = "approved" if data.decision == "approve" else "rejected"
    gp.warden_id = st.id
    db.commit()
    return {"status": gp.status}


# ============================================================ BLOCKS
@app.get("/api/blocks")
def list_blocks(st: Staff = Depends(staff_with("guard", "taker", "warden", "chief")),
                db: Session = Depends(get_db)):
    """Blocks this staff member can open (chief: all; others: their own)."""
    q = select(Block).order_by(Block.name)
    if st.role != "chief":
        q = q.where(Block.id == st.block_id)
    return [{"id": b.id, "name": b.name, "gender": b.gender} for b in db.scalars(q)]


# ============================================================ RECEPTION QR
@app.get("/api/blocks/{bid}/qr")
def reception_qr(bid: int, st: Staff = Depends(staff_with("guard", "warden", "chief")),
                 db: Session = Depends(get_db)):
    require_block_access(st, bid)
    token, remaining = nt.qr_token(bid)
    return {"token": token, "refresh_in": remaining}


# ============================================================ NIGHT (taker / warden)
@app.get("/api/blocks/{bid}/night")
def night_overview(bid: int, night: date | None = None,
                   st: Staff = Depends(staff_with("taker", "warden", "chief")),
                   db: Session = Depends(get_db)):
    require_block_access(st, bid)
    block = db.get(Block, bid)
    night = night or nt.night_of(nt.now())
    students = db.scalars(select(Student).where(Student.block_id == bid)).all()
    recs = {r.student_id: r for r in db.scalars(
        select(Attendance).where(Attendance.night == night,
                                 Attendance.student_id.in_([s.id for s in students])))}
    excused = excused_ids(db, bid, night)
    # Spot checks are drawn from self check-ins. Rooms already found empty stay in the
    # pool, so marking one empty doesn't reshuffle who else gets checked.
    self_present = [sid for sid, r in recs.items()
                    if (r.status == "present" and r.method == "self")
                    or r.reason == "spot_check_room_empty"]
    spot = set(nt.pick_spot_checks(self_present, bid, night, SPOT_CHECKS_PER_NIGHT))

    def row(s, why):
        r = recs.get(s.id)
        return {"student_id": s.id, "name": s.name, "room": s.room, "why": why,
                "status": r.status if r else None, "reason": r.reason if r else None}

    to_visit, counts = [], {"present": 0, "late": 0, "absent": 0, "excused": 0, "unmarked": 0}
    for s in sorted(students, key=lambda x: x.room):
        r = recs.get(s.id)
        if r:
            counts[r.status] += 1
            counts["late"] += int(r.late)
            if s.id in spot:
                to_visit.append(row(s, "spot_check"))
        elif s.id in excused:
            counts["excused"] += 1
        else:
            counts["unmarked"] += 1
            to_visit.append(row(s, "not_marked"))
    start, end = nt.window(block, night)
    return {"block": block.name, "night": night, "window": [start, end],
            "total": len(students), "counts": counts, "to_visit": to_visit}


class MarkIn(BaseModel):
    student_id: int
    status: str          # present / absent
    reason: str | None = None


@app.post("/api/blocks/{bid}/night/mark")
def manual_mark(bid: int, data: MarkIn,
                st: Staff = Depends(staff_with("taker", "warden", "chief")),
                db: Session = Depends(get_db)):
    require_block_access(st, bid)
    s = db.get(Student, data.student_id)
    if not s or s.block_id != bid:
        raise HTTPException(404, "Student not in this block")
    if data.status not in ("present", "absent"):
        raise HTTPException(400, "status must be present or absent")
    night = nt.night_of(nt.now())
    rec = db.scalar(select(Attendance).where(Attendance.student_id == s.id,
                                             Attendance.night == night))
    is_spot_check = rec and rec.method == "self" and rec.status == "present"
    if data.status == "present" and not is_spot_check and data.reason not in MANUAL_REASONS:
        raise HTTPException(400, f"Manual present needs a reason: {sorted(MANUAL_REASONS)}")
    reason = data.reason
    if rec and rec.method == "self" and rec.status == "present":
        if data.status == "present":          # spot check confirmed: keep it a self check-in
            rec.reason = "spot_check_confirmed"
            db.commit()
            return {"message": f"{s.name} confirmed present", "reason": rec.reason}
        reason = "spot_check_room_empty"      # checked in but not actually there
    if not rec:
        rec = Attendance(student_id=s.id, night=night)
        db.add(rec)
    rec.status, rec.method, rec.reason = data.status, "manual", reason
    rec.late = rec.late or False
    rec.marked_by, rec.marked_at = st.id, local_now_naive()
    db.commit()
    return {"message": f"{s.name} marked {data.status}", "reason": reason}


@app.get("/api/blocks/{bid}/alerts")
def absent_alerts(bid: int, night: date | None = None,
                  st: Staff = Depends(staff_with("warden", "chief")),
                  db: Session = Depends(get_db)):
    """Absent tonight with no pass -> warden follows up the same night."""
    require_block_access(st, bid)
    night = night or nt.night_of(nt.now())
    excused = excused_ids(db, bid, night)
    rows = db.scalars(select(Attendance).join(Student).where(
        Student.block_id == bid, Attendance.night == night, Attendance.status == "absent"))
    return [{"student_id": r.student_id, "name": r.student.name, "room": r.student.room,
             "reason": r.reason,
             "severity": "high" if r.reason == "spot_check_room_empty" else "normal"}
            for r in rows if r.student_id not in excused]


# ============================================================ REPEAT-OFFENDER RULE
@app.post("/api/flags/run")
def run_flags(st: Staff = Depends(staff_with("warden", "chief")),
              db: Session = Depends(get_db)):
    """3+ unexcused absences or 5+ manual marks in the last 14 nights -> email + meeting."""
    since = nt.night_of(nt.now()) - timedelta(days=FLAG_WINDOW_NIGHTS)
    q = (select(Attendance.student_id,
                func.sum((Attendance.status == "absent").cast(Integer_)).label("absent"),
                func.sum(((Attendance.method == "manual") & (Attendance.status == "present"))
                         .cast(Integer_)).label("manual"))
         .join(Student).where(Attendance.night > since).group_by(Attendance.student_id))
    if st.role != "chief":
        q = q.where(Student.block_id == st.block_id)
    flagged = []
    for sid, absent, manual in db.execute(q):
        if (absent or 0) >= FLAG_MAX_ABSENCES or (manual or 0) >= FLAG_MAX_MANUAL:
            s = db.get(Student, sid)
            why = (f"{absent} missed attendances" if absent >= FLAG_MAX_ABSENCES
                   else f"{manual} manual check-ins")
            send_email(db, s.email, "Hostel attendance: please meet your warden",
                       f"Dear {s.name},\nIn the last {FLAG_WINDOW_NIGHTS} nights you have "
                       f"{why}. Reply with your reasons and meet your block warden.")
            flagged.append({"student_id": sid, "name": s.name, "room": s.room,
                            "absent": absent, "manual": manual})
    db.commit()
    return flagged


# ============================================================ REPORTS (curfew data)
@app.get("/api/reports/nightly")
def nightly_report(days: int = 14, block_id: int | None = None,
                   st: Staff = Depends(staff_with("warden", "chief")),
                   db: Session = Depends(get_db)):
    if st.role != "chief":
        block_id = st.block_id
    since = nt.night_of(nt.now()) - timedelta(days=days)
    q = select(Attendance).join(Student).where(Attendance.night > since)
    if block_id:
        q = q.where(Student.block_id == block_id)
    out: dict = {}
    for r in db.scalars(q):
        d = out.setdefault(str(r.night), {"present": 0, "late": 0, "absent": 0, "manual": 0})
        d[r.status] += 1
        d["late"] += int(r.late)
        d["manual"] += int(r.method == "manual")
    return dict(sorted(out.items()))


# Serve the frontend (only the frontend folder -- never the backend code).
FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(FRONTEND):
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
