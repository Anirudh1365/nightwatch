# NightWatch - Hostel Night Attendance (CampusOPS Problem Statement 04: Night Attendance Scanning)

Students check in on their phone at 22:00-22:15 or 23:30-00:00 by scanning a QR on the
reception screen while connected to the campus Wi-Fi. The attendance taker only
visits rooms that didn't check in, plus a few random spot checks. Gate passes are
approved by the parent (email link) and the warden, and excuse the student for that day.

## Run it
```bash
cd backend
pip install -r requirements.txt
python seed.py            # demo data, every password is demo123
python test_flow.py       # 30 end-to-end checks, should print ALL TESTS PASSED
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
python run_demo.py --pretend 23:35 --reset   # fresh demo data + server acts as if it's 23:35
```
`--pretend` matters: check-in is only open 22:00-22:15 and 23:30-00:00, so a daytime demo
without it gets "Check-in is closed now" (or everyone marked late before noon). `--reset` reloads the demo
data (`seed.py --history`) using the same pretend clock. The pretend clock keeps ticking, so
23:35 gives about 25 minutes of on-time check-ins; restart without `--reset` to reset the clock
and keep the data. Plain `python run_demo.py` uses real time.
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

## Technical overview
```
 phones / reception screen / ID desk / staff laptops   (browser, campus Wi-Fi)
                     |  HTTPS, JSON
              FastAPI server (backend/main.py)
       auth.py: logins, signed tokens, role checks
       night.py: check-in windows, rotating QR, campus Wi-Fi check, spot checks
                     |  SQLAlchemy
          SQLite (demo) or MySQL (production)
```
- **Backend:** Python 3.11+, FastAPI, SQLAlchemy. One process serves both the API (`/api/...`)
  and the pages in `frontend/`. Tables are created on start-up.
- **Frontend:** plain HTML, CSS and JavaScript, no build step. The QR scanner and QR drawing
  libraries and the font are stored in `frontend/vendor/`, so nothing loads from the internet.
- **Logins:** one login page for students and staff. Passwords are stored as bcrypt hashes. After
  login the browser gets a signed token (itsdangerous) and sends it with every request. Each
  endpoint checks the role (student, guard, taker, warden, chief) and the block.
- **Rotating QR:** each reception screen shows a code made from the block number and the current
  15-second slot, signed with HMAC-SHA256 and the server's secret key. The server accepts the
  current slot and the one before it, so a screenshot stops working after about 30 seconds.
- **Campus Wi-Fi check:** the server reads the IP address of the incoming connection and checks it
  against `CAMPUS_NETWORKS`. The phone sends nothing, so there is nothing for the phone to fake.
- **ID card desk:** the camera reads the card's barcode, or a USB tap reader types the chip number.
  Both go through the same check-in rules as the phone.
- **Data:** `blocks`, `students`, `staff`, `gate_passes`, `attendance` (one row per student per
  night: status, method, who marked it, reason), `audit_log` (append-only change history) and
  `outbox` (emails waiting to be sent).
- **Emails:** written to the `outbox` table and printed in the terminal. Sending them for real
  needs an SMTP account from the university.
- **Tests:** `backend/test_flow.py` runs the whole flow (check-ins, refusals, taker rounds, gate
  passes, alerts, ID desk, change history) against a fresh database.

## Deployment (campus server)
The server has to sit on the campus network, because the Wi-Fi check needs to see each phone's
campus IP address. A small Linux VM from IT is enough: each check-in is one short request, so even every
student in all six blocks checking in within half an hour is light work for one server.
1. Install Python 3.11+ and MySQL. Create an empty database, e.g. `nightwatch`.
2. Copy the repo and install: `cd backend && pip install -r requirements.txt`
3. Set these environment variables (for example in a systemd service file):
   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | `mysql+mysqlconnector://USER:PASSWORD@localhost:3306/nightwatch` |
   | `SECRET_KEY` | a long random string (signs logins and QR codes; never leave the default) |
   | `CAMPUS_NETWORKS` | campus Wi-Fi ranges from IT, comma-separated (default `10.30.0.0/16`) |
   | `BASE_URL` | the address people open, e.g. `https://nightwatch.<campus domain>` (used in parent email links) |
   | `CAMPUS_TZ` | `Asia/Dubai` (default) |
   | `EARLY_WINDOW`, `SPOT_CHECKS_PER_NIGHT` | optional, see `backend/config.py` |
