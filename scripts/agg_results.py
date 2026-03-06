"""
Cannlytics Cannabis Results Aggregator
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2026-03-02
Updated: 2026-03-02
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:
    Aggregate AI-parsed COA cache data into a unified cannabis results
    dataset. This script:

    1. Discovers all parsed cache files in the cache directory
    2. Loads metadata and analysis caches per state
    3. Merges metadata + analysis results per pdf_hash into flat records
    4. Deduplicates by pdf_hash (SHA-256)
    5. Validates and flags data quality issues
    6. Calculates comprehensive coverage statistics
    7. Outputs a build-stage CSV for downstream enrichment

Pipeline Position:
    This is the FIRST script in the results pipeline.
    Input:  .cache/results-{state}-{analysis}-{model}.jsonl
    Output: build/cannabis-results.csv
    Next:   Enrichment, standardization, and final export to output/

Usage:
    python agg_results.py
    python agg_results.py --cache-dir "D:\\data\\.cache"
    python agg_results.py --output-dir "D:\\data\\build"
    python agg_results.py --states ca fl ny

Changelog:
    v1.2.0 (2026-03-04):
        - Added model-preference cache selection (--model flag)
        - Default preferred model: gpt-5-nano (largest caches)
        - When multiple models exist for same state+analysis, prefers
          the specified model instead of most-recently-modified file
        - Falls back to largest file by size when preferred model unavailable

    v1.1.0 (2026-03-02):
        - Restricted cache file pattern to only match known analysis types
          (fixes loading stale caches like 'historic', 'flower', 'archive')
        - Added analysis name normalization (54 AI variants → 8 canonical names)
        - Updated default build directory to D:\\data\\.build

    v1.0.0 (2026-03-02):
        - Initial version with multi-state cache aggregation
        - Metadata + analysis merging by pdf_hash
        - Deduplication and validation
        - Coverage statistics in JSON and Markdown
"""
# Standard imports:
import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# External imports:
import numpy as np
import pandas as pd


# =============================================================================
# Path Resolution
# =============================================================================

# Resolve repo root for config imports (scripts/ → repo root).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from config.results_config import PATHS as _PATHS
    DEFAULT_CACHE_DIR = str(_PATHS.cache_dir)
    DEFAULT_BUILD_DIR = str(_PATHS.build_dir)
except ImportError:
    DEFAULT_CACHE_DIR = os.environ.get('CANNLYTICS_CACHE_DIR', r'D:\data\.cache')
    DEFAULT_BUILD_DIR = os.environ.get('CANNLYTICS_BUILD_DIR', r'D:\data\.build')


# =============================================================================
# Configuration
# =============================================================================

# Version
VERSION = '1.2.0'

# Reproducibility
RANDOM_SEED = 42

# Default preferred AI model for cache file selection.
# When multiple model caches exist for the same state + analysis type,
# files matching this model name are preferred.
_DEFAULT_MODEL = 'gpt-5-nano'

# Analysis types that the parsing pipeline produces.
# Only cache files matching these types are loaded.
ANALYSIS_TYPES = [
    'metadata',
    'cannabinoids',
    'terpenes',
    'pesticides',
    'heavy_metals',
    'microbials',
    'residual_solvents',
    'moisture_foreign_matter',
]

# Cache file naming pattern:
#   results-{state}-{analysis_type}-{model}.jsonl
CACHE_FILE_PATTERN = re.compile(
    r'^results-([a-z]{2})-([a-z_]+)-([\w\-.]+)\.jsonl$'
)

# Algorithm cache file naming pattern:
#   results-{state}-algorithm.jsonl
ALGORITHM_CACHE_PATTERN = re.compile(
    r'^results-([a-z]{2})-algorithm\.jsonl$'
)


# Metadata fields extracted from the metadata cache.
# These become top-level columns in the output.
METADATA_FIELDS = [
    # Product info
    'product_name', 'strain_name', 'product_type',
    'batch_number', 'batch_size', 'sample_weight',
    # Producer info
    'producer', 'producer_street', 'producer_city',
    'producer_state', 'producer_zipcode', 'producer_license_number',
    # Distributor info
    'distributor', 'distributor_license_number',
    # Lab info
    'lab', 'lab_license_number', 'lab_address',
    'lab_city', 'lab_state', 'lab_zipcode',
    # Dates
    'date_tested', 'date_received', 'date_collected',
    # IDs
    'sample_id',
    # Totals
    'total_thc', 'total_cbd', 'total_cannabinoids', 'total_terpenes',
    # Status
    'status',
    # Analyses detected
    'analyses',
]

# Fields that indicate error records (skip these)
_EMPTY_STRS = {'', 'nan', 'none', 'null', 'inf', '-inf'}

# Columns for the final output, ordered logically.
EXPORT_COLUMNS = [
    # Identifiers
    'pdf_hash', 'sample_id', 'state', 'source',
    # Product info
    'product_name', 'strain_name', 'product_type',
    'batch_number', 'batch_size', 'sample_weight',
    # Producer info
    'producer', 'producer_street', 'producer_city',
    'producer_state', 'producer_zipcode', 'producer_license_number',
    # Distributor info
    'distributor', 'distributor_license_number',
    # Lab info
    'lab', 'lab_license_number', 'lab_address',
    'lab_city', 'lab_state', 'lab_zipcode',
    # Dates
    'date_tested', 'date_received', 'date_collected',
    # Cannabinoids (top-level summaries)
    'total_thc', 'total_cbd', 'total_cannabinoids',
    # Terpenes (top-level summary)
    'total_terpenes',
    # Contaminant statuses
    'status', 'pesticides_status', 'heavy_metals_status',
    'microbials_status', 'residual_solvents_status',
    'moisture_content', 'water_activity',
    # Analyses present
    'analyses',
    # Detailed results (JSON list)
    'results',
    # Parsing metadata
    'parsing_method', 'parsing_algorithm', 'parsing_model', 'parsing_cost',
    # Aggregation metadata
    'date_aggregated',
]

# State name mapping for display
STATE_NAMES = {
    'ak': 'Alaska', 'az': 'Arizona', 'ca': 'California', 'co': 'Colorado',
    'ct': 'Connecticut', 'fl': 'Florida', 'hi': 'Hawaii', 'ma': 'Massachusetts',
    'md': 'Maryland', 'mi': 'Michigan', 'mo': 'Missouri', 'ms': 'Mississippi',
    'nj': 'New Jersey', 'nv': 'Nevada', 'ny': 'New York', 'oh': 'Ohio',
    'or': 'Oregon', 'ri': 'Rhode Island', 'ut': 'Utah', 'vt': 'Vermont',
    'wa': 'Washington',
}

