# Getting the EOD Tracker and Sales Tracker

Both the Saturday ramp table and the daily dashboard read two Google Sheets, read-only:

| Sheet | File id | Used for |
|---|---|---|
| EOD Tracker (Sheet1) | `11PnemLh8wDT-baqbYBcW3cw0zv4vl82J7NumHTh7dCc` | scheduled, showed, no-show, cancelled, rescheduled, offers, as each closer reported them |
| New Hire Tracker (Roster) | `1I2HbtdeFMC_rszI9BJnm3hozt5XQDkyFJ0XP481Kp3Q` | who is ramping, go-live dates, status, replacement deadline (daily milestone sweep; `python3 -m ramp.roster_sheet`) |
| Sales Tracker 2026 (Inputs) | `1Y7LCecej4lj2XNfVw0b_9L0KAp7Qj8WKeYM711UGsp0` | days with no EOD (Sched. calls, Live calls, Offers), closes (Close 1) and cash |

Sheila, 11/3: the EOD Tracker is accurate for scheduled and live calls, historical dates included. This is also how the closer-scorecard repo counts them.

Steps:
1. With the Google Drive connector, call `download_file_content` for each file id with `exportMimeType` `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`. Each result is too large to show, so it is saved to a tool-results file (JSON with a base64 `content`). Keep both paths.
2. Decode each file into its own new folder, outside the repo:
   `python3 -c "import json,base64,sys; j=json.load(open(sys.argv[1])); open(sys.argv[2],'wb').write(base64.b64decode(j['content']))" <saved result> <folder>/eod.xlsx` (and the same for `sales.xlsx`).
3. `python3 -m ramp.sheets data/snapshots/$DATE <folder>/eod.xlsx <folder>/sales.xlsx`. It prints the row counts, and writes `eod_tracker.json` and `sales_inputs.json`.
4. If either download fails or a count is 0, stop and say so. Don't build from old data.

If `python3` can't import `openpyxl` (on some routine machines the default `python3` is 3.11 without it), run every `python3 -m ramp...` step with `python3.13` instead.

Never edit either sheet.
