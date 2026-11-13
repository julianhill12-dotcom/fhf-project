#!/usr/bin/env bash
# FHF DABB-PADABB WP4 — Full acoustic pipeline (Mac / Linux)
#
# Usage:
#   bash run_pipeline.sh
#   bash run_pipeline.sh --skip-upload     # local outputs only
#   bash run_pipeline.sh --skip-download   # re-process existing files
#
# Prerequisites:
#   1. pip install -r requirements.txt
#   2. cp config.example.yml config.yml  (then edit)
#   3. export ODP_API_KEY="sk_your_key_here"

set -euo pipefail

SKIP_DOWNLOAD=0
SKIP_UPLOAD=0

for arg in "$@"; do
  case $arg in
    --skip-download) SKIP_DOWNLOAD=1 ;;
    --skip-upload)   SKIP_UPLOAD=1 ;;
  esac
done

step() { echo ""; echo "[$1/6] $2"; echo "------------------------------------------------------------"; }

# Checks
if [ ! -f config.yml ]; then
  echo "ERROR: config.yml not found."
  echo "       Copy config.example.yml to config.yml and fill in your values."
  exit 1
fi

if [ -z "${ODP_API_KEY:-}" ]; then
  echo "ERROR: ODP_API_KEY not set."
  echo "       Run: export ODP_API_KEY='sk_your_key_here'"
  exit 1
fi

START=$(date +%s)
echo ""
echo "FHF DABB-PADABB WP4 — Hydroacoustic Pipeline"
echo "Started: $(date)"

if [ $SKIP_DOWNLOAD -eq 0 ]; then
  step 1 "Download raw EK80 file(s) from ODP"
  python scripts/01_odp_download.py
else
  echo "[1/6] Download skipped (--skip-download)"
fi

step 2 "Calibrate → Sv + MVBS (Echopype)"
python scripts/02_process.py

step 3 "Echogram visualisation"
python scripts/03_visualize.py

step 4 "Provenance record (W3C PROV-JSON)"
python scripts/04_provenance.py

step 5 "Frequency differencing (ΔSv = Sv120 - Sv38)"
python scripts/05_freq_diff.py

if [ $SKIP_UPLOAD -eq 0 ]; then
  step 6 "Upload derived products to ODP"
  python scripts/06_odp_upload.py
else
  echo "[6/6] Upload skipped (--skip-upload)"
fi

ELAPSED=$(( $(date +%s) - START ))
echo ""
echo "Pipeline complete in $(( ELAPSED / 60 ))m $(( ELAPSED % 60 ))s."
echo "Outputs: ./outputs/   Figures: ./outputs/figures/"
