"""Turn a real study run into a static page anyone can open.

    python publish.py study_data/20260909-160118.json [more.json ...] -o docs/index.html

Two things this does deliberately.

It bakes the run into the HTML, so the published page needs no server, no API key and no
running process. The demo outlives the hackathon at zero cost, which is the only kind of
deployment worth leaving up.

And it anonymises. The pharmacies called are real, named businesses, and the outcomes are
unflattering in ways that are not really about them: a shop that did not pick up at four in
the afternoon has done nothing wrong, and neither has one whose phone tree asked for a
prescription number. Reporting "Independent 3" keeps every fact and drops the accusation.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path

HERE = Path(__file__).parent

CHAINS = ("cvs", "walgreen", "duane reade", "rite aid", "walmart", "costco", "kroger")

STATE_FOR = {
    "quoted": "done",
    "refused": "refused",
    "not_stocked": "not_stocked",
}

# "No answer" is the wrong word for most of what happened, and flattening everything into
# it hides the finding. A pharmacist who picked up and said something is a different fact
# from a phone nobody lifted, and a phone tree that never reached a person is a third
# thing. The board says which.
STATE_FOR_ENDPOINT = {
    "human": "no_price",
    "voicemail": "voicemail",
    "ivr": "phone_tree",
}


def _kind(name: str) -> str:
    lowered = name.lower()
    return "Chain" if any(c in lowered for c in CHAINS) else "Independent"


def _turns(events: list[dict], window: float = 1.5) -> tuple[list[dict], float]:
    """Who held the floor, second by second, from the moment the line opened.

    The platform emits point events rather than intervals, so these are marks on a
    timeline and not durations, and the page draws them that way. A bot mark falling
    within `window` of a pharmacy mark is a collision: the agent opening its turn while
    the person who answered still has the floor. That single event is what this whole
    board exists to show.
    """
    connect = None
    marks: list[tuple[float, str]] = []
    for event in events:
        stamp = event.get("created_at")
        message = str(event.get("message") or "")
        if not stamp:
            continue
        when = _dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if message.startswith("Call connected"):
            connect = when
        elif message.startswith("Bot is speaking"):
            marks.append((when.timestamp(), "bot"))
        elif message.startswith(("Callee said", "Callee speech detected")):
            marks.append((when.timestamp(), "them"))
    if not marks:
        return [], 0.0
    zero = connect.timestamp() if connect else min(t for t, _ in marks)
    span = max(t for t, _ in marks) - zero

    them = [t for t, ch in marks if ch == "them"]
    out: list[dict] = []
    for t, ch in sorted(marks):
        collide = ch == "bot" and any(0 <= t - h <= window for h in them)
        out.append({"t": round(t - zero, 2), "ch": ch, "x": collide})
    return out, round(span, 2)


def _seconds_on_line(raw: dict) -> int | None:
    attempts = (raw.get("recipients") or [{}])[0].get("attempts") or []
    if not attempts:
        return None
    first = attempts[0]
    if not (first.get("started_at") and first.get("completed_at")):
        return None
    began = _dt.datetime.fromisoformat(first["started_at"].replace("Z", "+00:00"))
    ended = _dt.datetime.fromisoformat(first["completed_at"].replace("Z", "+00:00"))
    return int((ended - began).total_seconds())


def build(paths: list[Path]) -> dict:
    rows: list[dict] = []
    counters: dict[str, int] = {}
    drug = quantity = postal = ""
    cost = None
    citation = url = ""
    started = finished = None
    calls = 0

    for path in paths:
        run = json.loads(path.read_text(encoding="utf-8"))
        d = run["drug"]
        drug = f"{d['name']} {d['strength']} {d['form']}"
        quantity = d["quantity"]
        postal = run["zip"]
        nadac = run.get("nadac") or {}
        cost = nadac.get("cost_for_quantity", cost)
        if nadac:
            citation = (
                f"CMS NADAC, {nadac.get('description')} (NDC {nadac.get('ndc')}): "
                f"${nadac.get('per_unit')} per each, effective {nadac.get('effective_date')}."
            )
            url = "https://data.medicaid.gov/dataset/dfa2ab14-06c2-457a-9e36-5cb6d80f8d93"
        first = _dt.datetime.fromisoformat(run["started_at"])
        last = _dt.datetime.fromisoformat(run["finished_at"])
        started = first if started is None else min(started, first)
        finished = last if finished is None else max(finished, last)

        for call in run["calls"]:
            calls += 1
            raw = call.get("raw") or {}
            recipient = (raw.get("recipients") or [{}])[0]
            structured = recipient.get("structured_result") or {}
            kind = _kind(call["pharmacy"])
            counters[kind] = counters.get(kind, 0) + 1
            label = f"{kind} {counters[kind]}"

            status = str(structured.get("quote_status") or "unknown")
            answered = str(structured.get("answered_by") or "unknown")
            state = STATE_FOR.get(status)
            if state is None:
                state = STATE_FOR_ENDPOINT.get(answered, "unreached")
            price = structured.get("cash_price_usd")
            try:
                price = float(str(price).lstrip("$"))
            except (TypeError, ValueError):
                price = None

            turns, span = _turns(call.get("events") or [])
            note = str(structured.get("notes") or "")
            if state == "voicemail":
                note = note or "Reached a voicemail box."
            if state == "unreached" and not note:
                note = "Nobody picked up."
            rows.append(
                {
                    "npi": "",
                    "name": label,
                    # The ZIP is already in the title block; repeating it on every row
                    # wraps the label to two lines and says nothing new.
                    "address": kind,
                    "city": "",
                    "phone_masked": "",
                    "state": state,
                    "price": price,
                    "quantity": quantity if price is not None else None,
                    "markup": round(price / cost, 1) if price and cost else None,
                    "note": note,
                    "transcript": [],
                    "seconds": _seconds_on_line(raw),
                    "turns": turns,
                    "span": span,
                    "collisions": len([t for t in turns if t["x"]]),
                }
            )

    prices = [r["price"] for r in rows if r["price"] is not None]
    headline: dict = {}
    if prices:
        low, high = min(prices), max(prices)
        ordered = sorted(prices)
        mid = ordered[len(ordered) // 2]
        headline = {
            "cheapest": round(low, 2),
            "dearest": round(high, 2),
            "median": round(mid, 2),
            "spread": round(high / low, 1) if low else None,
            "annual_difference": round((mid - low) * 12, 2),
            "markup_cheapest": round(low / cost, 1) if cost else None,
            "markup_dearest": round(high / cost, 1) if cost else None,
        }

    # Deliberately not a wall-clock span. These runs happened at intervals across a day,
    # so subtracting the first start from the last finish would read as one 82-minute
    # survey and would be a lie about how the calls were placed. The sum of time actually
    # spent connected is the honest figure.
    on_line = sum(r["seconds"] or 0 for r in rows)
    return {
        "status": "complete",
        "live": True,
        "published": True,
        "seconds_on_line": on_line,
        "drug": drug,
        "quantity": quantity,
        "postal_code": postal,
        "rows": rows,
        "acquisition_cost": cost,
        "nadac_citation": citation,
        "nadac_url": url,
        "started_at": started.isoformat() if started else "",
        "elapsed_seconds": on_line,
        "calls_placed": calls,
        "error": "",
        "run_id": "published",
        "headline": headline,
        "counts": {
            "called": len(rows),
            "quoted": len(prices),
            "refused": len([r for r in rows if r["state"] == "refused"]),
            "not_stocked": len([r for r in rows if r["state"] == "not_stocked"]),
            "unreached": len([r for r in rows if r["state"] == "unreached"]),
            "total": len(rows),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=HERE / "docs" / "index.html")
    args = ap.parse_args()

    data = build(args.runs)
    template = (HERE / "index.html").read_text(encoding="utf-8")
    payload = json.dumps(data, indent=1)
    page = template.replace(
        "<script>", f"<script>\nwindow.STICKER_RUN = {payload};\n</script>\n<script>", 1
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")

    print(f"Wrote {args.out}")
    print(f"  {data['counts']['total']} pharmacies, {data['counts']['quoted']} priced, "
          f"{data['counts']['refused']} refused, {data['counts']['unreached']} unreached")
    names = {r["name"] for r in data["rows"]}
    print(f"  anonymised to: {', '.join(sorted(names))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
