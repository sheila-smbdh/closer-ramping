"""Weekly "everyone at the same point in the ramp month" table for the reps currently ramping.

Usage: python3 -m ramp.weekly_table <YYYY-MM-DD>
Reads:
  config/roster.json            go-live dates, eod_names, and "weekly_table" (which reps, which base snapshot)
  data/snapshots/<base>/        past closers (EOD snapshot, Close bookings, 2M tracker months)
  data/snapshots/<date>/        closers_YYYY-MM.json from the 2M tracker read API, eod_slack.json from ramp.slack_eod
Writes outputs/<date>/weekly_table.html, weekly_table.png and weekly_table.md.

Method (agreed with Sheila 11/2, see docs/decisions.md):
- Ramp month k = calendar month counted from go-live (first 45-min discovery call held).
- Everyone is compared at the same day of that month: the ramping reps' day as of yesterday.
- Cash: 2M tracker. Live calls and closes: EOD reports. Past closers use the EOD snapshot, capped each day at their
  45-min Close bookings. Ramping reps use #closer-eod, capped the same way with fresh Close meetings
  (data/snapshots/<date>/close_meetings_*.json). Only 45-minute discovery calls count; 15-minute intro (setter)
  calls don't, until Zoom length can show which intros ran long enough to count (Sheila, 11/2).
- A ramping rep's EOD close counts only if cash was collected that day (EOD or tracker); otherwise it's flagged.
- Rows are sorted by the day of the month each closer went live, so similar start days sit together.
"""
import csv
import json
import statistics
import subprocess
import sys
from datetime import date, datetime, timedelta
from html import escape

from ramp.build import ROOT, Data, add_months, d, money
from ramp.compare import PAST, measure



def _is_est(iso):
    """US Eastern is on standard time from the first Sunday of November to the second Sunday of March."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).replace(tzinfo=None)
    y = dt.year
    def nth_sunday(month, n):
        first = datetime(y, month, 1)
        return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))
    dst_start = nth_sunday(3, 2) + timedelta(hours=7)   # 2am EST = 07:00 UTC
    dst_end = nth_sunday(11, 1) + timedelta(hours=6)    # 2am EDT = 06:00 UTC
    return not (dst_start <= dt < dst_end)


def fmt_money(x):
    return "$0" if not x else money(x).replace(".0K", "K")


def ctc(cl, live):
    return f"{round(cl / live * 100)}%" if live else "–"


class Current:
    """A ramping rep's numbers from the fresh tracker pull and #closer-eod."""

    def __init__(self, snap, people, base=None):
        self.tracker = {}
        for f in snap.glob("closers_*.json"):
            for c in json.loads(f.read_text())["data"]["closers"]:
                self.tracker[(f.stem[-7:], c["rep_id"])] = c
        self.eod = json.loads((snap / "eod_slack.json").read_text())
        self.people = people
        # #closer-eod only goes back to 10/8. Older days fall back to the base EOD snapshot (same reports, same numbers).
        self.base_eod = []
        if base is not None and (base / "eod.csv").exists():
            def num(v):
                try:
                    return int(float(str(v).replace("$", "").replace(",", "")))
                except ValueError:
                    return None
            for r in csv.DictReader(open(base / "eod.csv")):
                self.base_eod.append({"name": r["closer_name"].strip(), "date": r["report_date"][:10],
                                      "showed": num(r["showed"]) or 0, "showed_estimated": False,
                                      "closed": num(r["closed"]), "cash": num(r["cash_collected"])})
        # 45-min discovery bookings per Close user per ET day (canceled / declined-by-org left out).
        self.booked = {}
        seen = set()
        for f in sorted(snap.glob("close_meetings_*.json")):
            for m in json.loads(f.read_text())["data"]:
                if m["id"] in seen or not m.get("starts_at") or m.get("duration") != 2700:
                    continue
                if m.get("status") in ("canceled", "declined-by-org"):
                    continue
                seen.add(m["id"])
                day = (datetime.fromisoformat(m["starts_at"].replace("Z", "+00:00")) - timedelta(hours=5 if _is_est(m["starts_at"]) else 4)).date()
                key = (m["user_id"], day)
                self.booked[key] = self.booked.get(key, 0) + 1

    def cum_cash(self, rep_id, day):
        c = self.tracker.get((day.strftime("%Y-%m"), rep_id)) or {}
        arr = c.get("daily_cumulative_cash") or []
        return arr[min(day.day, len(arr)) - 1] if arr else 0

    def cash_on(self, rep_id, day):
        before = self.cum_cash(rep_id, day - timedelta(days=1)) if day.day > 1 else 0
        return self.cum_cash(rep_id, day) - before

    def cash(self, rep_id, s, e):
        return sum(self.cash_on(rep_id, s + timedelta(days=i)) for i in range((e - s).days + 1))

    def eod_by_day(self, p):
        names = set(p["eod_names"]) | {p["name"]}
        full = {n for n in names if " " in n}
        days = {}
        for r in self.eod:
            if r["name"] not in names:
                continue
            day = d(r["date"])
            prefer = r["name"] in full  # Nero's checked report uses the full Slack name
            if day not in days or (prefer and not days[day][1]):
                days[day] = (r, prefer)
        for r in self.base_eod:
            if r["name"] in names and d(r["date"]) not in days:
                days[d(r["date"])] = (r, False)
        return {k: v[0] for k, v in days.items()}

    def measure(self, p, k, upto):
        gl = d(p["go_live"])
        s = add_months(gl, k - 1)
        e = min(add_months(gl, k) - timedelta(days=1), upto)
        eod = self.eod_by_day(p)
        live = closes = 0
        flagged, missing, estimated, cash_no_eod = [], [], [], []
        cur = s
        while cur <= e:
            r = eod.get(cur)
            if r is None:
                if cur.weekday() < 5:
                    missing.append(cur)
                if self.cash_on(p["tracker_id"], cur) > 0:
                    cash_no_eod.append((cur, self.cash_on(p["tracker_id"], cur)))
            else:
                booked = self.booked.get((p["close_user_id"], cur), 0)
                live += min(r["showed"] or 0, booked)
                if r["showed_estimated"]:
                    estimated.append(cur)
                cl = r["closed"] or 0
                if cl and not ((r["cash"] or 0) > 0 or self.cash_on(p["tracker_id"], cur) > 0):
                    flagged.append((cur, cl))
                else:
                    closes += cl
            cur += timedelta(days=1)
        return {"name": p["name"], "go_live": gl, "live": live, "closes": closes,
                "cash": self.cash(p["tracker_id"], s, e), "cur": True, "day": (e - s).days + 1,
                "flagged": flagged, "missing": missing, "estimated": estimated, "end": e,
                "cash_no_eod": cash_no_eod}


