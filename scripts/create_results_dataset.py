"""
Cannlytics Cannabis Results -- Dataset Compilation
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2026-03-02
Updated: 2026-03-02
License: MIT

Description:
    Final dataset compilation for the cannabis results pipeline. Reads
    the QC-validated build file, applies final standardization, selects
    and orders delivery columns, computes comprehensive coverage
    statistics, exports versioned CSV with companion JSON / Markdown
    reports, and optionally generates a LaTeX data dictionary.

Pipeline Position:
    Stage 3 of 4 -- Final compilation
    Runs AFTER:  qc_results.py (quality control)
    Runs BEFORE: analyze_results.py (validation reporting)

    ┌────────────┐     ┌──────────────┐     ┌─────────────────────┐     ┌────────────────┐
    │ Stage 1    │     │ Stage 2      │     │ Stage 3             │     │ Stage 4        │
    │ agg_results│ ──▶ │ qc_results   │ ──▶ │ create_results_     │ ──▶ │ analyze_results│
    │ (aggregate)│     │ (quality ctl)│     │ dataset (compile)   │     │ (validation)   │
    └────────────┘     └──────────────┘     └─────────────────────┘     └────────────────┘

Input Files:
    - .build/cannabis-results.csv        -- QC-validated results data

Output Files:
    - output/cannabis-results-latest.csv             -- Final deliverable
    - output/cannabis-results-{date}.csv             -- Versioned copy
    - output/cannabis-results-statistics.json         -- Coverage stats (JSON)
    - output/cannabis-results-statistics.md           -- Coverage report (MD)
    - documents/cannabis-results-data-dictionary.tex  -- LaTeX data dictionary
    - documents/build/...pdf                          -- Compiled PDF (optional)

Dependencies:
    - pandas, numpy
    - create_results_dictionary.py (optional, for data dictionary)

Usage:
    python create_results_dataset.py
    python create_results_dataset.py --input .build/cannabis-results.csv
    python create_results_dataset.py --output output/custom-output.csv
    python create_results_dataset.py --no-versioned --no-dictionary
"""

# Standard library
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Third-party
import pandas as pd
import numpy as np


# =============================================================================
# Path Resolution
# =============================================================================

# Resolve repo root for config imports (scripts/ -> repo root).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Ensure sibling scripts are importable (for create_results_dictionary).
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

try:
    from config.results_config import PATHS as _PATHS
    DEFAULT_BUILD_DIR = str(_PATHS.build_dir)
    DEFAULT_OUTPUT_DIR = str(_PATHS.output_dir)
except ImportError:
    DEFAULT_BUILD_DIR = os.environ.get(
        'CANNLYTICS_BUILD_DIR', r'D:\data\.build',
    )
    DEFAULT_OUTPUT_DIR = os.environ.get(
        'CANNLYTICS_OUTPUT_DIR', r'D:\data\.output',
    )

# Data dictionary generator (optional -- graceful fallback)
try:
    from create_results_dictionary import generate_data_dictionary
    DATA_DICTIONARY_AVAILABLE = True
except ImportError:
    DATA_DICTIONARY_AVAILABLE = False


# =============================================================================
# Version
# =============================================================================

__version__ = '1.0.0'


# =============================================================================
# Default Paths
# =============================================================================

DEFAULT_INPUT_PATH = os.path.join(DEFAULT_BUILD_DIR, 'cannabis-results.csv')
DEFAULT_OUTPUT_PATH = os.path.join(
    DEFAULT_OUTPUT_DIR, 'cannabis-results-latest.csv',
)


# =============================================================================
# Delivery Columns
# =============================================================================
#
# The canonical, ordered set of columns that appear in the final
# deliverable CSV. This is the single source of truth for column
# selection and ordering.

