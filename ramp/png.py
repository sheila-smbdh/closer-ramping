"""Render the Monday comparison tables as a PNG for Slack.

Usage: python3 -m ramp.png 2026-10-29  -> outputs/<date>/ramp_comparison.png
Needs node + the preinstalled Playwright/Chromium.
"""
import json
import statistics
import subprocess
import sys
from html import escape

from ramp.build import ROOT, money, pct
from ramp.compare import digest_tables

# Status palette (dataviz reference): colour always paired with an icon, never alone.
GOOD, WARN, BAD = ("#0ca30c", "▲"), ("#fab219", "●"), ("#d03b3b", "▼")
FIELDS = [("sched", "Booked", str), ("live", "Showed", lambda v: str(int(v))), ("closes", "Closes", lambda v: str(int(v))),
          ("cash", "Cash", money), ("ctc", "CTC", pct)]


def status(value, pace):
    if value is None or not pace:
        return None
    r = value / pace
    return GOOD if r >= 1 else WARN if r >= 0.7 else BAD


def cell(m, key, fmt, pace, mark):
    v = m.get(key)
    text = "–" if v is None else fmt(v)
    st = status(v, pace) if mark else None
    if not st:
        return f"<td>{escape(text)}</td>"
    color, icon = st
    return f'<td class="st" style="--c:{color}"><span class="ic">{icon}</span>{escape(text)}</td>'


def table_html(t):
    who = " & ".join(c["name"] for c in t["current"])
    rows = []
    # Colour against the past reps' median at the same day (cash is back-loaded, so straight-line pace is too harsh mid-month).
    ref = {k: statistics.median([m[k] for m in t["past"] if m.get(k) is not None] or [0]) for k, _, _ in FIELDS} if t["past"] else t["pace"]
    for m in t["current"]:
        tds = "".join(cell(m, k, f, ref[k], True) for k, _, f in FIELDS)
        rows.append(f'<tr class="cur"><th>{m["name"]}<small>live {m["start"]:%-m/%-d}</small></th>{tds}</tr>')
    for m in t["past"]:
        tds = "".join(cell(m, k, f, None, False) for k, _, f in FIELDS)
        rows.append(f'<tr><th>{m["name"]}<small>live {m["start"]:%-m/%-d/%y}</small></th>{tds}</tr>')
    if t["past"]:
        med = {k: statistics.median([m[k] for m in t["past"] if m.get(k) is not None] or [0]) for k, _, _ in FIELDS}
        med["sched"] = round(med["sched"]) if med["sched"] else None
        tds = "".join(cell(med, k, f, None, False) for k, _, f in FIELDS)
        rows.append(f'<tr class="med"><th>Past median</th>{tds}</tr>')
    p = t["pace"]
    pace_cells = "".join(f"<td>{escape(f(round(p[k]) if k != 'cash' and k != 'ctc' else p[k]))}</td>" for k, _, f in FIELDS)
    rows.append(f'<tr class="pace"><th>Pace to target</th>{pace_cells}</tr>')
    head = "".join(f"<th>{label}</th>" for _, label, _ in FIELDS)
    return (f'<section><h2>Ramp month {t["month"]} · day {t["day"]}</h2>'
            f'<p class="sub">{escape(who)} against everyone else at day {t["day"]} of their month {t["month"]}</p>'
            f'<table><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></section>')


CSS = """
:root{--ink:#0b0b0b;--ink2:#52514e;--muted:#8a8984;--grid:#e1e0d9;--bg:#fbfaf7;--card:#ffffff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);font:14px/1.35 -apple-system,'Segoe UI',Inter,Helvetica,Arial,sans-serif;color:var(--ink)}
.wrap{width:760px;padding:24px}
h1{font-size:20px;margin:0 0 2px}.lede{color:var(--ink2);margin:0 0 18px}
section{background:var(--card);border:1px solid var(--grid);border-radius:12px;padding:16px 18px;margin-bottom:16px}
h2{font-size:16px;margin:0}.sub{color:var(--ink2);margin:2px 0 10px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
thead th{font-size:12px;color:var(--muted);font-weight:600;text-align:right;padding:4px 8px;border-bottom:1px solid var(--grid)}
thead th:first-child{text-align:left}
tbody th{text-align:left;font-weight:600;padding:7px 8px;white-space:nowrap}
tbody th small{display:block;font-weight:400;color:var(--muted);font-size:11px}
td{text-align:right;padding:7px 8px;border-bottom:1px solid var(--grid);color:var(--ink2)}
tr.cur th,tr.cur td{color:var(--ink)}
tr.cur{background:#f3f2ec}
td.st{position:relative}
td.st .ic{color:var(--c);font-size:10px;margin-right:5px;vertical-align:1px}
tr.med th,tr.med td{color:var(--ink2);font-style:italic}
tr.pace th,tr.pace td{color:var(--ink2);border-bottom:none;font-weight:600}
.legend{color:var(--ink2);font-size:12px;margin-top:4px}
.legend b{font-weight:600}
"""


def render(date):
    snap = ROOT / "data/snapshots" / date
    tables = digest_tables(snap)
    through = tables[0]["through"] if tables else None
    legend = (f'<p class="legend"><b style="color:{GOOD[0]}">▲</b> at or above the past median at the same day &nbsp; '
              f'<b style="color:{WARN[0]}">●</b> 70–99% of it &nbsp; <b style="color:{BAD[0]}">▼</b> under 70% &nbsp;·&nbsp; '
              "Pace to target = month target × day ÷ 30 (cash lands late in a month, so mid-month cash usually trails it). "
              "Booked = 45-min discovery calls in Close · Showed/Closes = EOD reports · Cash = 2M tracker.</p>")
    html = (f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body><div class='wrap'>"
            f"<h1>New hire ramp: same-day comparison</h1><p class='lede'>Data through {through:%a %-m/%-d/%Y}</p>"
            + "".join(table_html(t) for t in tables) + legend + "</div></body></html>")
    out = ROOT / "outputs" / date
    out.mkdir(parents=True, exist_ok=True)
    (out / "ramp_comparison.html").write_text(html)
    js = (
        "const {chromium}=require('playwright');(async()=>{const b=await chromium.launch();"
        "const p=await b.newPage({viewport:{width:808,height:400},deviceScaleFactor:2});"
        f"await p.goto('file://{out / 'ramp_comparison.html'}');"
        f"await p.locator('.wrap').screenshot({{path:'{out / 'ramp_comparison.png'}'}});await b.close();}})();"
    )
    subprocess.run(["node", "-e", js], check=True)
    return out / "ramp_comparison.png"


if __name__ == "__main__":
    print(render(sys.argv[1]))