def build(run_date, reps=None):
    roster = json.loads((ROOT / "config/roster.json").read_text())
    people = {p["name"]: p for p in roster["people"]}
    cfg = roster["weekly_table"]
    snap = ROOT / "data/snapshots" / run_date
    base = ROOT / "data/snapshots" / cfg["past_snapshot"]
    through = d(run_date) - timedelta(days=1)
    cur = Current(snap, people, base)
    past_data = Data(base)
    cw = json.loads((base / "close_discovery_windows.json").read_text())

    # Which ramp month each rep is in as of yesterday.
    groups = {}
    for n in (reps or cfg["reps"]):
        gl = d(people[n]["go_live"])
        k = next((k for k in range(1, 4) if add_months(gl, k - 1) <= through < add_months(gl, k)), None)
        if k:
            groups.setdefault(k, []).append(n)

    tables = []
    for k, names in sorted(groups.items()):
        firsts = [cur.measure(people[n], k, through) for n in names]
        day = min(m["day"] for m in firsts)
        rows = [cur.measure(people[n], k, add_months(d(people[n]["go_live"]), k - 1) + timedelta(days=day - 1)) for n in names]
        for r, f in zip(rows, firsts):
            r["latest"] = f if f["day"] > day else None
        past, left = [], []
        for n in PAST:
            if n in names:
                continue
            at = measure(n, people[n], past_data, cw, k, day)
            full = measure(n, people[n], past_data, cw, k)
            if not at or full["days"] < full["total_days"]:
                continue  # hasn't finished this month in the base snapshot
            if not full["live"] and not full["cash"]:
                left.append(n)  # had already left; an all-zero month would drag the median down
                continue
            past.append({"name": n, "go_live": d(people[n]["go_live"]), "live": int(at["live"]), "closes": int(at["closes"]),
                         "cash": at["cash"], "cur": False, "f_live": int(full["live"]), "f_closes": int(full["closes"]),
                         "f_cash": full["cash"]})
        allrows = sorted(rows + past, key=lambda r: (r["go_live"].day, r["go_live"]))
        tables.append({"month": k, "day": day, "rows": allrows, "current": rows, "past": past, "through": through,
                       "people": people, "left": left})
    return tables


def month_end_day(go_live, k):
    """Which day of ramp month k the calendar month's last day falls on (cash tends to land at calendar month end)."""
    s = add_months(go_live, k - 1)
    nxt = date(s.year + (s.month == 12), s.month % 12 + 1, 1)
    return (nxt - s).days


