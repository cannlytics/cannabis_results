"""
Cannlytics Cannabis Results — License Merge & Entity Standardization
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2026-03-04
Updated: 2026-03-04
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:
    Merge cannabis license data with parsed lab results to establish
    reliable M:1 foreign-key linkage between the two core Cannlytics
    datasets. This script:

    1. Loads the post-QC results build file and the licenses dataset
    2. Normalizes license numbers in both datasets for matching
    3. Builds a license lookup index (license_number → license record)
    4. Matches results' 3 license fields against the license index
    5. Canonicalizes entity names (producer, distributor, lab) when matched
    6. Detects potential role misassignment (e.g., producer license in lab field)
    7. Reports comprehensive match statistics
    8. Saves the enriched build file

    Design Rationale — Foreign Key Approach:
        Rather than enriching all 20+ license fields × 3 entity roles
        (60+ columns of redundant, stale-prone data), this script
        establishes reliable M:1 keys between the datasets. The two
        datasets update on different cadences (licenses weekly, results
        as parsed), and embedding full license records into results
        creates data integrity issues. Instead, we:
        - Standardize license numbers for exact matching
        - Canonicalize entity names using license data as ground truth
        - Add match quality metadata for provenance tracking
        - Keep the datasets separate but reliably joinable

Pipeline Position:
    This is Stage 4.5, between QC and Dataset Compilation.
    Input:  .build/cannabis-results.csv (post-QC)
    Deps:   cannabis-licenses-latest.csv
    Output: .build/cannabis-results.csv (enriched, in-place)
    Next:   create_results_dataset.py (Stage 5)

Usage:
    python merge_licenses.py
    python merge_licenses.py --licenses "path/to/licenses.csv"
    python merge_licenses.py --dry-run
    python merge_licenses.py --report merge-report.json
    python merge_licenses.py --quiet

Changelog:
    v1.0.0 (2026-03-04):
        - Initial version with license number normalization and matching
        - Entity name canonicalization from license ground truth
        - Role misassignment detection (cross-category matching)
        - Fuzzy matching fallback for minor format variations
        - Comprehensive match statistics and provenance tracking
"""
# Standard imports:
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# External imports:
import numpy as np
import pandas as pd


# =============================================================================
# Path Resolution
# =============================================================================

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from config.results_config import PATHS as _PATHS
    DEFAULT_BUILD_DIR = str(_PATHS.build_dir)
except ImportError:
    # Fallback: .build/ lives in the repo root (not D:/data).
    DEFAULT_BUILD_DIR = os.environ.get(
        'CANNLYTICS_BUILD_DIR',
        os.path.join(_REPO_ROOT, '.build'),
    )

# Default licenses path (Hugging Face dataset output).
DEFAULT_LICENSES_PATH = os.path.join(
    os.path.expanduser('~'),
    'OneDrive', 'Cannlytics', 'huggingface', 'datasets',
    'cannabis_licenses', 'output',
    'cannlytics-cannabis-licenses-latest.csv',
)


# =============================================================================
# Configuration
# =============================================================================

VERSION = '1.0.0'

# License number fields in the results dataset and their corresponding
# entity name field and license category expectation.
LICENSE_FIELDS = {
    'producer_license_number': {
        'name_field': 'producer',
        'expected_categories': {
            'Cultivation', 'Manufacturing/Processing',
            'Vertically Integrated', 'Microbusiness',
            'Distribution/Transport', 'Other/Unclassified',
        },
        'role': 'producer',
    },
    'distributor_license_number': {
        'name_field': 'distributor',
        'expected_categories': {
            'Distribution/Transport', 'Vertically Integrated',
            'Microbusiness', 'Other/Unclassified',
            'Manufacturing/Processing',
        },
        'role': 'distributor',
    },
    'lab_license_number': {
        'name_field': 'lab',
        'expected_categories': {
            'Testing Laboratory',
        },
        'role': 'lab',
    },
}

# License categories in the licenses dataset.
LICENSE_CATEGORIES = {
    'Cultivation', 'Retail/Dispensary', 'Manufacturing/Processing',
    'Distribution/Transport', 'Testing Laboratory', 'Microbusiness',
    'Vertically Integrated', 'Other/Unclassified',
}

# Legal suffixes to strip for name comparison (not from the data, just
# for fuzzy matching purposes).
LEGAL_SUFFIXES = [
    r'L\.?L\.?C\.?',
    r'P\.?L\.?L\.?C\.?',
    r'L\.?L\.?P\.?',
    r'L\.?P\.?',
    r'Inc(?:orporated)?\.?',
    r'Corp(?:oration)?\.?',
    r'Ltd\.?',
    r'Limited',
    r'Co\.?',
    r'P\.?C\.?',
]

LEGAL_SUFFIX_PATTERN = re.compile(
    r'(?:(?:[,.]\s*|\s+)(?:' + '|'.join(LEGAL_SUFFIXES) + r'))+\.?\s*$',
    flags=re.IGNORECASE,
)

# Sentinel values treated as missing.
SENTINEL_VALUES: Set[str] = {
    'nan', 'none', 'n/a', 'na', 'null', 'undefined', 'not available',
    'unknown', 'tbd', '-', '--', '---', '.', '', 'not applicable',
}

# =============================================================================
# License Number Normalization — Patterns
# =============================================================================

