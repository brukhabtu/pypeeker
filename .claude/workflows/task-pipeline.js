export const meta = {
  name: 'task-pipeline',
  description: 'Reusable staged task pipeline: scout+plan → (plan review) → implement → gate → adversarial lenses → fix → final gate',
  whenToUse: 'Per-task execution in pypeeker. args: {task, spec?, mode?, scout?, plan_review?, implement_model?, split?, lenses?, test_policy?, gate_check?, fix_rounds?, fixer_model?}. mode "plan" runs scout+plan only (read-only, safe to run concurrently); mode "full" mutates the working tree — run one mutating pipeline at a time.',
  phases: [
    { title: 'Scout+Plan', detail: 'verified current-state map + design', model: 'opus' },
    { title: 'Plan review', detail: 'optional adversarial attack on the plan', model: 'opus' },
    { title: 'Implement', detail: 'sonnet for well-specified, opus for judgment-heavy' },
    { title: 'Gate', detail: 'pytest + ruff + self-lint, bounded fix loop', model: 'haiku' },
    { title: 'Review', detail: 'parallel adversarial lenses', model: 'opus' },
    { title: 'Fix', detail: 'apply confirmed findings, re-gate' },
  ],
}

const REPO = '/home/user/pypeeker'
const GATE_CMDS = 'cd /home/user/pypeeker && uv run pytest -q; uv run ruff check src tests; uv run pypeeker index src && uv run pypeeker check'
const TOOL_FAIL_NOTE = 'IMPORTANT: if the environment rejects your tool calls (spurious "missing required parameter" on valid calls), retry once; if still rejected, DO NOT loop and DO NOT fabricate — return your final/structured answer truthfully describing the failure. If blocked, say BLOCKED prominently.'

// ---- args with defaults -------------------------------------------------
// Defensive: some callers deliver args as a JSON-encoded string.
const A = typeof args === 'string' ? JSON.parse(args) : (args || {})
if (!A.task) throw new Error('task-pipeline requires args.task')
const MODE = A.mode || 'full'
const SCOUT = A.scout !== false
const PLAN_REVIEW = A.plan_review === true
const IMPL_MODEL = A.implement_model || 'sonnet'
const FIXER_MODEL = A.fixer_model || 'opus'
const FIX_ROUNDS = A.fix_rounds || 2
const TEST_POLICY = A.test_policy ||
  'NO pre-existing test may be modified — additions only; the entire pre-existing suite passing unmodified is part of the proof. New tests must assert behavior (bytes, codes, wording), not existence.'
const LENSES = A.lenses || [
  { key: 'correctness', focus: 'Frozen contracts and behavior: CLI JSON envelopes, refusal codes, TransactionSummary fields, check --fix report shape. Exercise the changed paths YOURSELF in a /tmp scratch project — do not only read the diff. must_fix for any divergence from documented behavior.' },
  { key: 'architecture', focus: 'Layering and idiom: import-boundaries (check never imports refactor; intents only models/query/storage), barrel-only, the registration idioms, no parallel code paths reintroduced, docs updated where the diff makes prose false. must_fix for genuine violations only.' },
  { key: 'tests', focus: 'Test integrity: verify the test policy held (git diff --cached --stat tests/), new tests are discriminating (would fail if the change were wrong — reason about or perform a mutation), no scenario lost, run the full gate yourself. must_fix for weak proofs or policy violations.' },
]
const SPEC = `${TOOL_FAIL_NOTE}\n\nTHE TASK: ${A.task}\n${A.spec ? `\nNORMATIVE REFERENCES (read in full before acting): ${A.spec}\n` : ''}\nRepo: ${REPO} (Python 3.14, uv — run everything via "uv run"). Read CLAUDE.md and the relevant architecture.md sections first. Work in the working tree, NEVER git commit; end every mutating stage with "git add -A".\n\nTEST POLICY: ${TEST_POLICY}\n\nFROZEN unless the task explicitly says otherwise: CLI JSON envelopes and refusal codes, the check --fix report shape, TransactionSummary fields, import-boundaries layering, barrel-only discipline, the self-lint zero-baseline gate.`

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

// ---- stage 1: scout + plan (read-only) ----------------------------------
let plan = null
if (SCOUT) {
  phase('Scout+Plan')
  plan = await agent(
    `${SPEC}\n\nYou are the SCOUT+PLANNER — read-only, modify NOTHING. Map the current state relevant to the task (verify every claim in the code; cite file:function), then design the implementation: decisions with rationale, ordered steps precise enough that a separate implementer needs no further judgment for the mechanical parts, risks worth a reviewer's attention.`,
    { model: 'opus', effort: 'high', label: 'scout+plan', schema: PLAN_SCHEMA }
  )
}
if (MODE === 'plan') {
  return { plan }
}

// ---- stage 2: optional adversarial plan review --------------------------
let planAdjust = ''
if (PLAN_REVIEW && plan) {
  phase('Plan review')
  const pr = await agent(
    `${SPEC}\n\nYou are the PLAN REVIEWER — read-only. Attack this plan before any code is written: wrong claims about current code (re-verify against the tree), steps that break a frozen contract, missing failure modes, cheaper designs rejected without cause. Return concrete adjustments or "sound".\n\nPLAN:\n${JSON.stringify(plan, null, 1)}`,
    { model: 'opus', effort: 'high', label: 'plan-review' }
  )
  planAdjust = typeof pr === 'string' ? pr : JSON.stringify(pr)
}

