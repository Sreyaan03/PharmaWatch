# =============================================================================
# PharmaWatch — Setup Script (Windows PowerShell)
#
# Run this ONCE after cloning the repo. It will:
#   1. Check that Docker Desktop is installed and running
#   2. Create backend\.env from the generated template (if missing)
#   3. Download and load the FAERS data the app needs (real FDA files)
#   4. Build and start the core stack with Docker Compose
#
# Usage (run in PowerShell as a normal user — no admin needed):
#   .\setup.ps1
#   $env:FAERS_QUARTERS = "8"; .\setup.ps1    # fewer quarters = faster, smaller download
#
# If you get a "script blocked" error, run this first:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
# =============================================================================

$ErrorActionPreference = "Stop"

# ── Colours ───────────────────────────────────────────────────────────────────
function Info    { param($msg) Write-Host "[INFO]  $msg" -ForegroundColor Cyan    }
function Success { param($msg) Write-Host "[OK]    $msg" -ForegroundColor Green   }
function Warn    { param($msg) Write-Host "[WARN]  $msg" -ForegroundColor Yellow  }
function Err     { param($msg) Write-Host "[ERROR] $msg" -ForegroundColor Red; exit 1 }

$FaersQuarters = if ($env:FAERS_QUARTERS) { $env:FAERS_QUARTERS } else { "20" }

Write-Host ""
Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║       PharmaWatch — Setup Script         ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: Check Docker ──────────────────────────────────────────────────────
Info "Checking for Docker..."
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Err "Docker is not installed. Download it from https://www.docker.com/products/docker-desktop/"
}
try {
    docker info 2>&1 | Out-Null
} catch {
    Err "Docker is installed but not running. Please start Docker Desktop and re-run this script."
}
Success "Docker is running."

# ── Step 2: Check .env file ───────────────────────────────────────────────────
Info "Checking backend\.env..."
if (-not (Test-Path "backend\.env")) {
    Warn "backend\.env not found — creating it from .env.example."
    if (-not (Test-Path ".env.example")) {
        Push-Location backend; python -m core.config; Pop-Location
        if ($LASTEXITCODE -ne 0) { Err "Could not generate .env.example" }
    }
    Copy-Item -Path ".env.example" -Destination "backend\.env"
    Warn "Created backend\.env. Edit it to add FDA_API_KEY and, for production, DOMAIN_NAME / FLASK_SECRET_KEY."
} else {
    Success "backend\.env found."
}

# ── Step 3: Download and load FAERS data ──────────────────────────────────────
# The app reads a DuckDB database built from the FDA's published FAERS quarterly files.
# This replaces the old "download a prebuilt file from Google Drive" step: the data is
# fetched straight from the FDA and built locally, so there is nothing to host or share.
Info "Creating data\ directory..."
New-Item -ItemType Directory -Path "data" -Force | Out-Null
Success "data\ directory ready."

if (Test-Path "data\pharmawatch_faers.duckdb") {
    Success "FAERS database already present — skipping download. Delete it to rebuild."
} else {
    Info "Downloading the $FaersQuarters most recent FAERS quarters from the FDA (this can take a while)..."
    Push-Location backend
    try {
        python -m faers.downloader --latest $FaersQuarters
        if ($LASTEXITCODE -ne 0) { throw "download failed" }
        python -m faers.parser --all
        if ($LASTEXITCODE -ne 0) { throw "parse failed" }
        python -m faers.loader
        if ($LASTEXITCODE -ne 0) { throw "load failed" }
    } catch {
        Pop-Location
        Err "FAERS download/load failed. Re-run setup.ps1 to resume (verified files are not re-downloaded)."
    }
    Pop-Location
    Success "FAERS database built at data\pharmawatch_faers.duckdb."
}

# The DDI datasets (twosides.parquet, drugbank_ddi.tsv) and trained models are produced by the
# validation/training steps in the docs; the app runs without them, with DDI features disabled.
if (-not (Test-Path "data\twosides.parquet")) { Warn "data\twosides.parquet not present — interaction features will be limited." }
if (-not (Test-Path "data\drugbank_ddi.tsv")) { Warn "data\drugbank_ddi.tsv not present — interaction features will be limited." }

# ── Step 4: Build and run the core stack ──────────────────────────────────────
Write-Host ""
Info "Building the image and starting the core stack (app + MySQL + Caddy)..."
Write-Host "(First build takes ~10-15 minutes — subsequent builds are fast)" -ForegroundColor Yellow
Write-Host ""

docker compose -f docker/compose.yml --profile core up --build

# Note: the above command is blocking. Press Ctrl+C to stop.
# To also run the live Kafka ingestion stream, add:  --profile kafka