# Prefix patterns to strip from AI-parsed license numbers.
# Ordered from most specific (longest) to least specific (shortest)
# so the first match wins without clobbering partial matches.
_PREFIX_PATTERNS = [
    re.compile(r'^FL\s+LICENSE\s*#?\s*', re.IGNORECASE),
    re.compile(r'^LICENSE\s*#?\s*', re.IGNORECASE),
    re.compile(r'^LIC\.?\s*#?\s*', re.IGNORECASE),
    re.compile(r'^DEA\s+NO\.?\s*', re.IGNORECASE),
    re.compile(r'^DEA\s+', re.IGNORECASE),
    re.compile(r'^CLIA\s+NO\.?\s*', re.IGNORECASE),
]

# The "-LIC" suffix appears across ALL states (CA, FL, NY, etc.).
_LIC_SUFFIX_RE = re.compile(r'-LIC$', re.IGNORECASE)

# State prefix on FL licenses: "FL-CMTL-0003" or "FL CMTL-0003".
_FL_STATE_PREFIX_RE = re.compile(r'^FL[\s-]+', re.IGNORECASE)

# Compound value separators: "/", ";", ",".
# Split and take the first license-like token.
_COMPOUND_SPLIT_RE = re.compile(r'\s*[/;,]\s*')

# Values that are clearly NOT cannabis license numbers.
_REJECT_PATTERNS = [
    re.compile(r'^CLIA\b', re.IGNORECASE),    # CLIA accreditation IDs
    re.compile(r'XXXXX', re.IGNORECASE),       # Placeholder values
    re.compile(r'^10D\d{7}$'),                 # CLIA IDs (10D1094068)
    re.compile(r'^[A-Z]{2}\d{5}-\d{2}$'),     # FL accreditation IDs (IA15009-04, HL26012-01)
    re.compile(r'^MTM-\d{4}$'),                # Non-standard FL IDs
]

# License type keywords — reject if found in the "number" field.
_TYPE_KEYWORDS = [
    'cultivation', 'retail', 'dispensary', 'manufacturing',
    'processing', 'distribution', 'transport', 'testing',
    'laboratory', 'microbusiness', 'vertically', 'integrated',
    'adult', 'medical', 'license type', 'license_type',
]

# Location suffixes sometimes appended by AI parser.
_LOCATION_SUFFIX_RE = re.compile(
    r'\s+(?:APOPKA|MIAMI|TAMPA|ORLANDO|JACKSONVILLE|'
    r'TALLAHASSEE|GAINESVILLE|CLEARWATER|SARASOTA)\s*$',
    re.IGNORECASE,
)

# NY "smashed" prefix patterns — AI omits the hyphen between
# "OCM" and the license type code.
_NY_SMASHED_PREFIXES = {
    'OCMPROC':  'OCM-PROC',
    'OCMMICR':  'OCM-MICR',
    'OCMMICRO': 'OCM-MICR',   # Alternate spelling
    'OCMPRQC':  'OCM-PROC',   # Typo: PRQC → PROC
    'OCMPPCL':  'OCM-CPL',    # Typo: PPCL → CPL
    'OCMPCCL':  'OCM-CPL',    # Typo: PCCL → CPL
    'OCMPC':    'OCM-CPL',    # Truncated
}

# NY partial prefix patterns — missing "OCM-" entirely.
_NY_PARTIAL_PREFIXES = {
    'AUCP-':    'OCM-AUCP-',
    'AUCC-':    'OCM-AUCC-',
    'CPL-':     'OCM-CPL-',
    'PL-':      'OCM-CPL-',   # Further truncated
}

# NY "#" separator: "OCM-CPL # 00005" → "OCM-CPL-00005".
_NY_HASH_SEP_RE = re.compile(r'^(OCM-[A-Z\d]+)\s*#\s*(\d+)$', re.IGNORECASE)

# NY hyphenated truncation repairs — AI truncates type codes.
# These differ from _NY_SMASHED_PREFIXES (which lack the hyphen).
_NY_HYPHENATED_TRUNCATIONS = {
    'OCM-MICRO-':  'OCM-MICR-',    # OCM-MICRO → OCM-MICR
    'OCM-PRO-':    'OCM-PROC-',    # OCM-PRO → OCM-PROC
    'OCM-PL-':     'OCM-CPL-',     # OCM-PL → OCM-CPL (truncated)
    'OCM-IST-':    'OCM-DIST-',    # OCM-IST → OCM-DIST (missing D)
}

# NY site/phase suffixes: "OCM-MICR-21-000058-P1" → strip -P1.
_NY_SITE_SUFFIX_RE = re.compile(r'-P\d+$', re.IGNORECASE)


# =============================================================================
# License Number Normalization
# =============================================================================

