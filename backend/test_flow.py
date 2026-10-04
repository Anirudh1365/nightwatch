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

# --- one login page: no student/staff choice, the account decides the role
for email, role in (("aarav@demo.edu", "student"), ("warden.a@demo.edu", "warden"),
                    ("taker.a@demo.edu", "taker"), ("chief@demo.edu", "chief")):
    r = c.post("/api/login", json={"email": email, "password": "demo123"})
    assert r.status_code == 200 and r.json()["role"] == role, r.text
assert c.post("/api/login", json={"email": "aarav@demo.edu", "password": "wrong"}).status_code == 401
assert c.post("/api/login", json={"email": "nobody@demo.edu", "password": "demo123"}).status_code == 401
ok("single login: student lands as student, staff as their role, wrong password refused")

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
fake_now["t"] = at(0, 21, 50)
assert "opens at 22:00" in c.post("/api/checkin", json={"qr_token": qr}, headers=aarav).json()["detail"]
ok("check-in before 22:00 rejected")
fake_now["t"] = at(0, 22, 30)
assert "opens at 23:30" in c.post("/api/checkin", json={"qr_token": qr}, headers=aarav).json()["detail"]
ok("check-in between the two windows (22:15-23:30) rejected")
blockA = main.SessionLocal().get(Block, 1)
for (h, m), want in (((22, 0), "on_time"), ((22, 14), "on_time"), ((22, 15), "between"),
                     ((23, 29), "between"), ((23, 30), "on_time"), ((0, 1), "late")):
    got = nt.classify(blockA, at(1 if h < 12 else 0, h, m))[1]
    assert got == want, (h, m, got)
ok("two windows: 22:00-22:15 and 23:30-00:00 on time, gap closed, after 00:00 late")
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

# --- register, change history, downloads
tonight = str(nt.night_of(at(0, 23, 0)))
reg = {r["name"]: r for r in c.get(f"/api/blocks/1/register?night={tonight}", headers=warden).json()["rows"]}
assert reg["Kabir"]["status"] == "gate_pass" and not reg["Kabir"]["exception"]
assert reg["Vivaan"]["status"] == "late" and reg["Vivaan"]["exception"]           # came at 00:25
assert reg["Ishaan"]["exception"] and reg["Ishaan"]["marked_by"]                  # manual, by the taker
ok("register: every student, exceptions marked, manual marks show who")
hist = c.get(f"/api/blocks/1/audit?night={tonight}", headers=warden).json()
acts = [(h["student"], h["action"]) for h in hist]
assert ("Aarav", "checkin_refused") in acts                      # refusals are kept, not just printed
aarav_empty = next(h for h in hist if h["student"] == "Aarav" and h["action"] == "manual_mark")
assert aarav_empty["before"].startswith("present, self") and "spot_check_room_empty" in aarav_empty["after"]
reyansh = [h for h in hist if h["student"] == "Reyansh" and h["action"] in ("manual_mark", "checkin")]
assert [h["after"].split(",")[0] for h in reversed(reyansh)] == ["absent", "present (late)"]
assert any(h["action"] == "pass_decision" and h["actor"] == "parent (email link)" for h in hist)
ok("change history: refusals, before/after of every change, who made it")
assert c.get("/api/blocks/1/audit", headers=taker).status_code == 403
assert c.get("/api/blocks/1/audit", headers=wardenG).status_code == 403
ok("change history: warden of this block and chief only")
for url in (f"/api/blocks/1/register.csv?night={tonight}", "/api/blocks/1/audit.csv",
            "/api/reports/nightly.csv"):
    r = c.get(url, headers=warden)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv"), url
    assert "attachment" in r.headers["content-disposition"]
csv_text = c.get(f"/api/blocks/1/register.csv?night={tonight}", headers=warden).text
assert "Aarav" in csv_text and ",'," not in csv_text          # empty cells stay empty
assert main.csv_response("x.csv", ["a"], [["=SUM(A1)"]]).body.decode().splitlines()[1] == "'=SUM(A1)"
assert c.get("/api/blocks/1/register.csv", headers=aarav).status_code == 403
ok("CSV downloads work for the warden, not for students")

# --- ID card at the reception desk (barcode via camera, or chip via a USB tap reader)
fake_now["t"] = at(1, 1, 40)
scan = lambda code, who=guard, bid=1: c.post(f"/api/blocks/{bid}/scan", json={"code": code}, headers=who)
r = scan("2023A7PS0006U").json()
assert r["result"] == "late" and r["student"]["name"] == "Arjun", r
assert scan("2023A7PS0006U").json()["result"] == "already"
ok("guard scans Arjun's ID barcode at 01:40 -> present (late); second scan changes nothing")
assert scan(" 2023a7ps0008u\n").json()["student"]["name"] == "Vihaan"
ok("scanner input is cleaned up (spaces, lower case, Enter)")
r = scan("2023A7PS0011U").json()
assert r["result"] == "refused" and "Block G" in r["message"], r
ok("a Block G card scanned at Block A is refused")
r = scan("04A1B2C3").json()
assert r["result"] == "unknown", r
link = lambda who, sid=7, code="04 a1 b2 c3": c.post("/api/blocks/1/link-card",
                                                     json={"code": code, "student_id": sid}, headers=who)
assert link(guard).status_code == 403 and link(taker).status_code == 403
assert link(warden).status_code == 200
assert link(warden, sid=9).status_code == 400              # one card, one student
assert scan("04A1B2C3").json()["student"]["name"] == "Dhruv"
ok("unknown chip number: only the warden can link it, then the tap checks Dhruv in")
assert c.post("/api/blocks/1/scan", json={"code": "2023A7PS0009U"}, headers=aarav).status_code == 403
assert scan("2023A7PS0009U", who=taker).status_code == 403
assert scan("2023A7PS0011U", who=guard, bid=2).status_code == 403
ok("students, takers and other blocks' guards can't use the scan desk")
fake_now["t"] = at(1, 14, 0)
r = scan("2023A7PS0010U").json()
assert r["result"] == "refused" and "closed" in r["message"], r
ok("ID scan outside check-in hours refused")
fake_now["t"] = at(1, 1, 45)
reg = {r["name"]: r for r in c.get(f"/api/blocks/1/register?night={tonight}", headers=warden).json()["rows"]}
assert reg["Arjun"]["method"] == "id_card" and reg["Arjun"]["marked_by"] == "Guard A"
hist = c.get(f"/api/blocks/1/audit?night={tonight}", headers=warden).json()
assert any(h["action"] == "id_scan" and h["student"] == "Arjun" and h["actor"] == "Guard A (guard)" for h in hist)
assert any(h["action"] == "id_scan_unknown" for h in hist)
ok("ID scans show in the register and the change history, with the guard's name")
ov = c.get("/api/blocks/1/night", headers=taker).json()
assert "Arjun" not in [v["name"] for v in ov["to_visit"] if v["why"] == "not_marked"]
ok("ID-card check-ins count as checked in for the taker's rounds")

# --- network check
campus = ["10.30.0.0/16"]
assert nt.on_campus_network("10.30.87.248", campus)          # phone on BITS-Student
assert not nt.on_campus_network("5.31.10.2", campus)         # mobile data
assert not nt.on_campus_network("192.168.137.23", campus)    # someone else's hotspot
assert not nt.on_campus_network(None, campus) and not nt.on_campus_network("junk", campus)
ok("campus Wi-Fi accepted, mobile data and other networks rejected")
print("\nALL TESTS PASSED")
