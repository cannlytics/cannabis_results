"""
Cannlytics Cannabis Results -- Test Results
Copyright (c) 2026 Cannlytics

Final pre-delivery validation for the Cannlytics Cannabis Results Dataset.
Runs a comprehensive suite of checks against the production CSV and raises
errors on any invalid values. Designed to execute after
``create_results_dataset.py`` and before packaging / delivery.

Pipeline Position:
    Stage 4 of 4 -- Validation (runs AFTER create_results_dataset.py)

    agg_results → qc_results → create_results_dataset → **test_results**

Usage:
    python test_results.py [--csv PATH] [--strict] [--verbose]

Exit codes:
    0  All checks passed
    1  One or more checks failed
"""
# Standard imports.
import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Set

# External imports.
import pandas as pd


# =============================================================================
# Constants
# =============================================================================

# Expected schema (44 columns, exact order).
EXPECTED_COLUMNS: List[str] = [
    # Identifiers
    'pdf_hash',
    'sample_id',
    'state',
    'source',
    # Product information
    'product_name',
    'strain_name',
    'product_type',
    'batch_number',
    'batch_size',
    'sample_weight',
    # Producer information
    'producer',
    'producer_street',
    'producer_city',
    'producer_state',
    'producer_zipcode',
    'producer_license_number',
    # Distributor information
    'distributor',
    'distributor_license_number',
    # Laboratory information
    'lab',
    'lab_license_number',
    'lab_address',
    'lab_city',
    'lab_state',
    'lab_zipcode',
    # Dates
    'date_tested',
    'date_received',
    'date_collected',
    # Cannabinoid totals
    'total_thc',
    'total_cbd',
    'total_cannabinoids',
    # Terpene total
    'total_terpenes',
    # Contaminant statuses
    'status',
    'pesticides_status',
    'heavy_metals_status',
    'microbials_status',
    'residual_solvents_status',
    'moisture_content',
    'water_activity',
    # Analyses & detailed results
    'analyses',
    'results',
    # Parsing metadata
    'parsing_model',
    'parsing_cost',
    # Aggregation & QC metadata
    'date_aggregated',
    'completeness',
]

# Minimum acceptable record count (safety threshold).
MIN_RECORD_COUNT = 500

# Valid U.S. state codes (lowercase, as stored in the dataset).
VALID_STATE_CODES: Set[str] = {
    'al', 'ak', 'az', 'ar', 'ca', 'co', 'ct', 'de', 'dc', 'fl',
    'ga', 'hi', 'id', 'il', 'in', 'ia', 'ks', 'ky', 'la', 'me',
    'md', 'ma', 'mi', 'mn', 'ms', 'mo', 'mt', 'ne', 'nv', 'nh',
    'nj', 'nm', 'ny', 'nc', 'nd', 'oh', 'ok', 'or', 'pa', 'ri',
    'sc', 'sd', 'tn', 'tx', 'ut', 'vt', 'va', 'wa', 'wv', 'wi', 'wy',
}

# Canonical product types (after QC normalization).
VALID_PRODUCT_TYPES: Set[str] = {
    'flower', 'preroll', 'concentrate', 'vape',
    'edible', 'tincture', 'topical',
}

# Valid contaminant/safety status values.
VALID_STATUS_VALUES: Set[str] = {'pass', 'fail', 'nt', 'n/a'}

# Status columns in the dataset.
STATUS_COLUMNS: List[str] = [
    'status', 'pesticides_status', 'heavy_metals_status',
    'microbials_status', 'residual_solvents_status',
]

# Valid completeness tier values.
VALID_COMPLETENESS_TIERS: Set[str] = {'high', 'medium', 'low'}

# Canonical analysis types (after QC normalization).
VALID_ANALYSIS_TYPES: Set[str] = {
    'cannabinoids', 'terpenes', 'pesticides', 'heavy_metals',
    'microbials', 'residual_solvents', 'moisture_foreign_matter',
}

# Required keys in each element of the ``results`` JSON list.
REQUIRED_RESULT_KEYS: Set[str] = {'key', 'value'}

# Numeric range validation rules: (column, min, max, description).
NUMERIC_RANGES = [
    ('total_thc',          0, 100,  'Total THC (%)'),
    ('total_cbd',          0, 100,  'Total CBD (%)'),
    ('total_cannabinoids', 0, 100,  'Total cannabinoids (%)'),
    ('total_terpenes',     0,  20,  'Total terpenes (%)'),
    ('moisture_content',   0,  20,  'Moisture content (%)'),
    ('water_activity',     0,   1,  'Water activity (aW)'),
    ('parsing_cost',       0,  10,  'Parsing cost ($)'),
]

# SHA-256 hex pattern (64 lowercase hexadecimal characters).
SHA256_PATTERN = re.compile(r'^[0-9a-f]{64}$')

# Date format pattern (YYYY-MM-DD).
DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')

# Placeholder values that should never appear in production data.
PLACEHOLDER_VALUES: Set[str] = {
    'N/A', 'n/a', 'N/a',
    'Not Available', 'not available', 'NOT AVAILABLE',
    'Unknown', 'unknown', 'UNKNOWN',
    'TBD', 'tbd', 'Tbd',
    'None', 'none', 'NONE',
    'NULL', 'null', 'Null',
    'NA', 'na',
    '-', '--', '---',
    'pending', 'Pending review',
    'not provided', 'Not Provided',
    'unspecified', 'Unspecified',
    'nan', 'NaN', 'NAN',
    'inf', '-inf',
}

# States that must be present in any production dataset.
REQUIRED_STATES: Set[str] = {'ca', 'fl'}


# =============================================================================
# Test Framework
# =============================================================================

class TestResult:
    """Container for a single test outcome."""

    def __init__(
        self,
        name: str,
        passed: bool,
        message: str,
        details: Optional[list] = None,
        severity: str = 'ERROR',
    ):
        self.name = name
        self.passed = passed
        self.message = message
        self.details = details or []
        self.severity = severity  # ERROR or WARNING

    def __repr__(self):
        status = '✅ PASS' if self.passed else f'❌ {self.severity}'
        return f'{status} | {self.name}: {self.message}'


