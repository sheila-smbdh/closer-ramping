"""The Roster tab of the New Hire Tracker: the source of truth for who is ramping and their dates (Sheila, 11/5).

New Hire Tracker: Google Sheet 1I2HbtdeFMC_rszI9BJnm3hozt5XQDkyFJ0XP481Kp3Q, tab "Roster". People are columns; each
role block starts with a "Role" row (New Hire Closer, Setter Promote) followed by labelled rows (Name, Org Start Date,
Closer calendar go-live date, Status, Referral Payout Status, Deadline, Closing Commission %, ...).

Usage: python3 -m ramp.roster_sheet <snapshot_dir> <new_hire_tracker.xlsx>
Writes <snapshot_dir>/roster_sheet.json: one entry per person, with dates as YYYY-MM-DD and "NA" or blank as null.
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

FIELDS = {
    "Role": "role",
    "Name": "name",
    "Org Start Date": "org_start",
    "Setter Ascension Start Date": "ascension_start",
    "First self-set call date": "first_self_set",
    "Closer calendar go-live date": "go_live",
    "Status": "sheet_status",
    "Referral Payout Status": "referral_payout_status",
    "Deadline": "replacement_deadline",
    "Closing Commission %": "commission_pct",
}


def _val(v):
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if v is None or (isinstance(v, str) and v.strip().upper() in ("", "NA", "N/A", "-")):
        return None
    return v.strip() if isinstance(v, str) else v


def parse(xlsx):
    import openpyxl
    ws = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)["Roster"]
    people, block = [], None
    for row in ws.iter_rows(values_only=True):
        label = str(row[0]).strip() if row and row[0] else ""
        if label == "Role":
            if block:
                people += block
            block = [{"role": _val(v)} for v in row[1:]]
            continue
        if block is None or label not in FIELDS:
            continue
        for i, v in enumerate(row[1:len(block) + 1]):
            block[i][FIELDS[label]] = _val(v)
    if block:
        people += block
    out = []
    for p in people:
        if not p.get("name"):
            continue
        p["name"] = str(p["name"]).strip()
        p["track"] = "setter_promote" if "promote" in str(p.get("role") or "").lower() else "new_hire_closer"
        out.append(p)
    return out


def export(snap, xlsx):
    people = parse(xlsx)
    snap = Path(snap)
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "roster_sheet.json").write_text(json.dumps(people, indent=1))
    return people


def merged(snap, config_roster):
    """The sheet's people and dates, with lookup keys (eod_names, sales_name, ...) from config/roster.json.

    The sheet wins on every field it has. Someone on the sheet but not in config still gets checked, with their sheet
    name as their EOD and Sales Tracker name.
    """
    sheet = json.loads((Path(snap) / "roster_sheet.json").read_text())
    cfg = {p["name"]: p for p in config_roster["people"]}
    out = []
    for s in sheet:
        p = dict(cfg.get(s["name"], {"eod_names": [s["name"]]}))
        p.update({k: v for k, v in s.items() if k != "role"})
        p["status"] = "out" if (s.get("sheet_status") or "").lower() == "out" else "active"
        p.pop("comparison_only", None)
        out.append(p)
    return out


if __name__ == "__main__":
    for p in export(sys.argv[1], sys.argv[2]):
        print(p["name"], p["track"], p.get("go_live"), p.get("sheet_status"), p.get("replacement_deadline"))
