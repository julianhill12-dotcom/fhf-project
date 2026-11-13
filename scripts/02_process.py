"""
Step 2: Convert raw EK60/EK80 files to Zarr and compute volume backscattering strength (Sv).

Reads .raw files from ./data/ (written by 01_odp_download.py),
converts them to SONAR-netCDF4 via Echopype, computes Sv,
adds depth, and saves Zarr stores to ./outputs/.

Outputs per file:
  outputs/{stem}.zarr       — raw EchoData in SONAR-netCDF4 format
  outputs/{stem}_Sv.zarr    — calibrated Sv (volume backscattering strength)
  outputs/{stem}_MVBS.zarr  — mean volume backscattering strength (gridded)

Usage:
    python scripts/02_process.py
    python scripts/02_process.py --file data/AKBM-AntarcticSea-2025-D20250101-T000557.raw
"""

import argparse
import json
import sys
from pathlib import Path

import echopype as ep
import yaml

CONFIG_PATH = Path(__file__).parent.parent / "config.yml"


def load_config():
    if not CONFIG_PATH.exists():
        sys.exit("config.yml not found.")
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def process_raw(raw_path: Path, cfg: dict, output_dir: Path):
    """Convert one .raw file through the full Echopype pipeline."""
    stem = raw_path.stem
    waveform_mode = cfg["processing"]["waveform_mode"]
    encode_mode = cfg["processing"]["encode_mode"]
    range_bin = cfg["processing"]["range_bin"]
    ping_time_bin = cfg["processing"]["ping_time_bin"]
    range_max_m = cfg["processing"]["range_max_m"]

    ed_path = output_dir / f"{stem}.zarr"
    sv_path = output_dir / f"{stem}_Sv.zarr"
    mvbs_path = output_dir / f"{stem}_MVBS.zarr"

    # --- 1. Parse raw file → EchoData (SONAR-netCDF4) ---
    print(f"\n[1/3] Parsing {raw_path.name} ...")
    # Detect sonar model from filename convention or config.
    # AKBM vessels used both EK60 and EK80; 2025 data is EK80.
    sonar_model = "EK80" if "EK80" in raw_path.name.upper() else "EK60"
    # Fallback: EK80 is correct for recent AKBM vessels.
    if sonar_model == "EK60":
        sonar_model = "EK80"

    ed = ep.open_raw(str(raw_path), sonar_model=sonar_model)

    # Save EchoData as NetCDF (SONAR-netCDF4 layout)
    # Note: zarr save skipped due to zarr 3.x / xarray compressor API change.
    # NetCDF4 is fully equivalent for this pipeline.
    nc_path = output_dir / f"{stem}.nc"
    print(f"    Saving EchoData → {nc_path}")
    try:
        ed.to_netcdf(str(nc_path), overwrite=True)
    except Exception as e:
        print(f"    Warning: EchoData save failed ({e}) — continuing without it")

    # --- 2. Calibrate → Sv ---
    print(f"[2/3] Computing Sv (waveform={waveform_mode}, encode={encode_mode}) ...")
    ds_Sv = ep.calibrate.compute_Sv(
        ed,
        waveform_mode=waveform_mode,
        encode_mode=encode_mode,
    )
    # Add depth coordinate (uses sonar tilt angle and sound speed from env group)
    ds_Sv = ep.consolidate.add_depth(ds_Sv, ed)

    print(f"    Saving Sv → {sv_path}")
    ds_Sv.to_zarr(str(sv_path), mode="w")

    # --- 3. Compute MVBS (mean volume backscattering strength on a regular grid) ---
    print(f"[3/3] Computing MVBS (range_bin={range_bin}, ping_time_bin={ping_time_bin}) ...")
    ds_MVBS = ep.commongrid.compute_MVBS(
        ds_Sv,
        range_var="depth",
        range_bin=range_bin,
        ping_time_bin=ping_time_bin,
        range_var_max=f"{range_max_m}m",
    )

    print(f"    Saving MVBS → {mvbs_path}")
    ds_MVBS.to_zarr(str(mvbs_path), mode="w")

    print(f"\n  Done: {raw_path.name}")
    print(f"    Channels : {list(ds_Sv.channel.values)}")
    print(f"    Pings    : {ds_Sv.sizes.get('ping_time', '?')}")
    print(f"    Range    : {ds_Sv.sizes.get('range_sample', '?')} samples")

    return {
        "raw": str(raw_path),
        "sonar_model": sonar_model,
        "echodata_zarr": str(ed_path),
        "Sv_zarr": str(sv_path),
        "MVBS_zarr": str(mvbs_path),
        "channels": list(ds_Sv.channel.values),
        "n_pings": int(ds_Sv.sizes.get("ping_time", 0)),
    }


def main():
    parser = argparse.ArgumentParser(description="Process raw EK80 files with Echopype")
    parser.add_argument("--file", type=str, default=None, help="Process a single .raw file")
    args = parser.parse_args()

    cfg = load_config()
    data_dir = Path(cfg["outputs"]["data_dir"])
    output_dir = Path(cfg["outputs"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.file:
        raw_files = [Path(args.file)]
    else:
        manifest_path = data_dir / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path) as f:
                manifest = json.load(f)
            raw_files = [Path(p) for p in manifest["files"]]
        else:
            raw_files = sorted(data_dir.glob("*.raw"))

    if not raw_files:
        sys.exit(f"No .raw files found in {data_dir}. Run 01_odp_download.py first.")

    results = []
    for raw_path in raw_files:
        result = process_raw(raw_path, cfg, output_dir)
        results.append(result)

    # Write processing manifest for downstream scripts
    proc_manifest = output_dir / "processed.json"
    with open(proc_manifest, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nProcessed {len(results)} file(s). Manifest: {proc_manifest}")
    print("Next step: python scripts/03_visualize.py")


if __name__ == "__main__":
    main()
