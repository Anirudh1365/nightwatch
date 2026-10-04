"""main.py -- NightWatch API.

Run:  uvicorn main:app --reload
Docs: http://127.0.0.1:8000/docs
"""
import csv
import hashlib
import io
import os
import secrets
from datetime import date, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
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
from models import Attendance, AuditLog, Block, GatePass, Outbox, Staff, Student

Base.metadata.create_all(engine)
app = FastAPI(title="NightWatch - Hostel Night Attendance")

MANUAL_REASONS = {"asleep", "sick", "phone_issue", "other"}
# Check-ins the student made themselves (phone + QR, or ID card at reception). Spot checks are
# drawn from these; "manual" marks by the taker are not.
SELF_METHODS = ("self", "id_card")


def local_now_naive() -> datetime:
    return nt.now().replace(tzinfo=None)


def send_email(db: Session, to: str, subject: str, body: str):
    """Demo: store in outbox + print. Swap for SMTP/Mailtrap later."""
    db.add(Outbox(to=to, subject=subject, body=body))
    print(f"\n--- EMAIL to {to}: {subject}\n{body}\n---")


def audit(db: Session, actor: str, action: str, *, student: Student | None = None,
          night: date | None = None, before: str | None = None, after: str | None = None,
          detail: str | None = None, ip: str | None = None, block_id: int | None = None):
    """Add one row to the change history. The caller commits."""
    db.add(AuditLog(at=local_now_naive(), actor=actor, action=action,
                    block_id=student.block_id if student else block_id,
                    student_id=student.id if student else None,
                    night=night, before=before, after=after, detail=detail, ip=ip))


def describe(rec: Attendance | None) -> str:
    """Short text for an attendance entry, used as before/after in the change history."""
    if not rec:
        return "not marked"
    text = rec.status + (" (late)" if rec.late else "") + f", {rec.method}"
    return text + (f", {rec.reason}" if rec.reason else "")


def who(person: Student | Staff) -> str:
    return f"{person.name} ({'student' if isinstance(person, Student) else person.role})"


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
    kind: str | None = None   # optional: "student" or "staff". Left out = work it out from the email.


@app.post("/api/login")
def login(data: LoginIn, db: Session = Depends(get_db)):
    # One login page for everyone: try student accounts, then staff accounts.
    # The account that matches decides which dashboard the person lands on.
    kinds = [data.kind] if data.kind in ("student", "staff") else ["student", "staff"]
    for kind in kinds:
        Model = Student if kind == "student" else Staff
        user = db.scalar(select(Model).where(Model.email == data.email))
        if user and check_password(data.password, user.password_hash):
            return {"token": make_token(kind, user.id), "name": user.name,
                    "role": "student" if kind == "student" else user.role,
                    "block_id": user.block_id}
    raise HTTPException(401, "Invalid email or password")


# ============================================================ STUDENT
@app.get("/api/me")
def me(s: Student = Depends(current_student), db: Session = Depends(get_db)):
    tonight = nt.night_of(nt.now())
    rec = db.scalar(select(Attendance).where(Attendance.student_id == s.id,
                                             Attendance.night == tonight))
    start, end = nt.window(s.block, tonight)
    return {"name": s.name, "block": s.block.name, "room": s.room,
            "now": nt.now(),
            "tonight": {"night": tonight, "window_start": start, "window_end": end,
                        "windows": nt.windows(s.block, tonight),
                        "status": rec.status if rec else "not_marked",
                        "late": rec.late if rec else None}}


class CheckInIn(BaseModel):
    qr_token: str


def log_checkin(s: Student, ip: str | None, outcome: str):
    """One line per check-in attempt in the server terminal, so refusals are easy to explain."""
    print(f"[check-in] {nt.now():%H:%M:%S} {s.name} ({s.block.name}, {s.room}) from {ip or '?'}: {outcome}", flush=True)


def refuse_checkin(db: Session, s: Student, ip: str | None, why: str, message: str):
    """Save the refused attempt in the change history, then tell the student why."""
    log_checkin(s, ip, "REFUSED - " + why)
    audit(db, who(s), "checkin_refused", student=s, night=nt.night_of(nt.now()),
          detail=why, ip=ip)
    db.commit()
    raise HTTPException(400, message)


@app.post("/api/checkin")
def checkin(data: CheckInIn, request: Request,
            s: Student = Depends(current_student), db: Session = Depends(get_db)):
    block = s.block
    ip = request.client.host if request.client else None
    if not nt.qr_valid(data.qr_token, block.id):
        refuse_checkin(db, s, ip, "QR expired or from another block",
                       "QR code expired or not from your block. Scan the screen at your reception again.")
    if ENFORCE_NETWORK and not nt.on_campus_network(ip):
        refuse_checkin(db, s, ip, f"not on campus Wi-Fi (allowed: {', '.join(CAMPUS_NETWORKS)})",
                       "Connect to the campus Wi-Fi (BITS-Student), not mobile data, and try again.")
    closed = check_in_closed(block)
    if closed:
        refuse_checkin(db, s, ip, *closed)
    return mark_present(db, s, ip, "self", who(s))


