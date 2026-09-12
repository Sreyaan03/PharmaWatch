# =============================================================================
# PharmaWatch — Setup Script (Windows PowerShell)
#
# Run this ONCE after cloning the repo. It will:
#   1. Check that Docker Desktop is installed and running
#   2. Download the large data files from Google Drive
#   3. Build and start the app with Docker Compose
#
# Usage (run in PowerShell as normal user — no admin needed):
#   .\setup.ps1
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

# ── Step 2: Check / install gdown ────────────────────────────────────────────
Info "Checking for gdown (Google Drive downloader)..."
if (-not (Get-Command gdown -ErrorAction SilentlyContinue)) {
    Warn "gdown not found — installing via pip..."
    pip install -q gdown
    if ($LASTEXITCODE -ne 0) { Err "Could not install gdown. Make sure Python/pip is installed." }
}
Success "gdown is ready."

# ── Step 3: Create data directory ────────────────────────────────────────────
Info "Creating data\ directory..."
New-Item -ItemType Directory -Path "data" -Force | Out-Null
Success "data\ directory ready."

# =============================================================================
# ⚠️  FILL IN YOUR GOOGLE DRIVE FILE IDs BELOW
#
# How to get a File ID:
#   1. Upload the file to Google Drive
#   2. Right-click → Share → "Anyone with the link"
#   3. Copy the link — it looks like:
#      https://drive.google.com/file/d/FILE_ID_HERE/view?usp=sharing
#   4. Paste just the FILE_ID_HERE part below
# =============================================================================

$DUCKDB_FILE_ID   = "PASTE_YOUR_FILE_ID_HERE"   # pharmawatch_faers.duckdb (~25 MB)
$PARQUET_FILE_ID  = "PASTE_YOUR_FILE_ID_HERE"   # twosides.parquet (~14 MB)
$DRUGBANK_FILE_ID = "PASTE_YOUR_FILE_ID_HERE"   # drugbank_ddi.tsv (~44 MB)

# =============================================================================

# ── Helper: Download a file from Google Drive ─────────────────────────────────
function Download-GDrive {
    param($FileId, $Output, $Label)

    if ($FileId -eq "PASTE_YOUR_FILE_ID_HERE") {
        Warn "Skipping $Label — Google Drive file ID not set yet."
        Warn "Open setup.ps1 and fill in the file ID for: $Output"
        return
    }

    if (Test-Path $Output) {
        Success "$Label already exists — skipping download."
        return
    }

    Info "Downloading $Label..."
    gdown --id $FileId --output $Output --fuzzy
    if ($LASTEXITCODE -ne 0) { Err "Failed to download $Label. Check your file ID and sharing settings." }
    Success "Downloaded: $Output"
}

Download-GDrive $DUCKDB_FILE_ID   "data\pharmawatch_faers.duckdb"  "pharmawatch_faers.duckdb"
Download-GDrive $PARQUET_FILE_ID  "data\twosides.parquet"          "twosides.parquet"
Download-GDrive $DRUGBANK_FILE_ID "data\drugbank_ddi.tsv"          "drugbank_ddi.tsv"

# ── Step 5: Check .env file ───────────────────────────────────────────────────
Info "Checking backend\.env..."
if (-not (Test-Path "backend\.env")) {
    Warn "backend\.env not found — creating a template."
    @"
FDA_API_KEY=Rtfpkk33k8qYViDvlaO6p1ZFdZ72hcvnbyn0nCiX
ALLOWED_ORIGINS=http://localhost:5000,http://127.0.0.1:5000
"@ | Set-Content "backend\.env"
    Success "Created backend\.env with default values."
} else {
    Success "backend\.env found."
}

# ── Step 6: Build and run ─────────────────────────────────────────────────────
Write-Host ""
Info "Building Docker image and starting PharmaWatch..."
Write-Host "(First build takes ~10-15 minutes — subsequent builds are fast)" -ForegroundColor Yellow
Write-Host ""

docker compose -f docker/docker-compose.yml up --build

# Note: the above command is blocking. Press Ctrl+C to stop.
