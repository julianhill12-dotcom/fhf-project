"""
Step 6: Upload derived products to ODP.

Uploads three things per processed file:
  1. Tabular dataset  — vessel track + ΔSv per ping → ODP map explorer
  2. GeoJSON sidecar  — same track as RFC 7946 FeatureCollection → file storage
  3. Provenance JSON  — W3C PROV-JSON lineage record → file storage
  (Zarr stores are zipped and uploaded as file attachments)

Prerequisites:
    pip install odp-sdk pyarrow shapely

Config (config.yml → upload section):
    collection_id      — YOUR project data collection UUID on ODP (not AKBM's)
    track_dataset_name — name shown in the ODP catalogue
    license            — e.g. "CC-BY-4.0"
    citation           — free-text citation for the derived dataset

Leave upload.collection_id blank for a dry-run that prints what would be uploaded.

Usage:
    python scripts/06_odp_upload.py
    python scripts/06_odp_upload.py --stem AKBM-AntarcticSea-2025-D20250101-T000557
    python scripts/06_odp_upload.py --clean   # delete existing dataset and re-ingest
    python scripts/06_odp_upload.py --dry-run # print plan without touching ODP
"""

import argparse
import csv
import io
import json
import os
import sys
import uuid
import zipfile
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent.parent / "config.yml"


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit("config.yml not found.")
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_api_key() -> str:
    key = os.environ.get("ODP_API_KEY", "").strip()
    if not key:
        sys.exit(
            "ODP_API_KEY environment variable is not set.\n"
            "Set it with:  $env:ODP_API_KEY = 'sk_...'"
        )
    return key


# ---------------------------------------------------------------------------
# ODP client bootstrap
# ---------------------------------------------------------------------------

def make_client(api_key: str):
    """Return an authenticated ODP client, trying both SDK entry points."""
    try:
        from odp.client import Client
        return Client(api_key=api_key)
    except (ImportError, TypeError):
        pass
    try:
        from odp.new_client import Client
        return Client(api_key=api_key)
    except ImportError:
        sys.exit(
            "Cannot import ODP client. Install the SDK:\n"
            "    pip install odp-sdk"
        )


# ---------------------------------------------------------------------------
# Dataset catalogue helpers
# ---------------------------------------------------------------------------

def find_dataset_by_name(client, name: str) -> str | None:
    """Return dataset id if a dataset with this name exists, else None."""
    import requests as _req
    res = client._request(
        _req.Request(
            method="GET",
            url=client.base_url + "/api/catalog/v2/datasets",
        ),
        retry=False,
    )
    res.raise_for_status()
    for ds in res.json():
        if ds.get("name") == name:
            return ds["id"]
    return None


def create_dataset(client, name: str, description: str, collection_id: str) -> str:
    """Create a new dataset and add it to the given collection. Returns dataset id."""
    import requests as _req

    # 1. Create the dataset
    res = client._request(
        _req.Request(
            method="POST",
            url=client.base_url + "/api/catalog/v2/datasets",
            json={"name": name, "description": description},
        ),
        retry=False,
    )
    res.raise_for_status()
    dataset_id = res.json()["id"]
    print(f"    Created dataset: {dataset_id}")

    # 2. Add to collection
    res2 = client._request(
        _req.Request(
            method="POST",
            url=client.base_url
            + f"/api/catalog/v2/data-collections/{collection_id}/datasets/{dataset_id}",
        ),
        retry=False,
    )
    res2.raise_for_status()
    print(f"    Added to collection: {collection_id}")
    return dataset_id


def delete_dataset(client, dataset_id: str):
    import requests as _req
    res = client._request(
        _req.Request(
            method="DELETE",
            url=client.base_url + f"/api/catalog/v2/datasets/{dataset_id}",
        ),
        retry=False,
    )
    res.raise_for_status()
    print(f"    Deleted dataset: {dataset_id}")


