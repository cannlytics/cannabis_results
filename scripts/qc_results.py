"""
Cannlytics Cannabis Results -- Quality Control
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2026-03-02
Updated: 2026-03-09
License: MIT

Description:
    Quality control and validation for the cannabis results build file.
    Runs 18 rules: string cleanup (with bracket artifact stripping),
    product/strain normalization, date formatting, status normalization,
    numeric range validation, producer and lab name cleaning, license
    number formatting, state/zip validation, address cleaning,
    deduplication, analysis name normalization, results JSON validation,
    cross-field consistency checks, and completeness scoring. Every
    transformation is logged; the script is idempotent.

Pipeline Position:
    Stage 2 of 4 -- Quality control
    Runs AFTER:  agg_results.py (cache aggregation)
    Runs BEFORE: enrichment pipeline (geocoding, entity resolution)

Input Files:
    - .build/cannabis-results.csv  -- Aggregated results (from agg_results.py)

Output Files:
    - .build/cannabis-results.csv  -- Validated results (in-place)
    - qc-results-report.json       -- QC report (optional, via --report)

Dependencies:
    - pandas

Usage:
    python qc_results.py
    python qc_results.py --input path/to/dataset.csv --output path/to/cleaned.csv
    python qc_results.py --verbose
    python qc_results.py --report qc-results-report.json
"""

# Standard library
import argparse
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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
# Configuration
# =============================================================================

VERSION = '1.0.0'

# Default paths.
DEFAULT_INPUT = os.path.join(DEFAULT_BUILD_DIR, 'cannabis-results.csv')

# Valid US state codes (two-letter abbreviations).
VALID_US_STATES: Set[str] = {
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
    'DC', 'PR', 'VI', 'GU', 'AS', 'MP',
}

# Valid Canadian province codes.
VALID_CA_PROVINCES: Set[str] = {
    'AB', 'BC', 'MB', 'NB', 'NL', 'NS', 'NT', 'NU', 'ON', 'PE',
    'QC', 'SK', 'YT',
}

ALL_VALID_REGIONS: Set[str] = VALID_US_STATES | VALID_CA_PROVINCES

# Full state/province name → two-letter code (lowercase keys, uppercase values).
STATE_NAME_TO_CODE: Dict[str, str] = {
    'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR',
    'california': 'CA', 'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE',
    'florida': 'FL', 'georgia': 'GA', 'hawaii': 'HI', 'idaho': 'ID',
    'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA', 'kansas': 'KS',
    'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
    'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN',
    'mississippi': 'MS', 'missouri': 'MO', 'montana': 'MT', 'nebraska': 'NE',
    'nevada': 'NV', 'new hampshire': 'NH', 'new jersey': 'NJ',
    'new mexico': 'NM', 'new york': 'NY', 'north carolina': 'NC',
    'north dakota': 'ND', 'ohio': 'OH', 'oklahoma': 'OK', 'oregon': 'OR',
    'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
    'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT',
    'vermont': 'VT', 'virginia': 'VA', 'washington': 'WA',
    'west virginia': 'WV', 'wisconsin': 'WI', 'wyoming': 'WY',
    'district of columbia': 'DC', 'puerto rico': 'PR',
    # Canadian provinces.
    'alberta': 'AB', 'british columbia': 'BC', 'manitoba': 'MB',
    'new brunswick': 'NB', 'newfoundland and labrador': 'NL',
    'nova scotia': 'NS', 'northwest territories': 'NT', 'nunavut': 'NU',
    'ontario': 'ON', 'prince edward island': 'PE', 'quebec': 'QC',
    'saskatchewan': 'SK', 'yukon': 'YT',
}


# =============================================================================
# Sentinel and Normalization Constants
# =============================================================================

# Strings treated as empty/missing (case-insensitive).
SENTINEL_VALUES: Set[str] = {
    'nan', 'none', 'n/a', 'na', 'null', 'undefined', 'not available',
    'not published', 'not disclosed', 'confidential', 'unavailable',
    'unknown', 'tbd', 'to be determined', '-', '--', '---', '.',
    '#n/a', '#ref!', '#value!', 'not applicable', 'no data',
    'missing', 'inf', '-inf', '0.0', 'false',
}

# Standard product types and their known variants.
PRODUCT_TYPE_MAP: Dict[str, str] = {}
_PRODUCT_TYPES = {
    'flower': [
        'flower', 'bud', 'buds', 'cannabis flower', 'dried flower',
        'trim', 'shake', 'plant material', 'biomass', 'raw plant material',
        'whole wet plant', 'usable marijuana', 'usable cannabis',
        'cannabis plant material',
        # Non-canonical variants from validation (March 7, 2026):
        'plant',                      # 87 records
        'flower, inhalable',          # 15 records
        'flower, medical inhalable',  # 1 record
        # Non-canonical variants from validation (March 9, 2026):
        "cannabis (mmtc's) flower & plants",  # 207 records (FL MMTC)
        'enhanced/infused flowers',           # 60 records
        'flower - cured',                     # 50 records
    ],
    'preroll': [
        'preroll', 'pre-roll', 'pre roll', 'joint', 'blunt',
        'prerolls', 'pre-rolls', 'infused preroll', 'infused pre-roll',
        'enhanced preroll', 'moon rock', 'moonrock',
        # Non-canonical variants from validation (March 7, 2026):
        'infused flower/pre-roll',    # 5 records
        'pre-roll cannabis',          # 2 records
        # Non-canonical variants from validation (March 9, 2026):
        'enhanced/infused preroll',           # 33 records
        'infused flower/pre-roll, product inhalable',  # 24 records
        'pre-roll cannabis, product inhalable',        # 20 records
        'infused/enhanced preroll',           # 2 records
        'pre-roll product, product inhalable', # 211 records
    ],
    'concentrate': [
        'concentrate', 'extract', 'wax', 'shatter', 'rosin',
        'live resin', 'budder', 'badder', 'sauce', 'diamonds',
        'sugar', 'crumble', 'hash', 'kief', 'distillate', 'rso',
        'live rosin', 'cured resin', 'resin', 'dab', 'dabs',
        'non-solvent concentrate', 'solvent based concentrate',
        # Non-canonical variants from validation (March 7, 2026):
        'derivative',                       # 4,488 records
        'concentrates & extracts',          # 322 records
        'concentrate, product inhalable',   # 100 records
        # Non-canonical variants from validation (March 9, 2026):
        "cannabis (mmtc's) derivative products",  # 17 records (FL MMTC)
        'sugar wax',                        # 6 records
        'concentrates &',                   # 1 record (truncated)
        'batter/badder',                    # 1 record
    ],
    'vape': [
        'vape', 'cartridge', 'cart', 'vaporizer', 'pod',
        'disposable', 'aio', 'all-in-one', 'vape pen', 'pen',
        'vape cartridge',
        # Non-canonical variants from validation (March 9, 2026):
        'inhalation',                 # 6 records
        '(inhalation - heated)',      # 2 records
    ],
    'edible': [
        'edible', 'gummy', 'gummies', 'chocolate', 'beverage',
        'candy', 'baked goods', 'capsule', 'tablet', 'ingestible',
        'infused edible', 'food', 'drink', 'lozenge', 'hard candy',
        'infused non-edible',
        # Non-canonical variants from validation (March 7, 2026):
        'infused',                    # 64 records
        'infused, solid edible',      # 9 records
        'infused, liquid edible',     # 1 record
        # Non-canonical variants from validation (March 9, 2026):
        'soft chew',                  # 177 records
        'infused, concentrated liquid edible',  # 8 records
        'oral',                           # 1 record (oral dosage form)
    ],
    'tincture': [
        'tincture', 'oil', 'drops', 'sublingual', 'oral solution',
        'mct oil', 'tinctures',
        # Non-canonical variants from validation (March 9, 2026):
        '(transmucosal )',            # 1 record (sublingual/buccal)
        'transmucosal',               # variant without parens
    ],
    'topical': [
        'topical', 'cream', 'lotion', 'balm', 'salve',
        'transdermal', 'patch', 'ointment', 'topicals',
        # Non-canonical variants from validation (March 7, 2026):
        'infused, non-inhalable',     # 1 record
        # Non-canonical variants from validation (March 9, 2026):
        'infused, topical',           # 4 records
    ],
}
for canonical, variants in _PRODUCT_TYPES.items():
    for v in variants:
        PRODUCT_TYPE_MAP[v.lower()] = canonical

