import argparse
import json
import logging
import re
import os
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add app directory to sys.path to allow imports from app/
sys.path.append(os.path.join(os.getcwd(), "app"))

from pipeline import ingest, build, prescoring
from google.cloud import storage
from pipeline.common import CACHE_DIR, CONFIG_FILE, is_cache_valid, load_config
from pipeline.run_context import PipelineRun, PipelineRunError, bind_run_paths

SOURCE_DIR = Path("pipeline/cache/output")

DATASET_FILES = [
    "odis_referentiels.parquet",
    "odis_communes.parquet",
    "odis_bassins_de_vie.parquet",
    "odis_pois.parquet",
    "odis_associations_agg.parquet",
    "odis_associations_thematiques_agg.parquet",
    "odis_associations_tags_bdv.parquet",
    "odis_association_tags_ref.parquet",
    "odis_formations_agg.parquet",
    "odis_ccas.parquet",
    "odis_refugee_associations.parquet",
    "odis_ft_jobs_agg.parquet",
    "odis_inclusion_jobs.parquet",
    "salesforce_jaccueille_bdv.parquet",
]
RELEASE_MANIFEST_FILE = "data_manifest.json"


def _get_release_version(source_dir: Path) -> str:
    """Read and validate the manifest version used as the GCS release ID."""
    manifest_path = source_dir / "data_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest source file not found: {manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON manifest: {manifest_path}") from exc

    version = manifest.get("manifest_version")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", version):
        raise ValueError(
            "Manifest must contain a safe, non-empty 'manifest_version' value"
        )
    return version


