# ODIS ingestion and build pipeline

This directory contains the offline ETL pipeline that produces the Parquet
release consumed by the ODIS application. The pipeline has three important
boundaries:

- active source adapters fetch and normalize Odace data and the explicitly
  supported live/external sources;
- each execution builds an isolated candidate under
  `pipeline/cache/runs/<run_id>/`;
- deployment is a separate, explicit operation that can publish only a
  candidate that passed its contracts and quality gate.

For the design and data-flow details, see
[PIPELINE_ARCHITECTURE.md](PIPELINE_ARCHITECTURE.md).

## Quick start

Run commands from the repository root.

### Configure credentials

Create `pipeline/.env` with the credentials required by the providers used in
the run. At minimum, Odace credentials are needed for Odace-backed sources:

```env
ODACE_API_URL=https://odace.services.d4g.fr
ODACE_API_KEY=sk_live_...
```

Live France Travail and Les emplois de l'inclusion refreshes require their own
configured credentials. A deployment also requires access to the configured
GCS bucket.

### Build a candidate

```bash
# Ingest, build and prescore. This does not deploy.
uv run python -m pipeline.etl --step all
```

The command prints the generated `run_id`, for example
`run-20260731T120000Z-ab12cd34`. Inspect that candidate before deployment:

```text
pipeline/cache/runs/<run_id>/
├── run.json
├── clean/
├── quality_report.json
└── output/
    ├── data_manifest.json
    └── <release Parquet files>
```

The candidate's `run.json` records its state, step results and source outcomes.
The quality report contains the contract version and every quality check. A
failed candidate remains available for diagnosis but cannot be deployed.

### Run one phase or selected tables

```bash
# Start a new candidate and run one phase.
uv run python -m pipeline.etl --step ingest
uv run python -m pipeline.etl --step build
uv run python -m pipeline.etl --step prescoring

# Select tables/steps where supported by the phase.
uv run python -m pipeline.etl --step all --table communes
uv run python -m pipeline.etl --step ingest --table population,caf
```

To continue an existing candidate, pass its `run_id`. Only a candidate still
in `RUNNING` state may be continued; a `FAILED` candidate must be replaced by a
new run.

```bash
uv run python -m pipeline.etl \
  --step prescoring \
  --run-id run-20260731T120000Z-ab12cd34
```

Optional live jobs can be skipped explicitly:

```bash
uv run python -m pipeline.etl \
  --step all \
  --skip-live-jobs \
  --skip-inclusion-jobs
```

### Deploy a passed candidate

Deployment requires the explicit `run_id` of a candidate whose run record,
manifest and quality gate all agree and whose state is `PASSED`:

```bash
uv run python -m pipeline.etl \
  --step deploy \
  --run-id run-20260731T120000Z-ab12cd34
```

Deployment validates the complete release file set, uploads the candidate to
an immutable GCS release, and advances `datasets/current.json` only after the
release upload succeeds. The local application mirror is updated afterward.
Do not deploy from the historical shared directory
`pipeline/cache/output`.

## Execution phases

- **`ingest`**: fetches active provider data, stages raw inputs, validates the
  applicable source contract, and writes candidate-scoped cleaned artifacts.
- **`build`**: joins cleaned datasets, builds commune and vertical artifacts,
  resolves geography, consolidates Paris/Lyon/Marseille (PLM), and writes the
  candidate output tables.
- **`prescoring`**: computes configured pre-calculated indicators and scaled
  values, then runs the release quality gate and generates the manifest.
- **`deploy`**: publishes only a passed candidate and advances the active
  release pointer.

The `all` step runs ingest, build and prescoring. It intentionally does not
run deployment.

## Active source boundary

The active catalog is in [sources.yaml](sources.yaml). It currently combines:

- **Odace** for the normalized datasets declared with `use_odace: true` and an
  `odace_table`;
- **live providers** such as Salesforce J'Accueille, France Travail and Les
  emplois de l'inclusion;
- **external reference/open-data inputs** that remain required for the active
  build, such as INSEE, education, electoral, postal-code and referential
  files;
- **BigQuery RNA RAG** where the configured association enrichment requires
  it.

For Odace-backed sources, a request failure may reuse an existing Odace cache,
  but the pipeline never silently revives the retired manual download
  implementation. If no readable Odace artifact is available, the required
  clean step fails. The retired direct-download cleaners and the former
  CSV/XLSX and BigQuery J'Accueille paths are preserved only in
  [legacy_ingest/](legacy_ingest/); they are not imported by the default ETL.

Salesforce is the single active source for J'Accueille. Its published BDV
artifact supplies both accueillant and prospect aggregates used by scoring and
the result details UI; the old runtime BigQuery queries and local manual
exports are not part of the active path.

## Contracts and quality gate

Two contract layers protect a candidate:

1. Source-level schemas are declared in `sources.yaml`. Required columns and
   basic dataset validity are checked before a cleaner is promoted.
2. The versioned release contract is in
   [data_contracts.yaml](data_contracts.yaml). The quality gate in
   [quality_gate.py](quality_gate.py) checks the candidate's minimum row count,
   required commune columns, unique/non-null `codgeo`, geographic coverage,
   configured precomputed score columns, required release artifacts and the
   communes-to-BdV join orphan rate.

The score checks are derived from [app/scores_config.yaml](../app/scores_config.yaml),
so adding a precomputed score creates a corresponding release check rather than
requiring a second hard-coded list. The gate is non-mutating: it returns a
report or raises a contract failure. The ETL records that report at
`<run>/quality_report.json` and marks the candidate `PASSED` only after the
manifest has also been generated.

## Candidate states and source outcomes

Candidate state transitions are recorded in `<run>/run.json`:

- `RUNNING`: candidate execution is in progress or ready to continue;
- `PASSED`: required steps, manifest generation and quality gate succeeded;
- `FAILED`: a required step, contract, quality gate or manifest failed.

Source outcomes are deliberately explicit: `refreshed`,
`reused_within_ttl`, `fallback_last_good`, `skipped_optional` or `failed`.
Reusing a valid cache is different from successfully refreshing a source. A
last-known-good Odace cache is distinct from an archived manual replacement,
and a failed required cleaner is never treated as a successful candidate step.

## PLM aggregation policy

The PLM rule is implemented as an explicit metric contract in
[build.py](build.py), following the same declarative-policy approach as
`missing_strategy` in the score catalog:

- an existing parent value is authoritative, including a legitimate zero;
- additive metrics fall back to a complete sum of arrondissement children;
- rate/mean metrics use a population-weighted child mean;
- parent-only metrics are never inferred from children;
- boolean/flag metrics use their declared maximum rule;
- incomplete child families and unclassified numeric metrics fail loudly.

After consolidation, arrondissement rows are removed from the commune release.
The vertical and detail-list helpers also avoid adding a duplicate parent when
one already exists.

## Operational notes

- A new candidate uses isolated `clean/`, `output/` and `run.json` paths. The
  provider raw cache remains a last-known-good cache and is never itself the
  release source of truth.
- Odace downloads are staged and readability-checked before the cache is
  atomically replaced.
- A failed candidate never advances the active GCS pointer and does not update
  the application release mirror.
- `pipeline/cache/runs/` is intentionally inspectable. Retain or remove old
  candidates according to the project's operational retention policy; never
  copy loose artifacts into a release manually.
