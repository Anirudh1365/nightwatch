"""models.py -- database tables."""
from datetime import datetime, date
from sqlalchemy import (String, Integer, ForeignKey, DateTime, Date, Boolean,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from db import Base


class Block(Base):
    __tablename__ = "blocks"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    gender: Mapped[str] = mapped_column(String(10))            # Male / Female
    window_start: Mapped[str] = mapped_column(String(5), default="23:30")
    window_end: Mapped[str] = mapped_column(String(5), default="00:00")


class Student(Base):
    __tablename__ = "students"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(120), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    id_no: Mapped[str] = mapped_column(String(20), unique=True)    # university ID, e.g. 2023A7PS0123U (on the card's barcode)
    card_uid: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)  # chip number from a tap reader, linked by a warden
    block_id: Mapped[int] = mapped_column(ForeignKey("blocks.id"))
    room: Mapped[str] = mapped_column(String(10))
    parent_email: Mapped[str] = mapped_column(String(120))
    block: Mapped[Block] = relationship()


class Staff(Base):
    __tablename__ = "staff"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(120), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(10))   # taker / guard / warden / chief
    block_id: Mapped[int | None] = mapped_column(ForeignKey("blocks.id"), nullable=True)


class GatePass(Base):
    __tablename__ = "gate_passes"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"))
    pass_date: Mapped[date] = mapped_column(Date)       # valid for this one day only
    reason: Mapped[str] = mapped_column(String(300))
    # pending_parent -> pending_warden -> approved / rejected
    status: Mapped[str] = mapped_column(String(20), default="pending_parent")
    parent_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_token_expires: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    parent_decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    warden_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    student: Mapped[Student] = relationship()


class Attendance(Base):
    """One row per student per night."""
    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("student_id", "night"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"))
    night: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10))     # present / absent
    late: Mapped[bool] = mapped_column(Boolean, default=False)
    method: Mapped[str] = mapped_column(String(10))     # self (phone) / id_card (reception) / manual (taker)
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    marked_by: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), nullable=True)
    marked_at: Mapped[datetime] = mapped_column(DateTime)
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    student: Mapped[Student] = relationship()


class AuditLog(Base):
    """Change history. Rows are only ever added, never edited or deleted, so a warden
    can see who changed an attendance entry, when, and what it was before."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime)                 # campus time
    actor: Mapped[str] = mapped_column(String(120))                # "Aarav (student)", "Ms Rao (taker)", "parent"
    action: Mapped[str] = mapped_column(String(30))                # checkin / checkin_refused / manual_mark / pass_decision / flag_email
    block_id: Mapped[int | None] = mapped_column(ForeignKey("blocks.id"), nullable=True)
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id"), nullable=True)
    night: Mapped[date | None] = mapped_column(Date, nullable=True)
    before: Mapped[str | None] = mapped_column(String(200), nullable=True)
    after: Mapped[str | None] = mapped_column(String(200), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    student: Mapped[Student | None] = relationship()


class Outbox(Base):
    """Emails the system would send. Printed to console in the demo."""
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(primary_key=True)
    to: Mapped[str] = mapped_column(String(120))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