# Analysis name normalization mapping.
# AI-parsed metadata produces many variants of the same analysis type
# (e.g., "heavy metals", "heavy_metals", "metals", "trace metals").
# This map normalizes them to canonical names matching ANALYSIS_TYPES.
ANALYSIS_NAME_NORMALIZATION = {
    # ── Cannabinoids / Potency ────────────────────────────────────
    'cannabinoids': 'cannabinoids', 'potency': 'cannabinoids',
    'cannabinoid': 'cannabinoids', 'cannabinoid_profile': 'cannabinoids',
    'cannabinoid profile': 'cannabinoids', 'cannabinoid potency': 'cannabinoids',
    'potency analysis': 'cannabinoids', 'potency details': 'cannabinoids',
    'potency_details': 'cannabinoids', 'potency summary': 'cannabinoids',
    'potency_summary': 'cannabinoids',
    'potency summary (as received)': 'cannabinoids',
    # Individual cannabinoid names → cannabinoids
    'delta-9-thc': 'cannabinoids', 'delta-8-thc': 'cannabinoids',
    'delta 9-thc': 'cannabinoids', 'delta 8-thc': 'cannabinoids',
    'thca': 'cannabinoids', 'cbda': 'cannabinoids', 'cbd': 'cannabinoids',
    'cbg': 'cannabinoids', 'cbga': 'cannabinoids', 'cbn': 'cannabinoids',
    'cbc': 'cannabinoids', 'cbdv': 'cannabinoids', 'thcv': 'cannabinoids',
    'total thc': 'cannabinoids', 'total cbd': 'cannabinoids',
    'total cbg': 'cannabinoids', 'total active cannabinoids': 'cannabinoids',
    'label claim': 'cannabinoids', 'label_claim': 'cannabinoids',
    # ── Terpenes ──────────────────────────────────────────────────
    'terpenes': 'terpenes', 'terpene': 'terpenes',
    'terpene_profile': 'terpenes', 'terpene profile': 'terpenes',
    'terpenoids': 'terpenes', 'terpenoid': 'terpenes',
    'terpenes summary': 'terpenes', 'terpenes_summary': 'terpenes',
    'terpenes summary (top ten)': 'terpenes', 'terpenes (top ten)': 'terpenes',
    'terpenes panel': 'terpenes', 'terpene_testing': 'terpenes',
    'terpenes_analysis': 'terpenes',
    'flavonoids': 'terpenes',
    # ── Pesticides ────────────────────────────────────────────────
    'pesticides': 'pesticides', 'pesticide': 'pesticides',
    'gcms_pesticides': 'pesticides', 'lcms_pesticides': 'pesticides',
    'agricultural agents': 'pesticides', 'herbicides': 'pesticides',
    'pesticide screening': 'pesticides',
    # ── Heavy Metals ──────────────────────────────────────────────
    'heavy_metals': 'heavy_metals', 'heavy metals': 'heavy_metals',
    'metals': 'heavy_metals', 'trace metals': 'heavy_metals',
    'trace_metals': 'heavy_metals', 'heavy metal analysis': 'heavy_metals',
    'heavy metal': 'heavy_metals',
    # ── Microbials ────────────────────────────────────────────────
    'microbials': 'microbials', 'microbial': 'microbials',
    'microbiology': 'microbials', 'microbiological': 'microbials',
    'microbiologicals': 'microbials',
    'microbial impurities': 'microbials', 'microbial_impurities': 'microbials',
    'microbial contaminants': 'microbials', 'microbial_contaminants': 'microbials',
    'microbial analysis': 'microbials', 'pathogenic microbiology': 'microbials',
    'pathogenic_microbiology': 'microbials',
    'pathogenic': 'microbials', 'pathogens': 'microbials',
    'pathogenic microorganisms': 'microbials',
    'pathogenic (qpcr)': 'microbials', 'pathogenic_qpcr': 'microbials',
    'microbiology (qpcr)': 'microbials', 'microbiology_qpcr': 'microbials',
    'microbiology_pcr': 'microbials', 'microbiological (qpcr)': 'microbials',
    'aspergillus': 'microbials', 'salmonella': 'microbials',
    'shiga-toxin e. coli': 'microbials', 'e. coli': 'microbials',
    'mycotoxins': 'microbials', 'mycotoxin': 'microbials',
    'mycotoxin analysis': 'microbials',
    'total yeast and mold': 'microbials', 'total_yeast_and_mold': 'microbials',
    'total aerobic bacteria': 'microbials', 'total_aerobic_bacteria': 'microbials',
    # ── Residual Solvents ─────────────────────────────────────────
    'residual_solvents': 'residual_solvents', 'residual solvents': 'residual_solvents',
    'solvents': 'residual_solvents', 'residual solvent analysis': 'residual_solvents',
    'residual solvent': 'residual_solvents', 'residue_solvents': 'residual_solvents',
    # ── Moisture / Foreign Matter ─────────────────────────────────
    'moisture_foreign_matter': 'moisture_foreign_matter',
    'moisture': 'moisture_foreign_matter', 'moisture content': 'moisture_foreign_matter',
    'moisture analysis': 'moisture_foreign_matter',
    'moisture_analysis': 'moisture_foreign_matter',
    'moisture_content': 'moisture_foreign_matter',
    '% moisture': 'moisture_foreign_matter', '%_moisture': 'moisture_foreign_matter',
    'percent_moisture': 'moisture_foreign_matter',
    'moisture_percent': 'moisture_foreign_matter',
    'water activity': 'moisture_foreign_matter', 'water_activity': 'moisture_foreign_matter',
    'foreign matter': 'moisture_foreign_matter', 'foreign_matter': 'moisture_foreign_matter',
    'foreign': 'moisture_foreign_matter', 'foreign material': 'moisture_foreign_matter',
    'foreign_material': 'moisture_foreign_matter',
    'foreign matter water activity': 'moisture_foreign_matter',
    'filth & foreign': 'moisture_foreign_matter',
    'filth_and_foreign': 'moisture_foreign_matter',
    'filth and foreign': 'moisture_foreign_matter',
    'filth and foreign material': 'moisture_foreign_matter',
    'filth_and_foreign_material': 'moisture_foreign_matter',
    'filth_and_foreign_materials': 'moisture_foreign_matter',
    'filth & foreign material': 'moisture_foreign_matter',
    'filth & foreign material analysis': 'moisture_foreign_matter',
    'filth/foreign material': 'moisture_foreign_matter',
    'filth_foreign_material': 'moisture_foreign_matter',
    'filth and foreign matter': 'moisture_foreign_matter',
    'filth and foreign load': 'moisture_foreign_matter',
    'visual inspection': 'moisture_foreign_matter',
    'visual_inspection': 'moisture_foreign_matter',
    # ── Safety ────────────────────────────────────────────────────
    'safety': 'safety', 'safety analysis': 'safety',
    # ── Other / Catch-all ─────────────────────────────────────────
    'homogeneity': 'other',
    'total_contaminant_load': 'other', 'total contaminant load': 'other',
    'total contaminants': 'other', 'total_contaminants': 'other',
    'total contaminant': 'other', 'total_contaminant': 'other',
    'analysis summary': 'other', 'analysis_summary': 'other',
    'summary': 'other',
    'edibles summary': 'other',
    # ── Screen / Method suffixes ──────────────────────────────────
    'heavy_metals_screen': 'heavy_metals', 'heavy metals screen': 'heavy_metals',
    'heavy_metals_by_icpms': 'heavy_metals',
    'pesticides_screen': 'pesticides', 'pesticides screen': 'pesticides',
    'pesticides_by_lcmsms': 'pesticides',
    'pesticides, fungicides, and growth regulators': 'pesticides',
    'pesticides/fungicides and growth regulators': 'pesticides',
    'pesticides_fungicides_and_growth_regulators': 'pesticides',
    'pesticides_fungicides_growth_regulators': 'pesticides',
    'growth regulators': 'pesticides',
    'mycotoxins_screen': 'microbials', 'mycotoxins screen': 'microbials',
    'mycotoxins_by_lcmsms': 'microbials',
    'microbiological_screen': 'microbials', 'microbiological screen': 'microbials',
    'microbes_by_qpcr': 'microbials', 'microbes': 'microbials',
    'qpcr microbiology': 'microbials',
    'yeast & mold': 'microbials', 'yeast and mold': 'microbials',
    'aflatoxins': 'microbials',
    'pathogenic testing': 'microbials', 'pathogenic_testing': 'microbials',
    'pathogenic moisture': 'microbials',
    # ── Additional catch-all variants ─────────────────────────────
    'filth': 'moisture_foreign_matter',
    'filth_and_foreign_matter': 'moisture_foreign_matter',
    'moisture meter': 'moisture_foreign_matter',
    'cannabinoids potency': 'cannabinoids',
    'cannabinoid_potency': 'cannabinoids',
    'residue solvents': 'residual_solvents',
}


