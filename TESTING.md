# Testing Sticker

Sixteen steps, in order. Steps 1 through 14 need no API key and place no calls. Steps 15
and 16 need nothing installed at all. Every expected result below was observed on a fresh
clone on 2026-09-11.

## Setup

**1. Clone and install.**

```bash
git clone https://github.com/JonathanSolvesProblems/sticker.git
cd sticker/app
pip install -e ".[dev]"
```

Expected: installs with no errors. The `[dev]` extra brings pytest.

**2. Run the test suite.**

```bash
python -m pytest tests -q
```

Expected: `94 passed` in under two seconds. No network, no credentials, no calls. This
proves the request building, idempotency header, polling loop, error mapping, masking,
allowlist reader, halt logic and call ceiling all behave, against a simulated wire mounted
under the real transport.

**3. See the commands.**

```bash
sticker --help
```

Expected: four commands. `find` and `cost` are read-only, `survey` is simulated by
default, `doctor` checks a key without spending a call.

## The survey, without calling anyone

**4. Simulated survey, fully offline.**

```bash
sticker survey --offline
```

Expected: a report headed `SIMULATED, no calls placed`, ten invented pharmacies with
invented names on 555-01xx numbers, a sorted price table, a lowest and highest line, and a
footer reading `10 simulated calls, none placed`. The header and the footer agree.

**5. Simulated survey with the real federal benchmark.**

```bash
sticker survey
```

Expected: the same simulated calls, plus a block reading `Published national average
acquisition cost for 30 of these: $0.43` with the CMS NADAC citation and effective date,
and a `vs NADAC` column with multiples. The block states that this is a national average
and not any pharmacy's invoice. This touches the CMS API and nothing else.

**6. Look up a cost directly.**

```bash
sticker cost --drug "atorvastatin" --strength "20 mg"
```

Expected: the CMS row, the per-unit price, the total for 30, the sentence "This is CMS's
national average, not any one pharmacy's invoice," and a link to the dataset.

Spell the drug the way CMS does. `atorvastatin calcium` returns a clear message saying
no row matched and to check the spelling against CMS's own wording, because NADAC lists
it without the salt. `metformin hcl` works because NADAC keeps that one.

## Discovery, which never dials

**7. Find pharmacies with numbers masked.**

```bash
sticker find --zip 10025 --limit 3
```

Expected: three licensed community pharmacies from the federal NPI registry, with numbers
shown as `+1******4400`. The terminal never shows an exact number.

**8. Write exact numbers to a file you then edit.**

```bash
sticker find --zip 10025 --limit 3 --out candidates.txt
```

Expected: the message `Exact numbers written to candidates.txt. It authorizes nothing
until you edit it.` Open the file: each line is an exact E.164 number followed by a `#`
comment naming the pharmacy. The file is the only place exact numbers ever appear.

## Every live gate refuses

None of these place a call. Each one fails a different gate and exits with code 2.

**9. Doctor without a key.**

```bash
sticker doctor
```

Expected: `CALLE_API_KEY is not set.`

**10. Live without the switch.**

```bash
sticker survey --live --confirm PLACE-REAL-CALLS --authorized candidates.txt
```

Expected: `Refusing: set STICKER_LIVE_CALLS_ENABLED=true to enable live calling.`

**11. Live without the confirmation token.**

```bash
STICKER_LIVE_CALLS_ENABLED=true sticker survey --live --authorized candidates.txt
```

Expected: `Refusing: pass --confirm PLACE-REAL-CALLS to place real calls.`

**12. Live without a key.**

```bash
STICKER_LIVE_CALLS_ENABLED=true sticker survey --live --confirm PLACE-REAL-CALLS --authorized candidates.txt
```

Expected: `Refusing: CALLE_API_KEY is not set.`

**13. Live with no allowlist file.**

```bash
STICKER_LIVE_CALLS_ENABLED=true CALLE_API_KEY=x sticker survey --live --confirm PLACE-REAL-CALLS --authorized missing.txt
```

Expected: `Refusing: No authorized destinations file at missing.txt.` A missing or empty
allowlist authorizes nothing rather than everything.

**14. A call ceiling of zero means zero.**

```bash
STICKER_LIVE_CALLS_ENABLED=true CALLE_API_KEY=x sticker survey --live --confirm PLACE-REAL-CALLS --authorized candidates.txt --max-calls 0 --zip 10025
```

Expected: `Refusing: 3 authorized numbers exceeds --max-calls 0.` The most cautious thing
an operator can type is honoured as a ceiling, not treated as "no limit."

## With a real key

If you have a CALL-E key, one more read-only step. It reads a call id that cannot exist and
proves the key authenticates without spending a call:

```bash
CALLE_API_KEY=your_key sticker doctor
```

Expected: `API key accepted.`

Placing real calls is deliberately not in this document. It needs all four gates open at
once and an allowlist you have edited by hand, and it rings real telephones.

## The console and the results page

**15. The web console.**

```bash
cd ../console
pip install fastapi "uvicorn[standard]"
python -m uvicorn server:app --port 8787
```

Open http://127.0.0.1:8787 and press **Call the pharmacies** with "place real calls"
unchecked. Expected: a `SAMPLE DATA` banner across the top, "sample data" where a ZIP would
be, invented pharmacy names, rows moving from queued to dialing to a price with a trace
drawn in each row's well, and a reading strip at the bottom once prices have landed. The
status line says `Simulated`.

**16. The published results page.**

Open https://jonathansolvesproblems.github.io/sticker/ in any browser, no install.

Expected: the same page with no banner, `Live calls`, fifteen anonymised rows
(`Independent 1`, `Chain 1`), a two-channel trace per call drawn over real seconds, magenta
marks where the agent opened its turn while the pharmacy still had the floor, a collision
count in the status line, and no price in any row. Try it in both light and dark system
themes. At a phone width the board collapses without horizontal scrolling.

## What each step proves

| Steps | Proves |
| --- | --- |
| 1, 2 | The contribution installs and its own claims hold, with no credentials |
| 4, 5 | The whole pipeline runs and reports honestly about being simulated |
| 6 | The federal benchmark is real, cited, and described as a national average |
| 7, 8 | Discovery never dials, and exact numbers exist only in an editable file |
| 9 to 14 | Four independent gates, each of which refuses on its own |
| 15 | The live surface, clearly marked as sample data when simulated |
| 16 | Sixteen real calls, reported with zero prices and the measured reason |