def patch_metadata(client, dataset_id: str, cfg: dict):
    """Apply citation, license, and additional-info metadata to the dataset."""
    import requests as _req
    base = client.base_url + f"/api/catalog/v2/datasets/{dataset_id}/metadata"
    upload_cfg = cfg.get("upload", {})

    license_str = upload_cfg.get("license", "CC-BY-4.0")
    citation    = upload_cfg.get("citation", "")

    if license_str:
        client._request(
            _req.Request(method="PATCH", url=f"{base}/license",
                         json={"license_enum": license_str}),
            retry=False,
        ).raise_for_status()

    if citation:
        client._request(
            _req.Request(method="PATCH", url=f"{base}/citation",
                         json={"text": citation}),
            retry=False,
        ).raise_for_status()

    client._request(
        _req.Request(method="PATCH", url=f"{base}/additional-info",
                     json={
                         "source_collection": cfg["odp"]["collection_id"],
                         "pipeline": "FHF DABB-PADABB WP4 — reproducible hydroacoustic workflow",
                         "variables": "ΔSv = Sv(120 kHz) − Sv(38 kHz), krill/fish classification",
                         "thresholds": ">10 dB krill, 2–10 dB possible krill, <2 dB fish/non-krill",
                         "reference": "Madureira et al. 1993; Everson et al. 1999",
                     }),
        retry=False,
    ).raise_for_status()


# ---------------------------------------------------------------------------
# Tabular upload: vessel track + ΔSv
# ---------------------------------------------------------------------------

def build_track_schema():
    """PyArrow schema for the ΔSv track table with full ODP column metadata."""
    import pyarrow as pa
    return pa.schema([
        pa.field("id", pa.string(), nullable=False,
                 metadata={"description": "UUID primary key."}),
        pa.field("stem", pa.string(), nullable=False,
                 metadata={"description": "Source file stem (AKBM filename without .raw)."}),
        pa.field("timestamp", pa.string(), nullable=False,
                 metadata={"description": "Ping time (UTC, ISO-8601 string)."}),
        pa.field("latitude", pa.float64(), nullable=True,
                 metadata={"description": "Vessel latitude (WGS84, decimal degrees).",
                           "class": "latitude"}),
        pa.field("longitude", pa.float64(), nullable=True,
                 metadata={"description": "Vessel longitude (WGS84, decimal degrees).",
                           "class": "longitude"}),
        pa.field("dSv_mean_dB", pa.float64(), nullable=True,
                 metadata={"description": (
                     "Mean ΔSv = Sv(120 kHz) − Sv(38 kHz) averaged over all depth bins "
                     "at this ping (dB). Positive = krill-like; negative = fish-like."
                 ), "aggr": "mean"}),
        pa.field("classification", pa.string(), nullable=True,
                 metadata={"description": (
                     "Acoustic species classification based on ΔSv. "
                     "One of: krill (>10 dB), possible_krill (2–10 dB), fish_or_non_krill (<2 dB)."
                 )}),
        pa.field("geometry", pa.string(), nullable=True,
                 metadata={
                     "isGeometry": "1",
                     "index": "1",
                     "class": "geometry",
                     "description": "Vessel position as WKT POINT (longitude latitude), WGS84.",
                 }),
    ])


def csv_to_rows(csv_path: Path) -> list[dict]:
    """Read the track CSV and return rows with UUID primary keys and WKT geometry."""
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lat = float(row["latitude"])
            lon = float(row["longitude"])
            dsv = float(row["dSv_mean_dB"]) if row["dSv_mean_dB"] else None
            rows.append({
                "id":             str(uuid.uuid4()),
                "stem":           row["stem"],
                "timestamp":      row["timestamp"],
                "latitude":       lat,
                "longitude":      lon,
                "dSv_mean_dB":    dsv,
                "classification": row["classification"],
                "geometry":       f"POINT ({lon} {lat})",
            })
    return rows


def upload_tabular(client, dataset_id: str, csv_path: Path, dry_run: bool):
    schema = build_track_schema()
    rows   = csv_to_rows(csv_path)

    if dry_run:
        print(f"    [dry-run] Would create table with {len(rows)} rows from {csv_path.name}")
        return

    ds = client.dataset(dataset_id)
    ds.table.create(schema)
    print(f"    Table schema created ({len(schema)} columns)")

    # Insert in one transaction (batch if >10k rows)
    BATCH = 5000
    inserted = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i : i + BATCH]
        with ds as tx:
            tx.insert(batch)
        inserted += len(batch)
        print(f"    Inserted {inserted}/{len(rows)} rows")

    print(f"    Tabular upload complete: {len(rows)} pings → ODP map explorer")