# Additional product type mappings that don't fit a canonical category.
# These map to non-canonical but accepted labels (e.g., "other").
PRODUCT_TYPE_MAP['environmental'] = 'other'  # 42 records (monitoring samples)

# Status normalization map.
STATUS_MAP: Dict[str, str] = {
    'pass': 'pass', 'passed': 'pass', 'passing': 'pass',
    'p': 'pass', 'compliant': 'pass', 'yes': 'pass',
    'true': 'pass', '1': 'pass',
    'complete': 'pass', 'completed': 'pass',
    'final': 'pass', 'finalized': 'pass',
    'approved': 'pass', 'accepted': 'pass',
    'fail': 'fail', 'failed': 'fail', 'failing': 'fail',
    'f': 'fail', 'non-compliant': 'fail', 'no': 'fail',
    'false': 'fail', '0': 'fail',
    'nt': 'nt', 'not tested': 'nt', 'n/t': 'nt',
    'not applicable': 'n/a', 'n/a': 'n/a', 'na': 'n/a',
    'tested': '',  # "tested" indicates completion, not pass/fail
}

# Standard status values.
VALID_STATUSES: Set[str] = {'pass', 'fail', 'nt', 'n/a'}

# Status columns in the dataset.
STATUS_COLUMNS = [
    'status', 'pesticides_status', 'heavy_metals_status',
    'microbials_status', 'residual_solvents_status',
]

# Numeric range validation rules: (column, min, max, description).
NUMERIC_RANGES = [
    ('total_thc', 0, 100, 'Total THC (%)'),
    ('total_cbd', 0, 100, 'Total CBD (%)'),
    ('total_cannabinoids', 0, 100, 'Total cannabinoids (%)'),
    ('total_terpenes', 0, 20, 'Total terpenes (%)'),
    ('moisture_content', 0, 20, 'Moisture content (%)'),
    ('water_activity', 0, 1, 'Water activity (aW)'),
]

