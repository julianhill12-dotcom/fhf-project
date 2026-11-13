"""
Step 4: Generate a W3C PROV-JSON provenance record for the full processing run.

Records the complete lineage chain:
  ODP raw EK80 → Sv → MVBS → ΔSv (frequency difference) → GeoJSON/CSV track

The PROV-JSON file can be:
  - Stored alongside Zarr outputs on ODP as a sidecar
  - Registered in ODP's metadata catalogue (STAC item properties)
  - Used to re-run or audit the pipeline at any point

Outputs:
  outputs/{stem}_provenance.json  — W3C PROV-JSON record

Usage:
    python scripts/04_provenance.py
"""

import datetime
import json
import sys
from pathlib import Path

import echopype as ep
import yaml

CONFIG_PATH = Path(__file__).parent.parent / "config.yml"

PROV_NS = "https://oceandata.earth/prov#"
ODP_NS = "https://api.hubocean.earth/api/stac/collections/"


def load_config():
    if not CONFIG_PATH.exists():
        sys.exit("config.yml not found.")
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def iso_now():
    return datetime.datetime.utcnow().isoformat() + "Z"


def build_prov_record(entry: dict, cfg: dict) -> dict:
    """
    Build a W3C PROV-JSON document for one processing run.

    Structure:
      entity   — the data artefacts (raw file, Sv zarr, MVBS zarr)
      activity — the processing steps (download, calibrate, grid)
      agent    — the software and the person who ran it
      wasDerivedFrom / wasGeneratedBy / used — the lineage links
    """
    stem = Path(entry["raw"]).stem
    collection_id = cfg["odp"]["collection_id"]
    waveform_mode = cfg["processing"]["waveform_mode"]
    encode_mode = cfg["processing"]["encode_mode"]
    range_bin = cfg["processing"]["range_bin"]
    ping_time_bin = cfg["processing"]["ping_time_bin"]

    # Unique IDs (prefix with PROV_NS to make them resolvable URIs)
    raw_id            = f"{PROV_NS}entity/{stem}_raw"
    sv_id             = f"{PROV_NS}entity/{stem}_Sv"
    mvbs_id           = f"{PROV_NS}entity/{stem}_MVBS"
    dsv_id            = f"{PROV_NS}entity/{stem}_dSv"
    geojson_id        = f"{PROV_NS}entity/{stem}_track_geojson"
    download_act_id   = f"{PROV_NS}activity/download_{stem}"
    calibrate_act_id  = f"{PROV_NS}activity/calibrate_{stem}"
    grid_act_id       = f"{PROV_NS}activity/grid_{stem}"
    freqdiff_act_id   = f"{PROV_NS}activity/freq_diff_{stem}"
    echopype_agent_id = f"{PROV_NS}agent/echopype"
    user_agent_id     = f"{PROV_NS}agent/user"

    # Check whether ΔSv outputs exist on disk
    output_dir = Path(entry["raw"]).parent.parent / cfg["outputs"]["output_dir"]
    dsv_zarr_path   = output_dir / f"{stem}_dSv.zarr"
    geojson_file    = output_dir / "figures" / f"{stem}_track.geojson"
    csv_file        = output_dir / f"{stem}_track_dSv.csv"

    record = {
        "prefix": {
            "prov": "http://www.w3.org/ns/prov#",
            "odp": ODP_NS,
            "workflow": PROV_NS,
        },
        "entity": {
            # Source: raw echosounder file on ODP
            raw_id: {
                "prov:type": "prov:Entity",
                "prov:label": f"{stem}.raw",
                "workflow:sonar_model": entry.get("sonar_model", "EK80"),
                "workflow:format": "Simrad EK60/EK80 .raw",
                "workflow:source_collection": f"odp:{collection_id}",
                "workflow:odp_collection_url": f"{ODP_NS}{collection_id}",
            },
            # Intermediate: SONAR-netCDF4 EchoData (Zarr)
            sv_id: {
                "prov:type": "prov:Entity",
                "prov:label": f"{stem}_Sv.zarr",
                "workflow:format": "Zarr / SONAR-netCDF4",
                "workflow:variable": "Sv (volume backscattering strength)",
                "workflow:units": "dB re 1 m-1",
                "workflow:local_path": entry.get("Sv_zarr"),
                "workflow:waveform_mode": waveform_mode,
                "workflow:encode_mode": encode_mode,
            },
            # Intermediate: gridded MVBS (Zarr)
            mvbs_id: {
                "prov:type": "prov:Entity",
                "prov:label": f"{stem}_MVBS.zarr",
                "workflow:format": "Zarr",
                "workflow:variable": "MVBS (mean volume backscattering strength)",
                "workflow:units": "dB re 1 m-1",
                "workflow:local_path": entry.get("MVBS_zarr"),
                "workflow:range_bin": range_bin,
                "workflow:ping_time_bin": ping_time_bin,
                "workflow:channels": entry.get("channels", []),
            },
            # Output: frequency-differenced ΔSv (Zarr)
            dsv_id: {
                "prov:type": "prov:Entity",
                "prov:label": f"{stem}_dSv.zarr",
                "workflow:format": "Zarr",
                "workflow:variable": "ΔSv = Sv(120 kHz) − Sv(38 kHz)",
                "workflow:units": "dB",
                "workflow:local_path": str(dsv_zarr_path) if dsv_zarr_path.exists() else None,
                "workflow:interpretation": (
                    ">10 dB krill; 2–10 dB possible krill/mixed; <2 dB fish/non-krill"
                ),
                "workflow:reference": "Madureira et al. 1993; Everson et al. 1999",
            },
            # Output: geo-referenced track with ΔSv (GeoJSON — ODP map explorer)
            geojson_id: {
                "prov:type": "prov:Entity",
                "prov:label": f"{stem}_track.geojson",
                "workflow:format": "GeoJSON (RFC 7946)",
                "workflow:local_path": str(geojson_file) if geojson_file.exists() else None,
                "workflow:odp_note": (
                    "Upload to ODP as a tabular/spatial dataset to enable "
                    "map-explorer rendering of the vessel track coloured by ΔSv. "
                    "CSV equivalent: outputs/{stem}_track_dSv.csv"
                ),
                "workflow:csv_path": str(csv_file) if csv_file.exists() else None,
            },
        },
        "activity": {
            # Step 1: Download from ODP
            download_act_id: {
                "prov:type": "prov:Activity",
                "prov:label": "Download raw file from ODP",
                "prov:startTime": iso_now(),
                "prov:endTime": iso_now(),
                "workflow:script": "scripts/01_odp_download.py",
                "workflow:odp_collection": collection_id,
            },
            # Step 2: Calibrate → Sv
            calibrate_act_id: {
                "prov:type": "prov:Activity",
                "prov:label": "Calibrate EK80 raw data to Sv using Echopype",
                "prov:startTime": iso_now(),
                "prov:endTime": iso_now(),
                "workflow:script": "scripts/02_process.py",
                "workflow:function": "echopype.calibrate.compute_Sv",
                "workflow:params": {
                    "waveform_mode": waveform_mode,
                    "encode_mode": encode_mode,
                },
            },
            # Step 3: Compute MVBS grid
            grid_act_id: {
                "prov:type": "prov:Activity",
                "prov:label": "Compute MVBS on regular depth/time grid",
                "prov:startTime": iso_now(),
                "prov:endTime": iso_now(),
                "workflow:script": "scripts/02_process.py",
                "workflow:function": "echopype.commongrid.compute_MVBS",
                "workflow:params": {
                    "range_var": "depth",
                    "range_bin": range_bin,
                    "ping_time_bin": ping_time_bin,
                },
            },
            # Step 4: Frequency differencing → ΔSv + GeoJSON track
            freqdiff_act_id: {
                "prov:type": "prov:Activity",
                "prov:label": "Compute ΔSv = Sv(120 kHz) − Sv(38 kHz) for krill/fish discrimination",
                "prov:startTime": iso_now(),
                "prov:endTime": iso_now(),
                "workflow:script": "scripts/05_freq_diff.py",
                "workflow:params": {
                    "krill_threshold_dB": 10.0,
                    "mixed_threshold_dB": 2.0,
                },
                "workflow:outputs": [
                    f"{stem}_dSv.zarr",
                    f"{stem}_track.geojson",
                    f"{stem}_track_dSv.csv",
                ],
            },
        },
        "agent": {
            echopype_agent_id: {
                "prov:type": "prov:SoftwareAgent",
                "prov:label": "Echopype",
                "workflow:version": ep.__version__,
                "workflow:url": "https://github.com/OSOceanAcoustics/echopype",
                "workflow:license": "Apache-2.0",
            },
            user_agent_id: {
                "prov:type": "prov:Person",
                "prov:label": "HUB Ocean WP4 analyst",
            },
        },
        # Lineage links
        "wasGeneratedBy": {
            f"{sv_id}_gen": {
                "prov:entity": sv_id,
                "prov:activity": calibrate_act_id,
            },
            f"{mvbs_id}_gen": {
                "prov:entity": mvbs_id,
                "prov:activity": grid_act_id,
            },
            f"{dsv_id}_gen": {
                "prov:entity": dsv_id,
                "prov:activity": freqdiff_act_id,
            },
            f"{geojson_id}_gen": {
                "prov:entity": geojson_id,
                "prov:activity": freqdiff_act_id,
            },
        },
        "used": {
            f"{download_act_id}_used": {
                "prov:activity": download_act_id,
                "prov:entity": raw_id,
            },
            f"{calibrate_act_id}_used": {
                "prov:activity": calibrate_act_id,
                "prov:entity": raw_id,
            },
            f"{grid_act_id}_used": {
                "prov:activity": grid_act_id,
                "prov:entity": sv_id,
            },
            f"{freqdiff_act_id}_used": {
                "prov:activity": freqdiff_act_id,
                "prov:entity": mvbs_id,
            },
        },
        "wasDerivedFrom": {
            f"{sv_id}_derived": {
                "prov:generatedEntity": sv_id,
                "prov:usedEntity": raw_id,
                "prov:activity": calibrate_act_id,
            },
            f"{mvbs_id}_derived": {
                "prov:generatedEntity": mvbs_id,
                "prov:usedEntity": sv_id,
                "prov:activity": grid_act_id,
            },
            f"{dsv_id}_derived": {
                "prov:generatedEntity": dsv_id,
                "prov:usedEntity": mvbs_id,
                "prov:activity": freqdiff_act_id,
            },
            f"{geojson_id}_derived": {
                "prov:generatedEntity": geojson_id,
                "prov:usedEntity": dsv_id,
                "prov:activity": freqdiff_act_id,
            },
        },
        "wasAssociatedWith": {
            f"{calibrate_act_id}_assoc": {
                "prov:activity": calibrate_act_id,
                "prov:agent": echopype_agent_id,
            },
            f"{download_act_id}_assoc": {
                "prov:activity": download_act_id,
                "prov:agent": user_agent_id,
            },
        },
    }

    return record


