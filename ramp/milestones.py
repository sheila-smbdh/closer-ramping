"""Daily milestone sweep: what Jay and Sheila need to act on for each ramping closer.

Usage:
  python3 -m ramp.milestones <YYYY-MM-DD>          sweep; writes outputs/<date>/milestones.md and milestones.json
  python3 -m ramp.milestones <YYYY-MM-DD> --mark   after the Slack post went out: log what was sent

Reads data/snapshots/<date>/roster_sheet.json (the New Hire Tracker's Roster tab, from ramp.roster_sheet: who is
ramping, go-live dates, status, replacement deadlines; required, the sweep stops without it), config/roster.json
(lookup keys only: EOD and Sales Tracker names), config/feedback.json (Jay's open items),
data/milestones_sent.json (what was already posted) and, if present, data/snapshots/<date>/eod_tracker.json and
sales_inputs.json (numbers through yesterday, from ramp.sheets). Without the snapshot the reminders still go out,
just without numbers.

Milestones (Sheila, 11/5; targets from docs/decisions.md and docs/SOP.md):
- Free replacement period ending (roster `replacement_deadline`): 14, 7 and 3 days before, and on the last
  weekday before it. Jay should watch the closer's calls closely and give a keep-or-replace call before it ends.
- Ramp month 1-3 ending: 7 days before the month's last day, with pace against target and floor.
- Ramp month 1-3 ended: the first weekday after, with final numbers and the keep / path / cut decision it triggers.
- 5th and 10th showed call (from go-live) while Jay has open feedback items: ask Jay to review a recent call.
Advance reminders that land on a weekend move to the Friday before; results move to the Monday after, so nothing
posts on a weekend unless a run was missed. Each reminder posts once. If a run is missed, the next run sends only
the latest stage that is due (e.g. "3 days left", not also "7 days left").
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from ramp.build import RAMP, THROTTLED_M2, OPEN_PATH_LINE, NEAR_MISS, ROOT, add_months, d

JAY, SHEILA = "U06Q63DTS13", "U0BM6J8KM32"
SENT = ROOT / "data/milestones_sent.json"


def fmt_money(x):
    x = round(x or 0)
    if not x:
        return "$0"
    return f"${x / 1000:,.1f}K".replace(".0K", "K")


def fmt_day(day):
    return f"{day:%a %-m/%-d}"


def ctc(closes, live):
    return f"{round(closes / live * 100)}%" if live else "n/a"


def back_to_weekday(day):
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def fwd_to_weekday(day):
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def ramping(people):
    """Closers on the calendar who aren't out: they have a go-live date and the Roster tab doesn't say Out."""
    for p in people:
        if not p.get("go_live"):
            continue
        if p.get("status") not in ("active", "watch") or p.get("sheet_status") == "Out":
            continue
        yield p


class Numbers:
    def __init__(self, snap):
        self.sh = None
        if (snap / "eod_tracker.json").exists() and (snap / "sales_inputs.json").exists():
            from ramp.sheets import Sheets
            self.sh = Sheets(snap)

    def month(self, p, k, through):
        if not self.sh:
            return None
        gl = d(p["go_live"])
        s, e = add_months(gl, k - 1), add_months(gl, k) - timedelta(days=1)
        if through < s:
            return None
        m = self.sh.measure(p, s, min(e, through))
        m.update(start=s, end=e, through=min(e, through), day=(min(e, through) - s).days + 1, days=(e - s).days + 1)
        return m

    def live_crossings(self, p, through):
        """Day the rep's showed calls since go-live first reached 5 and 10."""
        if not self.sh:
            return {}
        gl, out, total = d(p["go_live"]), {}, 0
        cur = gl
        while cur <= through:
            total += self.sh.measure(p, cur, cur)["live"]
            for n in (5, 10):
                if n not in out and total >= n:
                    out[n] = cur
            cur += timedelta(days=1)
        return out


def month_targets(k, m1_cash=None):
    if k == 2 and m1_cash is not None and m1_cash < OPEN_PATH_LINE:
        return THROTTLED_M2
    return RAMP[k]


def stats_line(m):
    return (f"{m['sched']} scheduled, {m['live']} live, {m['closes']} close{'' if m['closes'] == 1 else 's'}, "
            f"{ctc(m['closes'], m['live'])} CTC, {fmt_money(m['cash'])} cash")


def open_items(feedback, name):
    return [f for f in feedback.get(name, []) if f.get("status") != "fixed"]


def items_text(items):
    return "; ".join(f"{f['issue']} ({f['category'].lower()}, {d(f['date']):%-m/%-d})" for f in items)