# Analysis name normalization map.
ANALYSIS_NAME_NORMALIZATION: Dict[str, str] = {
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
    'terpenes total': 'terpenes', 'terpenes_total': 'terpenes',
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
    # ── Non-canonical residual solvents variants (March 7, 2026) ──
    'residue solvents': 'residual_solvents',
    'residual solvents analysis': 'residual_solvents',
    'residual_solvents_analysis': 'residual_solvents',
    'residual solvents testing': 'residual_solvents',
    'residual_solvents_testing': 'residual_solvents',
    'solvent analysis': 'residual_solvents', 'solvent_analysis': 'residual_solvents',
    'solvent testing': 'residual_solvents', 'solvent_testing': 'residual_solvents',
    'solvents analysis': 'residual_solvents', 'solvents_analysis': 'residual_solvents',
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
    # ── Non-canonical heavy metals variants (March 7, 2026) ───────
    'heavy metals icpms': 'heavy_metals', 'heavy metals icp-ms': 'heavy_metals',
    'heavy_metals_icpms': 'heavy_metals', 'heavy_metals_icp_ms': 'heavy_metals',
    'heavy metals analysis': 'heavy_metals', 'heavy_metals_analysis': 'heavy_metals',
    'heavy metals testing': 'heavy_metals', 'heavy_metals_testing': 'heavy_metals',
    'metals testing': 'heavy_metals', 'metals analysis': 'heavy_metals',
    'metals_testing': 'heavy_metals', 'metals_analysis': 'heavy_metals',
    'pesticides_screen': 'pesticides', 'pesticides screen': 'pesticides',
    'pesticides_by_lcmsms': 'pesticides',
    'pesticides, fungicides, and growth regulators': 'pesticides',
    'pesticides/fungicides and growth regulators': 'pesticides',
    'pesticides_fungicides_and_growth_regulators': 'pesticides',
    'pesticides_fungicides_growth_regulators': 'pesticides',
    'growth regulators': 'pesticides',
    # ── Non-canonical pesticide variants (March 7, 2026) ──────────
    'pesticides lc': 'pesticides', 'pesticides_lc': 'pesticides',
    'pesticides gc': 'pesticides', 'pesticides_gc': 'pesticides',
    'pesticides lcms': 'pesticides', 'pesticides_lcms': 'pesticides',
    'pesticides gcms': 'pesticides', 'pesticides_gcms': 'pesticides',
    'pesticides lc/ms': 'pesticides', 'pesticides gc/ms': 'pesticides',
    'pesticides - lc': 'pesticides', 'pesticides - gc': 'pesticides',
    'pesticide analysis': 'pesticides', 'pesticide_analysis': 'pesticides',
    'pesticides analysis': 'pesticides', 'pesticides_analysis': 'pesticides',
    'pesticide testing': 'pesticides', 'pesticide_testing': 'pesticides',
    'pest': 'pesticides',
    'mycotoxins_screen': 'microbials', 'mycotoxins screen': 'microbials',
    'mycotoxins_by_lcmsms': 'microbials',
    'microbiological_screen': 'microbials', 'microbiological screen': 'microbials',
    'microbes_by_qpcr': 'microbials', 'microbes': 'microbials',
    'qpcr microbiology': 'microbials',
    'yeast & mold': 'microbials', 'yeast and mold': 'microbials',
    'aflatoxins': 'microbials',
    'pathogenic testing': 'microbials', 'pathogenic_testing': 'microbials',
    'pathogenic moisture': 'microbials',
    # ── Non-canonical microbial variants (March 7, 2026) ──────────
    'microbial impurities - mdg': 'microbials',
    'microbial impurities - mg': 'microbials',
    'microbial impurities mdg': 'microbials',
    'micro': 'microbials', 'micro impurities': 'microbials',
    'microbial testing': 'microbials', 'microbial_testing': 'microbials',
    'microbiology analysis': 'microbials', 'microbiology_analysis': 'microbials',
    'mycotoxin testing': 'microbials', 'mycotoxin_testing': 'microbials',
    'total viable aerobic bacteria': 'microbials',
    'total coliforms': 'microbials', 'bile tolerant gram neg': 'microbials',
    'bile-tolerant gram-negative': 'microbials',
    # ── Additional catch-all variants ─────────────────────────────
    'filth': 'moisture_foreign_matter',
    'filth_and_foreign_matter': 'moisture_foreign_matter',
    'moisture meter': 'moisture_foreign_matter',
    'cannabinoids potency': 'cannabinoids',
    'cannabinoid_potency': 'cannabinoids',
    # ── Non-canonical analysis variants (March 9, 2026) ──────────
    # These variants appeared in the 30,991-record dataset run.
    # Underscore-joined variants (from AI parsing with underscored keys):
    'agricultural_agents': 'pesticides',
    'pesticide_residues': 'pesticides', 'pesticide residues': 'pesticides',
    'percent moisture': 'moisture_foreign_matter',
    'microbial_analysis': 'microbials',
    'heavy_metal_analysis': 'heavy_metals',
    'filth_and_foreign_material_analysis': 'moisture_foreign_matter',
    'mycotoxin_analysis': 'microbials',
    'total cannabinoids': 'cannabinoids', 'total_cannabinoids': 'cannabinoids',
    # Method-specific variants with spaces (AI sometimes preserves full method names):
    'microbials pcr': 'microbials', 'microbials_pcr': 'microbials',
    'microbial impurities - tymc': 'microbials',
    'microbial impurities tymc': 'microbials',
    'pesticides by lcmsms': 'pesticides',
    'residual solvents by hs-gc-ms': 'residual_solvents',
    'residual_solvents_by_hs_gc_ms': 'residual_solvents',
    'mycotoxins by lcmsms': 'microbials',
    'heavy metals by icpms': 'heavy_metals',
    'micro by petri & qpcr': 'microbials',
    'micro by petri and qpcr': 'microbials',
}

# Date formats to try when parsing.
DATE_FORMATS = [
    '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f',
    '%m/%d/%Y', '%m/%d/%y', '%m-%d-%Y', '%m-%d-%y',
    '%d/%m/%Y', '%d-%m-%Y', '%B %d, %Y', '%b %d, %Y',
    '%Y/%m/%d', '%d %B %Y', '%d %b %Y',
]

# Date columns and reasonable range.
DATE_COLUMNS = ['date_tested', 'date_received', 'date_collected', 'date_aggregated']
DATE_MIN = datetime(2015, 1, 1)
DATE_MAX = datetime(2030, 12, 31)

# String fields to apply whitespace/sentinel cleanup.
STRING_FIELDS = [
    'product_name', 'strain_name', 'product_type',
    'batch_number', 'producer', 'producer_street',
    'producer_city', 'producer_state', 'producer_zipcode',
    'producer_license_number', 'distributor',
    'distributor_license_number', 'lab', 'lab_license_number',
    'lab_address', 'lab_city', 'lab_state', 'lab_zipcode',
    'source', 'parsing_model',
]


# =============================================================================
# QC Report Class
# =============================================================================

class QCReport:
    """Tracks QC findings and fixes across all rules."""

    def __init__(self):
        self.total_records: int = 0
        self.output_records: int = 0
        self.rules: Dict[str, Dict[str, int]] = {}
        self.warnings: List[str] = []
        self.started_at: str = datetime.now().isoformat()
        self.completed_at: Optional[str] = None

    def log(self, rule: str, found: int = 0, fixed: int = 0,
            remaining: Optional[int] = None) -> None:
        if remaining is None:
            remaining = found - fixed
        self.rules[rule] = {
            'found': found,
            'fixed': fixed,
            'remaining': max(0, remaining),
        }

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    @property
    def total_found(self) -> int:
        return sum(r['found'] for r in self.rules.values())

    @property
    def total_fixed(self) -> int:
        return sum(r['fixed'] for r in self.rules.values())

    @property
    def total_remaining(self) -> int:
        return sum(r['remaining'] for r in self.rules.values())

    def print_report(self) -> None:
        self.completed_at = datetime.now().isoformat()
        print(f'\n{"="*70}')
        print('QC RESULTS REPORT')
        print(f'{"="*70}')
        print(f'  Version:    {VERSION}')
        print(f'  Started:    {self.started_at}')
        print(f'  Completed:  {self.completed_at}')
        print(f'  Input:      {self.total_records:,} records')
        print(f'  Output:     {self.output_records:,} records')
        print(f'\n  {"Rule":<45} {"Found":>7} {"Fixed":>7} {"Left":>7}')
        print(f'  {"-"*45} {"-"*7} {"-"*7} {"-"*7}')
        for rule, counts in self.rules.items():
            print(f'  {rule:<45} {counts["found"]:>7,} '
                  f'{counts["fixed"]:>7,} {counts["remaining"]:>7,}')
        print(f'  {"-"*45} {"-"*7} {"-"*7} {"-"*7}')
        print(f'  {"TOTALS":<45} {self.total_found:>7,} '
              f'{self.total_fixed:>7,} {self.total_remaining:>7,}')
        if self.warnings:
            print(f'\n  Warnings ({len(self.warnings)}):')
            for w in self.warnings:
                print(f'    - {w}')
        print(f'{"="*70}\n')

    def to_dict(self) -> Dict[str, Any]:
        self.completed_at = self.completed_at or datetime.now().isoformat()
        return {
            'version': VERSION,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'total_records': self.total_records,
            'output_records': self.output_records,
            'total_found': self.total_found,
            'total_fixed': self.total_fixed,
            'total_remaining': self.total_remaining,
            'rules': self.rules,
            'warnings': self.warnings,
        }


