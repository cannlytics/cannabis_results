"""
Cannlytics Cannabis Results -- Data Dictionary Generator
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2026-03-02
Updated: 2026-03-02
License: MIT

Description:
    Generates a professional LaTeX data dictionary for the cannabis
    results dataset, including dataset summary statistics, per-field
    definitions and coverage, state coverage tables, product type
    distributions, analysis type breakdowns, result detail schema,
    and data methodology documentation.

    Mirrors the architecture of ``create_data_dictionary.py`` in the
    cannabis_licenses repository: calculate statistics → generate
    LaTeX → optionally compile to PDF.

Pipeline Position:
    Stage 3 of 4 -- Documentation
    Runs AFTER:  qc_results.py (quality control)
    Called from: create_dataset.py (final compilation) or standalone

Input Files:
    - .build/cannabis-results.csv  -- QC-validated results

Output Files:
    - documents/cannabis-results-data-dictionary.tex   -- LaTeX source
    - documents/build/cannabis-results-data-dictionary.pdf -- Compiled PDF
    - documents/cannabis-results-data-dictionary.json   -- Stats JSON

Dependencies:
    - pandas
    - pdflatex (optional, for PDF compilation)

Usage:
    python create_results_dictionary.py --input .build/cannabis-results.csv
    python create_results_dictionary.py --input data.csv --output docs/dict.tex
    python create_results_dictionary.py --input data.csv --no-pdf
"""

# Standard library
import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Third-party
import pandas as pd


# =============================================================================
# Path Resolution
# =============================================================================

# Resolve repo root for config imports (scripts/ -> repo root).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from config.results_config import PATHS as _PATHS
    DEFAULT_BUILD_DIR = str(_PATHS.build_dir)
except ImportError:
    DEFAULT_BUILD_DIR = os.environ.get(
        'CANNLYTICS_BUILD_DIR', r'D:\data\.build'
    )


# =============================================================================
# Version
# =============================================================================

__version__ = '1.0.0'


# =============================================================================
# Default Paths
# =============================================================================

DEFAULT_INPUT = os.path.join(DEFAULT_BUILD_DIR, 'cannabis-results.csv')


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
# Field Definitions
# =============================================================================
#
# Each tuple: (field_name, data_type, description)
# The order here defines the order in the LaTeX table.
# Descriptions draw from the historic field definitions provided by the
# user, the LabResult schema, and the EXPORT_COLUMNS from agg_results.py.

