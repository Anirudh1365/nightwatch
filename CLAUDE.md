# NightWatch — context for Claude Code

IEEE BPDC CampusOPS hackathon, Problem Statement 04 (Night Attendance Scanning; the
numbering changed on 2 Oct 2026, it used to be PS03). Deadline: 3 October 2026. Campus: BITS Pilani Dubai. Read README.md for how to run.

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
- Two check-in windows a night: 22:00–22:15 (early sleepers; EARLY_WINDOW in config.py, same for
  all blocks) and the block's main window 23:30–00:00. A student checks in ONCE, in either.
  22:15–23:30 is closed. After 00:00 still allowed but marked late.
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
- Change history (AuditLog table, `audit()` in main.py): append-only. Every check-in attempt
  (also refused and repeated ones), manual mark (before -> after), gate pass decision and
  repeat-absence email adds a row. Nothing edits or deletes rows. The brief asks for
  "an auditable record of changes" and "downloadable reports" -- the warden page has the
  register, change history and CSV downloads (`NW.download()` sends the login token).
- Two ways to check in, same time rules (`check_in_closed()` + `mark_present()` in main.py):
  phone (rotating QR + campus Wi-Fi, method "self") or university ID card at the block's reception
  desk (scan.html, method "id_card", marked_by = the guard). The card has a barcode (camera reads
  it; seed IDs look like 2023A7PS0001U) and a chip (the printer top-up machine reads it by tap). A USB
  tap reader types the chip number + Enter into the desk's input box. We don't know the chip-number
  -> student mapping, so an unknown card is linked once by the warden (Student.card_uid).
  ID-card check-ins are in the spot-check pool like phone ones (SELF_METHODS).
- One login page, no Student / Staff choice: the server tries student then staff accounts and
  the matching account decides the landing page. Staff roles: guard, taker, warden, chief.
  Parents have no login (one-time email link).

## Stack and code
- Backend: FastAPI + SQLAlchemy in /backend (SQLite by default, MySQL via DATABASE_URL).
  Signed bearer tokens, bcrypt passwords, role checks in auth.py. Time/QR/network logic in night.py.
- `python test_flow.py` must keep passing (reseed with `python seed.py` after).
- Frontend: plain HTML/CSS/JS in /frontend, served by FastAPI. No build step.
  Never serve the backend folder statically.

## Frontend (all pages built)
Shared: styles.css (light-only, styled to match the BITS ERP Gateway: Inter font in vendor/, navy #211D70,
gold #FCB017, buttons #2B2B88; the warden's 14-night report is a table only, the user asked
for no chart), api.js (also adds the top-left home icon; `NW` helper: session in localStorage,
`NW.api()` fetch wrapper, `NW.requireLogin(roles)`, `NW.homeFor(role)`, `NW.blockPicker()`
for the chief's block dropdown, esc/hhmm/niceDate/show).
Pages: index (login), student, reception, guard, scan (ID desk), taker, warden, parent.
Landing by role: student→student, taker→taker, guard→guard, warden/chief→warden.
`python seed.py --history` = demo data with 14 past nights (not for test_flow).
Libraries are local in frontend/vendor/ (no CDN). Phone demo: `python run_demo.py` (HTTPS, self-signed cert in backend/certs/, gitignored).

## The brief (PS04, organisers' PDF of 2 Oct 2026) and how we meet it
"Record night attendance by scanning a university-issued student ID or another approved
credential at designated locations." The team is pursuing this statement only.
- Authenticate + record date, time, location -> login or ID card; timestamp; block QR / block desk.
- Prevent duplicate / invalid / unauthorized entries -> rotating QR, Wi-Fi check, block match,
  one check-in a night, role checks. Refusals are kept in the change history.
- Real-time dashboard with exception indicators -> warden page: counts, alerts, register
  (exceptions = late, absent, not checked in, marked by hand).
- Review missing / late / manually verified entries with an auditable record -> register +
  change history (before -> after, who, when).
- Summaries + downloadable reports -> 14-night table; CSV of register, 14 nights, change history.
- Notifications / escalation -> same-night warden alert; 3 absences / 5 manual marks -> email.
- Constraints: existing ID systems -> university ID card (barcode / tap); ERP link is phase 2.
  Controlled fallback -> taker marks by hand (reason required), ID desk if a phone fails.
  Limited connectivity -> NOT handled yet (needs the server reachable on the campus network).

## Where we are (end of 2 Oct 2026 session)
- Done and tested (`test_flow.py`: 30 checks pass; warden page and ID desk checked in a browser
  at phone and desktop sizes): change history, warden register, CSV downloads, ID desk.
- NOTHING IS COMMITTED. The working tree also holds earlier sessions' work (two windows, single
  login, ERP-style restyle, pitch doc edits). Commit under the laptop's existing git identity.
- No servers running. Database reset with `python seed.py` (clean, no history).
- Still to do:
  1. Pitch doc (`docs/NightWatch_Pitch_and_Demo_Script.docx` + .pdf) still says PS03 and doesn't
     mention the ID desk, change history or CSV downloads. Also: team name + presenter split,
     check the curfew-pilot wording.
  2. Test a REAL BITS ID card with the camera (`python run_demo.py`, https link). Its number won't
     be in the demo data: log in as warden, link it at the ID desk, scan again. This also shows
     what the barcode actually contains. Tap reader only if a USB reader is available (chip type
     unknown, don't buy blind).
  3. Optional: offline mode for the taker page (save marks on the phone, send when back online).
  4. Full rehearsal. Demo: `python run_demo.py --pretend 23:35 --reset` in backend/.
- Pitch weakness to say out loud: a student could hand their ID card to a friend. Answer: the
  guard is at the desk and sees the name on screen; random spot checks still apply.

## Rules for changes
- Explain in simple terms; the team prefers plain language.
- Be honest about weaknesses; don't overclaim in UI text or the pitch.
- Phone camera needs HTTPS; tunnels (ngrok) break the Wi-Fi IP check.
