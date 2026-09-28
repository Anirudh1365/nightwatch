"""seed.py -- demo data. Run once:  python seed.py
All demo passwords: demo123

python seed.py --history   also adds 14 past nights of attendance so the warden's
                           chart and the repeat-absence check have something to show.
                           (Don't use it before test_flow.py, the tests expect a clean start.)
"""
import random
import sys
from datetime import datetime, timedelta

import night as nt
from models import Attendance
from db import Base, engine, SessionLocal
from models import Block, Staff, Student
from auth import hash_password

Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)
db = SessionLocal()
pw = hash_password("demo123")

a = Block(name="Block A", gender="Male", wifi_subnet="192.168.137.0/24")   # Windows hotspot default
g = Block(name="Block G", gender="Female", wifi_subnet="192.168.43.0/24")   # Android hotspot default
db.add_all([a, g])
db.flush()

db.add_all([
    Staff(name="Chief Warden", email="chief@demo.edu", password_hash=pw, role="chief"),
    Staff(name="Warden A", email="warden.a@demo.edu", password_hash=pw, role="warden", block_id=a.id),
    Staff(name="Taker A", email="taker.a@demo.edu", password_hash=pw, role="taker", block_id=a.id),
    Staff(name="Guard A", email="guard.a@demo.edu", password_hash=pw, role="guard", block_id=a.id),
    Staff(name="Warden G", email="warden.g@demo.edu", password_hash=pw, role="warden", block_id=g.id),
    Staff(name="Taker G", email="taker.g@demo.edu", password_hash=pw, role="taker", block_id=g.id),
    Staff(name="Guard G", email="guard.g@demo.edu", password_hash=pw, role="guard", block_id=g.id),
])

boys = ["Aarav", "Vivaan", "Ishaan", "Kabir", "Reyansh", "Arjun", "Dhruv", "Vihaan", "Rohan", "Karan"]
girls = ["Ananya", "Diya", "Saanvi", "Myra", "Aadhya", "Kiara", "Riya", "Meera", "Tara", "Nisha"]
for i, n in enumerate(boys):
    db.add(Student(name=n, email=f"{n.lower()}@demo.edu", password_hash=pw, block_id=a.id,
                   room=f"A{101 + i // 2}", parent_email=f"parent.{n.lower()}@demo.edu"))
for i, n in enumerate(girls):
    db.add(Student(name=n, email=f"{n.lower()}@demo.edu", password_hash=pw, block_id=g.id,
                   room=f"G{101 + i // 2}", parent_email=f"parent.{n.lower()}@demo.edu"))
db.commit()
print("Seeded: 2 blocks, 7 staff, 20 students. Password for everyone: demo123")

if "--history" in sys.argv:
    rng = random.Random(42)                       # same "random" history every time
    tonight = nt.night_of(nt.now())
    takers = {st.block_id: st.id for st in db.query(Staff).filter(Staff.role == "taker")}
    for s in db.query(Student).all():
        for back in range(1, 15):
            night = tonight - timedelta(days=back)
            at = datetime(night.year, night.month, night.day, 23, 30)
            roll = rng.random()
            if s.name == "Rohan" and back in (2, 6, 11):  # one repeat absentee to flag
                roll = 1.0
            if roll < 0.80:                       # checked in on time
                row = dict(status="present", late=False, method="self",
                           marked_at=at + timedelta(minutes=rng.randint(0, 29)))
            elif roll < 0.90:                     # checked in after the window
                row = dict(status="present", late=True, method="self",
                           marked_at=at + timedelta(minutes=rng.randint(31, 90)))
            elif roll < 0.96:                     # taker found them, no phone check-in
                row = dict(status="present", late=False, method="manual",
                           reason=rng.choice(["asleep", "phone_issue", "sick"]),
                           marked_by=takers[s.block_id], marked_at=at + timedelta(minutes=45))
            else:                                 # not in the room
                row = dict(status="absent", late=False, method="manual",
                           marked_by=takers[s.block_id], marked_at=at + timedelta(minutes=50))
            db.add(Attendance(student_id=s.id, night=night, **row))
    db.commit()
    print("Added 14 nights of history (Rohan has 3 absences, for the repeat-absence demo)")