def normalize_analysis_name(name: str) -> str:
    """Normalize an analysis name to its canonical form.

    Args:
        name: Raw analysis name from AI parsing.

    Returns:
        Canonical analysis name (e.g., 'heavy_metals' not 'trace metals').
    """
    if not name:
        return name
    key = str(name).lower().strip()
    # Strip "name: status" patterns (e.g., "potency: completed")
    if ':' in key:
        key = key.split(':')[0].strip()
    # Strip trailing status words (e.g., "heavy metals passed")
    for suffix in (
        ' not tested', ' not applicable', ' passed', ' tested',
        ' completed', ' pass', ' fail', ' failed',
    ):
        if key.endswith(suffix):
            key = key[:-len(suffix)].strip()
            break
    # Strip parenthetical status (e.g., "homogeneity (not tested)")
    if '(' in key:
        key = key.split('(')[0].strip()
    return ANALYSIS_NAME_NORMALIZATION.get(key, key)

# Output filenames
OUTPUT_DATASET = 'cannabis-results.csv'
OUTPUT_STATS_JSON = 'cannabis-results-stats.json'
OUTPUT_STATS_MD = 'cannabis-results-details.md'


# =============================================================================
# Utilities
# =============================================================================

def get_timestamp() -> str:
    """Return current date as YYYY-MM-DD."""
    return datetime.now().strftime('%Y-%m-%d')


def pct(numerator: int, denominator: int) -> float:
    """Calculate percentage rounded to 1 decimal."""
    return round(100 * numerator / denominator, 1) if denominator > 0 else 0.0


def is_jupyter() -> bool:
    """Detect Jupyter notebook environment."""
    try:
        from IPython import get_ipython
        return get_ipython().__class__.__name__ == 'ZMQInteractiveShell'
    except Exception:
        return False


# =============================================================================
# Cache Discovery
# =============================================================================