DELIVERY_COLUMNS: List[str] = [
    # ── Identifiers ──────────────────────────────────────────────
    'pdf_hash',
    'sample_id',
    'state',
    'source',

    # ── Product Information ──────────────────────────────────────
    'product_name',
    'strain_name',
    'product_type',
    'batch_number',
    'batch_size',
    'sample_weight',

    # ── Producer Information ─────────────────────────────────────
    'producer',
    'producer_street',
    'producer_city',
    'producer_state',
    'producer_zipcode',
    'producer_license_number',

    # ── Distributor Information ──────────────────────────────────
    'distributor',
    'distributor_license_number',

    # ── Laboratory Information ───────────────────────────────────
    'lab',
    'lab_license_number',
    'lab_address',
    'lab_city',
    'lab_state',
    'lab_zipcode',

    # ── Dates ────────────────────────────────────────────────────
    'date_tested',
    'date_received',
    'date_collected',

    # ── Cannabinoid Totals ───────────────────────────────────────
    'total_thc',
    'total_cbd',
    'total_cannabinoids',

    # ── Terpene Total ────────────────────────────────────────────
    'total_terpenes',

    # ── Contaminant Statuses ─────────────────────────────────────
    'status',
    'pesticides_status',
    'heavy_metals_status',
    'microbials_status',
    'residual_solvents_status',
    'moisture_content',
    'water_activity',

    # ── Analyses & Detailed Results ──────────────────────────────
    'analyses',
    'results',

    # ── Parsing Metadata ─────────────────────────────────────────
    'parsing_model',
    'parsing_cost',

    # ── Aggregation & QC Metadata ────────────────────────────────
    'date_aggregated',
    'completeness',
]


# =============================================================================
# State Names
# =============================================================================

STATE_NAMES: Dict[str, str] = {
    'ak': 'Alaska', 'al': 'Alabama', 'az': 'Arizona', 'ar': 'Arkansas',
    'ca': 'California', 'co': 'Colorado', 'ct': 'Connecticut',
    'de': 'Delaware', 'dc': 'District of Columbia', 'fl': 'Florida',
    'ga': 'Georgia', 'hi': 'Hawaii', 'id': 'Idaho', 'il': 'Illinois',
    'in': 'Indiana', 'ia': 'Iowa', 'ks': 'Kansas', 'ky': 'Kentucky',
    'la': 'Louisiana', 'me': 'Maine', 'md': 'Maryland',
    'ma': 'Massachusetts', 'mi': 'Michigan', 'mn': 'Minnesota',
    'ms': 'Mississippi', 'mo': 'Missouri', 'mt': 'Montana',
    'ne': 'Nebraska', 'nv': 'Nevada', 'nh': 'New Hampshire',
    'nj': 'New Jersey', 'nm': 'New Mexico', 'ny': 'New York',
    'nc': 'North Carolina', 'nd': 'North Dakota', 'oh': 'Ohio',
    'ok': 'Oklahoma', 'or': 'Oregon', 'pa': 'Pennsylvania',
    'ri': 'Rhode Island', 'sc': 'South Carolina', 'sd': 'South Dakota',
    'tn': 'Tennessee', 'tx': 'Texas', 'ut': 'Utah', 'vt': 'Vermont',
    'va': 'Virginia', 'wa': 'Washington', 'wv': 'West Virginia',
    'wi': 'Wisconsin', 'wy': 'Wyoming',
}


# =============================================================================
# Sentinel / Placeholder Values
# =============================================================================

PLACEHOLDER_VALUES = {
    '', 'nan', 'NaN', 'None', 'none', 'null', 'NULL',
    'N/A', 'n/a', 'NA', 'na',
    'Not Available', 'Not Published', 'Unknown', 'TBD',
}


# =============================================================================
# Helpers
# =============================================================================

def get_timestamp() -> str:
    """Return the current ISO timestamp."""
    return datetime.now().isoformat()


def get_today_str() -> str:
    """Return today's date as YYYY-MM-DD."""
    return datetime.now().strftime('%Y-%m-%d')


def _pct(numerator: int, denominator: int) -> float:
    """Compute a rounded percentage, safe against division by zero."""
    if denominator == 0:
        return 0.0
    return round(100.0 * numerator / denominator, 1)


def _is_placeholder(value) -> bool:
    """Check whether a value is a sentinel/placeholder."""
    if value is None or pd.isna(value):
        return True
    return str(value).strip() in PLACEHOLDER_VALUES


# =============================================================================
# Step 1: Load Build File
# =============================================================================