def normalize_license_number(value: Any, state: Optional[str] = None) -> str:
    """Normalize a license number for matching.

    Applies a comprehensive series of cleaning steps to maximize match
    rate between license numbers as they appear on COAs (AI-extracted)
    versus as they appear in regulatory databases.

    Processing order:
        1. String conversion, whitespace/quote stripping
        2. Compound value splitting (take first license)
        3. Prefix stripping (LIC#, LICENSE#, FL LICENSE#, DEA NO, etc.)
        4. FL state prefix stripping (FL-CMTL → CMTL)
        5. Uppercase
        6. -LIC suffix stripping (universal, all states)
        7. Location suffix stripping (APOPKA, MIAMI, etc.)
        8. NY smashed/partial prefix repair
        9. Punctuation cleanup and whitespace collapse
        10. Sentinel and non-license value rejection

    Args:
        value: Raw license number value.
        state: Optional two-letter state code for state-specific rules.

    Returns:
        Normalized license number string, or empty string if invalid.
    """
    if value is None or pd.isna(value):
        return ''
    s = str(value).strip()
    if not s:
        return ''

    # Remove surrounding quotes.
    s = s.strip('"\'')
    if not s:
        return ''

    # ── Step 1: Split compound values ─────────────────────────────
    # "00000111ESTX14447382/00000012DCJT00224887" → first part
    # "RA0571996 / CMTL-0003" → first part
    # "CMTL-0003; CLIA NO. 10D1094068" → first part
    if any(sep in s for sep in ['/', ';', ',']):
        parts = _COMPOUND_SPLIT_RE.split(s)
        s = next((p.strip() for p in parts if p.strip()), s)

    # ── Step 2: Strip text prefixes ───────────────────────────────
    # "LIC# 00000034DCOD00007550" → "00000034DCOD00007550"
    # "FL LICENSE # CMTL-0003" → "CMTL-0003"
    # "DEA NO. RA0571996" → "RA0571996"
    for pattern in _PREFIX_PATTERNS:
        s_new = pattern.sub('', s).strip()
        if s_new != s:
            s = s_new
            break  # Only strip the first matching prefix.

    # ── Step 3: Strip FL state prefix ─────────────────────────────
    # "FL-CMTL-0003" → "CMTL-0003", "FL CMTL-0003" → "CMTL-0003"
    if state and state.upper() == 'FL':
        s = _FL_STATE_PREFIX_RE.sub('', s).strip()

    # ── Step 4: Uppercase ─────────────────────────────────────────
    s = s.upper()

    # ── Step 5: Strip "-LIC" suffix (universal) ──────────────────
    s = _LIC_SUFFIX_RE.sub('', s).strip()

    # ── Step 6: CA space-to-hyphen ────────────────────────────────
    # "DCC 1000533" → "DCC-1000533"
    if state and state.upper() == 'CA':
        s = re.sub(r'^(DCC|CDPH|CCL\d{2}|C\d{1,2})\s+', r'\1-', s)

    # ── Step 7: Strip location suffixes ───────────────────────────
    # "MMTC-2019-0017 APOPKA" → "MMTC-2019-0017"
    s = _LOCATION_SUFFIX_RE.sub('', s).strip()

    # ── Step 8: NY prefix repair ──────────────────────────────────
    if state and state.upper() == 'NY':
        # Fix hyphenated truncations first (more specific).
        # "OCM-MICRO-25-000234" → "OCM-MICR-25-000234"
        s_upper = s
        for trunc, correct in sorted(
            _NY_HYPHENATED_TRUNCATIONS.items(),
            key=lambda x: len(x[0]),
            reverse=True,
        ):
            if s_upper.startswith(trunc.upper()):
                s = correct.upper() + s_upper[len(trunc):]
                break

        # Fix smashed prefixes (no hyphen between OCM and type).
        # "OCMPROC-24-000063" → "OCM-PROC-24-000063"
        s_upper = s
        for smashed, correct in sorted(
            _NY_SMASHED_PREFIXES.items(),
            key=lambda x: len(x[0]),
            reverse=True,
        ):
            if s_upper.startswith(smashed.upper()):
                rest = s_upper[len(smashed):]
                s = correct.upper() + rest
                break

        # Fix partial prefixes (missing "OCM-").
        s_upper = s
        for partial, full in _NY_PARTIAL_PREFIXES.items():
            if s_upper.startswith(partial.upper()):
                s = full.upper() + s_upper[len(partial):]
                break

        # Fix "#" separator: "OCM-CPL # 00005" → "OCM-CPL-00005".
        m = _NY_HASH_SEP_RE.match(s)
        if m:
            s = f'{m.group(1).upper()}-{m.group(2)}'

        # Strip site/phase suffixes: "-P1", "-P2".
        s = _NY_SITE_SUFFIX_RE.sub('', s)

    # ── Step 8: Clean up ──────────────────────────────────────────
    s = re.sub(r'^[\-._]+|[\-._]+$', '', s)
    s = re.sub(r'\s+', ' ', s).strip()

    # ── Step 9: Reject invalid values ─────────────────────────────
    if not s:
        return ''
    if s.lower() in SENTINEL_VALUES:
        return ''
    if any(kw in s.lower() for kw in _TYPE_KEYWORDS):
        return ''
    for pattern in _REJECT_PATTERNS:
        if pattern.search(s):
            return ''

    return s


def build_license_index(
    licenses_df: pd.DataFrame,
) -> Tuple[Dict[str, pd.Series], Dict[str, List[pd.Series]]]:
    """Build a license lookup index from the licenses dataset.

    Creates two indexes:
        1. Primary index: normalized license_number → license record
           (first match wins for duplicates within same state)
        2. Name index: normalized business name → list of license records
           (used for fuzzy matching when license number doesn't match)

    Args:
        licenses_df: Full licenses DataFrame.

    Returns:
        Tuple of (license_number_index, business_name_index).
    """
    lic_index: Dict[str, pd.Series] = {}
    name_index: Dict[str, List[pd.Series]] = defaultdict(list)

    for _, row in licenses_df.iterrows():
        state = str(row.get('premise_state', '')).upper()
        raw_lic = row.get('license_number', '')
        norm_lic = normalize_license_number(raw_lic, state=state)

        if norm_lic:
            # Primary key: normalized license number.
            # For duplicates, keep the first (typically the most complete).
            if norm_lic not in lic_index:
                lic_index[norm_lic] = row

        # Name index for fuzzy fallback.
        for name_col in ('business_dba_name', 'business_legal_name', 'business_brand'):
            name = str(row.get(name_col, '')).strip()
            if name and name.lower() not in SENTINEL_VALUES:
                norm_name = _normalize_entity_name(name)
                if norm_name:
                    name_index[norm_name].append(row)

    return lic_index, name_index


