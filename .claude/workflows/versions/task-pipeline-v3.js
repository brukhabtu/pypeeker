export const meta = {
  name: 'task-pipeline',
  description: 'Task pipeline v3: scout+plan → conduct (reads PIPELINE-GUIDANCE.md) → (plan review) → implement → gate → lenses → fix → (focused re-review) → final gate',
  whenToUse: 'Per-task execution in pypeeker. args: {task, spec?, repo?, mode?, scout?, conduct?, plan_review?, implement_model?, split?, lenses?, test_policy?, sanctioned_tests?, gate_check?, fix_rounds?, fixer_model?}. Explicit caller args always override conductor decisions. mode "plan" runs scout+plan only (read-only, safe concurrently); mode "full" mutates the tree at `repo` — one mutating pipeline per worktree. Frozen prior versions live in .claude/workflows/versions/ (launchable via scriptPath); each version ships with a retro there, distilled into PIPELINE-GUIDANCE.md which the conductor reads.',
  phases: [
    { title: 'Scout+Plan', detail: 'verified current-state map + design', model: 'opus' },
    { title: 'Conduct', detail: 'Opus decides pipeline shape within script caps', model: 'opus' },
    { title: 'Plan review', detail: 'adversarial attack on the plan (conductor-gated)', model: 'opus' },
    { title: 'Implement', detail: 'model chosen by conductor unless caller pinned' },
    { title: 'Gate', detail: 'pytest + ruff + self-lint, bounded fix loop', model: 'haiku' },
    { title: 'Review', detail: 'parallel adversarial lenses', model: 'opus' },
    { title: 'Fix', detail: 'apply confirmed findings, re-gate, optional one re-review round' },
  ],
}

// Defensive: some callers deliver args as a JSON-encoded string.
const A = typeof args === 'string' ? JSON.parse(args) : (args || {})
if (!A.task) throw new Error('task-pipeline requires args.task')
const REPO = A.repo || '/home/user/pypeeker'
const MODE = A.mode || 'full'
const SCOUT = A.scout !== false
const CONDUCT = A.conduct !== false
const GATE_CMDS = `cd ${REPO} && uv run pytest -q; uv run ruff check src tests; uv run pypeeker index src && uv run pypeeker check`
const TOOL_FAIL_NOTE = 'IMPORTANT: if the environment rejects your tool calls (spurious "missing required parameter" on valid calls), retry once; if still rejected, DO NOT loop and DO NOT fabricate — return your final/structured answer truthfully describing the failure. If blocked, say BLOCKED prominently.'

const TEST_POLICIES = {
  frozen:
    'NO pre-existing test may be modified — additions only; the entire pre-existing suite passing unmodified is part of the proof. New tests must assert behavior (bytes, codes, wording), not existence.',
  port:
    'This task deletes or replaces an internal API. Tests of the DELETED API must be PORTED scenario-by-scenario to the new API — equal or better coverage, never deleted without a one-for-one home; a scenario without a new home is a must-fix. Every OTHER pre-existing test — especially CLI-level and contract oracles — is frozen and must pass unmodified. A pre-existing test may be edited ONLY where it literally references the deleted API.',
  migrate:
    'This task DELIBERATELY breaks a contract the plan pins precisely. Tests asserting the OLD contract are migrated to the NEW one; every pre-change scenario (each refusal, edge case, and output-shape assertion) must exist under the new contract — a dropped scenario is a must-fix. Tests of contracts this task does not change are frozen. The implementer never decides mid-run that a failing frozen test is wrong: a frozen-test failure means the implementation is wrong.',
}
const DEFAULT_LENSES = [
  { key: 'correctness', focus: 'Frozen contracts and behavior: CLI JSON envelopes, refusal codes, TransactionSummary fields, check --fix report shape. Exercise the changed paths YOURSELF in a /tmp scratch project — do not only read the diff. must_fix for any divergence from documented behavior.' },
  { key: 'architecture', focus: 'Layering and idiom: import-boundaries (check never imports refactor; intents only models/query/storage), barrel-only, the registration idioms, no parallel code paths reintroduced, docs updated where the diff makes prose false. must_fix for genuine violations only.' },
  { key: 'tests', focus: 'Test integrity: verify the test policy held (git diff --cached --stat tests/), new tests are discriminating (would fail if the change were wrong — reason about or perform a mutation), no scenario lost, run the full gate yourself. must_fix for weak proofs or policy violations.' },
]