def _publish_datasets_to_gcs(
    source_dir: Path, bucket_name: str, *, release_version: str | None = None
) -> str:
    """Publish one immutable dataset release, then atomically advance its pointer."""
    release_version = release_version or _get_release_version(source_dir)
    missing_files = [
        filename for filename in DATASET_FILES if not (source_dir / filename).exists()
    ]
    if missing_files:
        raise FileNotFoundError(
            "Cannot publish an incomplete dataset release; missing files: "
            + ", ".join(missing_files)
        )

    datasets_prefix = os.getenv("GCS_DATASETS_PREFIX", "datasets").strip("/")
    release_prefix = f"{datasets_prefix}/releases/{release_version}"
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    logging.info(
        "Uploading dataset release '%s' to gs://%s/%s/",
        release_version,
        bucket_name,
        release_prefix,
    )
    from pipeline.manifest import artifact_metadata

    release_artifacts = list(DATASET_FILES) + [RELEASE_MANIFEST_FILE]
    for filename in release_artifacts:
        blob_path = f"{release_prefix}/{filename}"
        bucket.blob(blob_path).upload_from_filename(str(source_dir / filename))
        logging.info("Uploaded %s -> gs://%s/%s", filename, bucket_name, blob_path)

    # Optional cartographic geojson upload
    geojson_candidate = source_dir / "communes_france.geojson"
    if not geojson_candidate.exists():
        static_candidate = Path("app/static/data/communes_france.geojson")
        if static_candidate.exists():
            geojson_candidate = static_candidate

    release_files = list(DATASET_FILES)
    if geojson_candidate.exists():
        blob_path = f"{release_prefix}/communes_france.geojson"
        bucket.blob(blob_path).upload_from_filename(str(geojson_candidate))
        logging.info(
            "Uploaded communes_france.geojson -> gs://%s/%s", bucket_name, blob_path
        )
        release_files.append("communes_france.geojson")

    manifest_metadata = artifact_metadata(
        source_dir / RELEASE_MANIFEST_FILE, name=RELEASE_MANIFEST_FILE
    ).model_dump()

    # Read existing current.json to capture previous_version for graceful rollback
    pointer_path = f"{datasets_prefix}/current.json"
    pointer_blob = bucket.blob(pointer_path)
    previous_version = None
    try:
        if pointer_blob.exists():
            old_pointer_bytes = pointer_blob.download_as_bytes()
            if old_pointer_bytes:
                old_pointer = json.loads(old_pointer_bytes)
                old_version = old_pointer.get("version")
                if old_version and old_version != release_version:
                    previous_version = str(old_version)
    except Exception as exc:
        logging.warning("Could not read previous pointer version from GCS: %s", exc)

    pointer = {
        "version": release_version,
        "previous_version": previous_version,
        "files": release_files,
        "manifest": manifest_metadata,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    pointer_blob.upload_from_string(
        json.dumps(pointer, ensure_ascii=False, indent=2),
        content_type="application/json",
    )
    logging.info(
        "Activated dataset release '%s' with pointer gs://%s/%s",
        release_version,
        bucket_name,
        pointer_path,
    )
    return release_version


def _assert_deployable_candidate(source_dir: Path, run: PipelineRun) -> None:
    """Reject loose or stale artefacts before they can reach the release pointer."""
    run.assert_deployable()
    manifest_path = source_dir / "data_manifest.json"
    if not manifest_path.exists():
        raise PipelineRunError(f"Candidate manifest is missing: {manifest_path}")
    from pipeline.manifest import validate_manifest_for_deployment

    try:
        validate_manifest_for_deployment(
            manifest_path, run_id=run.run_id, required_artifacts=DATASET_FILES
        )
    except ValueError as exc:
        raise PipelineRunError(str(exc)) from exc


def _uses_odace(config: dict[str, Any], source_name: str) -> bool:
    """Return whether a pipeline source is configured to ingest from ODAS.

    Args:
        config: Parsed pipeline sources configuration from ``sources.yaml``.
        source_name: Source key under ``sources`` or ``local_files``.

    Returns:
        True when the source enables ``use_odace``; otherwise False.
    """
    source_cfg = config.get("sources", {}).get(source_name) or config.get(
        "local_files", {}
    ).get(source_name, {})
    return isinstance(source_cfg, dict) and bool(source_cfg.get("use_odace", False))


def _resolve_jobs_skip_flag(
    config: dict[str, Any],
    *,
    source_name: str,
    already_skipped: bool,
    status_fn: Callable[[], dict[str, Any]],
    label: str,
    aggregate_name: str,
    prompt_text: str,
    skip_label: str,
    run_message: str,
) -> bool:
    """Resolve whether an employment source should be skipped before ingestion.

    When ``use_odace`` is enabled, ingestion always runs non-interactively
    against ODAS unless explicitly skipped via CLI flags. Legacy interactive
    refresh prompts are preserved only when ``use_odace`` is disabled.

    Args:
        config: Parsed pipeline sources configuration.
        source_name: Source key in ``sources.yaml``.
        already_skipped: CLI skip flag value.
        status_fn: Callable returning cache freshness metadata for the source.
        label: Human-readable source label for status messages.
        aggregate_name: Human-readable aggregate label when coverage is missing.
        prompt_text: Interactive confirmation prompt shown in legacy API mode.
        skip_label: Label printed when the operator declines refresh.
        run_message: Confirmation message printed when refresh is accepted.

    Returns:
        Updated skip boolean for the source.
    """
    if already_skipped or _uses_odace(config, source_name):
        return already_skipped

    status = status_fn()
    if status["within_ttl"]:
        return False

    print("\n" + "=" * 50)
    if not status["exists"]:
        print(f"[?] {label} data is MISSING.")
    elif not status.get("coverage_exists", False):
        print(
            f"[!] {aggregate_name} exists, but its coverage evidence is missing; "
            "a refresh is required."
        )
    else:
        print(
            f"[?] {label} data is {status['age_days']:.1f} days old "
            f"(TTL={status['ttl_days']})."
        )

    choice = input(prompt_text).lower().strip()
    if choice != "y":
        print(f"    >> Skipping {skip_label}.")
        print("=" * 50 + "\n")
        return True

    print(f"    >> {run_message}")
    print("=" * 50 + "\n")
    return False


def main():
    parser = argparse.ArgumentParser(description="ODIS Data Pipeline ETL")
    parser.add_argument(
        "--step",
        choices=["ingest", "build", "prescoring", "deploy", "all"],
        default="all",
        help="Step to run",
    )
    parser.add_argument(
        "--run-id",
        help="Existing run to continue or deploy. A new ID is created when omitted.",
    )
    parser.add_argument(
        "--table",
        "--tables",
        "--steps",
        dest="steps",
        type=str,
        help="Specific table(s) or step(s) to process (comma-separated, e.g. communes,population)",
    )
    parser.add_argument(
        "--skip-live-jobs",
        action="store_true",
        help="Skip France Travail Live Jobs fetch",
    )
    parser.add_argument(
        "--skip-inclusion-jobs", action="store_true", help="Skip Inclusion Jobs fetch"
    )
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Publish the passed candidate after --step all completes",
    )
    args = parser.parse_args()

    if args.step == "deploy" and not args.run_id:
        parser.error("--step deploy requires --run-id for a previously passed run")
    if args.step == "prescoring" and not args.run_id:
        parser.error(
            "--step prescoring requires --run-id for a candidate produced by build; "
            "use --step all to run the full pipeline"
        )
    if args.deploy and args.step != "all":
        parser.error("--deploy can only be used with --step all")

    if args.run_id:
        run = PipelineRun.from_id(args.run_id)
        if args.step != "deploy" and not run.directory.exists():
            raise PipelineRunError(f"Run does not exist: {run.run_id}")
        if args.step != "deploy" and run.directory.exists():
            previous_state = run.read_state().get("state")
            if previous_state != "RUNNING":
                raise PipelineRunError(
                    f"Run {run.run_id} cannot be reused (state={previous_state!r})"
                )
    else:
        run = PipelineRun.create()
    bind_run_paths(run)
    source_dir = run.output_dir
    logging.info("Pipeline candidate run: %s", run.run_id)
    print(f"Pipeline candidate run: {run.run_id}")

    skip_live_jobs = args.skip_live_jobs
    skip_inclusion_jobs = args.skip_inclusion_jobs

    publication_started = False
    try:
        if args.step in ["ingest", "all"]:
            config = load_config(CONFIG_FILE)
            from pipeline.ingest import (
                get_inclusion_jobs_status,
                get_live_jobs_status,
            )

            skip_live_jobs = _resolve_jobs_skip_flag(
                config,
                source_name="france_travail_live",
                already_skipped=skip_live_jobs,
                status_fn=get_live_jobs_status,
                label="France Travail Live Jobs",
                aggregate_name="France Travail aggregate",
                prompt_text="    Do you want to refresh the metadata? (y/N): ",
                skip_label="Live Jobs fetch",
                run_message="Live Jobs fetch will run during ingestion.",
            )
            skip_inclusion_jobs = _resolve_jobs_skip_flag(
                config,
                source_name="inclusion_jobs",
                already_skipped=skip_inclusion_jobs,
                status_fn=get_inclusion_jobs_status,
                label="Inclusion Jobs",
                aggregate_name="Inclusion Jobs aggregate",
                prompt_text="    Do you want to refresh the Inclusion Jobs? (y/N): ",
                skip_label="Inclusion Jobs fetch",
                run_message="Inclusion Jobs fetch will run during ingestion using credentials from .env.",
            )

            # Print reminders for non-datagouv sources that have expired caches
            try:
                expired_reminders = []
                for name, source_cfg in config["sources"].items():
                    if not source_cfg.get("datagouv_resource_id"):
                        local_name = source_cfg.get("local_name")
                        if local_name:
                            local_path = CACHE_DIR / local_name
                            if local_path.exists() and not is_cache_valid(
                                name, source_cfg
                            ):
                                mtime = datetime.fromtimestamp(
                                    local_path.stat().st_mtime
                                )
                                age_days = (datetime.now() - mtime).days
                                ttl = source_cfg["ttl_days"]
                                expired_reminders.append(
                                    f"  - {name}: age={age_days} days (TTL={ttl} days)"
                                )
                if expired_reminders:
                    print("\n" + "🔔" * 15 + " CACHE EXPIRATION REMINDERS " + "🔔" * 15)
                    for reminder in expired_reminders:
                        print(reminder)
                    print(
                        "Please check manually if new versions of these datasets are available."
                    )
                    print("🔔" * 58 + "\n")
            except Exception as e:
                logging.debug(f"Failed to compile early reminders: {e}")

            logging.info("=== Starting Ingestion Phase ===")
            ingest_args = []
            if skip_live_jobs:
                ingest_args.append("--skip-live-jobs")
            if skip_inclusion_jobs:
                ingest_args.append("--skip-inclusion-jobs")
            if args.steps:
                ingest_args.extend(["--steps", args.steps])
            ingest.main(ingest_args)
            logging.info("=== Ingestion Phase Completed ===")

        if args.step in ["build", "all"]:
            logging.info("=== Starting Build Phase ===")
            build_args = []
            if args.steps:
                build_args.extend(["--steps", args.steps])
            build.main(build_args)
            logging.info("=== Build Phase Completed ===")

        if args.step in ["prescoring", "all"]:
            logging.info("=== Starting Prescoring Phase ===")
            quality_summary = prescoring.main([])
            (run.directory / "quality_report.json").write_text(
                json.dumps(quality_summary, indent=2), encoding="utf-8"
            )
            logging.info("=== Prescoring Phase Completed ===")

            logging.info("=== Generating Data Manifest ===")
            try:
                from pipeline.manifest import generate_manifest
                from pipeline.odace_client import get_odace_client

                odace_client = None
                try:
                    odace_client = get_odace_client()
                except Exception as e:
                    logging.warning(
                        f"Could not initialize OdaceClient for manifest generation: {e}"
                    )

                generate_manifest(
                    output_path=source_dir / "data_manifest.json",
                    odace_client=odace_client,
                    run_id=run.run_id,
                    quality_gate=quality_summary,
                    release_artifacts=DATASET_FILES,
                    quality_report_path=run.directory / "quality_report.json",
                )
                run.update_state("PASSED", quality_gate=quality_summary)
                logging.info("=== Data Manifest Generation Completed ===")
                if args.step == "all" and not args.deploy:
                    command = (
                        "uv run python -m pipeline.etl --step deploy "
                        f"--run-id {run.run_id}"
                    )
                    logging.info("Candidate passed. Deploy it with: %s", command)
                    print(f"Candidate passed. Deploy it with:\n{command}")
            except Exception as e:
                logging.error(f"Failed to generate Data Manifest: {e}", exc_info=True)
                raise
        if args.step == "deploy" or args.deploy:
            logging.info("=== Starting Deployment Phase ===")
            publication_started = True
            _assert_deployable_candidate(source_dir, run)
            missing_datasets = [
                f for f in DATASET_FILES if not (source_dir / f).exists()
            ]
            if missing_datasets:
                raise FileNotFoundError(
                    "Deployment source is incomplete; missing files: "
                    + ", ".join(missing_datasets)
                )

            # Publish only the validated candidate before updating the local
            # development mirror.  A failed candidate never changes the
            # runtime pointer.
            bucket_name = os.getenv("GCS_DATASETS_BUCKET")
            if not bucket_name:
                raise RuntimeError("GCS_DATASETS_BUCKET must be configured for deploy")
            _publish_datasets_to_gcs(
                source_dir, bucket_name, release_version=run.run_id
            )

            logging.info("=== Deployment Phase Completed ===")
    except Exception as exc:
        from pipeline.quality_gate import QualityGateFailureError

        if isinstance(exc, QualityGateFailureError) and exc.summary is not None:
            (run.directory / "quality_report.json").write_text(
                json.dumps(exc.summary, indent=2), encoding="utf-8"
            )
        if publication_started:
            # A GCS/local-mirror failure does not invalidate a candidate that
            # already passed its pipeline checks; retain it for a safe retry.
            run.update_state("PASSED", deployment_error=str(exc))
        else:
            run.update_state("FAILED", error=str(exc))
        raise


if __name__ == "__main__":
    main()
