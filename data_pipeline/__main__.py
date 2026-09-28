"""Command-line entry point.

    python -m data_pipeline              # full run: download if needed, clean, load
    python -m data_pipeline --dry-run    # everything except writing to PostgreSQL
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from . import cleaning, ingestion, load, transformation, validation

log = logging.getLogger("data_pipeline")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NEXUS BI data pipeline")
    parser.add_argument("--dry-run", action="store_true", help="validate and transform without loading")
    parser.add_argument("--no-download", action="store_true", help="fail instead of downloading missing files")
    args = parser.parse_args(argv)

    # The Windows console defaults to cp1252; the data contains Portuguese text.
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stdout)
    started = time.perf_counter()
    report = validation.QualityReport()

    log.info("[1/5] Ingestion")
    frames = ingestion.read_raw(download=not args.no_download)
    report.rows_read = {name: len(df) for name, df in frames.items()}

    log.info("[2/5] Validation")
    validation.check_required_columns(frames)

    log.info("[3/5] Cleaning")
    cleaned = cleaning.clean_all(frames, report)

    log.info("[4/5] Transformation")
    model = transformation.build_model(cleaned, report)
    problems = validation.check_referential_integrity(model)
    if problems:
        raise RuntimeError("Referential integrity check failed: " + "; ".join(problems))

    if args.dry_run:
        report.rows_loaded = {name: len(df) for name, df in model.items()}
        print(report.render())
        log.info("Dry run finished in %.1fs (nothing written to the database)", time.perf_counter() - started)
        return 0

    log.info("[5/5] Load into PostgreSQL")
    with load.connect(autocommit=True) as audit, load.connect() as conn:
        run_id = load.start_run(audit, ingestion.SOURCE_URL, sum(report.rows_read.values()))
        try:
            report.rows_loaded = load.load_model(conn, model)
        except Exception as exc:
            load.finish_run(audit, run_id, "failed", error=str(exc)[:2000])
            load.save_issues(audit, run_id, report)
            raise
        load.save_issues(audit, run_id, report)
        load.finish_run(audit, run_id, "success", rows_loaded=sum(report.rows_loaded.values()))
        refreshed = load.refresh_analytics(conn)
        log.info("Refreshed materialized views: %s", ", ".join(refreshed) or "none (run sql/views.sql)")

    print(report.render())
    log.info("Pipeline run %d finished in %.1fs", run_id, time.perf_counter() - started)
    return 0


if __name__ == "__main__":
    sys.exit(main())