def median_row(past):
    med = lambda xs: statistics.median(xs) if xs else 0
    m = {key: med([r[key] for r in past]) for key in ("live", "closes", "cash", "f_live", "f_closes", "f_cash")}
    m["ctc"] = med([r["closes"] / r["live"] if r["live"] else 0 for r in past])
    m["f_ctc"] = med([r["f_closes"] / r["f_live"] if r["f_live"] else 0 for r in past])
    return m


def num(x):
    return f"{x:g}"


def table_html(t):
    k, day = t["month"], t["day"]
    trs = []
    for r in t["rows"]:
        star = " *" if r["cur"] and (r["missing"] or r["estimated"] or r["flagged"] or r["cash_no_eod"]) else ""
        same = (f'<td class="gs">{r["live"]}{star}</td><td>{r["closes"]}</td><td>{ctc(r["closes"], r["live"])}</td>'
                f'<td>{fmt_money(r["cash"])}</td>')
        if r["cur"]:
            full = '<td class="gs ip" colspan="4">in progress</td>'
        else:
            full = (f'<td class="gs">{r["f_live"]}</td><td>{r["f_closes"]}</td><td>{ctc(r["f_closes"], r["f_live"])}</td>'
                    f'<td>{fmt_money(r["f_cash"])}</td>')
        cls = ' class="cur"' if r["cur"] else ""
        trs.append(f'<tr{cls}><td>{escape(r["name"])}</td><td>{r["go_live"]:%m/%d/%y}</td>'
                   f'<td>day {month_end_day(r["go_live"], k)}</td>{same}{full}</tr>')
    m = median_row(t["past"])
    trs.append(f'<tr class="med"><td>Past median</td><td></td><td></td><td class="gs">{num(m["live"])}</td><td>{num(m["closes"])}</td>'
               f'<td>{round(m["ctc"] * 100)}%</td><td>{fmt_money(m["cash"])}</td><td class="gs">{num(m["f_live"])}</td>'
               f'<td>{num(m["f_closes"])}</td><td>{round(m["f_ctc"] * 100)}%</td><td>{fmt_money(m["f_cash"])}</td></tr>')
    when = ", ".join(f'{r["end"]:%-m/%-d} for {r["name"]}' for r in t["current"])
    notes = [f"Month end = the day of ramp month {k} that the last day of the calendar month fell on. "
             f"Sorted by the day of the month each closer went live. Everyone is shown at day {day} of ramp month {k} ({when}). "
             "CTC = closes ÷ live calls; medians are taken column by column."]
    star = []
    for r0 in t["current"]:
        r = r0.get("latest") or r0  # footnote covers everything up to yesterday, not just the shared day
        bits = []
        if r["missing"]:
            bits.append("no EOD on " + ", ".join(f"{x:%-m/%-d}" for x in r["missing"]))
        if r["estimated"]:
            bits.append("shows estimated from the call list on " + ", ".join(f"{x:%-m/%-d}" for x in r["estimated"]))
        for day_, amt in r["cash_no_eod"]:
            bits.append(f"{fmt_money(amt)} collected on {day_:%-m/%-d} with no EOD, so any close that day isn't counted")
        for day_, cl in r["flagged"]:
            bits.append(f"EOD on {day_:%-m/%-d} says {cl} closed with no cash collected, not counted")
        if bits:
            star.append(f"{r['name']}: " + "; ".join(bits))
    if star:
        notes.append("* " + ". ".join(star) + ".")
    if len(t["past"]) < 5:
        notes.append(f"Only {len(t['past'])} past closers have finished ramp month {k} so far "
                     f"({', '.join(r['name'] for r in t['past'])}), so the median is thin.")
    if t.get("left"):
        notes.append(f"Left out: {', '.join(t['left'])} (no calls or cash in ramp month {k}; already gone).")
    subs = []
    for r in t["current"]:
        pp = t["people"][r["name"]]
        bits = []
        if pp.get("commission_pct") is not None:
            bits.append(f'{pp["commission_pct"] * 100:g}% commission')
        if pp.get("replacement_deadline"):
            bits.append(f'replacement expires {d(pp["replacement_deadline"]):%a %-m/%-d/%y}')
        if r.get("latest"):
            f = r["latest"]
            bits.append(f'today (day {f["day"]}, {f["end"]:%-m/%-d}): {f["live"]} live calls, {f["closes"]} closes, '
                        f'{ctc(f["closes"], f["live"])} CTC, {fmt_money(f["cash"])}')
        if bits:
            subs.append(f'<p class="sub"><b>{escape(r["name"])}</b>: {" · ".join(bits)}</p>')
    return (f'<section><h2>Ramp month {k} · day {day}</h2>' + "".join(subs) +
            f'<table><thead><tr><th></th><th></th><th></th><th colspan="4" class="grp">By day {day}</th><th colspan="4" class="grp">Full month {k}</th></tr>'
            '<tr><th>Closer</th><th>Went live</th><th>Month end</th><th class="gs">Live calls</th><th>Closes</th><th>CTC</th><th>Cash</th>'
            '<th class="gs">Live calls</th><th>Closes</th><th>CTC</th><th>Cash</th></tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table>' + "".join(f'<p class="foot">{escape(n)}</p>' for n in notes) + "</section>")


