# Weekly ramp table: Monday routine

Posts the "everyone at the same point in the ramp month" table, as an image, to #new-hire-onboarding (`C0CD29P851Q`, Sheila and Jay only). It runs every Monday morning, US Eastern. Which reps it covers is set in `config/roster.json` → `weekly_table.reps`.

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
5. Build: `python3 -m ramp.weekly_table $DATE`. It writes `outputs/$DATE/weekly_table.png`, `.html` and `.md`.
6. Look at the PNG. Check that each ramping rep's row is filled in and that the day and dates in the footnote are right. If a rep shows 0 live calls with EODs in the channel, the parser missed a format: fix `ramp/slack_eod.py`, rerun, and say so in the run summary.
7. Post to Slack: `slack_get_file_upload_url` (filename `ramp_table_$DATE.png`, exact byte size, alt text from `weekly_table.md`), POST the bytes with curl (`Content-Type: image/png`), then `slack_complete_file_upload` with `channel_id` `C0CD29P851Q`, title `Ramp table, data through <M/D>`, and a one-line `initial_comment` from `weekly_table.md`. Add any failed tracker check to the comment. Post only to this channel, and post only this one file.
   - If nobody on the list is in ramp months 1–3, post nothing. End the run and say that `weekly_table.reps` needs updating.
8. Commit `data/snapshots/$DATE` and `outputs/$DATE`, then push to `claude/weekly-ramp-table`. Don't open a PR.

## Rules

- Read-only everywhere except the one Slack post and the git push. No writes to Close, the tracker, or Google Sheets.
- If the upload fails partway, check whether the file was already shared in the channel before trying again, so it never posts twice.
- Past closers come from `weekly_table.past_snapshot`. When a new cohort finishes a month and should become a comparison row, that needs a fresh snapshot and a config change, made in a normal session.
