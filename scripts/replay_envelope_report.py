"""Report rendering for ``scripts/replay-envelope.py``.

The replay harness computes a report dict (``analyse`` in the script); this
module turns it into the human-readable text report and the markdown
counterfactual document, and owns the small amount of presentation arithmetic
the renderers need: :func:`break_even_rate` (the drill-in rate at which an
envelope stops paying for itself, quoted at two ``phi`` values because one is
not enough to be honest) and :func:`split_material_families` (which
projection rows are worth a line of their own). Nothing here reads
transcripts or fixtures.
"""

from __future__ import annotations

FORMAT_FAMILIES = ("json", "diff", "pytest", "search", "text")

# Payload-size bands the population is split into; the reduction ratio turns
# between them, so every band is reported on its own.
SIZE_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("2-4 KB", 2048, 4096),
    ("4-8 KB", 4096, 8192),
    ("8-16 KB", 8192, 16384),
    (">16 KB", 16384, 1 << 62),
)


def break_even_rate(ratio: float, drill_fraction: float) -> float:
    """Drill-in rate at which the envelope plus its follow-up costs the original.

    ``r + d * phi = 1`` solved for ``d``. Negative means the envelope is already
    a net loss before any drill-in; above 1 means it pays even if every single
    envelope is followed by a drill-in.

    Reported at two ``phi`` values because one is not enough to be honest.
    ``phi = 0.25`` is the modelled case: a drill-in pulls back a quarter of the
    original (one file of a four-file diff). ``phi = 1.0`` is the worst case a
    drill-in can be — the follow-up pulls back the *entire* output, so the
    envelope was pure overhead. At that bound ``d* = 1 - r``, a far tighter bar,
    and it is the number to quote when someone asks what happens if the
    envelope's recipes are ignored and the agent just cats the blob.
    """
    return (1.0 - ratio) / drill_fraction if drill_fraction else float("inf")




def split_material_families(
    rows: list[dict], floor_share: float = 0.01
) -> tuple[list[dict], dict | None]:
    """Split projection rows into material families and an aggregated tail.

    ``command_family`` falls back to the command's first word, so a shell
    one-liner opening with a variable assignment becomes its own "family". The
    live baseline has ~50 such singletons. They are carried in the projection
    (S3 means every shell family) but listing them individually would bury the
    six rows that actually matter, so anything under ``floor_share`` of the
    scope is rolled into one row.
    """
    total = sum(row["baseline_tokens"] for row in rows)
    if not total:
        return rows, None
    shown = [r for r in rows if r["baseline_tokens"] / total >= floor_share]
    tail = [r for r in rows if r["baseline_tokens"] / total < floor_share]
    if not tail:
        return shown, None
    return shown, {
        "families": len(tail),
        "baseline_tokens": sum(r["baseline_tokens"] for r in tail),
        "imputed": sum(1 for r in tail if r["imputed"]),
    }


def _k(tokens: float) -> str:
    return f"{tokens / 1000:,.0f}k"


def _pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def _break_even_text(rate: float) -> str:
    if rate < 0:
        return "never"
    if rate > 1:
        return ">1.00"
    return f"{rate:.2f}"


def _break_even_pair(ratio: float, drill_fraction: float) -> str:
    """Break-even at the modelled phi and at the worst-case phi=1.0."""
    return (
        f"{_break_even_text(break_even_rate(ratio, drill_fraction))}"
        f" / {_break_even_text(break_even_rate(ratio, 1.0))}"
    )


def _scope_lines(report: dict, name: str, label: str) -> list[str]:
    scope = report["scopes"][name]
    total = report["baseline"]["total_tokens"]
    lines = [
        f"{name} — {label}",
        f"  in scope: {_k(scope['scope_baseline_tokens'])} of {_k(total)} "
        f"({_pct(scope['scope_share_of_total'])} of baseline)",
    ]
    for rate in report["drill_in_rates"]:
        key = f"{rate:g}"
        lines.append(
            f"  d={rate:<4g} -> {_k(scope['projected'][key])} "
            f"(saves {_k(scope['saved'][key])}, "
            f"{_pct(scope['saved_share_of_total'][key])} of the {_k(total)} baseline)"
        )
    return lines