CSS = """
:root{--ink:#111412;--ink2:#4d5450;--muted:#848b86;--grid:#e3e6e2;--bg:#f7f8f6;--card:#fff;--hl:#eef3fb}
*{box-sizing:border-box}body{margin:0;background:var(--bg);font:14px/1.4 'IBM Plex Sans',-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;color:var(--ink)}
.wrap{width:980px;padding:22px}
h1{font:800 20px/1.2 Archivo,-apple-system,'Segoe UI',sans-serif;margin:0 0 2px}.lede{color:var(--ink2);margin:0 0 14px}
section{background:var(--card);border:1px solid var(--grid);border-radius:10px;padding:14px 16px;margin-bottom:14px}
h2{font:700 15px/1.2 Archivo,-apple-system,'Segoe UI',sans-serif;margin:0 0 8px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:6px 9px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
thead th{font:500 11px 'IBM Plex Mono',ui-monospace,monospace;letter-spacing:.04em;text-transform:uppercase;color:var(--muted)}
thead th.grp{text-align:center;color:var(--ink2)}
td.gs,th.gs{border-left:1px solid var(--grid)}
tr.cur td{font-weight:600;background:var(--hl)}
td.ip{text-align:center;color:var(--muted);font-weight:400}
tr.med td{color:var(--ink2);font-style:italic;border-bottom:none}
.foot{font-size:12px;color:var(--ink2);margin:8px 0 0}
.sub{margin:0 0 2px;color:var(--ink2)}.sub b{color:var(--ink)}
section h2+.sub{margin-top:-2px}section .sub:last-of-type{margin-bottom:10px}
"""


def render(run_date, reps=None, prefix="weekly_table"):
    tables = build(run_date, reps)
    out = ROOT / "outputs" / run_date
    out.mkdir(parents=True, exist_ok=True)
    through = d(run_date) - timedelta(days=1)
    sources = ("Live calls = 45-minute discovery calls: EOD shows capped each day at 45-min Close bookings "
               "(intro calls left out; Nick, Julien, Harry and Chris predate the Close data and are uncapped). Closes: EOD reports. Cash: 2M tracker.")
    html = ("<!doctype html><html><head><meta charset='utf-8'>"
            "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=Archivo:wght@700;800&family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Mono:wght@500&display=swap'>"
            f"<style>{CSS}</style></head><body><div class='wrap'>"
            f"<h1>New closer ramp: everyone at the same point</h1><p class='lede'>Data through {through:%a %-m/%-d/%Y}. {escape(sources)}</p>"
            + ("".join(table_html(t) for t in tables) or "<p>No one on the weekly list is in ramp months 1–3.</p>")
            + "</div></body></html>")
    (out / f"{prefix}.html").write_text(html)
    md = [f"Ramp table, data through {through:%-m/%-d}"]
    for t in tables:
        md.append(f"Month {t['month']}, day {t['day']}: " + "; ".join(
            f"{r['name']} {r['live']} live, {r['closes']} closes, {fmt_money(r['cash'])}" for r in t["current"])
            + f" (past median {num(median_row(t['past'])['live'])} live, {fmt_money(median_row(t['past'])['cash'])})")
    (out / f"{prefix}.md").write_text("\n".join(md) + "\n")
    js = ("const {chromium}=require('playwright');(async()=>{const b=await chromium.launch();"
          "const p=await b.newPage({viewport:{width:1024,height:400},deviceScaleFactor:2});"
          f"await p.goto('file://{out / f"{prefix}.html"}');await p.waitForTimeout(600);"
          f"await p.locator('.wrap').screenshot({{path:'{out / f"{prefix}.png"}'}});await b.close();}})();")
    subprocess.run(["node", "-e", js], check=True)
    print("\n".join(md))
    return out / f"{prefix}.png"


if __name__ == "__main__":
    # python3 -m ramp.weekly_table <date> [Rep1,Rep2 <output_prefix>]   (defaults: weekly_table.reps, weekly_table)
    reps = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    print(render(sys.argv[1], reps, sys.argv[3] if len(sys.argv) > 3 else "weekly_table"))