# =============================================================================
# Entity Name Normalization
# =============================================================================

def _normalize_entity_name(name: str) -> str:
    """Normalize an entity name for comparison.

    Strips legal suffixes, collapses whitespace, and lowercases for
    fuzzy comparison. NOT applied to the output data — only used for
    matching logic.

    Args:
        name: Raw entity name.

    Returns:
        Normalized name for comparison purposes.
    """
    if not name or not name.strip():
        return ''
    s = str(name).strip()
    s = LEGAL_SUFFIX_PATTERN.sub('', s).strip()
    s = re.sub(r'[^\w\s]', '', s)  # Remove punctuation.
    s = re.sub(r'\s+', ' ', s).strip().lower()
    return s


def canonicalize_entity_name(
    raw_name: str,
    matched_license: Optional[pd.Series],
) -> str:
    """Derive canonical entity name from license match.

    When a license number match is found, the license dataset provides
    the authoritative business name. We prefer:
        1. business_dba_name (consumer-facing)
        2. business_brand (normalized brand)
        3. business_legal_name (fallback)

    If no match is found, the original AI-parsed name is returned
    with basic cleaning applied.

    Args:
        raw_name: Entity name as parsed from the COA.
        matched_license: Matched license record, or None.

    Returns:
        Canonicalized entity name.
    """
    if matched_license is not None:
        # Prefer DBA → brand → legal name.
        for col in ('business_dba_name', 'business_brand', 'business_legal_name'):
            candidate = str(matched_license.get(col, '')).strip()
            if candidate and candidate.lower() not in SENTINEL_VALUES:
                return candidate

    # No match or no usable name in license data — clean the raw name.
    if not raw_name or str(raw_name).strip().lower() in SENTINEL_VALUES:
        return ''
    cleaned = str(raw_name).strip()
    cleaned = cleaned.strip('"\'')
    cleaned = re.sub(r'[\s,;.]+$', '', cleaned)
    cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()
    return cleaned


# =============================================================================
# License Matching Engine
# =============================================================================

class MatchResult:
    """Result of a license matching attempt."""
    __slots__ = (
        'matched', 'match_type', 'license_record',
        'category_match', 'canonical_name', 'original_number',
        'normalized_number', 'matched_number',
    )

    def __init__(self):
        self.matched: bool = False
        self.match_type: str = 'unmatched'
        self.license_record: Optional[pd.Series] = None
        self.category_match: bool = True
        self.canonical_name: str = ''
        self.original_number: str = ''
        self.normalized_number: str = ''
        self.matched_number: str = ''


def match_license(
    license_number: str,
    entity_name: str,
    state: str,
    role: str,
    expected_categories: Set[str],
    lic_index: Dict[str, pd.Series],
    name_index: Dict[str, List[pd.Series]],
) -> MatchResult:
    """Attempt to match a license number against the license index.

    Matching strategy (in priority order):
        1. Exact normalized match on license number
        2. Prefix/suffix-relaxed match (strip common state prefixes)
        3. Name-based fuzzy match (when license number fails)

    For each match, also checks whether the license category is
    consistent with the expected role (e.g., a 'Testing Laboratory'
    license should not appear as a producer license number).

    Args:
        license_number: Raw license number from the results record.
        entity_name: Entity name from the results record.
        state: Two-letter state code for context.
        role: Entity role ('producer', 'distributor', 'lab').
        expected_categories: Set of license categories expected for this role.
        lic_index: License number → license record index.
        name_index: Normalized name → license record(s) index.

    Returns:
        MatchResult with match details and provenance.
    """
    result = MatchResult()
    result.original_number = str(license_number) if license_number else ''

    # Normalize the license number.
    norm_lic = normalize_license_number(license_number, state=state)
    result.normalized_number = norm_lic

    if not norm_lic:
        return result

    # ── Strategy 1: Exact normalized match ────────────────────────
    if norm_lic in lic_index:
        record = lic_index[norm_lic]
        result.matched = True
        result.match_type = 'exact'
        result.license_record = record
        result.matched_number = norm_lic
        result.category_match = _check_category(record, expected_categories)
        result.canonical_name = canonicalize_entity_name(entity_name, record)
        return result

    # ── Strategy 2: Relaxed match (common variations) ─────────────
    # Try stripping common prefixes/suffixes that vary between sources.
    relaxed_variants = _generate_relaxed_variants(norm_lic, state)
    for variant in relaxed_variants:
        if variant in lic_index:
            record = lic_index[variant]
            result.matched = True
            result.match_type = 'relaxed'
            result.license_record = record
            result.matched_number = variant
            result.category_match = _check_category(record, expected_categories)
            result.canonical_name = canonicalize_entity_name(entity_name, record)
            return result

    # ── Strategy 3: Name-based fallback ───────────────────────────
    # Only used when we have a license number that didn't match.
    # Finds licenses with matching names in the same state.
    if entity_name and str(entity_name).strip().lower() not in SENTINEL_VALUES:
        norm_name = _normalize_entity_name(entity_name)
        if norm_name and norm_name in name_index:
            candidates = name_index[norm_name]
            # Filter to same state.
            state_upper = state.upper() if state else ''
            same_state = [
                r for r in candidates
                if str(r.get('premise_state', '')).upper() == state_upper
            ]
            # Filter to expected category.
            role_match = [
                r for r in same_state
                if str(r.get('license_type_category', '')).strip() in expected_categories
            ]
            best = role_match[0] if role_match else (same_state[0] if same_state else None)
            if best is not None:
                result.matched = True
                result.match_type = 'name_fallback'
                result.license_record = best
                result.matched_number = normalize_license_number(
                    best.get('license_number', ''),
                    state=state,
                )
                result.category_match = _check_category(best, expected_categories)
                result.canonical_name = canonicalize_entity_name(entity_name, best)
                return result

    # No match found.
    result.canonical_name = canonicalize_entity_name(entity_name, None)
    return result