# =============================================================================
# Helper Utilities
# =============================================================================

def _is_sentinel(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    s = str(value).strip().lower()
    return s in SENTINEL_VALUES or s == ''


def _clean_unicode(text: str) -> str:
    if not text:
        return text
    text = unicodedata.normalize('NFC', text)
    text = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\ufeff\u00ad]', '', text)
    replacements = {
        '\u2018': "'", '\u2019': "'",
        '\u201c': '"', '\u201d': '"',
        '\u2013': '-', '\u2014': '-',
        '\u2026': '...',
        '\u00a0': ' ',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, float):
        return None if pd.isna(value) else value
    if isinstance(value, (int,)):
        return float(value)
    s = str(value).strip().lower()
    if s in SENTINEL_VALUES or s == '':
        return None
    s = s.replace('%', '').replace(',', '').strip()
    try:
        f = float(s)
        return None if pd.isna(f) else f
    except (ValueError, TypeError):
        return None


def _parse_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if _is_sentinel(s):
        return None
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d')
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        return s
    s_date = re.sub(r'[T ]\d{2}:\d{2}.*$', '', s).strip()
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(s_date, fmt)
            if DATE_MIN <= dt <= DATE_MAX:
                return dt.strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            continue
    try:
        dt = pd.to_datetime(s, errors='coerce')
        if pd.notna(dt) and DATE_MIN <= dt.to_pydatetime() <= DATE_MAX:
            return dt.strftime('%Y-%m-%d')
    except Exception:
        pass
    return None


def _normalize_analyses_list(analyses_raw: Any) -> List[str]:
    if analyses_raw is None or _is_sentinel(analyses_raw):
        return []
    if isinstance(analyses_raw, list):
        raw_list = analyses_raw
    else:
        s = str(analyses_raw).strip()
        if not s or s.lower() in SENTINEL_VALUES:
            return []
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                raw_list = parsed
            else:
                raw_list = [str(parsed)]
        except (json.JSONDecodeError, TypeError):
            if s.startswith('[') and s.endswith(']'):
                inner = s[1:-1]
                raw_list = [
                    x.strip().strip("'\"")
                    for x in inner.split(',')
                    if x.strip().strip("'\"")
                ]
            else:
                raw_list = [x.strip() for x in s.split(',') if x.strip()]
    normalized = []
    seen = set()
    for name in raw_list:
        if not name:
            continue
        # Handle dict items: {"name": "pesticides", "status": "passed"}
        if isinstance(name, dict):
            name = name.get('name', '')
            if not name:
                continue
        # Handle stringified Python dicts: "{'name': 'pesticides', ...}"
        name_str = str(name).strip()
        if name_str.startswith('{') and 'name' in name_str:
            try:
                import ast
                parsed_dict = ast.literal_eval(name_str)
                if isinstance(parsed_dict, dict):
                    name_str = parsed_dict.get('name', name_str)
            except (ValueError, SyntaxError):
                pass
        key = name_str.lower().strip()
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
        canonical = ANALYSIS_NAME_NORMALIZATION.get(key, key)
        if canonical and canonical not in seen:
            normalized.append(canonical)
            seen.add(canonical)
    return normalized


# =============================================================================
# QC Rule Functions
# =============================================================================

