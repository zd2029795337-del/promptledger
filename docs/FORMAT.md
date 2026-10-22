# Suite and replay format

Use UTF-8 JSON. Files may start with a UTF-8 BOM. Duplicate keys, nonfinite numbers, unknown suite/case/check fields, unsupported versions, duplicate IDs, and empty suites/check arrays are rejected before generation.

```json
{
  "version": 1,
  "name": "Issue classification",
  "instructions": "Return the requested raw JSON object.",
  "cases": [
    {
      "id": "empty-file",
      "prompt": "Classify an empty-file crash as a bug.",
      "checks": [
        {"type": "json_valid"},
        {"type": "json_path_equals", "path": "/category", "value": "bug"}
      ]
    }
  ]
}
```

`instructions` is optional and defaults to an empty string. All other shown fields are required. Names and IDs must be nonblank strings; prompts must be strings. Every case must have at least one check. All checks are combined with AND.

## Checks

| Type | Fields in addition to `type` | Semantics |
| --- | --- | --- |
| `contains` | `value`: string; optional `case_sensitive`: boolean | Required substring; case-insensitive by default |
| `not_contains` | Same as `contains` | Forbidden substring; case-insensitive by default |
| `equals` | Same as `contains` | Full-string match; case-sensitive by default; whitespace is significant |
| `json_valid` | None | Raw strict JSON of any JSON type, including null; Markdown fences fail |
| `json_path_equals` | `path`: JSON pointer; `value`: any finite JSON value | Resolve the pointer and compare the value; a missing path fails |
| `max_length` | `value`: nonnegative integer | Length at most the specified Unicode code point count |

Case-insensitive checks use Unicode `casefold()`. They do not normalize whitespace or Unicode composition. An empty `contains` value always matches; an empty `not_contains` value always fails, so use meaningful nonempty expectations.

Pointers follow RFC 6901: `/labels/0` indexes the first array item, `/a~1b` addresses key `a/b`, and `/a~0b` addresses `a~b`. The empty pointer `""` compares the root. Missing values differ from JSON `null`; booleans differ from numbers. JSON object key ordering does not affect equality. Array ordering does.

## Replay files

```json
{
  "empty-file": "{\"category\":\"bug\"}"
}
```

Every suite ID must have exactly one string value. Missing and extra IDs are errors. Replies can be hand-authored fixtures or recorded outputs; mark their provenance in your own dataset. Replay does not generate a new reply from the prompt. Replay latency is local evaluation overhead, and replay token usage is zero, not an estimate of an original generation.

## Reports and baselines

Each run writes `report.json`, `report.md`, and `report.html` to `--output`. These files are overwritten on a later run to the same directory. Input files and the baseline cannot be overwritten by those report targets. Keep baseline and current reports in separate directories.

JSON reports use `schema_version: 1`, metadata, summary counts and per-case results. `failed` includes provider errors; `errors` is a subset of `failed`, so `total = passed + failed`. Token totals cover successful parsed responses, not a billing record. A normalized `suite_snapshot` and its `suite_digest` record the full configuration. Per-case `check_signature` hashes the effective checks, including default case sensitivity, using canonical UTF-8 JSON and SHA-256.

`run --baseline` validates the baseline and rejects unrelated suite names, removed IDs, or altered checks before making requests. New IDs are allowed and must pass. A deliberate change to checks or coverage requires reviewing the suite and generating a fresh baseline without `--baseline`.

`diff BASELINE CURRENT` can inspect existing incompatible reports and returns nonzero for newly failing cases, removed cases, and changed checks. It reports improvements, added IDs, and changed outputs as information. Comparison does not certify model equivalence or statistical significance. Baselines are trusted review artifacts, not tamper-proof attestations.
