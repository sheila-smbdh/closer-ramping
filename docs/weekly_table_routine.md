# Weekly ramp table: routines

Two routines post the same ramp table, as an image, for the reps in `config/roster.json` → `weekly_table.reps`:

| Routine | When (ET) | Channel |
|---|---|---|
| Weekly ramp table | Monday morning | #new-hire-onboarding (`C0CD29P851Q`, Sheila and Jay only) |
| Friday ramp table | Friday afternoon | #sales-leadership (`C0ALMSYHZB6`) |

What the image holds:
- One table per group of reps in the same ramp month. Reps within 3 days of each other share a table; anyone else gets their own, at their own current day.
- In the week after a rep's ramp month 3 ends (including when yesterday was its last day), they get one final "Ramp month 3 · complete" table against past closers' full month 3. After that they drop out on their own.

The method is fixed (docs/decisions.md). Do not change how numbers are counted in a run.

## Steps

`DATE` = today's date (US Eastern), `YYYY-MM-DD`. Data runs through yesterday.

1. Start from the latest `main`: `git fetch origin main && git checkout -B claude/weekly-ramp-table origin/main`.
2. `mkdir -p data/snapshots/$DATE`.
3. 2M tracker, read-only. For this month and the previous month (`YYYY-MM`), one request each:
   `curl -s -H "Authorization: Bearer $TRACKER_READ_TOKEN" "https://smbdh-2m-tracker.vercel.app/api/read/closers?month=YYYY-MM" -o data/snapshots/$DATE/closers_YYYY-MM.json`
   Check `ok` is true. If `all_checks_pass` is false, note the failing check's `detail` for the Slack message. Never print the token.
4. EOD reports. Read Slack channel #closer-eod (`C06RE8S1L4V`) with `slack_read_channel`, `oldest` = midnight UTC one day before the earliest go-live (or current ramp-month start) of the reps in `weekly_table.reps`, `limit` 100, `response_format` concise. Follow `pagination_info` cursors until it says there are no more messages, or a cursor repeats.
   - A large result is saved to a file under the session's tool-results folder; keep each path.
   - If a result comes back inline, write that JSON to `data/snapshots/$DATE/slack_page_N.json` before moving on.
   - Then: `python3 -m ramp.slack_eod data/snapshots/$DATE/eod_slack.json <every page file>`
5. Close meetings, read-only. These are used to count only 45-minute discovery calls; 15-minute intro (setter) calls are left out. For each rep in `weekly_table.reps`, use their `close_user_id` from the roster. Make one inline curl per page, never in a script:
   `curl -s -u "$CLOSE_API_KEY:" "https://api.close.com/api/v1/activity/meeting/?user_id=<close_user_id>&date_created__gte=<go-live minus 30 days>&_limit=100&_skip=<0,100,…>" -o data/snapshots/$DATE/close_meetings_<name>_<N>.json`
   Keep paging while `has_more` is true. An empty `$CLOSE_API_KEY` is expected, because the proxy adds the login. Only if the API call itself fails (401/403/5xx/network), fall back to the Close connector and save the same meeting fields.
6. Build: `python3 -m ramp.weekly_table $DATE`. It writes `outputs/$DATE/weekly_table.png`, `.html` and `.md`.
7. Look at the PNG. Check that each ramping rep's row is filled in and that the day and dates in the footnote are right. If a rep shows 0 live calls with EODs in the channel, the parser missed a format: fix `ramp/slack_eod.py`, rerun, and say so in the run summary.
8. Post to Slack: `slack_get_file_upload_url` (filename `ramp_table_$DATE.png`, exact byte size, alt text from `weekly_table.md`), POST the bytes with curl (`Content-Type: image/png`), then `slack_complete_file_upload` with the `channel_id` for this routine (table above), title `Ramp table, data through <M/D>`, and a one-line `initial_comment` from `weekly_table.md`. Add any failed tracker check to the comment. Post only to that one channel, and post only this one file.
   - If nobody on the list is in ramp months 1–3, post nothing. End the run and say that `weekly_table.reps` needs updating.
9. Commit `data/snapshots/$DATE` and `outputs/$DATE`, then push to `claude/weekly-ramp-table`. Don't open a PR.

## Rules

- Live calls count 45-minute discovery calls only (EOD shows capped at Close's 45-min bookings each day). Intro calls aren't counted until there's a reliable Zoom length to check them against; Sheila is looking into that.
- Read-only everywhere except the one Slack post and the git push. No writes to Close, the tracker, or Google Sheets.
- If the upload fails partway, check whether the file was already shared in the channel before trying again, so it never posts twice.
- Commission and replacement expiry come from `config/roster.json` (`commission_pct`, `replacement_deadline`), which mirrors the New Hire Tracker Roster tab. The routine can't read Google Sheets, so update those fields in a normal session when the sheet changes.
- Past closers come from `weekly_table.past_snapshot`. When a new cohort finishes a month and should become a comparison row, that needs a fresh snapshot and a config change, made in a normal session.