def check_in_closed(block: Block) -> tuple[str, str] | None:
    """None if check-in is open now, else (reason for the log, message for the person)."""
    moment = nt.now()
    night, timing = nt.classify(block, moment)
    if timing not in ("too_early", "between"):
        return None
    opens = nt.next_opening(block, moment)
    times = " and ".join(f"{a:%H:%M}–{b:%H:%M}" for a, b in nt.windows(block, night))
    return (f"check-in closed, opens {opens:%H:%M}",
            f"Check-in is closed now. It opens at {opens:%H:%M} (check-in times: {times}).")


def mark_present(db: Session, s: Student, ip: str | None, method: str, actor: str,
                 marked_by: int | None = None) -> dict:
    """Record tonight's check-in (window already checked). method: self (phone) / id_card (reception)."""
    night, timing = nt.classify(s.block, nt.now())
    rec = db.scalar(select(Attendance).where(Attendance.student_id == s.id,
                                             Attendance.night == night))
    if rec and rec.status == "present":
        log_checkin(s, ip, "already present")
        audit(db, actor, "checkin_duplicate", student=s, night=night,
              detail="already present, nothing changed", ip=ip)
        db.commit()
        return {"message": "Already marked present tonight", "late": rec.late, "already": True}
    before = describe(rec)
    if rec:   # taker marked absent earlier, student came back later
        rec.status, rec.late, rec.method = "present", True, method
        rec.reason = f"returned after being marked absent ({rec.reason or '-'})"
        rec.marked_at, rec.client_ip, rec.marked_by = local_now_naive(), ip, marked_by
    else:
        rec = Attendance(student_id=s.id, night=night, status="present",
                         late=(timing == "late"), method=method, marked_by=marked_by,
                         marked_at=local_now_naive(), client_ip=ip)
        db.add(rec)
    audit(db, actor, "checkin" if method == "self" else "id_scan", student=s, night=night,
          before=before, after=describe(rec), ip=ip)
    db.commit()
    log_checkin(s, ip, "present" + (" (late)" if rec.late else "") + (" by ID card" if method == "id_card" else ""))
    return {"message": "Marked present" + (" (late)" if rec.late else ""),
            "late": rec.late, "time": rec.marked_at, "already": False}


# ============================================================ ID CARD SCAN (reception desk)
# The guard's laptop or phone at the block reception reads the student's university ID card:
# the barcode through the camera, or the card's chip through a USB tap reader (those readers
# type the card number like a keyboard). Same rules as phone check-in, except the guard is
# the one standing there, so the card replaces the rotating QR.
class ScanIn(BaseModel):
    code: str


def clean_code(raw: str) -> str:
    return "".join(raw.split()).upper()[:64]


def find_card(db: Session, code: str) -> Student | None:
    return db.scalar(select(Student).where((Student.id_no == code) | (Student.card_uid == code)))


@app.post("/api/blocks/{bid}/scan")
def scan_id(bid: int, data: ScanIn, request: Request,
            st: Staff = Depends(staff_with("guard", "warden", "chief")),
            db: Session = Depends(get_db)):
    require_block_access(st, bid)
    ip = request.client.host if request.client else None
    if ENFORCE_NETWORK and not nt.on_campus_network(ip):
        raise HTTPException(400, "This scanner isn't on the campus network. Connect it to campus Wi-Fi or LAN.")
    code = clean_code(data.code)
    if not code:
        raise HTTPException(400, "Empty scan")
    at = nt.now().strftime("%H:%M")
    s = find_card(db, code)
    night = nt.night_of(nt.now())
    if not s:
        audit(db, who(st), "id_scan_unknown", block_id=bid, night=night, detail=f"card {code}", ip=ip)
        db.commit()
        return {"result": "unknown", "code": code, "at": at,
                "message": "Card not recognised. A warden can link it to a student."}
    card = {"name": s.name, "room": s.room, "block": s.block.name, "id_no": s.id_no}
    if s.block_id != bid:
        why = f"ID card scanned at the wrong block (scanned at block {bid})"
        log_checkin(s, ip, "REFUSED - " + why)
        audit(db, who(st), "checkin_refused", student=s, night=night, detail=why, ip=ip)
        db.commit()
        return {"result": "refused", "student": card, "at": at,
                "message": f"{s.name} lives in {s.block.name}. Check in at that block's reception."}
    closed = check_in_closed(s.block)
    if closed:
        log_checkin(s, ip, "REFUSED - " + closed[0])
        audit(db, who(st), "checkin_refused", student=s, night=night, detail="ID card: " + closed[0], ip=ip)
        db.commit()
        return {"result": "refused", "student": card, "at": at, "message": closed[1]}
    r = mark_present(db, s, ip, "id_card", who(st), marked_by=st.id)
    result = "already" if r["already"] else "late" if r["late"] else "present"
    return {"result": result, "student": card, "at": at, "message": r["message"]}


