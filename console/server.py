"""The Sticker console: run a real survey and watch the prices land.

Deliberately outside the contribution repository. This process touches real pharmacy
names, real numbers and real transcripts, none of which may appear in a pull request
there, so the console and its run files live here and reach judges through the demo and
the hosted results page instead.

    python -m uvicorn server:app --reload --port 8000

Nothing dials until you POST /api/run with confirm set, and only numbers present in the
authorized file are dialled.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

HERE = Path(__file__).parent
RUNS = HERE / "runs"
RUNS.mkdir(exist_ok=True)

# The app package lives in the fork, and is imported rather than copied so the console
# and the contributed CLI cannot drift apart.
sys.path.insert(0, str(HERE.parent / "app"))

from sticker.call_design import RECIPIENT_SCHEMA, DrugRequest, build_task  # noqa: E402
from sticker.calle import CalleTransport, idempotency_key  # noqa: E402
from sticker.nadac import NadacError, lookup  # noqa: E402
from sticker.pharmacies import Pharmacy, find  # noqa: E402
from sticker.safety import SafetyError, authorize_destinations, mask  # noqa: E402
from sticker.simulation import SIMULATED_BASE_URL, SimulatedCalle  # noqa: E402
from sticker.survey import _quote_from, _unreached  # noqa: E402

app = FastAPI(title="Sticker console")


@dataclass
class Row:
    """One pharmacy's line on the board, from dialling to price."""

    npi: str
    name: str
    address: str
    city: str
    phone_masked: str
    state: str = "waiting"  # waiting | dialing | on_call | done | refused | unreached
    price: float | None = None
    quantity: int | None = None
    markup: float | None = None
    note: str = ""
    transcript: list[dict] = field(default_factory=list)
    seconds: int | None = None


@dataclass
class RunState:
    """Everything the page needs, refreshed by polling."""

    status: str = "idle"  # idle | finding | calling | complete | error
    live: bool = False
    drug: str = ""
    quantity: int = 30
    postal_code: str = ""
    rows: list[Row] = field(default_factory=list)
    acquisition_cost: float | None = None
    nadac_citation: str = ""
    nadac_url: str = ""
    started_at: str = ""
    elapsed_seconds: int = 0
    calls_placed: int = 0
    error: str = ""
    run_id: str = ""

    def public(self) -> dict:
        data = asdict(self)
        prices = [r.price for r in self.rows if r.price is not None and r.quantity == self.quantity]
        data["headline"] = _headline(prices, self.acquisition_cost)
        data["counts"] = {
            "called": len([r for r in self.rows if r.state != "waiting"]),
            "quoted": len(prices),
            "refused": len([r for r in self.rows if r.state == "refused"]),
            "not_stocked": len([r for r in self.rows if r.state == "not_stocked"]),
            "unreached": len([r for r in self.rows if r.state == "unreached"]),
            "total": len(self.rows),
        }
        return data


