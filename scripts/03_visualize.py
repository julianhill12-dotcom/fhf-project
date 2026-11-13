"""
Step 3: Generate echogram visualisations from processed Sv and MVBS data.

Reads Zarr stores from ./outputs/ (written by 02_process.py) and
produces publication-quality PNG echograms for each file and channel.

Outputs per file:
  outputs/figures/{stem}_echogram.png   — per-channel Sv echogram
  outputs/figures/{stem}_MVBS.png       — multi-frequency MVBS echogram

Usage:
    python scripts/03_visualize.py
"""

import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
import yaml

CONFIG_PATH = Path(__file__).parent.parent / "config.yml"

# Colour limits for Sv (dB re 1 m-1); typical range for krill/fish
SV_VMIN = -80
SV_VMAX = -30
CMAP = "RdYlBu_r"


def load_config():
    if not CONFIG_PATH.exists():
        sys.exit("config.yml not found.")
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def plot_sv_echogram(ds_Sv: xr.Dataset, stem: str, fig_dir: Path):
    """Plot per-channel Sv echogram and save as PNG."""
    channels = ds_Sv.channel.values
    n_channels = len(channels)

    fig, axes = plt.subplots(
        n_channels, 1,
        figsize=(14, 3 * n_channels),
        sharex=True,
        constrained_layout=True,
    )
    if n_channels == 1:
        axes = [axes]

    for ax, ch in zip(axes, channels):
        sv_ch = ds_Sv["Sv"].sel(channel=ch)
        # Use depth if available, else range_sample
        y_var = "depth" if "depth" in sv_ch.coords else "range_sample"
        y_label = "Depth (m)" if y_var == "depth" else "Range sample"

        sv_vals = sv_ch.values
        ping_times = ds_Sv.ping_time.values
        y_vals = sv_ch[y_var].values

        # Mask NaN for clean display
        sv_plot = np.where(np.isfinite(sv_vals), sv_vals, np.nan)

        im = ax.pcolormesh(
            ping_times,
            y_vals,
            sv_plot.T,
            vmin=SV_VMIN,
            vmax=SV_VMAX,
            cmap=CMAP,
            shading="auto",
        )
        ax.invert_yaxis()
        ax.set_ylabel(y_label)
        ax.set_title(f"Channel: {ch}", fontsize=9)
        fig.colorbar(im, ax=ax, label="Sv (dB re 1 m⁻¹)", fraction=0.02, pad=0.01)

    axes[-1].set_xlabel("Ping time (UTC)")
    fig.suptitle(f"Sv echogram — {stem}", fontsize=11, fontweight="bold")

    out_path = fig_dir / f"{stem}_echogram.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {out_path}")
    return out_path


def plot_mvbs_echogram(ds_MVBS: xr.Dataset, stem: str, fig_dir: Path):
    """Plot gridded MVBS echogram per frequency and save as PNG."""
    # Swap channel → frequency_nominal if available
    if "frequency_nominal" in ds_MVBS.coords:
        ds_plot = ds_MVBS
        dim = "frequency_nominal"
        dim_label = "Frequency (Hz)"
    else:
        ds_plot = ds_MVBS
        dim = "channel"
        dim_label = "Channel"

    freqs = ds_plot[dim].values
    n_freqs = len(freqs)

    fig, axes = plt.subplots(
        n_freqs, 1,
        figsize=(14, 3 * n_freqs),
        sharex=True,
        constrained_layout=True,
    )
    if n_freqs == 1:
        axes = [axes]

    for ax, freq in zip(axes, freqs):
        sv_f = ds_plot["Sv"].sel({dim: freq})
        y_var = "depth" if "depth" in sv_f.coords else "echo_range"
        y_label = "Depth (m)"

        im = ax.pcolormesh(
            ds_plot.ping_time.values,
            sv_f[y_var].values,
            sv_f.values.T,
            vmin=SV_VMIN,
            vmax=SV_VMAX,
            cmap=CMAP,
            shading="auto",
        )
        ax.invert_yaxis()
        ax.set_ylabel(y_label)
        freq_khz = freq / 1e3 if isinstance(freq, (int, float)) else freq
        ax.set_title(f"{freq_khz:.0f} kHz" if isinstance(freq_khz, float) else str(freq), fontsize=9)
        fig.colorbar(im, ax=ax, label="MVBS (dB re 1 m⁻¹)", fraction=0.02, pad=0.01)

    axes[-1].set_xlabel("Ping time (UTC)")
    fig.suptitle(f"MVBS echogram — {stem}", fontsize=11, fontweight="bold")

    out_path = fig_dir / f"{stem}_MVBS.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {out_path}")
    return out_path


def main():
    cfg = load_config()
    output_dir = Path(cfg["outputs"]["output_dir"])
    fig_dir = output_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    proc_manifest = output_dir / "processed.json"
    if not proc_manifest.exists():
        sys.exit(f"No processed.json in {output_dir}. Run 02_process.py first.")

    with open(proc_manifest) as f:
        results = json.load(f)

    for entry in results:
        stem = Path(entry["raw"]).stem
        print(f"\nVisualising: {stem}")

        sv_path = Path(entry["Sv_zarr"])
        mvbs_path = Path(entry["MVBS_zarr"])

        if sv_path.exists():
            ds_Sv = xr.open_zarr(str(sv_path))
            plot_sv_echogram(ds_Sv, stem, fig_dir)
        else:
            print(f"  Warning: Sv Zarr not found at {sv_path}")

        if mvbs_path.exists():
            ds_MVBS = xr.open_zarr(str(mvbs_path))
            # Try to add frequency_nominal coordinate
            if "frequency_nominal" not in ds_MVBS.coords and "channel" in ds_MVBS.dims:
                try:
                    ds_MVBS = ds_MVBS.swap_dims({"channel": "frequency_nominal"})
                except Exception:
                    pass
            plot_mvbs_echogram(ds_MVBS, stem, fig_dir)
        else:
            print(f"  Warning: MVBS Zarr not found at {mvbs_path}")

    print(f"\nFigures saved to {fig_dir}/")
    print("Next step: python scripts/04_provenance.py")


if __name__ == "__main__":
    main()
