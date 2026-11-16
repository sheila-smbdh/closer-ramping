"""Calls and closes from the team's own sheets (Sheila, 11/3): the EOD Tracker and the Sales Tracker 2026.

- EOD Tracker (Google Sheet 11PnemLh8wDT-baqbYBcW3cw0zv4vl82J7NumHTh7dCc, Sheet1): scheduled, showed, no-show,
  cancelled, rescheduled and offers, as each closer reported them. Accurate for historical dates too.
- Sales Tracker 2026 (1Y7LCecej4lj2XNfVw0b_9L0KAp7Qj8WKeYM711UGsp0, Inputs): fills any day a closer has no EOD
  report (Sched. calls, Live calls, Offers), and is the source of closes (Close 1) and cash.
This is how the closer-scorecard repo counts them (docs/HANDOFF.md there, "Sources of truth").

Usage: python3 -m ramp.sheets <snapshot_dir> <eod.xlsx> <sales.xlsx>
Writes <snapshot_dir>/eod_tracker.json and <snapshot_dir>/sales_inputs.json (only the columns used here).
"""
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from ramp.build import add_months


def _num(v):
    try:
        return float(str(v).replace("$", "").replace(",", "")) if v not in (None, "") else None
    except ValueError:
        return None


def _day(v):
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    try:
        return str(v)[:10] if v else None
    except Exception:
        return None


def _cell(r, i):
    return r[i] if i < len(r) else None


def export(snap, eod_xlsx, sales_xlsx):
    import openpyxl
    wb = openpyxl.load_workbook(eod_xlsx, read_only=True, data_only=True)
    rows = list(wb["Sheet1"].iter_rows(values_only=True))
    hdr = list(rows[0])
    ix = {h: hdr.index(h) for h in ("report_date", "closer_name", "scheduled", "showed", "no_showed", "cancelled",
                                     "rescheduled", "offers_made", "closed", "cash_collected", "slack_posted_at_utc",
                                     "showed_leads")}
    eod = []
    for r in rows[1:]:
        day, name = _day(r[ix["report_date"]]), r[ix["closer_name"]]
        if not day or not name or not day[:4].isdigit():
            continue
        eod.append({"date": day, "name": str(name).strip(),
                    **{k: _num(r[ix[k]]) for k in ("scheduled", "showed", "no_showed", "cancelled", "rescheduled",
                                                   "offers_made", "closed", "cash_collected")},
                    "posted": str(r[ix["slack_posted_at_utc"]] or ""),
                    # Close lead ids of the calls that showed (listed on EODs from mid-September on).
                    "showed_lead_ids": re.findall(r"lead_[A-Za-z0-9]+", str(_cell(r, ix["showed_leads"]) or ""))})
    wb = openpyxl.load_workbook(sales_xlsx, read_only=True, data_only=True)
    rows = list(wb["Inputs"].iter_rows(values_only=True))
    hdr = list(rows[0])
    ix = {h: hdr.index(h) for h in ("Closer", "Date", "Sched. calls", "Live calls", "Offers", "Close 1", "Cash Collected")}
    st = []
    for r in rows[1:]:
        day, name = _day(r[ix["Date"]]), r[ix["Closer"]]
        if not day or not name or not day[:4].isdigit():
            continue
        st.append({"date": day, "name": str(name).strip(), "sched": _num(r[ix["Sched. calls"]]),
                   "live": _num(r[ix["Live calls"]]), "offers": _num(r[ix["Offers"]]),
                   "closes": _num(r[ix["Close 1"]]), "cash": _num(r[ix["Cash Collected"]])})
    snap = Path(snap)
    (snap / "eod_tracker.json").write_text(json.dumps(eod))
    (snap / "sales_inputs.json").write_text(json.dumps(st))
    return len(eod), len(st)


