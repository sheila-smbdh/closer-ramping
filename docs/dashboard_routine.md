# Ramp dashboard: daily refresh

Dashboard: https://claude.ai/artifact/Mofjm28dzLmLnzHK5SU5vc ("New Closer Ramp Months 1–3").
It shows every 2026 new-hire closer in `config/roster.json` → `dashboard.reps`, for each ramp month 1–3 they've started, next to that month's expectation.
The page reads one dataset, `ramp`, an attached JSON file that this routine rebuilds every morning.

The method is fixed (docs/decisions.md and the docstring in `ramp/dashboard_data.py`). Do not change how numbers are counted in a run.

## Steps

`DATE` = today's date (US Eastern), `YYYY-MM-DD`. Data runs through yesterday.

1. Start from the latest `main`: `git fetch origin main && git checkout -B claude/ramp-dashboard origin/main`. Then `mkdir -p data/snapshots/$DATE`.
2. Get the two sheets (read-only), following docs/sheets.md. That writes `data/snapshots/$DATE/eod_tracker.json` and `sales_inputs.json`.
3. Build: `python3 -m ramp.dashboard_data $DATE`. It writes `outputs/$DATE/ramp_dashboard.json` and prints one line per row. Check that every live rep has a row for their current month, and that no number looks broken (for example 0 live calls for a rep who filed EODs that month).
4. Publish to the dashboard (Artifact tool, url above):
   - Read the `datasets/ramp` document with ArtifactData `get` and note its `version` and its current `source.url`.
   - Upload the new file: Artifact publish with `url`, `asset: true` and `file_path` = `outputs/$DATE/ramp_dashboard.json`. Keep the returned `/_blob/<id>` url exactly as given.
   - ArtifactData `update` `datasets/ramp` with `if_version`: `source` = `{kind: "file", url: <new url>, name: "ramp_dashboard.json", format: "json"}`, and `updated` = `{at: <now, ISO>, by: "Ramp dashboard refresh routine"}`.
   - Delete the old file: Artifact `delete` with `url` and `path` = the old blob id (the 32 characters after `_blob/`). Delete it only after the update succeeds, and only if no page file names it.
5. Commit `data/snapshots/$DATE` and `outputs/$DATE/ramp_dashboard.json`, then push to `claude/ramp-dashboard`. Don't open a PR.

## Rules

- Read-only everywhere except the dashboard's `ramp` dataset and its file, and the git push. No Slack posts, and never write to Google Sheets.
- Don't change the page (`files/index.html`) in a run.
- If a source can't be read, don't publish a partial file. Leave yesterday's data up and say what failed in the run summary.
- New hire: add them to `dashboard.reps` (and `weekly_table.reps`) with their `go_live` and `eod_names` (their name as the EOD Tracker writes it), in a normal session. Their Sales Tracker name is their roster `name` unless `sales_name` says otherwise.