FIELD_DEFINITIONS: List[Tuple[str, str, str]] = [
    # ── Identifiers ──────────────────────────────────────────────
    ('pdf_hash', 'string',
     'SHA-256 hash of the source COA PDF file. '
     'Serves as the primary deduplication key across all states.'),
    ('sample_id', 'string',
     'A lab-assigned identifier for the sample '
     '(e.g., \\texttt{220415-001}).'),
    ('state', 'string',
     'Two-letter U.S. state code where the product was tested '
     '(lowercase, e.g., \\texttt{ca}).'),
    ('source', 'string',
     'Name of the data source or collector that produced the COA '
     '(e.g., \\texttt{flower\\_company}, \\texttt{kaycha}).'),

    # ── Product Information ──────────────────────────────────────
    ('product_name', 'string',
     'The commercial name of the product as printed on the COA.'),
    ('strain_name', 'string',
     'A strain or cultivar name, if specified. May be parsed from '
     '\\texttt{product\\_name} when not explicitly stated.'),
    ('product_type', 'string',
     'Standardized product category. One of: '
     '\\texttt{flower}, \\texttt{preroll}, \\texttt{infused}, '
     '\\texttt{concentrate}, \\texttt{vape}, \\texttt{edible}, '
     '\\texttt{tincture}, \\texttt{topical}.'),
    ('batch_number', 'string',
     'The manufacturer batch or lot number for the sampled product.'),
    ('batch_size', 'float',
     'The total batch size in grams, if reported on the COA.'),
    ('sample_weight', 'float',
     'The weight of the laboratory sample in grams.'),

    # ── Producer Information ─────────────────────────────────────
    ('producer', 'string',
     'The name of the licensed producer (cultivator or manufacturer).'),
    ('producer_street', 'string',
     'Street address of the producer facility.'),
    ('producer_city', 'string',
     'City of the producer facility.'),
    ('producer_state', 'string',
     'State of the producer facility (two-letter code).'),
    ('producer_zipcode', 'string',
     'ZIP code of the producer facility.'),
    ('producer_license_number', 'string',
     'The state-issued license number for the producer.'),

    # ── Distributor Information ──────────────────────────────────
    ('distributor', 'string',
     'The name of the product distributor, if applicable.'),
    ('distributor_license_number', 'string',
     'The state-issued license number for the distributor, if applicable.'),

    # ── Lab Information ──────────────────────────────────────────
    ('lab', 'string',
     'The name of the testing laboratory that performed the analyses.'),
    ('lab_license_number', 'string',
     'The state-issued license number for the testing laboratory.'),
    ('lab_address', 'string',
     'Full street address of the testing laboratory.'),
    ('lab_city', 'string',
     'City of the testing laboratory.'),
    ('lab_state', 'string',
     'State of the testing laboratory (two-letter code).'),
    ('lab_zipcode', 'string',
     'ZIP code of the testing laboratory.'),

    # ── Dates ────────────────────────────────────────────────────
    ('date_tested', 'date',
     'ISO-formatted date when laboratory analysis was completed '
     '(\\texttt{YYYY-MM-DD}).'),
    ('date_received', 'date',
     'ISO-formatted date when the sample was received by the lab.'),
    ('date_collected', 'date',
     'ISO-formatted date when the sample was collected from '
     'the production facility.'),

    # ── Cannabinoid Totals ───────────────────────────────────────
    ('total_thc', 'float',
     'Analytical total of THC and THCA, in weight percent (\\%). '
     'Calculated as $\\Delta$9-THC + (THCA $\\times$ 0.877).'),
    ('total_cbd', 'float',
     'Analytical total of CBD and CBDA, in weight percent (\\%). '
     'Calculated as CBD + (CBDA $\\times$ 0.877).'),
    ('total_cannabinoids', 'float',
     'Sum of all cannabinoids detected, in weight percent (\\%).'),

    # ── Terpene Total ────────────────────────────────────────────
    ('total_terpenes', 'float',
     'Sum of all terpenes detected, in weight percent (\\%).'),

    # ── Contaminant Statuses ─────────────────────────────────────
    ('status', 'string',
     'Overall pass/fail status for all contaminant screening analyses. '
     'Values: \\texttt{pass}, \\texttt{fail}, \\texttt{nt} (not tested).'),
    ('pesticides_status', 'string',
     'Pass/fail status for the pesticide residue analysis.'),
    ('heavy_metals_status', 'string',
     'Pass/fail status for the heavy metals analysis.'),
    ('microbials_status', 'string',
     'Pass/fail status for the microbial contaminant analysis.'),
    ('residual_solvents_status', 'string',
     'Pass/fail status for the residual solvents analysis.'),

    # ── Moisture ─────────────────────────────────────────────────
    ('moisture_content', 'float',
     'Moisture content of the sample in weight percent (\\%).'),
    ('water_activity', 'float',
     'Water activity ($a_W$) of the sample (dimensionless, 0.0--1.0).'),

    # ── Analyses & Results ───────────────────────────────────────
    ('analyses', 'list',
     'JSON list of analysis types performed on the sample '
     '(e.g., \\texttt{["cannabinoids", "terpenes", "pesticides"]}).'),
    ('results', 'list',
     'JSON list of individual analyte measurements. '
     'See \\textbf{Table~2} for the result detail schema.'),

    # ── Parsing Metadata ─────────────────────────────────────────
    ('parsing_model', 'string',
     'The AI model used to parse the COA '
     '(e.g., \\texttt{gpt-4o-mini}, \\texttt{gpt-4.1-nano}).'),
    ('parsing_cost', 'float',
     'Estimated API cost in USD incurred to parse this COA.'),

    # ── Aggregation Metadata ─────────────────────────────────────
    ('date_aggregated', 'date',
     'ISO-formatted date when the record was aggregated into the '
     'build file by \\texttt{agg\\_results.py}.'),

    # ── QC Metadata ──────────────────────────────────────────────
    ('completeness', 'string',
     'Data completeness tier assigned by \\texttt{qc\\_results.py}. '
     'Values: \\texttt{high}, \\texttt{medium}, \\texttt{low}, or empty.'),
]

# Result detail sub-schema (the objects inside the `results` JSON list).
RESULT_DETAIL_FIELDS: List[Tuple[str, str, str]] = [
    ('analysis', 'string',
     'The analysis type that produced this measurement '
     '(e.g., \\texttt{cannabinoids}, \\texttt{pesticides}).'),
    ('key', 'string',
     'A standardized snake\\_case key for the analyte '
     '(e.g., \\texttt{delta\\_9\\_thc}, \\texttt{beta\\_myrcene}).'),
    ('name', 'string',
     "The laboratory's displayed name for the analyte "
     '(e.g., ``$\\Delta$9-THC\'\', ``Pyrethrins\'\').'),
    ('value', 'float',
     'The measured test result value. '
     'For cannabinoids and terpenes, reported in weight percent (\\%); '
     'for pesticides and heavy metals, in the units specified.'),
    ('units', 'string',
     'The units for \\texttt{value}, \\texttt{limit}, \\texttt{lod}, '
     'and \\texttt{loq} (e.g., \\texttt{percent}, \\texttt{ppb}, '
     '\\texttt{cfu/g}).'),
    ('limit', 'float',
     'The regulatory action limit for contaminant screening analyses. '
     '0.0 if not applicable or not reported.'),
    ('lod', 'float',
     'Limit of Detection. Values below the LOD are typically reported '
     'as ``ND\'\' (Not Detected). 0.0 if not reported.'),
    ('loq', 'float',
     'Limit of Quantification. Values above the LOD but below the LOQ '
     'are typically reported as ``$<$LOQ\'\'. 0.0 if not reported.'),
    ('status', 'string',
     'Pass/fail status for contaminant screening analytes. '
     'Values: \\texttt{pass}, \\texttt{fail}, or empty.'),
]


# =============================================================================
# Helper Functions
# =============================================================================

