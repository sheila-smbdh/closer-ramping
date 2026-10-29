"""Build the daily alert, Monday digest and office hour prep from one data snapshot.

Usage: python3 -m ramp.build 2026-10-29
Reads config/roster.json and data/snapshots/<date>/, writes outputs/<date>/.
"""
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Targets agreed with Sheila (docs/decisions.md).
RAMP = {
    1: {"cash": 100_000, "floor": 75_000, "ctc": 0.12, "sched": 60},
    2: {"cash": 200_000, "floor": 150_000, "ctc": 0.20, "sched": 75},  # open path; throttled below
    3: {"cash": 200_000, "floor": 200_000, "ctc": 0.25, "sched": 75},
}
THROTTLED_M2 = {"cash": 150_000, "floor": 150_000, "ctc": 0.20, "sched": 60}
OPEN_PATH_LINE, NEAR_MISS = 100_000, 95_000
PRE_RAMP_CALLS = {1: 20, 2: 40}
PRE_RAMP_CTC = 0.15


def d(s):
    return date.fromisoformat(s[:10])


def add_months(day, n):
    y, m = divmod(day.month - 1 + n, 12)
    y += day.year
    m += 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(day.day, last))


def money(x):
    return f"${x / 1000:,.1f}K" if x < 1_000_000 else f"${x / 1e6:,.2f}M"


def pct(x):
    return "n/a" if x is None else f"{x * 100:.0f}%"


class Data:
    def __init__(self, snap):
        self.snap = snap
        self.today = d(snap.name)
        self.through = self.today - timedelta(days=1)  # last complete day
        self.close = json.loads((snap / "close_discovery_daily.json").read_text())
        self.tracker = {}
        for f in snap.glob("closers_*.json"):
            for c in json.loads(f.read_text())["data"]["closers"]:
                self.tracker[(f.stem[-7:], c["rep_id"])] = c
        self.eod = list(csv.DictReader(open(snap / "eod.csv")))

    def scheduled(self, close_user, start, end):
        """Discovery calls booked in [start, end] per Close (held or upcoming)."""
        days = self.close.get(close_user, {})
        return sum(n for k, n in days.items() if start <= d(f"{start.year}-{k}") <= end)

    def scheduled_on(self, close_user, day):
        return self.close.get(close_user, {}).get(day.strftime("%m-%d"), 0)

    def cash(self, rep_id, start, end):
        """Cash collected in [start, end] from the 2M tracker's daily cumulative cash."""
        def cum(day):
            c = self.tracker.get((day.strftime("%Y-%m"), rep_id))
            arr = (c or {}).get("daily_cumulative_cash") or []
            return arr[min(day.day, len(arr)) - 1] if arr else 0

        total, cur = 0, start
        while cur <= end:
            month_end = add_months(cur.replace(day=1), 1) - timedelta(days=1)
            seg_end = min(end, month_end)
            before = cum(cur - timedelta(days=1)) if cur.day > 1 else 0
            total += cum(seg_end) - before
            cur = seg_end + timedelta(days=1)
        return total

    def eod_rows(self, names, start, end):
        return [r for r in self.eod if r["closer_name"].strip() in names and start <= d(r["report_date"]) <= end]

    @staticmethod
    def num(v):
        try:
            return float(str(v).replace("$", "").replace(",", "") or 0)
        except ValueError:
            return 0.0

    def eod_totals(self, names, start, end):
        rows = self.eod_rows(names, start, end)
        t = {k: sum(self.num(r[k]) for r in rows) for k in ("scheduled", "showed", "closed", "cash_collected")}
        t["days_reported"] = len({r["report_date"][:10] for r in rows})
        return t


def ramp_windows(go_live, through):
    out, k = [], 1
    while k <= 3:
        s, e = add_months(go_live, k - 1), add_months(go_live, k) - timedelta(days=1)
        if s > through:
            break
        out.append((k, s, e))
        k += 1
    return out


def pre_ramp_windows(first_self_set, time_off, through):
    """30-day windows that skip time-off days (Quinn's clock pauses while he's away)."""
    off = set()
    for a, b in time_off:
        cur = d(a)
        while cur <= d(b):
            off.add(cur)
            cur += timedelta(days=1)
    out, start, k = [], d(first_self_set), 1
    while start <= through and k <= 2:
        cur, worked = start, 0
        target = (add_months(start, 1) - start).days
        while worked < target:
            if cur not in off:
                worked += 1
            cur += timedelta(days=1)
        out.append((k, start, cur - timedelta(days=1), len([x for x in off if start <= x < cur])))
        start, k = cur, k + 1
    return out


