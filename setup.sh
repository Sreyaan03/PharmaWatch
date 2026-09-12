#!/usr/bin/env bash
# =============================================================================
# PharmaWatch — Setup Script (Linux / macOS)
#
# Run this ONCE after cloning the repo. It will:
#   1. Check that Docker is installed
#   2. Download the large data files from Google Drive
#   3. Build and start the app with Docker Compose
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
# =============================================================================

set -e  # Exit immediately on any error

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $1"; }
success() { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║       PharmaWatch — Setup Script         ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── Step 1: Check Docker ──────────────────────────────────────────────────────
info "Checking for Docker..."
if ! command -v docker &>/dev/null; then
  error "Docker is not installed. Download it from https://www.docker.com/products/docker-desktop/"
fi
if ! docker info &>/dev/null; then
  error "Docker is installed but not running. Please start Docker Desktop and re-run this script."
fi
success "Docker is running."

# ── Step 2: Check / install gdown ────────────────────────────────────────────
info "Checking for gdown (Google Drive downloader)..."
if ! command -v gdown &>/dev/null; then
  warn "gdown not found — installing via pip..."
  pip install -q gdown || pip3 install -q gdown || error "Could not install gdown. Run: pip install gdown"
fi
success "gdown is ready."

# ── Step 3: Create data directory ────────────────────────────────────────────
info "Creating data/ directory..."
mkdir -p data
success "data/ directory ready."

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

DUCKDB_FILE_ID="PASTE_YOUR_FILE_ID_HERE"          # pharmawatch_faers.duckdb (~25 MB)
PARQUET_FILE_ID="PASTE_YOUR_FILE_ID_HERE"          # twosides.parquet (~14 MB)
DRUGBANK_FILE_ID="PASTE_YOUR_FILE_ID_HERE"         # drugbank_ddi.tsv (~44 MB)

# =============================================================================

# ── Step 4: Download data files ───────────────────────────────────────────────
download_file() {
  local file_id="$1"
  local output="$2"
  local label="$3"

  if [[ "$file_id" == "PASTE_YOUR_FILE_ID_HERE" ]]; then
    warn "Skipping $label — Google Drive file ID not set yet."
    warn "Open setup.sh and fill in the file ID for: $output"
    return
  fi

  if [[ -f "$output" ]]; then
    success "$label already exists — skipping download."
    return
  fi

  info "Downloading $label..."
  gdown --id "$file_id" --output "$output" --fuzzy
  success "Downloaded: $output"
}

download_file "$DUCKDB_FILE_ID"   "data/pharmawatch_faers.duckdb"  "pharmawatch_faers.duckdb"
download_file "$PARQUET_FILE_ID"  "data/twosides.parquet"          "twosides.parquet"
download_file "$DRUGBANK_FILE_ID" "data/drugbank_ddi.tsv"          "drugbank_ddi.tsv"

# ── Step 5: Check .env file ───────────────────────────────────────────────────
info "Checking backend/.env..."
if [[ ! -f "backend/.env" ]]; then
  warn "backend/.env not found — creating a template."
  cat > backend/.env <<EOF
FDA_API_KEY=Rtfpkk33k8qYViDvlaO6p1ZFdZ72hcvnbyn0nCiX
ALLOWED_ORIGINS=http://localhost:5000,http://127.0.0.1:5000
EOF
  success "Created backend/.env with default values."
else
  success "backend/.env found."
fi

# ── Step 6: Build and run ─────────────────────────────────────────────────────
echo ""
info "Building Docker image and starting PharmaWatch..."
echo -e "${YELLOW}(First build takes ~10–15 minutes — subsequent builds are fast)${NC}"
echo ""

docker compose -f docker/docker-compose.yml up --build

# Note: the above command is blocking. Ctrl+C to stop.
