"""
Step 5: Multi-frequency analysis — compute ΔSv (Sv₁₂₀ − Sv₃₈) and produce
a frequency-differenced echogram for krill / fish discrimination.

ΔSv interpretation (after Madureira et al. 1993; Everson et al. 1999):
  ΔSv > 10 dB   → strong krill signature
   2 < ΔSv < 10 → possible krill / mixed
  ΔSv <  2 dB   → fish / non-krill (swimbladder target, 38 kHz dominant)

Inputs:
  outputs/{stem}_MVBS.zarr   — both channels in one zarr (from 02_process.py)
  outputs/{stem}.nc          — EchoData with GPS in Platform group

Outputs:
  outputs/figures/{stem}_dSv.png     — ΔSv echogram with interpretation bands
  outputs/figures/{stem}_dSv_map.png — vessel GPS track coloured by ΔSv
  outputs/{stem}_dSv.zarr            — gridded ΔSv xarray dataset

Usage:
    python scripts/05_freq_diff.py
    python scripts/05_freq_diff.py --file data/AKBM-AntarcticSea-2025-D20250101-T000557.raw
"""

import argparse
import json
import sys
from pathlib import Path

import json as _json
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
import numpy as np
import xarray as xr
import yaml

CONFIG_PATH = Path(__file__).parent.parent / "config.yml"

# ΔSv thresholds (dB)
KRILL_HIGH = 10.0   # > 10 dB  → krill
KRILL_LOW  =  2.0   # 2–10 dB  → possible krill / mixed
# < 2 dB            → fish / non-krill

CMAP_DSV = "RdYlGn"   # red = fish, green = krill


def load_config():
    if not CONFIG_PATH.exists():
        sys.exit("config.yml not found.")
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_channel_by_freq(ds: xr.Dataset, target_khz: int) -> str:
    """Return the channel name whose label contains the target frequency."""
    freq_str = str(target_khz)
    for ch in ds.channel.values:
        if freq_str in ch:
            return ch
    # Fallback: match on actual frequency values if stored
    raise ValueError(
        f"No channel matching {target_khz} kHz found. "
        f"Available: {list(ds.channel.values)}"
    )