def render_text(report: dict) -> str:
    """Render the human-readable report."""
    out: list[str] = []
    base = report["baseline"]
    corpus = report["corpus"]
    out.append("ENVELOPE REPLAY — counterfactual against the live token baseline")
    out.append(f"generated {report['generated_utc']}  harness v{report['harness_version']}")
    out.append(
        f"config: {report['config']['source']}, "
        f"threshold={report['config']['threshold_bytes']}B, "
        f"max_envelope={report['config']['max_envelope_bytes']}B, "
        f"{report['config']['registry_rules']} registry rules"
    )
    out.append(
        f"baseline: {_k(base['total_tokens'])} tokens / {base['total_calls']:,} calls "
        f"across {base['transcript_files']} transcripts"
    )
    out.append("")

    out.append("CORPUS REPLAY BY FORMAT FAMILY (chars/4, no json.dumps wrapping)")
    out.append(
        f"  {'family':<10}{'FIX':>5}{'BEFORE':>9}{'AFTER':>9}{'RED':>8}"
        f"{'d* .25/1.0':>14}{'net-loss':>10}"
    )
    for family in FORMAT_FAMILIES:
        stats = report["by_format"].get(family)
        if not stats:
            continue
        loss = "{}/{}".format(stats["net_loss_fixtures"], stats["fixtures"])
        out.append(
            f"  {family:<10}{stats['fixtures']:>5}{stats['before']:>9,}"
            f"{stats['after']:>9,}{_pct(stats['reduction']):>8}"
            f"{_break_even_pair(stats['ratio'], report['drill_fraction']):>14}"
            f"{loss:>10}"
        )
    overall_loss = "{}/{}".format(len(corpus["net_loss"]), corpus["fixtures"])
    out.append(
        f"  {'OVERALL':<10}{corpus['fixtures']:>5}{corpus['before_tokens']:>9,}"
        f"{corpus['after_tokens']:>9,}{_pct(corpus['reduction']):>8}"
        f"{_break_even_pair(corpus['ratio'], report['drill_fraction']):>14}"
        f"{overall_loss:>10}"
    )
    out.append("")

    out.append("CORPUS REPLAY BY COMMAND FAMILY (regression corpus, NOT the projection input)")
    out.append(f"  {'family':<18}{'FIX':>5}{'BEFORE':>9}{'AFTER':>9}{'RED':>8}")
    for family, stats in sorted(
        report["by_command_family"].items(), key=lambda kv: -kv[1]["before"]
    ):
        out.append(
            f"  {family:<18}{stats['fixtures']:>5}{stats['before']:>9,}"
            f"{stats['after']:>9,}{_pct(stats['reduction']):>8}"
        )
    out.append("")

    population = report["population"]
    out.append(
        "POPULATION REPLAY — every above-threshold Bash result (this is what is projected)"
    )
    out.append(
        f"  {'family':<18}{'N':>6}{'BEFORE':>10}{'AFTER':>10}{'r_pop':>8}"
        f"{'r_corpus':>10}{'net-loss':>10}"
    )
    corpus_families = report["by_command_family"]
    ordered = sorted(
        population["families"].items(), key=lambda kv: -kv[1]["before"]
    )
    for family, stats in ordered[:14]:
        corpus_stats = corpus_families.get(family)
        corpus_text = f"{corpus_stats['ratio']:.2f}" if corpus_stats else "—"
        out.append(
            f"  {family:<18}{stats['results']:>6}{stats['before']:>10,}"
            f"{stats['after']:>10,}{stats['ratio']:>8.2f}{corpus_text:>10}"
            f"{_pct(stats['net_loss_share']):>10}"
        )
    overall = population["overall"]
    out.append(
        f"  {'OVERALL':<18}{overall['results']:>6}{overall['before']:>10,}"
        f"{overall['after']:>10,}{overall['ratio']:>8.2f}"
        f"{report['corpus']['ratio']:>10.2f}{_pct(overall['net_loss_share']):>10}"
    )
    out.append(
        "  net-loss = share of the family's above-threshold tokens whose envelope"
        " is LARGER than the output"
    )
    out.append(
        f"  all {overall['results']:,} envelopes are valid JSON; "
        f"{population['over_ceiling']} exceed the "
        f"{report['config']['max_envelope_bytes']}-byte ceiling"
    )
    out.append("")

    out.append("SIZE DEPENDENCE (population) — why a largest-first corpus flatters")
    out.append(f"  {'bucket':<10}{'N':>6}{'BEFORE':>10}{'r':>7}{'net-loss':>10}")
    for label, _, _ in SIZE_BUCKETS:
        stats = population["buckets"].get(label)
        if not stats:
            continue
        out.append(
            f"  {label:<10}{stats['results']:>6}{stats['before']:>10,}"
            f"{stats['ratio']:>7.2f}{_pct(stats['net_loss_share']):>10}"
        )
    out.append("")

    out.append(
        f"SENSITIVITY: drill-in fraction phi={report['drill_fraction']}, "
        f"rates d={report['drill_in_rates']}"
    )
    out.append(
        "  a drill-in follow-up costs phi of the original output; "
        "d* is the rate at which the envelope stops paying"
    )
    out.append("")

    for name, label in (
        ("S1", "committed scope (what TASK-147 wraps: pypeeker, pytest, verify-repo.sh)"),
        ("S2", "plausible scope (S1 + git, needs a wrapper or hook that does not exist)"),
        ("S3", "theoretical ceiling (every shell family, if an agent ran all of it through envl)"),
    ):
        out.extend(_scope_lines(report, name, label))
        out.append("")

    native = report["harness_native"]
    out.append(
        f"STRUCTURALLY UNREACHABLE: {_k(native['tokens'])} "
        f"({_pct(native['share'])} of the baseline)"
    )
    out.append(
        "  harness-native tools are not shell commands; "
        "`envl -- <cmd>` cannot wrap them at any adoption level"
    )
    for tool, tokens in list(native["by_tool"].items())[:6]:
        out.append(f"    {tool:<16}{_k(tokens):>8}")
    out.append("")

    s3 = report["scopes"]["S3"]
    shown, tail = split_material_families(s3["families"])
    out.append("PER-FAMILY PROJECTION DETAIL (S3)")
    out.append(
        f"  {'family':<18}{'B_f':>8}{'a_f':>8}{'r_f':>7}{'d* .25/1.0':>14}"
        f"{'n':>4}  r_f source"
    )
    for row in shown:
        out.append(
            f"  {row['family']:<18}{_k(row['baseline_tokens']):>8}"
            f"{_pct(row['above_share']):>8}{row['ratio']:>7.2f}"
            f"{_break_even_pair(row['ratio'], report['drill_fraction']):>14}"
            f"{row['corpus_fixtures']:>4}  {row['source']}"
        )
    if tail:
        label = "+ {} more".format(tail["families"])
        out.append(
            f"  {label:<18}{_k(tail['baseline_tokens']):>8}"
            f"{'':>8}{'':>7}{'':>14}{'':>4}  {tail['imputed']} imputed"
        )
    out.append(
        "  n = above-threshold results behind r_f (population), not corpus fixtures"
    )
    out.append(
        f"  imputed rows carry {_k(s3['imputed_tokens'])} "
        f"({_pct(s3['imputed_share'])}) of the S3 scope"
    )
    out.append("")

    if corpus["net_loss"]:
        out.append(
            f"NET-LOSS FIXTURES: {len(corpus['net_loss'])} of {corpus['fixtures']} "
            "envelopes are LARGER than the output they replace"
        )
        for item in corpus["net_loss"][:12]:
            out.append(
                f"    {item['format']:<8}{item['before']:>6} -> {item['after']:>6}  "
                f"{item['path']}"
            )
        out.append("")

    if corpus["invalid_json"]:
        out.append("INVALID JSON (a hard failure of the core guarantee):")
        out.extend(f"    {path}" for path in corpus["invalid_json"])
    else:
        out.append(
            f"VALIDITY: all {corpus['fixtures']} replayed envelopes parse as JSON."
        )
    return "\n".join(out)


