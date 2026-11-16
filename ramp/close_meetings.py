"""Close calendar meetings, boiled down to what the Intro-call rule needs (Sheila, 11/16).

Reads Close meeting pages already saved by inline curl calls (GET /activity/meeting/?user_id=...), never Close itself,
and writes <snapshot_dir>/close_meetings.json: one row per meeting with the Close user, lead, ET start date, status
and call type. The type comes from the "Event Name" in the calendar note:
- "Discovery Call ..."  -> "discovery" (closer call)
- "Intro Call ..."      -> "intro" (setter call)
- anything else (30/60-minute follow-up meetings, hand-made invites) -> "other"

Usage: python3 -m ramp.close_meetings <snapshot_dir> <folder of saved pages>
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def call_type(m):
    x = re.search(r"Event Name\s*\n([^\n]+)", m.get("note") or "")
    name = x.group(1) if x else ""
    if "Discovery" in name:
        return "discovery"
    if "Intro" in name:
        return "intro"
    return "other"


def export(snap, pages):
    seen, out = set(), []
    for f in sorted(Path(pages).glob("*.json")):
        for m in json.loads(f.read_text()).get("data", []):
            if m["id"] in seen or not m.get("starts_at"):
                continue
            seen.add(m["id"])
            out.append({"id": m["id"], "user_id": m["user_id"], "lead_id": m.get("lead_id"),
                        "date": datetime.fromisoformat(m["starts_at"].replace("Z", "+00:00")).astimezone(ET).date().isoformat(),
                        "status": m.get("status"), "type": call_type(m)})
    (Path(snap) / "close_meetings.json").write_text(json.dumps(out))
    return len(out)


if __name__ == "__main__":
    print(export(sys.argv[1], sys.argv[2]))