def load_build_file(input_path: str) -> pd.DataFrame:
    """
    Load the QC-validated build file.

    Reads with ``dtype=str`` to prevent pandas from silently converting
    numeric strings or JSON columns.

    Args:
        input_path: Path to the build CSV.

    Returns:
        DataFrame with all columns as strings.
    """
    if not os.path.exists(input_path):
        print(f'  ✗ ERROR: Input file not found: {input_path}')
        print('    Run agg_results.py → qc_results.py first.')
        return pd.DataFrame()

    df = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    print(f'  ✓ Loaded {len(df):,} records with {len(df.columns)} columns')
    return df


# =============================================================================
# Step 2: Final Standardization
# =============================================================================

def standardize_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply final standardization to every record.

    - Convert remaining sentinels to blank strings.
    - Ensure ``state`` is lowercase.
    - Strip leading/trailing whitespace on all text fields.

    Args:
        df: QC-validated DataFrame.

    Returns:
        Standardized DataFrame.
    """
    df = df.copy()
    cleaned = 0

    # Lowercase state codes.
    if 'state' in df.columns:
        df['state'] = df['state'].str.strip().str.lower()

    # Sweep every column: strip whitespace, convert sentinels to ''.
    for col in df.columns:
        original_blanks = (df[col] == '').sum()
        df[col] = df[col].str.strip()
        df[col] = df[col].replace(list(PLACEHOLDER_VALUES), '')
        new_blanks = (df[col] == '').sum()
        cleaned += max(0, new_blanks - original_blanks)

    print(f'  ✓ Final standardization: {cleaned:,} residual sentinels blanked')
    return df


# =============================================================================
# Step 3: Column Selection & Ordering
# =============================================================================

def select_and_order_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Select delivery columns and order them canonically.

    Any column in ``DELIVERY_COLUMNS`` that is missing from the
    DataFrame is silently skipped (the column set may grow as the
    pipeline matures).

    Args:
        df: Standardized DataFrame.

    Returns:
        DataFrame with only the delivery columns, in canonical order.
    """
    available = [c for c in DELIVERY_COLUMNS if c in df.columns]
    missing = [c for c in DELIVERY_COLUMNS if c not in df.columns]
    extra = [c for c in df.columns if c not in DELIVERY_COLUMNS]

    print(f'  Delivery columns present: {len(available)}/{len(DELIVERY_COLUMNS)}')
    if missing:
        print(f'  Columns not yet in build: {", ".join(missing)}')
    if extra:
        print(f'  Non-delivery columns dropped: {len(extra)}')

    return df[available].copy()


# =============================================================================
# Step 4: Sort Records
# =============================================================================

def sort_records(df: pd.DataFrame) -> pd.DataFrame:
    """
    Sort records in a consistent, reproducible order.

    Primary sort: state (alphabetical).
    Secondary sort: date_tested (most recent first).
    Tertiary sort: product_name (alphabetical).

    Args:
        df: Column-selected DataFrame.

    Returns:
        Sorted DataFrame with reset index.
    """
    sort_cols = []
    ascending = []
    for col, asc in [
        ('state', True),
        ('date_tested', False),
        ('product_name', True),
    ]:
        if col in df.columns:
            sort_cols.append(col)
            ascending.append(asc)

    if sort_cols:
        df = df.sort_values(sort_cols, ascending=ascending, na_position='last')

    df = df.reset_index(drop=True)
    print(f'  ✓ Sorted by {" → ".join(sort_cols)}')
    return df


# =============================================================================
# Step 5: Calculate Coverage Statistics
# =============================================================================

def _field_coverage(df: pd.DataFrame, field: str) -> float:
    """Compute the percentage of non-blank values for a field."""
    if field not in df.columns:
        return 0.0
    total = len(df)
    if total == 0:
        return 0.0
    non_empty = (df[field] != '').sum()
    return _pct(non_empty, total)


