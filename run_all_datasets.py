"""
Batch orchestrator — runs the full pipeline on the first chronological file
from each specified dataset, uploading results to the shared ODP project dataset.

Usage:
    python run_all_datasets.py
    python run_all_datasets.py --dry-run     # print plan without downloading
    python run_all_datasets.py --skip-upload # local outputs only

Progress and errors are logged to outputs/batch_run.log.
Failed datasets are skipped and listed at the end — rerun to retry them.

Datasets to process (confirmed UUIDs, November 2026):
  Antarctic Provider 2022  f77ce947-4154-4931-b9d6-981152a7d3f7
  Antarctic Sea 2016       a6cfbb08-68e4-49b9-add6-5a463421f0d0
  Endurance 2021           d5c8fa9e-a8e7-4fae-8665-1f8965c89691
  Saga Sea 2011            fc74152d-b256-4ce0-81d4-e1f87e88c4af
  Saga Sea 2025            3d1d97bc-7698-47f0-89e1-cac0ab9656b1
  Saga Sea LNSK            058bd885-5788-4bed-94ee-9efb47081bad
  Sagasea Kalibrering      7e8690be-783b-41c9-97cf-bf30067d7bf0
  Tracklines & Metadata    a954a551-4be9-4059-a85a-b3ef8f75e75f  (0 files, skipped)
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent / "config.yml"
LOG_PATH    = Path(__file__).parent / "outputs" / "batch_run.log"

# Datasets to process: (label, UUID)
DATASETS = [
    ("Antarctic Provider 2022", "f77ce947-4154-4931-b9d6-981152a7d3f7"),
    ("Antarctic Sea 2016",      "a6cfbb08-68e4-49b9-add6-5a463421f0d0"),
    ("Endurance 2021",          "d5c8fa9e-a8e7-4fae-8665-1f8965c89691"),
    ("Saga Sea 2011",           "fc74152d-b256-4ce0-81d4-e1f87e88c4af"),
    ("Saga Sea 2025",           "3d1d97bc-7698-47f0-89e1-cac0ab9656b1"),
    ("Saga Sea LNSK",           "058bd885-5788-4bed-94ee-9efb47081bad"),
    ("Sagasea Kalibrering",     "7e8690be-783b-41c9-97cf-bf30067d7bf0"),
    # Tracklines & Metadata has 0 files — skipped automatically
]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging():
    Path("outputs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("batch")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit("config.yml not found.")
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def write_config_collection(collection_id: str):
    """Temporarily update config.yml to point at a specific dataset UUID."""
    cfg = load_config()
    cfg["odp"]["collection_id"] = collection_id
    cfg["odp"]["n_files"] = 1
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)


def restore_config(original_collection: str):
    cfg = load_config()
    cfg["odp"]["collection_id"] = original_collection
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Download: get chronologically first file
# ---------------------------------------------------------------------------

def get_first_file_chronological(dataset_id: str, api_key: str, base_url: str) -> str | None:
    """
    List all files in a dataset, sort by the date/time embedded in the filename
    (format: ...-D{YYYYMMDD}-T{HHMMSS}...), return the name of the earliest one.
    Returns None if the dataset has no files.
    """
    try:
        from odp.client import Client
        client = Client(api_key=api_key)
    except (ImportError, TypeError):
        from odp.new_client import Client
        from odp.dataset import Dataset as _DS
        client = Client(api_key=api_key)

    try:
        from odp.dataset import Dataset
        ds    = Dataset(client, dataset_id)
        files = list(ds.files.list())
    except Exception:
        # Fallback: use client.dataset() style (newer SDK)
        ds    = client.dataset(dataset_id)
        files = list(ds.files.list())

    if not files:
        return None

    def sort_key(f):
        name = getattr(f, "name", None) or getattr(f, "filename", None) or str(f)
        m = re.search(r"D(\d{8})-T(\d{6})", name)
        return m.group(1) + m.group(2) if m else name

    files_sorted = sorted(files, key=sort_key)
    first = files_sorted[0]
    return getattr(first, "name", None) or getattr(first, "filename", None) or str(first)


# ---------------------------------------------------------------------------
# Run a single script as a subprocess
# ---------------------------------------------------------------------------

def run_script(script: str, log: logging.Logger, extra_args: list[str] | None = None) -> bool:
    """Run a pipeline script. Returns True on success."""
    cmd = [sys.executable, f"scripts/{script}"] + (extra_args or [])
    result = subprocess.run(cmd, capture_output=False, text=True)
    if result.returncode != 0:
        log.error(f"  {script} exited with code {result.returncode}")
        return False
    return True


# ---------------------------------------------------------------------------
# Process one dataset
# ---------------------------------------------------------------------------

def process_dataset(label: str, dataset_id: str, cfg: dict,
                    skip_upload: bool, dry_run: bool, log: logging.Logger) -> bool:
    api_key  = os.environ.get("ODP_API_KEY", "")
    base_url = cfg["odp"].get("base_url", "https://api.hubocean.earth")
    data_dir = Path(cfg["outputs"]["data_dir"])
    out_dir  = Path(cfg["outputs"]["output_dir"])

    log.info(f"{'[DRY-RUN] ' if dry_run else ''}=== {label} ({dataset_id}) ===")

    # --- Find chronologically first file ---
    if dry_run:
        log.info("  [dry-run] Would query ODP for first chronological file")
        return True

    log.info("  Querying ODP for first chronological file ...")
    try:
        first_name = get_first_file_chronological(dataset_id, api_key, base_url)
    except Exception as e:
        log.error(f"  Failed to list files: {e}")
        return False

    if first_name is None:
        log.warning(f"  No files found in dataset — skipping.")
        return True  # not an error, just empty

    log.info(f"  First file: {first_name}")

    # --- Point config at this dataset and download ---
    original_collection = cfg["odp"]["collection_id"]
    write_config_collection(dataset_id)

    t0 = time.time()
    log.info("  [1/5] Downloading ...")
    if not run_script("01_odp_download.py", log):
        restore_config(original_collection)
        return False

    # Identify what was downloaded
    manifest = data_dir / "manifest.json"
    if not manifest.exists():
        log.error("  manifest.json not written — download may have failed")
        restore_config(original_collection)
        return False

    with open(manifest) as f:
        downloaded = json.load(f)["files"]
    stem = Path(downloaded[0]).stem if downloaded else None
    if not stem:
        log.error("  No files in manifest")
        restore_config(original_collection)
        return False

    log.info(f"  Downloaded: {stem}.raw  ({time.time()-t0:.0f}s)")

    # --- Run pipeline steps ---
    steps = [
        ("02_process.py",    "[2/5] Calibrate → Sv + MVBS"),
        ("03_visualize.py",  "[3/5] Visualise"),
        ("04_provenance.py", "[4/5] Provenance"),
        ("05_freq_diff.py",  "[5/5] Frequency differencing"),
    ]

    for script, label_step in steps:
        log.info(f"  {label_step} ...")
        if not run_script(script, log):
            restore_config(original_collection)
            return False

    # --- Upload ---
    if not skip_upload:
        log.info("  [6/5] Uploading to ODP ...")
        if not run_script("06_odp_upload.py", log):
            restore_config(original_collection)
            return False
    else:
        log.info("  Upload skipped (--skip-upload)")

    # --- Clean up raw file (always keep zarr + figures) ---
    raw_path = data_dir / f"{stem}.raw"
    if raw_path.exists():
        raw_path.unlink()
        log.info(f"  Cleaned up raw file ({raw_path.name})")

    restore_config(original_collection)
    elapsed = time.time() - t0
    log.info(f"  Done: {label} in {elapsed/60:.1f} min")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Batch pipeline over multiple ODP datasets")
    parser.add_argument("--dry-run",     action="store_true", help="Print plan, no downloads")
    parser.add_argument("--skip-upload", action="store_true", help="Skip ODP upload step")
    args = parser.parse_args()

    log = setup_logging()
    cfg = load_config()

    if not os.environ.get("ODP_API_KEY") and not args.dry_run:
        log.error("ODP_API_KEY not set. Run: $env:ODP_API_KEY = 'sk_...'")
        sys.exit(1)

    log.info(f"Batch run started: {datetime.now().isoformat()}")
    log.info(f"Datasets: {len(DATASETS)}  |  dry_run={args.dry_run}  |  skip_upload={args.skip_upload}")
    log.info(f"Upload target: {cfg.get('upload', {}).get('dataset_id', '(none)')}")

    succeeded, failed, skipped = [], [], []

    for label, dataset_id in DATASETS:
        try:
            ok = process_dataset(
                label, dataset_id, cfg,
                skip_upload=args.skip_upload,
                dry_run=args.dry_run,
                log=log,
            )
            if ok:
                succeeded.append(label)
            else:
                failed.append(label)
        except Exception as e:
            log.exception(f"Unexpected error processing {label}: {e}")
            failed.append(label)

    # Summary
    log.info("")
    log.info("=" * 60)
    log.info(f"Batch complete: {len(succeeded)} succeeded, {len(failed)} failed")
    if succeeded:
        log.info("  Succeeded: " + ", ".join(succeeded))
    if failed:
        log.warning("  Failed:    " + ", ".join(failed))
        log.warning("  Re-run to retry failed datasets.")
    log.info(f"Full log: {LOG_PATH}")


if __name__ == "__main__":
    main()
