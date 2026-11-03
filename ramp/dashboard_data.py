"""Rows for the ramp months 1-3 dashboard: every 2026 new-hire closer, each ramp month they've started.

Usage: python3 -m ramp.dashboard_data <YYYY-MM-DD>
Reads the same inputs as ramp.weekly_table (data/snapshots/<date>/ plus the base snapshot) and writes
outputs/<date>/ramp_dashboard.json: {"through": ..., "rows": [...]}.

Same method as the weekly table (docs/decisions.md):
- Ramp month k = calendar month counted from go-live (first 45-min discovery call held).
- Scheduled = 45-min discovery bookings in Close (held or no-show, cancels left out).
- Live calls = EOD shows capped each day at those bookings; closes and offers from EOD; cash from the 2M tracker.
- Show rate = live ÷ scheduled. CTC = closes ÷ live.
- Months that ended before the base snapshot come from it; later ones from the fresh pulls.
"""
import json
import sys
from datetime import timedelta

from ramp.build import ROOT, Data, add_months, d
from ramp.compare import EXPECT, measure
from ramp.weekly_table import Current

SHOW_EXPECT = 0.75  # the New Hire Tracker's show rate expectation


def position(gl, through):
    """'Month 4, Day 28': where the rep is today, counted from go-live."""
    k = 1
    while add_months(gl, k) <= through:
        k += 1
    return k, (through - add_months(gl, k - 1)).days + 1


def build(run_date):
    roster = json.loads((ROOT / "config/roster.json").read_text())
    people = {p["name"]: p for p in roster["people"]}
    cfg = roster["dashboard"]
    base = ROOT / "data/snapshots" / roster["weekly_table"]["past_snapshot"]
    snap = ROOT / "data/snapshots" / run_date
    through = d(run_date) - timedelta(days=1)
    past = Data(base)
    cw = json.loads((base / "close_discovery_windows.json").read_text())
    cur = Current(snap, people, base)
    rows = []
    for n in cfg["reps"]:
        p = people[n]
        gl = d(p["go_live"])
        now_k, now_day = position(gl, through)
        for k in (1, 2, 3):
            s, e = add_months(gl, k - 1), add_months(gl, k) - timedelta(days=1)
            if s > through:
                continue
            complete = e <= through
            length = (e - s).days + 1
            if n in cfg["live_reps"] and e > past.through:
                m = cur.measure(p, k, through)
                sched, live, offers, closes, cash = m["sched"], m["live"], m["offers"], m["closes"], m["cash"]
                src = "fresh"
            else:
                m = measure(n, p, past, cw, k)
                if m is None or m["days"] < m["total_days"]:
                    continue  # finished after the base snapshot but not refreshed: leave it out rather than show a partial month
                sched, live, offers, closes, cash = m["sched"], int(m["live"]), int(m["offers"]), int(m["closes"]), m["cash"]
                src = "snapshot"
            if complete and not live and not cash:
                continue  # already gone that month (Chris, month 3): an all-zero row says nothing
            day_in = min((through - s).days + 1, length)
            ex = EXPECT[k]
            rows.append({
                "key": f"{n}|{k}", "kind": "closer", "name": n, "ramp_month": k,
                "period": f"End of ramp month {k}" if complete else f"Ramp month {k}, day {day_in} of {length}",
                "complete": "Y" if complete else "N",
                "day_in": day_in, "month_days": length,
                "status": p.get("sheet_status", ""),
                "went_live": p["go_live"],
                "now": f"Month {now_k}, day {now_day}",
                "sched": sched, "live": live,
                "show_rate": round(live / sched, 4) if sched else None,
                "offers": offers, "closes": closes, "cash": round(cash),
                "ctc": round(closes / live, 4) if live else None,
                # Only a finished month is judged against the month's expectation.
                "below_sched": complete and sched is not None and sched < ex["sched"],
                "below_live": complete and live < ex["live"],
                "below_closes": complete and closes < ex["closes"],
                "below_cash": complete and cash < ex["cash"],
                "below_ctc": complete and live and closes / live < ex["ctc"],
                "below_show": complete and bool(sched) and live / sched < SHOW_EXPECT,
                "source": src, "through": through.isoformat(),
            })
    for k in (1, 2, 3):
        ex = EXPECT[k]
        rows.append({"key": f"Expectation|{k}", "kind": "expectation", "name": f"Ramp month {k} expectation",
                     "ramp_month": k, "period": "", "complete": "", "day_in": None, "month_days": None, "status": "", "went_live": "", "now": "",
                     "sched": ex["sched"], "live": ex["live"], "show_rate": SHOW_EXPECT, "offers": None,
                     "closes": ex["closes"], "cash": ex["cash"], "ctc": ex["ctc"],
                     "below_sched": False, "below_live": False, "below_closes": False, "below_cash": False,
                     "below_ctc": False, "below_show": False, "source": "expectation",
                     "through": through.isoformat()})
    return {"through": through.isoformat(), "rows": rows}


def main():
    out = ROOT / "outputs" / sys.argv[1]
    out.mkdir(parents=True, exist_ok=True)
    res = build(sys.argv[1])
    (out / "ramp_dashboard.json").write_text(json.dumps(res, indent=1) + "\n")
    for r in res["rows"]:
        print(f"{r['name']:<26}{r['ramp_month']} {r['period']:<32}{str(r['sched']):>5}{r['live']:>5}{str(r['offers']):>5}{r['closes']:>4}{r['cash']:>9} {r['now']}")


if __name__ == "__main__":
    main()
