#!/usr/bin/env bash
# =============================================================================
# PharmaWatch — Setup Script (Linux / macOS)
#
# Run this ONCE after cloning the repo. It will:
#   1. Check that Docker is installed and running
#   2. Create backend/.env from the generated template (if missing)
#   3. Download and load the FAERS data the app needs (real FDA files)
#   4. Build and start the core stack with Docker Compose
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh                 # default: 20 most recent FAERS quarters
#   FAERS_QUARTERS=8 ./setup.sh # fewer quarters = faster, smaller download
# =============================================================================

set -e  # Exit immediately on any error

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $1"; }
success() { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

FAERS_QUARTERS="${FAERS_QUARTERS:-20}"   # how many recent quarters to fetch

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

# ── Step 2: Check .env file ───────────────────────────────────────────────────
info "Checking backend/.env..."
if [[ ! -f "backend/.env" ]]; then
  warn "backend/.env not found — creating it from .env.example."
  if [[ ! -f ".env.example" ]]; then
    (cd backend && python -m core.config) || error "Could not generate .env.example"
  fi
  cp .env.example backend/.env
  warn "Created backend/.env. Edit it to add FDA_API_KEY and, for production, DOMAIN_NAME / FLASK_SECRET_KEY."
else
  success "backend/.env found."
fi

# ── Step 3: Download and load FAERS data ──────────────────────────────────────
# The app reads a DuckDB database built from the FDA's published FAERS quarterly files.
# This replaces the old "download a prebuilt file from Google Drive" step: the data is
# fetched straight from the FDA and built locally, so there is nothing to host or share.
info "Creating data/ directory..."
mkdir -p data
success "data/ directory ready."

if [[ -f "data/pharmawatch_faers.duckdb" ]]; then
  success "FAERS database already present — skipping download. Delete it to rebuild."
else
  info "Downloading the $FAERS_QUARTERS most recent FAERS quarters from the FDA (this can take a while)..."
  ( cd backend \
    && python -m faers.downloader --latest "$FAERS_QUARTERS" \
    && python -m faers.parser --all \
    && python -m faers.loader ) \
    || error "FAERS download/load failed. Re-run setup.sh to resume (verified files are not re-downloaded)."
  success "FAERS database built at data/pharmawatch_faers.duckdb."
fi

# The DDI datasets (twosides.parquet, drugbank_ddi.tsv) and trained models are produced by the
# validation/training steps in the docs; the app runs without them, with DDI features disabled.
[[ -f "data/twosides.parquet" ]]  || warn "data/twosides.parquet not present — interaction features will be limited."
[[ -f "data/drugbank_ddi.tsv" ]]  || warn "data/drugbank_ddi.tsv not present — interaction features will be limited."

# ── Step 4: Build and run the core stack ──────────────────────────────────────
echo ""
info "Building the image and starting the core stack (app + MySQL + Caddy)..."
echo -e "${YELLOW}(First build takes ~10–15 minutes — subsequent builds are fast)${NC}"
echo ""

docker compose -f docker/compose.yml --profile core up --build

# Note: the above command is blocking. Ctrl+C to stop.
# To also run the live Kafka ingestion stream, add:  --profile kafka