def clean_string_fields(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 1: Clean whitespace, sentinels, unicode, and bracket artifacts in string fields."""
    rule = 'String whitespace & sentinels'
    found = 0
    fixed = 0
    for col in STRING_FIELDS:
        if col not in df.columns:
            continue
        original = df[col].copy()
        cleaned = df[col].fillna('').astype(str)
        cleaned = cleaned.str.strip()
        cleaned = cleaned.apply(_clean_unicode)
        cleaned = cleaned.str.replace(r'\s+', ' ', regex=True)
        # Strip bracket artifacts from AI parsing:
        # 1) Fully-wrapped values: "[Some Value]" → "Some Value"
        cleaned = cleaned.str.replace(r'^\[(.*)\]$', r'\1', regex=True)
        # 2) Embedded bracket-wrapped sentinels: "Company [Not Provided]" → "Company"
        cleaned = cleaned.str.replace(
            r'\s*\[(?:not provided|unknown|n/?a|none|null|not available|'
            r'not disclosed|not applicable|tbd|pending|unspecified)\]\s*',
            ' ', regex=True, case=False,
        )
        cleaned = cleaned.str.strip()
        sentinel_mask = cleaned.str.lower().isin(SENTINEL_VALUES)
        cleaned = cleaned.where(~sentinel_mask, '')
        changed = (original.fillna('').astype(str) != cleaned)
        n_changed = changed.sum()
        found += n_changed
        fixed += n_changed
        df[col] = cleaned
    report.log(rule, found=found, fixed=fixed)
    return df


def clean_product_names(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 2: Clean and standardize product names."""
    rule = 'Product name cleaning'
    col = 'product_name'
    if col not in df.columns:
        report.log(rule, found=0, fixed=0)
        return df
    original = df[col].copy()
    cleaned = df[col].fillna('').astype(str)
    cleaned = cleaned.str.strip('"\'')
    cleaned = cleaned.str.replace(r'^[\s\-|:;,]+', '', regex=True)
    cleaned = cleaned.str.replace(r'[\s\-|:;,]+$', '', regex=True)
    cleaned = cleaned.str.replace(r'\s{2,}', ' ', regex=True)
    cleaned = cleaned.str.strip()
    too_short = cleaned.str.len().between(1, 1)
    cleaned = cleaned.where(~too_short, '')
    changed = (original.fillna('').astype(str) != cleaned)
    n_changed = changed.sum()
    df[col] = cleaned
    report.log(rule, found=n_changed, fixed=n_changed)
    return df


def clean_strain_names(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 3: Clean and standardize strain names."""
    rule = 'Strain name cleaning'
    col = 'strain_name'
    if col not in df.columns:
        report.log(rule, found=0, fixed=0)
        return df
    original = df[col].copy()
    cleaned = df[col].fillna('').astype(str).str.strip()
    cleaned = cleaned.str.strip('"\'')
    cleaned = cleaned.str.replace(
        r'\s*\((indica|sativa|hybrid|i-hybrid|s-hybrid)\)\s*$',
        '', regex=True, case=False
    )

    def _title_strain(s):
        if not s or len(s) <= 2:
            return s
        words = s.split()
        titled = []
        for w in words:
            if w.isupper() and len(w) <= 3:
                titled.append(w)
            else:
                titled.append(w.title())
        return ' '.join(titled)

    cleaned = cleaned.apply(_title_strain)
    cleaned = cleaned.str.strip()
    changed = (original.fillna('').astype(str) != cleaned)
    n_changed = changed.sum()
    df[col] = cleaned
    report.log(rule, found=n_changed, fixed=n_changed)
    return df


def normalize_product_types(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 4: Normalize product_type to canonical categories."""
    rule = 'Product type normalization'
    col = 'product_type'
    if col not in df.columns:
        report.log(rule, found=0, fixed=0)
        return df
    original = df[col].copy()

    def _normalize_pt(val):
        s = str(val).strip().lower()
        if s in SENTINEL_VALUES or s == '':
            return ''
        return PRODUCT_TYPE_MAP.get(s, s)

    cleaned = df[col].apply(_normalize_pt)
    changed = (original.fillna('').astype(str).str.lower() != cleaned)
    n_changed = changed.sum()
    known_types = set(_PRODUCT_TYPES.keys())
    non_standard_mask = ~cleaned.isin(known_types) & (cleaned != '')
    if non_standard_mask.any():
        unknown = cleaned[non_standard_mask].value_counts().head(10).to_dict()
        report.warn(f'product_type: {non_standard_mask.sum()} records with '
                    f'non-standard types: {unknown}')
    df[col] = cleaned
    report.log(rule, found=n_changed, fixed=n_changed)
    return df


def clean_dates(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 5: Validate and normalize date fields to ISO format."""
    rule = 'Date validation'
    found = 0
    fixed = 0
    for col in DATE_COLUMNS:
        if col not in df.columns:
            continue
        original = df[col].copy()

        def _clean_date(val):
            if _is_sentinel(val):
                return ''
            result = _parse_date(val)
            return result if result else ''

        cleaned = df[col].apply(_clean_date)
        changed = (original.fillna('').astype(str) != cleaned)
        n_changed = changed.sum()
        found += n_changed
        fixed += n_changed
        had_value = original.fillna('').astype(str).str.strip() != ''
        now_blank = cleaned == ''
        lost = (had_value & now_blank).sum()
        if lost > 0:
            report.warn(f'{col}: {lost} unparseable date(s) blanked')
        df[col] = cleaned
    report.log(rule, found=found, fixed=fixed)
    return df


def normalize_statuses(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 6: Normalize status fields to standard values."""
    rule = 'Status normalization'
    found = 0
    fixed = 0
    for col in STATUS_COLUMNS:
        if col not in df.columns:
            continue
        original = df[col].copy()

        def _normalize(val):
            s = str(val).strip().lower()
            if s in SENTINEL_VALUES or s == '':
                return ''
            return STATUS_MAP.get(s, s)

        cleaned = df[col].apply(_normalize)
        changed = (original.fillna('').astype(str).str.lower() != cleaned)
        n_changed = changed.sum()
        found += n_changed
        fixed += n_changed
        non_standard = cleaned[~cleaned.isin(VALID_STATUSES) & (cleaned != '')]
        if len(non_standard) > 0:
            unique_ns = non_standard.unique().tolist()[:10]
            report.warn(f'{col}: {len(non_standard)} records with non-standard '
                        f'status values: {unique_ns}')
        df[col] = cleaned
    report.log(rule, found=found, fixed=fixed)
    return df


def validate_numeric_ranges(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 7: Validate numeric fields are within expected ranges."""
    rule = 'Numeric range validation'
    found = 0
    fixed = 0
    for col, min_val, max_val, desc in NUMERIC_RANGES:
        if col not in df.columns:
            continue
        original = df[col].copy()

        def _validate_numeric(val):
            f = _safe_float(val)
            if f is None:
                return ''
            if f < min_val or f > max_val:
                return ''
            return str(round(f, 4))

        cleaned = df[col].apply(_validate_numeric)
        had_value = original.apply(lambda v: _safe_float(v) is not None)
        now_blank = cleaned == ''
        out_of_range = (had_value & now_blank).sum()
        if out_of_range > 0:
            report.warn(f'{col}: {out_of_range} out-of-range value(s) blanked '
                        f'(valid: {min_val}-{max_val})')
        changed = (original.fillna('').astype(str) != cleaned)
        n_changed = changed.sum()
        found += n_changed
        fixed += n_changed
        df[col] = cleaned
    report.log(rule, found=found, fixed=fixed)
    return df


def clean_producer_names(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 8: Clean and standardize producer names."""
    rule = 'Producer name cleaning'
    col = 'producer'
    if col not in df.columns:
        report.log(rule, found=0, fixed=0)
        return df
    original = df[col].copy()
    cleaned = df[col].fillna('').astype(str).str.strip()
    cleaned = cleaned.str.strip('"\'')
    cleaned = cleaned.str.replace(r'[\s,;.]+$', '', regex=True)
    cleaned = cleaned.str.replace(r'\bL\.?L\.?C\.?\b', 'LLC', regex=True)
    cleaned = cleaned.str.replace(r'\bInc\.?\b', 'Inc', regex=True)
    cleaned = cleaned.str.replace(r'\bCorp\.?\b', 'Corp', regex=True)
    cleaned = cleaned.str.replace(r'\s{2,}', ' ', regex=True)
    cleaned = cleaned.str.strip()
    changed = (original.fillna('').astype(str) != cleaned)
    n_changed = changed.sum()
    df[col] = cleaned
    report.log(rule, found=n_changed, fixed=n_changed)
    return df


def clean_lab_names(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 9: Clean and standardize lab names."""
    rule = 'Lab name cleaning'
    col = 'lab'
    if col not in df.columns:
        report.log(rule, found=0, fixed=0)
        return df
    original = df[col].copy()
    cleaned = df[col].fillna('').astype(str).str.strip()
    cleaned = cleaned.str.strip('"\'')
    cleaned = cleaned.str.replace(r'[\s,;.]+$', '', regex=True)
    cleaned = cleaned.str.replace(r'\bL\.?L\.?C\.?\b', 'LLC', regex=True)
    cleaned = cleaned.str.replace(r'\bInc\.?\b', 'Inc', regex=True)
    cleaned = cleaned.str.replace(r'\bLab(oratory|oratories)\b', 'Labs',
                                  regex=True, case=False)
    cleaned = cleaned.str.replace(r'\s{2,}', ' ', regex=True)
    cleaned = cleaned.str.strip()
    changed = (original.fillna('').astype(str) != cleaned)
    n_changed = changed.sum()
    df[col] = cleaned
    report.log(rule, found=n_changed, fixed=n_changed)
    return df


def clean_license_numbers(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 10: Clean and format license numbers."""
    rule = 'License number formatting'
    license_cols = [
        'producer_license_number',
        'distributor_license_number',
        'lab_license_number',
    ]
    found = 0
    fixed = 0
    for col in license_cols:
        if col not in df.columns:
            continue
        original = df[col].copy()
        cleaned = df[col].fillna('').astype(str).str.strip()
        cleaned = cleaned.str.strip('"\'')
        cleaned = cleaned.str.upper()
        cleaned = cleaned.str.replace(r'^[\-\.]+|[\-\.]+$', '', regex=True)
        sentinel_mask = cleaned.str.lower().isin(SENTINEL_VALUES)
        cleaned = cleaned.where(~sentinel_mask, '')
        cleaned = cleaned.str.replace(r'\s{2,}', ' ', regex=True)
        cleaned = cleaned.str.strip()
        changed = (original.fillna('').astype(str) != cleaned)
        n_changed = changed.sum()
        found += n_changed
        fixed += n_changed
        df[col] = cleaned
    report.log(rule, found=found, fixed=fixed)
    return df


def validate_states(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 11: Validate state codes and convert full state names to codes."""
    rule = 'State validation'
    found = 0
    fixed = 0
    state_cols = ['state', 'producer_state', 'lab_state']
    for col in state_cols:
        if col not in df.columns:
            continue
        original = df[col].copy()

        def _validate_state(val):
            s = str(val).strip()
            if _is_sentinel(s):
                return ''
            upper = s.upper()
            if upper in ALL_VALID_REGIONS:
                return s.lower()
            if len(s) == 2:
                return s.lower() if upper in ALL_VALID_REGIONS else ''
            # Try full state/province name → code lookup.
            code = STATE_NAME_TO_CODE.get(s.lower())
            if code:
                return code.lower()
            return ''

        cleaned = df[col].apply(_validate_state)
        changed = (original.fillna('').astype(str) != cleaned)
        n_changed = changed.sum()
        had_value = original.fillna('').astype(str).str.strip() != ''
        now_blank = cleaned == ''
        lost = (had_value & now_blank).sum()
        if lost > 0:
            invalid_states = original[had_value & now_blank].unique()[:10]
            report.warn(f'{col}: {lost} invalid state code(s) blanked: '
                        f'{list(invalid_states)}')
        found += n_changed
        fixed += n_changed
        df[col] = cleaned
    report.log(rule, found=found, fixed=fixed)
    return df


def clean_zip_codes(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 12: Clean and validate zip codes."""
    rule = 'Zip code cleaning'
    zip_cols = ['producer_zipcode', 'lab_zipcode']
    found = 0
    fixed = 0
    for col in zip_cols:
        if col not in df.columns:
            continue
        original = df[col].copy()

        def _clean_zip(val):
            s = str(val).strip()
            if _is_sentinel(s):
                return ''
            digits = re.sub(r'[\s\-]', '', s)
            match = re.match(r'^(\d{5})', digits)
            if match:
                return match.group(1)
            return ''

        cleaned = df[col].apply(_clean_zip)
        changed = (original.fillna('').astype(str) != cleaned)
        n_changed = changed.sum()
        found += n_changed
        fixed += n_changed
        df[col] = cleaned
    report.log(rule, found=found, fixed=fixed)
    return df


def clean_address_fields(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 13: Clean address text fields."""
    rule = 'Address text cleaning'
    found = 0
    fixed = 0
    city_cols = ['producer_city', 'lab_city']
    for col in city_cols:
        if col not in df.columns:
            continue
        original = df[col].copy()
        cleaned = df[col].fillna('').astype(str).str.strip()
        cleaned = cleaned.str.strip('"\'')
        cleaned = cleaned.str.title()
        cleaned = cleaned.str.replace(r'\bOf\b', 'of', regex=True)
        cleaned = cleaned.str.replace(r'\bThe\b', 'the', regex=True)
        cleaned = cleaned.apply(lambda s: s[0].upper() + s[1:] if s else s)
        sentinel_mask = cleaned.str.lower().isin(SENTINEL_VALUES)
        cleaned = cleaned.where(~sentinel_mask, '')
        cleaned = cleaned.str.strip()
        changed = (original.fillna('').astype(str) != cleaned)
        n_changed = changed.sum()
        found += n_changed
        fixed += n_changed
        df[col] = cleaned
    street_cols = ['producer_street', 'lab_address']
    for col in street_cols:
        if col not in df.columns:
            continue
        original = df[col].copy()
        cleaned = df[col].fillna('').astype(str).str.strip()
        cleaned = cleaned.str.strip('"\'')
        cleaned = cleaned.str.replace(r'\s{2,}', ' ', regex=True)
        sentinel_mask = cleaned.str.lower().isin(SENTINEL_VALUES)
        cleaned = cleaned.where(~sentinel_mask, '')
        cleaned = cleaned.str.strip()
        changed = (original.fillna('').astype(str) != cleaned)
        n_changed = changed.sum()
        found += n_changed
        fixed += n_changed
        df[col] = cleaned
    report.log(rule, found=found, fixed=fixed)
    return df


def check_duplicates(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 14: Check for duplicate records by pdf_hash."""
    rule = 'Deduplication check'
    hash_col = 'pdf_hash'
    if hash_col not in df.columns:
        report.log(rule, found=0, fixed=0)
        return df
    dupes = df.duplicated(subset=[hash_col], keep='first')
    n_dupes = dupes.sum()
    if n_dupes > 0:
        df = df[~dupes].copy()
        report.warn(f'Removed {n_dupes} duplicate pdf_hash records')
    report.log(rule, found=n_dupes, fixed=n_dupes)
    return df


def normalize_analyses(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 15: Normalize analysis names in the analyses column."""
    rule = 'Analysis name normalization'
    col = 'analyses'
    if col not in df.columns:
        report.log(rule, found=0, fixed=0)
        return df
    original = df[col].copy()

    def _normalize(val):
        normalized = _normalize_analyses_list(val)
        return json.dumps(normalized) if normalized else '[]'

    cleaned = df[col].apply(_normalize)
    changed = (original.fillna('').astype(str) != cleaned)
    n_changed = changed.sum()
    df[col] = cleaned
    report.log(rule, found=n_changed, fixed=n_changed)
    return df


def validate_results_json(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 16: Validate the results JSON column."""
    rule = 'Results JSON validation'
    col = 'results'
    if col not in df.columns:
        report.log(rule, found=0, fixed=0)
        return df
    found = 0
    fixed = 0

    def _validate_results(val):
        nonlocal found, fixed
        if _is_sentinel(val):
            return '[]'
        s = str(val).strip()
        if not s or s == '[]':
            return '[]'
        try:
            parsed = json.loads(s)
        except (json.JSONDecodeError, TypeError):
            found += 1
            if s.startswith('{'):
                try:
                    parsed = json.loads(f'[{s}]')
                    fixed += 1
                except (json.JSONDecodeError, TypeError):
                    return '[]'
            else:
                return '[]'
        if not isinstance(parsed, list):
            parsed = [parsed]
        for entry in parsed:
            if isinstance(entry, dict) and 'analysis' in entry:
                raw_analysis = str(entry['analysis']).lower().strip()
                entry['analysis'] = ANALYSIS_NAME_NORMALIZATION.get(
                    raw_analysis, raw_analysis
                )
        return json.dumps(parsed, separators=(',', ':'))

    df[col] = df[col].apply(_validate_results)
    report.log(rule, found=found, fixed=fixed)
    return df


def check_cross_field_consistency(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 17: Cross-field consistency checks."""
    rule = 'Cross-field consistency'
    found = 0
    fixed = 0
    contaminant_cols = [
        'pesticides_status', 'heavy_metals_status',
        'microbials_status', 'residual_solvents_status',
    ]
    status_col = 'status'
    existing_contaminant_cols = [c for c in contaminant_cols if c in df.columns]
    if status_col in df.columns and existing_contaminant_cols:
        for idx, row in df.iterrows():
            overall = str(row.get(status_col, '')).strip()
            contaminant_statuses = [
                str(row.get(c, '')).strip()
                for c in existing_contaminant_cols
            ]
            active_statuses = [s for s in contaminant_statuses if s in ('pass', 'fail')]
            if not active_statuses:
                continue
            if 'fail' in active_statuses and overall != 'fail':
                df.at[idx, status_col] = 'fail'
                found += 1
                fixed += 1
            elif all(s == 'pass' for s in active_statuses) and overall == '':
                df.at[idx, status_col] = 'pass'
                found += 1
                fixed += 1
    if 'results' in df.columns and 'analyses' in df.columns:
        has_results = df['results'].apply(lambda v: v not in ('', '[]', None))
        no_analyses = df['analyses'].apply(lambda v: v in ('', '[]', None))
        mismatch_mask = has_results & no_analyses
        mismatch = mismatch_mask.sum()
        if mismatch > 0:
            # Infer analyses from the 'analysis' field in results JSON.
            for idx in df.index[mismatch_mask]:
                try:
                    parsed = json.loads(str(df.at[idx, 'results']))
                    if isinstance(parsed, list):
                        inferred = []
                        seen = set()
                        for entry in parsed:
                            if isinstance(entry, dict):
                                a = str(entry.get('analysis', '')).lower().strip()
                                canonical = ANALYSIS_NAME_NORMALIZATION.get(a, a)
                                if canonical and canonical not in seen:
                                    inferred.append(canonical)
                                    seen.add(canonical)
                        if inferred:
                            df.at[idx, 'analyses'] = json.dumps(inferred)
                            found += 1
                            fixed += 1
                except (json.JSONDecodeError, TypeError):
                    pass
            remaining = mismatch - fixed
            if remaining > 0:
                report.warn(
                    f'{remaining} records still have results data '
                    f'but no analyses listed'
                )
    report.log(rule, found=found, fixed=fixed)
    return df


def compute_completeness_score(df: pd.DataFrame, report: QCReport) -> pd.DataFrame:
    """Rule 18: Compute a completeness score for each record."""
    rule = 'Completeness scoring'
    scored_fields = {
        'pdf_hash': 5, 'sample_id': 3, 'state': 5,
        'product_name': 5, 'strain_name': 3, 'product_type': 4,
        'batch_number': 3,
        'producer': 5, 'producer_license_number': 3, 'producer_state': 2,
        'lab': 5, 'lab_license_number': 3,
        'date_tested': 5,
        'total_thc': 5, 'total_cbd': 4, 'total_terpenes': 4,
        'status': 4,
        'results': 5, 'analyses': 3,
    }
    active_fields = {k: v for k, v in scored_fields.items() if k in df.columns}
    max_score = sum(active_fields.values())
    if max_score == 0:
        report.log(rule, found=0, fixed=0)
        return df

    def _score_row(row):
        score = 0
        for field_name, weight in active_fields.items():
            val = row.get(field_name, '')
            if val is None:
                continue
            s = str(val).strip()
            if s and s not in ('', '[]', '0', '0.0') and s.lower() not in SENTINEL_VALUES:
                score += weight
        return round((score / max_score) * 100, 1)

    df['completeness_score'] = df.apply(_score_row, axis=1)

    # Derive a categorical completeness tier.
    def _tier(score):
        if score >= 75:
            return 'high'
        elif score >= 50:
            return 'medium'
        elif score >= 25:
            return 'low'
        return ''

    df['completeness'] = df['completeness_score'].apply(_tier)
    avg_score = df['completeness_score'].mean()
    below_50 = (df['completeness_score'] < 50).sum()
    below_25 = (df['completeness_score'] < 25).sum()
    report.warn(f'Completeness: avg={avg_score:.1f}%, '
                f'{below_50} records <50%, {below_25} records <25%')
    found = below_25
    report.log(rule, found=found, fixed=0, remaining=found)
    return df


def finalize_blanks(df: pd.DataFrame) -> pd.DataFrame:
    """Convert all NaN/None to empty strings for clean CSV output."""
    return df.fillna('')


# =============================================================================
# Main QC Pipeline
# =============================================================================

def run_qc(
    input_path: str,
    output_path: Optional[str] = None,
    verbose: bool = True,
    report_path: Optional[str] = None,
) -> Tuple[pd.DataFrame, QCReport]:
    """Run the full QC pipeline on a cannabis results dataset."""
    report = QCReport()

    if verbose:
        print(f'\n{"="*70}')
        print(f'CANNLYTICS QC PIPELINE -- Cannabis Results v{VERSION}')
        print(f'{"="*70}')
        print(f'  Input:  {input_path}')
        print(f'  Output: {output_path or input_path}')
        print(f'  Time:   {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        print(f'{"="*70}\n')

    if not os.path.exists(input_path):
        print(f'ERROR: Input file not found: {input_path}')
        sys.exit(1)

    if verbose:
        print('Loading dataset...')

    df = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    report.total_records = len(df)

    if verbose:
        print(f'  Loaded {len(df):,} records with {len(df.columns)} columns')

    rules = [
        ('Rule  1: String whitespace & sentinels',     clean_string_fields),
        ('Rule  2: Product name cleaning',              clean_product_names),
        ('Rule  3: Strain name cleaning',               clean_strain_names),
        ('Rule  4: Product type normalization',         normalize_product_types),
        ('Rule  5: Date validation',                    clean_dates),
        ('Rule  6: Status normalization',               normalize_statuses),
        ('Rule  7: Numeric range validation',           validate_numeric_ranges),
        ('Rule  8: Producer name cleaning',             clean_producer_names),
        ('Rule  9: Lab name cleaning',                  clean_lab_names),
        ('Rule 10: License number formatting',          clean_license_numbers),
        ('Rule 11: State validation',                   validate_states),
        ('Rule 12: Zip code cleaning',                  clean_zip_codes),
        ('Rule 13: Address text cleaning',              clean_address_fields),
        ('Rule 14: Deduplication check',                check_duplicates),
        ('Rule 15: Analysis name normalization',        normalize_analyses),
        ('Rule 16: Results JSON validation',            validate_results_json),
        ('Rule 17: Cross-field consistency',            check_cross_field_consistency),
        ('Rule 18: Completeness scoring',               compute_completeness_score),
    ]

    for rule_name, rule_func in rules:
        if verbose:
            print(f'\n  Running {rule_name}...')
        df = rule_func(df, report)
        if verbose:
            rule_key = list(report.rules.keys())[-1] if report.rules else rule_name
            if rule_key in report.rules:
                r = report.rules[rule_key]
                print(f'    -> Found: {r["found"]:,}  Fixed: {r["fixed"]:,}  '
                      f'Remaining: {r["remaining"]:,}')

    if verbose:
        print('\n  Finalizing blank values...')
    df = finalize_blanks(df)
    report.output_records = len(df)

    out = output_path or input_path
    if verbose:
        print(f'\n  Writing cleaned dataset to: {out}')

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    df.to_csv(out, index=False)

    if verbose:
        file_size = os.path.getsize(out)
        print(f'  Written: {len(df):,} records ({file_size:,} bytes)')

    report.print_report()

    if report_path:
        os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
        with open(report_path, 'w') as f:
            json.dump(report.to_dict(), f, indent=2)
        if verbose:
            print(f'\n  QC report saved to: {report_path}')

    return df, report


# =============================================================================
# CLI Entry Point
# =============================================================================

def main():
    """Command-line interface for the QC pipeline."""
    parser = argparse.ArgumentParser(
        description='Cannlytics Cannabis Results -- Quality Control & Validation',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Pipeline Position:
  get_results_*.py -> agg_results.py -> qc_results.py -> enrichment -> [delivery]

  QC runs AFTER agg_results.py and BEFORE any enrichment pipeline
  so downstream scripts work with validated, normalized data.

Examples:
  python qc_results.py
  python qc_results.py --input .build/cannabis-results.csv
  python qc_results.py --verbose --report qc-results-report.json
  python qc_results.py --input data.csv --output cleaned.csv
        """,
    )
    parser.add_argument(
        '--input', '-i', type=str, default=None,
        help=f'Input CSV path (default: {DEFAULT_INPUT})',
    )
    parser.add_argument(
        '--output', '-o', type=str, default=None,
        help='Output CSV path (default: overwrite input)',
    )
    parser.add_argument(
        '--verbose', '-v', action='store_true', default=True,
        help='Print detailed progress messages (default: True)',
    )
    parser.add_argument(
        '--quiet', '-q', action='store_true',
        help='Suppress progress messages',
    )
    parser.add_argument(
        '--report', '-r', type=str, default=None,
        help='Path to save QC report as JSON',
    )
    args = parser.parse_args()

    input_path = args.input
    if input_path is None:
        candidates = [
            DEFAULT_INPUT,
            'cannabis-results.csv',
            os.path.join('.build', 'cannabis-results.csv'),
            os.path.join('build', 'cannabis-results.csv'),
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                input_path = candidate
                break
        if input_path is None:
            print('ERROR: No input file specified and could not auto-detect.')
            print('       Use --input <path> to specify the dataset CSV.')
            sys.exit(1)

    verbose = args.verbose and not args.quiet

    df, report = run_qc(
        input_path=input_path,
        output_path=args.output,
        verbose=verbose,
        report_path=args.report,
    )

    if report.total_remaining > 0:
        print(f'\n  Warning: {report.total_remaining:,} issues remain -- review flagged records.')

    print('\n  QC pipeline complete.\n')


if __name__ == '__main__':
    main()