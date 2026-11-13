# FHF DABB-PADABB WP4 — Hydroacoustic Workflow Demo

Reproducible hydroacoustic pipeline: pulls raw EK80 echosounder files from the
[Ocean Data Platform (ODP)](https://hub.ocean.earth), processes them with
[Echopype](https://echopype.readthedocs.io), produces echogram visualisations and
frequency-differenced krill/fish classification, and uploads results back to ODP
with a full W3C PROV-JSON provenance record.

**One 30-minute EK80 file → Sv echogram → MVBS → ΔSv (krill/fish) → ODP tabular map layer**

---

## What this demonstrates

| Step | Script | Output |
|---|---|---|
| Download raw EK80 file from ODP | `01_odp_download.py` | `data/*.raw` |
| Calibrate → Sv, MVBS (Echopype) | `02_process.py` | `outputs/*_Sv.zarr`, `*_MVBS.zarr` |
| Echogram visualisation (PNG) | `03_visualize.py` | `outputs/figures/*_echogram.png`, `*_MVBS.png` |
| W3C PROV-JSON provenance record | `04_provenance.py` | `outputs/*_provenance.json` |
| Frequency differencing ΔSv = Sv₁₂₀ − Sv₃₈ | `05_freq_diff.py` | `outputs/*_dSv.zarr`, `*_track.geojson`, `*_track_dSv.csv` |
| Upload derived products to ODP | `06_odp_upload.py` | tabular map layer + file sidecars on ODP |

---

## Prerequisites

- **Python 3.10+**
- **ODP API key** — sign in at [hub.ocean.earth](https://hub.ocean.earth) and generate a key under your profile settings
- Access to the Aker BioMarine EK60/EK80 collection on ODP (request via HUB Ocean if needed)

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note:** zarr is pinned to `<3.4` due to a breaking change in the `compressor` API that
> affects Echopype. Do not upgrade zarr beyond the pinned range without testing first.

### 2. Configure

```bash
cp config.example.yml config.yml
```

Edit `config.yml`:

```yaml
odp:
  collection_id: "85b8d710-6865-45ea-b287-7c8215c5bf95"   # Antarctic Sea 2025
  n_files: 1                                                # files to download per run

upload:
  dataset_id: "42b21369-1558-44d1-942e-b1d680baa2cd"      # your ODP project dataset
```

`config.yml` is gitignored — **never commit it** (it may contain your API key path).

### 3. Set your ODP API key

**Windows (PowerShell):**
```powershell
$env:ODP_API_KEY = "sk_your_key_here"
```

**Mac / Linux:**
```bash
export ODP_API_KEY="sk_your_key_here"
```

---

## Run the full pipeline

### One-shot (recommended)

**Windows:**
```powershell
.\run_pipeline.ps1
```

**Mac / Linux:**
```bash
bash run_pipeline.sh
```

### Step by step

```bash
python scripts/01_odp_download.py
python scripts/02_process.py
python scripts/03_visualize.py
python scripts/04_provenance.py
python scripts/05_freq_diff.py
python scripts/06_odp_upload.py
```

Each script reads `config.yml` and writes to `outputs/`. Run them in order; each step
depends on the previous step's output.

---

## Outputs explained

```
data/
  AKBM-AntarcticSea-2025-*.raw          raw EK80 binary (Simrad format, ~105 MB per 30-min file)
  manifest.json                          list of downloaded files

outputs/
  *_Sv.zarr/                            calibrated volume backscattering strength (all channels)
  *_MVBS.zarr/                          mean Sv gridded to 1 m depth × 5 s time bins
  *_dSv.zarr/                           ΔSv = Sv(120 kHz) − Sv(38 kHz) — krill/fish layer
  *_track_dSv.csv                       vessel track + ΔSv per ping (ODP tabular ingest format)
  *_provenance.json                     W3C PROV-JSON lineage record (raw → Sv → MVBS → ΔSv)

  figures/
    *_echogram.png                      raw Sv echogram, both frequencies
    *_MVBS.png                          gridded MVBS echogram, both frequencies
    *_dSv.png                           ΔSv echogram + depth-averaged krill/fish profile
    *_dSv_map.png                       vessel GPS track coloured by mean ΔSv
    *_track.geojson                     GeoJSON version of the GPS track
```

---

## Understanding the outputs

### Frequency differencing (ΔSv)

| ΔSv value | Interpretation |
|---|---|
| > 10 dB | Strong krill signature (120 kHz >> 38 kHz) |
| 2 – 10 dB | Possible krill / mixed |
| < 2 dB | Fish or non-krill (swimbladder, 38 kHz dominant) |

Reference: Madureira et al. 1993; Everson et al. 1999; standard CCAMLR methodology.

### The echograms

- **Top panel**: 38 kHz — deeper penetration, fish / large aggregations
- **Bottom panel**: 120 kHz — more sensitive to krill and diving air-breathers
- **Arch features**: diving penguins, fur seals, or whales (strong 120 kHz signature)
- **Horizontal red band**: seafloor return

---

## ODP integration

After `06_odp_upload.py` runs, the following are stored on ODP under your project dataset:

| Artefact | Purpose |
|---|---|
| Tabular track (333 rows) | Map explorer — vessel track coloured by ΔSv |
| `*_track.geojson` | GIS sidecar (QGIS, Mapbox, Leaflet) |
| `*_provenance.json` | STAC lineage sidecar |
| `*_dSv.zarr.zip` | ΔSv derived product (re-downloadable) |
| `*_MVBS.zarr.zip` | MVBS derived product (re-downloadable) |

The tabular track renders on the ODP map explorer once the geometry column is indexed
(typically within a few minutes of upload).

---

## Scaling to more data

The pipeline is designed to scale. To process more files, change `n_files` in `config.yml`:

```yaml
odp:
  n_files: 10   # download and process 10 files
```

To process a different dataset (e.g. Endurance 2024), change `collection_id`:

```yaml
odp:
  collection_id: "8b9dba46-2665-481b-a4a1-0f364ca83a98"  # Endurance 2024
```

See `config.example.yml` for a list of known dataset UUIDs.

---

## Data licence

The source EK80 raw files are published by Aker BioMarine under
**ODC-BY 1.0** — attribution required. Derived products (Sv, MVBS, ΔSv) produced
by this pipeline inherit this licence. Cite as:

> Aker BioMarine (2025). EK60/EK80 Echosounder Data, Antarctic Season 2025.
> Ocean Data Platform. https://hub.ocean.earth

---

## Project context

Built for **FHF DABB-PADABB WP4 — Cloud and Data Platform** (HUB Ocean, 2026).
Demonstrates that ODP is not just a storage layer but a compute-ready platform
capable of producing CCAMLR-reportable acoustic science products.

**Key dependencies:**
- [Echopype](https://github.com/OSOceanAcoustics/echopype) — EK80 parsing and calibration
- [ODP SDK](https://github.com/C4IROcean/odp-sdk-python) — ODP authentication and data access
- [PyArrow](https://arrow.apache.org/docs/python/) — tabular schema for ODP ingest
- [Shapely](https://shapely.readthedocs.io/) — WKT geometry conversion
