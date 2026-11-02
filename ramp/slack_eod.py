"""Parse #closer-eod reports (Nero's "NEW EOD REPORT" posts) from saved Slack read results.

Usage: python3 -m ramp.slack_eod <out.json> <slack_result_file> [<slack_result_file> ...]

Each input file is a saved result of the Slack read_channel tool: JSON with a "messages" string.
Writes one row per (closer, report date), keeping the latest post when a report was re-posted.
"""
import json
import re
import sys

NOT_ATTENDED = re.compile(r"No-show|No show|Missed|clarification|[Cc]ancel|Reschedul|moved the call")


def to_int(v):
    v = (v or "").replace("$", "").replace(",", "").strip()
    try:
        return int(float(v))
    except ValueError:
        return None


def parse_report(text):
    # Labels come bold ("*How Many Showed:* 2") or plain ("How Many Showed: 2"); "Cancelled" is spelled both ways.
    corrected = {}
    for line in re.findall(r"Corrected:\s*([^\n\[]*)", text):
        for part in line.split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                corrected[k.strip()] = v.strip()

    def field(label):
        if label in corrected:
            return corrected[label]
        m = re.search(r"(?m)^\*?" + re.escape(label) + r":\*?[ \t]*([^\n]*)", text)
        return m.group(1).strip() if m else ""

    raw_name = field("Name")
    mention = re.search(r"\|([^>]+)>", raw_name)
    name = mention.group(1) if mention else re.sub(r"[<>@*]", "", raw_name).strip()
    date = re.search(r"\d{4}-\d{2}-\d{2}", field("Date"))
    if not name or not date:
        return None
    posted = re.findall(r"\[(\d{4}-\d\d-\d\d [\d:]+ E[DS]T)\]", text)
    calls = re.split(r"How Did The Calls Go Today:\*?", text)[-1]
    calls = re.split(r"\*?How Many Verbals", calls)[0]
    bullets = re.split(r"\n(?:• |- \*\*|\*\*)", calls)[1:]
    attended = [b for b in bullets if not NOT_ATTENDED.search(b.split("\n")[0])]
    showed = to_int(field("How Many Showed"))
    return {
        "name": name,
        "date": date.group(0),
        "posted": posted[-1] if posted else "",
        "scheduled": to_int(field("How Many Calls Scheduled")),
        # When the bot marks shows "Needs confirmation", count the calls the report lists as attended.
        "showed": showed if showed is not None else len(attended),
        "showed_estimated": showed is None,
        "offers": to_int(field("How Many Offers Made")),
        "closed": to_int(field("How Many Closed")),
        "cash": to_int(field("How Much Cash Collected")),
        "provisional": "Provisional report" in text,
        "corrected": bool(corrected),
    }


def parse_files(paths):
    best = {}
    for p in paths:
        for chunk in re.split(r"\n(?=[^\n]*: \*NEW EOD REPORT\*)", json.load(open(p))["messages"]):
            if "NEW EOD REPORT" not in chunk:
                continue
            r = parse_report(chunk)
            if not r:
                continue
            k = (r["name"], r["date"])
            if k not in best or r["posted"] >= best[k]["posted"]:
                best[k] = r
    return sorted(best.values(), key=lambda r: (r["name"], r["date"]))


def main():
    rows = parse_files(sys.argv[2:])
    json.dump(rows, open(sys.argv[1], "w"), indent=1)
    print(f"{len(rows)} EOD reports, {min(r['date'] for r in rows)} to {max(r['date'] for r in rows)}" if rows else "no EOD reports found")


if __name__ == "__main__":
    main()
