# Intelligence v2 Operations

## Runtime flags

Stage 2 is controlled by:

```text
DRJAVAN_INTELLIGENCE_V2=true
DRJAVAN_SOURCE_ROUTER_V2=true
DRJAVAN_HYBRID_RETRIEVAL_V2=true
```

These flags remain **absent/disabled in production during Prompt 1**. Enabling them is a Prompt 2 deployment action. Setting them to `false` restores the legacy archive-only runtime without rolling back code. The production `.env`/EnvironmentFile remains outside Git; change it atomically and keep a pre-change backup for rollback.

## Model policy

Default policy:

- FAST: `deepseek-v4-flash`, thinking disabled;
- REASONING: `deepseek-v4-pro`, strong reasoning configurable;
- deterministic simple QI: zero AI calls;
- deterministic simple archive synthesis: zero AI calls;
- normal supported scientific/current/hybrid synthesis: one logical AI call;
- hybrid alone does not select STRONG;
- one structured/grounding repair is allowed;
- no unbounded semantic retry loops.

Reasoning effort and output ceilings are configurable with the `DRJAVAN_AI_*` Stage 2 variables documented in `.env.example`.

## Provider network behavior

- PubMed uses NCBI E-utilities, process-wide request pacing and one bounded retry for transient network/429/5xx responses; it stores no full copyrighted text.
- Current pages are fetched with bounded size/timeouts and SSRF/private-network rejection.
- Current discovery failure is fail-soft; missing required evidence yields insufficient, not model completion.
- Official evidence must pass authoritative-domain admission.

## Pre-deploy gates

1. verify branch/main divergence and exact SHA;
2. compile;
3. full pytest;
4. 125-case Intelligence v2 lab;
5. frozen full-archive Quality Lab + grounding red-team;
6. live PubMed/current smoke on deployment host;
7. verify raw archive/secrets/runtime DB are absent from Git diff;
8. record production SHA/service/index/disk/rollback release.

## Canonical deployment

Use the repository's root-owned self-updater. Do not copy files into `/opt/drjavanbot/current` manually. The updater fetches exact `origin/main`, requires a fast-forward descendant of the active release, builds/tests a candidate release, creates a staging index, atomically switches `current`, starts the service and records structured status.

## Post-deploy health

Verify:

- `/opt/drjavanbot/current/.deploy_commit` equals final main SHA;
- `drjavanbot.service` active with no restart loop;
- updater path/service healthy;
- archive/index message and FTS counts intact;
- AvalAI authentication succeeds without printing the key;
- source-router flags are enabled;
- local end-to-end smoke: cyst scientific, salary current, e.max archive, e.max hybrid, RCT terse archive, no-evidence sentinel.

## Rollback

Before deployment record the previous healthy release SHA. Roll back via the canonical updater/control path when possible. A rollback candidate must have a matching worktree SHA/deployment marker/virtualenv. Stage 2 has no destructive production data migration; cache/state additions are backward compatible. Immediate architecture-only rollback can also disable the three Intelligence v2 flags and restart the bot.