def events(today, people, feedback, nums):
    """Every reminder group for every ramping closer: (group, [(stage, due, valid_until)], render(stage))."""
    through = today - timedelta(days=1)
    out = []
    for p in ramping(people):
        name, gl = p["name"], d(p["go_live"])
        k_now = next((k for k in range(1, 4) if add_months(gl, k - 1) <= through < add_months(gl, k)), None)
        cur = nums.month(p, k_now, through) if k_now else None
        items = open_items(feedback, name)

        # Free replacement period.
        if p.get("replacement_deadline"):
            dl = d(p["replacement_deadline"])
            last_wd = back_to_weekday(dl - timedelta(days=1))
            stages = [(f"{n}d", back_to_weekday(dl - timedelta(days=n)), dl) for n in (14, 7, 3)]
            stages.append(("last_weekday", last_wd, dl))

            def render(stage, p=p, dl=dl, last_wd=last_wd, cur=cur, k_now=k_now, items=items):
                left = (dl - today).days
                src = f" ({p['referral_payout_status']})" if p.get("referral_payout_status") else ""
                when = "tomorrow" if left == 1 else ("today" if left == 0 else f"in {left} days")
                lines = [f"*{p['name']}: free replacement period ends {when}, {fmt_day(dl)}*{src}. "
                         f"After that, replacing {p['name']} costs us."]
                if today >= last_wd:
                    lines.append(f"Today is the last weekday before it ends: please make the keep-or-replace call today.")
                else:
                    lines.append(f"Jay: please give {p['name']}'s calls extra attention (listen to 2-3 live calls) "
                                 f"and make the keep-or-replace call by {fmt_day(last_wd)}.")
                if cur:
                    t = RAMP[k_now]
                    lines.append(f"Ramp month {k_now}, day {cur['day']} of {cur['days']} (through {cur['through']:%-m/%-d}): "
                                 f"{stats_line(cur)}. Month target {fmt_money(t['cash'])}, floor {fmt_money(t['floor'])}.")
                if items:
                    lines.append(f"Jay's open feedback items: {items_text(items)}.")
                return lines
            out.append((f"{name}:replacement:{dl}", stages, render))

        # Ramp months 1-3: ending soon, and ended.
        for k in range(1, 4):
            s, e = add_months(gl, k - 1), add_months(gl, k) - timedelta(days=1)
            if e < through - timedelta(days=14):
                continue

            def render_ending(stage, p=p, k=k, e=e):
                m = nums.month(p, k, through)
                m1 = nums.month(p, 1, through) if k == 2 else None
                t = month_targets(k, m1["cash"] if m1 else None)
                left = (e - today).days
                when = "today" if left == 0 else ("tomorrow" if left == 1 else f"in {left} days")
                lines = [f"*{p['name']}: ramp month {k} ends {when}, {fmt_day(e)}.*"]
                if m:
                    need = max(0, t["floor"] - m["cash"])
                    lines.append(f"Day {m['day']} of {m['days']}: {stats_line(m)}. "
                                 f"Target {fmt_money(t['cash'])}, floor {fmt_money(t['floor'])}, {t['sched']} scheduled, "
                                 f"{round(t['ctc'] * 100)}% CTC.")
                    lines.append(f"{fmt_money(need)} more needed to reach the floor." if need
                                 else "Already above the floor.")
                lines.append({1: "$100K or more puts month 2 on the open path; under the $75K floor is out.",
                              2: "Under the $150K floor is out.",
                              3: "Missing $200K or 25% CTC is a cut; otherwise the ramp is complete."}[k])
                return lines

            def render_ended(stage, p=p, k=k, e=e):
                m = nums.month(p, k, through)
                m1 = nums.month(p, 1, through) if k == 2 else None
                t = month_targets(k, m1["cash"] if m1 else None)
                lines = [f"*{p['name']}: ramp month {k} ended {fmt_day(e)}.* Sheila and Jay: decision needed."]
                if m:
                    lines.append(f"Final: {stats_line(m)}. Target {fmt_money(t['cash'])}, floor {fmt_money(t['floor'])}.")
                    cash, rate = m["cash"], (m["closes"] / m["live"] if m["live"] else 0)
                    if k == 1:
                        if cash >= OPEN_PATH_LINE:
                            v = "At or above $100K: month 2 on the open path (75 scheduled, $200K, 20% CTC)."
                        elif cash >= NEAR_MISS:
                            v = "Near miss ($95K-$99.9K): Sheila decides the month 2 path."
                        elif cash >= t["floor"]:
                            v = "Above the $75K floor but under $100K: month 2 on the throttled path (60 scheduled, $150K)."
                        else:
                            v = "Below the $75K floor: out by the SOP, unless you keep them given calls received and start date."
                    elif k == 2:
                        v = ("Below the $150K floor: out by the SOP, unless you decide otherwise." if cash < t["floor"]
                             else "Above the floor: on to ramp month 3 ($200K and 25% CTC).")
                    else:
                        miss = [x for x, bad in (("$200K", cash < t["floor"]), ("25% CTC", rate < t["ctc"])) if bad]
                        v = (f"Missed {' and '.join(miss)}: a cut by the SOP, unless you decide otherwise." if miss
                             else "Hit $200K and 25% CTC: ramp complete.")
                    lines.append(v)
                return lines

            ending_due = back_to_weekday(e - timedelta(days=7))
            out.append((f"{name}:month{k}_ending:{e}", [("7d", ending_due, e)], render_ending))
            ended_due = fwd_to_weekday(e + timedelta(days=1))
            out.append((f"{name}:month{k}_ended:{e}", [("result", ended_due, e + timedelta(days=10))], render_ended))

        # 5th and 10th showed call checkpoints, only while Jay has open items.
        if items:
            for n, day_ in nums.live_crossings(p, through).items():
                def render_cp(stage, p=p, n=n, day_=day_, items=items):
                    lines = [f"*{p['name']}: reached showed call #{n} on {fmt_day(day_)}.* "
                             f"Jay: please review one recent call against the open items and reply here with fixed / still open."]
                    lines.append(f"Open items: {items_text(items)}.")
                    lines.append("By the SOP, items still open now mean watch." if n == 5 else
                                 "By the SOP, items still open at the 10th showed call mean out.")
                    return lines
                due = fwd_to_weekday(day_ + timedelta(days=1))
                out.append((f"{name}:showed{n}", [(f"call{n}", due, due + timedelta(days=7))], render_cp))
    return out


