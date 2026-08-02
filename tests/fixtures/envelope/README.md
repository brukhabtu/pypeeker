# Envelope fixture corpus

Real tool output, sampled from Claude Code subagent transcripts, used to develop
and measure the `envl` envelope against shapes that actually occur rather than
shapes we invented.

Regenerate or extend it with:

```bash
uv run python scripts/extract-envelope-fixtures.py            # regenerate
uv run python scripts/extract-envelope-fixtures.py --dry-run  # show the selection only
uv run python scripts/extract-envelope-fixtures.py --extend   # keep this corpus, top it up
```

`MANIFEST.json` is the index. Its header records the snapshot time and the scan
statistics; `fixtures[]` records each file's originating command, command family,
detected format, original size, exit code, and sha256.

## Why this exists in the repo at all

The transcripts live outside the repository, in an ephemeral container, and are
lost when it is reclaimed. Extracting them is preservation as much as testing:
without this corpus the 2026-08-02 token baseline in
`.claude/workflows/TOKEN-COSTS.md` stops being reproducible the moment the
container goes away.

**The transcripts were still being appended to during extraction** — the
extraction run itself writes into them. `snapshot_utc` in the manifest header is
therefore load-bearing: a later scan will see a slightly larger population and
will not reproduce these exact counts. That is expected, not an error.

## Size cap

**400 KB total** (`size_cap_bytes`), against roughly 6 MB of transcript payload.
The corpus exists to exercise every format adapter against real shapes; it does
not need to be a full copy of the transcripts to do that, and a multi-megabyte
blob in `tests/fixtures/` would be a permanent tax on every clone.

The cap is a ceiling, not a target. It is split into per-family budgets
(`per_family_caps`) weighted towards the families whose real output is largest
and least bounded:

| family | budget |
| --- | ---: |
| diff | 100 KB |
| text | 90 KB |
| search | 80 KB |
| json | 70 KB |
| pytest | 60 KB |

`json` and `pytest` underspend their budgets because the transcripts simply do
not contain that much of either — the population is exhausted, not truncated.

Fixtures are stored **verbatim**. A trimmed fixture would be a fixture whose
byte count lies, and the tests assert those byte counts against the manifest.

## Selection rule

Deterministic, so that a future regeneration produces a readable diff rather
than a reshuffled corpus:

1. **Bash tool results only.** `envl -- <command>` wraps shell commands. The
   harness-native tools (`Read`, `Grep`, `Edit`) are not shell commands and
   cannot be wrapped at any adoption level, so sampling them would describe a
   corpus the envelope can never see.
2. **Floor at 2048 bytes** (`min_fixture_bytes`), matching the envelope's default
   `threshold_bytes`. Output below the threshold passes through untouched by
   design, so sub-threshold captures are not envelope material.
3. **Dedupe by sha256** of the payload.
4. **Skip anything credential-shaped** — never redact. A redacted fixture is a
   fixture whose byte count lies. The rejection count is recorded in the
   manifest header as `candidates_rejected_for_secrets` so the check is
   auditable rather than merely asserted.
5. **Two tranches per family.** 70% of the budget goes to the largest captures;
   the remainder goes to captures nearest the surviving pool's median size. A
   family described only by its outliers only half-tests its adapter — `git diff`
   produces both 26 KB monsters and 3 KB one-file diffs.

Every ordering breaks ties on the sha256, so the corpus is a pure function of
the transcript directory.

## Format labels come from the library

The `format` field in the manifest is produced by `envl.detect_format` — the
shipping classifier — not by a heuristic local to the extractor. If the manifest
recorded some other classifier's labels, the replay harness's per-family numbers
would describe a classifier that is not the one in production.
`tests/test_envl_formats.py` asserts the agreement, so a change to the sniffing
order fails the suite instead of silently reclassifying the corpus.

## This corpus is a regression corpus, not a measurement sample

The selection rule above is largest-first on purpose — an adapter is best
exercised by the shapes that stress it — and that makes the corpus **unfit for
measuring how much the envelope saves in practice**. The reduction ratio is
strongly size-dependent: on the full transcript population the envelope's ratio
runs about 0.99 in the 2–4 KB band and about 0.09 above 16 KB, and the 2–4 KB
band is where most real calls sit. A ratio measured on this corpus is therefore
systematically better than the truth.

Harness v1 projected corpus ratios onto the token baseline and overstated every
adoption scope as a result. `scripts/replay-envelope.py` now replays **every
above-threshold Bash result in the transcripts** and projects that instead;
`.claude/workflows/ENVELOPE-COUNTERFACTUAL.md` reports both, side by side, with
the per-family gap. Quote the population numbers, not these.

**Bash tool results are clipped by the harness at roughly 30,000 characters.**
Measured over the source transcripts: the largest Bash results are 29,832 /
28,999 / 28,999 / 28,859 chars, while `Read` results — which are not clipped the
same way — reach 73,974 chars. So neither the corpus nor the population contains
a Bash output larger than about 30 KB, no matter how much a real `git diff` or a
real failing pytest run would actually have printed. That bias is roughly
neutral for ratios — it clips both sides — but it does mean nothing measured here
describes genuinely unbounded output.

## Exit codes

`exit_code` is derived from the transcript's `tool_result.is_error` flag: the
transcripts do not record the numeric exit status, so a failing command appears
here as `1`. The manifest header says the same thing in `exit_code_note`.