4. Load real data: blocks, students (with room and parent email) and staff accounts. `seed.py` shows
   the format; production data would come as a one-off export from the ERP.
5. Start the server: `uvicorn main:app --host 127.0.0.1 --port 8000 --workers 2`
6. Put nginx (or IT's existing web server) in front with a proper HTTPS certificate. Phones need
   HTTPS for the camera. The proxy must pass the real client IP:
   `proxy_set_header X-Forwarded-For $remote_addr;`. Uvicorn trusts this header from 127.0.0.1
   by default; if the proxy is on another machine, add `--forwarded-allow-ips=PROXY_IP`.
   Without this, every check-in looks like it comes from the proxy.
7. Put a screen (any old monitor + browser) at each block reception, logged in as that block's
   guard, showing the reception page. A USB card reader at the desk is optional.
8. Back up the database every night. The change history lives there.

For a demo on one laptop, use `python run_demo.py` instead (see "Phone demo" above).

## Cost (rough estimates)
No licence fees: everything used is open source, and students use their own phones.
| Item | One-off cost |
|---|---|
| Server on the campus network | AED 0 as a VM on IT's existing servers, or about AED 1,500-2,500 for a mini PC |
| Screen at each of the 6 receptions | AED 0 with an old monitor + PC, or about AED 400-700 each for a basic tablet |
| ID desk | AED 0 (uses the same camera) |
| USB tap reader (optional) | about AED 50-250 per desk, once the card's chip type is known |
| Email | AED 0 (university mail server) |

Total: close to AED 0 if IT reuses equipment, roughly AED 8,000 if everything is bought new.
Running cost is close to zero. IT needs about 1-2 days to set it up and a few hours each
semester to load the student list. These are UAE retail estimates (October 2026), not quotes.

## Main endpoints
| Who | Endpoint | What |
|---|---|---|
| All | POST /api/login | returns a signed token (send as `Authorization: Bearer ...`) |
| Student | POST /api/checkin | QR token + Wi-Fi check + time window |
| Guard / warden | POST /api/blocks/{id}/scan | ID card check-in at reception (barcode or chip number), same time rules |
| Warden | POST /api/blocks/{id}/link-card | link an unknown card's chip number to a student |
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
| Warden | GET /api/blocks/{id}/register | every student tonight, exceptions marked |
| Warden | GET /api/blocks/{id}/audit | change history: who changed what, before -> after |
| Warden | GET .../register.csv, .../audit.csv, /api/reports/nightly.csv | CSV downloads |

Emails go to the `outbox` table and are printed in the terminal (swap for SMTP/Mailtrap later).

## Rules built in
- Two check-in windows: 22:00-22:15 for early sleepers (`EARLY_WINDOW` in config.py) and the block's
  main window (default 23:30-00:00). Check in once, in either; 22:15-23:30 is closed. After 00:00
  it still works but is marked late.
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
| index.html | everyone | one login for everyone; sends each person to their own page |
| student.html | student | tonight's status, Scan QR, gate pass request, my passes |
| reception.html | guard / warden | full-screen rotating QR for the reception display |
| guard.html | guard | today's approved passes, links to the reception screen and the ID desk |
| scan.html | guard / warden | ID desk: scan the card's barcode with the camera, or tap it on a USB card reader (types the number + Enter); warden can link unknown cards |
| taker.html | taker (warden too) | rooms to visit, Present (with reason) / Absent / spot checks |
| warden.html | warden / chief | tonight's counts, alerts, register (exceptions), passes to approve, 14-night table, change history, CSV downloads, repeat-absence check |
| parent.html | parent (no login) | opened from the email link: Approve / Reject |

For the demo, `python seed.py --history` adds 14 past nights so the 14-night table and the
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