def _check_category(
    license_record: pd.Series,
    expected_categories: Set[str],
) -> bool:
    """Check if a license record's category matches the expected role."""
    category = str(license_record.get('license_type_category', '')).strip()
    if not category:
        # Also check license_category (alternate column name).
        category = str(license_record.get('license_category', '')).strip()
    return category in expected_categories if category else True


def _generate_relaxed_variants(
    norm_lic: str,
    state: Optional[str] = None,
) -> List[str]:
    """Generate relaxed variants of a normalized license number.

    Common variations between COAs and regulatory databases:
        - Leading zeros: "0001234" vs "1234"
        - Dash variations: "C10-0001234" vs "C100001234"
        - Prefix variations: "LIC-12345" vs "12345"
        - Extra zeros: "CDPH-100003818" vs "CDPH-10003818"
        - CA typos: "CB-" → "C8-", "BCC-" → "DCC-", "CC1-" → "CCL-"
        - Zero padding: "CCL18-00002160" vs "CCL18-0002160"

    Args:
        norm_lic: Normalized license number.
        state: Optional state code.

    Returns:
        List of variant strings to try matching.
    """
    variants = []

    # Variant: strip leading zeros (but keep at least 1 character).
    stripped = norm_lic.lstrip('0')
    if stripped and stripped != norm_lic:
        variants.append(stripped)

    # Variant: remove all hyphens.
    no_dash = norm_lic.replace('-', '')
    if no_dash != norm_lic:
        variants.append(no_dash)

    # Variant: remove all hyphens and leading zeros.
    no_dash_stripped = no_dash.lstrip('0')
    if no_dash_stripped and no_dash_stripped not in (norm_lic, no_dash, stripped):
        variants.append(no_dash_stripped)

    # Variant: add hyphen after common prefix patterns.
    # E.g., "C100001234" → "C10-0001234"
    m = re.match(r'^([A-Z]+\d{1,3})(\d{4,})$', norm_lic)
    if m:
        with_dash = f'{m.group(1)}-{m.group(2)}'
        if with_dash != norm_lic:
            variants.append(with_dash)

    # Variant: Collapse or expand zero padding in numeric portions.
    # Handles patterns like:
    #   "CDPH-100003818" → "CDPH-10003818"  (extra zero in run)
    #   "DCC-100005001"  → "DCC-10005001"   (extra zero in run)
    #   "CCL18-00002160" → "CCL18-0002160"  (leading zero in segment)
    #   "OCM-PROC-24-0000070" → "OCM-PROC-24-000070"
    # Strategy: find the last numeric segment, then:
    #   a) For each run of 2+ consecutive zeros, try removing one zero
    #   b) Try adding a leading zero to the segment
    segments = re.split(r'(-)', norm_lic)
    for i in range(len(segments) - 1, -1, -1):
        seg = segments[i]
        if re.match(r'^\d{5,}$', seg):
            # (a) Find runs of consecutive zeros and try collapsing each.
            for m_zero in re.finditer(r'0{2,}', seg):
                start, end = m_zero.start(), m_zero.end()
                collapsed = seg[:start] + seg[start+1:end] + seg[end:]
                v = ''.join(segments[:i] + [collapsed] + segments[i+1:])
                if v != norm_lic and v not in variants:
                    variants.append(v)
                break  # Only collapse the first zero-run found.
            # (b) Try adding one leading zero.
            longer = '0' + seg
            v = ''.join(segments[:i] + [longer] + segments[i+1:])
            if v != norm_lic and v not in variants:
                variants.append(v)
            break  # Only process the last numeric segment.

    # CA-specific typo correction variants.
    if state and state.upper() == 'CA':
        ca_typo_map = {
            'CB-':     'C8-',      # OCR confuses B/8
            'BCC-':    'DCC-',     # OCR confuses B/D
            'CC1-':    'C11-',     # Extra C or missing 1
            'CC11-':   'C11-',     # Extra C
            'CC12-':   'C12-',     # Extra C
            'CC21-':   'CCL21-',   # Missing L
            'CCL8-':   'CCL18-',   # Missing 1
            'CCZL2-':  'CCL22-',   # Transposed letters
            'CDDPH-':  'CDPH-',   # Doubled D
            'CGL18-':  'CCL18-',   # G→C typo
            'LL22-':   'CCL22-',   # Missing CC
            'CI11-':   'C11-',     # OCR: I→1 confusion
            'C1-':     'C11-',     # Truncated C11 (also try C12)
        }
        for typo, correct in ca_typo_map.items():
            if norm_lic.startswith(typo):
                variant = correct + norm_lic[len(typo):]
                if variant not in variants:
                    variants.append(variant)
        # C1- could also be C12-
        if norm_lic.startswith('C1-'):
            variant = 'C12-' + norm_lic[3:]
            if variant not in variants:
                variants.append(variant)
        # Missing "C" prefix: "8-0000019" → "C8-0000019"
        if re.match(r'^\d-\d{4,}', norm_lic):
            variant = 'C' + norm_lic
            if variant not in variants:
                variants.append(variant)

    # FL-specific typo correction variants.
    if state and state.upper() == 'FL':
        fl_typo_map = {
            'CMLT-':   'CMTL-',   # Transposed letters
            'CMT-':    'CMTL-',   # Truncated (missing L)
            'FL-CMT-': 'CMTL-',   # State prefix + truncated
        }
        for typo, correct in fl_typo_map.items():
            if norm_lic.startswith(typo):
                variant = correct + norm_lic[len(typo):]
                if variant not in variants:
                    variants.append(variant)

    return variants


