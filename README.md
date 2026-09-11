# Sticker

**There is no sticker price on a prescription. Sticker calls the pharmacies and asks.**

Built for the [CALL-E: Your Code Is Calling](https://call-e.devpost.com/) hackathon.
Contributed upstream as
[CALLE-AI/awesome-phone-call-agents#404](https://github.com/CALLE-AI/awesome-phone-call-agents/pull/404).

## The problem

Pharmacies nationally paid about **43 cents** for thirty metformin tablets. That is not a
guess. It is the national average acquisition cost the federal government publishes every
week, surveyed from pharmacy invoices, and anyone can read it:

```
CMS NADAC, METFORMIN HCL 500 MG TABLET: $0.01419 per each, effective 2026-08-19.
https://data.medicaid.gov/dataset/dfa2ab14-06c2-457a-9e36-5cb6d80f8d93
```

What *you* pay is a different number. It differs at every counter, it changes, and it is
published nowhere. If you have insurance you rarely see it. If you are uninsured or have
not met a deductible, it is the only number that matters.

There is a structural reason it stays hidden. Pharmacy contracts with pharmacy benefit
managers commonly require the pharmacy to give the PBM its lowest price, so pharmacies keep
the posted cash price *above* their negotiated rates rather than competing on it
([USC Schaeffer Center, 2022](https://schaeffer.usc.edu/wp-content/uploads/2024/10/2022.05_US-Consumers-Overpay-for-Generic-Drugs.pdf)).
Comparison sites mostly show discount-card prices at chains, and roughly 19,000 independent
pharmacies sit largely outside that surface.

So the only reliable way to learn a cash price is to telephone the counter and ask. That is
not a workaround, it is the established research method. A 2020 study in *Psychiatric
Services* phoned **265 pharmacies over one month** and found a 30-day supply of one generic
ranging from **$29.99 to $1,345.00** in a single metro area
([Kriz et al.](https://doi.org/10.1176/appi.ps.201900319)). A 2017 study phoned 528
pharmacies in Los Angeles County and found an average **$52 difference inside a single ZIP
code** ([Arora et al.](https://pubmed.ncbi.nlm.nih.gov/28817779/)).

Sticker runs that survey with a phone agent instead of a research team.

## What is here

| Directory | What it is |
| --- | --- |
| [`app/`](app/) | The survey tool. A CLI that reads the federal pharmacy registry, calls the pharmacies you authorize, and prices every answer against CMS NADAC. Simulated by default. |
| [`skill/`](skill/) | A reusable CALL-E agent skill for a single retail cash-price enquiry. |
| [`console/`](console/) | A web console that runs a survey and shows the prices landing, plus the study instrument used for live research runs. |

## Try it without calling anyone

```bash
cd app
pip install -e ".[dev]"
python -m pytest tests -q      # 94 tests, no network, no credentials, no calls
sticker survey --offline       # the whole pipeline against a local stand-in
```

Nothing dials. The simulated CALL-E is an `httpx.MockTransport` mounted underneath the real
transport, so the request building, idempotency header, polling loop and error mapping
under test are the same ones a live run uses.

Two more read-only commands:

```bash
sticker find --zip 10025                                # licensed pharmacies near a ZIP
sticker cost --drug "atorvastatin" --strength "20 mg"   # the national average cost
```

## Safety, because the side effect is a telephone ringing

- **Simulated by default.** An API key in the environment does not change that. Live
  calling needs a separate switch, an explicit confirmation token, and an allowlist.
- **Discovery never dials.** `sticker find` writes a candidate list for a person to edit.
  A public business listing is not permission to call it, and an absent or empty allowlist
  authorizes nothing rather than everything.
- **Ambiguity halts the run.** A rejected request is a fact. A timeout, a server error, a
  lost read or an unrecognised status is not, because the phone may be ringing. The run
  stops before dialling anyone else and never retries.
- **Numbers are masked** everywhere except the one file the operator edits.
- **The call asks a retail price question only.** No patient, no prescription, no date of
  birth, no insurance details, no clinical advice.
- **The agent discloses that it is an AI** before it asks for anything, on every call and
  after every transfer.

## What happened on real calls

Sixteen calls were placed to licensed community pharmacies in one Manhattan ZIP code
across 2026-09-09 and 2026-09-10, at hours from early morning to late afternoon. Fifteen
are drawn on the [results page](https://jonathansolvesproblems.github.io/sticker/); the
first, a probe placed from the command line before the study instrument existed, reached a
closed pharmacy's voicemail. **Not one produced a usable cash price.** That is a finding
about the method, and it is reported here rather than buried:

- **Turn-taking is not something the caller controls.** The agent opens its turn while the
  person answering is still speaking. `CreateCallRequest` has six fields and rejects
  unknown keys, so there is no parameter for it, and no wording in the task prevented it.
  Filed upstream as
  [issue #415](https://github.com/CALLE-AI/awesome-phone-call-agents/issues/415), where a
  maintainer is now investigating. The mitigation in this repository is to open with a
  single word, so a collision costs one word instead of the whole question.
- **Chain pharmacy phone trees are a wall.** One chain's system asked for a ten-digit phone
  number for identity verification, could not validate a caller who has no patient record,
  and routed to voicemail. A price-checking caller cannot get past that by design.
- **Calls with a live human ended in 11 to 41 seconds**, typically just after the question
  was asked.

Whether pharmacies decline to price for a disclosed AI caller, or simply never heard the
question cleanly, is not something fifteen calls can separate. Testing it by removing the
disclosure would answer it and is not a thing this project will do.

## Known limitations

- NADAC is a national average acquisition cost, not what one pharmacy paid on one day. It
  is the right order of magnitude and the wrong number for any single invoice.
- NADAC resolves most generics by description; unusual strengths, combination products and
  most brand-only drugs will not match, and the report then omits the markup column.
- The NPI registry includes pharmacies that have closed or moved, so a survey contains
  unreachable numbers. They are reported as unreached rather than dropped.
- Prices would be a snapshot from one morning, not a standing price.

## Licence

MIT. See [LICENSE](LICENSE).
