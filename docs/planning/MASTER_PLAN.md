# ControlGauge — Master Plan (Continuum)

Consolidates the Sprint 0–16 roadmap in [`docs/SPRINTS.md`](../SPRINTS.md), which remains the
source of truth for each sprint's objective, deliverables, responsibilities, dependencies and
exit criteria. This file does not restate them; it adds phases, the critical path and state.
Live status: [CURRENT_STATUS](CURRENT_STATUS.md). Executable work: [BATCH_JOBS](BATCH_JOBS.md).
Rules: [EXECUTION_RULES](EXECUTION_RULES.md). Decisions: [DECISION_LOG](DECISION_LOG.md).

State legend: **Done** (merged, evidence in repo) · **Pending** (next, detailed) · **Future**
(milestone level only). States as of 2026-11-02.

## Phases and milestones

| Phase | Sprints | Milestone | State |
|---|---|---|---|
| A. Foundation | 0, 1 | Docs set approved; Tauri + FastAPI shape proven incl. packaged build | Sprint 0 **Done**; Sprint 1 built, S1-08 sign-off **Pending** |
| B. Data & framework | 2, 3 | Persistent assessments; real NIST 800-53 Rev. 5 catalog browsable | Sprint 2 **Done**, Sprint 3 **In review** |
| C. Evidence & AI substrate | 4, 5, 6, 7 | Safe ingestion with parser containment; local model runtime; classification | **Future** |
| D. Core analysis | 8, 9, 10 | Evidence→control mapping with provenance; human review workflow | **Future** |
| E. Insight | 11, 12 | Knowledge graph; coverage signals and gap analysis | **Future** |
| F. Assurance & release | 13, 14, 15, 16 | Security hardening; evaluation harness; reporting; V0.1 release | **Future** |

## Sprint index

Pointers only; see `SPRINTS.md` for scope and acceptance criteria. "Detail" is where finer
information lives.

| Sprint | Title | Depends on | State | Detail |
|---|---|---|---|---|
| 0 | Architecture and product definition | — | Done (PRs #2, #3) | `docs/*.md` |
| 1 | Desktop foundation | 0 | Done (S1-08 accepted, PD-009) | `SPRINT_1_BACKLOG.md`, `PACKAGING.md`, D-001, D-009, D-018, D-025, D-026 |
| 2 | Assessment and database layer | 1 | Done (PR #17) | `DATABASE.md` §7, `BATCH_JOBS.md` S2-* |
| 3 | NIST 800-53 framework engine | 2 | Implemented on `claude/sprint-3-framework-engine`, PR in review (S3-01..S3-06) | `DATABASE.md` §8, `BATCH_JOBS.md` S3-*, PD-012/013/015 |
| 4 | Evidence ingestion + parser containment | 2 | Future | `SECURITY.md`, D-010, D-022, OQ S-1, S-2 |
| 5 | Local model runtime | 1 | Future | `MODEL_RUNTIME.md`, D-011, D-017, D-023 |
| 6 | Hardware detection + model recommendations | 5 | Future | `MODEL_RUNTIME.md`, OQ P-3, S-4 |
| 7 | Artifact classification | 4, 5 | Future | `AI_PIPELINE.md` §4, OQ AI-2 |
| 8 | Evidence-to-control mapping | 3, 4, 5, 7 | Future | `AI_PIPELINE.md`, D-012..D-014, D-019..D-025 |
| 9 | Policy-to-control mapping | 8 | Future | D-004, OQ C-1 |
| 10 | Analyst review system | 8 | Future | `USER_FLOWS.md` §8, D-005, D-024, OQ U-3, U-4 |
| 11 | Compliance knowledge graph | 10 | Future | `ARCHITECTURE.md` §10, D-002, OQ U-5 |
| 12 | Gap analysis | 3, 10 | Future | `COMPLIANCE_MODEL.md`, D-006, D-019, D-021, OQ P-1, C-2 |
| 13 | Security hardening | 4, 5, 8 | Future | `SECURITY.md` §6, OQ S-3, S-5, U-6 |
| 14 | AI evaluation harness | 8 | Future | OQ AI-3 |
| 15 | Reporting | 10, 12 | Future | `PRODUCT.md` §8 |
| 16 | V0.1 polish and release | all | Future | `PACKAGING.md` follow-ups, OQ A-3 |

## Critical path

`S1-08 sign-off → 2 (DB) → 3 (framework) + 4 (ingestion, containment) → 8 (mapping, also needs 5, 7) → 10 (review) → 12 (gaps) → 15 (reports) → 16 (release)`.

- Sprint 5 depends only on Sprint 1, so it can run in parallel with 2–4 if the owner wants to
  retire model-runtime risk early. `SPRINTS.md` advises against two agents editing the same
  subsystem concurrently.
- Sprint 8 is the largest integration point and the highest-risk sprint: it gates 9–12 and 14.
- Sprint 4's parser containment (D-010, D-022) is a hard requirement of that sprint, not
  deferrable to Sprint 13.

## Inherited constraints (apply to every phase)

Privacy/local-first, evidence-as-data, no automatic cloud fallback, AI never determines
compliance, provenance on every mapping, no fabricated or product-authored "official" NIST text,
framework logic not NIST-hardcoded (`CLAUDE.md`, `SECURITY.md`, `ARCHITECTURE.md` §8).

## Unresolved decisions that gate work

Source: `docs/OPEN_QUESTIONS.md` (unchanged by Continuum).

| Gate | Needed before | Open items |
|---|---|---|
| Sprint 1 sign-off | Sprint 2 | S1-08 |
| Storage location | Sprint 2 persistence design finalized | A-2 — DB location answered (PD-006); configurable path and model location open |
| Baseline scope | Sprint 12 (deferred from Sprint 2 by PD-006) | P-1, P-2, C-2 |
| NIST source + licensing | Sprint 3 | C-3 |
| Limits + containment mechanism | Sprint 4 | S-1, S-2 |
| Model catalog + sources | Sprints 5–6 | P-3, S-4 |
| Classification method | Sprint 7 | AI-2 |
| Retrieval tuning, eval method | Sprints 8, 14 | AI-1, AI-3 |
| Review UX | Sprint 10 | U-3, U-4 |
| Graph library/targets | Sprint 11 | U-5 |
| Encryption, retention, SCA | Sprint 13 | S-3, S-5, U-6 |
| Final packaging approach | Sprint 16 | A-3 |

## Milestones already reached

- 2026-09-14: Sprint 0 documentation merged.
- 2026-09-15..10-06: S1-01 to S1-07 merged.
- 2026-10-07: S1-09 packaged Windows build merged; clean-machine manual test reported passed.
- 2026-10-22: EvidenceGraph → ControlGauge rebrand merged (PRs #12, #13).
