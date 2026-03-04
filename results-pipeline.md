# Cannlytics Cannabis Results — Pipeline Reference

> **One-line summary:** Collect → Parse → Aggregate → QC → Compile → Validate → Package

This document is the **canonical source** for pipeline execution order. When in doubt, follow this.

## Pipeline Overview

The Cannlytics Cannabis Results pipeline collects cannabis Certificates of Analysis (COAs) from publicly available sources across the United States, parses them with AI vision models to extract structured lab test data, aggregates per-state caches into a unified dataset, applies comprehensive quality control, compiles a standardized 44-column dataset with a professional data dictionary, and packages everything into a delivery-ready archive with full provenance tracking.

## Prerequisites

- **Python** 3.11+ with `pandas`, `numpy`, `pdfplumber`, `beautifulsoup4`, `requests`
- **Collection:** `selenium` (for JavaScript-heavy dispensary sites), `webdriver-manager`
- **Parsing:** `openai` SDK (gpt-5-nano), `PIL`/`Pillow` for image rendering
- **QR Scanning:** `qrustie` (Rust binary, optional), `pyzbar` (fallback), `opencv-python` (optional)
- **Reporting:** LaTeX (`pdflatex`) for PDF data dictionary generation
- **API keys:** `.env` file with `OPENAI_API_KEY` (required), `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY` (optional)
- **Configuration:** `results_config.py` — all paths, state configs, constants

## Directory Structure

```
cannabis_results/
├── .cache/           JSONL caches (parsed COAs, QR scans)
├── .build/           Intermediate build artifacts
├── algorithms/              All collection algorithms
│   └── get_results_*.py  State/source-specific collectors (26+)
├── config/              Configuration modules
│   ├── driver_utils.py
│   ├── results_config.py Configuration & source definitions
│   └── results_schema*.py Analyte keys & analysis configs
├── scripts/              All pipeline scripts
│   ├── get_results_urls.py
│   ├── parse_coas.py     AI-powered COA parsing engine
│   ├── scan_qrcodes.py   QR code scanner for COA provenance
│   ├── agg_results.py    Data aggregation
│   ├── qc_results.py     Quality control
│   ├── create_results_dictionary.py   Data dictionary generation
│   ├── create_results_dataset.py      Dataset compilation
│   ├── test_results.py   Validation
│   └── package_results.py Packaging
├── data/
│   ├── all
│   └── {source}
├── package/             ZIP packages for distribution
├── tests/  
├── results_base.py
└── results-pipeline.md           ← You are here
```

## Pipeline Stages

```
┌─────────────────────────────────────────────────────────────────┐
│  Stage 1       Stage 2       Stage 2.5                         │
│  Collection →  Parsing ────→ QR Scanning (optional, parallel)  │
│  (26+ scripts) parse_coas    scan_qrcodes                      │
│                     │                                          │
│                     ▼                                          │
│               Stage 3       Stage 4       Stage 5    Stage 6   │
│               agg_results → qc_results → create_  → test_     │
│               (aggregate)   (validate)   dataset    results    │
│                                          (compile)  (verify)   │
│                                               │                │
│                                               ▼                │
│                                          Stage 7               │
│                                          package_results       │
└─────────────────────────────────────────────────────────────────┘
```

### Stage 1: Collection

Run the state- and source-specific scrapers. Each follows a four-phase architecture: **catalog** existing PDFs → **scrape** source websites for new URLs → **download** new COA PDFs → **convert** manifest to LabResult records. See `results_config.py` `STATES` for the full list.

```bash
# Examples
python algorithms/get_results_fl_kaycha.py
python algorithms/get_results_ca_flower_co.py
python algorithms/get_results_fl_flowery.py --catalog-only   # Just inventory, no network
```

**Output:** COA PDFs in `data/{state-name}/results/pdfs/{source}/`, manifests in `data/{state-name}/results/datasets/`.

### Stage 2: Parsing

```bash
python scripts/parse_coas.py --state fl --max-parses 500
python scripts/parse_coas.py --state ca --provider openai
python scripts/parse_coas.py --all --budget 5.00
python scripts/parse_coas.py --state ny --cache-stats      # View stats only
python scripts/parse_coas.py --state ny --dry-run           # Preview only
```

**Input:** COA PDFs from Stage 1
**Output:** JSONL cache files per state and analysis type in `.cache/`:
`results-{state}-metadata.jsonl`, `results-{state}-cannabinoids.jsonl`, `results-{state}-terpenes.jsonl`, `results-{state}-pesticides.jsonl`, `results-{state}-heavy_metals.jsonl`, `results-{state}-microbials.jsonl`, `results-{state}-residual_solvents.jsonl`, `results-{state}-moisture_foreign_matter.jsonl`