class Sheets:
    """Per-rep, per-day numbers from the two sheets, merged the scorecard's way."""

    def __init__(self, snap):
        snap = Path(snap)
        self.eod = json.loads((snap / "eod_tracker.json").read_text())
        self.st = json.loads((snap / "sales_inputs.json").read_text())
        # Close calendar meetings (ramp.close_meetings), for the Intro-call rule. Without the file, no rule.
        self.meetings = None
        if (snap / "close_meetings.json").exists():
            self.meetings = {}
            for m in json.loads((snap / "close_meetings.json").read_text()):
                if m["status"] == "completed":
                    self.meetings.setdefault((m["user_id"], m["date"]), []).append(m)

    def _intro_cut(self, p, day, reported, lead_ids):
        """Live calls left after taking out Intro (setter) calls (Sheila, 11/16).

        Only days with an Intro call held in Close change. On those days:
        1. Shows the EOD lists by lead that match an Intro call held that day (or the day before or after) come off.
        2. What's left is capped at the non-Intro meetings (Discovery and follow-ups) held that day, which catches
           Intro shows the EOD didn't list by lead and Sales Tracker days.
        """
        if self.meetings is None or not p.get("close_user_id"):
            return reported
        uid = p["close_user_id"]
        held = self.meetings.get((uid, day.isoformat()), [])
        intro = [m for m in held if m["type"] == "intro"]
        if not intro:
            return reported
        near = held + self.meetings.get((uid, (day - timedelta(days=1)).isoformat()), []) \
            + self.meetings.get((uid, (day + timedelta(days=1)).isoformat()), [])
        left = reported
        for lid in lead_ids or []:
            types = {m["type"] for m in near if m["lead_id"] == lid}
            if "intro" in types and "discovery" not in types:
                left -= 1
        return max(0, min(left, len(held) - len(intro)))

    def _eod_days(self, p):
        names = set(p.get("eod_names", [])) | {p["name"]}
        days = {}
        for r in self.eod:
            if r["name"] in names:
                k = r["date"]
                # Reposts of the same day: keep the latest one.
                if k not in days or r["posted"] >= days[k]["posted"]:
                    days[k] = r
        return days

    def _st_days(self, p):
        days = {}
        for r in self.st:
            if r["name"] == p.get("sales_name", p["name"]):
                d = days.setdefault(r["date"], {"sched": 0, "live": 0, "offers": 0, "closes": 0, "cash": 0})
                for k in d:
                    d[k] += r[k] or 0
        return days

    def measure(self, p, s, e):
        """Totals over [s, e]: scheduled, live, offers from EOD (Sales Tracker on days with no EOD);
        closes and cash from the Sales Tracker."""
        eod, st = self._eod_days(p), self._st_days(p)
        out = {"sched": 0, "live": 0, "offers": 0, "closes": 0, "cash": 0,
               "eod_days": 0, "filled_days": [], "inferred_days": [], "resched_removed": 0,
               "intro_removed": 0, "intro_days": []}
        cur = s
        while cur <= e:
            k = cur.isoformat()
            r, f = eod.get(k), st.get(k)
            if r is not None:
                sched = r["scheduled"] or 0
                showed = r["showed"]
                if showed is None:  # blank "showed": scheduled minus no-shows, cancels and reschedules
                    showed = max(0, sched - (r["no_showed"] or 0) - (r["cancelled"] or 0) - (r["rescheduled"] or 0))
                    if sched:
                        out["inferred_days"].append(cur)
                # Sheila's rules (11/3): a call rescheduled before it happens (the day before, or the same day before
                # the call) is not a scheduled call; a same-day cancel and a no-show (even if rebooked afterwards)
                # are. EOD "scheduled" usually includes that day's reschedules (scheduled = showed + no-shows +
                # cancels + reschedules on most reports), so they come off, but never below the calls that
                # showed, no-showed or cancelled that day.
                floor = showed + (r["no_showed"] or 0) + (r["cancelled"] or 0)
                adj = max(floor, sched - (r["rescheduled"] or 0)) if r["rescheduled"] else sched
                if adj != sched:
                    out["resched_removed"] += sched - adj
                out["sched"] += adj
                kept = self._intro_cut(p, cur, showed, r.get("showed_lead_ids"))
                if kept != showed:
                    out["intro_removed"] += showed - kept
                    out["intro_days"].append(cur)
                out["live"] += kept
                out["offers"] += r["offers_made"] or 0
                out["eod_days"] += 1
            elif f is not None and (f["sched"] or f["live"]):
                out["sched"] += f["sched"]
                kept = self._intro_cut(p, cur, f["live"], None)
                if kept != f["live"]:
                    out["intro_removed"] += f["live"] - kept
                    out["intro_days"].append(cur)
                out["live"] += kept
                out["offers"] += f["offers"]
                out["filled_days"].append(cur)
            if f is not None:
                out["closes"] += f["closes"]
                out["cash"] += f["cash"]
            cur += timedelta(days=1)
        for k in ("sched", "live", "offers", "closes", "intro_removed"):
            out[k] = int(out[k])
        return out

    def ramp_month(self, p, k, upto):
        from ramp.build import d
        gl = d(p["go_live"])
        s, e = add_months(gl, k - 1), add_months(gl, k) - timedelta(days=1)
        return s, e, self.measure(p, s, min(e, upto))


if __name__ == "__main__":
    print(export(*sys.argv[1:4]))