def closer_status(p, data):
    t = data.through
    rows = []
    for k, s, e in ramp_windows(d(p["go_live"]), t):
        upto = min(e, t)
        cash = data.cash(p["tracker_id"], s, upto)
        eod = data.eod_totals(p["eod_names"], s, upto)
        rows.append({
            "month": k, "start": s, "end": e, "through": upto,
            "days_in": (upto - s).days + 1, "days_total": (e - s).days + 1,
            "cash": cash, "sched_close": data.scheduled(p["close_user"], s, upto),
            "booked_ahead": data.scheduled(p["close_user"], t + timedelta(days=1), e) if e > t else 0,
            "showed": eod["showed"], "closed": eod["closed"], "eod_days": eod["days_reported"],
            "ctc": eod["closed"] / eod["showed"] if eod["showed"] else None,
        })
    # Month 2 target depends on month 1 cash.
    if len(rows) >= 2:
        m1 = rows[0]["cash"]
        rows[1]["path"] = "open" if m1 >= OPEN_PATH_LINE else "throttled"
        rows[1]["near_miss"] = NEAR_MISS <= m1 < OPEN_PATH_LINE
    for r in rows:
        tgt = THROTTLED_M2 if r.get("path") == "throttled" else RAMP[r["month"]]
        r["target"] = tgt
        r["straight_line"] = tgt["cash"] * r["days_in"] / r["days_total"]
    return rows


def promote_status(p, data):
    t = data.through
    rows = []
    for k, s, e, paused in pre_ramp_windows(p["first_self_set"], p.get("time_off", []), t):
        upto = min(e, t)
        eod = data.eod_totals(p["eod_names"], s, upto)
        rows.append({
            "month": k, "start": s, "end": e, "paused_days": paused,
            "held_or_booked": data.scheduled(p["close_user"], s, upto),
            "booked_ahead": data.scheduled(p["close_user"], t + timedelta(days=1), e) if e > t else 0,
            "showed": eod["showed"], "closed": eod["closed"], "cash": eod["cash_collected"], "eod_days": eod["days_reported"],
            "target_calls": PRE_RAMP_CALLS[k],
        })
    last_call = max((d(f"2026-{k}") for k in data.close.get(p["close_user"], {}) if d(f"2026-{k}") <= t), default=None)
    return rows, last_call


def shown_call_dates(p, data, since):
    """Dates of the 5th and 10th shown closer calls by EOD 'showed' (cumulative)."""
    rows = sorted(data.eod_rows(p["eod_names"], since, data.through), key=lambda r: r["report_date"])
    total, hits = 0, {}
    for r in rows:
        total += data.num(r["showed"])
        for n in (5, 10):
            if n not in hits and total >= n:
                hits[n] = d(r["report_date"])
    first_eod = d(rows[0]["report_date"]) if rows else None
    return hits, total, first_eod


def fmt_day(x):
    return x.strftime("%a %-m/%-d")


