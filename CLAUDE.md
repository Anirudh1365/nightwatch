# NightWatch — context for Claude Code

IEEE BPDC CampusOPS hackathon, problem statement PS03 (Nighttime Attendance & Presence
Scanning). Deadline: 3 October 2026. Campus: BITS Pilani Dubai. Read README.md for how to run.

## The real-world process we are replacing
- An attendance taker walks room to room at ~10:30 PM and marks attendance on paper.
- Students who come back late write their room number in a notebook at the block reception,
  watched by a security guard. No name or time verification.
- Gate passes: parent emails the block warden from their registered email; warden posts a
  screenshot in a staff-only WhatsApp group (wardens, guards, chief warden); guard checks it.
  A pass is valid for ONE day only.
- Main gate curfew: 11:30 PM boys, 11:00 PM girls (girls' curfew is a one-month pilot;
  management said it will be reversed if late coming continues — our nightly report gives
  them that data). Girls' hostel in-time is 12:00 midnight.
- 6 blocks: 4 boys, 2 girls. Main gate has a face scanner (exit + entry) — we do NOT
  integrate it now (no data access); it is "phase 2" in the pitch.
- Complaints are OUT OF SCOPE: the university's ERP support portal already handles them
  and the Student Council asked students to use it.

## Design decisions (keep these)
- Check-in window per block: 23:30–00:00. After 00:00 still allowed but marked late.
  A "night" runs noon to noon.
- Check-in requires BOTH: scanning a QR on the reception screen that rotates every 15 s,
  AND the request coming from the campus Wi-Fi (BITS-Student), checked server-side from the
  connection IP against CAMPUS_NETWORKS in config.py (default 10.30.0.0/16). The team
  decided "on campus vs mobile data" is enough; block identity comes from the block's QR.
  No GPS — it is easy to fake and unreliable indoors.
- ERP (Oracle APEX on Oracle Cloud) is not integrated; NightWatch runs beside it. A cloud
  server can't see per-device campus IPs, only the campus public IP.
- Approved gate pass for that date = excused.
- Taker visits only unmarked rooms + ~5 random spot checks among students who self-checked in.
  Manual "present" needs a reason (asleep / sick / phone_issue / other).
  Spot-check room empty = high-severity alert.
- Unexcused absence -> warden alert the same night.
- 3 absences OR 5 manual marks in any 14 nights -> email + meeting with warden.
- One login page with Student / Staff tabs. Staff roles: guard, taker, warden, chief.
  Parents have no login (one-time email link).

## Stack and code
- Backend: FastAPI + SQLAlchemy in /backend (SQLite by default, MySQL via DATABASE_URL).
  Signed bearer tokens, bcrypt passwords, role checks in auth.py. Time/QR/network logic in night.py.
- `python test_flow.py` must keep passing (reseed with `python seed.py` after).
- Frontend: plain HTML/CSS/JS in /frontend, served by FastAPI. No build step.
  Never serve the backend folder statically.

## Frontend (all pages built)
Shared: styles.css (light/dark tokens, chart colours --c-ontime/--c-late/--c-absent were
checked with a colour-blind validator), api.js (`NW` helper: session in localStorage,
`NW.api()` fetch wrapper, `NW.requireLogin(roles)`, `NW.homeFor(role)`, `NW.blockPicker()`
for the chief's block dropdown, esc/hhmm/niceDate/show).
Pages: index (login), student, reception, guard, taker, warden, parent.
Landing by role: student→student, taker→taker, guard→guard, warden/chief→warden.
`python seed.py --history` = demo data with 14 past nights (not for test_flow).
Libraries are local in frontend/vendor/ (no CDN). Phone demo: `python run_demo.py` (HTTPS, self-signed cert in backend/certs/, gitignored).

## Rules for changes
- Explain in simple terms; the team prefers plain language.
- Be honest about weaknesses; don't overclaim in UI text or the pitch.
- Phone camera needs HTTPS; tunnels (ngrok) break the Wi-Fi IP check.
