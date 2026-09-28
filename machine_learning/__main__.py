"""Train every model and write its predictions to PostgreSQL.

    python -m machine_learning                 # all models
    python -m machine_learning churn forecast  # a subset

Run after the data pipeline: reloading core data invalidates previous predictions.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from .anomaly_detection import detect
from .churn import model as churn
from .forecasting import forecast
from .segmentation import rfm

MODULES = {"segmentation": rfm.run, "forecast": forecast.run, "churn": churn.run, "anomalies": detect.run}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NEXUS BI model training")
    parser.add_argument("models", nargs="*", metavar="model",
                        help=f"models to train: {', '.join(MODULES)} (default: all)")
    args = parser.parse_args(argv)
    unknown = set(args.models) - set(MODULES)
    if unknown:
        parser.error(f"unknown model(s): {', '.join(sorted(unknown))}")

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stdout)
    for name in args.models or MODULES:
        started = time.perf_counter()
        MODULES[name]()
        logging.info("%s finished in %.1fs", name, time.perf_counter() - started)
    return 0


if __name__ == "__main__":
    sys.exit(main())