def render(snap):
    data = Data(snap)
    roster = json.loads((ROOT / "config/roster.json").read_text())
    people = {p["name"]: p for p in roster["people"]}
    t = data.through
    closers = {n: closer_status(people[n], data) for n in ("Kevin", "Daniel", "Solomon", "Oliver", "Tyler")}
    promotes = {n: promote_status(people[n], data) for n in ("Quinn", "Yobani", "Dave")}

    # ---------- daily alert ----------
    action, watch, data_issues = [], [], []
    for n in ("Oliver", "Tyler"):
        p = people[n]
        hits, total, first_eod = shown_call_dates(p, data, d(p["go_live"]))
        gap = (first_eod - d(p["go_live"])).days if first_eod else None
        line = f"*{n}* (ramp month 1, day {closers[n][0]['days_in']}): {int(total)} shown closer calls by EOD reports"
        if 10 in hits:
            line += f"; passed the *10th* on {fmt_day(hits[10])}. Jay: are the mock-call issues fixed? If not, this is the cut checkpoint."
        elif 5 in hits:
            line += f"; passed the *5th* on {fmt_day(hits[5])}. Jay: still seeing the mock-call issues? If yes, they go on watch."
        action.append(line + " _No Jay feedback is logged yet, so I can't check this automatically._")
        if gap and gap > 1:
            data_issues.append(f"{n}: no EOD reports from go-live {fmt_day(d(p['go_live']))} until {fmt_day(first_eod)}, so the shown-call count above starts late.")
    for n, (rows, last_call) in promotes.items():
        y = data.scheduled_on(people[n]["close_user"], t)
        cur = rows[-1]
        msg = f"*{n}* (pre-ramp month {cur['month']}): {y} self-set closer calls yesterday (floor 1–2)."
        if cur["end"] < t:
            calls = sum(r["held_or_booked"] for r in rows)
            msg = (f"*{n}*: pre-ramp month 2 ended {fmt_day(cur['end'])} (clock paused {sum(r['paused_days'] for r in rows)} days for time off) "
                   f"with {cur['held_or_booked']} closer calls against 40 ({calls} across both months). Ascension decision is due; he isn't on the closer calendar.")
            action.append(msg)
            continue
        need = cur["target_calls"] - cur["held_or_booked"] - cur["booked_ahead"]
        days_left = (cur["end"] - t).days
        msg += f" Month so far {cur['held_or_booked']} held + {cur['booked_ahead']} booked of {cur['target_calls']}"
        if need > 0:
            msg += f"; needs {need} more in {days_left} days (~{need / max(days_left, 1):.1f}/day)."
        if last_call and (t - last_call).days >= 5:
            msg += f" Last closer call {fmt_day(last_call)}."
        watch.append(msg)
    for n in ("Kevin", "Daniel", "Solomon"):
        r = closers[n][-1]
        if r["month"] == 3 and r["end"] - t <= timedelta(days=10):
            gap_cash = r["target"]["cash"] - r["cash"]
            action.append(
                f"*{n}* ramp month 3 ends {fmt_day(r['end'])} ({(r['end'] - t).days} days): {money(r['cash'])} of $200K and {pct(r['ctc'])} CTC (needs both $200K and 25%). "
                f"Short {money(max(gap_cash, 0))}; cut decision due at month end."
            )

    lines = [f"*New hire ramp: daily alert, {fmt_day(data.today)}* _(data through {fmt_day(t)})_", ""]
    lines += ["*Needs a decision*"] + [f"• {x}" for x in action] + [""]
    lines += ["*Self-set volume*"] + [f"• {x}" for x in watch] + [""]
    lines += ["*Setter week*", "• Nobody is in setter week right now.", ""]
    if data_issues:
        lines += ["*Data gaps*"] + [f"• {x}" for x in data_issues] + [""]
    lines += ["_Reply in thread with feedback (e.g. “Oliver, call w/ X 10/28. Fix: … Better: …”) and I’ll log it._"]
    daily = "\n".join(lines)

    # ---------- Monday digest ----------
    dl = [f"*New hire ramp: weekly digest* _(data through {fmt_day(t)})_", ""]
    dl += ["*New hire closers*", "```", f"{'Name':<8}{'Stage':<10}{'Day':>8}{'Cash':>10}{'Pace*':>10}{'Showed':>8}{'Closes':>7}{'CTC':>6}{'Booked':>8}"]
    for n, rows in closers.items():
        r = rows[-1]
        dl.append(
            f"{n:<8}{'Ramp M' + str(r['month']):<10}{str(r['days_in']) + '/' + str(r['days_total']):>8}{money(r['cash']):>10}"
            f"{money(r['straight_line']):>10}{int(r['showed']):>8}{int(r['closed']):>7}{pct(r['ctc']):>6}{r['sched_close'] + r['booked_ahead']:>8}"
        )
    dl += ["```", "_*Pace = target × days elapsed ÷ days in ramp month (straight line; cash actually lands late in a month). Booked = Close discovery calls held + already on the calendar this ramp month._", ""]
    dl.append("*Ramp history and calls coming up*")
    for n in ("Kevin", "Daniel", "Solomon"):
        rows = closers[n]
        hist = "; ".join(
            f"M{r['month']} {money(r['cash'])}" + (f" ({r['path']})" if r.get("path") else "") for r in rows
        )
        dl.append(f"• *{n}*: {hist}. M3 needs $200K and 25% CTC by {fmt_day(rows[-1]['end'])}.")
    for n in ("Oliver", "Tyler"):
        r = closers[n][0]
        dl.append(f"• *{n}*: M1 ends {fmt_day(r['end'])}. {money(r['cash'])} so far; floor $75K, open path at $100K (flag $95K–$99.9K to Sheila).")
    dl += ["", "*Setter promotes (pre-ramp, self-set)*", "```", f"{'Name':<8}{'Month':<7}{'Window':<16}{'Calls':>7}{'Target':>8}{'Showed':>8}{'Closes':>7}"]
    for n, (rows, _) in promotes.items():
        for r in rows:
            w = f"{r['start']:%-m/%-d}–{r['end']:%-m/%-d}"
            calls = f"{r['held_or_booked']}+{r['booked_ahead']}" if r["booked_ahead"] else str(r["held_or_booked"])
            dl.append(f"{n:<8}{'PR' + str(r['month']):<7}{w:<16}{calls:>7}{r['target_calls']:>8}{int(r['showed']):>8}{int(r['closed']):>7}")
    dl += ["```", "_Calls = Close discovery calls held (+ booked ahead). Quinn’s windows skip his time off. Showed/closes come from EOD reports, which these three rarely file._", ""]
    checks = []
    for n in ("Oliver", "Tyler"):
        p = people[n]
        s0 = data.through - timedelta(days=6)
        eod = data.eod_totals(p["eod_names"], s0, t)
        close_n = data.scheduled(p["close_user"], s0, t)
        if eod["scheduled"] > close_n * 1.2:
            checks.append(f"• {n}: EOD says {int(eod['scheduled'])} calls scheduled in the last 7 days, Close shows {close_n} discovery calls. Setter calls are likely being logged as closer calls (Jay flagged this to Tyler 10/23), which inflates shows and the 5th/10th call count.")
    for n in ("Kevin", "Daniel", "Solomon"):
        p = people[n]
        c = data.tracker.get((t.strftime("%Y-%m"), p["tracker_id"]))
        eod = data.eod_totals(p["eod_names"], t.replace(day=1), t)
        if c and c["values"].get("live") is not None and abs(c["values"]["live"] - eod["showed"]) >= 3:
            checks.append(f"• {n}, {t:%B} to date: Sales Tracker says {c['values']['live']} live calls, EOD says {int(eod['showed'])}. Using EOD.")
    checks.append("• Quinn, Yobani and Dave rarely file closer EODs, so their show rate and CTC aren’t reliable yet.")
    for n in ("Kevin", "Daniel", "Solomon"):
        m1 = closers[n][0]
        if m1["cash"] < RAMP[1]["floor"]:
            checks.append(f"• {n}’s ramp month 1 ({m1['start']:%-m/%-d}–{m1['end']:%-m/%-d}) comes out at {money(m1['cash'])}, under the $75K floor, when counted from his first held call. Worth a look at whether the floor was applied.")
    dl += ["*Data checks*"] + checks
    digest = "\n".join(dl)

    # ---------- office hour prep ----------
    cohort = roster["cohorts"]["kds"]
    session = date(2026, 11, 5)
    ol = [f"*Office hour prep: {cohort['label']}, Thu {session:%-m/%-d} 12–1pm ET* _(posts 24h before; data through {fmt_day(t)})_", "",
          "*Where they are*: all three are in ramp month 3. Kevin and Daniel’s month ends the day of this session (11/5); Solomon’s ends 11/12.", ""]
    for n in cohort["members"]:
        r = closers[n][-1]
        m1, m2 = closers[n][0], closers[n][1]
        ol.append(
            f"• *{n}*: M3 {money(r['cash'])} of $200K, {int(r['closed'])}/{int(r['showed'])} closes ({pct(r['ctc'])} CTC, needs 25%). "
            f"{r['booked_ahead']} discovery calls booked before month end. M1 {money(m1['cash'])}, M2 {money(m2['cash'])} ({m2.get('path')} path)."
        )
    ol += ["", "*Suggested agenda*",
           "1. Month-end close plan: who’s in Contract Out / verbals, and what has to land by 11/5 (Kevin, Daniel) and 11/12 (Solomon).",
           "2. CTC is the gap for all three, not just cash. Pick one recent no-close call per person and review the pitch-to-close section against the rubric.",
           "3. Daniel is on watch: agree what “cleared” looks like and say it out loud.",
           "4. Ask each: what would you do differently on calls in the next 7 days?",
           "",
           "*Open feedback items from Jay*: none logged yet. Once Jay posts feedback in this channel, the open items for each person will show here.",
           "*Calls submitted for review*: none submitted this week."]
    prep = "\n".join(ol)
    return {"daily_alert.md": daily, "monday_digest.md": digest, "office_hours_prep_kds.md": prep}


def main():
    snap = ROOT / "data/snapshots" / sys.argv[1]
    out = ROOT / "outputs" / sys.argv[1]
    out.mkdir(parents=True, exist_ok=True)
    for name, text in render(snap).items():
        (out / name).write_text(text + "\n")
        print(f"--- {name}\n{text}\n")


if __name__ == "__main__":
    main()