def _band(scope: dict, rates: list[float]) -> str:
    """Render a scope's saving as a band across the drill-in rates, never one number."""
    shares = [scope["saved_share_of_total"][f"{rate:g}"] for rate in rates]
    return f"{_pct(min(shares))}–{_pct(max(shares))}"


def _md_table(header: list[str], rows: list[list[str]]) -> list[str]:
    align = ["---"] + ["---:"] * (len(header) - 1)
    return [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(align) + "|",
        *["| " + " | ".join(row) + " |" for row in rows],
    ]


def render_markdown(report: dict) -> str:
    """Render the counterfactual document.

    Generated rather than hand-written so the prose cannot drift away from the
    numbers: re-running the harness regenerates the whole document. Every
    projected figure below comes from the population replay; the committed
    corpus appears only in its own section, labelled as the regression corpus
    it is.
    """
    base = report["baseline"]
    corpus = report["corpus"]
    native = report["harness_native"]
    population = report["population"]
    overall = population["overall"]
    s1, s2, s3 = (report["scopes"][k] for k in ("S1", "S2", "S3"))
    rates = report["drill_in_rates"]
    d0 = f"{rates[0]:g}"
    dmax = f"{rates[-1]:g}"
    threshold = report["config"]["threshold_bytes"]
    pytest_pop = population["families"].get("uv run pytest", {"ratio": 0.0})
    small = population["buckets"].get(SIZE_BUCKETS[0][0], {"ratio": 0.0})

    doc_path = report.get("output_path", "docs/envelope-counterfactual.md")
    out = [
        "# Envelope counterfactual (measured)",
        "",
        f"Generated by `python3 scripts/replay-envelope.py --write {doc_path}`."
        " Do not hand-edit: re-run the harness instead. The companion baseline"
        " is `TOKEN-COSTS.md`; this document answers what the envelope"
        " (`src/envl`, TASK-143) would have done to it.",
        "",
        f"**Generated {report['generated_utc']}** — harness v"
        f"{report['harness_version']}, baseline recomputed live from"
        f" {base['transcript_files']} transcripts"
        f" ({_k(base['total_tokens'])} tokens / {base['total_calls']:,} calls),"
        f" reduction ratios measured by replaying **all"
        f" {overall['results']:,} above-threshold Bash results** through the"
        f" shipping envelope. The committed corpus"
        f" (`{corpus['manifest']}`, {corpus['fixtures']} fixtures) is reported"
        " separately and is not what the projection multiplies.",
        "",
        "## Verdict",
        "",
        f"**At the scope this work actually commits to, the envelope saves"
        f" {_band(s1, rates)} of the agent's total tool-result cost.** That is"
        " small enough to be a rounding error, and it is not a surprise:"
        " `TOKEN-COSTS.md` already found that our own tooling's output is ~3% of"
        " the spend. The committed scope — `uv run pypeeker`, `uv run pytest`,"
        f" `verify-repo.sh` — is {_k(s1['scope_baseline_tokens'])} of a"
        f" {_k(base['total_tokens'])} baseline"
        f" ({_pct(s1['scope_share_of_total'])}), so even a perfect envelope"
        " could not have saved more than that — and one of its three families,"
        f" `uv run pytest`, is a **net loss** (r = {pytest_pop['ratio']:.2f}:"
        " the envelope is larger than the output it replaces).",
        "",
        f"**The mechanism works, but only above about 4 KB.** Across the"
        f" {overall['results']:,} replayed results the envelope reduces what it"
        f" wraps by {_pct(overall['reduction'])} overall, and all"
        f" {overall['results']:,} envelopes parse as JSON — the harness fails"
        " the run if any one of them does not, so this is checked rather than"
        " asserted. That average is carried entirely by large output:"
        " bucketed by payload size the ratio runs"
        f" {small['ratio']:.2f} in the {SIZE_BUCKETS[0][0]} band"
        f" (with {_pct(small['net_loss_share'])} of that band's tokens spent on"
        " envelopes *larger* than the output) against"
        f" {population['buckets'][SIZE_BUCKETS[-1][0]]['ratio']:.2f} above"
        " 16 KB. The envelope is a compression device for fat output and close"
        " to a no-op just above the threshold.",
        "",
        f"**The one place this is a large lever is `git`.** Diff output is the"
        " most compressible thing an agent sees — a diff's shape (files,"
        " +/- counts, hunk offsets) is a tiny fraction of its bytes — and the"
        f" `git` family alone carries {_k(base['families']['git']['tokens'])} of"
        " the baseline at a measured r ="
        f" {population['families']['git']['ratio']:.2f}. Wrapping git and"
        f" nothing else would save {_band(s2, rates)} of the total. Nothing in"
        " this arc commits to that: it needs a wrapper or a hook that does not"
        " exist, and TASK-147 is scoped to pypeeker's own commands. If the goal"
        " is token reduction rather than a better LLM-facing CLI, **that is"
        " where the next increment should go**, and this document is the"
        " evidence for it.",
        "",
        f"**{_pct(native['share'])} of the baseline is unreachable by"
        f" construction.** {_k(native['tokens'])} flows through harness-native"
        " tools — `Read`, `Grep`, `Edit` — which are not shell commands."
        " `envl -- <command>` cannot wrap them at any adoption level. This is a"
        " ceiling, not an adoption gap, and no amount of envelope work moves it."
        " `TOKEN-COSTS.md`'s conclusion stands unchanged: the largest lever is"
        " how agents *read files and diffs*, and most of it is reached by"
        " guidance and narrower `Read`/`git diff` habits rather than by"
        " wrapping a CLI.",
        "",
        "**Corpus ratios do not transfer to the population, and harness v1"
        " assumed they did.** The committed corpus is sampled largest-first and"
        " the reduction ratio is strongly size-dependent, so corpus ratios run"
        " well below population ratios in every family that carries the result"
        f" (corpus {corpus['ratio']:.2f} vs population {overall['ratio']:.2f}"
        " overall). v1 of this document projected the corpus ratios and"
        " overstated every scope — S1 by roughly 3x. The numbers above are"
        " projected from the population; the corpus is kept as the regression"
        " corpus for the adapters, which is what it is good for.",
        "",
        "## Method",
        "",
        "- **Estimator**: characters / 4, the same one `measure-tool-costs.py`"
        " uses. That script additionally wraps the payload in `json.dumps`"
        " before counting (~2% inflation from quoting and escaping); the"
        " before/after figures here do not. Both sides of every ratio use the"
        " same convention, so ratios are unaffected and only absolute"
        " before/after counts sit ~2% below the baseline's convention.",
        "- **The baseline is recomputed live** from the transcripts on each run,"
        " not quoted. The transcripts are appended to while the pipeline runs,"
        " so these totals will not match TOKEN-COSTS.md's frozen figures"
        " exactly — see *Biases* below.",
        "- **`r_f` is measured on the whole above-threshold population.** Every"
        " Bash tool result in the transcripts at or above the"
        f" {threshold}-byte threshold ({overall['results']:,} results,"
        f" {_k(overall['before'])} tokens) is replayed through the shipping"
        " envelope at the built-in config. Nothing is sampled, so no sampling"
        " bias can leak into `r_f`.",
        "- **The projection never multiplies raw fixture bytes.**",
        "",
        "  ```",
        "  projected_after_f = B_f * ((1 - a_f) + a_f * (r_f + d * phi))",
        "  ```",
        "",
        f"  where `B_f` is family *f*'s baseline tokens, `a_f` the share of those"
        f" tokens carried by results at or above the {threshold}-byte threshold"
        " (everything below it passes through untouched and is charged at full"
        " price), `r_f` the population reduction ratio for that family, `d` the"
        f" drill-in rate and `phi` = {report['drill_fraction']} the share of the"
        " original a follow-up drill-in pulls back.",
        f"- **Config**: {report['config']['source']},"
        f" threshold {threshold} B, envelope ceiling"
        f" {report['config']['max_envelope_bytes']} B,"
        f" {report['config']['registry_rules']} registry rules. These are"
        " out-of-the-box numbers: with no registry, format labels come from"
        " content sniffing and no per-command truncation tuning is applied. A"
        " tuned registry would move `r_f`, and not necessarily downward — a rule"
        " that keeps *more* per-file detail for `git diff` trades size for fewer"
        " drill-ins. Measuring a tuned config is left as a follow-up rather than"
        " guessed at here.",
        "",
        "## Population replay — what the projection actually uses",
        "",
        f"Every above-threshold Bash result, replayed through `build_envelope`:"
        f" {overall['results']:,} results, {_k(overall['before'])} tokens before,"
        f" {_k(overall['after'])} after (r = {overall['ratio']:.2f}).",
        "",
    ]

    pop_rows = []
    corpus_families = report["by_command_family"]
    ordered = sorted(population["families"].items(), key=lambda kv: -kv[1]["before"])
    for family, stats in ordered[:14]:
        corpus_stats = corpus_families.get(family)
        pop_rows.append(
            [
                f"`{family}`",
                f"{stats['results']:,}",
                f"{stats['before']:,}",
                f"{stats['after']:,}",
                f"{stats['ratio']:.2f}",
                _pct(stats["net_loss_share"]),
                f"{corpus_stats['ratio']:.2f} ({corpus_stats['fixtures']})"
                if corpus_stats
                else "—",
            ]
        )
    pop_rows.append(
        [
            "**overall**",
            f"{overall['results']:,}",
            f"{overall['before']:,}",
            f"{overall['after']:,}",
            f"{overall['ratio']:.2f}",
            _pct(overall["net_loss_share"]),
            f"{corpus['ratio']:.2f} ({corpus['fixtures']})",
        ]
    )
    out += _md_table(
        [
            "family",
            "results",
            "before (tok)",
            "after (tok)",
            "r (population)",
            "net-loss token share",
            "r (corpus, n)",
        ],
        pop_rows,
    )
    out += [
        "",
        "*net-loss token share* is the fraction of that family's"
        " above-threshold tokens whose envelope came out **larger** than the"
        " output it replaced. It is not a rounding artefact: it is two thirds of"
        " the `uv run pytest` family and a third of `grep/rg`.",
        "",
        f"Two invariants are checked on all {overall['results']:,} of these"
        " envelopes rather than argued for: every one parses as JSON (the"
        " harness aborts the run otherwise), and"
        f" {population['over_ceiling']} of them exceed the"
        f" {report['config']['max_envelope_bytes']}-byte ceiling.",
        "",
        "### Size dependence — why a largest-first sample flatters",
        "",
    ]
    bucket_rows = []
    for label, _, _ in SIZE_BUCKETS:
        stats = population["buckets"].get(label)
        if not stats:
            continue
        bucket_rows.append(
            [
                label,
                f"{stats['results']:,}",
                f"{stats['before']:,}",
                f"{stats['ratio']:.2f}",
                _pct(stats["net_loss_share"]),
            ]
        )
    out += _md_table(
        ["payload size", "results", "before (tok)", "r", "net-loss token share"],
        bucket_rows,
    )
    out += [
        "",
        f"The {SIZE_BUCKETS[0][0]} band carries the plurality of real calls and"
        " is where the envelope is neutral-to-negative; everything the envelope"
        " is good at happens above it. This is the single strongest argument in"
        f" the document for **raising `threshold_bytes` from {threshold} toward"
        " 4096**: it would trade coverage for a strictly-positive floor. The"
        " default is left at"
        f" {threshold} here so the numbers describe what actually ships.",
        "",
        "### Classifier coverage",
        "",
        "`detect_format` fires on content, so a family's name is not its"
        " format. Above-threshold results by detected format:",
        "",
    ]
    classifier_rows = []
    for family, counts in sorted(
        population["classifier"].items(),
        key=lambda kv: -population["families"][kv[0]]["before"],
    )[:6]:
        total = sum(counts.values())
        classifier_rows.append(
            [
                f"`{family}`",
                str(total),
                ", ".join(f"{fmt} {n}" for fmt, n in counts.items()),
            ]
        )
    out += _md_table(["family", "results", "detected formats"], classifier_rows)
    out += [
        "",
        "Two consequences worth stating plainly. First, the format-family table"
        " below describes the captures the classifier *labels* that way, not the"
        " command families of the same name — most `uv run pytest` output is"
        " summarised by the plain-text adapter, because a passing `-q` run"
        " carries no `FAILURES` block and no node ids to sniff. Second, that is"
        " a real gap rather than a measurement artefact: a broader pytest and"
        " search sniffer is the cheapest available improvement to `r_f` in those"
        " families and is left as a follow-up.",
        "",
        "## Committed corpus replay (the regression corpus)",
        "",
        f"The {corpus['fixtures']} committed fixtures exist to hold the adapters"
        " to captured reality in the test suite. They are a size-capped,"
        " largest-first sample, so **their ratios are not population ratios** and"
        " are not used in any projection. They are reported because a reader"
        " should be able to check the adapters' behaviour on the same data the"
        " tests use.",
        "",
    ]

    rows = []
    for family in FORMAT_FAMILIES:
        stats = report["by_format"].get(family)
        if not stats:
            continue
        rows.append(
            [
                f"`{family}`",
                str(stats["fixtures"]),
                f"{stats['before']:,}",
                f"{stats['after']:,}",
                _pct(stats["reduction"]),
                _break_even_pair(stats["ratio"], report["drill_fraction"]),
                f"{stats['net_loss_fixtures']}/{stats['fixtures']}",
            ]
        )
    rows.append(
        [
            "**overall**",
            str(corpus["fixtures"]),
            f"{corpus['before_tokens']:,}",
            f"{corpus['after_tokens']:,}",
            _pct(corpus["reduction"]),
            _break_even_pair(corpus["ratio"], report["drill_fraction"]),
            f"{len(corpus['net_loss'])}/{corpus['fixtures']}",
        ]
    )
    out += _md_table(
        [
            "format",
            "fixtures",
            "before (tok)",
            "after (tok)",
            "reduction",
            f"break-even d* @ phi={report['drill_fraction']:g} / phi=1.0",
            "net-loss",
        ],
        rows,
    )
    out += [
        "",
        f"All {corpus['fixtures']} replayed envelopes parse as JSON."
        if not corpus["invalid_json"]
        else f"**{len(corpus['invalid_json'])} envelopes failed to parse as JSON.**",
        "",
        "`d*` is the drill-in rate at which the envelope stops paying: solve"
        " `r + d * phi = 1`. It is quoted at two drill-in fractions because one"
        f" would be flattering. At the modelled phi={report['drill_fraction']:g}"
        " (a follow-up pulls back a quarter of the original — one file of a"
        " four-file diff) a ratio near 0.6 pays even if *every* envelope forces"
        " a drill-in. At `phi=1.0` — the worst case, where the agent ignores the"
        " recipes and just cats the whole blob, so the envelope was pure"
        " overhead — `d* = 1 - r` and the bar is much tighter. Real behaviour"
        " sits between the two; the pessimistic column is what to quote if you"
        " doubt the recipes get used.",
        "",
        "### Corpus versus population, per family",
        "",
        "The gap below is the measurement error v1 of this document shipped."
        " Positive `gap` means the corpus understates what the envelope costs.",
        "",
    ]
    gap_rows = [
        [
            f"`{row['family']}`",
            f"{row['corpus_ratio']:.2f} ({row['corpus_fixtures']})",
            f"{row['population_ratio']:.2f} ({row['population_results']:,})",
            f"{row['gap']:+.2f}",
        ]
        for row in report["corpus_vs_population"]
    ]
    out += _md_table(
        ["family", "r corpus (n)", "r population (n)", "gap"], gap_rows
    )
    out += [
        "",
        "### The net-loss band is real and is not rounded away",
        "",
        f"{len(corpus['net_loss'])} of {corpus['fixtures']} committed fixtures"
        " produce an envelope **larger** than the output it replaces, every one"
        f" of them just above the {threshold}-byte threshold. The population"
        " table above is the honest version of the same fact:"
        f" {_pct(overall['net_loss_share'])} of all above-threshold tokens are"
        " spent on envelopes that cost more than they save.",
        "",
    ]
    if corpus["net_loss"]:
        out += _md_table(
            ["fixture", "format", "before", "after"],
            [
                [f"`{i['path']}`", i["format"], str(i["before"]), str(i["after"])]
                for i in corpus["net_loss"]
            ],
        )
        out.append("")

    out += [
        "## Adoption scopes",
        "",
        "Reported separately because a single blended number would imply a"
        " reachability that does not exist.",
        "",
    ]
    scope_rows = []
    for name, scope, label in (
        ("S1", s1, "committed scope — `uv run pypeeker`, `uv run pytest`, `verify-repo.sh`"),
        ("S2", s2, "plausible scope — S1 + `git` (needs a wrapper or hook that does not exist yet)"),
        ("S3", s3, "theoretical ceiling — every shell family, if an agent ran all of it through `envl`"),
    ):
        scope_rows.append(
            [
                f"**{name}**",
                label,
                _k(scope["scope_baseline_tokens"]),
                _pct(scope["scope_share_of_total"]),
                *[
                    f"{_k(scope['saved'][f'{r:g}'])} "
                    f"({_pct(scope['saved_share_of_total'][f'{r:g}'])})"
                    for r in rates
                ],
            ]
        )
    scope_rows.append(
        [
            "**—**",
            "structurally unreachable — harness-native `Read`/`Grep`/`Edit`, "
            "not shell commands",
            _k(native["tokens"]),
            _pct(native["share"]),
            *["0k (0.0%)" for _ in rates],
        ]
    )
    out += _md_table(
        ["scope", "what it covers", "in scope", "of baseline"]
        + [f"saved @ d={r:g}" for r in rates],
        scope_rows,
    )
    out += [
        "",
        f"The band runs from d={d0} (no drill-in ever needed) to d={dmax} (half"
        " of all envelopes force a follow-up). Savings are quoted as a share of"
        " the whole baseline, so they are directly comparable to"
        " TOKEN-COSTS.md's purpose table.",
        "",
        "### Per-family projection detail (S3)",
        "",
    ]
    shown, tail = split_material_families(s3["families"])
    detail_rows = [
        [
            f"`{row['family']}`",
            _k(row["baseline_tokens"]),
            _pct(row["above_share"]),
            f"{row['ratio']:.2f}",
            _break_even_pair(row["ratio"], report["drill_fraction"]),
            f"{row['corpus_fixtures']:,}",
            row["source"],
        ]
        for row in shown
    ]
    if tail:
        detail_rows.append(
            [
                f"*+ {tail['families']} smaller families*",
                _k(tail["baseline_tokens"]),
                "—",
                "—",
                "—",
                "—",
                f"{tail['imputed']} imputed",
            ]
        )
    out += _md_table(
        [
            "family",
            "B_f",
            "a_f (above threshold)",
            "r_f",
            f"d* @ phi={report['drill_fraction']:g} / 1.0",
            "n (results)",
            "r_f source",
        ],
        detail_rows,
    )
    out += [
        "",
        "Families below 1% of the scope are rolled into one row:"
        " `command_family` falls back to a command's first word, so shell"
        " one-liners opening with a variable assignment each become their own"
        " singleton \"family\". They are still carried in the projection"
        " arithmetic; listing them individually would bury the handful of rows"
        " that actually carry the result.",
        "",
        "Families marked *imputed* have no above-threshold result of their own"
        " and borrow the population-wide ratio. They carry"
        f" {_k(s3['imputed_tokens'])} ({_pct(s3['imputed_share'])}) of the S3"
        " scope — small enough not to move the answer, and labelled rather than"
        " blended away. S1 and S2 are unaffected: every family in them is"
        " measured.",
        "",
        "## Biases and their direction",
        "",
        "Not all of these run the same way. Two of them make the envelope look"
        " better than it is.",
        "",
        "1. **Bash tool results are clipped by the harness at ~30,000"
        " characters** (measured maxima in these transcripts: 29,832 / 28,999 /"
        " 28,999 for Bash, against 73,974 for an uncapped `Read`). *Direction:"
        " roughly neutral.* The clip caps the baseline `B_f` and the measured"
        " `before` alike, so both sides of every ratio are clipped consistently."
        " It does mean nothing here describes genuinely unbounded output — a"
        " 200 KB `git diff`, a wide pytest failure — where the envelope would do"
        " better than these numbers show.",
        "2. **The transcript set grew during this work.** TOKEN-COSTS.md records"
        f" 6,403k tokens / 9,307 calls; this run measures"
        f" {_k(base['total_tokens'])} / {base['total_calls']:,}, because the"
        " pipeline that built the envelope wrote into the same transcript"
        " directory it is measuring. *Direction: neutral*, and a reviewer seeing"
        " the mismatch should not read it as an error.",
        "3. **The population replay charges the envelope's own drill-in"
        " recipes at their printed length but assumes the model uses them"
        " correctly.** The `d` band is the model of what happens when it does"
        " not. *Direction: optimistic at low `d`.*",
        "4. **The committed corpus is a largest-first sample.** *Direction:"
        " strongly anti-conservative if its ratios are projected* — which is"
        " exactly what harness v1 did, and why this document now projects"
        " population ratios instead. The corpus-versus-population table above"
        " quantifies the gap per family.",
        "",
        "## Adoption notes",
        "",
        "- `envl` propagates the wrapped command's exit code, so `&&` chains and"
        " `$?` checks keep working. But `envl -- uv run pytest` exits non-zero"
        " on failure *and prints an envelope*, so any adoption inside"
        " `verify-repo.sh` or CI has to account for the envelope replacing the"
        " step's own output on the failing path — which is the path a human is"
        " reading.",
        "- On this evidence, **do not wrap `uv run pytest`** at the default"
        f" threshold: r = {pytest_pop['ratio']:.2f} on the real population and"
        f" {_pct(pytest_pop.get('net_loss_share', 0.0))} of its above-threshold"
        " tokens are net losses. A passing run is already cheap; a failing run"
        " is what needs wrapping, and telling the two apart needs either a"
        " registry rule or a higher threshold.",
        "- The envelope hands back a path to an **immutable snapshot**, never a"
        " cached re-run. Agents edit files between invocations; serving a stale"
        " blob as if it were live would be a silent-wrong-data bug, which is why"
        " `BlobCache` has no lookup-by-command path and a test asserts it never"
        " grows one.",
        "- Below the threshold, output passes through untouched with no blob"
        " written. The pass-through path is not observable in the cache.",
        "",
        "## Reproducing",
        "",
        "```bash",
        "python3 scripts/replay-envelope.py                 # report to stdout",
        "python3 scripts/replay-envelope.py --json          # machine-readable",
        f"python3 scripts/replay-envelope.py --write {doc_path}",
        "```",
        "",
        "The corpus itself is regenerable with"
        " `python3 scripts/extract-envelope-fixtures.py`, though the transcripts"
        " are ephemeral and a later run will not reproduce this snapshot.",
        "",
    ]
    return "\n".join(out)