def escape_latex(text: str) -> str:
    """Escape special LaTeX characters in plain text."""
    if not isinstance(text, str):
        return str(text)
    # Skip text that already contains LaTeX commands.
    if '\\' in text and any(
        cmd in text for cmd in [
            '\\texttt', '\\textbf', '\\&', '\\%', '\\$',
        ]
    ):
        return text
    replacements = [
        ('&', '\\&'), ('%', '\\%'), ('$', '\\$'),
        ('#', '\\#'), ('_', '\\_'),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def escape_latex_strict(text: str) -> str:
    """Escape ALL LaTeX-special characters in dynamic data.

    Unlike ``escape_latex``, this never bypasses escaping and also
    handles curly braces.  Use for values originating from the
    dataset (product names, analysis types, lab names, etc.) that
    are placed inside tabularx or longtable cells.
    """
    if not isinstance(text, str):
        text = str(text)
    replacements = [
        ('\\', '\\textbackslash{}'),
        ('{', '\\{'), ('}', '\\}'),
        ('&', '\\&'), ('%', '\\%'), ('$', '\\$'),
        ('#', '\\#'), ('_', '\\_'), ('~', '\\textasciitilde{}'),
        ('^', '\\textasciicircum{}'),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def format_number(n: int) -> str:
    """Format a number with comma thousands separator."""
    return f'{n:,}'


def format_pct(n: float) -> str:
    """Format a percentage with one decimal place."""
    return f'{n:.1f}'


def format_date_no_leading_zero(dt: datetime) -> str:
    """Format date without leading zero on day (e.g., 'March 2, 2026')."""
    return dt.strftime('%B ') + str(dt.day) + dt.strftime(', %Y')


# =============================================================================
# Statistics Calculation
# =============================================================================

def calculate_statistics(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Calculate all statistics needed for the data dictionary.

    Args:
        df: The cannabis results DataFrame (loaded with dtype=str).

    Returns:
        Dictionary containing all calculated statistics.
    """
    total = len(df)
    if total == 0:
        return {'total_records': 0}

    # ── Field completeness ───────────────────────────────────────
    field_completeness: Dict[str, float] = {}
    for field_name, _, _ in FIELD_DEFINITIONS:
        if field_name in df.columns:
            non_empty = df[field_name].notna() & (
                df[field_name].astype(str).str.strip() != ''
            )
            field_completeness[field_name] = round(
                100.0 * non_empty.sum() / total, 1
            )
        else:
            field_completeness[field_name] = 0.0

    # ── State coverage ───────────────────────────────────────────
    state_counts: Dict[str, int] = {}
    if 'state' in df.columns:
        state_counts = (
            df['state']
            .dropna()
            .str.strip()
            .str.lower()
            .value_counts()
            .to_dict()
        )

    state_data = []
    for code in sorted(state_counts, key=lambda x: state_counts[x],
                       reverse=True):
        state_data.append({
            'code': code.upper(),
            'name': STATE_NAMES.get(code, code),
            'count': state_counts[code],
        })

    # ── Product type distribution ────────────────────────────────
    product_types = []
    if 'product_type' in df.columns:
        pt_counts = (
            df['product_type']
            .dropna()
            .loc[lambda s: s.str.strip() != '']
            .value_counts()
        )
        for pt, count in pt_counts.items():
            product_types.append({
                'name': pt,
                'count': int(count),
                'percentage': round(100.0 * count / total, 1),
            })

    # ── Analysis coverage ────────────────────────────────────────
    # Canonical analysis types that should appear in the table.
    CANONICAL_ANALYSIS_TYPES = {
        'cannabinoids', 'terpenes', 'pesticides', 'heavy_metals',
        'microbials', 'residual_solvents', 'moisture_foreign_matter',
        'safety', 'other',
    }
    analysis_coverage: Dict[str, int] = defaultdict(int)
    try:
        from qc_results import ANALYSIS_NAME_NORMALIZATION as _NORM_MAP
    except ImportError:
        _NORM_MAP = {}
    if 'analyses' in df.columns:
        for val in df['analyses'].dropna():
            val_str = str(val).strip()
            if not val_str or val_str == '[]':
                continue
            try:
                parsed = json.loads(val_str)
                if isinstance(parsed, list):
                    for a in parsed:
                        # Handle dict items: {"name": "pesticides", ...}
                        if isinstance(a, dict):
                            a = a.get('name', '')
                        name = str(a).strip().lower()
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
                        if ':' in name:
                            name = name.split(':')[0].strip()
                        for suffix in (
                            ' not tested', ' not applicable',
                            ' passed', ' tested', ' completed',
                            ' pass', ' fail', ' failed',
                        ):
                            if name.endswith(suffix):
                                name = name[:-len(suffix)].strip()
                                break
                        if '(' in name:
                            name = name.split('(')[0].strip()
                        canonical = _NORM_MAP.get(name, name)
                        if canonical:
                            analysis_coverage[canonical] += 1
            except (json.JSONDecodeError, TypeError):
                pass
    # Filter to canonical analysis types only.
    analysis_data = sorted(
        [{'name': k, 'count': v, 'percentage': round(100.0 * v / total, 1)}
         for k, v in analysis_coverage.items()
         if k in CANONICAL_ANALYSIS_TYPES],
        key=lambda x: x['count'], reverse=True,
    )

    # ── Results statistics ───────────────────────────────────────
    results_counts = []
    if 'results' in df.columns:
        for val in df['results']:
            if pd.isna(val) or str(val).strip() in ('', '[]'):
                results_counts.append(0)
                continue
            try:
                parsed = json.loads(str(val))
                results_counts.append(
                    len(parsed) if isinstance(parsed, list) else 0
                )
            except (json.JSONDecodeError, TypeError):
                results_counts.append(0)
    total_analytes = sum(results_counts)
    avg_results = round(total_analytes / total, 1) if total > 0 else 0
    median_results = int(
        pd.Series(results_counts).median()
    ) if results_counts else 0

    # ── Unique entity counts ─────────────────────────────────────
    def nunique(col):
        if col in df.columns:
            return int(
                df[col].dropna()
                .loc[lambda s: s.astype(str).str.strip() != '']
                .nunique()
            )
        return 0

    # ── Parsing cost summary ─────────────────────────────────────
    total_cost = 0.0
    if 'parsing_cost' in df.columns:
        costs = pd.to_numeric(df['parsing_cost'], errors='coerce')
        total_cost = round(costs.sum(), 4)

    # ── Completeness distribution ────────────────────────────────
    completeness_dist: Dict[str, int] = {}
    if 'completeness' in df.columns:
        for level in ['high', 'medium', 'low']:
            completeness_dist[level] = int(
                (df['completeness'].str.strip().str.lower() == level).sum()
            )
        completeness_dist['incomplete'] = int(
            (df['completeness'].isna() |
             (df['completeness'].str.strip() == '')).sum()
        )

    return {
        'total_records': total,
        'states_covered': len(state_counts),
        'unique_labs': nunique('lab'),
        'unique_producers': nunique('producer'),
        'unique_sources': nunique('source'),
        'total_analyte_results': total_analytes,
        'avg_results_per_record': avg_results,
        'median_results_per_record': median_results,
        'total_parsing_cost': total_cost,
        'field_completeness': field_completeness,
        'state_data': state_data,
        'product_types': product_types,
        'analysis_data': analysis_data,
        'completeness_distribution': completeness_dist,
        'date': format_date_no_leading_zero(datetime.now()),
        'year': datetime.now().year,
    }


# =============================================================================
# LaTeX Table Generators
# =============================================================================

def generate_dataset_summary_table(stats: Dict[str, Any]) -> str:
    """Generate the dataset summary metrics table."""
    rows = [
        ('Total Records', format_number(stats['total_records'])),
        ('U.S. States Covered', str(stats['states_covered'])),
        ('Unique Testing Laboratories', format_number(stats['unique_labs'])),
        ('Unique Producers', format_number(stats['unique_producers'])),
        ('Data Sources', format_number(stats['unique_sources'])),
        ('Total Analyte Measurements',
         format_number(stats['total_analyte_results'])),
        ('Avg.\\ Analytes per Record',
         str(stats['avg_results_per_record'])),
        ('Total AI Parsing Cost',
         f"\\${stats['total_parsing_cost']:.2f}"),
    ]
    lines = [
        '{\\small',
        '\\renewcommand{\\arraystretch}{1.4}',
        '\\begin{tabularx}{\\textwidth}{@{}Xr@{}}',
        '\\toprule',
        '\\textbf{Metric} & \\textbf{Value} \\\\',
        '\\midrule',
    ]
    for label, value in rows:
        lines.append(f'{label} & {value} \\\\')
    lines += ['\\bottomrule', '\\end{tabularx}', '}']
    return '\n'.join(lines)


def generate_state_coverage_table(
    state_data: List[Dict], total: int,
) -> str:
    """Generate a two-column state coverage table."""
    if not state_data:
        return '\\textit{No state data available.}'

    # Two-column layout: split list in half.
    n = len(state_data)
    mid = (n + 1) // 2
    left = state_data[:mid]
    right = state_data[mid:]

    lines = [
        '{\\small',
        '\\renewcommand{\\arraystretch}{1.2}',
        '\\begin{tabularx}{\\textwidth}{@{}Xlr|Xlr@{}}',
        '\\toprule',
        '\\textbf{State} & \\textbf{Records} & \\textbf{\\%}'
        ' & \\textbf{State} & \\textbf{Records} & \\textbf{\\%} \\\\',
        '\\midrule',
    ]
    for i in range(mid):
        l = left[i]
        l_pct = format_pct(100.0 * l['count'] / total)
        row = (
            f"{escape_latex(l['name'])} ({l['code']})"
            f" & {format_number(l['count'])} & {l_pct}\\%"
        )
        if i < len(right):
            r = right[i]
            r_pct = format_pct(100.0 * r['count'] / total)
            row += (
                f" & {escape_latex(r['name'])} ({r['code']})"
                f" & {format_number(r['count'])} & {r_pct}\\%"
            )
        else:
            row += ' & & &'
        row += ' \\\\'
        lines.append(row)

    lines += ['\\bottomrule', '\\end{tabularx}', '}']
    return '\n'.join(lines)


def generate_product_type_table(
    product_types: List[Dict],
) -> str:
    """Generate the product type distribution table."""
    if not product_types:
        return '\\textit{No product type data available.}'
    lines = [
        '{\\small',
        '\\renewcommand{\\arraystretch}{1.3}',
        '\\begin{tabularx}{\\textwidth}{@{}Xrrr@{}}',
        '\\toprule',
        '\\textbf{Product Type} & \\textbf{Records}'
        ' & \\textbf{Proportion} & \\textbf{Cumulative} \\\\',
        '\\midrule',
    ]
    cumulative = 0.0
    for pt in product_types:
        cumulative += pt['percentage']
        safe_name = escape_latex_strict(pt['name'])
        lines.append(
            f"\\texttt{{{safe_name}}} & {format_number(pt['count'])}"
            f" & {format_pct(pt['percentage'])}\\%"
            f" & {format_pct(cumulative)}\\% \\\\"
        )
    lines += ['\\bottomrule', '\\end{tabularx}', '}']
    return '\n'.join(lines)


def generate_analysis_coverage_table(
    analysis_data: List[Dict],
) -> str:
    """Generate the analysis type coverage table."""
    if not analysis_data:
        return '\\textit{No analysis coverage data available.}'
    lines = [
        '{\\small',
        '\\renewcommand{\\arraystretch}{1.3}',
        '\\begin{tabularx}{\\textwidth}{@{}Xrr@{}}',
        '\\toprule',
        '\\textbf{Analysis Type} & \\textbf{Records}'
        ' & \\textbf{Coverage} \\\\',
        '\\midrule',
    ]
    for a in analysis_data:
        name_display = escape_latex_strict(a['name'])
        lines.append(
            f'\\texttt{{{name_display}}} & {format_number(a["count"])}'
            f' & {format_pct(a["percentage"])}\\% \\\\'
        )
    lines += ['\\bottomrule', '\\end{tabularx}', '}']
    return '\n'.join(lines)


def generate_definitions_table(
    stats: Dict[str, Any],
    field_defs: List[Tuple[str, str, str]],
) -> str:
    """Generate the field definitions longtable."""
    field_completeness = stats.get('field_completeness', {})
    lines = [
        '\\small',
        '\\renewcommand{\\arraystretch}{1.5}',
        '\\begin{longtable}{@{}L{4.2cm}C{1.1cm}C{1.6cm}p{9.5cm}@{}}',
        '\\toprule',
        '\\textbf{Field} & \\textbf{Type} & \\textbf{Coverage}'
        ' & \\textbf{Description} \\\\',
        '\\midrule',
        '\\endfirsthead',
        '\\toprule',
        '\\textbf{Field} & \\textbf{Type} & \\textbf{Coverage}'
        ' & \\textbf{Description} \\\\',
        '\\midrule',
        '\\endhead',
        '\\midrule',
        '\\multicolumn{4}{r}{\\small\\textit{Continued on next page}} \\\\',
        '\\endfoot',
        '\\bottomrule',
        '\\endlastfoot',
    ]
    for i, (field_name, field_type, description) in enumerate(field_defs):
        coverage = field_completeness.get(field_name, 0.0)
        display_name = field_name.replace('_', '\\_')
        row = (
            f'\\fieldname{{{display_name}}} & {field_type}'
            f' & {coverage}\\% & {description}'
        )
        if i < len(field_defs) - 1:
            row += ' \\\\[0.4em]'
        else:
            row += ' \\\\'
        lines.append(row)
    lines.append('\\end{longtable}')
    return '\n'.join(lines)


def generate_result_detail_table() -> str:
    """Generate the result detail sub-schema table."""
    lines = [
        '\\small',
        '\\renewcommand{\\arraystretch}{1.5}',
        '\\begin{longtable}{@{}L{2.5cm}C{1.1cm}p{13.0cm}@{}}',
        '\\toprule',
        '\\textbf{Field} & \\textbf{Type}'
        ' & \\textbf{Description} \\\\',
        '\\midrule',
        '\\endfirsthead',
        '\\toprule',
        '\\textbf{Field} & \\textbf{Type}'
        ' & \\textbf{Description} \\\\',
        '\\midrule',
        '\\endhead',
        '\\bottomrule',
        '\\endlastfoot',
    ]
    for i, (field_name, field_type, description) in enumerate(
        RESULT_DETAIL_FIELDS
    ):
        display_name = field_name.replace('_', '\\_')
        row = (
            f'\\fieldname{{{display_name}}} & {field_type}'
            f' & {description}'
        )
        if i < len(RESULT_DETAIL_FIELDS) - 1:
            row += ' \\\\[0.4em]'
        else:
            row += ' \\\\'
        lines.append(row)
    lines.append('\\end{longtable}')
    return '\n'.join(lines)


# =============================================================================
# LaTeX Document Assembly
# =============================================================================

def generate_data_dictionary_latex(stats: Dict[str, Any]) -> str:
    """
    Generate the complete LaTeX data dictionary document.

    Args:
        stats: Statistics dictionary from ``calculate_statistics()``.

    Returns:
        Complete LaTeX document as a string.
    """
    # Build dynamic sections.
    dataset_summary_table = generate_dataset_summary_table(stats)
    state_coverage_table = generate_state_coverage_table(
        stats.get('state_data', []), stats['total_records'],
    )
    product_type_table = generate_product_type_table(
        stats.get('product_types', []),
    )
    analysis_coverage_table = generate_analysis_coverage_table(
        stats.get('analysis_data', []),
    )
    definitions_table = generate_definitions_table(stats, FIELD_DEFINITIONS)
    result_detail_table = generate_result_detail_table()

    # Completeness distribution snippet.
    comp = stats.get('completeness_distribution', {})
    comp_text = ''
    if comp:
        parts = []
        for level in ['high', 'medium', 'low', 'incomplete']:
            if comp.get(level, 0) > 0:
                parts.append(f'{level}: {format_number(comp[level])}')
        if parts:
            comp_text = ', '.join(parts)
    if not comp_text:
        comp_text = 'Not yet computed'

    # Build the LaTeX document.
    latex_doc = f'''\\documentclass[11pt,letterpaper]{{article}}

% ============================================================================
% Packages
% ============================================================================
\\usepackage[utf8]{{inputenc}}
\\usepackage[T1]{{fontenc}}
\\usepackage{{geometry}}
\\usepackage{{booktabs}}
\\usepackage{{longtable}}
\\usepackage{{tabularx}}
\\usepackage{{array}}
\\usepackage{{xcolor}}
\\usepackage{{colortbl}}
\\usepackage{{hyperref}}
\\usepackage{{fancyhdr}}
\\usepackage{{enumitem}}
\\usepackage{{graphicx}}
\\usepackage{{titlesec}}
\\usepackage{{parskip}}
\\usepackage{{setspace}}
\\usepackage{{multirow}}
\\usepackage{{lastpage}}
\\usepackage{{needspace}}
\\usepackage{{amsmath}}

% ============================================================================
% Page Layout
% ============================================================================
\\geometry{{
    letterpaper,
    left=0.75in,
    right=0.75in,
    top=0.85in,
    bottom=0.85in,
}}

\\setlength{{\\parskip}}{{0.5em}}
\\setstretch{{1.05}}

% ============================================================================
% Colors
% ============================================================================
\\definecolor{{cannlyticsprimary}}{{RGB}}{{12, 75, 51}}
\\definecolor{{cannlyticssecondary}}{{RGB}}{{45, 45, 45}}
\\definecolor{{lightgray}}{{RGB}}{{248, 248, 248}}
\\definecolor{{tableborder}}{{RGB}}{{200, 200, 200}}

% ============================================================================
% Header / Footer
% ============================================================================
\\pagestyle{{fancy}}
\\fancyhf{{}}
\\fancyhead[L]{{\\textcolor{{cannlyticssecondary}}{{\\small Cannabis Results Data Dictionary}}}}
\\fancyhead[R]{{\\textcolor{{cannlyticssecondary}}{{\\small {stats['date']}}}}}
\\fancyfoot[C]{{\\textcolor{{cannlyticssecondary}}{{\\small Page \\thepage\\ of \\pageref{{LastPage}}}}}}
\\renewcommand{{\\headrulewidth}}{{0.4pt}}
\\renewcommand{{\\footrulewidth}}{{0pt}}

% ============================================================================
% Section Formatting
% ============================================================================
\\titleformat{{\\section}}
    {{\\Large\\bfseries\\color{{cannlyticsprimary}}}}
    {{\\thesection}}{{0.8em}}{{}}
\\titleformat{{\\subsection}}
    {{\\normalsize\\bfseries\\color{{cannlyticssecondary}}}}
    {{\\thesubsection}}{{0.6em}}{{}}

\\titlespacing*{{\\section}}{{0pt}}{{2.5ex plus 1ex minus 0.3ex}}{{1.5ex plus 0.3ex}}
\\titlespacing*{{\\subsection}}{{0pt}}{{1.8ex plus 0.6ex minus 0.2ex}}{{0.8ex plus 0.2ex}}

% ============================================================================
% Hyperref Setup
% ============================================================================
\\hypersetup{{
    colorlinks=true,
    linkcolor=cannlyticsprimary,
    urlcolor=cannlyticsprimary,
    citecolor=cannlyticsprimary,
    pdftitle={{Data Dictionary | Cannabis Results}},
    pdfauthor={{Cannlytics}},
}}

% ============================================================================
% Custom Commands
% ============================================================================
\\newcommand{{\\fieldname}}[1]{{\\texttt{{\\textbf{{#1}}}}}}

% Custom column types
\\newcolumntype{{L}}[1]{{>{{\\raggedright\\arraybackslash\\ttfamily\\small}}p{{#1}}}}
\\newcolumntype{{C}}[1]{{>{{\\centering\\arraybackslash\\small}}p{{#1}}}}

% ============================================================================
% Document
% ============================================================================
\\begin{{document}}

% ----------------------------------------------------------------------------
% Title Page
% ----------------------------------------------------------------------------
\\begin{{titlepage}}
    \\centering
    \\vspace*{{1.5cm}}

    {{\\Huge\\bfseries\\textcolor{{cannlyticsprimary}}{{Cannabis Results\\\\[0.5\\baselineskip]Data Dictionary}}}}

    \\rule{{0.5\\textwidth}}{{1pt}}

    \\vspace{{0.5cm}}

    {{\\large
    \\begin{{tabular}}{{rl}}
    \\textbf{{Prepared by:}} & Cannlytics \\\\[0.25cm]
    \\textbf{{Date:}} & {stats['date']} \\\\[0.25cm]
    \\textbf{{Coverage:}} & United States \\\\[0.25cm]
    \\textbf{{States:}} & {stats['states_covered']} \\\\[0.25cm]
    \\textbf{{Total Records:}} & {format_number(stats['total_records'])} \\\\[0.25cm]
    \\textbf{{Analyte Measurements:}} & {format_number(stats['total_analyte_results'])} \\\\[0.25cm]
    \\textbf{{Update Frequency:}} & Weekly \\\\[0.25cm]
    \\textbf{{Format:}} & CSV \\\\
    \\end{{tabular}}
    }}

    \\vfill

    \\vspace{{0.8cm}}

    {{\\small\\textcolor{{cannlyticssecondary}}{{
    \\textbf{{Contact:}} contact@cannlytics.com\\\\[0.25cm]
    \\textbf{{Website:}} \\url{{https://cannlytics.com}}\\\\[0.25cm]
    \\textcopyright\\ {stats['year']} Cannlytics. All rights reserved.
    }}}}

\\end{{titlepage}}

% ============================================================================
\\section*{{Overview}}
% ============================================================================

The \\textbf{{Cannlytics Cannabis Results}} dataset is a comprehensive
collection of cannabis laboratory test results (Certificates of
Analysis) aggregated from publicly available sources across the
United States. Each record represents a single COA and contains
product metadata, producer and laboratory information, cannabinoid
and terpene totals, contaminant screening statuses, and detailed
per-analyte measurements. For data questions, technical support,
or licensing inquiries please email: contact@cannlytics.com

\\vspace{{1em}}

{dataset_summary_table}

\\vspace{{0.5em}}

\\noindent\\textbf{{Data completeness tiers:}} {comp_text}

% ============================================================================
\\section{{State Coverage}}
% ============================================================================

The dataset currently spans \\textbf{{{stats['states_covered']}}} U.S.
states. Coverage varies by state depending on the availability of
public COA data, active data collection sources, and regulatory
transparency.

\\vspace{{1em}}

{state_coverage_table}

% ============================================================================
\\section{{Product Type Distribution}}
% ============================================================================

Product types are standardized to eight canonical categories during
quality control. The distribution reflects the mix of products tested
across all states.

\\vspace{{1em}}

{product_type_table}

% ============================================================================
\\section{{Analysis Coverage}}
% ============================================================================

Each COA may include one or more analysis types. Cannabinoid and
terpene analyses are nearly universal, while safety screening
analyses (pesticides, heavy metals, microbials, residual solvents)
depend on state regulations and the level of detail extractable
from the source COA.

\\vspace{{1em}}

{analysis_coverage_table}

% ============================================================================
\\section{{Field Definitions}}
% ============================================================================

\\textbf{{Table~1}} defines every field in the dataset.
The \\textbf{{Coverage}} column indicates the percentage of records
where the field has a non-empty value.

\\vspace{{0.5em}}

\\noindent\\textit{{Table 1: Record-level field definitions}}

\\vspace{{0.5em}}

{definitions_table}

% ============================================================================
\\section{{Result Detail Schema}}
% ============================================================================

The \\texttt{{results}} field contains a JSON list of individual analyte
measurements. Each element is a dictionary conforming to the schema
described in \\textbf{{Table~2}}.

\\vspace{{0.5em}}

\\noindent\\textit{{Table 2: Analyte result detail fields (elements of the
\\texttt{{results}} JSON list)}}

\\vspace{{0.5em}}

{result_detail_table}

% ============================================================================
\\section{{Data Methodology}}
% ============================================================================

\\begin{{enumerate}}[leftmargin=1.5em, itemsep=0.4em]

\\item \\textbf{{Collection:}} COA documents (PDFs) are collected from
publicly available sources including dispensary websites, laboratory
portals, brand transparency pages, and public records requests.
Jurisdiction-specific collection algorithms handle the unique data
access patterns of each source.

\\item \\textbf{{Aggregation:}} The \\texttt{{agg\\_results.py}} pipeline
reads AI-parsed cache files (JSONL format organized by state and
analysis type), merges metadata with analyte-level results, deduplicates
by SHA-256 PDF hash, validates ranges, and exports the build CSV.

\\item \\textbf{{Quality Control:}} The \\texttt{{qc\\_results.py}} pipeline
applies 18 validation rules: string normalization, name cleaning,
date standardization, product type and status normalization, numeric
range validation with automatic mg/g-to-percent correction, state
validation, deduplication, JSON integrity checks, cross-field
consistency validation, lab name standardization, and data
completeness scoring.

\\item \\textbf{{Parsing:}} COA documents are parsed using AI vision
models (primarily \\texttt{{gpt-5-nano}}) with structured output.
The parsing pipeline implements single-page fast-path processing,
metadata extraction with smart retry logic, and OCR fallback detection.
Parsing accuracy targets exceed 99\\% on potency analyses.

\\item \\textbf{{Standardization:}} All analyte keys are normalized to
a canonical snake\\_case vocabulary. Product types are collapsed to
eight standard categories. Contaminant statuses are normalized to
\\texttt{{pass}}, \\texttt{{fail}}, \\texttt{{nt}} (not tested), or
\\texttt{{n/a}}.

\\end{{enumerate}}

\\end{{document}}'''

    return latex_doc


# =============================================================================
# PDF Compilation
# =============================================================================

def compile_latex_to_pdf(
    tex_path: str,
    pdf_output_dir: Optional[str] = None,
) -> Optional[str]:
    """
    Compile a LaTeX file to PDF using pdflatex.

    Args:
        tex_path:       Path to the .tex source file.
        pdf_output_dir: Directory for PDF output. Defaults to a
                        ``build/`` subfolder next to the .tex file.

    Returns:
        Path to the compiled PDF, or None if compilation failed.
    """
    tex_path = Path(tex_path)
    tex_dir = tex_path.parent

    if pdf_output_dir is None:
        pdf_output_dir = tex_dir / 'build'
    else:
        pdf_output_dir = Path(pdf_output_dir)
    pdf_output_dir.mkdir(parents=True, exist_ok=True)

    pdflatex_cmd = shutil.which('pdflatex')
    if not pdflatex_cmd:
        print('  pdflatex not found — skipping PDF compilation')
        print('    Open the .tex file in Texmaker to compile manually.')
        return None

    print('  Compiling PDF...')

    try:
        for i in range(2):  # Two passes for cross-references.
            result = subprocess.run(
                [
                    'pdflatex',
                    '-interaction=nonstopmode',
                    '-output-directory', str(pdf_output_dir),
                    str(tex_path),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0 and i == 1:
                print(f'  LaTeX warning: {result.stderr[:500]}')

        pdf_path = pdf_output_dir / tex_path.with_suffix('.pdf').name
        if pdf_path.exists():
            # Clean auxiliary files.
            for ext in ['.aux', '.log', '.out', '.toc']:
                aux = pdf_output_dir / tex_path.with_suffix(ext).name
                if aux.exists():
                    aux.unlink()
            print(f'  PDF compiled: {pdf_path}')
            return str(pdf_path)
        else:
            print('  PDF compilation may have failed — check LaTeX output.')
            log_path = pdf_output_dir / tex_path.with_suffix('.log').name
            if log_path.exists():
                log_text = log_path.read_text(errors='ignore')
                # Find error lines.
                errors = [
                    l for l in log_text.splitlines()
                    if l.startswith('!')
                ]
                if errors:
                    print('  Errors:')
                    for e in errors[:5]:
                        print(f'    {e}')
            return None

    except FileNotFoundError:
        print('  pdflatex not found. Install TeX Live or MiKTeX.')
        return None
    except subprocess.TimeoutExpired:
        print('  LaTeX compilation timed out.')
        return None


# =============================================================================
# Main Functions
# =============================================================================

def generate_data_dictionary(
    df: pd.DataFrame,
    output_path: str,
    compile_pdf: bool = True,
    stats_path: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """
    Generate the data dictionary from a DataFrame.

    Args:
        df:           Cannabis results DataFrame.
        output_path:  Path for the .tex output file.
        compile_pdf:  Whether to attempt PDF compilation.
        stats_path:   Optional path to write statistics JSON.

    Returns:
        Tuple of (tex_path, pdf_path_or_None).
    """
    print('\n' + '-' * 70)
    print('Generating Cannabis Results Data Dictionary')
    print('-' * 70)

    # Step 1: Calculate statistics.
    print('  Calculating statistics...')
    stats = calculate_statistics(df)

    # Step 2: Generate LaTeX.
    print('  Generating LaTeX document...')
    latex_content = generate_data_dictionary_latex(stats)

    # Step 3: Write LaTeX file.
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(latex_content)
    print(f'  LaTeX saved: {output_path}')

    # Step 4: Write statistics JSON.
    if stats_path:
        stats_dir = os.path.dirname(stats_path)
        if stats_dir:
            os.makedirs(stats_dir, exist_ok=True)
        with open(stats_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, default=str)
        print(f'  Stats JSON saved: {stats_path}')

    # Step 5: Compile PDF.
    pdf_path = None
    if compile_pdf:
        pdf_path = compile_latex_to_pdf(output_path)

    return output_path, pdf_path


# =============================================================================
# CLI Entry Point
# =============================================================================

def main():
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description=(
            'Cannlytics Cannabis Results — '
            'Data Dictionary Generator'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python create_results_dictionary.py --input .build/cannabis-results.csv
  python create_results_dictionary.py -i data.csv -o docs/dict.tex
  python create_results_dictionary.py -i data.csv --no-pdf
  python create_results_dictionary.py -i data.csv --stats stats.json
        """,
    )
    parser.add_argument(
        '--input', '-i',
        type=str,
        default=None,
        help=(
            f'Path to cannabis results CSV '
            f'(default: {DEFAULT_INPUT})'
        ),
    )
    parser.add_argument(
        '--output', '-o',
        type=str,
        default=None,
        help=(
            'Output path for .tex file '
            '(default: documents/cannabis-results-data-dictionary.tex)'
        ),
    )
    parser.add_argument(
        '--stats', '-s',
        type=str,
        default=None,
        help='Path to write statistics JSON',
    )
    parser.add_argument(
        '--no-pdf',
        action='store_true',
        help='Skip PDF compilation',
    )
    args = parser.parse_args()

    # Resolve input path.
    input_path = args.input
    if input_path is None:
        candidates = [
            DEFAULT_INPUT,
            os.path.join('.build', 'cannabis-results.csv'),
            'cannabis-results.csv',
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                input_path = candidate
                break
        if input_path is None:
            print('ERROR: No input file found. Use --input <path>.')
            sys.exit(1)

    # Resolve output path.
    if args.output is None:
        input_parent = os.path.dirname(os.path.abspath(input_path))
        docs_dir = os.path.join(
            os.path.dirname(input_parent), 'documents',
        )
        args.output = os.path.join(
            docs_dir, 'cannabis-results-data-dictionary.tex',
        )

    # Resolve stats path.
    stats_path = args.stats
    if stats_path is None:
        stats_path = os.path.splitext(args.output)[0] + '.json'

    # Load data.
    print(f'Loading: {input_path}')
    df = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    print(f'Loaded {len(df):,} records with {len(df.columns)} columns')

    # Generate.
    tex_path, pdf_path = generate_data_dictionary(
        df,
        args.output,
        compile_pdf=not args.no_pdf,
        stats_path=stats_path,
    )

    print('\n' + '=' * 70)
    print('DATA DICTIONARY GENERATION COMPLETE')
    print('=' * 70)
    print(f'  LaTeX: {tex_path}')
    if pdf_path:
        print(f'  PDF:   {pdf_path}')
    print(f'  Stats: {stats_path}')


if __name__ == '__main__':
    main()