const SPEC = `${TOOL_FAIL_NOTE}\n\nTHE TASK: ${A.task}\n${A.spec ? `\nNORMATIVE REFERENCES (read in full before acting): ${A.spec}\n` : ''}\nRepo: ${REPO} (Python 3.14, uv — run everything via "uv run"; work in THIS directory, it may be a git worktree). Read CLAUDE.md and the relevant architecture.md sections first. Work in the working tree, NEVER git commit; end every mutating stage with "git add -A". NEVER edit anything under backlog/ — task bookkeeping belongs to the orchestrator after independent verification, not to pipeline agents.\n\nFROZEN unless the task explicitly says otherwise: CLI JSON envelopes and refusal codes, the check --fix report shape, TransactionSummary fields, import-boundaries layering, barrel-only discipline, the self-lint zero-baseline gate.`

const GATE = {
  type: 'object',
  properties: {
    passed: { type: 'boolean' },
    summary: { type: 'string' },
    failures: { type: 'string' },
  },
  required: ['passed', 'summary'],
}
const REVIEW = {
  type: 'object',
  properties: {
    clean: { type: 'boolean' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          line: { type: 'integer' },
          summary: { type: 'string' },
          why_it_breaks: { type: 'string' },
          must_fix: { type: 'boolean' },
        },
        required: ['file', 'summary', 'why_it_breaks', 'must_fix'],
      },
    },
  },
  required: ['clean', 'findings'],
}
const PLAN_SCHEMA = {
  type: 'object',
  properties: {
    current_state: { type: 'string', description: 'what the code does today — every claim verified by reading it, cited as file:function' },
    design: { type: 'string', description: 'the approach: decisions with rationale, alternatives rejected and why' },
    steps: { type: 'array', items: { type: 'string' }, description: 'ordered implementation steps' },
    risks: { type: 'array', items: { type: 'string' } },
  },
  required: ['current_state', 'design', 'steps', 'risks'],
}
const CONTROL_SCHEMA = {
  type: 'object',
  properties: {
    plan_review: { type: 'boolean', description: 'run an adversarial plan review before any code' },
    implement_model: { type: 'string', description: 'sonnet (well-specified/mechanical) or opus (judgment-heavy, contract-preserving cutovers)' },
    split: { type: 'array', items: { type: 'string' }, description: 'sequential implementation sub-stages when the task has >2 distinct concerns; empty for a single implementer' },
    lenses: {
      type: 'array',
      items: {
        type: 'object',
        properties: { key: { type: 'string' }, focus: { type: 'string', description: 'a SPECIFIC hunting ground naming the exact contracts/invariants to attack and instructing the reviewer to execute code in /tmp, not just read the diff' } },
        required: ['key', 'focus'],
      },
      description: '2-4 adversarial review lenses tailored to THIS task',
    },
    test_policy: { type: 'string', description: 'one of: frozen, port, migrate' },
    gate_check: { type: 'string', description: 'a structural existence check the gate should verify first (e.g. a file or symbol that must exist), or empty' },
    fix_rounds: { type: 'integer', description: 'gate fix-loop bound, 1-3' },
    re_review_on_fix: { type: 'boolean', description: 'after must-fix findings are applied, run ONE more lens round on the updated diff' },
    rationale: { type: 'string', description: 'one short paragraph explaining the choices' },
  },
  required: ['plan_review', 'implement_model', 'split', 'lenses', 'test_policy', 'gate_check', 'fix_rounds', 're_review_on_fix', 'rationale'],
}

// ---- stage 1: scout + plan (read-only) ----------------------------------
let plan = null
if (SCOUT) {
  phase('Scout+Plan')
  plan = await agent(
    `${SPEC}\n\nYou are the SCOUT+PLANNER — read-only, modify NOTHING (running probe scripts against /tmp scratch copies is encouraged; the repo tree itself stays untouched). Map the current state relevant to the task (verify every claim in the code; cite file:function), then design the implementation: decisions with rationale, ordered steps precise enough that a separate implementer needs no further judgment for the mechanical parts, risks worth a reviewer's attention.`,
    { model: 'opus', effort: 'high', label: 'scout+plan', schema: PLAN_SCHEMA }
  )
}
if (MODE === 'plan') {
  return { plan }
}

