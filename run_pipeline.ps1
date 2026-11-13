# FHF DABB-PADABB WP4 — Full acoustic pipeline (Windows PowerShell)
#
# Usage:
#   .\run_pipeline.ps1
#   .\run_pipeline.ps1 -SkipUpload     # skip ODP upload (local outputs only)
#   .\run_pipeline.ps1 -SkipDownload   # re-process already-downloaded files
#
# Prerequisites:
#   1. pip install -r requirements.txt
#   2. Copy config.example.yml to config.yml and fill in your values
#   3. $env:ODP_API_KEY = "sk_your_key_here"

param(
    [switch]$SkipDownload,
    [switch]$SkipUpload
)

$ErrorActionPreference = "Stop"

function Step($n, $label) {
    Write-Host ""
    Write-Host "[$n/6] $label" -ForegroundColor Cyan
    Write-Host ("-" * 60) -ForegroundColor DarkGray
}

# Verify config exists
if (-not (Test-Path "config.yml")) {
    Write-Host "ERROR: config.yml not found." -ForegroundColor Red
    Write-Host "       Copy config.example.yml to config.yml and fill in your values."
    exit 1
}

# Verify API key
if (-not $env:ODP_API_KEY) {
    Write-Host "ERROR: ODP_API_KEY environment variable is not set." -ForegroundColor Red
    Write-Host "       Run: `$env:ODP_API_KEY = 'sk_your_key_here'"
    exit 1
}

$start = Get-Date
Write-Host ""
Write-Host "FHF DABB-PADABB WP4 — Hydroacoustic Pipeline" -ForegroundColor Green
Write-Host "Started: $start"

if (-not $SkipDownload) {
    Step 1 "Download raw EK80 file(s) from ODP"
    python scripts/01_odp_download.py
    if ($LASTEXITCODE -ne 0) { Write-Host "Step 1 failed." -ForegroundColor Red; exit 1 }
} else {
    Write-Host "[1/6] Download skipped (--SkipDownload)" -ForegroundColor DarkGray
}

Step 2 "Calibrate → Sv + MVBS (Echopype)"
python scripts/02_process.py
if ($LASTEXITCODE -ne 0) { Write-Host "Step 2 failed." -ForegroundColor Red; exit 1 }

Step 3 "Echogram visualisation"
python scripts/03_visualize.py
if ($LASTEXITCODE -ne 0) { Write-Host "Step 3 failed." -ForegroundColor Red; exit 1 }

Step 4 "Provenance record (W3C PROV-JSON)"
python scripts/04_provenance.py
if ($LASTEXITCODE -ne 0) { Write-Host "Step 4 failed." -ForegroundColor Red; exit 1 }

Step 5 "Frequency differencing (ΔSv = Sv120 - Sv38)"
python scripts/05_freq_diff.py
if ($LASTEXITCODE -ne 0) { Write-Host "Step 5 failed." -ForegroundColor Red; exit 1 }

if (-not $SkipUpload) {
    Step 6 "Upload derived products to ODP"
    python scripts/06_odp_upload.py
    if ($LASTEXITCODE -ne 0) { Write-Host "Step 6 failed." -ForegroundColor Red; exit 1 }
} else {
    Write-Host "[6/6] Upload skipped (--SkipUpload)" -ForegroundColor DarkGray
}

$elapsed = (Get-Date) - $start
Write-Host ""
Write-Host "Pipeline complete in $([math]::Round($elapsed.TotalMinutes, 1)) minutes." -ForegroundColor Green
Write-Host "Outputs: ./outputs/   Figures: ./outputs/figures/"