# =============================================================================
# Role Misassignment Detection
# =============================================================================

def detect_role_misassignment(
    df: pd.DataFrame,
    lic_index: Dict[str, pd.Series],
    verbose: bool = True,
) -> pd.DataFrame:
    """Detect and flag license numbers assigned to the wrong role.

    Common issue: AI parsing may put a producer license number into
    the lab_license_number field (or vice versa). This function checks
    each license number against all category expectations and flags
    misassignments.

    This function reports misassignments but does NOT automatically
    reassign them — that requires human review to avoid silent data
    corruption.

    Args:
        df: Results DataFrame with license number fields.
        lic_index: License number index.
        verbose: Print diagnostics.

    Returns:
        DataFrame with 'license_role_flags' column added.
    """
    flags_list = []
    misassignment_counts = Counter()

    for idx, row in df.iterrows():
        row_flags = []
        state = str(row.get('state', '')).upper()

        for lic_field, config in LICENSE_FIELDS.items():
            raw_lic = row.get(lic_field, '')
            if not raw_lic or pd.isna(raw_lic) or str(raw_lic).strip() == '':
                continue

            norm_lic = normalize_license_number(raw_lic, state=state)
            if not norm_lic or norm_lic not in lic_index:
                continue

            record = lic_index[norm_lic]
            category = str(record.get('license_type_category', '')).strip()
            if not category:
                category = str(record.get('license_category', '')).strip()

            if category and category not in config['expected_categories']:
                flag = (
                    f'{lic_field}:{norm_lic} is {category}, '
                    f'expected {config["role"]}'
                )
                row_flags.append(flag)
                misassignment_counts[f'{lic_field}→{category}'] += 1

        flags_list.append('; '.join(row_flags) if row_flags else '')

    df['license_role_flags'] = flags_list

    if verbose and any(flags_list):
        n_flagged = sum(1 for f in flags_list if f)
        print(f'\n  ⚠️  Role misassignment detected: {n_flagged:,} records')
        for key, count in misassignment_counts.most_common(20):
            print(f'    {key}: {count:,}')
        print('  Note: Misassignments flagged but NOT auto-corrected.')
        print('        Review license_role_flags column for manual triage.\n')

    return df


# =============================================================================
# Match Statistics
# =============================================================================

