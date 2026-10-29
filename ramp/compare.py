"""Ramp-month comparison across everyone who has gone through each month, plus a check of Sheila's table.

Usage: python3 -m ramp.compare 2026-10-29
Writes outputs/<date>/ramp_comparison.md and data_check.md.
"""
import json
import sys
from datetime import timedelta
from pathlib import Path

from ramp.build import ROOT, RAMP, Data, add_months, d, money, pct

# Ramp-month expectations, as in the Weekly/Monthly Report tab.
EXPECT = {1: {"sched": 60, "live": 45, "closes": 7, "cash": 100_000, "ctc": 0.156},
          2: {"sched": 60, "live": 45, "closes": 9, "cash": 150_000, "ctc": 0.20},
          3: {"sched": 75, "live": 56, "closes": 14, "cash": 200_000, "ctc": 0.25}}

# Sheila's table (Weekly/Monthly Report, screenshot 2026-10-29): sched, live, offers, closes, cash.
SHEILA = {
    1: {"Harry": (69, 50, 34, 5, 93000), "Daniel": (52, 35, 33, 4, 70000), "Kevin": (66, 46, 29, 5, 88000),
        "Julien": (69, 48, 42, 4, 74500), "Chris": (58, 38, 31, 3, 50000), "Nick": (84, 64, 43, 11, 176500),
        "Solomon": (82, 57, 34, 6, 104500)},
    2: {"Harry": (67, 55, 38, 13, 241000), "Daniel": (53, 38, 33, 10, 157000), "Kevin": (71, 43, 36, 10, 170500),
        "Julien": (58, 44, 36, 11, 185000), "Chris": (37, 25, 22, 3, 51000), "Nick": (65, 46, 36, 10, 195500),
        "Solomon": (62, 38, 27, 9, 171000)},
    3: {"Julien": (65, 42, 35, 9, 160000), "Nick": (68, 48, 29, 15, 242500), "Harry": (72, 47, 35, 13, 230500)},
}
PAST = ["Owen", "Nick", "Julien", "Harry", "Chris", "Kevin", "Daniel", "Solomon"]
CURRENT = ["Oliver", "Tyler", "Kevin", "Daniel", "Solomon"]
CLOSE_USERS = {"Kevin": "Kevin Manuel", "Daniel": "Daniel Jenkins", "Solomon": "Solomon House",
               "Oliver": "Oliver English", "Tyler": "Tyler Johnson"}


def window(go_live, k, through, days=None):
    s = add_months(go_live, k - 1)
    e = add_months(go_live, k) - timedelta(days=1)
    if days is not None:
        e = min(e, s + timedelta(days=days - 1))
    return s, min(e, through), (add_months(go_live, k) - s).days


def measure(name, p, data, close_windows, k, days=None):
    s, e, total = window(d(p["go_live"]), k, data.through, days)
    if s > data.through:
        return None
    eod = data.eod_totals(p["eod_names"], s, e)
    offers = sum(data.num(r["offers_made"]) for r in data.eod_rows(p["eod_names"], s, e))
    if name in CLOSE_USERS:
        sched = data.scheduled(CLOSE_USERS[name], s, e)
        # EODs sometimes count setter calls as closer calls: cap each day's shows at Close's 45-min bookings.
        capped = 0
        for r in data.eod_rows(p["eod_names"], s, e):
            booked = data.scheduled_on(CLOSE_USERS[name], d(r["report_date"]))
            capped += min(data.num(r["showed"]), booked)
        eod["showed"] = capped
    else:
        sched = close_windows.get(name, {}).get(str(k) if days is None else f"{k}@{days}")
    tracked = s >= d("2026-05-01") and name != "Chris"
    cash = data.cash(p["tracker_id"], s, e) if tracked else eod["cash_collected"]
    weekdays = sum(1 for i in range((e - s).days + 1) if (s + timedelta(days=i)).weekday() < 5)
    return {
        "name": name, "start": s, "end": e, "days": (e - s).days + 1, "total_days": total,
        "sched": sched, "live": eod["showed"], "offers": offers, "closes": eod["closed"],
        "cash": cash, "cash_src": "tracker" if tracked else "EOD",
        "ctc": eod["closed"] / eod["showed"] if eod["showed"] else None,
        "coverage": eod["days_reported"] / weekdays if weekdays else None,
        "go_live_dom": d(p["go_live"]).day,
    }


