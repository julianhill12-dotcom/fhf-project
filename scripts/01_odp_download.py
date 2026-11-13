"""
Step 1: Download raw EK60/EK80 files from ODP.

Authenticates to the Ocean Data Platform via API key (set ODP_API_KEY env var),
finds the Aker BioMarine EK60/EK80 collection, and downloads
one or more .raw echosounder files into ./data/.

Usage:
    python scripts/01_odp_download.py                  # download n_files from config
    python scripts/01_odp_download.py --list-datasets  # list all ODP collections
    python scripts/01_odp_download.py --list-files     # list files in the AKBM collection
    python scripts/01_odp_download.py --n 3            # override n_files

Authentication:
    Set ODP_API_KEY in your environment before running:
        Windows:  $env:ODP_API_KEY = "your-api-key"
        Linux/Mac: export ODP_API_KEY=your-api-key

    Get your API key from: app.hubocean.earth → Account → API Keys
"""

import argparse
import json
import os
import sys
from pathlib import Path

import requests
import yaml
from tqdm import tqdm

CONFIG_PATH = Path(__file__).parent.parent / "config.yml"
ODP_STAC_BASE = "https://api.hubocean.earth/api/stac"


def load_config():
    if not CONFIG_PATH.exists():
        sys.exit(
            "config.yml not found. Copy config.example.yml to config.yml and fill it in."
        )
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_odp_client():
    """Return an authenticated odp.new_client.Client using ODP_API_KEY env var."""
    try:
        from odp.new_client import Client
    except ImportError:
        sys.exit("odp-sdk not installed correctly. Try: pip install odp-sdk --upgrade")

    api_key = os.environ.get("ODP_API_KEY", "")
    if not api_key:
        sys.exit(
            "\nODP_API_KEY environment variable is not set.\n"
            "Get your API key from: app.hubocean.earth → Account → API Keys\n"
            "Then run:\n"
            "    Windows:   $env:ODP_API_KEY = 'your-api-key'\n"
            "    Linux/Mac: export ODP_API_KEY=your-api-key\n"
        )
    return Client(api_key=api_key)


def list_all_collections():
    """Print all STAC collections available on ODP (no auth needed)."""
    r = requests.get(f"{ODP_STAC_BASE}/collections", timeout=15)
    r.raise_for_status()
    collections = r.json().get("collections", [])
    print(f"\n{'UUID':<40} {'Title'}")
    print("-" * 100)
    for c in collections:
        print(f"{c['id']:<40} {c.get('title', c.get('id', ''))}")
    print(f"\n{len(collections)} collections.")


def list_files_in_dataset(client, dataset_id: str, limit: int = 20):
    """List raw files available in an ODP dataset."""
    from odp.dataset import Dataset
    ds = Dataset(client, dataset_id)
    files = ds.files.list()
    print(f"\n{'File ID':<40} {'Name'}")
    print("-" * 90)
    for f in files[:limit]:
        print(f"{f.get('id',''):<40} {f.get('name', f.get('id', ''))}")
    if len(files) > limit:
        print(f"  ... and {len(files) - limit} more")
    print(f"\nTotal: {len(files)} files")
    return files


def download_file_from_dataset(client, dataset_id: str, file_id: str, file_name: str, dest: Path):
    """Download one raw file from an ODP dataset."""
    from odp.dataset import Dataset
    dest.parent.mkdir(parents=True, exist_ok=True)

    ds = Dataset(client, dataset_id)
    chunks = ds.files.download(file_id)

    with open(dest, "wb") as f, tqdm(unit="B", unit_scale=True, desc=file_name) as bar:
        for chunk in chunks:
            f.write(chunk)
            bar.update(len(chunk))


def main():
    parser = argparse.ArgumentParser(description="Download AKBM raw files from ODP")
    parser.add_argument("--list-datasets", action="store_true", help="List all ODP collections")
    parser.add_argument("--list-files", action="store_true", help="List files in the AKBM collection")
    parser.add_argument("--n", type=int, default=None, help="Number of files to download")
    args = parser.parse_args()

    cfg = load_config()
    dataset_id = cfg["odp"]["collection_id"]
    n_files = args.n or cfg["odp"]["n_files"]
    data_dir = Path(cfg["outputs"]["data_dir"])

    if args.list_datasets:
        list_all_collections()
        return

    print("Connecting to ODP...")
    client = get_odp_client()
    print("Authenticated via API key.")

    from odp.dataset import Dataset
    ds = Dataset(client, dataset_id)
    all_files = ds.files.list()

    if args.list_files:
        list_files_in_dataset(client, dataset_id, limit=50)
        return

    if not all_files:
        sys.exit(
            f"No files found in dataset '{dataset_id}'.\n"
            "Check config.yml collection_id — use --list-datasets to find the right UUID."
        )

    files_to_download = all_files[:n_files]
    downloaded = []

    for file_meta in files_to_download:
        file_id = file_meta.get("id") or file_meta.get("raw_id")
        file_name = file_meta.get("name") or f"{file_id}.raw"
        dest = data_dir / file_name

        if dest.exists():
            print(f"  {file_name} already exists, skipping")
        else:
            print(f"\nDownloading {file_name} ...")
            download_file_from_dataset(client, dataset_id, file_id, file_name, dest)

        downloaded.append({"path": str(dest), "file_id": file_id, "name": file_name})

    # Write manifest for downstream scripts
    manifest_path = data_dir / "manifest.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w") as f:
        json.dump(
            {"files": [d["path"] for d in downloaded], "dataset_id": dataset_id, "file_meta": downloaded},
            f, indent=2,
        )

    print(f"\nDone. {len(downloaded)} file(s) in {data_dir}/")
    print(f"Manifest written to {manifest_path}")
    print("\nNext step: python scripts/02_process.py")


if __name__ == "__main__":
    main()