def calculate_coverage_statistics(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Calculate comprehensive coverage statistics.

    Covers: summary metrics, per-field completeness, state and product
    type distributions, analysis coverage, analyte result statistics,
    parsing cost summary, and data completeness tiers.

    Args:
        df: Final delivery DataFrame (all columns as str).

    Returns:
        Dictionary of statistics suitable for JSON export.
    """
    total = len(df)
    if total == 0:
        return {'error': 'No records in dataset'}

    # ── Field completeness ───────────────────────────────────────
    field_completeness = {}
    for col in DELIVERY_COLUMNS:
        if col in df.columns:
            field_completeness[col] = _field_coverage(df, col)

    # ── State coverage ───────────────────────────────────────────
    by_state: Dict[str, Dict[str, Any]] = {}
    if 'state' in df.columns:
        for state_code, count in (
            df['state'].loc[df['state'] != '']
            .value_counts()
            .items()
        ):
            by_state[state_code] = {
                'name': STATE_NAMES.get(state_code, state_code),
                'count': int(count),
                'percentage': _pct(int(count), total),
            }

    # ── Product type distribution ────────────────────────────────
    by_product_type: Dict[str, Dict[str, Any]] = {}
    if 'product_type' in df.columns:
        for pt, count in (
            df['product_type'].loc[df['product_type'] != '']
            .value_counts()
            .items()
        ):
            by_product_type[pt] = {
                'count': int(count),
                'percentage': _pct(int(count), total),
            }

    # ── Analysis coverage ────────────────────────────────────────
    # Import normalization map to ensure consistent canonical counting.
    try:
        from qc_results import ANALYSIS_NAME_NORMALIZATION
    except ImportError:
        ANALYSIS_NAME_NORMALIZATION = {}
    analysis_counts: Dict[str, int] = defaultdict(int)
    if 'analyses' in df.columns:
        for val in df['analyses']:
            val = str(val).strip()
            if not val or val == '[]':
                continue
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    for a in parsed:
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
                        # Normalize: strip status suffixes and map
                        key = name.lower().strip()
                        if ':' in key:
                            key = key.split(':')[0].strip()
                        for suffix in (
                            ' not tested', ' not applicable',
                            ' passed', ' tested', ' completed',
                            ' pass', ' fail', ' failed',
                        ):
                            if key.endswith(suffix):
                                key = key[:-len(suffix)].strip()
                                break
                        if '(' in key:
                            key = key.split('(')[0].strip()
                        canonical = ANALYSIS_NAME_NORMALIZATION.get(key, key)
                        if canonical:
                            analysis_counts[canonical] += 1
            except (json.JSONDecodeError, TypeError):
                pass
    by_analysis = {
        k: {'count': v, 'percentage': _pct(v, total)}
        for k, v in sorted(
            analysis_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )
    }

    # ── Analyte result statistics ────────────────────────────────
    results_lengths: List[int] = []
    if 'results' in df.columns:
        for val in df['results']:
            val_str = str(val).strip()
            if not val_str or val_str == '[]':
                results_lengths.append(0)
                continue
            try:
                parsed = json.loads(val_str)
                results_lengths.append(
                    len(parsed) if isinstance(parsed, list) else 0,
                )
            except (json.JSONDecodeError, TypeError):
                results_lengths.append(0)
    total_analytes = sum(results_lengths)
    avg_results = round(total_analytes / total, 1) if total > 0 else 0
    median_results = int(
        pd.Series(results_lengths).median(),
    ) if results_lengths else 0
    max_results = max(results_lengths) if results_lengths else 0
    zero_results = sum(1 for r in results_lengths if r == 0)

    # ── Unique entity counts ─────────────────────────────────────
    def _nunique(col: str) -> int:
        if col not in df.columns:
            return 0
        return int(df[col].loc[df[col] != ''].nunique())

    unique_labs = _nunique('lab')
    unique_producers = _nunique('producer')
    unique_sources = _nunique('source')

    # ── Parsing cost ─────────────────────────────────────────────
    total_cost = 0.0
    avg_cost = 0.0
    if 'parsing_cost' in df.columns:
        costs = pd.to_numeric(df['parsing_cost'], errors='coerce')
        total_cost = round(float(costs.sum()), 4)
        avg_cost = round(float(costs.mean()), 6) if len(costs) > 0 else 0.0

    # ── Completeness tiers ───────────────────────────────────────
    completeness_tiers: Dict[str, int] = {}
    if 'completeness' in df.columns:
        for tier in ['high', 'medium', 'low']:
            completeness_tiers[tier] = int(
                (df['completeness'].str.lower() == tier).sum(),
            )
        completeness_tiers['unscored'] = int(
            (df['completeness'] == '').sum(),
        )

    # ── THC/CBD summary statistics ───────────────────────────────
    thc_stats = {}
    for col, label in [('total_thc', 'thc'), ('total_cbd', 'cbd'),
                        ('total_terpenes', 'terpenes')]:
        if col in df.columns:
            vals = pd.to_numeric(df[col], errors='coerce').dropna()
            if len(vals) > 0:
                thc_stats[label] = {
                    'mean': round(float(vals.mean()), 2),
                    'median': round(float(vals.median()), 2),
                    'std': round(float(vals.std()), 2),
                    'min': round(float(vals.min()), 2),
                    'max': round(float(vals.max()), 2),
                    'n': int(len(vals)),
                }

    # ── Assemble ─────────────────────────────────────────────────
    return {
        'metadata': {
            'dataset_name': 'Cannlytics Cannabis Results',
            'generated_at': get_timestamp(),
            'version': __version__,
        },
        'summary': {
            'total_records': total,
            'states_covered': len(by_state),
            'unique_labs': unique_labs,
            'unique_producers': unique_producers,
            'data_sources': unique_sources,
            'total_analyte_results': total_analytes,
            'avg_results_per_record': avg_results,
            'median_results_per_record': median_results,
            'max_results_per_record': max_results,
            'records_with_zero_results': zero_results,
        },
        'field_completeness': field_completeness,
        'state_coverage': by_state,
        'product_type_distribution': by_product_type,
        'analysis_coverage': by_analysis,
        'potency_statistics': thc_stats,
        'completeness_tiers': completeness_tiers,
        'parsing_costs': {
            'total_cost_usd': total_cost,
            'avg_cost_per_record_usd': avg_cost,
        },
    }


# =============================================================================
# Step 6: Generate Coverage Report (Markdown)
# =============================================================================

def generate_coverage_report(stats: Dict[str, Any]) -> str:
    """
    Generate a human-readable Markdown coverage report.

    Args:
        stats: Statistics dictionary from ``calculate_coverage_statistics``.

    Returns:
        Markdown-formatted report string.
    """
    meta = stats.get('metadata', {})
    summary = stats.get('summary', {})
    lines: List[str] = [
        '# Cannlytics Cannabis Results — Coverage Report',
        '',
        f"**Generated:** {meta.get('generated_at', 'N/A')}",
        f"**Version:** {meta.get('version', 'N/A')}",
        '',
        '## Executive Summary',
        '',
        '| Metric | Value |',
        '|--------|-------|',
        f"| **Total Records** | {summary.get('total_records', 0):,} |",
        f"| **U.S. States Covered** | {summary.get('states_covered', 0)} |",
        f"| **Unique Testing Labs** | {summary.get('unique_labs', 0):,} |",
        f"| **Unique Producers** | {summary.get('unique_producers', 0):,} |",
        f"| **Data Sources** | {summary.get('data_sources', 0):,} |",
        f"| **Total Analyte Measurements** | {summary.get('total_analyte_results', 0):,} |",
        f"| **Avg Analytes / Record** | {summary.get('avg_results_per_record', 0)} |",
        f"| **Median Analytes / Record** | {summary.get('median_results_per_record', 0)} |",
        '',
    ]

    # State coverage.
    state_cov = stats.get('state_coverage', {})
    if state_cov:
        lines += [
            '## State Coverage',
            '',
            '| State | Records | Percentage |',
            '|-------|---------|------------|',
        ]
        for code, data in state_cov.items():
            name = data.get('name', code)
            lines.append(
                f"| {name} ({code.upper()}) | {data['count']:,}"
                f" | {data['percentage']}% |",
            )
        lines.append('')

    # Product type distribution.
    pt_dist = stats.get('product_type_distribution', {})
    if pt_dist:
        lines += [
            '## Product Type Distribution',
            '',
            '| Product Type | Count | Percentage |',
            '|-------------|-------|------------|',
        ]
        for pt, data in pt_dist.items():
            lines.append(
                f"| {pt} | {data['count']:,} | {data['percentage']}% |",
            )
        lines.append('')

    # Analysis coverage.
    analysis_cov = stats.get('analysis_coverage', {})
    if analysis_cov:
        lines += [
            '## Analysis Coverage',
            '',
            '| Analysis Type | Records | Coverage |',
            '|--------------|---------|----------|',
        ]
        for analysis, data in analysis_cov.items():
            lines.append(
                f"| {analysis} | {data['count']:,} | {data['percentage']}% |",
            )
        lines.append('')

    # Potency statistics.
    potency = stats.get('potency_statistics', {})
    if potency:
        lines += [
            '## Potency Summary Statistics',
            '',
            '| Metric | Mean | Median | Std Dev | Min | Max | n |',
            '|--------|------|--------|---------|-----|-----|---|',
        ]
        for metric, data in potency.items():
            lines.append(
                f"| {metric} | {data['mean']} | {data['median']}"
                f" | {data['std']} | {data['min']} | {data['max']}"
                f" | {data['n']:,} |",
            )
        lines.append('')

    # Completeness tiers.
    comp = stats.get('completeness_tiers', {})
    if comp:
        lines += [
            '## Data Completeness Tiers',
            '',
            '| Tier | Records |',
            '|------|---------|',
        ]
        for tier, count in comp.items():
            lines.append(f'| {tier} | {count:,} |')
        lines.append('')

    # Field completeness.
    fc = stats.get('field_completeness', {})
    if fc:
        lines += [
            '## Field Completeness',
            '',
            '| Field | Coverage (%) |',
            '|-------|-------------|',
        ]
        for field, pct in fc.items():
            display = field.replace('_', ' ').title()
            lines.append(f'| {display} | {pct}% |')
        lines.append('')

    # Parsing costs.
    costs = stats.get('parsing_costs', {})
    if costs:
        lines += [
            '## Parsing Costs',
            '',
            f"- **Total Cost:** ${costs.get('total_cost_usd', 0):.4f}",
            f"- **Avg Cost per Record:** ${costs.get('avg_cost_per_record_usd', 0):.6f}",
            '',
        ]

    lines += [
        '---',
        '',
        f'*Generated by Cannlytics Cannabis Results Dataset Compiler'
        f' v{__version__}*',
    ]

    return '\n'.join(lines)


# =============================================================================
# Step 7: Export Functions
# =============================================================================

def export_final_dataset(
    df: pd.DataFrame,
    output_path: str,
    versioned_path: Optional[str] = None,
) -> None:
    """
    Write the final deliverable CSV (and optional versioned copy).

    Args:
        df:              Final, column-selected, sorted DataFrame.
        output_path:     Path for the ``-latest.csv`` file.
        versioned_path:  Optional path for the dated copy.
    """
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    df.to_csv(output_path, index=False)
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f'\n  ✓ Exported: {output_path}')
    print(f'    Records: {len(df):,}')
    print(f'    Columns: {len(df.columns)}')
    print(f'    Size: {size_mb:.1f} MB')

    if versioned_path:
        versioned_dir = os.path.dirname(versioned_path)
        if versioned_dir:
            os.makedirs(versioned_dir, exist_ok=True)
        df.to_csv(versioned_path, index=False)
        print(f'  ✓ Versioned copy: {versioned_path}')


def export_statistics(
    stats: Dict[str, Any],
    json_path: str,
    md_path: str,
) -> None:
    """
    Export coverage statistics as JSON and a Markdown report.

    Args:
        stats:     Statistics dictionary.
        json_path: Path for JSON output.
        md_path:   Path for Markdown report.
    """
    for path in [json_path, md_path]:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)

    # JSON
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2, default=str)
    print(f'  ✓ Statistics JSON: {json_path}')

    # Markdown
    report = generate_coverage_report(stats)
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f'  ✓ Coverage report: {md_path}')


# =============================================================================
# Main Function
# =============================================================================

def main(
    input_path: Optional[str] = None,
    output_path: Optional[str] = None,
    create_versioned: bool = True,
    create_dictionary: bool = True,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Main entry point: compile the final cannabis results dataset.

    Args:
        input_path:        Path to QC-validated build CSV.
        output_path:       Path for the final output CSV.
        create_versioned:  Whether to write a dated versioned copy.
        create_dictionary: Whether to generate the LaTeX data dictionary.

    Returns:
        Tuple of (final DataFrame, statistics dictionary).
    """
    print('=' * 70)
    print('Cannlytics Cannabis Results — Dataset Compiler')
    print('=' * 70)
    print(f'Timestamp:  {get_timestamp()}')
    print(f'Version:    {__version__}')

    # ── Resolve paths ────────────────────────────────────────────
    if input_path is None:
        candidates = [
            DEFAULT_INPUT_PATH,
            os.path.join('.build', 'cannabis-results.csv'),
            'cannabis-results.csv',
        ]
        for c in candidates:
            if os.path.exists(c):
                input_path = c
                break
        if input_path is None:
            input_path = DEFAULT_INPUT_PATH  # will error on load

    if output_path is None:
        output_path = DEFAULT_OUTPUT_PATH

    output_dir = os.path.dirname(output_path) or '.'
    stats_json = os.path.join(output_dir, 'cannabis-results-statistics.json')
    stats_md = os.path.join(output_dir, 'cannabis-results-statistics.md')
    versioned_path = (
        os.path.join(output_dir, f'cannabis-results-{get_today_str()}.csv')
        if create_versioned else None
    )

    print(f'\nInput:   {input_path}')
    print(f'Output:  {output_path}')
    if versioned_path:
        print(f'Version: {versioned_path}')

    # ══════════════════════════════════════════════════════════════
    # Step 1: Load QC-validated build file
    # ══════════════════════════════════════════════════════════════
    print('\n' + '-' * 70)
    print('Step 1: Loading QC-validated build file')
    print('-' * 70)

    df = load_build_file(input_path)
    if df.empty or len(df) == 0:
        print('  ⚠ No records to process.')
        return pd.DataFrame(), {'error': 'No records in dataset'}

    # ══════════════════════════════════════════════════════════════
    # Step 2: Final standardization
    # ══════════════════════════════════════════════════════════════
    print('\n' + '-' * 70)
    print('Step 2: Final data standardization')
    print('-' * 70)

    df = standardize_data(df)

    # ══════════════════════════════════════════════════════════════
    # Step 3: Column selection & ordering
    # ══════════════════════════════════════════════════════════════
    print('\n' + '-' * 70)
    print('Step 3: Selecting and ordering delivery columns')
    print('-' * 70)

    df = select_and_order_columns(df)

    # ══════════════════════════════════════════════════════════════
    # Step 4: Sort records
    # ══════════════════════════════════════════════════════════════
    print('\n' + '-' * 70)
    print('Step 4: Sorting records')
    print('-' * 70)

    df = sort_records(df)

    # ══════════════════════════════════════════════════════════════
    # Step 5: Calculate coverage statistics
    # ══════════════════════════════════════════════════════════════
    print('\n' + '-' * 70)
    print('Step 5: Calculating coverage statistics')
    print('-' * 70)

    stats = calculate_coverage_statistics(df)

    summary = stats.get('summary', {})
    print(f'\n  Summary:')
    print(f'    Total records:    {summary.get("total_records", 0):,}')
    print(f'    States covered:   {summary.get("states_covered", 0)}')
    print(f'    Unique labs:      {summary.get("unique_labs", 0):,}')
    print(f'    Unique producers: {summary.get("unique_producers", 0):,}')
    print(f'    Analyte results:  {summary.get("total_analyte_results", 0):,}')

    # ══════════════════════════════════════════════════════════════
    # Step 6: Export final outputs
    # ══════════════════════════════════════════════════════════════
    print('\n' + '-' * 70)
    print('Step 6: Exporting final outputs')
    print('-' * 70)

    export_final_dataset(df, output_path, versioned_path)
    export_statistics(stats, stats_json, stats_md)

    # ══════════════════════════════════════════════════════════════
    # Step 7: Generate data dictionary
    # ══════════════════════════════════════════════════════════════
    print('\n' + '-' * 70)
    print('Step 7: Generating data dictionary')
    print('-' * 70)

    if create_dictionary and DATA_DICTIONARY_AVAILABLE:
        try:
            docs_dir = os.path.join(
                os.path.dirname(os.path.dirname(output_path) or '.'),
                'documents',
            )
            os.makedirs(docs_dir, exist_ok=True)
            dict_tex_path = os.path.join(
                docs_dir, 'cannabis-results-data-dictionary.tex',
            )
            dict_stats_path = os.path.join(
                docs_dir, 'cannabis-results-data-dictionary.json',
            )

            tex_path, pdf_path = generate_data_dictionary(
                df,
                dict_tex_path,
                compile_pdf=True,
                stats_path=dict_stats_path,
            )

            if pdf_path:
                print(f'  ✓ Data dictionary PDF: {pdf_path}')
            else:
                print(f'  ✓ Data dictionary LaTeX: {tex_path}')
                print('    (Compile manually with Texmaker or pdflatex)')
        except Exception as e:
            print(f'  ⚠ Data dictionary generation failed: {e}')
            print('    You can run create_results_dictionary.py separately.')
    elif create_dictionary:
        print('  ⚠ Data dictionary generator not available')
        print('    Place create_results_dictionary.py in the same directory.')
    else:
        print('  Skipped (--no-dictionary)')

    # ══════════════════════════════════════════════════════════════
    # Summary
    # ══════════════════════════════════════════════════════════════
    print('\n' + '=' * 70)
    print('DATASET COMPILATION COMPLETE')
    print('=' * 70)
    print(f'\nFinal dataset:  {output_path}')
    print(f'Records:        {len(df):,}')
    print(f'Columns:        {len(df.columns)}')

    # State breakdown.
    state_cov = stats.get('state_coverage', {})
    if state_cov:
        print('\nState coverage:')
        for code, data in state_cov.items():
            name = data.get('name', code)
            print(f'  {name} ({code.upper()}): {data["count"]:,}'
                  f'  ({data["percentage"]}%)')

    # Product type breakdown.
    pt_dist = stats.get('product_type_distribution', {})
    if pt_dist:
        print('\nProduct types:')
        for pt, data in pt_dist.items():
            print(f'  {pt}: {data["count"]:,}  ({data["percentage"]}%)')

    # Parsing cost.
    costs = stats.get('parsing_costs', {})
    if costs.get('total_cost_usd'):
        print(
            f'\nParsing cost: ${costs["total_cost_usd"]:.4f}'
            f' (${costs["avg_cost_per_record_usd"]:.6f}/record)',
        )

    return df, stats


# =============================================================================
# CLI Entry Point
# =============================================================================

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description=(
            'Cannlytics Cannabis Results — '
            'Final Dataset Compiler'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Pipeline Position:
    Stage 3 of 4 — runs AFTER qc_results.py, BEFORE analyze_results.py

Examples:
  python create_results_dataset.py
  python create_results_dataset.py --input .build/cannabis-results.csv
  python create_results_dataset.py --output output/custom-output.csv
  python create_results_dataset.py --no-versioned --no-dictionary
        """,
    )
    parser.add_argument(
        '--input', '-i',
        type=str,
        default=None,
        help=f'Path to QC-validated build CSV (default: {DEFAULT_INPUT_PATH})',
    )
    parser.add_argument(
        '--output', '-o',
        type=str,
        default=None,
        help=f'Path for final output CSV (default: {DEFAULT_OUTPUT_PATH})',
    )
    parser.add_argument(
        '--no-versioned',
        action='store_true',
        help='Skip creating a dated versioned copy',
    )
    parser.add_argument(
        '--no-dictionary',
        action='store_true',
        help='Skip data dictionary generation',
    )

    args = parser.parse_args()

    df, stats = main(
        input_path=args.input,
        output_path=args.output,
        create_versioned=not args.no_versioned,
        create_dictionary=not args.no_dictionary,
    )