"""The study instrument.

Runs the real survey and keeps everything: the raw call payload from the API, the full
transcript, the timings, and the structured answer. The contributed app in the fork is
deliberately incapable of writing any of this down, because that repository does not
accept real-call artifacts. This script is where the actual research data lives.

    python study.py --drug "metformin hcl" --strength "500 mg" --quantity 30 \
        --zip 10025 --authorized authorized_destinations.txt --confirm PLACE-REAL-CALLS

Nothing dials without --confirm, and only numbers in the authorized file are dialled.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "study_data"
DATA.mkdir(exist_ok=True)

sys.path.insert(0, str(HERE.parent / "app"))

from sticker.call_design import RECIPIENT_SCHEMA, DrugRequest, build_task  # noqa: E402
from sticker.calle import CalleTransport, idempotency_key  # noqa: E402
from sticker.nadac import lookup  # noqa: E402
from sticker.pharmacies import Pharmacy, find  # noqa: E402
from sticker.report import Quote, Survey, render_text  # noqa: E402
from sticker.survey import _quote_from, _unreached  # noqa: E402


def _count_barge_ins(events: list[dict], window: float = 1.5) -> int:
    """How many times the bot started speaking while the callee was still talking.

    The events stream emits "Callee speech detected" while the far end has the floor and
    "Bot is speaking" when ours takes it. A bot turn that opens within `window` seconds of
    the callee still being heard is the agent cutting across a person, which is the thing
    that gets a call hung up on.
    """
    speech: list[_dt.datetime] = []
    starts: list[_dt.datetime] = []
    for event in events:
        stamp = event.get("created_at")
        message = str(event.get("message") or "")
        if not stamp:
            continue
        when = _dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if message.startswith("Callee speech detected") or message.startswith("Callee said"):
            speech.append(when)
        elif message.startswith("Bot is speaking"):
            starts.append(when)
    return sum(
        1
        for start in starts
        if any(0 <= (start - heard).total_seconds() <= window for heard in speech)
    )


async def one(
    transport: CalleTransport,
    pharmacy: Pharmacy,
    *,
    task: str,
    run_id: str,
    gate: asyncio.Semaphore,
    records: list[dict],
) -> Quote:
    async with gate:
        began = _dt.datetime.now()
        print(f"  {began:%H:%M:%S} dialing  {pharmacy.name[:38]}", flush=True)
        try:
            call_id = await transport.create(
                phone=pharmacy.e164,
                task=task,
                recipient_schema=RECIPIENT_SCHEMA,
                metadata={"sticker_run_id": run_id, "sticker_npi": pharmacy.npi},
                idem_key=idempotency_key(
                    run_id=run_id, phone=pharmacy.e164, task=task, schema=RECIPIENT_SCHEMA
                ),
            )
            outcome = await transport.wait(call_id)
            # Re-read the terminal snapshot unmasked, so the study keeps exactly what the
            # API said rather than what our report layer chose to show.
            raw = await transport.get(call_id)
            # The events stream carries the turn-by-turn timeline, including when the bot
            # started speaking and when the callee was still talking. It is the only place
            # the platform exposes who was talking over whom, which is the thing this
            # study most needs to measure.
            try:
                events = (await transport.get(f"{call_id}/events")).get("data", [])
            except Exception:
                events = []
        except Exception as exc:
            elapsed = int((_dt.datetime.now() - began).total_seconds())
            records.append(
                {
                    "pharmacy": pharmacy.name,
                    "npi": pharmacy.npi,
                    "phone": pharmacy.e164,
                    "error": f"{type(exc).__name__}: {exc}",
                    "seconds": elapsed,
                }
            )
            print(f"  FAILED   {pharmacy.name[:38]}  {type(exc).__name__}", flush=True)
            return _unreached(pharmacy, f"{type(exc).__name__}")

        elapsed = int((_dt.datetime.now() - began).total_seconds())
        records.append(
            {
                "pharmacy": pharmacy.name,
                "npi": pharmacy.npi,
                "phone": pharmacy.e164,
                "address": pharmacy.address,
                "call_id": call_id,
                "seconds": elapsed,
                "raw": raw,
                "events": events,
                "barge_ins": _count_barge_ins(events),
            }
        )

        structured = (raw.get("recipients") or [{}])[0].get("structured_result") or {}
        status = raw.get("status")
        done = raw.get("task_completed")
        answered = structured.get("answered_by")
        price = structured.get("cash_price_usd")
        attempts = (raw.get("recipients") or [{}])[0].get("attempts") or []
        on_line = ""
        if attempts and attempts[0].get("started_at") and attempts[0].get("completed_at"):
            began_at = _dt.datetime.fromisoformat(attempts[0]["started_at"].replace("Z", "+00:00"))
            ended_at = _dt.datetime.fromisoformat(
                attempts[0]["completed_at"].replace("Z", "+00:00")
            )
            on_line = f" on_line={int((ended_at - began_at).total_seconds())}s"
        print(
            f"  {_dt.datetime.now():%H:%M:%S} done     {pharmacy.name[:26]:<28}{on_line}  "
            f"status={status} answered_by={answered} price={price} "
            f"barge_ins={_count_barge_ins(events)}",
            flush=True,
        )
        if not structured:
            return _unreached(pharmacy, f"ended {status} with no structured answer")
        return _quote_from(pharmacy, structured, call_id)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drug", default="metformin hcl")
    ap.add_argument("--strength", default="500 mg")
    ap.add_argument("--form", default="tablet")
    ap.add_argument("--quantity", type=int, default=30)
    ap.add_argument("--zip", dest="zip_code", default="10025")
    ap.add_argument("--org", default="an independent price comparison")
    ap.add_argument("--authorized", default="authorized_destinations.txt")
    ap.add_argument("--confirm", default="")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    if args.confirm != "PLACE-REAL-CALLS":
        print("Refusing: pass --confirm PLACE-REAL-CALLS to place real calls.")
        return 2

    allowed = frozenset(
        line.split("#")[0].strip()
        for line in Path(args.authorized).read_text(encoding="utf-8").splitlines()
        if line.split("#")[0].strip()
    )
    registry = {p.e164: p for p in find(args.zip_code, limit=200)}
    pharmacies = [registry[n] for n in sorted(allowed) if n in registry]
    missing = [n for n in allowed if n not in registry]
    if missing:
        print(f"{len(missing)} authorized number(s) are not in this ZIP's registry results.")

    drug = DrugRequest(
        name=args.drug, strength=args.strength, form=args.form, quantity=args.quantity
    )
    nadac = lookup(drug.nadac_prefix())
    print(f"\n{drug.quantity} x {drug.spoken()}  |  ZIP {args.zip_code}")
    print(f"Acquisition cost: ${nadac.cost_for(drug.quantity):.2f}  ({nadac.citation()})")
    print(f"Calling {len(pharmacies)} pharmacies, concurrency {args.concurrency}.\n")

    run_id = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    task = build_task(drug, caller_org=args.org)
    records: list[dict] = []
    started = _dt.datetime.now()

    transport = CalleTransport(
        api_key=os.environ["CALLE_API_KEY"],
        base_url=os.environ.get("CALLE_BASE_URL", "https://api.heycall-e.com"),
    )
    gate = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(
        *(one(transport, p, task=task, run_id=run_id, gate=gate, records=records) for p in pharmacies),
        return_exceptions=True,
    )
    await transport.aclose()
    finished = _dt.datetime.now()

    quotes = [
        r if isinstance(r, Quote) else _unreached(p, f"{type(r).__name__}")
        for p, r in zip(pharmacies, results)
    ]
    survey = Survey(
        drug=drug,
        postal_code=args.zip_code,
        quotes=quotes,
        nadac=nadac,
        started_at=started,
        finished_at=finished,
        live=True,
    )

    out = DATA / f"{run_id}.json"
    out.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "task": task,
                "drug": {
                    "name": drug.name,
                    "strength": drug.strength,
                    "form": drug.form,
                    "quantity": drug.quantity,
                },
                "zip": args.zip_code,
                "nadac": {
                    "ndc": nadac.ndc,
                    "description": nadac.ndc_description,
                    "per_unit": nadac.per_unit_usd,
                    "effective_date": nadac.effective_date,
                    "cost_for_quantity": nadac.cost_for(drug.quantity),
                },
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "calls": records,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n" + render_text(survey))
    print(f"\nRaw study data: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