def sweep(today):
    roster = json.loads((ROOT / "config/roster.json").read_text())
    snap = ROOT / "data/snapshots" / today.isoformat()
    if not (snap / "roster_sheet.json").exists():
        raise SystemExit("STOP: no roster_sheet.json for today. Read the New Hire Tracker Roster tab first "
                         "(python3 -m ramp.roster_sheet). Post nothing.")
    from ramp.roster_sheet import merged
    people = merged(snap, roster)
    feedback = {k: v for k, v in json.loads((ROOT / "config/feedback.json").read_text()).items() if not k.startswith("_")}
    sent = set(json.loads(SENT.read_text())) if SENT.exists() else set()
    nums = Numbers(snap)
    due, mark = [], []
    for group, stages, render in events(today, people, feedback, nums):
        live = [(st, du, vu) for st, du, vu in stages if du <= today <= vu and f"{group}:{st}" not in sent]
        if not live:
            continue
        stage = max(live, key=lambda x: x[1])
        # Mark every stage already due (including skipped earlier ones), so only the latest is ever sent.
        keys = [f"{group}:{st}" for st, du, _ in stages if du <= today]
        due.append({"group": group, "stage": stage[0], "lines": render(stage[0]), "keys": keys})
        mark += keys
    return due, mark, nums.sh is not None


def message(today, due, have_numbers):
    if not due:
        return ""
    # Most urgent first: replacement deadlines, then month results, checkpoints, month endings.
    order = {"replacement": 0, "ended": 1, "showed": 2, "ending": 3}
    due = sorted(due, key=lambda x: next((v for k, v in order.items() if k in x["group"]), 9))
    out = [f"<@{JAY}> <@{SHEILA}> New-closer milestones for {today:%a %-m/%-d}:"]
    for x in due:
        out.append("")
        out.append("• " + x["lines"][0])
        out += [f"    {line}" for line in x["lines"][1:]]
    if not have_numbers:
        out += ["", "_Numbers left out: the EOD Tracker and Sales Tracker couldn't be read this morning._"]
    else:
        out += ["", "_Numbers through yesterday from the EOD Tracker (calls) and Sales Tracker (closes, cash). "
                     "Dates from the New Hire Tracker Roster tab._"]
    return "\n".join(out)


def main():
    today = d(sys.argv[1])
    outdir = ROOT / "outputs" / today.isoformat()
    if "--mark" in sys.argv:
        sent = set(json.loads(SENT.read_text())) if SENT.exists() else set()
        sent |= set(json.loads((outdir / "milestones.json").read_text())["mark"])
        SENT.write_text(json.dumps(sorted(sent), indent=1) + "\n")
        print(f"logged {len(sent)} sent reminders")
        return
    due, mark, have = sweep(today)
    outdir.mkdir(parents=True, exist_ok=True)
    text = message(today, due, have)
    (outdir / "milestones.md").write_text(text + ("\n" if text else ""))
    (outdir / "milestones.json").write_text(json.dumps({"due": due, "mark": mark, "numbers": have}, indent=1, default=str))
    print(text or "Nothing of note today: post nothing.")


if __name__ == "__main__":
    main()