AI-powered extraction using vision models (primarily gpt-5-nano at ~$0.003/COA). Multi-prompt approach: metadata prompt + per-analysis prompts. Supports single-page fast path for simple COAs.

### Stage 2.5: QR Code Scanning (Optional)

```bash
python scripts/scan_qrcodes.py --state ny
python scripts/scan_qrcodes.py --state fl --source flowery
python scripts/scan_qrcodes.py --state ca --max-scans 50
python scripts/scan_qrcodes.py --state ny --export           # Export URLs to CSV
```

**Input:** COA PDFs from Stage 1
**Output:** `.cache/qr-scan-{state}.jsonl`
Runs in parallel with parsing. Two-phase detection: full-page decode via Rust `qrustie` (or Python `pyzbar` fallback) → OpenCV region-crop decode for small QR codes. Free — local processing only.

### Stage 3: Aggregation

```bash
python agg_results.py
python agg_results.py --states ca fl ny              # Specific states only
python agg_results.py --cache-dir /path/to/cache     # Custom paths
```

**Input:** All `.cache/results-{state}-{analysis}.jsonl` files
**Output:** `.build/cannabis-results.csv` + `.build/cannabis-results-stats.json` + `.build/cannabis-results-details.md`
Discovers cache files, loads metadata as record skeleton, merges each analysis cache by `pdf_hash`, derives contaminant statuses from analyte results, deduplicates by SHA-256 PDF hash.

### Stage 4: Quality Control

```bash
python qc_results.py
python qc_results.py --quiet                         # Suppress progress
python qc_results.py --report build/qc-report.json   # Save report
```

**Input/Output:** `.build/cannabis-results.csv` (in-place)
Runs 18 validation rules: string cleanup, product/strain name cleaning, product type and status normalization, date validation, numeric range correction (auto-converts mg/g → %), lab/producer name standardization, state validation, zip code cleaning, deduplication, analysis name normalization (140+ variants → 9 canonical types), JSON integrity, cross-field consistency, completeness scoring. Idempotent.

### Stage 5: Dataset Compilation

```bash
python create_results_dataset.py
python create_results_dataset.py --no-dictionary     # Skip PDF generation
python create_results_dataset.py --no-versioned      # Skip dated copy
```

**Input:** `.build/cannabis-results.csv` (post-QC)
**Output:** `.output/cannabis-results-latest.csv` (44 columns), versioned copy `cannabis-results-{date}.csv`, `cannabis-results-statistics.{json,md}`, `documents/build/cannabis-results-data-dictionary.pdf`
Selects and orders 44 delivery columns, sorts by state → date → product name, generates coverage statistics, compiles LaTeX data dictionary with cover page.

### Stage 6: Validation

```bash
python test_results.py
python test_results.py --strict                      # Warnings → errors
python test_results.py --csv output/custom.csv       # Custom path
```

**Input:** `.output/cannabis-results-latest.csv`
Runs 35 automated checks: UTF-8 encoding, column schema (44 columns in order), required field coverage, state/product/status canonical values, numeric ranges, JSON structure, date formats, THC ≤ cannabinoids consistency, sort order, deduplication, data freshness, distribution sanity. **Read-only** — does not modify the dataset.

### Stage 7: Packaging

```bash
python scripts/package_results.py
python scripts/package_results.py --date 2026-03-03
python scripts/package_results.py --verify-only package/cannlytics-cannabis-results-2026-03-03.zip
```

**Input:** Final CSV, data dictionary PDF, statistics files, README
**Output:** `delivery/cannlytics-cannabis-results-{date}.zip` containing `cannabis-results.csv`, `cannabis-results-data-dictionary.pdf`, `cannabis-results-statistics.json`, `cannabis-results-statistics.md`, `README.md`, `MANIFEST.txt` (SHA-256 checksums).

## Common Workflows

### Weekly Refresh (Collection Through Delivery)

```bash
# Stage 1: Collect new COAs
python algorithms/get_results_fl_kaycha.py
python algorithms/get_results_fl_flowery.py
python algorithms/get_results_ca_flower_co.py

# Stage 2: Parse new COAs
python scripts/parse_coas.py --state fl --max-parses 500
python scripts/parse_coas.py --state ca --max-parses 500

# Stage 2.5: QR scanning (optional)
python scripts/scan_qrcodes.py --state fl
python scripts/scan_qrcodes.py --state ca

# Stages 3-7: Build pipeline
python scripts/agg_results.py
python scripts/qc_results.py
python scripts/create_results_dataset.py
python scripts/test_results.py
python scripts/package_results.py
```

### Re-Compile Only (No Collection or Parsing)

When you only need to regenerate output from existing caches:

```bash
python scripts/agg_results.py
python scripts/qc_results.py
python scripts/create_results_dataset.py
python scripts/test_results.py
python scripts/package_results.py
```