def row(m, mark=""):
    sched = "–" if m["sched"] is None else str(m["sched"])
    cov = "" if m["coverage"] is None or m["coverage"] >= 0.8 else f" ⚠ EOD {m['coverage'] * 100:.0f}%"
    src = "" if m["cash_src"] == "tracker" else " (EOD)"
    return (f"| {m['name']}{mark} | {m['start']:%-m/%-d/%y} | {sched} | {int(m['live'])} | {int(m['offers'])} | "
            f"{int(m['closes'])} | {money(m['cash'])}{src} | {pct(m['ctc'])}{cov} |")


HEADER = "| Name | Went live | Booked 45-min (Close) | Showed (EOD) | Offers | Closes | Cash | CTC |\n|---|---|---|---|---|---|---|---|"


def render(snap):
    data = Data(snap)
    people = {p["name"]: p for p in json.loads((ROOT / "config/roster.json").read_text())["people"]}
    close_windows = json.loads((snap / "close_discovery_windows.json").read_text())
    out = [f"# Ramp-month comparison (data through {data.through:%-m/%-d/%Y})", "",
           "Each table compares everyone who has been through that ramp month. Ramp months count from the first 45-minute discovery call held.",
           "Cash comes from the 2M tracker (Sales Tracker) unless marked EOD. Showed, offers and closes come from EOD reports; ⚠ marks months where fewer than 80% of weekdays have an EOD, so those counts run low.",
           ""]
    for k in (1, 2, 3):
        cur = [n for n in CURRENT if (m := measure(n, people[n], data, close_windows, k)) and m["end"] < add_months(d(people[n]["go_live"]), k) - timedelta(days=1)]
        out += [f"## Ramp month {k}", ""]
        if cur:
            day = min(measure(n, people[n], data, close_windows, k)["days"] for n in cur)
            out += [f"### Same point in the month: first {day} days", "", HEADER]
            for n in cur:
                out.append(row(measure(n, people[n], data, close_windows, k, day), " (now)"))
            for n in PAST:
                m = measure(n, people[n], data, close_windows, k, day)
                if m and n not in cur and m["days"] == day:
                    out.append(row(m))
            out.append("")
        out += ["### Full month", "", HEADER]
        for n in PAST:
            if n in cur:
                continue
            m = measure(n, people[n], data, close_windows, k)
            if m and m["days"] == m["total_days"]:
                out.append(row(m))
        e = EXPECT[k]
        out += [f"| **Expectation** | | {e['sched']} | {e['live']} | | {e['closes']} | {money(e['cash'])} | {pct(e['ctc'])} |", ""]
        out.append("_Booked 45-min counts every discovery call on the calendar, including no-shows and some follow-ups, so it runs higher than the self-reported “scheduled”._")
        out.append("")

    # Data check of Sheila's table.
    chk = [f"# Check of the Weekly/Monthly Report table (data through {data.through:%-m/%-d/%Y})", "",
           "Your value → verified value. Verified cash is from the 2M tracker over the ramp month counted from the first held discovery call; calls are from EOD reports (and Close for booked calls). Cells within 5% match and are left out.", "",
           "| Person | Month | Field | Your table | Verified | Note |", "|---|---|---|---|---|---|"]
    names = ("Booked", "Live", "Offers", "Closes", "Cash")
    for k, rows in SHEILA.items():
        for n, vals in rows.items():
            m = measure(n, people[n], data, close_windows, k)
            mine = (m["sched"], m["live"], m["offers"], m["closes"], m["cash"])
            for f, a, b in zip(names, vals, mine):
                if b is None:
                    continue
                if abs(a - b) > max(0.05 * max(a, 1), 0.5):
                    note = []
                    if f != "Cash" and f != "Booked" and m["coverage"] is not None and m["coverage"] < 0.8:
                        note.append(f"EOD filed on {m['coverage'] * 100:.0f}% of weekdays")
                    if f == "Booked":
                        note.append("Close counts every 45-min discovery booking")
                    if f == "Cash" and m["cash_src"] == "EOD":
                        note.append("no daily tracker data; EOD cash")
                    fmt = money if f == "Cash" else (lambda x: str(int(x)))
                    chk.append(f"| {n} | M{k} | {f} | {fmt(a)} | {fmt(b)} | {'; '.join(note)} |")
    chk += ["", "Go-live dates used: " + ", ".join(f"{n} {d(people[n]['go_live']):%-m/%-d}" for n in PAST) + "."]
    return {"ramp_comparison.md": "\n".join(out), "data_check.md": "\n".join(chk)}


