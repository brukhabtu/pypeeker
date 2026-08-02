---
id: TASK-147
title: 'envelope: cut pypeeker CLI output over to the envelope'
status: To Do
assignee: []
created_date: '2026-08-02 16:55'
labels: []
dependencies:
  - TASK-144
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
DEFERRED PENDING APPROVAL: to be started only once the envelope approach is validated by the replay harness. Replaces pypeeker JSON emission with the envelope, always on and with no compatibility mode, since nothing consumes pypeeker output externally yet. Requires first collapsing the 17 scattered click.echo(json.dumps(...)) sites in cli.py into a single emit choke point. This is a migrate test-policy task, not frozen: many of the 2047 tests assert on top-level keys such as output[violations] and will need deliberate rewriting.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A single emit choke point in cli.py replaces the scattered json.dumps call sites
- [ ] #2 pypeeker CLI output is enveloped by default with no compatibility mode
- [ ] #3 The index command no longer emits its unused skipped-files array as bulk payload
- [ ] #4 Test migration is deliberate and enumerated in advance rather than discovered mid-run
- [ ] #5 Full gate green
<!-- AC:END -->