# ---------------------------------------------------------------------------
# File uploads: GeoJSON, provenance JSON, ΔSv Zarr (zipped)
# ---------------------------------------------------------------------------

def zip_directory(dir_path: Path) -> bytes:
    """Return in-memory zip of an entire directory (preserves relative paths)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(dir_path.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(dir_path.parent))
    return buf.getvalue()


def upload_file(client, dataset_id: str, local_path: Path,
                remote_name: str, dry_run: bool):
    if not local_path.exists():
        print(f"    Skipping {remote_name} — file not found: {local_path}")
        return

    if dry_run:
        size_kb = local_path.stat().st_size // 1024
        print(f"    [dry-run] Would upload {remote_name} ({size_kb} KB)")
        return

    ds = client.dataset(dataset_id)
    with open(local_path, "rb") as f:
        file_id = ds.files.upload(remote_name, f)
    print(f"    Uploaded {remote_name} → file id: {file_id}")


def upload_zarr(client, dataset_id: str, zarr_path: Path,
                remote_name: str, dry_run: bool):
    """Zip a Zarr directory and upload it as a single archive."""
    if not zarr_path.exists():
        print(f"    Skipping {remote_name} — zarr not found: {zarr_path}")
        return

    zip_name = remote_name.replace(".zarr", ".zarr.zip")
    file_count = sum(1 for f in zarr_path.rglob("*") if f.is_file())

    if dry_run:
        print(f"    [dry-run] Would zip {zarr_path.name} ({file_count} files) → {zip_name}")
        return

    print(f"    Zipping {zarr_path.name} ({file_count} files) ...")
    zip_bytes = zip_directory(zarr_path)
    size_kb = len(zip_bytes) // 1024

    ds = client.dataset(dataset_id)
    file_id = ds.files.upload(zip_name, zip_bytes)
    print(f"    Uploaded {zip_name} ({size_kb} KB) → file id: {file_id}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def upload_stem(stem: str, cfg: dict, output_dir: Path, fig_dir: Path,
                client, dataset_id: str, dry_run: bool):
    csv_path       = output_dir / f"{stem}_track_dSv.csv"
    geojson_path   = fig_dir   / f"{stem}_track.geojson"
    prov_path      = output_dir / f"{stem}_provenance.json"
    dsv_zarr_path  = output_dir / f"{stem}_dSv.zarr"
    mvbs_zarr_path = output_dir / f"{stem}_MVBS.zarr"

    # 1. Tabular track (map explorer)
    print(f"\n  [1/4] Tabular track (map explorer) ← {csv_path.name}")
    if csv_path.exists():
        upload_tabular(client, dataset_id, csv_path, dry_run)
    else:
        print(f"    Skipping — not found (run 05_freq_diff.py first)")

    # 2. GeoJSON sidecar
    print(f"  [2/4] GeoJSON sidecar ← {geojson_path.name}")
    upload_file(client, dataset_id, geojson_path, geojson_path.name, dry_run)

    # 3. Provenance JSON
    print(f"  [3/4] Provenance JSON ← {prov_path.name}")
    upload_file(client, dataset_id, prov_path, prov_path.name, dry_run)

    # 4. Zarr stores (zipped)
    print(f"  [4/4] ΔSv Zarr (zipped) ← {dsv_zarr_path.name}")
    upload_zarr(client, dataset_id, dsv_zarr_path, dsv_zarr_path.name, dry_run)

    print(f"  [4/4] MVBS Zarr (zipped) ← {mvbs_zarr_path.name}")
    upload_zarr(client, dataset_id, mvbs_zarr_path, mvbs_zarr_path.name, dry_run)


def main():
    parser = argparse.ArgumentParser(description="Upload derived products to ODP")
    parser.add_argument("--stem",    type=str,  default=None,
                        help="Process a single file stem")
    parser.add_argument("--clean",   action="store_true",
                        help="Delete existing ODP dataset and re-ingest from scratch")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be uploaded without touching ODP")
    args = parser.parse_args()
    dry_run = args.dry_run

    cfg        = load_config()
    output_dir = Path(cfg["outputs"]["output_dir"])
    fig_dir    = output_dir / "figures"
    upload_cfg = cfg.get("upload", {})

    # Prefer a pre-existing dataset_id; fall back to create-from-collection flow.
    target_dataset_id = upload_cfg.get("dataset_id", "").strip()
    collection_id     = upload_cfg.get("collection_id", "").strip()

    if not target_dataset_id and not collection_id:
        print(
            "\n[dry-run mode — no dataset_id or collection_id configured]\n"
            "Set upload.dataset_id in config.yml to upload to an existing dataset.\n"
        )
        dry_run = True

    # Discover stems
    if args.stem:
        stems = [args.stem]
    else:
        proc_manifest = output_dir / "processed.json"
        if proc_manifest.exists():
            with open(proc_manifest) as f:
                records = json.load(f)
            stems = [Path(r["raw"]).stem for r in records]
        else:
            stems = [p.stem.replace("_MVBS", "")
                     for p in sorted(output_dir.glob("*_MVBS.zarr"))]

    if not stems:
        sys.exit("No processed stems found. Run 02_process.py first.")

    # Init client
    if not dry_run:
        api_key = get_api_key()
        client  = make_client(api_key)
    else:
        client = None

    # Resolve dataset
    dataset_id = None
    if not dry_run:
        if target_dataset_id:
            # Use pre-existing dataset directly — no create/find needed.
            if args.clean:
                print(f"\n--clean flag ignored when dataset_id is set directly.")
            dataset_id = target_dataset_id
            print(f"\nTarget ODP dataset: {dataset_id}")
            patch_metadata(client, dataset_id, cfg)
        else:
            # Create-from-collection flow (fallback when only collection_id is set).
            dataset_name = upload_cfg.get(
                "track_dataset_name", "FHF DABB WP4 — Acoustic ΔSv vessel tracks"
            )
            existing_id = find_dataset_by_name(client, dataset_name)
            if existing_id and args.clean:
                print(f"\nDeleting existing dataset '{dataset_name}' ({existing_id}) ...")
                delete_dataset(client, existing_id)
                existing_id = None
            if existing_id:
                dataset_id = existing_id
                print(f"\nUsing existing ODP dataset: {dataset_name} ({dataset_id})")
            else:
                print(f"\nCreating ODP dataset: {dataset_name} ...")
                dataset_id = create_dataset(
                    client,
                    name=dataset_name,
                    description=(
                        "Vessel acoustic tracks with frequency-differenced ΔSv "
                        "(Sv 120 kHz − Sv 38 kHz) for krill/fish discrimination. "
                        "Derived from Aker BioMarine EK80 raw data on ODP via Echopype. "
                        "FHF DABB-PADABB WP4."
                    ),
                    collection_id=collection_id,
                )
            patch_metadata(client, dataset_id, cfg)
    else:
        dataset_id = target_dataset_id or "<dry-run-id>"

    # Upload each stem
    for stem in stems:
        print(f"\n=== Uploading: {stem} ===")
        upload_stem(stem, cfg, output_dir, fig_dir, client, dataset_id, dry_run)

    # Summary
    print("\n" + "=" * 60)
    if dry_run:
        print("Dry-run complete. Set upload.dataset_id in config.yml")
        print("and run again (with ODP_API_KEY set) to upload for real.")
    else:
        print(f"Upload complete.")
        print(f"  Dataset ID: {dataset_id}")
        print(f"  URL:        {client.base_url}/catalog/{dataset_id}")
        print(
            "\nThe tabular track dataset is now queryable in ODP and should\n"
            "appear in the map explorer once the portal indexes the geometry column.\n"
            "If the track does not appear on the map, check that upload.collection_id\n"
            "points to a collection with map-explorer rendering enabled."
        )


if __name__ == "__main__":
    main()