def print_summary(record: dict, stem: str):
    """Print a human-readable lineage chain."""
    ep_ver = record["agent"][list(record["agent"].keys())[0]].get("workflow:version", "?")
    print(f"\n  Provenance chain for {stem}:")
    print(f"    ODP raw file ({stem}.raw)")
    print(f"    └─ [calibrate via Echopype {ep_ver}]")
    print(f"       └─ Sv Zarr ({stem}_Sv.zarr)")
    print(f"          └─ [compute_MVBS]")
    print(f"             └─ MVBS Zarr ({stem}_MVBS.zarr)")
    print(f"                └─ [frequency differencing Sv₁₂₀ − Sv₃₈]")
    print(f"                   ├─ ΔSv Zarr ({stem}_dSv.zarr)        ← derived product on ODP")
    print(f"                   ├─ GeoJSON track ({stem}_track.geojson) ← ODP map explorer")
    print(f"                   └─ CSV track ({stem}_track_dSv.csv)   ← ODP tabular ingest")


def main():
    cfg = load_config()
    output_dir = Path(cfg["outputs"]["output_dir"])

    proc_manifest = output_dir / "processed.json"
    if not proc_manifest.exists():
        sys.exit(f"No processed.json in {output_dir}. Run 02_process.py first.")

    with open(proc_manifest) as f:
        results = json.load(f)

    for entry in results:
        stem = Path(entry["raw"]).stem
        print(f"\nBuilding provenance record: {stem}")

        record = build_prov_record(entry, cfg)
        out_path = output_dir / f"{stem}_provenance.json"
        with open(out_path, "w") as f:
            json.dump(record, f, indent=2)

        print_summary(record, stem)
        print(f"\n  Saved: {out_path}")

    print(
        "\n--- ODP integration note ---\n"
        "1. Upload *_dSv.zarr alongside *_Sv.zarr and *_MVBS.zarr as derived\n"
        "   products on ODP (requires write access to a project dataset).\n"
        "2. Upload *_track.geojson or *_track_dSv.csv as an ODP tabular dataset\n"
        "   to enable map-explorer rendering of the vessel track coloured by ΔSv.\n"
        "   The CSV columns (timestamp, latitude, longitude, dSv_mean_dB,\n"
        "   classification) match the ODP tabular ingestion schema.\n"
        "3. Store *_provenance.json as a sidecar file or STAC item property\n"
        "   to make the full lineage chain queryable from the catalogue.\n"
        "\nThe raw file is already registered in ODP under ODC-BY 1.0;\n"
        "the provenance JSON links back to it via workflow:odp_collection_url."
    )


if __name__ == "__main__":
    main()