// ---- stage 2: conductor — Opus decides the pipeline shape ---------------
let ctl = null
if (CONDUCT) {
  phase('Conduct')
  ctl = await agent(
    `${SPEC}\n\nYou are the PIPELINE CONDUCTOR — read-only. FIRST read ${REPO}/.claude/workflows/PIPELINE-GUIDANCE.md in full: it is the living, retro-maintained guidance on choosing this pipeline's shape (implementer model, plan review, split, lens design, test policy, re-review, cost envelope) and it supersedes any prior you carry. Then decide the shape; the workflow script executes your decisions within hard caps. Consider the plan below (if present), the task's blast radius, which frozen contracts it borders, and the guidance doc's cost envelope — choose the smallest shape that protects quality.\n\n${plan ? `THE PLAN:\n${JSON.stringify(plan, null, 1)}` : '(no scout ran — decide from the task text and your own quick reading of the repo)'}`,
    { model: 'opus', effort: 'high', label: 'conductor', schema: CONTROL_SCHEMA }
  )
  if (ctl && ctl.rationale) log(`conductor: ${ctl.rationale}`)
}

// ---- resolve effective config: caller args > conductor > defaults -------
const PLAN_REVIEW = A.plan_review !== undefined ? A.plan_review === true : (ctl ? ctl.plan_review === true : false)
const IMPL_MODEL = A.implement_model || (ctl && (ctl.implement_model === 'opus' || ctl.implement_model === 'sonnet') ? ctl.implement_model : 'sonnet')
const FIXER_MODEL = A.fixer_model || 'opus'
const FIX_ROUNDS = Math.min(3, Math.max(1, A.fix_rounds || (ctl && ctl.fix_rounds) || 2))
const RE_REVIEW = A.re_review !== undefined ? A.re_review === true : (ctl ? ctl.re_review_on_fix === true : false)
const GATE_EXTRA = A.gate_check || (ctl && ctl.gate_check) || ''
let TEST_POLICY = TEST_POLICIES[A.test_policy] || A.test_policy || TEST_POLICIES[ctl && ctl.test_policy] || TEST_POLICIES.frozen
if (Array.isArray(A.sanctioned_tests) && A.sanctioned_tests.length > 0) {
  TEST_POLICY += `\nSANCTIONED EXCEPTIONS (the ONLY pre-existing tests allowed to change semantics, each keeping its scenario intent): ${A.sanctioned_tests.join('; ')}. Any other pre-existing test modification is a violation.`
}
const LENSES = (A.lenses && A.lenses.length ? A.lenses : (ctl && ctl.lenses && ctl.lenses.length >= 2 ? ctl.lenses.slice(0, 4) : DEFAULT_LENSES))
const stages = Array.isArray(A.split) && A.split.length > 0 ? A.split : (ctl && Array.isArray(ctl.split) && ctl.split.length > 1 ? ctl.split.slice(0, 4) : [null])
const POLICY_NOTE = `\n\nTEST POLICY: ${TEST_POLICY}`

// ---- stage 3: optional adversarial plan review --------------------------
let planAdjust = ''
if (PLAN_REVIEW && plan) {
  phase('Plan review')
  const pr = await agent(
    `${SPEC}${POLICY_NOTE}\n\nYou are the PLAN REVIEWER — read-only. Attack this plan before any code is written: wrong claims about current code (re-verify against the tree), steps that break a frozen contract, missing failure modes, cheaper designs rejected without cause. Return concrete adjustments or "sound".\n\nPLAN:\n${JSON.stringify(plan, null, 1)}`,
    { model: 'opus', effort: 'high', label: 'plan-review' }
  )
  planAdjust = typeof pr === 'string' ? pr : JSON.stringify(pr)
}