def discover_cache_files(
        cache_dir: str,
        states: Optional[List[str]] = None,
        preferred_model: Optional[str] = None,
    ) -> Dict[str, Dict[str, str]]:
    """Discover all parsed cache files organized by state and analysis type.

    When multiple models exist for the same state + analysis type,
    the file matching ``preferred_model`` is selected. If no file
    matches the preferred model, falls back to the largest file
    (most data) rather than the most recently modified.

    Args:
        cache_dir: Path to the cache directory.
        states: Optional list of state codes to filter (e.g., ['ca', 'fl']).
            If None, discovers all available states.
        preferred_model: Preferred AI model name (e.g., 'gpt-5-nano').
            Files matching this model are chosen over all others.
            If None, defaults to _DEFAULT_MODEL ('gpt-5-nano').

    Returns:
        Nested dict: {state: {analysis_type: filepath}}
        Example: {'ca': {'metadata': 'D:/data/.cache/results-ca-metadata-gpt-5-nano.jsonl', ...}}
    """
    if preferred_model is None:
        preferred_model = _DEFAULT_MODEL

    cache_path = Path(cache_dir)
    if not cache_path.exists():
        print(f'  ERROR: Cache directory not found: {cache_dir}')
        return {}

    # First pass: collect ALL matching files grouped by (state, analysis_type).
    candidates: Dict[str, Dict[str, List[Tuple[Path, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    skipped_files = []
    known_types = set(ANALYSIS_TYPES)

    for f in sorted(cache_path.iterdir()):
        if not f.is_file():
            continue
        match = CACHE_FILE_PATTERN.match(f.name)
        if not match:
            continue

        state, analysis_type, model = match.groups()

        # Filter by requested states.
        if states and state not in states:
            continue

        # Filter by known analysis types — skip stray/legacy caches.
        if analysis_type not in known_types:
            skipped_files.append(f.name)
            continue

        candidates[state][analysis_type].append((f, model))

    # Second pass: select the best file for each (state, analysis_type).
    discovered: Dict[str, Dict[str, str]] = {}
    file_count = 0

    for state in sorted(candidates):
        discovered[state] = {}
        for analysis_type in sorted(candidates[state]):
            file_list = candidates[state][analysis_type]

            if len(file_list) == 1:
                chosen, chosen_model = file_list[0]
            else:
                # Multiple files: prefer the one matching preferred_model.
                preferred_matches = [
                    (f, m) for f, m in file_list if m == preferred_model
                ]
                if preferred_matches:
                    chosen, chosen_model = max(
                        preferred_matches, key=lambda x: x[0].stat().st_size,
                    )
                else:
                    # No preferred model match — pick the largest file.
                    chosen, chosen_model = max(
                        file_list, key=lambda x: x[0].stat().st_size,
                    )

                # Log the selection when alternatives existed.
                alt_models = [m for _, m in file_list if m != chosen_model]
                if alt_models:
                    print(f'    {state.upper()}/{analysis_type}: '
                          f'selected {chosen_model} '
                          f'(skipped: {", ".join(alt_models)})')

            discovered[state][analysis_type] = str(chosen)
            file_count += 1

    print(f'  Discovered {file_count} cache files across {len(discovered)} states '
          f'(preferred model: {preferred_model})')
    for state in sorted(discovered):
        analyses = sorted(discovered[state].keys())
        # Show the actual model selected for each file.
        details = []
        for a in analyses:
            fname = Path(discovered[state][a]).name
            m = CACHE_FILE_PATTERN.match(fname)
            model_used = m.group(2) if m else '?'
            details.append(a)
        print(f'    {state.upper()}: {", ".join(details)}')
    if skipped_files:
        print(f'  Skipped {len(skipped_files)} non-analysis cache file(s):')
        for name in skipped_files:
            print(f'    {name}')

    return discovered


def discover_algorithm_caches(
        cache_dir: str,
        states: Optional[List[str]] = None,
    ) -> Dict[str, str]:
    """Discover algorithm cache files organized by state.

    Algorithm caches follow the pattern: results-{state}-algorithm.jsonl
    Each contains flat, pre-merged records (no merge step needed).

    Args:
        cache_dir: Path to the cache directory.
        states: Optional list of state codes to filter.

    Returns:
        Dict mapping state -> filepath.
        Example: {'mo': 'D:/data/.cache/results-mo-algorithm.jsonl'}
    """
    cache_path = Path(cache_dir)
    if not cache_path.exists():
        return {}

    discovered = {}
    for f in sorted(cache_path.iterdir()):
        if not f.is_file():
            continue
        match = ALGORITHM_CACHE_PATTERN.match(f.name)
        if not match:
            continue
        state = match.group(1)
        if states and state not in states:
            continue
        discovered[state] = str(f)

    if discovered:
        print(f'  Discovered {len(discovered)} algorithm cache(s): '
              f'{", ".join(s.upper() for s in sorted(discovered))}')
        for state, path in sorted(discovered.items()):
            # Count records without fully loading.
            try:
                with open(path, 'r', encoding='utf-8') as fh:
                    count = sum(1 for line in fh if line.strip())
                print(f'    {state.upper()}: {count:,} records')
            except Exception:
                print(f'    {state.upper()}: (could not count)')

    return discovered


def load_algorithm_records(
        algo_caches: Dict[str, str],
    ) -> List[Dict]:
    """Load algorithm-parsed records from all discovered algorithm caches.

    Algorithm records are already in flat, pre-merged format (metadata +
    results combined), so they can be loaded directly without the merge
    step that AI-parsed records require.

    Args:
        algo_caches: Dict mapping state -> algorithm cache filepath.

    Returns:
        List of flat record dicts ready for aggregation.
    """
    all_records = []

    for state in sorted(algo_caches):
        path = algo_caches[state]
        print(f'\n  Loading algorithm records for {state.upper()}...')

        error_hashes: Set[str] = set()
        records = load_jsonl_cache(path, error_hashes)

        loaded = 0
        for pdf_hash, record in records.items():
            # Ensure state is set.
            record.setdefault('state', state)

            # Ensure parsing_method is tagged.
            record.setdefault('parsing_method', 'algorithm')

            # Ensure required fields exist with defaults.
            record.setdefault('parsing_cost', 0.0)
            record.setdefault('date_aggregated', get_timestamp())

            # Normalize analyses field.
            analyses_val = record.get('analyses', '[]')
            if isinstance(analyses_val, str):
                try:
                    analyses_list = json.loads(analyses_val)
                except (json.JSONDecodeError, TypeError):
                    analyses_list = []
            elif isinstance(analyses_val, list):
                analyses_list = analyses_val
            else:
                analyses_list = []

            # Normalize analysis names.
            normalized = []
            seen = set()
            for a in analyses_list:
                if not a:
                    continue
                canonical = normalize_analysis_name(str(a))
                if canonical and canonical not in seen:
                    normalized.append(canonical)
                    seen.add(canonical)
            record['analyses'] = json.dumps(normalized)

            # Ensure results is a JSON string.
            results_val = record.get('results', '[]')
            if isinstance(results_val, list):
                record['results'] = json.dumps(results_val, default=str)

            # Derive contaminant statuses from results if not already set.
            try:
                results_list = json.loads(record.get('results', '[]'))
            except (json.JSONDecodeError, TypeError):
                results_list = []

            if results_list:
                for analysis_type in ('pesticides', 'heavy_metals', 'microbials', 'residual_solvents'):
                    status_key = f'{analysis_type}_status'
                    if status_key not in record:
                        type_results = [r for r in results_list
                                        if isinstance(r, dict)
                                        and r.get('analysis') == analysis_type]
                        if type_results:
                            status = extract_contaminant_status(type_results)
                            if status:
                                record[status_key] = status

                if 'moisture_content' not in record or 'water_activity' not in record:
                    mfm_results = [r for r in results_list
                                   if isinstance(r, dict)
                                   and r.get('analysis') == 'moisture_foreign_matter']
                    if mfm_results:
                        moisture, water_activity = extract_moisture_water_activity(mfm_results)
                        if moisture is not None:
                            record.setdefault('moisture_content', moisture)
                        if water_activity is not None:
                            record.setdefault('water_activity', water_activity)

            # Infer source for algorithm records.
            algo = record.get('parsing_algorithm', 'algorithm')
            record.setdefault('source', f'algorithm-{algo}')

            all_records.append(record)
            loaded += 1

        print(f'    Loaded: {loaded:,} algorithm records')

    return all_records


# =============================================================================
# Cache Loading
# =============================================================================

def load_jsonl_cache(
        cache_path: str,
        error_hashes: Optional[Set[str]] = None,
    ) -> Dict[str, Dict]:
    """Load a JSONL cache file into a dict keyed by pdf_hash.

    Each line in the JSONL is expected to be either:
      - A dict with a single key (the pdf_hash) mapping to the data dict
      - A flat dict containing a 'pdf_hash' field

    Args:
        cache_path: Path to the JSONL cache file.
        error_hashes: Optional set to collect pdf_hashes of error records.

    Returns:
        Dict mapping pdf_hash -> record dict.
    """
    if not os.path.exists(cache_path):
        print(f'    Warning: Cache file not found: {cache_path}')
        return {}

    records = {}
    error_count = 0
    json_error_count = 0
    total_lines = 0

    with open(cache_path, 'r', encoding='utf-8') as f:
        for line in f:
            total_lines += 1
            line = line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                json_error_count += 1
                continue

            # Handle single-key wrapper: {"abc123": {...data...}}
            pdf_hash = None
            if isinstance(record, dict) and len(record) == 1:
                key, value = next(iter(record.items()))
                if isinstance(value, dict):
                    pdf_hash = key
                    if 'pdf_hash' not in value:
                        value['pdf_hash'] = key
                    record = value

            if pdf_hash is None:
                pdf_hash = record.get('pdf_hash')

            if not pdf_hash:
                continue

            # Skip error records
            error_val = record.get('error')
            if error_val is not None:
                error_str = str(error_val).strip().lower()
                if error_str not in _EMPTY_STRS:
                    error_count += 1
                    if error_hashes is not None:
                        error_hashes.add(pdf_hash)
                    continue

            records[pdf_hash] = record

    if json_error_count > 0:
        print(f'      JSON parse errors: {json_error_count}')
    if error_count > 0:
        print(f'      Error records skipped: {error_count}')

    return records


# =============================================================================
# Merging Logic
# =============================================================================

def extract_contaminant_status(
        analysis_results: List[Dict],
    ) -> Optional[str]:
    """Determine overall pass/fail from a list of analyte results.

    Args:
        analysis_results: List of result dicts, each with optional 'status' field.

    Returns:
        'pass', 'fail', or None if no status data available.
    """
    statuses = set()
    for r in analysis_results:
        s = r.get('status')
        if s:
            s_lower = str(s).lower().strip()
            if s_lower in ('fail', 'failed', 'f', 'non-compliant'):
                return 'fail'
            elif s_lower in ('pass', 'passed', 'p', 'compliant'):
                statuses.add('pass')
    return 'pass' if 'pass' in statuses else None


def extract_moisture_water_activity(
        results: List[Dict],
    ) -> Tuple[Optional[float], Optional[float]]:
    """Extract moisture content and water activity from results.

    Args:
        results: List of analyte result dicts.

    Returns:
        Tuple of (moisture_content, water_activity).
    """
    moisture = None
    water_activity = None
    for r in results:
        key = str(r.get('key', '')).lower().replace(' ', '_').replace('-', '_')
        value = r.get('value')
        if value is None or value == 0.0:
            continue
        try:
            value = float(value)
        except (ValueError, TypeError):
            continue

        if key in ('moisture_content', 'moisture', 'loss_on_drying'):
            moisture = value
        elif key in ('water_activity', 'aw', 'a_w'):
            water_activity = value

    return moisture, water_activity


def merge_state_caches(
        state: str,
        cache_files: Dict[str, str],
    ) -> List[Dict]:
    """Merge all cache files for a state into unified records.

    For each pdf_hash found in the metadata cache, this function:
    1. Extracts top-level metadata fields
    2. Collects all analysis results into a flat 'results' list
    3. Derives contaminant statuses from analysis results
    4. Attaches parsing metadata (model, cost)

    Args:
        state: Two-letter state code.
        cache_files: Dict mapping analysis_type -> filepath for this state.

    Returns:
        List of merged record dicts, one per unique pdf_hash.
    """
    print(f'\n  Processing {state.upper()} ({STATE_NAMES.get(state, state)})...')

    # Load metadata cache first — it defines the universe of records
    metadata_path = cache_files.get('metadata')
    if not metadata_path:
        print(f'    WARNING: No metadata cache for {state.upper()}. Skipping.')
        return []

    print(f'    Loading metadata...')
    error_hashes: Set[str] = set()
    metadata_records = load_jsonl_cache(metadata_path, error_hashes)
    print(f'    Metadata records: {len(metadata_records):,}')

    # Load all analysis caches
    analysis_caches: Dict[str, Dict[str, Dict]] = {}
    for analysis_type in ANALYSIS_TYPES:
        if analysis_type == 'metadata':
            continue
        path = cache_files.get(analysis_type)
        if not path:
            continue
        print(f'    Loading {analysis_type}...')
        cache = load_jsonl_cache(path, error_hashes)
        if cache:
            analysis_caches[analysis_type] = cache
            print(f'      Records: {len(cache):,}')

    # Merge: metadata + analysis results per pdf_hash
    merged_records = []
    analysis_hit_counts = defaultdict(int)

    for pdf_hash, meta in metadata_records.items():
        record = {
            'pdf_hash': pdf_hash,
            'state': state,
        }

        # Extract metadata fields
        for field in METADATA_FIELDS:
            value = meta.get(field)
            if value is not None and str(value).strip().lower() not in _EMPTY_STRS:
                record[field] = value

        # Handle analyses list (may be stored as list or string)
        analyses_val = meta.get('analyses', [])
        if isinstance(analyses_val, str):
            try:
                analyses_val = json.loads(analyses_val)
            except (json.JSONDecodeError, TypeError):
                analyses_val = [a.strip() for a in analyses_val.split(',') if a.strip()]
        # Normalize analysis names to canonical forms and deduplicate.
        if isinstance(analyses_val, list):
            normalized = []
            seen = set()
            for a in analyses_val:
                if not a:
                    continue
                # Handle dict items: {"name": "pesticides", "status": "passed"}
                if isinstance(a, dict):
                    a = a.get('name', '')
                    if not a:
                        continue
                canonical = normalize_analysis_name(a)
                if canonical and canonical not in seen:
                    normalized.append(canonical)
                    seen.add(canonical)
            record['analyses'] = normalized
        else:
            record['analyses'] = []

        # Parsing metadata
        record['parsing_model'] = meta.get('parsing_model', '')
        record['parsing_cost'] = meta.get('parsing_cost', 0.0)

        # Collect all analysis results into a single 'results' list
        all_results = []

        for analysis_type, cache in analysis_caches.items():
            analysis_data = cache.get(pdf_hash)
            if not analysis_data:
                continue

            analysis_hit_counts[analysis_type] += 1

            # The analysis cache stores results in a 'results' key
            # which is a list of analyte dicts
            results_list = analysis_data.get('results', [])

            # Handle case where results might be stored differently
            if not results_list and isinstance(analysis_data, dict):
                # Some caches store results at the top level
                # Check for a list-of-dicts pattern
                for key in ('results', 'analytes', 'data'):
                    if key in analysis_data and isinstance(analysis_data[key], list):
                        results_list = analysis_data[key]
                        break

            # Ensure each result has the analysis type tagged
            for r in results_list:
                if isinstance(r, dict):
                    if 'analysis' not in r or not r['analysis']:
                        r['analysis'] = analysis_type
                    all_results.append(r)

            # Derive contaminant statuses
            if analysis_type == 'pesticides':
                status = extract_contaminant_status(results_list)
                if status:
                    record['pesticides_status'] = status
            elif analysis_type == 'heavy_metals':
                status = extract_contaminant_status(results_list)
                if status:
                    record['heavy_metals_status'] = status
            elif analysis_type == 'microbials':
                status = extract_contaminant_status(results_list)
                if status:
                    record['microbials_status'] = status
            elif analysis_type == 'residual_solvents':
                status = extract_contaminant_status(results_list)
                if status:
                    record['residual_solvents_status'] = status
            elif analysis_type == 'moisture_foreign_matter':
                moisture, water_activity = extract_moisture_water_activity(results_list)
                if moisture is not None:
                    record['moisture_content'] = moisture
                if water_activity is not None:
                    record['water_activity'] = water_activity

        # Store results as JSON string for CSV compatibility
        record['results'] = json.dumps(all_results, default=str) if all_results else '[]'

        # Store analyses as JSON string
        record['analyses'] = json.dumps(record.get('analyses', []))

        # Aggregation timestamp
        record['date_aggregated'] = get_timestamp()

        merged_records.append(record)

    # Report analysis coverage
    print(f'    Merged records: {len(merged_records):,}')
    if analysis_hit_counts:
        print(f'    Analysis coverage:')
        for analysis_type in sorted(analysis_hit_counts):
            count = analysis_hit_counts[analysis_type]
            coverage = pct(count, len(merged_records))
            print(f'      {analysis_type}: {count:,} ({coverage}%)')

    return merged_records


# =============================================================================
# Source Inference
# =============================================================================

def infer_source_from_context(
        df: pd.DataFrame,
        cache_files: Dict[str, Dict[str, str]],
    ) -> pd.DataFrame:
    """Attempt to infer the 'source' field from cache file paths.

    The cache path often encodes the source, but the current naming
    convention uses state-level caches. When source info is available
    from the metadata records, it is preserved. Otherwise, the source
    is set to the parsing model name as a fallback.

    Args:
        df: DataFrame with merged records.
        cache_files: The discovered cache structure.

    Returns:
        DataFrame with 'source' column populated where possible.
    """
    # Source may already be in the metadata — leave those alone.
    # For records without a source, use a generic identifier.
    if 'source' not in df.columns:
        df['source'] = ''

    mask = df['source'].isna() | (df['source'] == '')
    if mask.any():
        df.loc[mask, 'source'] = df.loc[mask, 'parsing_model'].apply(
            lambda m: f'ai-parsed-{m}' if m else 'ai-parsed'
        )

    return df


# =============================================================================
# Validation
# =============================================================================

def validate_records(df: pd.DataFrame) -> pd.DataFrame:
    """Apply data quality validation rules and flag issues.

    Validation rules:
    - total_thc must be 0-100 (percent)
    - total_cbd must be 0-100 (percent)
    - total_cannabinoids must be 0-100 (percent)
    - total_terpenes must be 0-20 (percent)
    - Contaminant statuses must be 'pass', 'fail', or None

    Args:
        df: DataFrame of merged records.

    Returns:
        DataFrame with a 'validation_flags' column.
    """
    print('\nValidating records...')
    flags = []

    for idx, row in df.iterrows():
        row_flags = []

        # Validate cannabinoid percentages
        for field, max_val in [
            ('total_thc', 100), ('total_cbd', 100),
            ('total_cannabinoids', 100), ('total_terpenes', 20),
        ]:
            val = row.get(field)
            if val is not None and not pd.isna(val):
                try:
                    val = float(val)
                    if val < 0:
                        row_flags.append(f'{field}_negative')
                    elif val > max_val:
                        row_flags.append(f'{field}_exceeds_max')
                except (ValueError, TypeError):
                    row_flags.append(f'{field}_non_numeric')

        # Validate statuses
        valid_statuses = {'pass', 'fail', 'nt', 'n/a', None, '', np.nan}
        for field in ['pesticides_status', 'heavy_metals_status',
                      'microbials_status', 'residual_solvents_status']:
            val = row.get(field)
            if val is not None and not pd.isna(val):
                if str(val).lower().strip() not in valid_statuses:
                    row_flags.append(f'{field}_invalid')

        flags.append(','.join(row_flags) if row_flags else '')

    df['validation_flags'] = flags
    flagged = sum(1 for f in flags if f)
    print(f'  Records with validation flags: {flagged:,} ({pct(flagged, len(df))}%)')

    return df


# =============================================================================
# Deduplication
# =============================================================================

def deduplicate_records(df: pd.DataFrame) -> pd.DataFrame:
    """Remove duplicate records by pdf_hash.

    Since pdf_hash is a SHA-256 of the source PDF, duplicates
    indicate the same document was processed multiple times.
    Keeps the record with the most complete data.

    Args:
        df: DataFrame of merged records.

    Returns:
        Deduplicated DataFrame.
    """
    print('\nDeduplicating records...')
    before = len(df)

    if 'pdf_hash' not in df.columns or df.empty:
        print('  No deduplication possible (missing pdf_hash or empty DataFrame)')
        return df

    # Score each record by data completeness
    completeness_fields = [
        'product_name', 'producer', 'lab', 'total_thc',
        'date_tested', 'sample_id', 'batch_number',
    ]

    def completeness_score(row):
        score = 0
        for field in completeness_fields:
            val = row.get(field)
            if val is not None and not pd.isna(val) and str(val).strip() not in _EMPTY_STRS:
                score += 1
        # Also score by number of results
        results_str = row.get('results', '[]')
        try:
            results_list = json.loads(results_str) if isinstance(results_str, str) else results_str
            score += min(len(results_list), 10)  # Cap at 10 bonus points
        except (json.JSONDecodeError, TypeError):
            pass
        return score

    df['_completeness'] = df.apply(completeness_score, axis=1)
    df = df.sort_values('_completeness', ascending=False)
    df = df.drop_duplicates(subset=['pdf_hash'], keep='first')
    df = df.drop(columns=['_completeness'])

    after = len(df)
    removed = before - after
    if removed > 0:
        print(f'  Removed {removed:,} duplicate records')
    else:
        print(f'  No duplicates found')

    return df.reset_index(drop=True)


# =============================================================================
# Statistics
# =============================================================================

def calculate_statistics(df: pd.DataFrame) -> Dict:
    """Calculate comprehensive coverage statistics.

    Args:
        df: Final aggregated DataFrame.

    Returns:
        Dict of statistics suitable for JSON export.
    """
    print('\nCalculating coverage statistics...')
    total = len(df)
    if total == 0:
        print('  No records to analyze!')
        return {}

    # State coverage
    state_counts = df['state'].value_counts().to_dict()

    # Product type distribution
    product_type_counts = {}
    if 'product_type' in df.columns:
        product_type_counts = df['product_type'].dropna().value_counts().to_dict()

    # Completeness metrics
    def field_coverage(field):
        if field not in df.columns:
            return 0.0
        non_null = df[field].dropna()
        non_empty = non_null[non_null.astype(str).str.strip() != '']
        return pct(len(non_empty), total)

    # Analysis coverage (how many records have each analysis type)
    analysis_coverage = defaultdict(int)
    for _, row in df.iterrows():
        analyses_str = row.get('analyses', '[]')
        try:
            analyses_list = json.loads(analyses_str) if isinstance(analyses_str, str) else analyses_str
            if isinstance(analyses_list, list):
                for a in analyses_list:
                    # Handle dict items: {"name": "pesticides", ...}
                    if isinstance(a, dict):
                        a = a.get('name', '')
                    name = str(a).strip()
                    # Handle stringified Python dicts
                    if name.startswith('{') and 'name' in name:
                        try:
                            import ast
                            d = ast.literal_eval(name)
                            if isinstance(d, dict):
                                name = d.get('name', name)
                        except (ValueError, SyntaxError):
                            pass
                    # Normalize to canonical form
                    canonical = normalize_analysis_name(name)
                    if canonical:
                        analysis_coverage[canonical] += 1
        except (json.JSONDecodeError, TypeError):
            pass

    # Results count distribution
    results_counts = []
    for _, row in df.iterrows():
        results_str = row.get('results', '[]')
        try:
            results_list = json.loads(results_str) if isinstance(results_str, str) else results_str
            results_counts.append(len(results_list) if isinstance(results_list, list) else 0)
        except (json.JSONDecodeError, TypeError):
            results_counts.append(0)

    # Parsing cost summary
    costs = df['parsing_cost'].dropna().astype(float)
    total_cost = costs.sum()
    avg_cost = costs.mean() if len(costs) > 0 else 0

    # Flagged records
    flagged = df['validation_flags'].apply(lambda x: bool(x)).sum() if 'validation_flags' in df.columns else 0

    stats = {
        'metadata': {
            'dataset_name': 'Cannlytics Cannabis Results',
            'generated_at': get_timestamp(),
            'version': VERSION,
            'random_seed': RANDOM_SEED,
        },
        'summary': {
            'total_records': total,
            'unique_pdf_hashes': df['pdf_hash'].nunique(),
            'states_covered': len(state_counts),
            'unique_labs': int(df['lab'].dropna().nunique()) if 'lab' in df.columns else 0,
            'unique_producers': int(df['producer'].dropna().nunique()) if 'producer' in df.columns else 0,
        },
        'completeness_metrics': {
            'product_name': field_coverage('product_name'),
            'strain_name': field_coverage('strain_name'),
            'product_type': field_coverage('product_type'),
            'producer': field_coverage('producer'),
            'producer_license_number': field_coverage('producer_license_number'),
            'lab': field_coverage('lab'),
            'lab_license_number': field_coverage('lab_license_number'),
            'date_tested': field_coverage('date_tested'),
            'sample_id': field_coverage('sample_id'),
            'batch_number': field_coverage('batch_number'),
            'total_thc': field_coverage('total_thc'),
            'total_cbd': field_coverage('total_cbd'),
            'total_terpenes': field_coverage('total_terpenes'),
            'status': field_coverage('status'),
        },
        'state_coverage': {
            k: int(v) for k, v in sorted(state_counts.items(), key=lambda x: -x[1])
        },
        'product_type_distribution': {
            k: {'count': int(v), 'percentage': pct(v, total)}
            for k, v in sorted(product_type_counts.items(), key=lambda x: -x[1])
        } if product_type_counts else {},
        'analysis_coverage': {
            k: {'count': int(v), 'percentage': pct(v, total)}
            for k, v in sorted(analysis_coverage.items(), key=lambda x: -x[1])
        },
        'results_statistics': {
            'total_analyte_results': sum(results_counts),
            'avg_results_per_record': round(np.mean(results_counts), 1) if results_counts else 0,
            'median_results_per_record': int(np.median(results_counts)) if results_counts else 0,
            'max_results_per_record': max(results_counts) if results_counts else 0,
            'records_with_zero_results': sum(1 for c in results_counts if c == 0),
        },
        'parsing_costs': {
            'total_cost_usd': round(total_cost, 4),
            'avg_cost_per_record_usd': round(avg_cost, 6),
            'records_with_cost_data': int(len(costs)),
        },
        'data_quality': {
            'flagged_records': int(flagged),
            'flagged_percentage': pct(flagged, total),
            'clean_records': total - int(flagged),
            'clean_percentage': pct(total - int(flagged), total),
        },
    }

    # Print summary
    print(f'  Total records: {total:,}')
    print(f'  States: {len(state_counts)}')
    print(f'  Unique labs: {stats["summary"]["unique_labs"]}')
    print(f'  Unique producers: {stats["summary"]["unique_producers"]}')
    print(f'  Total analyte results: {sum(results_counts):,}')
    print(f'  Total parsing cost: ${total_cost:.4f}')
    print(f'  Flagged records: {flagged:,} ({pct(flagged, total)}%)')

    return stats


# =============================================================================
# Export Functions
# =============================================================================

def export_csv(
        df: pd.DataFrame,
        output_path: str,
        columns: Optional[List[str]] = None,
    ) -> None:
    """Export DataFrame to CSV.

    Args:
        df: DataFrame to export.
        output_path: Output file path.
        columns: Optional column ordering.
    """
    if df.empty:
        print(f'  Skipping export (empty DataFrame): {output_path}')
        return

    if columns is None:
        columns = EXPORT_COLUMNS

    # Use only columns that exist + any extra columns not in the predefined list
    available_cols = [c for c in columns if c in df.columns]
    extra_cols = [c for c in df.columns if c not in columns and not c.startswith('_')]
    export_df = df[available_cols + extra_cols].copy()

    # Replace NaN with empty string for cleaner CSV
    export_df = export_df.replace({np.nan: ''})

    export_df.to_csv(output_path, index=False)
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f'  Exported: {output_path} ({size_mb:.1f} MB)')


def export_statistics_json(stats: Dict, output_path: str) -> None:
    """Export statistics to JSON."""
    if not stats:
        return
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2, default=str)
    print(f'  Exported: {output_path}')


def export_statistics_markdown(stats: Dict, output_path: str) -> None:
    """Export statistics as a formatted Markdown report.

    Args:
        stats: Statistics dict from calculate_statistics().
        output_path: Output file path.
    """
    if not stats:
        return

    lines = []
    lines.append('# Cannabis Results Data Coverage Report')
    lines.append(f'\n**Generated:** {stats["metadata"]["generated_at"]}')
    lines.append(f'**Dataset:** {stats["metadata"]["dataset_name"]}')
    lines.append(f'**Version:** {stats["metadata"]["version"]}')
    lines.append('')

    # Summary
    lines.append('## Summary')
    lines.append('')
    summary = stats['summary']
    lines.append('| Metric | Value |')
    lines.append('|--------|-------|')
    lines.append(f'| **Total Records** | {summary["total_records"]:,} |')
    lines.append(f'| **Unique PDF Hashes** | {summary["unique_pdf_hashes"]:,} |')
    lines.append(f'| **States Covered** | {summary["states_covered"]} |')
    lines.append(f'| **Unique Labs** | {summary["unique_labs"]:,} |')
    lines.append(f'| **Unique Producers** | {summary["unique_producers"]:,} |')
    lines.append('')

    # Completeness
    lines.append('## Data Completeness')
    lines.append('')
    lines.append('| Field | Coverage (%) |')
    lines.append('|-------|-------------|')
    for field, coverage in stats['completeness_metrics'].items():
        display = field.replace('_', ' ').title()
        lines.append(f'| {display} | {coverage}% |')
    lines.append('')

    # State coverage
    lines.append('## State Coverage')
    lines.append('')
    lines.append('| State | Records |')
    lines.append('|-------|---------|')
    for state, count in stats['state_coverage'].items():
        name = STATE_NAMES.get(state, state.upper())
        lines.append(f'| {state.upper()} ({name}) | {count:,} |')
    lines.append('')

    # Product types
    if stats.get('product_type_distribution'):
        lines.append('## Product Type Distribution')
        lines.append('')
        lines.append('| Product Type | Count | Percentage |')
        lines.append('|-------------|-------|------------|')
        for ptype, data in stats['product_type_distribution'].items():
            lines.append(f'| {ptype} | {data["count"]:,} | {data["percentage"]}% |')
        lines.append('')

    # Analysis coverage
    if stats.get('analysis_coverage'):
        lines.append('## Analysis Coverage')
        lines.append('')
        lines.append('| Analysis Type | Records | Percentage |')
        lines.append('|--------------|---------|------------|')
        for analysis, data in stats['analysis_coverage'].items():
            lines.append(f'| {analysis} | {data["count"]:,} | {data["percentage"]}% |')
        lines.append('')

    # Results statistics
    rs = stats.get('results_statistics', {})
    if rs:
        lines.append('## Analyte Results Statistics')
        lines.append('')
        lines.append('| Metric | Value |')
        lines.append('|--------|-------|')
        lines.append(f'| Total Analyte Results | {rs.get("total_analyte_results", 0):,} |')
        lines.append(f'| Avg Results per Record | {rs.get("avg_results_per_record", 0)} |')
        lines.append(f'| Median Results per Record | {rs.get("median_results_per_record", 0)} |')
        lines.append(f'| Records with Zero Results | {rs.get("records_with_zero_results", 0):,} |')
        lines.append('')

    # Parsing costs
    pc = stats.get('parsing_costs', {})
    if pc:
        lines.append('## Parsing Costs')
        lines.append('')
        lines.append('| Metric | Value |')
        lines.append('|--------|-------|')
        lines.append(f'| Total Cost | ${pc.get("total_cost_usd", 0):.4f} |')
        lines.append(f'| Avg Cost per Record | ${pc.get("avg_cost_per_record_usd", 0):.6f} |')
        lines.append('')

    # Data quality
    dq = stats.get('data_quality', {})
    if dq:
        lines.append('## Data Quality')
        lines.append('')
        lines.append('| Metric | Value |')
        lines.append('|--------|-------|')
        lines.append(f'| Clean Records | {dq.get("clean_records", 0):,} ({dq.get("clean_percentage", 0)}%) |')
        lines.append(f'| Flagged Records | {dq.get("flagged_records", 0):,} ({dq.get("flagged_percentage", 0)}%) |')
        lines.append('')

    lines.append('---')
    lines.append(f'\n*Generated by Cannlytics Cannabis Results Aggregator v{VERSION}*')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'  Exported: {output_path}')


# =============================================================================
# Main Execution
# =============================================================================

def main(
        cache_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
        states: Optional[List[str]] = None,
        model: Optional[str] = None,
    ) -> Tuple[pd.DataFrame, Dict]:
    """Main execution function for aggregating cannabis results.

    Args:
        cache_dir: Path to the cache directory with parsed JSONL files.
        output_dir: Path for build-stage output files.
        states: Optional list of state codes to process.
        model: Preferred AI model name for cache selection
            (e.g., 'gpt-5-nano'). If None, uses default.

    Returns:
        Tuple of (aggregated DataFrame, statistics dict).
    """
    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR
    if output_dir is None:
        output_dir = DEFAULT_BUILD_DIR
    if model is None:
        model = _DEFAULT_MODEL

    print('=' * 70)
    print(f'Cannlytics Cannabis Results Aggregator v{VERSION}')
    print('=' * 70)
    print(f'Timestamp: {get_timestamp()}')
    print(f'Cache directory: {cache_dir}')
    print(f'Output directory: {output_dir}')
    print(f'Preferred model: {model}')
    if states:
        print(f'States filter: {", ".join(s.upper() for s in states)}')
    print('')

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: Discover cache files
    print('Step 1: Discovering cache files...')
    cache_files = discover_cache_files(
        cache_dir, states=states, preferred_model=model,
    )
    algo_caches = discover_algorithm_caches(cache_dir, states=states)

    if not cache_files and not algo_caches:
        print('\nERROR: No cache files found. Check cache directory path.')
        return pd.DataFrame(), {}

    # Step 2: Load and merge records per source
    print('\nStep 2: Loading and merging records...')

    # 2a: Load algorithm records (already flat, no merge needed).
    algo_records = []
    if algo_caches:
        print('\n  --- Algorithm Records ---')
        algo_records = load_algorithm_records(algo_caches)
        print(f'\n  Total algorithm records: {len(algo_records):,}')

    # 2b: Merge AI-parsed caches per state (existing logic).
    ai_records = []
    if cache_files:
        print('\n  --- AI-Parsed Records ---')
        for state in sorted(cache_files):
            records = merge_state_caches(state, cache_files[state])
            # Tag AI records with parsing_method.
            for r in records:
                r.setdefault('parsing_method', 'ai')
            ai_records.extend(records)
        print(f'\n  Total AI records: {len(ai_records):,}')

    # 2c: Combine algorithm + AI records with deduplication.
    # Priority: algorithm records are preferred when a pdf_hash exists
    # in both sources (algorithm = free, deterministic, reproducible).
    # Records unique to either source are always kept.
    if algo_records and ai_records:
        print('\n  --- Combining Sources ---')
        algo_hashes = {r['pdf_hash'] for r in algo_records if r.get('pdf_hash')}
        ai_hashes = {r['pdf_hash'] for r in ai_records if r.get('pdf_hash')}
        overlap = algo_hashes & ai_hashes
        algo_only = algo_hashes - ai_hashes
        ai_only = ai_hashes - algo_hashes

        print(f'    Algorithm-only: {len(algo_only):,}')
        print(f'    AI-only: {len(ai_only):,}')
        print(f'    Both sources (overlap): {len(overlap):,}')
        if overlap:
            print(f'    Priority: algorithm (free, deterministic)')

        # Build combined list: all algorithm records + AI-only records.
        all_records = list(algo_records)
        for r in ai_records:
            if r.get('pdf_hash') not in algo_hashes:
                all_records.append(r)

        print(f'    Combined total: {len(all_records):,}')
    elif algo_records:
        all_records = algo_records
    else:
        all_records = ai_records

    if not all_records:
        print('\nERROR: No records loaded from any state.')
        return pd.DataFrame(), {}

    # Convert to DataFrame
    df = pd.DataFrame(all_records)
    print(f'\nTotal raw records: {len(df):,}')

    # Step 3: Infer source
    print('\nStep 3: Inferring source metadata...')
    df = infer_source_from_context(df, cache_files)

    # Step 4: Deduplicate
    df = deduplicate_records(df)

    # Step 5: Validate
    df = validate_records(df)

    # Step 6: Calculate statistics
    stats = calculate_statistics(df)

    # Step 7: Export
    print('\n' + '-' * 70)
    print('Exporting files...')
    print('-' * 70)

    dataset_path = os.path.join(output_dir, OUTPUT_DATASET)
    export_csv(df, dataset_path)

    stats_json_path = os.path.join(output_dir, OUTPUT_STATS_JSON)
    export_statistics_json(stats, stats_json_path)

    stats_md_path = os.path.join(output_dir, OUTPUT_STATS_MD)
    export_statistics_markdown(stats, stats_md_path)

    # Summary
    print('')
    print('=' * 70)
    print('AGGREGATION COMPLETE')
    print('=' * 70)
    print(f'Output directory: {output_dir}')
    print(f'\nFiles generated:')
    print(f'  {OUTPUT_DATASET} ({len(df):,} records)')
    print(f'  {OUTPUT_STATS_JSON}')
    print(f'  {OUTPUT_STATS_MD}')
    print('')
    print('Next steps:')
    print('  1. Review cannabis-results-details.md for coverage report')
    print('  2. Run enrichment pipeline to standardize and enrich data')
    print('  3. Export final dataset to output/ directory')
    print('')

    return df, stats


# =============================================================================
# CLI Entry Point
# =============================================================================

if __name__ == '__main__':

    if is_jupyter():
        print('Running in Jupyter notebook mode...')
        print('To customize, call main() directly with parameters:')
        print('  main(cache_dir="...", output_dir="...", states=["ca", "fl"], model="gpt-5-nano")')
        print('')

        df, stats = main()
    else:
        parser = argparse.ArgumentParser(
            description=f'Aggregate cannabis results from parsed COA caches (v{VERSION})',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  python agg_results.py
  python agg_results.py --cache-dir "D:\\data\\.cache"
  python agg_results.py --output-dir "D:\\data\\build"
  python agg_results.py --states ca fl ny
  python agg_results.py --model gpt-5-nano
  python agg_results.py --model gpt-5-mini --states ca
  python agg_results.py --states ca --cache-dir "D:\\data\\.cache" --output-dir "D:\\output"
            """,
        )
        parser.add_argument(
            '--cache-dir', '-c',
            type=str,
            default=None,
            help=f'Cache directory with parsed JSONL files (default: {DEFAULT_CACHE_DIR})',
        )
        parser.add_argument(
            '--output-dir', '-o',
            type=str,
            default=None,
            help=f'Output directory for build-stage files (default: {DEFAULT_BUILD_DIR})',
        )
        parser.add_argument(
            '--states', '-s',
            nargs='+',
            type=str,
            default=None,
            help='State codes to process (e.g., ca fl ny). Default: all discovered.',
        )
        parser.add_argument(
            '--model',
            type=str,
            default=None,
            help=f'Preferred AI model for cache selection (default: {_DEFAULT_MODEL})',
        )

        args = parser.parse_args()

        # Normalize state codes to lowercase
        state_list = [s.lower() for s in args.states] if args.states else None

        main(
            cache_dir=args.cache_dir,
            output_dir=args.output_dir,
            states=state_list,
            model=args.model,
        )