def load_gps(nc_path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Read GPS track from EchoData NetCDF Platform group.
    Returns (time_ns, lat, lon) arrays — time in int64 nanoseconds.
    """
    try:
        import echopype as ep
        ed = ep.open_converted(str(nc_path))
        plat = ed["Platform"]
        # time coordinate name varies by echopype version
        time_key = next(
            k for k in plat.coords if "time" in k.lower()
        )
        t = plat[time_key].values.astype("datetime64[ns]").astype(np.int64)
        lat = plat["latitude"].values.astype(float)
        lon = plat["longitude"].values.astype(float)
        return t, lat, lon
    except Exception as e:
        print(f"    GPS load failed ({e}) — skipping geo-referenced plot")
        return None, None, None


def freq_diff(stem: str, cfg: dict, output_dir: Path, fig_dir: Path):
    mvbs_path = output_dir / f"{stem}_MVBS.zarr"
    nc_path   = output_dir / f"{stem}.nc"
    dsv_path    = output_dir / f"{stem}_dSv.zarr"
    geojson_path = fig_dir / f"{stem}_track.geojson"
    csv_path     = output_dir / f"{stem}_track_dSv.csv"

    if not mvbs_path.exists():
        sys.exit(f"MVBS zarr not found: {mvbs_path}. Run 02_process.py first.")

    print(f"\n[1/3] Loading MVBS ({mvbs_path.name}) ...")
    ds_mvbs = xr.open_zarr(str(mvbs_path))

    ch38  = get_channel_by_freq(ds_mvbs, 38)
    ch120 = get_channel_by_freq(ds_mvbs, 120)
    print(f"    38 kHz  → {ch38}")
    print(f"    120 kHz → {ch120}")

    sv38  = ds_mvbs["Sv"].sel(channel=ch38).drop_vars("channel")
    sv120 = ds_mvbs["Sv"].sel(channel=ch120).drop_vars("channel")

    dSv = sv120 - sv38
    dSv.name = "dSv"
    dSv.attrs["long_name"]  = "ΔSv (Sv 120 kHz − Sv 38 kHz)"
    dSv.attrs["units"]      = "dB re 1 m⁻¹"
    dSv.attrs["convention"] = (
        ">10 dB krill; 2–10 dB possible krill; <2 dB fish/non-krill"
    )

    ds_dSv = dSv.to_dataset()

    print(f"[2/3] Saving ΔSv → {dsv_path}")
    ds_dSv.to_zarr(str(dsv_path), mode="w")

    # --- Figure 1: ΔSv echogram ---
    print("[3/3] Plotting ...")
    fig_dir.mkdir(parents=True, exist_ok=True)

    # Depth axis (Y)
    depth_coord = "depth" if "depth" in dSv.coords else "range_sample"
    has_depth = depth_coord == "depth"

    # Build 2D arrays for pcolormesh
    times  = dSv.ping_time.values
    depths = dSv[depth_coord].values
    Z      = dSv.values  # shape (ping_time, depth)

    # ΔSv echogram
    fig, axes = plt.subplots(2, 1, figsize=(16, 10), constrained_layout=True)
    fig.suptitle(f"Frequency differencing (ΔSv = Sv₁₂₀ − Sv₃₈)\n{stem}", fontsize=13)

    # Panel 1: ΔSv echogram
    ax = axes[0]
    cmap = plt.get_cmap(CMAP_DSV)
    norm = mcolors.TwoSlopeNorm(vmin=-10, vcenter=KRILL_LOW, vmax=20)
    pcm = ax.pcolormesh(
        times, depths, Z.T,
        cmap=cmap, norm=norm, shading="nearest"
    )
    if has_depth:
        ax.set_ylim(depths.max(), 0)   # depth increases downward
        ax.set_ylabel("Depth (m)")
    else:
        ax.set_ylim(depths.max(), 0)
        ax.set_ylabel("Range sample")
    ax.set_xlabel("Ping time (UTC)")
    ax.set_title("ΔSv echogram")
    cb = fig.colorbar(pcm, ax=ax, label="ΔSv (dB)", pad=0.01)

    # Interpretation legend
    krill_patch  = mpatches.Patch(color=cmap(norm(15)),  label="Krill (ΔSv > 10 dB)")
    mixed_patch  = mpatches.Patch(color=cmap(norm(5)),   label="Possible krill / mixed (2–10 dB)")
    fish_patch   = mpatches.Patch(color=cmap(norm(-5)),  label="Fish / non-krill (< 2 dB)")
    ax.legend(handles=[krill_patch, mixed_patch, fish_patch],
              loc="lower right", fontsize=8, framealpha=0.8)

    # Panel 2: Depth-integrated ΔSv profile (column mean vs depth)
    ax2 = axes[1]
    # Mean ΔSv at each depth bin (time-averaged)
    dsv_mean_depth = dSv.mean(dim="ping_time")
    # Colour each point by its interpretation
    vals = dsv_mean_depth.values
    dep  = dsv_mean_depth[depth_coord].values
    colors = np.where(vals >= KRILL_HIGH, "#2d7a2d",
              np.where(vals >= KRILL_LOW,  "#b8860b", "#c0392b"))
    ax2.barh(dep, vals, color=colors, height=np.diff(dep).mean() if len(dep) > 1 else 1,
             align="center", alpha=0.8)
    ax2.axvline(KRILL_HIGH, color="#2d7a2d", ls="--", lw=1, label=f"Krill threshold ({KRILL_HIGH} dB)")
    ax2.axvline(KRILL_LOW,  color="#b8860b", ls="--", lw=1, label=f"Mixed threshold ({KRILL_LOW} dB)")
    ax2.axvline(0,           color="black",   ls="-",  lw=0.8)
    ax2.set_xlabel("Mean ΔSv (dB)")
    ax2.set_title("Time-averaged ΔSv profile (depth layers)")
    if has_depth:
        ax2.set_ylabel("Depth (m)")
        ax2.invert_yaxis()
    else:
        ax2.set_ylabel("Range sample")
        ax2.invert_yaxis()
    ax2.legend(fontsize=8)
    ax2.set_xlim(-15, 25)

    echogram_path = fig_dir / f"{stem}_dSv.png"
    fig.savefig(str(echogram_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"    Saved: {echogram_path}")

    # --- Figure 2: GPS track coloured by depth-mean ΔSv (if GPS available) ---
    t_gps, lat_gps, lon_gps = load_gps(nc_path)
    if t_gps is not None and not np.all(np.isnan(lat_gps)):
        # Interpolate per-ping mean ΔSv (depth-averaged) to GPS timestamps
        dsv_ping_mean = dSv.mean(dim=depth_coord)  # (ping_time,)
        pt_ns = dsv_ping_mean.ping_time.values.astype("datetime64[ns]").astype(np.int64)
        pt_dt = dsv_ping_mean.ping_time.values  # datetime64 for ISO strings
        dsv_vals_interp = np.interp(t_gps, pt_ns, dsv_ping_mean.values)

        # --- GPS track map (fixed axis formatting) ---
        fig2, ax3 = plt.subplots(figsize=(9, 7), constrained_layout=True)
        sc = ax3.scatter(lon_gps, lat_gps, c=dsv_vals_interp,
                         cmap=CMAP_DSV, norm=norm, s=12, zorder=3)
        # Show full decimal coordinates, no offset notation
        ax3.xaxis.set_major_formatter(mticker.FormatStrFormatter("%.3f"))
        ax3.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.3f"))
        ax3.set_xlabel("Longitude (°E/W)")
        ax3.set_ylabel("Latitude (°N/S)")
        ax3.set_title(f"Vessel track coloured by mean ΔSv\n{stem}")
        fig2.colorbar(sc, ax=ax3, label="Mean ΔSv (dB)")
        ax3.plot(lon_gps[0],  lat_gps[0],  "ko", ms=8, label="Start", zorder=5)
        ax3.plot(lon_gps[-1], lat_gps[-1], "k^", ms=8, label="End",   zorder=5)
        ax3.legend()
        map_path = fig_dir / f"{stem}_dSv_map.png"
        fig2.savefig(str(map_path), dpi=150, bbox_inches="tight")
        plt.close(fig2)
        print(f"    Saved: {map_path}")

        # --- GeoJSON export (ODP map explorer / QGIS) ---
        # Each GPS point becomes a Feature with timestamp and ΔSv as properties.
        valid = ~(np.isnan(lat_gps) | np.isnan(lon_gps))
        features = []
        for i in np.where(valid)[0]:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(lon_gps[i]), float(lat_gps[i])],
                },
                "properties": {
                    "dSv_mean_dB": round(float(dsv_vals_interp[i]), 2),
                    "classification": (
                        "krill" if dsv_vals_interp[i] >= KRILL_HIGH
                        else "possible_krill" if dsv_vals_interp[i] >= KRILL_LOW
                        else "fish_or_non_krill"
                    ),
                    "stem": stem,
                },
            })
        # Also include the full track as a LineString feature
        coords = [[float(lon_gps[i]), float(lat_gps[i])] for i in np.where(valid)[0]]
        track_feature = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "stem": stem,
                "mean_dSv_dB": round(float(np.nanmean(dsv_vals_interp[valid])), 2),
                "description": "Vessel track coloured by mean ΔSv (Sv120 - Sv38)",
            },
        }
        geojson = {
            "type": "FeatureCollection",
            "features": [track_feature] + features,
        }
        with open(geojson_path, "w") as f:
            _json.dump(geojson, f, indent=2)
        print(f"    Saved: {geojson_path}")

        # --- CSV export (ODP tabular ingest) ---
        # Columns: timestamp, latitude, longitude, dSv_mean_dB, classification
        # This is the format ODP tabular datasets expect for map-explorer rendering.
        import csv as _csv
        with open(csv_path, "w", newline="") as f:
            writer = _csv.DictWriter(
                f,
                fieldnames=["timestamp", "latitude", "longitude",
                            "dSv_mean_dB", "classification", "stem"],
            )
            writer.writeheader()
            # Interpolate GPS timestamps back to ping_time grid for the CSV
            # (use ping-time rows so each row is a sonar ping, not a GPS fix)
            pt_ns_all = dsv_ping_mean.ping_time.values.astype("datetime64[ns]")
            lat_interp = np.interp(pt_ns_all.astype(np.int64), t_gps, lat_gps)
            lon_interp = np.interp(pt_ns_all.astype(np.int64), t_gps, lon_gps)
            for idx, t in enumerate(pt_ns_all):
                dv = float(dsv_ping_mean.values[idx])
                writer.writerow({
                    "timestamp": str(t),
                    "latitude":  round(float(lat_interp[idx]), 6),
                    "longitude": round(float(lon_interp[idx]), 6),
                    "dSv_mean_dB": round(dv, 2),
                    "classification": (
                        "krill" if dv >= KRILL_HIGH
                        else "possible_krill" if dv >= KRILL_LOW
                        else "fish_or_non_krill"
                    ),
                    "stem": stem,
                })
        print(f"    Saved: {csv_path}")
    else:
        print("    Skipped GPS export (no valid GPS in Platform group)")

    return {
        "dSv_zarr": str(dsv_path),
        "geojson": str(geojson_path) if t_gps is not None else None,
        "csv": str(csv_path) if t_gps is not None else None,
    }


def main():
    parser = argparse.ArgumentParser(description="Frequency differencing (ΔSv)")
    parser.add_argument("--file", type=str, default=None,
                        help="Stem or path of the file to process (e.g. data/AKBM-....raw)")
    args = parser.parse_args()

    cfg = load_config()
    output_dir = Path(cfg["outputs"]["output_dir"])
    fig_dir    = output_dir / "figures"

    if args.file:
        stem = Path(args.file).stem
        stems = [stem]
    else:
        proc_manifest = output_dir / "processed.json"
        if proc_manifest.exists():
            with open(proc_manifest) as f:
                records = json.load(f)
            stems = [Path(r["raw"]).stem for r in records]
        else:
            stems = [p.stem.replace("_MVBS", "") for p in output_dir.glob("*_MVBS.zarr")]

    if not stems:
        sys.exit("No processed files found. Run 02_process.py first.")

    for stem in stems:
        print(f"\n=== Frequency differencing: {stem} ===")
        freq_diff(stem, cfg, output_dir, fig_dir)

    print("\nDone. Next step: python scripts/04_provenance.py")


if __name__ == "__main__":
    main()