// ---- stage 3: implement (single or split) -------------------------------
const implReports = []
const planText = plan ? `\n\nTHE PLAN (from the scout — follow it; deviations must be justified in your report):\n${JSON.stringify(plan, null, 1)}${planAdjust ? `\n\nPLAN-REVIEW ADJUSTMENTS (supersede the plan where they conflict):\n${planAdjust}` : ''}` : ''
const stages = Array.isArray(A.split) && A.split.length > 0 ? A.split : [null]
for (let i = 0; i < stages.length; i++) {
  phase('Implement')
  const stageNote = stages[i]
    ? `\n\nYOU EXECUTE ONLY THIS STAGE (${i + 1}/${stages.length}): ${stages[i]}${i > 0 ? `\n\nPrior stage reports:\n${implReports.join('\n---\n')}` : ''}`
    : ''
  const r = await agent(
    `${SPEC}${planText}${stageNote}\n\nImplement, run the full gate (${GATE_CMDS}) until green, then "git add -A". Report: what changed (file by file), deviations from the plan with justification, gate result.`,
    { model: IMPL_MODEL, effort: 'high', label: stages[i] ? `implement:${i + 1}` : 'implement', phase: 'Implement' }
  )
  const rt = typeof r === 'string' ? r : JSON.stringify(r)
  if (rt && rt.includes('BLOCKED')) {
    return { aborted: `implement stage ${i + 1} blocked by tool failures`, report: rt, plan }
  }
  implReports.push(rt)
}

// ---- stage 4: gate with bounded fix loop --------------------------------
phase('Gate')
const GATE_PROMPT = `${TOOL_FAIL_NOTE}\n\nIn ${REPO}:${A.gate_check ? ` first verify: ${A.gate_check} (if it fails: passed=false).` : ''} Run exactly:\n${GATE_CMDS}\nAlso run "git diff --cached --stat tests/" and check the test policy held: ${TEST_POLICY}\npassed=true only if every command is green AND the policy held. Do not modify files.`
let gate = await agent(GATE_PROMPT, { model: 'haiku', effort: 'low', label: 'gate:initial', schema: GATE })
let fixRounds = 0
while (gate && !gate.passed && fixRounds < FIX_ROUNDS) {
  fixRounds += 1
  await agent(
    `${SPEC}\n\nThe staged change fails the gate. Fix the implementation — never weaken an oracle test. Failing output:\n\n${gate.failures || gate.summary}\n\nRe-run the gate until green, then "git add -A".`,
    { model: IMPL_MODEL, effort: 'high', label: `gate-fix:${fixRounds}`, phase: 'Fix' }
  )
  gate = await agent(GATE_PROMPT, { model: 'haiku', effort: 'low', label: `gate:after-fix-${fixRounds}`, schema: GATE })
}

// ---- stage 5: adversarial review lenses ---------------------------------
phase('Review')
const REVIEW_FAIL_NOTE = 'If the environment rejects your tool calls entirely, return {clean:false, findings:[one entry, must_fix=false, summary "review could not execute — tool failure"]}.'
const reviews = await parallel(
  LENSES.map((l) => () =>
    agent(
      `Adversarial review, lens "${l.key}". ${REVIEW_FAIL_NOTE}\n\n${SPEC}\n\nReview the STAGED diff (git diff --cached) with this hunting ground: ${l.focus}\n\nReport ONLY findings that affect correctness or violate a stated contract — no style opinions. Default must_fix=false unless genuine breakage. Refusing-by-name always beats silently-wrong output — flag any silent wrong-output path as must_fix.`,
      { model: 'opus', effort: 'high', label: `review:${l.key}`, schema: REVIEW }
    )
  )
)
const mustFix = reviews.filter(Boolean).flatMap((r) => r.findings || []).filter((f) => f.must_fix)

// ---- stage 6: fix + final gate ------------------------------------------
phase('Fix')
let fixReport = 'no must-fix findings'
if (mustFix.length > 0) {
  log(`${mustFix.length} must-fix finding(s) — applying`)
  fixReport = await agent(
    `${SPEC}\n\nApply these confirmed review findings on the staged change. Contracts and test policy win every conflict. Findings:\n\n${mustFix
      .map((f, i) => `${i + 1}. ${f.file}${f.line ? ':' + f.line : ''} — ${f.summary}\n   why: ${f.why_it_breaks}`)
      .join('\n')}\n\nRe-run the full gate (${GATE_CMDS}) until green, then "git add -A". Report changes.`,
    { model: FIXER_MODEL, effort: 'high', label: 'apply-findings' }
  )
  gate = await agent(GATE_PROMPT, { model: 'haiku', effort: 'low', label: 'gate:final', schema: GATE })
}

return {
  plan,
  plan_review: planAdjust || null,
  implementation: implReports.join('\n\n=== next stage ===\n\n'),
  gate_passed: gate ? gate.passed : false,
  gate_summary: gate ? gate.summary : 'gate agent unavailable',
  fix_rounds: fixRounds,
  review_findings_total: reviews.filter(Boolean).flatMap((r) => r.findings || []).length,
  must_fix_applied: mustFix.length,
  fix_report: typeof fixReport === 'string' ? fixReport : JSON.stringify(fixReport),
}
