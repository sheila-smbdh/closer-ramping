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
    """Dates of the 5th and 10th showed closer calls.

    Per day: EOD 'showed', capped at the 45-min discovery calls Close has that day (EODs sometimes
    include setter calls). Days with no EOD use Close's count as an estimate.
    """
    eod = {}
    for r in data.eod_rows(p["eod_names"], since, data.through):
        eod[d(r["report_date"])] = eod.get(d(r["report_date"]), 0) + data.num(r["showed"])
    total, hits, estimated, cur = 0, {}, False, since
    while cur <= data.through:
        booked = data.scheduled_on(p["close_user"], cur)
        if cur in eod:
            total += min(eod[cur], booked) if booked else eod[cur]
        elif booked:
            estimated = True  # calls booked but no EOD: shows unknown, not counted (needs Zoom recording length)
        for n in (5, 10):
            if n not in hits and total >= n:
                hits[n] = (cur, estimated)
        cur += timedelta(days=1)
    return hits, total


def load_feedback():
    f = ROOT / "config/feedback.json"
    return json.loads(f.read_text()) if f.exists() else {}


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
    # One line per person, only when there's something for Sheila or Jay to do. Empty sections are left out.
    feedback = load_feedback()
    items = []
    for n in ("Oliver", "Tyler"):
        p = people[n]
        hits, _ = shown_call_dates(p, data, d(p["go_live"]))
        open_items = [f["issue"] for f in feedback.get(n, []) if f.get("status", "open") == "open"]
        fixes = f" ({'; '.join(open_items[:3])})" if open_items else ""
        for k, label, consequence in ((10, "10th", "If the issues aren't fixed, this is the cut checkpoint."),
                                      (5, "5th", "If the issues are still there, they go on watch.")):
            if k in hits:
                day, est = hits[k]
                when = f"~{fmt_day(day)}" if est else fmt_day(day)
                items.append(f"*{n}*: {label} showed call was {when}. A call review is due to check whether {('he' if n in ('Oliver', 'Tyler') else 'they')} improved on the mock-call feedback{fixes}. "
                             f"{consequence} <@U06Q63DTS13> can you review one of {n}'s recent calls and reply here?")
                break
    for n in ("Kevin", "Daniel", "Solomon"):
        r = closers[n][-1]
        left = (r["end"] - data.today).days
        if r["month"] == 3 and 0 <= left <= 7:
            items.append(f"*{n}*: ramp month 3 ends {fmt_day(r['end'])}. Keep-or-cut call due; see the month 3 comparison.")
    for n, (rows, last_call) in promotes.items():
        cu = people[n]["close_user"]
        cur = rows[-1]
        quiet_days = [t - timedelta(days=i) for i in range(5) if (t - timedelta(days=i)).weekday() < 5]
        nothing = all(data.scheduled_on(cu, x) == 0 for x in quiet_days)
        back = max((d(b) + timedelta(days=1) for a, b in people[n].get("time_off", []) if d(b) < t), default=None)
        since = max(x for x in (last_call, back) if x) if (last_call or back) else None
        idle_weekdays = sum(1 for i in range((t - since).days) if (since + timedelta(days=i + 1)).weekday() < 5) if since else 0
        if nothing and idle_weekdays >= 5 and last_call:
            nxt = min((d(f"2026-{k}") for k in data.close.get(cu, {}) if d(f"2026-{k}") > t), default=None)
            items.append(f"*{n}*: no self-set closer calls since {fmt_day(last_call)} (target 1–2 a day)."
                         + (f" Next one booked {fmt_day(nxt)}." if nxt else " Nothing booked."))

    lines = [f"*Ramp alert · {fmt_day(data.today)}*"] + [f"• {x}" for x in items]
    if any(" ~" in x for x in items):
        lines.append("_~ = counted from EOD reports only; some days with booked calls have no EOD, so the real date may be earlier._")
    if not items:
        lines = [f"✅ {fmt_day(data.today)}: nothing needs you today."]
    daily = "\n".join(lines)

    # ---------- Monday digest ----------
    from ramp.compare import slack_digest
    digest = slack_digest(snap)
    pl = ["", "*Setter promotes (self-set closer calls, Close)*", "```", f"{'':<8}{'Month':<7}{'Window':<14}{'Calls':>7}{'Target':>8}"]
    for n, (rows, _) in promotes.items():
        for r in rows:
            calls = f"{r['held_or_booked']}+{r['booked_ahead']}" if r["booked_ahead"] else str(r["held_or_booked"])
            pl.append(f"{n:<8}{'PR' + str(r['month']):<7}{r['start']:%-m/%-d}–{r['end']:%-m/%-d}".ljust(29) + f"{calls:>7}{r['target_calls']:>8}")
    pl += ["```", "_Calls = held (+ already booked). Windows skip time off._"]
    digest += "\n" + "\n".join(pl)

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
