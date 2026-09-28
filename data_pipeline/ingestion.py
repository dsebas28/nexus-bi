"""Raw data ingestion: download the Olist dataset (if needed) and read it.

Every column is read as text. Type conversion happens during cleaning so that
malformed values can be counted and reported instead of being silently coerced.
"""
from __future__ import annotations

import logging
import urllib.request
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

SOURCE_URL = "https://raw.githubusercontent.com/olist/work-at-olist-data/master/datasets"
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# Logical name used across the pipeline -> file name in the Olist repository.
RAW_FILES: dict[str, str] = {
    "customers": "olist_customers_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "payments": "olist_order_payments_dataset.csv",
    "reviews": "olist_order_reviews_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}


def download_raw_files(raw_dir: Path = RAW_DIR, timeout: int = 120) -> None:
    """Download any raw file that is not already present in ``raw_dir``."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    for file_name in RAW_FILES.values():
        target = raw_dir / file_name
        if target.exists() and target.stat().st_size > 0:
            continue
        url = f"{SOURCE_URL}/{file_name}"
        log.info("Downloading %s", url)
        partial = target.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=timeout) as response, open(partial, "wb") as out:
            out.write(response.read())
        partial.replace(target)  # atomic: never leave a half-written CSV behind


def read_raw(raw_dir: Path = RAW_DIR, download: bool = True) -> dict[str, pd.DataFrame]:
    """Return every raw table as a DataFrame of strings, keyed by logical name."""
    if download:
        download_raw_files(raw_dir)

    missing = [f for f in RAW_FILES.values() if not (raw_dir / f).exists()]
    if missing:
        raise FileNotFoundError(f"Raw files not found in {raw_dir}: {', '.join(missing)}")

    frames = {}
    for name, file_name in RAW_FILES.items():
        frames[name] = pd.read_csv(raw_dir / file_name, dtype=str, keep_default_na=True, encoding="utf-8")
        log.info("Read %-22s %9s rows", name, f"{len(frames[name]):,}")
    return frames
