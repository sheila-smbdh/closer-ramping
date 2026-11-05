# Daily milestone sweep: routine

Every morning, check every closer on the calendar for milestones Jay and Sheila need to act on. If anything is due, post **one** message to #new-hire-onboarding (`C0CD29P851Q`, Sheila and Jay only) tagging both. If nothing is due, post nothing.

What counts as a milestone (code: `ramp/milestones.py`, docstring has the full rules):

| Milestone | When it posts |
|---|---|
| Free replacement period ending (`replacement_deadline` in `config/roster.json`) | 14, 7 and 3 days before, and the last weekday before it |
| Ramp month 1, 2 or 3 ending | 7 days before the month's last day, with pace against target and floor |
| Ramp month 1, 2 or 3 ended | The first weekday after, with final numbers and the decision it triggers (path, keep, cut) |
| 5th and 10th showed call while Jay has open items in `config/feedback.json` | The weekday after the call |

Advance reminders that would land on a weekend move to the Friday before, and results move to the Monday after. Each reminder posts once: `data/milestones_sent.json` on branch `claude/milestone-reminders` logs what went out.

## Steps

`DATE` = today's date (US Eastern), `YYYY-MM-DD`. Data runs through yesterday.

1. Start from the branch that holds the sent log, brought up to date with `main`:
   `git fetch origin main claude/milestone-reminders`, then `git checkout -B claude/milestone-reminders origin/claude/milestone-reminders && git merge --no-edit origin/main`. If the branch doesn't exist yet (first run), `git checkout -B claude/milestone-reminders origin/main`. Then `mkdir -p data/snapshots/$DATE`.
2. Get the two sheets (read-only), following docs/sheets.md. That writes `data/snapshots/$DATE/eod_tracker.json` and `sales_inputs.json`. If a sheet can't be read, carry on without it: the reminders still go out, without numbers, and the message says so.
3. Sweep: `python3 -m ramp.milestones $DATE` (use `python3.13` if `python3` lacks openpyxl). It prints the message and writes `outputs/$DATE/milestones.md` and `milestones.json`.
   - If it prints "Nothing of note today: post nothing.", post nothing. Go to step 6.
4. Read the message. Check the names, dates and numbers look right (no rep with 0 live calls who filed EODs, no deadline already past).
5. Post the contents of `outputs/$DATE/milestones.md` exactly as written, as one `slack_send_message` to `C0CD29P851Q`. Then run `python3 -m ramp.milestones $DATE --mark` to log it as sent.
6. Commit `data/milestones_sent.json`, `data/snapshots/$DATE` and `outputs/$DATE/milestones.*`, then `git push -u origin claude/milestone-reminders`. Don't open a PR.

## Rules

- The one post to `C0CD29P851Q` is the only write outside git. Never message anyone else, never write to Google Sheets, Close or the tracker.
- Post only once a day. If the send fails or its result is unclear, read the channel before retrying, so it never posts twice. Run `--mark` only after the message is in the channel.
- Don't change the milestone rules, targets or wording in a run.
- Dates come from `config/roster.json` and Jay's open items from `config/feedback.json`. The routine can't read the New Hire Tracker, so update those files in a normal session when the sheet changes (new hire, new go-live date, new replacement deadline, an item fixed).
