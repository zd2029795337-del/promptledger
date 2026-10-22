# PromptLedger

**Small regression checks for AI outputs, with offline replay and readable CI reports.**

[中文文档](README.zh-CN.md) · [Suite format](docs/FORMAT.md) · [Contributing](CONTRIBUTING.md) · [MIT license](LICENSE)

When you edit a prompt or switch models, did your issue classifier stop returning JSON? Did a release summary start making unsupported claims? PromptLedger runs explicit checks, compares a reviewed baseline, and returns a CI-friendly exit code.

It is an early-stage Python CLI with **zero runtime dependencies**, not a hosted service. The included replies are hand-authored synthetic fixtures; they are not measured model results.

## Try it without an API key

Requires Python 3.11 or newer. Run from the repository root:

```sh
python -m promptledger validate examples/maintainer-suite.json
python -m promptledger run examples/maintainer-suite.json --responses examples/responses.good.json --output reports/baseline
```

Expected result: **7/7 passed**, no network calls. Open `reports/baseline/report.html` in a browser, or read `report.md` and `report.json`.

Now replay deliberately regressed replies against the same checks:

```sh
python -m promptledger run examples/maintainer-suite.json --responses examples/responses.regressed.json --baseline reports/baseline/report.json --output reports/current
```

Expected result: **3/7 passed**, four regressions, exit code `1`. This intentional failure demonstrates the gate. See [the examples](examples/README.md) for what each case checks.

To install the `promptledger` command in a virtual environment:

```sh
python -m pip install -e .
promptledger --help
```

No package has been published to PyPI as part of this repository setup; use the source checkout.

## What it checks

| Check | Purpose |
| --- | --- |
| `contains` | Require text, optionally with case sensitivity |
| `not_contains` | Forbid text |
| `equals` | Require an exact reply |
| `json_valid` | Require raw, strict JSON |
| `json_path_equals` | Compare a JSON value at an RFC 6901 pointer |
| `max_length` | Bound output length in Unicode code points |

Suites are versioned JSON files. Case IDs are stable across runs. A case passes only if every check passes. A provider failure counts as a failed case and is recorded separately as an error.

Baselines compare existing cases by ID and check signature. Changed check definitions or removed cases require a reviewed new baseline; `run` rejects these before generation. Prompt, instruction, and model changes can be evaluated against the same checks. Added cases are evaluated normally. Changed output alone is informational: it is not necessarily a regression.

## Call OpenAI explicitly

The adapter uses the [OpenAI Responses API](https://developers.openai.com/api/docs/guides/text). Choose a model ID available to your API project; PromptLedger does not pick one for you.

PowerShell:

```powershell
$env:OPENAI_API_KEY = "YOUR_API_KEY"
$env:OPENAI_MODEL = "YOUR_AVAILABLE_MODEL_ID"
python -m promptledger run examples/maintainer-suite.json --provider openai --output reports/live --max-output-tokens 1024 --max-cases 10
```

macOS/Linux:

```sh
export OPENAI_API_KEY="YOUR_API_KEY"
export OPENAI_MODEL="YOUR_AVAILABLE_MODEL_ID"
python -m promptledger run examples/maintainer-suite.json --provider openai --output reports/live --max-output-tokens 1024 --max-cases 10
```

OpenAI mode sends suite instructions and each case prompt to `https://api.openai.com/v1/responses`. Each case makes one request, with no automatic retries. The request includes `store: false`, does not enable tools, and caps generated output tokens. This cap includes reasoning tokens and is **not a monetary budget**. Incomplete responses fail; increase the cap if your selected model needs more output tokens. Model access and API billing are separate from the offline tool.

Token totals include successfully parsed responses only. Failed or timed-out requests may still incur charges. Reports contain prompts, outputs, checks, and a suite snapshot; use synthetic or appropriately shareable data and review reports before publishing them. The tool does not read `.env` files or execute generated output. `store: false` is not a statement about all provider data retention; see [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data).

## Compare existing reports

```sh
python -m promptledger diff reports/baseline/report.json reports/current/report.json
```

`diff` uses existing files and makes no network calls. It gates newly failing cases, removed cases, and incompatible checks. It can return `0` when an already failing case is unchanged; use `run` when every current case must pass.

| Exit code | Meaning |
| ---: | --- |
| `0` | All run checks pass, or `diff` finds no regression/coverage incompatibility |
| `1` | A run case fails, or a comparison detects a regression/removed case/changed check |
| `2` | Configuration, input, or filesystem error |
| `130` | Interrupted run |

JSON/Markdown/HTML reports work offline. HTML contains no remote assets or scripts and escapes generated text. The JSON report stores a normalized suite snapshot and digest for provenance. Replay fixtures can contain outputs from another model or tool as long as they map every case ID to a string.

## Development

```sh
python -m unittest discover -s tests -v
```

Tests mock API transport; they do not require a key or spend credits. GitHub Actions is configured for Python 3.11 and 3.14 on Windows and Linux, and uploads replay reports. It runs without API secrets. Local execution alone does not establish that remote CI has passed.

The [maintenance plan](docs/MAINTENANCE.md) separates implemented behavior from planned Codex-assisted maintenance. Initial scope is explicit deterministic checks, small suites, sequential requests, and inspectable reports. Semantic judging, a hosted dashboard, concurrency, and automatic issue comments are outside this version.

Passing these checks establishes only the specified assertions. Keyword checks do not establish factual accuracy or prompt-injection resistance; the sample injection case is illustrative. Review important decisions and broaden your evaluation cases as your application evolves.