def _headline(prices: list[float], cost: float | None) -> dict:
    if not prices:
        return {}
    low, high = min(prices), max(prices)
    prices_sorted = sorted(prices)
    mid = prices_sorted[len(prices_sorted) // 2]
    return {
        "cheapest": round(low, 2),
        "dearest": round(high, 2),
        "median": round(mid, 2),
        "spread": round(high / low, 1) if low > 0 else None,
        "annual_difference": round((mid - low) * 12, 2),
        "markup_cheapest": round(low / cost, 1) if cost else None,
        "markup_dearest": round(high / cost, 1) if cost else None,
    }


STATE = RunState()


class RunRequest(BaseModel):
    drug: str = "metformin hcl"
    strength: str = "500 mg"
    form: str = "tablet"
    quantity: int = 30
    postal_code: str = "10025"
    org: str = "an independent price comparison"
    live: bool = False
    confirm: str = ""
    authorized: str = "authorized_destinations.txt"
    max_calls: int = 12
    concurrency: int = 3


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(HERE / "index.html")


@app.get("/api/state")
async def state() -> JSONResponse:
    if STATE.status == "calling" and STATE.started_at:
        started = _dt.datetime.fromisoformat(STATE.started_at)
        STATE.elapsed_seconds = int((_dt.datetime.now() - started).total_seconds())
    return JSONResponse(STATE.public())


@app.post("/api/run")
async def run(request: RunRequest) -> JSONResponse:
    if STATE.status in {"finding", "calling"}:
        return JSONResponse({"error": "A run is already in progress."}, status_code=409)
    asyncio.create_task(_execute(request))
    return JSONResponse({"started": True})


async def _execute(request: RunRequest) -> None:
    global STATE
    STATE = RunState(
        status="finding",
        live=request.live,
        drug=f"{request.drug} {request.strength} {request.form}",
        quantity=request.quantity,
        postal_code=request.postal_code,
        started_at=_dt.datetime.now().isoformat(),
        run_id=_dt.datetime.now().strftime("%Y%m%d-%H%M%S"),
    )
    drug = DrugRequest(
        name=request.drug,
        strength=request.strength,
        form=request.form,
        quantity=request.quantity,
    )

    try:
        # The acquisition cost first, so the board can show what the pharmacy paid before
        # a single price lands. It is the yardstick, and it should be on screen first.
        try:
            row = lookup(drug.nadac_prefix())
            STATE.acquisition_cost = row.cost_for(drug.quantity)
            STATE.nadac_citation = row.citation()
            STATE.nadac_url = row.source_url
        except (NadacError, Exception):
            STATE.nadac_citation = "No published acquisition cost for this description."

        if request.live:
            pharmacies = await asyncio.to_thread(
                find, request.postal_code, limit=request.max_calls
            )
            allowlist = _read_authorized(Path(request.authorized))
            pharmacies = [p for p in pharmacies if p.e164 in allowlist]
            authorize_destinations([p.e164 for p in pharmacies], allowlist)
            if request.confirm != "PLACE-REAL-CALLS":
                raise SafetyError("Live runs need confirm set to PLACE-REAL-CALLS.")
        else:
            # Invented pharmacies on numbers reserved for fiction. A simulated run must
            # never hang made-up prices on the name of a real business: a screenshot of
            # this board would be indistinguishable from a real finding about them.
            pharmacies = _fixture_pharmacies(request.max_calls)

        if not pharmacies:
            raise SafetyError("No pharmacies to call. Check the ZIP or the authorized file.")

        STATE.rows = [
            Row(
                npi=p.npi,
                name=p.name.title(),
                address=p.address.title(),
                city=p.city,
                phone_masked=mask(p.e164),
            )
            for p in pharmacies
        ]
        STATE.status = "calling"
        STATE.calls_placed = len(pharmacies)

        transport = _transport(request)
        task = build_task(drug, caller_org=request.org)
        gate = asyncio.Semaphore(max(1, request.concurrency))
        await asyncio.gather(
            *(
                _call_one(transport, p, STATE.rows[i], task=task, gate=gate)
                for i, p in enumerate(pharmacies)
            ),
            return_exceptions=True,
        )
        await transport.aclose()

        STATE.status = "complete"
        _save(STATE)
    except Exception as exc:  # surfaced on the page rather than only in the log
        STATE.status = "error"
        STATE.error = f"{type(exc).__name__}: {exc}"


def _transport(request: RunRequest) -> CalleTransport:
    if request.live:
        return CalleTransport(
            api_key=os.environ["CALLE_API_KEY"],
            base_url=os.environ.get("CALLE_BASE_URL", "https://api.heycall-e.com"),
        )
    return CalleTransport(
        api_key="simulated", base_url=SIMULATED_BASE_URL, transport=SimulatedCalle().transport()
    )


async def _call_one(
    transport: CalleTransport,
    pharmacy: Pharmacy,
    row: Row,
    *,
    task: str,
    gate: asyncio.Semaphore,
) -> None:
    async with gate:
        began = _dt.datetime.now()
        row.state = "dialing"
        try:
            call_id = await transport.create(
                phone=pharmacy.e164,
                task=task,
                recipient_schema=RECIPIENT_SCHEMA,
                metadata={"sticker_run_id": STATE.run_id, "sticker_npi": pharmacy.npi},
                idem_key=idempotency_key(
                    run_id=STATE.run_id,
                    phone=pharmacy.e164,
                    task=task,
                    schema=RECIPIENT_SCHEMA,
                ),
            )
            row.state = "on_call"
            outcome = await transport.wait(call_id)
        except Exception as exc:
            row.state = "unreached"
            row.note = f"{type(exc).__name__}"
            return

        row.seconds = int((_dt.datetime.now() - began).total_seconds())
        row.transcript = [{"speaker": s, "text": t} for s, t in outcome.transcript]

        if not outcome.structured:
            row.state = "unreached"
            row.note = f"Call ended {outcome.status} with no structured answer."
            return

        quote = _quote_from(pharmacy, outcome.structured, outcome.call_id)
        row.note = quote.notes
        if quote.quote_status == "quoted" and quote.price_usd is not None:
            row.state = "done"
            row.price = quote.price_usd
            row.quantity = quote.quantity
            if STATE.acquisition_cost:
                row.markup = round(quote.price_usd / STATE.acquisition_cost, 1)
        elif quote.quote_status == "refused":
            row.state = "refused"
        elif quote.quote_status == "not_stocked":
            # A shop that does not carry the drug is a different fact from one nobody
            # reached, and collapsing the two would overstate how hard it is to get an answer.
            row.state = "not_stocked"
        else:
            row.state = "unreached"


def _fixture_pharmacies(count: int) -> list[Pharmacy]:
    """Invented counters for the simulated board, on NANP numbers reserved for fiction."""
    names = [
        ("Cedar Street Pharmacy", "Springfield", "412 Cedar Street"),
        ("Northgate Drug", "Springfield", "88 Northgate Plaza"),
        ("Harbour Chemists", "Riverton", "3 Harbour Row"),
        ("Oak & Vine Pharmacy", "Springfield", "1140 Oak Avenue"),
        ("Lakeside Community Drug", "Riverton", "27 Lakeside Drive"),
        ("Fifth Avenue Apothecary", "Springfield", "500 Fifth Avenue"),
        ("Union Square Pharmacy", "Riverton", "9 Union Square"),
        ("Greenfield Family Drug", "Springfield", "76 Greenfield Road"),
        ("Bellview Pharmacy", "Riverton", "212 Bellview Street"),
        ("Old Mill Drug Store", "Springfield", "4 Old Mill Lane"),
        ("Riverbend Pharmacy", "Riverton", "61 Riverbend Way"),
        ("Elm Court Chemists", "Springfield", "18 Elm Court"),
    ]
    return [
        Pharmacy(
            npi=f"000000{i:04d}",
            name=name,
            phone=f"202-555-{100 + i:04d}",
            address=address,
            city=city,
            state="EX",
            postal_code="00000",
        )
        for i, (name, city, address) in enumerate(names[: max(1, min(count, len(names)))])
    ]


def _read_authorized(path: Path) -> frozenset[str]:
    if not path.exists():
        raise SafetyError(
            f"No authorized destinations file at {path}. Live calling needs one."
        )
    return frozenset(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def _save(state: RunState) -> None:
    """Write the finished run so the hosted results page can render it without a server."""
    out = RUNS / f"{state.run_id}.json"
    out.write_text(json.dumps(state.public(), indent=2), encoding="utf-8")
