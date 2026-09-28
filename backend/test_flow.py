"""test_flow.py -- end-to-end check of the core. Run: python seed.py && python test_flow.py"""
import os
os.environ["ENFORCE_NETWORK"] = "false"      # TestClient has no real IP; network check tested separately
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
import night as nt
import main
from db import SessionLocal
main.SessionLocal = SessionLocal
from models import Block

c = TestClient(main.app)
fake_now = {"t": None}
nt.now = lambda: fake_now["t"]
main.nt.now = nt.now


def at(day_offset, hh, mm):
    base = datetime.now(nt.TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(days=day_offset, hours=hh, minutes=mm)


def login(email, kind):
    r = c.post("/api/login", json={"email": email, "password": "demo123", "kind": kind})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


ok = lambda msg: print("PASS", msg)
guard, taker, warden = (login(f"{r}.a@demo.edu", "staff") for r in ("guard", "taker", "warden"))
wardenG = login("warden.g@demo.edu", "staff")
aarav, vivaan, ishaan, kabir = (login(f"{n}@demo.edu", "student") for n in ("aarav", "vivaan", "ishaan", "kabir"))

# --- auth / roles
assert c.get("/api/blocks/1/night").status_code == 401
assert c.get("/api/blocks/1/night", headers=aarav).status_code == 403
assert c.get("/api/blocks/1/night", headers=wardenG).status_code == 403
ok("no login -> 401, student and other-block warden blocked")

# --- gate pass: Kabir, for today
fake_now["t"] = at(0, 14, 0)
r = c.post("/api/passes", json={"pass_date": str(at(0, 0, 0).date()), "reason": "Family dinner"}, headers=kabir)
assert r.status_code == 200, r.text
link = main.SessionLocal().query(main.Outbox).order_by(main.Outbox.id.desc()).first().body
raw = link.split("?t=")[1].split()[0]
assert c.post(f"/api/parent/{raw}", json={"decision": "approve"}).json()["status"] == "pending_warden"
assert c.post(f"/api/parent/{raw}", json={"decision": "approve"}).status_code == 404
ok("parent link approves once, reuse rejected")
pid = r.json()["id"]
assert c.get("/api/blocks/1/passes", headers=guard).json() == []
assert c.post(f"/api/passes/{pid}/decide", json={"decision": "approve"}, headers=warden).json()["status"] == "approved"
assert len(c.get("/api/blocks/1/passes", headers=guard).json()) == 1
ok("warden approves, guard now sees today's pass")

# --- QR + check-in window
qr = c.get("/api/blocks/1/qr", headers=guard).json()["token"]
fake_now["t"] = at(0, 22, 0)
assert "opens" in c.post("/api/checkin", json={"qr_token": qr}, headers=aarav).json()["detail"]
ok("check-in before 23:30 rejected")
assert c.post("/api/checkin", json={"qr_token": "1.1.bad"}, headers=aarav).status_code == 400
assert c.post("/api/checkin", json={"qr_token": nt.qr_token(2)[0]}, headers=aarav).status_code == 400
ok("fake QR and other block's QR rejected")
old = nt.qr_token(1, at=__import__("time").time() - 60)[0]
assert c.post("/api/checkin", json={"qr_token": old}, headers=aarav).status_code == 400
ok("QR older than ~30s rejected (screenshots useless)")

fake_now["t"] = at(0, 23, 40)
r = c.post("/api/checkin", json={"qr_token": qr}, headers=aarav).json()
assert r["late"] is False, r
ok("Aarav on time at 23:40")
fake_now["t"] = at(1, 0, 25)
r = c.post("/api/checkin", json={"qr_token": nt.qr_token(1)[0]}, headers=vivaan).json()
assert r["late"] is True, r
ok("Vivaan at 00:25 counted for the same night, marked late")

# --- taker list
ov = c.get("/api/blocks/1/night", headers=taker).json()
visit = {v["name"]: v["why"] for v in ov["to_visit"]}
assert "Kabir" not in visit and ov["counts"]["excused"] == 1
assert visit.get("Ishaan") == "not_marked"
assert visit.get("Aarav") == "spot_check" and visit.get("Vivaan") == "spot_check"   # only 2 self-present, k=5
ok(f"taker list: {len(ov['to_visit'])} rooms (unmarked + spot checks), pass holder excused")

# --- manual marks
assert c.post("/api/blocks/1/night/mark", json={"student_id": 3, "status": "present"}, headers=taker).status_code == 400
assert c.post("/api/blocks/1/night/mark", json={"student_id": 3, "status": "present", "reason": "asleep"}, headers=taker).status_code == 200
ok("manual present requires a reason")
r = c.post("/api/blocks/1/night/mark", json={"student_id": 2, "status": "present"}, headers=taker).json()
assert r.get("reason") == "spot_check_confirmed", r
ok("spot check confirmed: stays a self check-in, not held against student")
r = c.post("/api/blocks/1/night/mark", json={"student_id": 1, "status": "absent"}, headers=taker).json()
assert r["reason"] == "spot_check_room_empty"
c.post("/api/blocks/1/night/mark", json={"student_id": 5, "status": "absent"}, headers=taker)
alerts = c.get("/api/blocks/1/alerts", headers=warden).json()
assert {a["name"]: a["severity"] for a in alerts} == {"Aarav": "high", "Reyansh": "normal"}
ok("warden alerts: empty spot-check room = high, plain absence = normal")

fake_now["t"] = at(1, 1, 30)
r = c.post("/api/checkin", json={"qr_token": nt.qr_token(1)[0]}, headers=login("reyansh@demo.edu", "student")).json()
assert r["late"] is True
ok("Reyansh comes back at 01:30 -> absent flipped to late present")

# --- repeat-offender rule: 3 absences in 14 nights
db = main.SessionLocal()
for d in (2, 3):
    db.add(main.Attendance(student_id=5, night=nt.night_of(fake_now["t"]) - timedelta(days=d),
                           status="absent", method="manual", marked_at=datetime.now()))
db.commit()
fake_now["t"] = at(1, 1, 30)
db.query(main.Attendance).filter_by(student_id=5, night=nt.night_of(fake_now["t"])).update({"status": "absent"})
db.commit()
flagged = c.post("/api/flags/run", headers=warden).json()
assert [f["name"] for f in flagged] == ["Reyansh"], flagged
ok("3 absences in 14 nights -> flagged + email queued")

print("\nreport:", c.get("/api/reports/nightly", headers=warden).json())

# --- network check
b = Block(name="x", gender="Male", wifi_subnet="192.168.137.0/24")
assert nt.on_block_network("192.168.137.23", b) and not nt.on_block_network("5.31.10.2", b)
ok("hostel Wi-Fi subnet accepted, mobile-data IP rejected")
print("\nALL TESTS PASSED")
