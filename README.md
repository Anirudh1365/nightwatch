# NightWatch - Hostel Night Attendance (CampusOPS PS03)

Students check in on their phone between 23:30 and 00:00 by scanning a QR on the
reception screen while connected to the campus Wi-Fi. The attendance taker only
visits rooms that didn't check in, plus a few random spot checks. Gate passes are
approved by the parent (email link) and the warden, and excuse the student for that day.

## Run it
```bash
cd backend
pip install -r requirements.txt
python seed.py            # demo data, every password is demo123
python test_flow.py       # 15 end-to-end checks, should print ALL TESTS PASSED
python seed.py            # reset data after the tests
uvicorn main:app --reload --host 0.0.0.0
```
API docs: http://127.0.0.1:8000/docs  (try every endpoint from the browser)

Uses SQLite by default. For MySQL:
`DATABASE_URL=mysql+mysqlconnector://root:PASSWORD@localhost:3306/nightwatch`
(create the empty database first; tables are created automatically).

## Demo logins (password: demo123)
| Role | Email |
|---|---|
| Chief warden | chief@demo.edu |
| Block A warden / taker / guard | warden.a@ / taker.a@ / guard.a@demo.edu |
| Block G warden / taker / guard | warden.g@ / taker.g@ / guard.g@demo.edu |
| Students (Block A) | aarav@, vivaan@, ishaan@, kabir@ ... @demo.edu |
| Students (Block G) | ananya@, diya@ ... @demo.edu |

## The Wi-Fi check (how to demo it)
The server checks the *connection's* IP address, so the phone reports nothing and
fake-GPS apps don't help. Check-in only works from the campus Wi-Fi (BITS-Student).
Mobile data or home Wi-Fi is refused. Which block the student is in comes from the QR:
each reception screen shows its own block's code, and spot checks back it up.
- Campus ranges are set by `CAMPUS_NETWORKS` (default `10.30.0.0/16`, BITS-Student gave
  us 10.30.64.0/19; hostel blocks checked on 29 Sep 2026 also get 10.30.x). Comma-separate
  several ranges if IT adds more.
- If the server ever runs in the cloud (e.g. next to the ERP, which is on Oracle Cloud),
  every campus phone arrives from the campus's public IP: put that IP in `CAMPUS_NETWORKS`.
- Do NOT use ngrok/tunnels for this demo: every request would come from the tunnel's IP.
- For API testing off campus: `ENFORCE_NETWORK=false uvicorn main:app`

## Phone demo (HTTPS on campus Wi-Fi)
Phone browsers only allow the camera on HTTPS. One command sets that up:
```bash
cd backend
python run_demo.py --pretend 23:45 --reset   # fresh demo data + server acts as if it's 23:45
```
`--pretend` matters: check-in only opens at 23:30, so a daytime demo without it gets
"Check-in opens at 23:30" (or everyone marked late before noon). `--reset` reloads the demo
data (`seed.py --history`) using the same pretend clock. Plain `python run_demo.py` uses real time.
It makes a self-signed certificate in `backend/certs/` the first time, prints the address
to open on phones, and points parent email links at the laptop.
1. Laptop and phone both on **BITS-Student**. `run_demo.py` prints the laptop's campus
   address (e.g. `https://10.30.83.168:8000`); it changes when the laptop reconnects.
2. The first time, Windows Firewall asks about Python: allow it (tick public networks too,
   BITS-Student counts as public).
3. On the phone, open that address, tap Advanced -> Proceed on the certificate warning,
   log in as a Block A student.
4. Reception screen: log in as `guard.a@demo.edu` on the laptop -> Open reception screen.
5. Scan -> checked in.
6. To show a refusal: turn on the laptop's **Mobile hotspot**, put a phone on it, open
   `https://192.168.137.1:8000` and scan as another student. The hotspot isn't campus
   Wi-Fi, so it's refused. (Real mobile data can't reach a laptop on the campus network at all.)

Every check-in attempt prints a `[check-in]` line in the terminal with the reason.
`python run_demo.py --no-wifi-check` turns the Wi-Fi check off for rehearsals.

## Main endpoints
| Who | Endpoint | What |
|---|---|---|
| All | POST /api/login | returns a signed token (send as `Authorization: Bearer ...`) |
| Student | POST /api/checkin | QR token + Wi-Fi check + time window |
| Student | POST /api/passes, GET /api/passes/mine | request a one-day gate pass |
| Parent | GET/POST /api/parent/{token} | one-time approve/reject link from email |
| Warden | GET /api/blocks/{id}/passes, POST /api/passes/{id}/decide | approve passes |
| Guard | GET /api/blocks/{id}/passes | today's approved passes only |
| Reception screen | GET /api/blocks/{id}/qr | QR token, changes every 15 s |
| Taker | GET /api/blocks/{id}/night | who to visit tonight |
| Taker | POST /api/blocks/{id}/night/mark | present (with reason) / absent |
| Warden | GET /api/blocks/{id}/alerts | absent tonight with no pass |
| Warden | POST /api/flags/run | 3 absences or 5 manual marks in 14 nights -> email |
| Warden | GET /api/reports/nightly | per-night present / late / absent counts |

Emails go to the `outbox` table and are printed in the terminal (swap for SMTP/Mailtrap later).

## Rules built in
- Check-in window per block (default 23:30-00:00). After 00:00 it still works but is marked late.
  A night runs noon to noon, so 00:40 counts for the previous night.
- QR is valid for its 15 s slot plus the previous one; old screenshots fail.
- Approved gate pass for that date = excused. If they return and check in, they're present.
- Taker marks present only with a reason (asleep / sick / phone_issue / other).
- Spot check: taker confirms -> stays a normal check-in. Room empty -> "high" alert.
- Student marked absent who checks in later -> flipped to present, late.

## Pages
Open http://127.0.0.1:8000 and log in. Each role lands on its own page.
| Page | Who | What |
|---|---|---|
| index.html | everyone | login, Student / Staff tabs |
| student.html | student | tonight's status, Scan QR, gate pass request, my passes |
| reception.html | guard / warden | full-screen rotating QR for the reception display |
| guard.html | guard | today's approved passes, link to the reception screen |
| taker.html | taker (warden too) | rooms to visit, Present (with reason) / Absent / spot checks |
| warden.html | warden / chief | tonight's counts, alerts, passes to approve, 14-night chart, repeat-absence check |
| parent.html | parent (no login) | opened from the email link: Approve / Reject |

For the demo, `python seed.py --history` adds 14 past nights so the chart and the
repeat-absence check have data (Rohan in Block A gets flagged). Run the tests on a plain
`python seed.py`, not on history data.

The QR scanner and QR drawing libraries are saved in `frontend/vendor/`, so the demo works
without internet.

## Known limits (say these in the pitch)
- The Wi-Fi check proves "on campus", not "in this block". A friend in the lobby could
  forward the live QR to someone elsewhere on campus; the 30 s QR life and random spot
  checks cover this. If a block ever needs more, a small Wi-Fi router at its reception
  (check-in only on that network) is a cheap upgrade.
- Parent email is the trust anchor, same as today.
- Production needs the university's student list, parent emails and campus Wi-Fi ranges from IT.
- ERP: NightWatch runs beside it (link from the ERP home page); single sign-on and
  importing student data from the ERP are phase 2.
- Phase 2: link the main-gate face scanner log to catch anyone who left without a pass.