// ---- stage 4: implement (single or split) -------------------------------
const implReports = []
const planText = plan ? `\n\nTHE PLAN (from the scout — follow it; deviations must be justified in your report):\n${JSON.stringify(plan, null, 1)}${planAdjust ? `\n\nPLAN-REVIEW ADJUSTMENTS (supersede the plan where they conflict):\n${planAdjust}` : ''}` : ''
for (let i = 0; i < stages.length; i++) {
  phase('Implement')
  const isFinalStage = i === stages.length - 1
  const stageNote = stages[i]
    ? `\n\nYOU EXECUTE ONLY THIS STAGE (${i + 1}/${stages.length}): ${stages[i]}${i > 0 ? `\n\nPrior stage reports:\n${implReports.join('\n---\n')}` : ''}`
    : ''
  const gateNote = isFinalStage
    ? `run the full gate (${GATE_CMDS}) until green`
    : `run the tests most relevant to your stage plus "uv run ruff check src tests" until green (the FULL gate runs at the final stage and again by an independent gate agent — do not spend time on the whole suite here)`
  const r = await agent(
    `${SPEC}${POLICY_NOTE}${planText}${stageNote}\n\nImplement, ${gateNote}, then "git add -A". Report: what changed (file by file), deviations from the plan with justification, gate result.`,
    { model: IMPL_MODEL, effort: 'high', label: stages[i] ? `implement:${i + 1}` : 'implement', phase: 'Implement' }
  )
  const rt = typeof r === 'string' ? r : JSON.stringify(r)
  if (rt && rt.includes('BLOCKED')) {
    return { aborted: `implement stage ${i + 1} blocked by tool failures`, report: rt, plan, conductor: ctl }
  }
  implReports.push(rt)
}

// ---- stage 5: gate with bounded fix loop --------------------------------
phase('Gate')
const GATE_PROMPT = `${TOOL_FAIL_NOTE}\n\nIn ${REPO}:${GATE_EXTRA ? ` first verify: ${GATE_EXTRA} (if it fails: passed=false).` : ''} Run exactly:\n${GATE_CMDS}\nAlso run "git diff --cached --stat tests/" and check the test policy held: ${TEST_POLICY}\npassed=true only if every command is green AND the policy held. Do not modify files.`
let gate = await agent(GATE_PROMPT, { model: 'haiku', effort: 'low', label: 'gate:initial', schema: GATE })
let fixRounds = 0
while (gate && !gate.passed && fixRounds < FIX_ROUNDS) {
  fixRounds += 1
  await agent(
    `${SPEC}${POLICY_NOTE}\n\nThe staged change fails the gate. Fix the implementation — never weaken an oracle test. Failing output:\n\n${gate.failures || gate.summary}\n\nRe-run the gate until green, then "git add -A".`,
    { model: IMPL_MODEL, effort: 'high', label: `gate-fix:${fixRounds}`, phase: 'Fix' }
  )
  gate = await agent(GATE_PROMPT, { model: 'haiku', effort: 'low', label: `gate:after-fix-${fixRounds}`, schema: GATE })
}

// ---- stage 6: adversarial review lenses (+ optional one re-review) ------
const REVIEW_FAIL_NOTE = 'If the environment rejects your tool calls entirely, return {clean:false, findings:[one entry, must_fix=false, summary "review could not execute — tool failure"]}.'
const runLenses = async (lensArr, roundNote) => {
  const results = await parallel(
    lensArr.map((l) => () =>
      agent(
        `Adversarial review, lens "${l.key}"${roundNote ? ` (${roundNote})` : ''}. ${REVIEW_FAIL_NOTE}\n\n${SPEC}${POLICY_NOTE}\n\nReview the STAGED diff (git diff --cached) with this hunting ground: ${l.focus}\n\nReport ONLY findings that affect correctness or violate a stated contract — no style opinions. Default must_fix=false unless genuine breakage. Refusing-by-name always beats silently-wrong output — flag any silent wrong-output path as must_fix.`,
        { model: 'opus', effort: 'high', label: `review:${l.key}${roundNote ? ':2' : ''}`, phase: 'Review', schema: REVIEW }
      )
    )
  )
  // Tag each finding with the lens that produced it (parallel preserves order),
  // enabling the focused re-review subset and per-lens hit-rate stats.
  const findings = []
  results.forEach((r, i) => {
    if (r && r.findings) r.findings.forEach((f) => findings.push({ ...f, lens: lensArr[i].key }))
  })
  return findings
}

