import json, sys
from datetime import date, timedelta
sys.path.insert(0, "/home/user/closer-ramping")
from ramp.build import ROOT, Data, d
from ramp.compare import measure, PAST
S = sys.argv[1]
snap = ROOT / "data/snapshots/2026-10-29"
data = Data(snap)
for m in ("2026-10", "2026-11"):
    for c in json.load(open(f"{S}/closers_{m}.json"))["data"]["closers"]:
        data.tracker[(m, c["rep_id"])] = c
people = {p["name"]: p for p in json.loads((ROOT / "config/roster.json").read_text())["people"]}
cw = json.loads((snap / "close_discovery_windows.json").read_text())
CASH_THROUGH, EOD_THROUGH = date(2026, 11, 1), date(2026, 10, 28)
out = {}
for n in ["Oliver", "Tyler"] + PAST:
    p = people[n]; gl = d(p["go_live"])
    cash, live, closes = [], [], []
    from ramp.build import add_months
    total = (add_months(gl, 1) - gl).days
    for k in range(1, total + 1):
        day = gl + timedelta(days=k - 1)
        if day <= CASH_THROUGH:
            data.through = CASH_THROUGH
            cash.append(measure(n, p, data, cw, 1, k)["cash"])
        if day <= EOD_THROUGH:
            data.through = EOD_THROUGH
            m = measure(n, p, data, cw, 1, k)
            live.append(int(m["live"])); closes.append(int(m["closes"]))
    out[n] = {"go_live": p["go_live"], "cash": cash, "live": live, "closes": closes,
              "cash_src": "EOD" if n == "Owen" else "tracker", "current": n in ("Oliver", "Tyler")}
json.dump(out, open(f"{S}/series.json", "w"))
for n, v in out.items():
    print(f"{n:8} go {v['go_live']} days={len(v['cash'])} cash@d18={v['cash'][17] if len(v['cash'])>17 else None} cash@30={v['cash'][-1]} live@14={v['live'][13] if len(v['live'])>13 else None} live_last={v['live'][-1]} closes_last={v['closes'][-1]}")