class LinkCardIn(BaseModel):
    code: str
    student_id: int


@app.post("/api/blocks/{bid}/link-card")
def link_card(bid: int, data: LinkCardIn,
              st: Staff = Depends(staff_with("warden", "chief")),
              db: Session = Depends(get_db)):
    """First tap of a card whose chip number we don't know yet: the warden says whose it is."""
    require_block_access(st, bid)
    s = db.get(Student, data.student_id)
    if not s or s.block_id != bid:
        raise HTTPException(404, "Student not in this block")
    code = clean_code(data.code)
    if not code:
        raise HTTPException(400, "Empty card number")
    owner = find_card(db, code)
    if owner and owner.id != s.id:
        raise HTTPException(400, f"That card already belongs to {owner.name} ({owner.block.name}, {owner.room})")
    before = s.card_uid
    s.card_uid = code
    audit(db, who(st), "card_linked", student=s, night=nt.night_of(nt.now()),
          before=before or "no card", after=f"card {code}")
    db.commit()
    return {"message": f"Card linked to {s.name}. Scan it again to check in."}


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
               f"{data.pass_date:%d/%m/%y}.\nReason: {data.reason}\n\n"
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
    before = gp.status
    gp.status = "pending_warden" if data.decision == "approve" else "rejected"
    audit(db, "parent (email link)", "pass_decision", student=gp.student, night=gp.pass_date,
          before=before, after=gp.status, detail=f"gate pass {gp.pass_date:%d/%m/%y}")
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
    audit(db, who(st), "pass_decision", student=gp.student, night=gp.pass_date,
          before="pending_warden", after=gp.status, detail=f"gate pass {gp.pass_date:%d/%m/%y}")
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
    return {"token": token, "refresh_in": remaining, "now": nt.now()}


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
                    if (r.status == "present" and r.method in SELF_METHODS)
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
            "windows": nt.windows(block, night),
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
    is_spot_check = rec and rec.method in SELF_METHODS and rec.status == "present"
    before = describe(rec)
    if data.status == "present" and not is_spot_check and data.reason not in MANUAL_REASONS:
        raise HTTPException(400, f"Manual present needs a reason: {sorted(MANUAL_REASONS)}")
    reason = data.reason
    if is_spot_check:
        if data.status == "present":          # spot check confirmed: keep it a self check-in
            rec.reason = "spot_check_confirmed"
            audit(db, who(st), "manual_mark", student=s, night=night,
                  before=before, after=describe(rec), detail="spot check: student in room")
            db.commit()
            return {"message": f"{s.name} confirmed present", "reason": rec.reason}
        reason = "spot_check_room_empty"      # checked in but not actually there
    if not rec:
        rec = Attendance(student_id=s.id, night=night)
        db.add(rec)
    rec.status, rec.method, rec.reason = data.status, "manual", reason
    rec.late = rec.late or False
    rec.marked_by, rec.marked_at = st.id, local_now_naive()
    audit(db, who(st), "manual_mark", student=s, night=night, before=before, after=describe(rec))
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
            audit(db, who(st), "flag_email", student=s, night=nt.night_of(nt.now()),
                  detail=f"{why} in {FLAG_WINDOW_NIGHTS} nights, emailed to meet warden")
            flagged.append({"student_id": sid, "name": s.name, "room": s.room,
                            "absent": absent, "manual": manual})
    db.commit()
    return flagged


# ============================================================ REPORTS (curfew data)
def _nightly_counts(db: Session, st: Staff, days: int, block_id: int | None) -> dict:
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


@app.get("/api/reports/nightly")
def nightly_report(days: int = 14, block_id: int | None = None,
                   st: Staff = Depends(staff_with("warden", "chief")),
                   db: Session = Depends(get_db)):
    return _nightly_counts(db, st, days, block_id)


def csv_response(filename: str, header: list[str], rows: list[list]) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for row in rows:
        # A cell starting with = + - @ would run as a formula in Excel; prefix it so it stays text.
        w.writerow(["'" + v if isinstance(v, str) and v and v[0] in "=+-@" else v for v in row])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/reports/nightly.csv")