phase('Review')
let allFindings = await runLenses(LENSES, '')
let mustFix = allFindings.filter((f) => f.must_fix)

phase('Fix')
let fixReport = 'no must-fix findings'
let reReviewFindings = 0
if (mustFix.length > 0) {
  log(`${mustFix.length} must-fix finding(s) — applying`)
  fixReport = await agent(
    `${SPEC}${POLICY_NOTE}\n\nApply these confirmed review findings on the staged change. Contracts and test policy win every conflict. Findings:\n\n${mustFix
      .map((f, i) => `${i + 1}. ${f.file}${f.line ? ':' + f.line : ''} — ${f.summary}\n   why: ${f.why_it_breaks}`)
      .join('\n')}\n\nRe-run the full gate (${GATE_CMDS}) until green, then "git add -A". Report changes.`,
    { model: FIXER_MODEL, effort: 'high', label: 'apply-findings' }
  )
  gate = await agent(GATE_PROMPT, { model: 'haiku', effort: 'low', label: 'gate:post-fix', schema: GATE })

  // Re-review triggers when the conductor armed it OR the must-fix volume alone
  // warrants fresh eyes (evidence: the fixer's own diff introduced a must-fix once).
  // Focused subset: only the lenses that produced must-fix findings, plus a
  // dedicated fix-audit lens on the fixer's diff.
  if ((RE_REVIEW || mustFix.length >= 2) && gate && gate.passed) {
    const hitKeys = new Set(mustFix.map((f) => f.lens).filter(Boolean))
    const reLenses = LENSES.filter((l) => hitKeys.has(l.key))
    reLenses.push({
      key: 'fix-audit',
      focus: 'Fresh eyes on the post-fix diff specifically: for each applied fix, is it correct and complete, and did it introduce new breakage anywhere it touched? Diff the fixer\'s changes against the findings it was answering; execute the changed paths in a /tmp scratch project.',
    })
    log(`re-review of the fixed diff (${reLenses.length} lens(es): ${reLenses.map((l) => l.key).join(', ')})`)
    const findings2 = await runLenses(reLenses, 're-review of the post-fix diff — judge whether the fixes are correct and complete, and whether they introduced new breakage')
    const mustFix2 = findings2.filter((f) => f.must_fix)
    reReviewFindings = findings2.length
    allFindings = allFindings.concat(findings2)
    if (mustFix2.length > 0) {
      log(`${mustFix2.length} must-fix from re-review — final fix round`)
      const fix2 = await agent(
        `${SPEC}${POLICY_NOTE}\n\nApply these re-review findings on the staged change (final round — be surgical):\n\n${mustFix2
          .map((f, i) => `${i + 1}. ${f.file}${f.line ? ':' + f.line : ''} — ${f.summary}\n   why: ${f.why_it_breaks}`)
          .join('\n')}\n\nRe-run the full gate (${GATE_CMDS}) until green, then "git add -A". Report changes.`,
        { model: FIXER_MODEL, effort: 'high', label: 'apply-findings:2' }
      )
      fixReport += '\n\n=== re-review fix round ===\n' + (typeof fix2 === 'string' ? fix2 : JSON.stringify(fix2))
      gate = await agent(GATE_PROMPT, { model: 'haiku', effort: 'low', label: 'gate:final', schema: GATE })
      mustFix = mustFix.concat(mustFix2)
    }
  }
}

return {
  plan,
  conductor: ctl,
  plan_review: planAdjust || null,
  implementation: implReports.join('\n\n=== next stage ===\n\n'),
  gate_passed: gate ? gate.passed : false,
  gate_summary: gate ? gate.summary : 'gate agent unavailable',
  fix_rounds: fixRounds,
  review_findings_total: allFindings.length,
  re_review_findings: reReviewFindings,
  must_fix_applied: mustFix.length,
  fix_report: typeof fixReport === 'string' ? fixReport : JSON.stringify(fixReport),
}