class MergeStats:
    """Track and report merge statistics."""

    def __init__(self):
        self.total_records: int = 0
        self.total_licenses: int = 0
        self.index_size: int = 0
        self.field_stats: Dict[str, Dict[str, int]] = {}
        self.category_mismatches: int = 0
        self.role_misassignments: int = 0
        self.names_canonicalized: Dict[str, int] = {}
        self.start_time: datetime = datetime.now()
        self.end_time: Optional[datetime] = None

    def add_field_stats(self, field: str, stats: Dict[str, int]) -> None:
        """Add match statistics for a license field."""
        self.field_stats[field] = stats

    def finalize(self, df: pd.DataFrame) -> None:
        """Finalize statistics after processing."""
        self.end_time = datetime.now()
        if 'license_role_flags' in df.columns:
            self.role_misassignments = int(
                (df['license_role_flags'].fillna('').str.len() > 0).sum()
            )

    @staticmethod
    def _sanitize_for_json(obj: Any) -> Any:
        """Recursively convert numpy/pandas types to native Python types.

        Prevents ``TypeError: Object of type int64 is not JSON
        serializable`` when ``json.dump`` encounters numpy scalars
        that leak in via pandas operations.
        """
        if isinstance(obj, dict):
            return {k: MergeStats._sanitize_for_json(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [MergeStats._sanitize_for_json(v) for v in obj]
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        return obj

    def print_report(self) -> None:
        """Print a human-readable merge report."""
        elapsed = (self.end_time - self.start_time).total_seconds()
        print('\n' + '=' * 70)
        print('  MERGE LICENSES — REPORT')
        print('=' * 70)
        print(f'  Results records:    {self.total_records:>10,}')
        print(f'  License records:    {self.total_licenses:>10,}')
        print(f'  License index size: {self.index_size:>10,}')
        print(f'  Processing time:    {elapsed:>10.1f}s')

        for field, stats in self.field_stats.items():
            total = stats.get('total_populated', 0)
            if total == 0:
                continue
            print(f'\n  ── {field} ──')
            print(f'    Populated:       {total:>8,}')
            for match_type in ('exact', 'relaxed', 'name_fallback', 'unmatched'):
                count = stats.get(match_type, 0)
                pct = 100 * count / total if total else 0
                label = match_type.replace('_', ' ').title()
                print(f'    {label:16s} {count:>8,} ({pct:5.1f}%)')
            cat_mismatch = stats.get('category_mismatch', 0)
            if cat_mismatch:
                print(f'    Category mismatch: {cat_mismatch:>6,}')

        if self.role_misassignments:
            print(f'\n  ⚠️  Role misassignments: {self.role_misassignments:,}')

        for field, count in self.names_canonicalized.items():
            print(f'  Names canonicalized ({field}): {count:,}')

        print('=' * 70 + '\n')

    def to_dict(self) -> Dict[str, Any]:
        """Export statistics as a JSON-serializable dictionary."""
        return MergeStats._sanitize_for_json({
            'version': VERSION,
            'timestamp': self.end_time.isoformat() if self.end_time else None,
            'elapsed_seconds': (
                (self.end_time - self.start_time).total_seconds()
                if self.end_time else None
            ),
            'total_records': self.total_records,
            'total_licenses': self.total_licenses,
            'index_size': self.index_size,
            'field_stats': self.field_stats,
            'role_misassignments': self.role_misassignments,
            'names_canonicalized': self.names_canonicalized,
        })


# =============================================================================
# Core Merge Logic
# =============================================================================

def merge_licenses(
    results_df: pd.DataFrame,
    licenses_df: pd.DataFrame,
    verbose: bool = True,
    dry_run: bool = False,
) -> Tuple[pd.DataFrame, MergeStats]:
    """Merge license data with results for entity standardization.

    This is the main entry point. It performs:
        1. License index construction
        2. Per-field matching (producer, distributor, lab)
        3. Entity name canonicalization
        4. Role misassignment detection
        5. Statistics collection

    Args:
        results_df: Post-QC results DataFrame.
        licenses_df: Cannabis licenses DataFrame.
        verbose: Print progress and statistics.
        dry_run: If True, compute matches but don't modify the DataFrame.

    Returns:
        Tuple of (enriched DataFrame, MergeStats).
    """
    stats = MergeStats()
    stats.total_records = len(results_df)
    stats.total_licenses = len(licenses_df)

    if verbose:
        print(f'\n  Loading {len(results_df):,} results, '
              f'{len(licenses_df):,} licenses...')

    # ── Step 1: Build license index ───────────────────────────────
    if verbose:
        print('  Building license index...')

    lic_index, name_index = build_license_index(licenses_df)
    stats.index_size = len(lic_index)

    if verbose:
        print(f'  Index: {len(lic_index):,} unique license numbers, '
              f'{len(name_index):,} unique entity names')

    # ── Step 2: Match each license field ──────────────────────────
    for lic_field, config in LICENSE_FIELDS.items():
        name_field = config['name_field']
        expected_categories = config['expected_categories']
        role = config['role']

        if verbose:
            print(f'\n  Matching {lic_field}...')

        # Track match types.
        match_counts = Counter()
        category_mismatches = 0
        names_changed = 0

        # Prepare normalized license number column.
        norm_col = f'_norm_{lic_field}'
        results_df[norm_col] = results_df.apply(
            lambda row: normalize_license_number(
                row.get(lic_field, ''),
                state=str(row.get('state', '')),
            ),
            axis=1,
        )

        # Count populated.
        populated_mask = results_df[norm_col].str.len() > 0
        n_populated = populated_mask.sum()

        if n_populated == 0:
            if verbose:
                print(f'    No populated values for {lic_field}. Skipping.')
            stats.add_field_stats(lic_field, {'total_populated': 0})
            results_df.drop(columns=[norm_col], inplace=True)
            continue

        # Match status columns.
        match_type_col = f'{role}_license_match'
        matched_lic_col = f'_matched_{lic_field}'

        match_types = []
        matched_lics = []
        canonical_names = []

        for idx, row in results_df.iterrows():
            raw_lic = row.get(lic_field, '')
            entity_name = row.get(name_field, '')
            state = str(row.get('state', '')).upper()

            if not row[norm_col]:
                match_types.append('')
                matched_lics.append('')
                canonical_names.append(
                    canonicalize_entity_name(entity_name, None)
                )
                continue

            result = match_license(
                license_number=raw_lic,
                entity_name=entity_name,
                state=state,
                role=role,
                expected_categories=expected_categories,
                lic_index=lic_index,
                name_index=name_index,
            )

            match_counts[result.match_type] += 1
            match_types.append(result.match_type)
            matched_lics.append(result.matched_number)

            if not result.category_match:
                category_mismatches += 1

            canonical_names.append(result.canonical_name)

        # Apply results.
        if not dry_run:
            # Update normalized license numbers (uppercase, cleaned).
            results_df[lic_field] = results_df[norm_col].where(
                results_df[norm_col].str.len() > 0,
                '',
            )

            # Apply canonical names where they differ.
            original_names = results_df[name_field].fillna('').astype(str)
            new_names = pd.Series(canonical_names, index=results_df.index)
            changed_mask = (original_names != new_names) & (new_names.str.len() > 0)
            names_changed = changed_mask.sum()
            results_df[name_field] = new_names

            # Add match quality column.
            results_df[match_type_col] = match_types

        # Clean up temp columns.
        results_df.drop(columns=[norm_col], inplace=True, errors='ignore')

        # Record stats.
        field_stats = {
            'total_populated': int(n_populated),
            'exact': int(match_counts.get('exact', 0)),
            'relaxed': int(match_counts.get('relaxed', 0)),
            'name_fallback': int(match_counts.get('name_fallback', 0)),
            'unmatched': int(match_counts.get('unmatched', 0)),
            'category_mismatch': int(category_mismatches),
        }
        stats.add_field_stats(lic_field, field_stats)
        stats.names_canonicalized[name_field] = int(names_changed)

        if verbose:
            total = n_populated
            matched = (
                match_counts.get('exact', 0)
                + match_counts.get('relaxed', 0)
                + match_counts.get('name_fallback', 0)
            )
            pct = 100 * matched / total if total else 0
            print(f'    Populated: {total:,}')
            print(f'    Matched:   {matched:,} ({pct:.1f}%)')
            print(f'    Names canonicalized: {names_changed:,}')
            if category_mismatches:
                print(f'    ⚠️  Category mismatches: {category_mismatches:,}')

    # ── Step 3: Detect role misassignment ─────────────────────────
    if verbose:
        print('\n  Detecting role misassignments...')
    if not dry_run:
        results_df = detect_role_misassignment(
            results_df, lic_index, verbose=verbose,
        )

    # ── Step 4: Finalize ──────────────────────────────────────────
    stats.finalize(results_df)
    if verbose:
        stats.print_report()

    return results_df, stats


# =============================================================================
# I/O Utilities
# =============================================================================

def load_results(
    build_dir: str,
    filename: str = 'cannabis-results.csv',
) -> pd.DataFrame:
    """Load the post-QC results build file.

    Args:
        build_dir: Path to the build directory.
        filename: Results CSV filename.

    Returns:
        Results DataFrame.
    """
    path = os.path.join(build_dir, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f'Results file not found: {path}\n'
            f'Run agg_results.py and qc_results.py first.'
        )
    print(f'  Loading results: {path}')
    df = pd.read_csv(path, low_memory=False, dtype=str)
    # Restore numeric columns.
    numeric_cols = [
        'total_thc', 'total_cbd', 'total_cannabinoids',
        'total_terpenes', 'moisture_content', 'water_activity',
        'batch_size', 'sample_weight',
        'producer_latitude', 'producer_longitude',
        'lab_latitude', 'lab_longitude',
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    print(f'  Loaded {len(df):,} records')
    return df


def load_licenses(licenses_path: str) -> pd.DataFrame:
    """Load the cannabis licenses dataset.

    Args:
        licenses_path: Path to the licenses CSV.

    Returns:
        Licenses DataFrame.

    Raises:
        FileNotFoundError: If the file doesn't exist.
    """
    if not os.path.exists(licenses_path):
        raise FileNotFoundError(
            f'Licenses file not found: {licenses_path}\n'
            f'Check the path or set CANNLYTICS_LICENSES_PATH.'
        )
    print(f'  Loading licenses: {licenses_path}')
    df = pd.read_csv(
        licenses_path,
        low_memory=False,
        dtype={'premise_zip_code': str, 'license_number': str},
    )
    print(f'  Loaded {len(df):,} license records across '
          f'{df["premise_state"].nunique()} jurisdictions')
    return df


def save_results(
    df: pd.DataFrame,
    build_dir: str,
    filename: str = 'cannabis-results.csv',
    verbose: bool = True,
) -> str:
    """Save the enriched results back to the build directory.

    Args:
        df: Enriched results DataFrame.
        build_dir: Build directory path.
        filename: Output filename.
        verbose: Print save path.

    Returns:
        Path to the saved file.
    """
    os.makedirs(build_dir, exist_ok=True)
    path = os.path.join(build_dir, filename)

    # Drop internal columns before saving.
    drop_cols = [c for c in df.columns if c.startswith('_')]
    if drop_cols:
        df = df.drop(columns=drop_cols)

    df.to_csv(path, index=False)
    if verbose:
        print(f'  Saved {len(df):,} records → {path}')
    return path


def save_report(
    stats: MergeStats,
    path: str,
    verbose: bool = True,
) -> None:
    """Save merge statistics as JSON.

    Args:
        stats: MergeStats instance.
        path: Output JSON path.
        verbose: Print save path.
    """
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(stats.to_dict(), f, indent=2, ensure_ascii=False)
    if verbose:
        print(f'  Report saved → {path}')


# =============================================================================
# CLI
# =============================================================================

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Merge cannabis license data with parsed lab results.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Pipeline Position:
    Stage 4.5 — between qc_results.py (Stage 4) and
    create_results_dataset.py (Stage 5).

Examples:
    python merge_licenses.py
    python merge_licenses.py --licenses "path/to/licenses.csv"
    python merge_licenses.py --dry-run
    python merge_licenses.py --report merge-report.json
        """,
    )
    parser.add_argument(
        '--build-dir',
        default=DEFAULT_BUILD_DIR,
        help=f'Build directory (default: {DEFAULT_BUILD_DIR})',
    )
    parser.add_argument(
        '--licenses',
        default=os.environ.get('CANNLYTICS_LICENSES_PATH', DEFAULT_LICENSES_PATH),
        help='Path to the cannabis licenses CSV',
    )
    parser.add_argument(
        '--report',
        default=None,
        help='Path to save merge statistics JSON (default: build_dir/merge-report.json)',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Compute matches without modifying the results file',
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Suppress progress output',
    )
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()
    verbose = not args.quiet

    if verbose:
        print('\n' + '=' * 70)
        print('  CANNLYTICS — MERGE LICENSES')
        print(f'  Version {VERSION}')
        print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        print('=' * 70)

    # Load data.
    results_df = load_results(args.build_dir)
    licenses_df = load_licenses(args.licenses)

    # Run merge.
    enriched_df, stats = merge_licenses(
        results_df=results_df,
        licenses_df=licenses_df,
        verbose=verbose,
        dry_run=args.dry_run,
    )

    # Save results.
    if not args.dry_run:
        save_results(enriched_df, args.build_dir, verbose=verbose)
    else:
        if verbose:
            print('\n  [DRY RUN] No files modified.')

    # Save report.
    report_path = args.report or os.path.join(
        args.build_dir, 'merge-licenses-report.json',
    )
    save_report(stats, report_path, verbose=verbose)

    if verbose:
        print('\n  ✓ Merge complete.\n')


if __name__ == '__main__':
    main()