class TestSuite:
    """Test runner for the Cannabis Results dataset."""

    def __init__(
        self,
        csv_path: str,
        strict: bool = False,
        verbose: bool = False,
    ):
        self.csv_path = csv_path
        self.strict = strict
        self.verbose = verbose
        self.results: List[TestResult] = []
        self.df: Optional[pd.DataFrame] = None

    def run(self) -> int:
        """Run all tests and return exit code (0 = pass, 1 = fail)."""
        print('=' * 72)
        print('  CANNLYTICS CANNABIS RESULTS — VALIDATION SUITE')
        print(f'  File: {self.csv_path}')
        print(f'  Date: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        print('=' * 72)
        print()

        # Phase 0: File-level checks (before pandas load).
        self._test_file_encoding()
        self._test_csv_parseable()

        # Bail out if we couldn't load the data.
        if self.df is None:
            try:
                self.df = pd.read_csv(self.csv_path, dtype=str,
                                      keep_default_na=False)
            except Exception as e:
                self._record('CSV Load', False, f'Failed to load CSV: {e}')
                return self._summarize()

        # Phase 1: Schema checks.
        self._test_column_count()
        self._test_column_names()
        self._test_record_count()

        # Phase 2: Required field completeness.
        self._test_required_fields()

        # Phase 3: Value validation.
        self._test_state_codes()
        self._test_state_lowercase()
        self._test_lab_state_codes()
        self._test_producer_state_codes()
        self._test_product_type_values()
        self._test_status_values()
        self._test_completeness_tier_values()
        self._test_parsing_model_values()
        self._test_source_values()

        # Phase 4: Numeric range validation.
        self._test_numeric_ranges()

        # Phase 5: JSON structure validation.
        self._test_analyses_json()
        self._test_results_json()

        # Phase 6: Date format validation.
        self._test_date_formats()

        # Phase 7: Cross-field consistency.
        self._test_thc_cannabinoid_consistency()
        self._test_state_lab_state_consistency()
        self._test_analyses_results_alignment()
        self._test_status_analyses_consistency()

        # Phase 8: Data integrity.
        self._test_no_placeholder_values()
        self._test_no_whitespace_padding()
        self._test_no_bracket_artifacts()
        self._test_hash_format()
        self._test_duplicate_hashes()
        self._test_sort_order()
        self._test_required_states_present()
        self._test_data_freshness()

        # Phase 9: Statistical sanity (warnings, not errors).
        self._test_thc_distribution_sanity()
        self._test_state_distribution_balance()
        self._test_analysis_type_distribution()

        # Phase 10: Coverage thresholds (warnings, not errors).
        self._test_coverage_thresholds()

        return self._summarize()

    # ── Phase 0: File-Level Checks ──────────────────────────────────────

    def _test_file_encoding(self):
        """Verify UTF-8 encoding without BOM."""
        name = 'UTF-8 Encoding'
        try:
            with open(self.csv_path, 'rb') as f:
                raw = f.read(3)
                has_bom = raw == b'\xef\xbb\xbf'

            if has_bom:
                self._record(name, False,
                             'File has UTF-8 BOM — remove BOM for clean CSV')
                return

            with open(self.csv_path, 'r', encoding='utf-8') as f:
                _ = f.read()

            self._record(name, True, 'Valid UTF-8 encoding, no BOM')
        except UnicodeDecodeError as e:
            self._record(name, False, f'Encoding error: {e}')

    def _test_csv_parseable(self):
        """Verify CSV can be parsed without quoting or delimiter issues."""
        name = 'CSV Parseability'
        try:
            self.df = pd.read_csv(self.csv_path, dtype=str,
                                  keep_default_na=False)
            self._record(name, True,
                         f'CSV parsed successfully ({len(self.df):,} rows)')
        except Exception as e:
            self._record(name, False, f'CSV parse error: {e}')

    # ── Phase 1: Schema Checks ──────────────────────────────────────────

    def _test_column_count(self):
        """Verify the exact number of delivery columns."""
        name = 'Column Count'
        n = len(self.df.columns)
        expected = len(EXPECTED_COLUMNS)
        self._record(name, n == expected,
                     f'{n} columns (expected {expected})')

    def _test_column_names(self):
        """Verify column names and order match the delivery schema."""
        name = 'Column Names'
        actual = list(self.df.columns)
        if actual == EXPECTED_COLUMNS:
            self._record(name, True,
                         f'All {len(EXPECTED_COLUMNS)} column names match'
                         ' schema in correct order')
        else:
            missing = set(EXPECTED_COLUMNS) - set(actual)
            extra = set(actual) - set(EXPECTED_COLUMNS)
            details = []
            if missing:
                details.append(f'Missing: {sorted(missing)}')
            if extra:
                details.append(f'Extra: {sorted(extra)}')
            if not missing and not extra:
                for i, (a, e) in enumerate(zip(actual, EXPECTED_COLUMNS)):
                    if a != e:
                        details.append(
                            f'Column {i}: got "{a}", expected "{e}"')
            self._record(name, False, 'Column mismatch', details)

    def _test_record_count(self):
        """Verify minimum record count threshold."""
        name = 'Record Count'
        n = len(self.df)
        self._record(name, n >= MIN_RECORD_COUNT,
                     f'{n:,} records (minimum: {MIN_RECORD_COUNT:,})')

    # ── Phase 2: Required Field Completeness ────────────────────────────

    def _test_required_fields(self):
        """Verify 100% coverage on fields that must never be empty."""
        name = 'Required Fields'
        required = ['pdf_hash', 'state', 'date_aggregated']
        missing: Dict[str, int] = {}
        for col in required:
            if col not in self.df.columns:
                missing[col] = len(self.df)
                continue
            empty_count = int((self.df[col].str.strip() == '').sum())
            if empty_count > 0:
                missing[col] = empty_count

        if missing:
            details = [f'{col}: {cnt} empty' for col, cnt in missing.items()]
            self._record(name, False,
                         f'{len(missing)} required fields have gaps', details)
        else:
            self._record(name, True,
                         f'All {len(required)} required fields have'
                         ' 100% coverage')

    # ── Phase 3: Value Validation ───────────────────────────────────────

    def _test_state_codes(self):
        """Verify all state values are valid lowercase U.S. state codes."""
        name = 'State Codes'
        if 'state' not in self.df.columns:
            self._record(name, False, 'state column missing')
            return
        actual = set(self.df['state'].loc[self.df['state'] != ''].unique())
        invalid = actual - VALID_STATE_CODES
        if invalid:
            counts = {
                v: int((self.df['state'] == v).sum()) for v in invalid
            }
            details = [f'"{v}": {c} records' for v, c in counts.items()]
            self._record(name, False,
                         f'{len(invalid)} invalid state codes', details)
        else:
            self._record(name, True,
                         f'{len(actual)} valid state codes')

    def _test_state_lowercase(self):
        """Verify all state codes are lowercase (QC should have normalized)."""
        name = 'State Lowercase'
        state_cols = ['state', 'producer_state', 'lab_state']
        uppercase_found = []
        for col in state_cols:
            if col not in self.df.columns:
                continue
            non_empty = self.df[col].loc[self.df[col] != '']
            has_upper = non_empty[non_empty.str.contains(r'[A-Z]', na=False)]
            if len(has_upper) > 0:
                samples = has_upper.head(5).tolist()
                uppercase_found.append(
                    f'{col}: {len(has_upper)} values with uppercase'
                    f' (e.g. {samples})')

        if uppercase_found:
            self._record(name, False,
                         f'{len(uppercase_found)} state fields with'
                         ' uppercase values', uppercase_found)
        else:
            self._record(name, True,
                         'All state fields are lowercase')

    def _test_lab_state_codes(self):
        """Verify lab_state values are valid state codes where present."""
        name = 'Lab State Codes'
        if 'lab_state' not in self.df.columns:
            self._record(name, True, 'lab_state column not present',
                         severity='WARNING')
            return
        non_empty = self.df['lab_state'].loc[self.df['lab_state'] != '']
        if len(non_empty) == 0:
            self._record(name, True, 'No lab_state values present')
            return
        actual = set(non_empty.unique())
        invalid = actual - VALID_STATE_CODES
        if invalid:
            counts = {
                v: int((non_empty == v).sum()) for v in invalid
            }
            details = [f'"{v}": {c} records' for v, c in counts.items()]
            self._record(name, False,
                         f'{len(invalid)} invalid lab state codes', details)
        else:
            self._record(name, True,
                         f'All {len(non_empty):,} lab_state values valid')

    def _test_producer_state_codes(self):
        """Verify producer_state values are valid state codes where present."""
        name = 'Producer State Codes'
        if 'producer_state' not in self.df.columns:
            self._record(name, True, 'producer_state column not present',
                         severity='WARNING')
            return
        non_empty = self.df['producer_state'].loc[
            self.df['producer_state'] != ''
        ]
        if len(non_empty) == 0:
            self._record(name, True, 'No producer_state values present')
            return
        actual = set(non_empty.unique())
        invalid = actual - VALID_STATE_CODES
        if invalid:
            counts = {
                v: int((non_empty == v).sum()) for v in invalid
            }
            details = [f'"{v}": {c} records' for v, c in counts.items()]
            self._record(name, False,
                         f'{len(invalid)} invalid producer state codes',
                         details)
        else:
            self._record(name, True,
                         f'All {len(non_empty):,} producer_state values'
                         ' valid')

    def _test_product_type_values(self):
        """Verify product_type values are canonical after QC."""
        name = 'Product Type Values'
        if 'product_type' not in self.df.columns:
            self._record(name, False, 'product_type column missing')
            return
        actual = set(
            self.df['product_type']
            .loc[self.df['product_type'] != '']
            .unique()
        )
        invalid = actual - VALID_PRODUCT_TYPES
        if invalid:
            counts = {
                v: int((self.df['product_type'] == v).sum())
                for v in invalid
            }
            details = [f'"{v}": {c} records' for v, c in counts.items()]
            if self.strict:
                self._record(name, False,
                             f'{len(invalid)} non-canonical product types',
                             details)
            else:
                self._record(name, True,
                             f'{len(invalid)} non-canonical product types'
                             ' (warning — review recommended)',
                             details, severity='WARNING')
        else:
            self._record(name, True,
                         f'All product types canonical: {sorted(actual)}')

    def _test_status_values(self):
        """Verify all status fields contain only valid values."""
        name = 'Status Values'
        errors = []
        for col in STATUS_COLUMNS:
            if col not in self.df.columns:
                continue
            actual = set(
                self.df[col].loc[self.df[col] != ''].unique()
            )
            invalid = actual - VALID_STATUS_VALUES
            if invalid:
                counts = {
                    v: int((self.df[col] == v).sum()) for v in invalid
                }
                errors.append(
                    f'{col}: {sorted(invalid)} '
                    f'({sum(counts.values())} records)')
        if errors:
            self._record(name, False,
                         f'{len(errors)} status fields with invalid values',
                         errors)
        else:
            self._record(name, True,
                         f'All {len(STATUS_COLUMNS)} status fields valid')

    def _test_completeness_tier_values(self):
        """Verify completeness values are valid tier labels."""
        name = 'Completeness Tier Values'
        if 'completeness' not in self.df.columns:
            self._record(name, True, 'completeness column not present',
                         severity='WARNING')
            return
        actual = set(
            self.df['completeness']
            .loc[self.df['completeness'] != '']
            .unique()
        )
        invalid = actual - VALID_COMPLETENESS_TIERS
        if invalid:
            counts = {
                v: int((self.df['completeness'] == v).sum())
                for v in invalid
            }
            details = [f'"{v}": {c} records' for v, c in counts.items()]
            self._record(name, False,
                         f'{len(invalid)} invalid completeness values',
                         details)
        else:
            self._record(name, True,
                         f'All completeness values valid: {sorted(actual)}')

    def _test_parsing_model_values(self):
        """Verify parsing_model values are non-empty where present."""
        name = 'Parsing Model Values'
        if 'parsing_model' not in self.df.columns:
            self._record(name, True, 'parsing_model column not present',
                         severity='WARNING')
            return
        non_empty = self.df['parsing_model'].loc[
            self.df['parsing_model'] != ''
        ]
        models = sorted(non_empty.unique())
        self._record(name, True,
                     f'{len(non_empty):,} records with model: {models}')

    def _test_source_values(self):
        """Verify source field contains non-empty, reasonable identifiers."""
        name = 'Source Values'
        if 'source' not in self.df.columns:
            self._record(name, False, 'source column missing')
            return
        non_empty = self.df['source'].loc[self.df['source'] != '']
        if len(non_empty) == 0:
            self._record(name, False, 'No source values present')
            return

        sources = sorted(non_empty.unique())
        details = [f'Sources ({len(sources)}): {sources}']

        # Check for suspicious values (URLs, paths, very long strings).
        suspicious = non_empty[
            non_empty.str.contains(r'[/\\]|https?://', na=False)
            | (non_empty.str.len() > 100)
        ]
        if len(suspicious) > 0:
            samples = suspicious.head(5).tolist()
            details.append(
                f'{len(suspicious)} suspicious source values'
                f' (e.g. {samples})')
            self._record(name, True,
                         f'{len(sources)} sources found'
                         f' ({len(suspicious)} suspicious — review)',
                         details, severity='WARNING')
        else:
            self._record(name, True,
                         f'{len(sources)} valid sources,'
                         f' {len(non_empty):,} records',
                         details)

    # ── Phase 4: Numeric Range Validation ───────────────────────────────

    def _test_numeric_ranges(self):
        """Verify numeric fields fall within scientifically valid ranges."""
        name = 'Numeric Ranges'
        errors = []
        for col, vmin, vmax, desc in NUMERIC_RANGES:
            if col not in self.df.columns:
                continue
            vals = pd.to_numeric(
                self.df[col].replace('', pd.NA), errors='coerce',
            ).dropna()
            if len(vals) == 0:
                continue

            below = vals[vals < vmin]
            above = vals[vals > vmax]

            if len(below) > 0:
                samples = below.head(5).tolist()
                errors.append(
                    f'{desc} ({col}): {len(below)} values below {vmin}'
                    f' (e.g. {samples})')

            if len(above) > 0:
                samples = above.head(5).tolist()
                errors.append(
                    f'{desc} ({col}): {len(above)} values above {vmax}'
                    f' (e.g. {samples})')

        if errors:
            self._record(name, False,
                         f'{len(errors)} numeric range violations', errors)
        else:
            checked = sum(
                1 for col, _, _, _ in NUMERIC_RANGES
                if col in self.df.columns
            )
            self._record(name, True,
                         f'All {checked} numeric fields within valid ranges')

    # ── Phase 5: JSON Structure Validation ──────────────────────────────

    def _test_analyses_json(self):
        """Verify the analyses column contains valid JSON lists of strings."""
        name = 'Analyses JSON Structure'
        if 'analyses' not in self.df.columns:
            self._record(name, False, 'analyses column missing')
            return

        non_empty = self.df['analyses'].loc[self.df['analyses'] != '']
        errors = 0
        invalid_types = 0
        non_canonical = set()
        sample_errors: List[str] = []

        for idx, val in non_empty.items():
            try:
                parsed = json.loads(val)
                if not isinstance(parsed, list):
                    invalid_types += 1
                    if len(sample_errors) < 3:
                        sample_errors.append(
                            f'Row {idx}: expected list, got '
                            f'{type(parsed).__name__}')
                    continue
                for item in parsed:
                    if not isinstance(item, str):
                        invalid_types += 1
                        break
                    if item not in VALID_ANALYSIS_TYPES:
                        non_canonical.add(item)
            except (json.JSONDecodeError, TypeError):
                errors += 1
                if len(sample_errors) < 3:
                    sample_errors.append(
                        f'Row {idx}: invalid JSON: {str(val)[:60]}')

        details = []
        if errors > 0:
            details.append(f'{errors} rows with invalid JSON')
        if invalid_types > 0:
            details.append(f'{invalid_types} rows with non-list or'
                           ' non-string elements')
        if non_canonical:
            details.append(
                f'Non-canonical analysis types: {sorted(non_canonical)}')
        details.extend(sample_errors)

        has_hard_errors = errors > 0 or invalid_types > 0
        if has_hard_errors:
            self._record(name, False,
                         f'{errors + invalid_types} JSON structure issues',
                         details)
        elif non_canonical:
            self._record(name, True,
                         f'{len(non_canonical)} non-canonical analysis types'
                         ' (warning — review recommended)',
                         details, severity='WARNING')
        else:
            self._record(name, True,
                         f'{len(non_empty):,} analyses values are valid JSON'
                         ' lists with canonical types')

    def _test_results_json(self):
        """Verify the results column contains valid JSON lists of objects."""
        name = 'Results JSON Structure'
        if 'results' not in self.df.columns:
            self._record(name, False, 'results column missing')
            return

        non_empty = self.df['results'].loc[
            (self.df['results'] != '') & (self.df['results'] != '[]')
        ]
        errors = 0
        invalid_structure = 0
        missing_keys = 0
        total_analytes = 0
        sample_errors: List[str] = []

        for idx, val in non_empty.items():
            try:
                parsed = json.loads(val)
                if not isinstance(parsed, list):
                    invalid_structure += 1
                    if len(sample_errors) < 3:
                        sample_errors.append(
                            f'Row {idx}: expected list, got '
                            f'{type(parsed).__name__}')
                    continue

                total_analytes += len(parsed)

                for entry in parsed:
                    if not isinstance(entry, dict):
                        invalid_structure += 1
                        break
                    present_keys = set(entry.keys())
                    if not REQUIRED_RESULT_KEYS.issubset(present_keys):
                        missing_keys += 1
                        if len(sample_errors) < 3:
                            absent = REQUIRED_RESULT_KEYS - present_keys
                            sample_errors.append(
                                f'Row {idx}: missing keys {absent}')
                        break

            except (json.JSONDecodeError, TypeError):
                errors += 1
                if len(sample_errors) < 3:
                    sample_errors.append(
                        f'Row {idx}: invalid JSON: {str(val)[:60]}')

        details = []
        if errors > 0:
            details.append(f'{errors} rows with invalid JSON')
        if invalid_structure > 0:
            details.append(f'{invalid_structure} rows with non-list or'
                           ' non-dict structure')
        if missing_keys > 0:
            details.append(f'{missing_keys} rows with entries missing'
                           f' required keys {REQUIRED_RESULT_KEYS}')
        details.append(f'Total analyte measurements: {total_analytes:,}')
        details.extend(sample_errors)

        has_hard_errors = errors > 0 or invalid_structure > 0
        if has_hard_errors:
            self._record(name, False,
                         f'{errors + invalid_structure} JSON structure issues',
                         details)
        elif missing_keys > 0:
            if self.strict:
                self._record(name, False,
                             f'{missing_keys} entries missing required keys',
                             details)
            else:
                self._record(name, True,
                             f'{missing_keys} entries missing required keys'
                             ' (warning)',
                             details, severity='WARNING')
        else:
            self._record(name, True,
                         f'{len(non_empty):,} results values valid'
                         f' ({total_analytes:,} analyte measurements)',
                         details[:1])

    # ── Phase 6: Date Format Validation ─────────────────────────────────

    def _test_date_formats(self):
        """Verify all date fields are valid YYYY-MM-DD format."""
        name = 'Date Formats'
        date_columns = [
            'date_tested', 'date_received', 'date_collected',
            'date_aggregated',
        ]
        errors = []

        for col in date_columns:
            if col not in self.df.columns:
                continue

            non_empty = self.df[col].loc[self.df[col] != '']
            if len(non_empty) == 0:
                continue

            # Regex check.
            bad_format = non_empty[
                ~non_empty.str.match(DATE_PATTERN, na=False)
            ]
            if len(bad_format) > 0:
                samples = bad_format.head(5).tolist()
                errors.append(
                    f'{col}: {len(bad_format)} invalid date formats'
                    f' (e.g. {samples})')
                continue

            # Calendar validity check.
            invalid_dates = []
            for val in non_empty:
                try:
                    datetime.strptime(val, '%Y-%m-%d')
                except ValueError:
                    invalid_dates.append(val)
            if invalid_dates:
                errors.append(
                    f'{col}: {len(invalid_dates)} invalid calendar dates'
                    f' (e.g. {invalid_dates[:5]})')

        if errors:
            self._record(name, False,
                         f'{len(errors)} date field issues', errors)
        else:
            total = sum(
                (self.df[c] != '').sum() for c in date_columns
                if c in self.df.columns
            )
            self._record(name, True,
                         f'All {total:,} date values are valid YYYY-MM-DD')

    # ── Phase 7: Cross-Field Consistency ────────────────────────────────

    def _test_thc_cannabinoid_consistency(self):
        """Verify total_thc <= total_cannabinoids where both are present.

        A small tolerance (2 percentage points) accounts for rounding
        differences in lab instruments and AI parsing.
        """
        name = 'THC ≤ Cannabinoids Consistency'
        cols = ['total_thc', 'total_cannabinoids']
        if not all(c in self.df.columns for c in cols):
            self._record(name, True, 'Required columns not present',
                         severity='WARNING')
            return

        thc = pd.to_numeric(
            self.df['total_thc'].replace('', pd.NA), errors='coerce')
        cann = pd.to_numeric(
            self.df['total_cannabinoids'].replace('', pd.NA),
            errors='coerce')

        both = thc.notna() & cann.notna()
        if both.sum() == 0:
            self._record(name, True, 'No rows with both values present')
            return

        tolerance = 2.0
        violations = self.df[both & (thc > cann + tolerance)]

        if len(violations) > 0:
            samples = violations[
                ['pdf_hash', 'state', 'total_thc', 'total_cannabinoids']
            ].head(5).to_dict('records')
            details = [
                f'{len(violations)} records where THC > cannabinoids'
                f' + {tolerance}%',
                f'Samples: {samples}',
            ]
            if self.strict:
                self._record(name, False,
                             f'{len(violations)} consistency violations',
                             details)
            else:
                self._record(name, True,
                             f'{len(violations)} THC > cannabinoids'
                             ' (warning — review)', details,
                             severity='WARNING')
        else:
            self._record(name, True,
                         f'THC ≤ cannabinoids for all'
                         f' {both.sum():,} comparable records')

    def _test_state_lab_state_consistency(self):
        """Verify lab_state is consistent with the record's state.

        Labs occasionally test samples from neighboring states, so
        mismatches are flagged as warnings, not hard errors.
        """
        name = 'State-Lab State Consistency'
        if not all(c in self.df.columns for c in ['state', 'lab_state']):
            self._record(name, True, 'Required columns not present',
                         severity='WARNING')
            return

        both = (self.df['state'] != '') & (self.df['lab_state'] != '')
        if both.sum() == 0:
            self._record(name, True, 'No rows with both state and lab_state')
            return

        mismatches = self.df[
            both & (self.df['state'] != self.df['lab_state'])
        ]

        if len(mismatches) > 0:
            pct = len(mismatches) / both.sum() * 100
            state_pairs = (
                mismatches[['state', 'lab_state']]
                .value_counts()
                .head(5)
                .to_dict()
            )
            pairs_display = {
                f'{k[0]}→{k[1]}': v for k, v in state_pairs.items()
            }
            details = [
                f'{len(mismatches)} records ({pct:.1f}%) where'
                ' state ≠ lab_state',
                f'Top mismatches: {pairs_display}',
            ]
            self._record(name, True,
                         f'{len(mismatches)} state/lab_state mismatches'
                         ' (review — may be cross-state testing)',
                         details, severity='WARNING')
        else:
            self._record(name, True,
                         f'state = lab_state for all'
                         f' {both.sum():,} comparable records')

    def _test_analyses_results_alignment(self):
        """Verify records with analyses also have results, and vice versa.

        A record listing ``["cannabinoids", "terpenes"]`` in analyses but
        having ``[]`` in results indicates a parsing failure.
        """
        name = 'Analyses-Results Alignment'
        if not all(c in self.df.columns for c in ['analyses', 'results']):
            self._record(name, True, 'Required columns not present',
                         severity='WARNING')
            return

        has_analyses = (self.df['analyses'] != '') & (
            self.df['analyses'] != '[]')
        has_results = (self.df['results'] != '') & (
            self.df['results'] != '[]')

        # Records with analyses but empty results.
        analyses_no_results = has_analyses & ~has_results
        # Records with results but no analyses.
        results_no_analyses = has_results & ~has_analyses

        details = []
        issues = 0

        if analyses_no_results.sum() > 0:
            n = int(analyses_no_results.sum())
            issues += n
            samples = (
                self.df.loc[analyses_no_results, 'analyses']
                .head(3).tolist()
            )
            details.append(
                f'{n} records with analyses but empty results'
                f' (e.g. {samples})')

        if results_no_analyses.sum() > 0:
            n = int(results_no_analyses.sum())
            issues += n
            details.append(
                f'{n} records with results but empty analyses')

        if issues > 0:
            if self.strict:
                self._record(name, False,
                             f'{issues} alignment mismatches', details)
            else:
                self._record(name, True,
                             f'{issues} alignment mismatches (warning)',
                             details, severity='WARNING')
        else:
            aligned = int((has_analyses & has_results).sum())
            self._record(name, True,
                         f'Analyses and results aligned for'
                         f' {aligned:,} records')

    def _test_status_analyses_consistency(self):
        """Verify status fields are consistent with listed analyses.

        If pesticides_status is 'pass' or 'fail', the analyses list should
        contain 'pesticides'. Mismatches indicate parsing errors.
        """
        name = 'Status-Analyses Consistency'
        status_analysis_map = {
            'pesticides_status': 'pesticides',
            'heavy_metals_status': 'heavy_metals',
            'microbials_status': 'microbials',
            'residual_solvents_status': 'residual_solvents',
        }

        if 'analyses' not in self.df.columns:
            self._record(name, True, 'analyses column not present',
                         severity='WARNING')
            return

        mismatches = []
        for status_col, analysis_name in status_analysis_map.items():
            if status_col not in self.df.columns:
                continue

            has_status = self.df[status_col].isin({'pass', 'fail'})
            if has_status.sum() == 0:
                continue

            # Check if analyses lists the expected type.
            status_rows = self.df[has_status]
            missing_analysis = 0
            for _, row in status_rows.iterrows():
                try:
                    analyses_list = json.loads(row['analyses']) \
                        if row['analyses'] else []
                except (json.JSONDecodeError, TypeError):
                    analyses_list = []
                if analysis_name not in analyses_list:
                    missing_analysis += 1

            if missing_analysis > 0:
                mismatches.append(
                    f'{status_col}: {missing_analysis}/{len(status_rows)}'
                    f' records have status but "{analysis_name}" not in'
                    ' analyses')

        if mismatches:
            self._record(name, True,
                         f'{len(mismatches)} status/analyses mismatches'
                         ' (warning — review)',
                         mismatches, severity='WARNING')
        else:
            self._record(name, True,
                         'Status fields consistent with analyses lists')

    # ── Phase 7: Data Integrity ─────────────────────────────────────────

    def _test_no_placeholder_values(self):
        """Verify no AI / sentinel placeholder values remain."""
        name = 'No Placeholder Values'
        text_columns = [
            'product_name', 'strain_name', 'product_type',
            'producer', 'lab', 'batch_number',
            'status', 'pesticides_status', 'heavy_metals_status',
        ]
        findings = []
        for col in text_columns:
            if col not in self.df.columns:
                continue
            matches = self.df[col].isin(PLACEHOLDER_VALUES)
            count = int(matches.sum())
            if count > 0:
                vals = self.df.loc[matches, col].unique()[:5]
                findings.append(
                    f'{col}: {count} placeholder values'
                    f' (e.g. {list(vals)})')

        if findings:
            self._record(name, False,
                         f'{len(findings)} fields with placeholders',
                         findings)
        else:
            self._record(name, True, 'No placeholder values detected')

    def _test_no_whitespace_padding(self):
        """Verify no leading or trailing whitespace in key text fields."""
        name = 'No Whitespace Padding'
        text_columns = [
            'product_name', 'strain_name', 'product_type', 'state',
            'producer', 'lab', 'batch_number', 'source',
        ]
        findings = []
        for col in text_columns:
            if col not in self.df.columns:
                continue
            non_empty = self.df[col].loc[self.df[col] != '']
            padded = non_empty[non_empty != non_empty.str.strip()]
            if len(padded) > 0:
                samples = padded.head(3).apply(repr).tolist()
                findings.append(
                    f'{col}: {len(padded)} values with whitespace'
                    f' (e.g. {samples})')

        if findings:
            self._record(name, False,
                         f'{len(findings)} fields with whitespace padding',
                         findings)
        else:
            self._record(name, True, 'No whitespace padding detected')

    def _test_no_bracket_artifacts(self):
        """Check for bracket artifacts in key fields (warning-level).

        Brackets like ``[Company Name]`` or ``[Not Provided]`` are common
        AI parsing artifacts that should have been cleaned in QC.
        """
        name = 'Bracket Artifacts'
        bracket_pattern = re.compile(r'\[.*?\]')
        check_columns = [
            'product_name', 'strain_name', 'producer', 'lab',
            'distributor', 'batch_number',
        ]
        findings = []
        for col in check_columns:
            if col not in self.df.columns:
                continue
            matches = self.df[col].str.contains(
                bracket_pattern, na=False)
            count = int(matches.sum())
            if count > 0:
                samples = self.df.loc[matches, col].head(3).tolist()
                findings.append(
                    f'{col}: {count} values with brackets'
                    f' (e.g. {samples})')

        if findings:
            self._record(name, True,
                         f'{len(findings)} fields with bracket patterns'
                         ' (review recommended)',
                         findings, severity='WARNING')
        else:
            self._record(name, True, 'No bracket artifacts detected')

    def _test_hash_format(self):
        """Verify pdf_hash values are valid SHA-256 hex strings."""
        name = 'Hash Format (SHA-256)'
        if 'pdf_hash' not in self.df.columns:
            self._record(name, False, 'pdf_hash column missing')
            return

        non_empty = self.df['pdf_hash'].loc[self.df['pdf_hash'] != '']
        bad = non_empty[~non_empty.str.match(SHA256_PATTERN, na=False)]

        if len(bad) > 0:
            samples = bad.head(5).tolist()
            self._record(name, False,
                         f'{len(bad)} invalid SHA-256 hashes',
                         [f'Samples: {samples}'])
        else:
            self._record(name, True,
                         f'All {len(non_empty):,} hashes are valid SHA-256')

    def _test_duplicate_hashes(self):
        """Verify pdf_hash values are unique (no duplicate COAs)."""
        name = 'Duplicate Hashes'
        if 'pdf_hash' not in self.df.columns:
            self._record(name, False, 'pdf_hash column missing')
            return

        non_empty = self.df['pdf_hash'].loc[self.df['pdf_hash'] != '']
        dupes = non_empty[non_empty.duplicated(keep=False)]

        if len(dupes) == 0:
            self._record(name, True,
                         f'All {len(non_empty):,} pdf_hash values are unique')
            return

        n_unique_dupes = dupes.nunique()
        n_total_rows = len(dupes)

        # Check if duplicates are across different states (legitimate
        # multi-state products).
        dupe_df = self.df.loc[dupes.index]
        if 'state' in dupe_df.columns:
            multi_state = dupe_df.groupby('pdf_hash')['state'].nunique()
            cross_state = int((multi_state > 1).sum())
            same_state = n_unique_dupes - cross_state
        else:
            cross_state = 0
            same_state = n_unique_dupes

        details = [
            f'{n_total_rows} rows share {n_unique_dupes} duplicate hashes',
            f'Cross-state duplicates: {cross_state} (may be legitimate)',
            f'Same-state duplicates: {same_state} (potential errors)',
        ]

        if same_state > 0:
            samples = (
                dupe_df.groupby('pdf_hash')
                .filter(lambda g: g['state'].nunique() == 1)
                ['pdf_hash'].unique()[:5]
            )
            details.append(f'Samples: {list(samples)}')

        if same_state > 0:
            self._record(name, False,
                         f'{same_state} same-state duplicate hash groups',
                         details)
        else:
            self._record(name, True,
                         f'{cross_state} cross-state duplicates (legitimate)',
                         details, severity='WARNING')

    def _test_sort_order(self):
        """Verify records are sorted: state (asc) → date_tested (desc) → product_name (asc).

        This is the canonical sort order produced by create_results_dataset.py.
        """
        name = 'Sort Order'
        required_cols = ['state', 'date_tested', 'product_name']
        if not all(c in self.df.columns for c in required_cols):
            self._record(name, True, 'Required sort columns not present',
                         severity='WARNING')
            return

        if len(self.df) < 2:
            self._record(name, True, 'Too few records to check sort order')
            return

        # Build the expected sort: state ascending, date_tested
        # descending (blanks last), product_name ascending.
        df_check = self.df[required_cols].copy()

        # Create sort key for date (descending means reversed).
        # Fill blanks with a value that sorts last when descending.
        df_check['_date_sort'] = df_check['date_tested'].replace(
            '', '0000-00-00')

        expected = df_check.sort_values(
            by=['state', '_date_sort', 'product_name'],
            ascending=[True, False, True],
        ).index

        is_sorted = (self.df.index == expected).all()

        if not is_sorted:
            # Find the first out-of-order row.
            first_bad = None
            for i, (actual_idx, expected_idx) in enumerate(
                zip(self.df.index, expected)
            ):
                if actual_idx != expected_idx:
                    first_bad = i
                    break
            details = [
                f'First out-of-order row at position {first_bad}',
                f'Expected sort: state (asc) → date_tested (desc)'
                f' → product_name (asc)',
            ]
            self._record(name, False,
                         'Records are not in canonical sort order',
                         details)
        else:
            self._record(name, True,
                         f'{len(self.df):,} records in canonical'
                         ' sort order')

    def _test_required_states_present(self):
        """Verify key states (CA, FL) are present in the dataset."""
        name = 'Required States Present'
        if 'state' not in self.df.columns:
            self._record(name, False, 'state column missing')
            return
        actual = set(self.df['state'].unique())
        missing = REQUIRED_STATES - actual
        if missing:
            self._record(name, False,
                         f'Missing required states: {sorted(missing)}')
        else:
            counts = {
                s: int((self.df['state'] == s).sum())
                for s in sorted(REQUIRED_STATES)
            }
            self._record(name, True,
                         f'Required states present: {counts}')

    def _test_data_freshness(self):
        """Verify date_aggregated is reasonably current."""
        name = 'Data Freshness'
        if 'date_aggregated' not in self.df.columns:
            self._record(name, True, 'date_aggregated column not present',
                         severity='WARNING')
            return

        non_empty = self.df['date_aggregated'].loc[
            self.df['date_aggregated'] != ''
        ]
        if len(non_empty) == 0:
            self._record(name, False, 'No date_aggregated values present')
            return

        dates = pd.to_datetime(non_empty, errors='coerce')
        dates = dates.dropna()
        if len(dates) == 0:
            self._record(name, False, 'No parseable date_aggregated values')
            return

        latest = dates.max()
        oldest = dates.min()
        now = datetime.now()
        days_old = (now - latest).days

        details = [
            f'Latest aggregation: {latest.strftime("%Y-%m-%d")}',
            f'Oldest aggregation: {oldest.strftime("%Y-%m-%d")}',
            f'Days since latest: {days_old}',
        ]

        # Current if aggregated within the last 30 days.
        if days_old <= 30:
            self._record(name, True,
                         f'Data is current (latest:'
                         f' {latest.strftime("%Y-%m-%d")})',
                         details)
        else:
            self._record(name, False,
                         f'Data may be stale ({days_old} days old)',
                         details)

    # ── Phase 9: Statistical Sanity ────────────────────────────────────

    def _test_thc_distribution_sanity(self):
        """Verify THC values have a sensible distribution.

        Flags if mean THC is unreasonably low (< 5%) or high (> 50%),
        or if all values are identical (indicating a data processing bug).
        """
        name = 'THC Distribution Sanity'
        if 'total_thc' not in self.df.columns:
            self._record(name, True, 'total_thc column not present',
                         severity='WARNING')
            return

        thc = pd.to_numeric(
            self.df['total_thc'].replace('', pd.NA), errors='coerce'
        ).dropna()

        if len(thc) < 10:
            self._record(name, True,
                         f'Only {len(thc)} THC values — insufficient'
                         ' for distribution check',
                         severity='WARNING')
            return

        mean_thc = thc.mean()
        std_thc = thc.std()
        median_thc = thc.median()
        n_unique = thc.nunique()

        details = [
            f'n={len(thc):,}, mean={mean_thc:.1f}%,'
            f' median={median_thc:.1f}%, std={std_thc:.2f}%,'
            f' unique={n_unique}',
        ]

        issues = []
        if mean_thc < 5:
            issues.append(f'Mean THC unusually low: {mean_thc:.1f}%')
        if mean_thc > 50:
            issues.append(f'Mean THC unusually high: {mean_thc:.1f}%')
        if n_unique == 1:
            issues.append(
                f'All THC values identical: {thc.iloc[0]:.2f}%'
                ' — possible data bug')
        if std_thc == 0 and len(thc) > 1:
            issues.append('Zero variance in THC values — possible data bug')

        if issues:
            details.extend(issues)
            self._record(name, True,
                         f'{len(issues)} THC distribution concerns',
                         details, severity='WARNING')
        else:
            self._record(name, True,
                         f'THC distribution reasonable'
                         f' (mean={mean_thc:.1f}%,'
                         f' median={median_thc:.1f}%,'
                         f' n={len(thc):,})',
                         details)

    def _test_state_distribution_balance(self):
        """Verify no single state dominates > 95% of all records.

        An extreme imbalance may indicate a pipeline issue where only one
        collector ran, rather than intentional single-state focus.
        """
        name = 'State Distribution Balance'
        if 'state' not in self.df.columns:
            self._record(name, True, 'state column not present',
                         severity='WARNING')
            return

        counts = self.df['state'].value_counts()
        total = len(self.df)
        top_state = counts.index[0]
        top_pct = counts.iloc[0] / total * 100

        details = []
        for state_code, cnt in counts.head(5).items():
            pct = cnt / total * 100
            details.append(f'{state_code}: {cnt:,} ({pct:.1f}%)')

        if top_pct > 95 and len(counts) > 1:
            self._record(name, True,
                         f'{top_state} has {top_pct:.1f}% of records'
                         ' — possible pipeline issue',
                         details, severity='WARNING')
        else:
            self._record(name, True,
                         f'{len(counts)} states, largest is {top_state}'
                         f' ({top_pct:.1f}%)',
                         details)

    def _test_analysis_type_distribution(self):
        """Verify cannabinoids is the most common analysis type.

        Every COA should have potency data, so cannabinoids should appear
        in the vast majority of records.
        """
        name = 'Analysis Type Distribution'
        if 'analyses' not in self.df.columns:
            self._record(name, True, 'analyses column not present',
                         severity='WARNING')
            return

        non_empty = self.df['analyses'].loc[
            (self.df['analyses'] != '') & (self.df['analyses'] != '[]')
        ]
        if len(non_empty) < 10:
            self._record(name, True,
                         'Too few analyses records for distribution check',
                         severity='WARNING')
            return

        type_counts: Dict[str, int] = {}
        for val in non_empty:
            try:
                analyses_list = json.loads(val)
                if isinstance(analyses_list, list):
                    for a in analyses_list:
                        if isinstance(a, str):
                            type_counts[a] = type_counts.get(a, 0) + 1
            except (json.JSONDecodeError, TypeError):
                continue

        if not type_counts:
            self._record(name, False, 'No valid analysis types found')
            return

        details = []
        for analysis, count in sorted(
            type_counts.items(), key=lambda x: -x[1]
        ):
            pct = count / len(non_empty) * 100
            details.append(f'{analysis}: {count:,} ({pct:.1f}%)')

        cannabinoid_count = type_counts.get('cannabinoids', 0)
        cannabinoid_pct = cannabinoid_count / len(non_empty) * 100

        if cannabinoid_count == 0:
            self._record(name, False,
                         'No cannabinoid analyses found — critical issue',
                         details)
        elif cannabinoid_pct < 80:
            self._record(name, True,
                         f'Cannabinoids at {cannabinoid_pct:.1f}%'
                         ' (below 80% — review)',
                         details, severity='WARNING')
        else:
            self._record(name, True,
                         f'Cannabinoids at {cannabinoid_pct:.1f}%'
                         f' ({cannabinoid_count:,} records)',
                         details)

    # ── Phase 10: Coverage Thresholds ───────────────────────────────────

    def _test_coverage_thresholds(self):
        """Verify field coverage meets minimum thresholds (warnings)."""
        name = 'Coverage Thresholds'
        thresholds = {
            'product_name':       0.90,
            'product_type':       0.90,
            'total_thc':          0.80,
            'total_cbd':          0.60,
            'total_cannabinoids': 0.60,
            'lab':                0.80,
            'date_tested':        0.70,
            'analyses':           0.80,
            'results':            0.70,
        }

        below = []
        for col, threshold in thresholds.items():
            if col not in self.df.columns:
                below.append(
                    f'{col}: column missing (threshold: {threshold:.0%})')
                continue
            actual = (self.df[col] != '').mean()
            if actual < threshold:
                below.append(
                    f'{col}: {actual:.1%} (threshold: {threshold:.0%})')

        if below:
            self._record(name, True,
                         f'{len(below)} fields below coverage thresholds'
                         ' (review recommended)',
                         below, severity='WARNING')
        else:
            self._record(name, True,
                         'All fields meet coverage thresholds')

    # ── Utilities ───────────────────────────────────────────────────────

    def _record(
        self,
        name: str,
        passed: bool,
        message: str,
        details: Optional[list] = None,
        severity: str = 'ERROR',
    ):
        """Record a test result and print immediately."""
        result = TestResult(name, passed, message, details or [], severity)
        self.results.append(result)

        icon = '✅' if passed else (
            '⚠️' if severity == 'WARNING' else '❌')
        print(f'  {icon} {name}: {message}')
        if self.verbose and result.details:
            for d in result.details:
                print(f'      → {d}')

    def _summarize(self) -> int:
        """Print summary and return exit code."""
        print()
        print('=' * 72)

        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = [
            r for r in self.results
            if not r.passed and r.severity == 'ERROR'
        ]
        warnings = [
            r for r in self.results
            if r.passed and r.severity == 'WARNING'
        ]

        print(f'  RESULTS: {passed}/{total} passed | '
              f'{len(failed)} errors | {len(warnings)} warnings')
        print()

        if failed:
            print('  ❌ FAILURES:')
            for r in failed:
                print(f'     • {r.name}: {r.message}')
                for d in r.details:
                    print(f'       → {d}')
            print()

        if warnings:
            print('  ⚠️  WARNINGS:')
            for r in warnings:
                print(f'     • {r.name}: {r.message}')
                if self.verbose:
                    for d in r.details:
                        print(f'       → {d}')
            print()

        if failed:
            print('  ████ VALIDATION FAILED ████')
            print(f'  {len(failed)} critical issue(s) must be resolved'
                  ' before delivery.')
        else:
            print('  ✅ VALIDATION PASSED — Dataset is ready for'
                  ' packaging.')

        print('=' * 72)
        return 1 if failed else 0


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Validate the Cannlytics Cannabis Results dataset.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Pipeline Position:
    Stage 4 of 4 — runs AFTER create_results_dataset.py

Examples:
    python test_results.py
    python test_results.py --csv output/cannabis-results-latest.csv
    python test_results.py --strict --verbose
        """,
    )
    parser.add_argument(
        '--csv',
        default=os.path.join(
            os.environ.get('CANNLYTICS_OUTPUT_DIR', r'D:\data\.output'),
            'cannabis-results-latest.csv',
        ),
        help='Path to the CSV file to validate.',
    )
    parser.add_argument(
        '--strict', action='store_true',
        help='Treat warnings as errors (non-canonical product types, etc.).',
    )
    parser.add_argument(
        '--verbose', action='store_true',
        help='Show detailed information for all results.',
    )
    args = parser.parse_args()

    if not os.path.exists(args.csv):
        print(f'ERROR: File not found: {args.csv}')
        sys.exit(1)

    suite = TestSuite(args.csv, strict=args.strict, verbose=args.verbose)
    exit_code = suite.run()
    sys.exit(exit_code)


if __name__ == '__main__':
    main()