def main():
    snap = ROOT / "data/snapshots" / sys.argv[1]
    out = ROOT / "outputs" / sys.argv[1]
    out.mkdir(parents=True, exist_ok=True)
    for name, text in render(snap).items():
        (out / name).write_text(text + "\n")
        print(f"--- {name}\n{text}\n")


if __name__ == "__main__":
    main()


def slack_digest(snap):
    """Monday digest: each current ramp person against everyone else at the same point of the same ramp month."""
    data = Data(snap)
    people = {p["name"]: p for p in json.loads((ROOT / "config/roster.json").read_text())["people"]}
    close_windows = json.loads((snap / "close_discovery_windows.json").read_text())
    out = [f"*Ramp weekly digest · data through {data.through:%a %-m/%-d}*", ""]
    for k in (1, 2, 3):
        cur = []
        for n in CURRENT:
            m = measure(n, people[n], data, close_windows, k)
            if m and m["days"] < m["total_days"]:
                cur.append(m)
        if not cur:
            continue
        day = min(m["days"] for m in cur)
        out.append(f"*Ramp month {k}: everyone at day {day}*")
        out.append("```")
        out.append(f"{'':<9}{'Live':>8}{'Booked':>8}{'Showed':>8}{'Closes':>8}{'Cash':>10}{'CTC':>6}")
        rows = [measure(n, people[n], data, close_windows, k, day) for n in [m["name"] for m in cur]]
        rows += [m for n in PAST if n not in [c["name"] for c in cur]
                 and (m := measure(n, people[n], data, close_windows, k, day)) and m["days"] == day and m["cash_src"] == "tracker"]
        for m in rows:
            star = "*" if m["name"] in [c["name"] for c in cur] else " "
            booked = "–" if m["sched"] is None else m["sched"]
            out.append(f"{m['name'] + star:<9}{m['start'].strftime('%-m/%-d'):>8}{booked:>8}{int(m['live']):>8}{int(m['closes']):>8}{money(m['cash']):>10}{pct(m['ctc']):>6}")
        e = EXPECT[k]
        out.append(f"{'Pace':<9}{'':>8}{round(e['sched'] * day / 30):>8}{round(e['live'] * day / 30):>8}{round(e['closes'] * day / 30):>8}{money(e['cash'] * day / 30):>10}{pct(e['ctc']):>6}")
        out.append("```")
        out.append("_* = ramping now. Pace = month expectation × day ÷ 30. Showed/closes from EODs; cash from the 2M tracker._")
        out.append("")
    out.append("Full-month comparison for every ramp month, and the check of the Weekly/Monthly Report numbers: `outputs/<date>/ramp_comparison.md`, `data_check.md`.")
    return "\n".join(out)