def nightly_report_csv(days: int = 14, block_id: int | None = None,
                       st: Staff = Depends(staff_with("warden", "chief")),
                       db: Session = Depends(get_db)):
    data = _nightly_counts(db, st, days, block_id)
    rows = [[night, d["present"] - d["late"], d["late"], d["absent"], d["manual"]]
            for night, d in data.items()]
    return csv_response(f"nightwatch_last_{days}_nights.csv",
                        ["night", "on_time", "late", "absent", "marked_by_taker"], rows)


# ============================================================ REGISTER (every student, one night)
def _register(db: Session, bid: int, night: date) -> list[dict]:
    """Every student in the block for one night, with the exceptions marked."""
    students = db.scalars(select(Student).where(Student.block_id == bid).order_by(Student.room)).all()
    recs = {r.student_id: r for r in db.scalars(
        select(Attendance).where(Attendance.night == night,
                                 Attendance.student_id.in_([s.id for s in students])))}
    excused = excused_ids(db, bid, night)
    staff = {x.id: x.name for x in db.scalars(select(Staff))}
    out = []
    for s in students:
        r = recs.get(s.id)
        if r:
            status = "late" if r.status == "present" and r.late else r.status
        else:
            status = "gate_pass" if s.id in excused else "not_checked_in"
        manual = bool(r and r.method == "manual")
        out.append({
            "student_id": s.id, "id_no": s.id_no, "name": s.name, "room": s.room, "status": status,
            "method": r.method if r else None, "reason": r.reason if r else None,
            "time": r.marked_at.strftime("%H:%M") if r else None,
            "marked_by": staff.get(r.marked_by) if r and r.marked_by else None,
            # what the warden should look at: missing, late, absent, or marked by hand
            "exception": status in ("late", "absent", "not_checked_in") or manual,
        })
    return out


@app.get("/api/blocks/{bid}/register")
def night_register(bid: int, night: date | None = None,
                   st: Staff = Depends(staff_with("warden", "chief")),
                   db: Session = Depends(get_db)):
    require_block_access(st, bid)
    night = night or nt.night_of(nt.now())
    return {"night": night, "rows": _register(db, bid, night)}


@app.get("/api/blocks/{bid}/register.csv")
def night_register_csv(bid: int, night: date | None = None,
                       st: Staff = Depends(staff_with("warden", "chief")),
                       db: Session = Depends(get_db)):
    require_block_access(st, bid)
    night = night or nt.night_of(nt.now())
    block = db.get(Block, bid)
    rows = [[night, block.name, r["room"], r["id_no"], r["name"], r["status"], r["method"] or "",
             r["reason"] or "", r["time"] or "", r["marked_by"] or "", "yes" if r["exception"] else ""]
            for r in _register(db, bid, night)]
    return csv_response(f"nightwatch_{block.name}_{night}.csv".replace(" ", "_"),
                        ["night", "block", "room", "id_no", "name", "status", "method", "reason",
                         "time", "marked_by", "exception"], rows)


# ============================================================ CHANGE HISTORY (audit log)
def _audit_rows(db: Session, bid: int, night: date | None) -> list[AuditLog]:
    q = select(AuditLog).where(AuditLog.block_id == bid)
    if night:
        q = q.where(AuditLog.night == night)
    return db.scalars(q.order_by(AuditLog.id.desc())).all()


def _audit_dict(a: AuditLog) -> dict:
    return {"at": a.at, "actor": a.actor, "action": a.action,
            "student": a.student.name if a.student else None,
            "room": a.student.room if a.student else None,
            "night": a.night, "before": a.before, "after": a.after, "detail": a.detail, "ip": a.ip}


@app.get("/api/blocks/{bid}/audit")
def audit_log(bid: int, night: date | None = None,
              st: Staff = Depends(staff_with("warden", "chief")),
              db: Session = Depends(get_db)):
    """Who changed what, newest first. Leave out night= for the whole history."""
    require_block_access(st, bid)
    return [_audit_dict(a) for a in _audit_rows(db, bid, night)]


@app.get("/api/blocks/{bid}/audit.csv")
def audit_log_csv(bid: int, night: date | None = None,
                  st: Staff = Depends(staff_with("warden", "chief")),
                  db: Session = Depends(get_db)):
    require_block_access(st, bid)
    rows = [[a["at"].strftime("%Y-%m-%d %H:%M:%S"), a["actor"], a["action"], a["student"] or "",
             a["room"] or "", a["night"] or "", a["before"] or "", a["after"] or "",
             a["detail"] or "", a["ip"] or ""]
            for a in map(_audit_dict, _audit_rows(db, bid, night))]
    return csv_response(f"nightwatch_change_history_block{bid}{'_' + str(night) if night else ''}.csv",
                        ["time", "who", "action", "student", "room", "night", "before", "after",
                         "detail", "ip"], rows)


# Serve the frontend (only the frontend folder -- never the backend code).
FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(FRONTEND):
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
