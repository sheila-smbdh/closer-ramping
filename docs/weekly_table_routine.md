# Weekly ramp table: routines

The ramp table is posted, as an image, for the reps in `config/roster.json` → `weekly_table.reps`:

| Routine | When (ET) | Channel |
|---|---|---|
| Saturday ramp table (the one weekly send, from 11/3) | Saturday morning, data through Friday | #new-hire-onboarding (`C0CD29P851Q`, Sheila and Jay only) |
| Weekly ramp table (paused 11/3) | Monday morning | #new-hire-onboarding (`C0CD29P851Q`) |

What the image holds:
- One table per group of reps in the same ramp month. Reps within 3 days of each other share a table; anyone else gets their own, at their own current day.
- In the week after a rep's ramp month 3 ends (including when yesterday was its last day), they get one final "Ramp month 3 · complete" table against past closers' full month 3. After that they drop out on their own.

The method is fixed (docs/decisions.md). Do not change how numbers are counted in a run.

## Steps

`DATE` = today's date (US Eastern), `YYYY-MM-DD`. Data runs through yesterday.

1. Start from the latest `main`: `git fetch origin main && git checkout -B claude/weekly-ramp-table origin/main`. Then `mkdir -p data/snapshots/$DATE`.
2. Get the two sheets (read-only), following docs/sheets.md. That writes `data/snapshots/$DATE/eod_tracker.json` and `sales_inputs.json`.
3. (Steps 3–5 retired 11/3: the tracker API, #closer-eod and Close meetings are no longer used for this table.)
6. Build: `python3 -m ramp.weekly_table $DATE`. It writes `outputs/$DATE/weekly_table.png`, `.html` and `.md`.
7. Look at the PNG. Check that each ramping rep's row is filled in and that the day and dates in the footnote are right.
8. Post to Slack: `slack_get_file_upload_url` (filename `ramp_table_$DATE.png`, exact byte size, alt text from `weekly_table.md`), POST the bytes with curl (`Content-Type: image/png`), then `slack_complete_file_upload` with the `channel_id` for this routine (table above), title `Ramp table, data through <M/D>`, and a one-line `initial_comment` from `weekly_table.md`. Add any failed tracker check to the comment. Post only to that one channel, and post only this one file.
   - If nobody on the list is in ramp months 1–3, post nothing. End the run and say that `weekly_table.reps` needs updating.
9. Commit `data/snapshots/$DATE` and `outputs/$DATE`, then push to `claude/weekly-ramp-table`. Don't open a PR.

## Rules

- Since 11/3, scheduled calls, live calls and offers come from the EOD Tracker, as the closers reported them (the Sales Tracker fills days with no EOD), and closes and cash from the Sales Tracker. See docs/sheets.md.
- Read-only everywhere except the one Slack post and the git push. Never write to Google Sheets, Close or the tracker.
- If the upload fails partway, check whether the file was already shared in the channel before trying again, so it never posts twice.
- Commission and replacement expiry come from `config/roster.json` (`commission_pct`, `replacement_deadline`), which mirrors the New Hire Tracker Roster tab. The routine can't read Google Sheets, so update those fields in a normal session when the sheet changes.
- Past closers come from `weekly_table.past_snapshot`. When a new cohort finishes a month and should become a comparison row, that needs a fresh snapshot and a config change, made in a normal session.
