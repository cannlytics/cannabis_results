"""
Parse COAs | COA Doc — Hybrid Certificate of Analysis Parsing Engine
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 10/7/2024
Updated: 3/7/2026
License: MIT License <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:
    Production-grade hybrid COA parsing engine. Routes each COA to
    the optimal parsing method:

    1. **Algorithmic parsing** (free, fast, deterministic): For COAs
       from recognized labs with battle-tested parsing algorithms.
    2. **AI-powered parsing** (flexible, comprehensive): For
       unrecognized COAs or when algorithmic parsing fails.

    The hybrid architecture is the core of COA Doc — Cannlytics'
    premier COA parsing technology.

    Hybrid Routing Flow:
        COA PDF → SHA-256 hash → cache check → lab identification
        → if recognized: algorithmic parser → validate → cache
        → if unrecognized or failed: AI parser → cache

    AI Provider Priority (for AI fallback):
        1. Anthropic Claude (highest quality, production-grade)
        2. OpenAI (reliable, good structured output)
        3. Google Gemini (free tier available)
        4. xAI Grok (low cost, good for bulk)

    Pipeline Position:
        This is the PARSING script in the results pipeline:
        get_results_{state}.py → parse_coas.py → process_results.py

Usage:
    # Parse COAs for a specific state (auto = algorithm-first, AI-fallback)
    python parse_coas.py --state ny --max-parses 100

    # Force AI-only parsing
    python parse_coas.py --state ca --method ai --provider openai

    # Force algorithm-only parsing (no AI fallback, skips unrecognized)
    python parse_coas.py --state az --method algorithm

    # Parse with a specific provider
    python parse_coas.py --state ca --provider anthropic

    # Parse all states, using free tokens first
    python parse_coas.py --all --budget 5.00

    # View cache statistics
    python parse_coas.py --state ny --cache-stats

    # Dry run (show what would be parsed)
    python parse_coas.py --state ny --dry-run
"""
# Standard imports:
import argparse
import base64
import gc
import importlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union

# External imports:
from dotenv import dotenv_values
import pandas as pd
import pdfplumber
from PIL import Image

# Internal imports:
from cannlytics.data.cache import Bogart
from cannlytics.logs import initialize_logs
from cannlytics.utils.utils import hash_file

# Suppress pdfminer noise.
import logging
import platform
logging.getLogger('pdfminer').setLevel(logging.ERROR)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Windows Long Path Support                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

IS_WINDOWS = platform.system() == 'Windows'
WIN_MAX_PATH = 259  # Effective limit (260 minus null terminator).


def _long_path(path: str) -> str:
    """Prepend the Windows extended-length path prefix if needed.

    Windows has a default MAX_PATH of 260 characters. Cannabis COA
    files from PRRs often have deeply nested directories and verbose
    filenames that exceed this limit. The ``\\\\?\\`` prefix enables
    paths up to 32,767 characters.

    On non-Windows systems this is a no-op.
    """
    if not IS_WINDOWS:
        return path
    path = str(path)
    if path.startswith('\\\\?\\'):
        return path
    # Convert forward slashes and make absolute.
    abs_path = os.path.abspath(path)
    if len(abs_path) > WIN_MAX_PATH:
        return f'\\\\?\\{abs_path}'
    return abs_path


def _safe_file_size(path: str, min_size: int = 21_000) -> bool:
    """Check if a file meets the minimum size, handling long paths."""
    try:
        return os.path.getsize(_long_path(path)) >= min_size
    except (OSError, FileNotFoundError):
        return False


# ╔══════════════════════════════════════════════════════════════════╗
# ║ PDF Validity Detection                                           ║
# ╚══════════════════════════════════════════════════════════════════╝

# Minimum file size (bytes) for a valid COA PDF. Real COA PDFs are
# typically 50KB+ (even minimal single-page COAs). Files smaller
# than this threshold are almost certainly placeholders, error
# pages, or truncated downloads.
MIN_VALID_PDF_SIZE = 1_024  # 1 KB


def is_valid_pdf(path: str) -> Tuple[bool, str]:
    """Fast structural validation of a PDF file.

    Performs four progressively more expensive checks:

      1. **Header check** (~0.01ms): Read the first 5 bytes and
         verify the ``%PDF-`` magic number. Files that are HTML error
         pages, empty files, or non-PDF binaries fail here instantly.
         This catches ~95% of invalid files (e.g., Curaleaf 404 pages
         saved as .pdf, Cloudflare challenge pages, Reddit HTML).
      2. **Size check** (~0.01ms): Reject files below
         ``MIN_VALID_PDF_SIZE`` (1 KB). Real COA PDFs are 50KB+.
      3. **Structure check** (~1-5ms): Open with ``pdfplumber`` and
         verify the PDF has at least one page. Catches files with a
         valid header but a corrupted internal structure (missing
         /Root object, broken xref table, etc.).
      4. **Content access check** (~5-20ms): Attempt to extract text
         from page 1 AND render the page dimensions. Some PDFs have
         enough structure for ``pdfplumber.open()`` to succeed (lazy
         loading) but corrupt internal page objects that fail when
         actually accessed. This catches "valid shell, corrupt
         content" PDFs that would otherwise waste API calls.

    This function is designed to be called once per PDF and the
    result cached permanently via ``InvalidPDFCache``.

    Args:
        path: Filesystem path to the PDF file.

    Returns:
        Tuple of (is_valid, reason). ``reason`` is an empty string
        if valid, or a short diagnostic string if invalid:
        ``'not_pdf_header'``, ``'too_small'``, ``'empty_file'``,
        ``'no_pages'``, ``'corrupt'``, ``'corrupt_pages'``,
        ``'unreadable'``.
    """
    safe_path = _long_path(path)

    # ── Check 1: File existence and size ──────────────────────
    try:
        file_size = os.path.getsize(safe_path)
    except (OSError, FileNotFoundError):
        return False, 'unreadable'

    if file_size == 0:
        return False, 'empty_file'

    if file_size < MIN_VALID_PDF_SIZE:
        return False, 'too_small'

    # ── Check 2: PDF magic number ─────────────────────────────
    try:
        with open(safe_path, 'rb') as f:
            header = f.read(5)
    except (OSError, PermissionError):
        return False, 'unreadable'

    if header != b'%PDF-':
        return False, 'not_pdf_header'

    # ── Check 3: Structural integrity via pdfplumber ──────────
    try:
        with pdfplumber.open(safe_path) as pdf:
            if not pdf.pages:
                return False, 'no_pages'

            # ── Check 4: Page content accessibility ───────────
            # Some PDFs pass open() via lazy loading but have
            # corrupt page objects. Try to actually read page 1.
            # This catches the "No /Root object" errors that
            # only surface when accessing page content, as well
            # as corrupt xref tables, missing stream data, etc.
            try:
                page = pdf.pages[0]
                # Access page dimensions (very fast, tests object tree).
                _ = page.width
                _ = page.height
                # Try text extraction (tests content streams).
                # Returns '' for image-only PDFs — that's fine.
                _ = page.extract_text()
            except Exception:
                return False, 'corrupt_pages'

    except Exception:
        return False, 'corrupt'

    return True, ''


class InvalidPDFCache:
    """Persistent set of known-invalid PDF hashes.

    Stores one SHA-256 hash per line in a plain text file. This is
    deliberately simpler than the JSONL caches used for parse results
    because we only need to answer the question "is this hash known
    to be invalid?" as fast as possible.

    The file is human-readable and diffable, which is useful for
    auditing. It's also appendable — new entries are flushed to disk
    immediately so progress is never lost on interruption.

    Thread safety: Not required (single-threaded pipeline).
    """

    def __init__(self, cache_path: str):
        self.path = Path(cache_path)
        self._hashes: set = set()
        self._file_handle = None
        self._load()

    def _load(self):
        """Load existing invalid hashes from disk."""
        if self.path.exists():
            with open(self.path, 'r') as f:
                for line in f:
                    h = line.strip()
                    if h and not h.startswith('#'):
                        self._hashes.add(h)

    def __contains__(self, pdf_hash: str) -> bool:
        return pdf_hash in self._hashes

    def __len__(self) -> int:
        return len(self._hashes)

    def add(self, pdf_hash: str, reason: str = ''):
        """Add an invalid hash and flush to disk immediately."""
        if pdf_hash in self._hashes:
            return
        self._hashes.add(pdf_hash)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, 'a') as f:
            f.write(f'{pdf_hash}\n')

    def remove(self, pdf_hash: str):
        """Remove a hash (e.g., after re-downloading a valid copy)."""
        self._hashes.discard(pdf_hash)
        self._rewrite()

    def _rewrite(self):
        """Rewrite the file from the in-memory set."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, 'w') as f:
            f.write(f'# Invalid PDF hashes — generated by parse_coas.py\n')
            for h in sorted(self._hashes):
                f.write(f'{h}\n')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Configuration                                                    ║
# ╚══════════════════════════════════════════════════════════════════╝

# Import schema and config.
try:
    from config.results_schema import (
        ANALYSIS_CONFIGS,
        LabTestMetadata,
        LabAnalysis,
        LabTestResult,
        normalize_product_type,
    )
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config.results_schema import (
        ANALYSIS_CONFIGS,
        LabTestMetadata,
        LabAnalysis,
        LabTestResult,
        normalize_product_type,
    )


# ── AI Provider Pricing (per 1M tokens) ───────────────────────────

AI_PROVIDERS = {
    'anthropic': {
        'name': 'Anthropic Claude',
        'models': {
            'claude-sonnet-4-5-20250929': {
                'input': 3.00, 'output': 15.00,
                'supports_pdf': True, 'supports_images': True,
                'supports_structured_output': False,
                'max_output_tokens': 64_000,
            },
            'claude-haiku-4-5-20251001': {
                'input': 1.00, 'output': 5.00,
                'supports_pdf': True, 'supports_images': True,
                'supports_structured_output': False,
                'max_output_tokens': 64_000,
            },
        },
        'default_model': 'claude-haiku-4-5-20251001',
        'env_key': 'ANTHROPIC_API_KEY',
        'priority': 1,
        'free_tier': False,
    },
    'openai': {
        'name': 'OpenAI',
        'models': {
            'gpt-5-mini': {
                'input': 0.25, 'output': 2.00,
                'supports_pdf': True, 'supports_images': True,
                'supports_structured_output': True,
                'max_output_tokens': 16_384,
                'image_cost': 0.003825,
            },
            'gpt-5-nano': {
                'input': 0.05, 'output': 0.40,
                'supports_pdf': True, 'supports_images': True,
                'supports_structured_output': True,
                'max_output_tokens': 16_384,
                'image_cost': 0.001275,
            },
            'gpt-5': {
                'input': 1.25, 'output': 10.00,
                'supports_pdf': True, 'supports_images': True,
                'supports_structured_output': True,
                'max_output_tokens': 32_768,
                'image_cost': 0.003825,
            },
        },
        'default_model': 'gpt-5-nano',
        'env_key': 'OPENAI_API_KEY',
        'priority': 2,
        'free_tier': False,
    },
    'gemini': {
        'name': 'Google Gemini',
        'models': {
            'gemini-2.5-flash': {
                'input': 0.30, 'output': 2.50,
                'supports_pdf': True, 'supports_images': True,
                'supports_structured_output': True,
                'max_output_tokens': 65_536,
                'free_tier_input': 0.0, 'free_tier_output': 0.0,
            },
            'gemini-2.5-pro': {
                'input': 1.25, 'output': 10.00,
                'supports_pdf': True, 'supports_images': True,
                'supports_structured_output': True,
                'max_output_tokens': 65_536,
                'free_tier_input': 0.0, 'free_tier_output': 0.0,
            },
        },
        'default_model': 'gemini-2.5-flash',
        'env_key': 'GOOGLE_API_KEY',
        'priority': 3,
        'free_tier': True,
    },
    'xai': {
        'name': 'xAI Grok',
        'models': {
            'grok-4-1-fast-non-reasoning': {
                'input': 0.20, 'output': 0.50,
                'supports_pdf': False, 'supports_images': True,
                'supports_structured_output': True,
                'max_output_tokens': 16_384,
            },
            'grok-3-mini': {
                'input': 0.30, 'output': 0.50,
                'supports_pdf': False, 'supports_images': True,
                'supports_structured_output': True,
                'max_output_tokens': 16_384,
            },
        },
        'default_model': 'grok-4-1-fast-non-reasoning',
        'env_key': 'XAI_API_KEY',
        'priority': 4,
        'free_tier': False,
    },
}

# Default paths: prefer centralized config, fall back to env / hardcoded.
try:
    from config.results_config import PATHS as _PATHS
    DEFAULT_DATA_DIR = _PATHS.data_dir
    DEFAULT_CACHE_DIR = _PATHS.cache_dir
    DEFAULT_LOG_DIR = _PATHS.log_dir
except ImportError:
    DEFAULT_DATA_DIR = Path(os.environ.get('CANNLYTICS_DATA_DIR', 'D:/data'))
    DEFAULT_CACHE_DIR = Path(os.environ.get('CANNLYTICS_CACHE_DIR', 'D:/data/.cache'))
    DEFAULT_LOG_DIR = Path(os.environ.get('CANNLYTICS_LOG_DIR', 'D:/data/.logs'))

# State name mapping.
STATE_NAMES = {
    'ak': 'alaska', 'az': 'arizona', 'ca': 'california', 'co': 'colorado',
    'ct': 'connecticut', 'fl': 'florida', 'hi': 'hawaii', 'ma': 'massachusetts',
    'md': 'maryland', 'mi': 'michigan', 'mo': 'missouri', 'ms': 'mississippi',
    'nj': 'new-jersey', 'nv': 'nevada', 'ny': 'new-york', 'oh': 'ohio',
    'or': 'oregon', 'ri': 'rhode-island', 'ut': 'utah', 'vt': 'vermont',
    'wa': 'washington',
}

# ── Bayesian Analysis Skip Rules ─────────────────────────────────
# Product-type-specific priors for analyses that are known to be
# unnecessary based on domain knowledge and observed zero-result
# patterns. When metadata reveals the product type, we update our
# beliefs about which analyses to parse — skipping those with a
# near-zero prior probability of yielding results.
#
# Structure: {analysis_name: [product_types_to_skip]}
# Rationale is documented per rule so future additions are traceable.
ANALYSIS_SKIP_RULES = {
    # Edibles are almost never tested for terpenes. Terpene
    # profiles are irrelevant after decarboxylation / infusion.
    # Observed: 100% zero-result rate for edibles (11/11 in CA).
    'terpenes': ['edible'],
}

# ── Flex Processing Configuration ────────────────────────────────
# OpenAI Flex processing provides 50% cost reduction (Batch API
# rates) for synchronous requests with higher latency tolerance.
# Supported for GPT-5 family models.
FLEX_COST_MULTIPLIER = 0.5  # 50% discount on standard rates
FLEX_TIMEOUT = 900.0  # 15 minutes (recommended by OpenAI docs)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ COA Doc — Lab Registry & Algorithmic Routing                     ║
# ╚══════════════════════════════════════════════════════════════════╝

# The Lab Registry maps lab identifiers to their fingerprints,
# algorithmic parser entry points, and operational metadata.
# This is the routing table for COA Doc's hybrid architecture.
#
# Identification priority:
#   1. URL presence in page 1 text (highest confidence)
#   2. Lab/LIMS name presence in page 1 text
#   3. QR code URL domain match (optional, requires qrustie)
#
# Each entry:
#   - name:         Human-readable lab name
#   - urls:         URL fragments to search for (case-sensitive)
#   - text_patterns: Text strings to search for (case-insensitive)
#   - module:       Python module name (for import)
#   - algorithm:    Entry point function name
#   - version:      Algorithm version string
#   - states:       States where this lab operates
#   - tier:         Validation tier (1=production, 2=beta, 3=alpha, 4=dev)
#   - lims:         True if this is a LIMS (multi-lab), False if single lab
#
# To add a new lab:
#   1. Add an entry to LAB_REGISTRY
#   2. Place the algorithm module in algorithms/coa_parsers/{module}.py
#      OR ensure cannlytics.data.coas.algorithms.{module} is importable
#   3. Run the benchmarking suite to validate (Phase 4)

LAB_REGISTRY: Dict[str, Dict[str, Any]] = {
    'confidentcannabis': {
        'name': 'Confident Cannabis',
        'urls': ['confidentcannabis.com', 'confidentlims.com'],
        'text_patterns': ['Confident Cannabis', 'Confident LIMS'],
        'module': 'confidentcannabis',
        'algorithm': 'parse_cc_coa',
        'version': '1.0.0',
        'states': ['az', 'ca', 'co', 'mo', 'ny', 'or', 'wa'],
        'tier': 3,
        'lims': True,
    },
    'tagleaf': {
        'name': 'TagLeaf LIMS',
        'urls': ['lims.tagleaf.com', 'tagleaf.com'],
        'text_patterns': ['TagLeaf', 'lims.tagleaf'],
        'module': 'tagleaf',
        'algorithm': 'parse_tagleaf_coa',
        'version': '1.0.0',
        'states': ['ca', 'mo', 'ny', 'or',],
        'tier': 3,
        'lims': True,
    },
    'sclabs': {
        'name': 'SC Labs',
        'urls': ['client.sclabs.com', 'sclabs.com'],
        'text_patterns': ['SC Labs', 'SC Laboratories'],
        'module': 'sclabs',
        'algorithm': 'parse_sc_labs_coa',
        'version': '1.0.0',
        'states': ['az', 'ca', 'or', 'co', 'mi'],
        'tier': 3,
        'lims': False,
    },
    'encore': {
        'name': 'Encore Labs',
        'urls': ['encorelabs.com', 'encore-labs.com'],
        'text_patterns': ['Encore Labs'],
        'module': 'encore',
        'algorithm': 'parse_encore_coa',
        'version': '2.0.0',
        'states': ['ca', 'az'],
        'tier': 2,
        'lims': False,
    },
    'kaycha': {
        'name': 'Kaycha Labs',
        'urls': ['kaychalabs.com', 'yourcoa.com'],
        'text_patterns': ['Kaycha Labs', 'Kaycha Laboratory'],
        'module': 'kaycha',
        'algorithm': 'parse_kaycha_coa',
        'version': '1.0.0',
        'states': ['az', 'fl', 'ny', 'oh', 'nj'],
        'tier': 3,
        'lims': False,
    },
    'smithers': {
        'name': 'Smithers CTS',
        'urls': ['smithers.com'],
        'text_patterns': ['Smithers CTS', 'Smithers CTS Arizona',
                        'Smithers CTS New York'],
        'module': 'smithers',
        'algorithm': 'parse_smithers_coa',
        'version': '1.0.0',
        'states': ['az', 'ny'],
        'tier': 3,
        'lims': False,
    },
    'phytofarma': {
        'name': 'Phyto-Farma Labs',
        'urls': ['phytofarmalabs.com'],
        'text_patterns': ['Phyto-Farma Labs', 'Phyto-farma Labs'],
        'module': 'phytofarma',
        'algorithm': 'parse_phyto_farma_coa',
        'version': '1.0.0',
        'states': ['ny'],
        'tier': 3,
        'lims': False,
    },
    'green_analytics': {
        'name': 'Green Analytics',
        'urls': ['greenanalyticsllc.com'],
        'text_patterns': ['Green Analytics East', 'Green Analytics MD', 'Green Analytics NY'],
        'module': 'green_analytics',
        'algorithm': 'parse_green_analytics_coa',
        'version': '1.0.0',
        'states': ['nj', 'md', 'ny'],
        'tier': 3,
        'lims': False,
    },
    'acs': {
        'name': 'ACS Laboratory',
        'urls': ['acslabcannabis.com', 'acslab.com'],
        'text_patterns': ['ACS Laboratory', 'ACS Labs', '721 Cortaro'],
        'module': 'acs',
        'algorithm': 'parse_acs_coa',
        'version': '2.0.0',
        'states': ['fl'],
        'tier': 3,
        'lims': False,
    },
    'terplife': {
        'name': 'TerpLife Labs',
        'urls': ['terplifelabs.com', 'www.terplifelabs.com'],
        'text_patterns': ['TerpLife Labs', 'TerpLife', 'TL LABORATORIES', 'TL Laboratories'],
        'module': 'terplife',
        'algorithm': 'parse_terplife_coa',
        'version': '1.0.0',
        'states': ['fl'],
        'tier': 3,
        'lims': False,
    },

    # ── Phase 2+ labs (registered but not yet revived) ────────────
    # Uncomment and set tier to 3+ as algorithms are revived.
    #
    # 'anresco': {
    #     'name': 'Anresco Laboratories',
    #     'urls': ['anresco.com'],
    #     'text_patterns': ['Anresco'],
    #     'module': 'anresco',
    #     'algorithm': 'parse_anresco_coa',
    #     'version': '1.0.0',
    #     'states': ['ca'],
    #     'tier': 4,
    #     'lims': False,
    # },
    # 'mcrlabs': {
    #     'name': 'MCR Labs',
    #     'urls': ['mcrlabs.com', 'reports.mcrlabs.com'],
    #     'text_patterns': ['MCR Labs'],
    #     'module': 'mcrlabs',
    #     'algorithm': 'parse_mcr_labs_coa',
    #     'version': '1.0.0',
    #     'states': ['ma'],
    #     'tier': 4,
    #     'lims': False,
    # },
    # 'greenleaflab': {
    #     'name': 'Green Leaf Lab',
    #     'urls': ['greenleaflab.org'],
    #     'text_patterns': ['Green Leaf Lab'],
    #     'module': 'greenleaflab',
    #     'algorithm': 'parse_green_leaf_lab_coa',
    #     'version': '1.0.0',
    #     'states': ['or', 'ca'],
    #     'tier': 4,
    #     'lims': False,
    # },
    # 'sonoma': {
    #     'name': 'Sonoma Lab Works',
    #     'urls': ['sonomalabworks.com'],
    #     'text_patterns': ['Sonoma Lab Works'],
    #     'module': 'sonoma',
    #     'algorithm': 'parse_sonoma_coa',
    #     'version': '1.0.0',
    #     'states': ['ca'],
    #     'tier': 4,
    #     'lims': False,
    # },
}


def identify_lab(
        pdf_path: str,
        lab_registry: Optional[Dict[str, Dict]] = None,
        deep_search: bool = True,
        qr_fallback: bool = False,
        qrustie_path: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ) -> Optional[str]:
    """Identify the originating lab/LIMS from a COA PDF.

    This is the routing decision function for COA Doc's hybrid
    architecture. It determines whether a COA comes from a
    recognized lab with an algorithmic parser available.

    Strategy (in order of confidence):
      1. Extract text from page 1 via pdfplumber.
      2. Search for known lab URLs in text (highest confidence).
      3. Search for known lab/LIMS names in text.
      4. Optionally search page 2 text (deep_search).
      5. Optionally decode QR codes via qrustie (qr_fallback).

    Args:
        pdf_path:       Path to the COA PDF file.
        lab_registry:   Lab registry dict. Defaults to LAB_REGISTRY.
        deep_search:    Whether to also search page 2 text.
        qr_fallback:    Whether to attempt QR code identification.
        qrustie_path:   Path to qrustie binary (for QR fallback).
        logger:         Optional logger for debug messages.

    Returns:
        Lab registry key (e.g., 'kaycha') if identified, None otherwise.
    """
    if lab_registry is None:
        lab_registry = LAB_REGISTRY
    _log = logger or logging.getLogger(__name__)

    # ── Step 1: Extract text from page(s) ─────────────────────
    texts = []
    try:
        with pdfplumber.open(_long_path(pdf_path)) as pdf:
            if not pdf.pages:
                return None
            page1_text = pdf.pages[0].extract_text() or ''
            texts.append(page1_text)
            if deep_search and len(pdf.pages) > 1:
                page2_text = pdf.pages[1].extract_text() or ''
                texts.append(page2_text)
    except Exception as e:
        _log.debug(f'identify_lab: Failed to extract text: {e}')
        return None

    combined_text = '\n'.join(texts)
    if not combined_text.strip():
        _log.debug('identify_lab: No extractable text (image-only PDF)')
        # Image-only PDFs cannot be identified by text.
        # Fall through to QR fallback if enabled.
        if not qr_fallback:
            return None

    # ── Step 2: Search for known lab URLs (highest confidence) ─
    for lab_key, config in lab_registry.items():
        for url in config.get('urls', []):
            if url in combined_text:
                _log.debug(f'identify_lab: URL match "{url}" -> {lab_key}')
                return lab_key

    # ── Step 3: Search for known lab/LIMS names ───────────────
    text_lower = combined_text.lower()
    for lab_key, config in lab_registry.items():
        for pattern in config.get('text_patterns', []):
            if pattern.lower() in text_lower:
                _log.debug(f'identify_lab: Text match "{pattern}" -> {lab_key}')
                return lab_key

    # ── Step 4: QR code fallback via qrustie ──────────────────
    if qr_fallback and qrustie_path:
        qr_url = _decode_qr_for_lab_id(
            pdf_path, qrustie_path, lab_registry, _log,
        )
        if qr_url:
            return qr_url

    return None


def _decode_qr_for_lab_id(
        pdf_path: str,
        qrustie_path: str,
        lab_registry: Dict,
        logger: logging.Logger,
    ) -> Optional[str]:
    """Attempt to identify lab from QR code URL via qrustie.

    Renders page 1 as an image, decodes any QR codes, and
    checks decoded URLs against known lab URL patterns.

    Args:
        pdf_path:       Path to the COA PDF.
        qrustie_path:   Path to the qrustie binary.
        lab_registry:   Lab registry dict.
        logger:         Logger instance.

    Returns:
        Lab registry key if identified, None otherwise.
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            images = get_pdf_pages_as_images(
                pdf_path, page_indexes=[0], output_dir=tmpdir,
            )
            if not images:
                return None
            result = subprocess.run(
                [qrustie_path, '--input', images[0], '--first-only'],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return None
            qr_data = json.loads(result.stdout.strip())
            if not qr_data.get('found') or not qr_data.get('data'):
                return None
            qr_url = qr_data['data'][0]
            # Check decoded URL against known lab URLs.
            for lab_key, config in lab_registry.items():
                for url_fragment in config.get('urls', []):
                    if url_fragment in qr_url:
                        logger.debug(
                            f'identify_lab: QR URL match '
                            f'"{url_fragment}" -> {lab_key}'
                        )
                        return lab_key
    except Exception as e:
        logger.debug(f'identify_lab: QR fallback failed: {e}')
    return None


def load_algorithm(
        lab_key: str,
        lab_registry: Optional[Dict] = None,
        local_paths: Optional[List[str]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> Optional[Callable]:
    """Load a COA parsing algorithm with local override.

    Import priority:
      1. Local override directories (for development)
      2. cannlytics package (canonical source)

    Args:
        lab_key:        Registry key (e.g., 'kaycha').
        lab_registry:   Lab registry dict. Defaults to LAB_REGISTRY.
        local_paths:    List of local directories to search.
        logger:         Optional logger.

    Returns:
        The parsing function, or None if not loadable.
    """
    if lab_registry is None:
        lab_registry = LAB_REGISTRY
    _log = logger or logging.getLogger(__name__)

    config = lab_registry.get(lab_key)
    if not config:
        _log.debug(f'load_algorithm: Unknown lab key "{lab_key}"')
        return None

    module_name = config['module']
    func_name = config['algorithm']

    # Default local search paths.
    if local_paths is None:
        local_paths = [
            'algorithms/coa_parsers',
            '../algorithms/coa_parsers',
        ]

    # ── Try local override first ──────────────────────────────
    for local_dir in local_paths:
        local_path = Path(local_dir)
        module_file = local_path / f'{module_name}.py'
        if module_file.exists():
            try:
                spec = importlib.util.spec_from_file_location(
                    f'coa_parsers.{module_name}', str(module_file),
                )
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                func = getattr(mod, func_name, None)
                if func and callable(func):
                    _log.debug(
                        f'load_algorithm: Loaded {func_name} '
                        f'from local {module_file}'
                    )
                    return func
            except Exception as e:
                _log.warning(
                    f'load_algorithm: Failed to load local '
                    f'{module_file}: {e}'
                )

    # ── Fall back to cannlytics package ───────────────────────
    try:
        mod = importlib.import_module(
            f'cannlytics.data.coas.algorithms.{module_name}'
        )
        func = getattr(mod, func_name, None)
        if func and callable(func):
            _log.debug(
                f'load_algorithm: Loaded {func_name} '
                f'from cannlytics package'
            )
            return func
    except (ImportError, AttributeError) as e:
        _log.debug(
            f'load_algorithm: cannlytics package import failed: {e}'
        )

    _log.warning(f'load_algorithm: Could not load algorithm for "{lab_key}"')
    return None


def adapt_algorithm_output(
        raw_output: Dict,
        pdf_hash: str,
        lab_key: str,
        lab_registry: Optional[Dict] = None,
        elapsed: float = 0.0,
    ) -> Optional[Dict]:
    """Adapt legacy algorithmic parser output to the hybrid cache schema.

    Legacy CoADoc algorithms return a flat dictionary with mixed
    metadata and a nested 'results' list. This function transforms
    that into the standardized cache format used by the hybrid engine.

    Args:
        raw_output:     Raw dict from the algorithmic parser.
        pdf_hash:       SHA-256 hash of the source PDF.
        lab_key:        Lab registry key (e.g., 'kaycha').
        lab_registry:   Lab registry dict.
        elapsed:        Parse time in seconds.

    Returns:
        Dict with 'metadata' and per-analysis result dicts
        matching the hybrid cache schema, or None on failure.
    """
    if lab_registry is None:
        lab_registry = LAB_REGISTRY
    if not raw_output or not isinstance(raw_output, dict):
        return None

    config = lab_registry.get(lab_key, {})
    version = config.get('version', '0.0.0')

    # ── Common attribution fields ─────────────────────────────
    attribution = {
        'pdf_hash': pdf_hash,
        'parsing_method': 'algorithm',
        'parsing_algorithm': f'{lab_key}_v{version}',
        'parsing_model': None,
        'parsing_provider': 'local',
        'parsing_time': round(elapsed, 2),
        'parsing_cost': 0.0,
    }

    # ── Extract metadata fields ───────────────────────────────
    # Metadata is everything EXCEPT 'results' and 'analyses'.
    metadata_keys = {
        'product_name', 'strain_name', 'product_type',
        'date_tested', 'date_received', 'date_collected',
        'batch_number', 'batch_size',
        'lab', 'lab_license_number', 'lab_address', 'lab_city',
        'lab_state', 'lab_zipcode',
        'producer', 'producer_street', 'producer_city',
        'producer_state', 'producer_zipcode',
        'producer_license_number',
        'distributor', 'distributor_license_number',
        'sample_id', 'lab_id', 'sample_weight',
        'total_cannabinoids', 'total_cbd', 'total_thc',
        'total_terpenes', 'status',
    }
    metadata = {**attribution}
    for key in metadata_keys:
        if key in raw_output:
            metadata[key] = raw_output[key]

    # ── Extract analyses list ──────────────────────────────────
    analyses_raw = raw_output.get('analyses', '[]')
    if isinstance(analyses_raw, str):
        try:
            analyses_list = json.loads(analyses_raw)
        except (json.JSONDecodeError, TypeError):
            analyses_list = []
    elif isinstance(analyses_raw, list):
        analyses_list = analyses_raw
    else:
        analyses_list = []
    metadata['analyses'] = analyses_list

    # ── Extract and categorize results ────────────────────────
    results_raw = raw_output.get('results', '[]')
    if isinstance(results_raw, str):
        try:
            results_list = json.loads(results_raw)
        except (json.JSONDecodeError, TypeError):
            results_list = []
    elif isinstance(results_raw, list):
        results_list = results_raw
    else:
        results_list = []

    # Group results by analysis type.
    analysis_groups: Dict[str, List[Dict]] = {}
    for result in results_list:
        if not isinstance(result, dict):
            continue
        analysis = result.get('analysis', 'unknown')
        # Normalize analysis name to match ANALYSIS_CONFIGS keys.
        analysis_normalized = analysis.lower().replace(' ', '_')
        # Map common variations.
        if 'cannab' in analysis_normalized:
            analysis_normalized = 'cannabinoids'
        elif 'terp' in analysis_normalized:
            analysis_normalized = 'terpenes'
        elif 'pestic' in analysis_normalized:
            analysis_normalized = 'pesticides'
        elif 'heavy' in analysis_normalized or 'metal' in analysis_normalized:
            analysis_normalized = 'heavy_metals'
        elif 'micro' in analysis_normalized:
            analysis_normalized = 'microbials'
        elif 'solvent' in analysis_normalized:
            analysis_normalized = 'residual_solvents'
        elif 'moisture' in analysis_normalized or 'water' in analysis_normalized:
            analysis_normalized = 'moisture_foreign_matter'
        if analysis_normalized not in analysis_groups:
            analysis_groups[analysis_normalized] = []
        analysis_groups[analysis_normalized].append(result)

    # Build per-analysis cache entries.
    analysis_entries = {}
    for analysis_name, results in analysis_groups.items():
        analysis_entries[analysis_name] = {
            **attribution,
            'results': results,
        }

    return {
        'metadata': metadata,
        'analyses': analysis_entries,
    }


# ╔══════════════════════════════════════════════════════════════════╗
# ║ AI Provider Clients                                              ║
# ╚══════════════════════════════════════════════════════════════════╝

class CostTracker:
    """Track cumulative costs across all providers and models."""

    def __init__(self):
        self.records: List[Dict] = []
        self.total_cost: float = 0.0

    def record(
            self,
            provider: str,
            model: str,
            input_tokens: int,
            output_tokens: int,
            cost: float,
            analysis: str = '',
            pdf_hash: str = '',
        ):
        """Record a single API call's cost."""
        entry = {
            'timestamp': datetime.now().isoformat(),
            'provider': provider,
            'model': model,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'cost': cost,
            'analysis': analysis,
            'pdf_hash': pdf_hash,
        }
        self.records.append(entry)
        self.total_cost += cost

    def summary(self) -> Dict:
        """Get a summary of all costs."""
        by_provider = {}
        by_model = {}
        for r in self.records:
            prov = r['provider']
            mod = r['model']
            by_provider[prov] = by_provider.get(prov, 0.0) + r['cost']
            by_model[mod] = by_model.get(mod, 0.0) + r['cost']
        return {
            'total_cost': round(self.total_cost, 6),
            'total_calls': len(self.records),
            'by_provider': {k: round(v, 6) for k, v in by_provider.items()},
            'by_model': {k: round(v, 6) for k, v in by_model.items()},
        }

    def __str__(self) -> str:
        s = self.summary()
        return (
            f"Total: ${s['total_cost']:.4f} "
            f"({s['total_calls']} calls) | "
            f"By provider: {s['by_provider']}"
        )


def _is_flex_unavailable(error: Exception) -> bool:
    """Check if an OpenAI error is a flex-specific Resource Unavailable.

    OpenAI returns 429 "Resource Unavailable" when flex processing
    lacks capacity. This is distinct from rate-limit 429 errors and
    should trigger a retry with standard processing, not provider
    exhaustion.
    """
    error_str = str(error).lower()
    status_code = getattr(error, 'status_code', None)
    if status_code == 429 and 'resource' in error_str:
        return True
    # Also check the error code attribute (openai SDK v1+).
    error_code = getattr(error, 'code', '')
    if error_code and 'resource' in str(error_code).lower():
        return True
    return False


class AIClient:
    """Unified AI client supporting multiple providers."""

    def __init__(
            self,
            provider: str = 'openai',
            model: Optional[str] = None,
            config: Optional[Dict] = None,
            logger: Optional[logging.Logger] = None,
        ):
        self.provider = provider
        self.provider_config = AI_PROVIDERS[provider]
        self.model = model or self.provider_config['default_model']
        self.model_config = self.provider_config['models'][self.model]
        self.config = config or dotenv_values('../.env')
        self.logger = logger or logging.getLogger(__name__)
        self.client = None
        self._exhausted = False
        self.use_flex = provider == 'openai'  # Enable flex for OpenAI
        self._flex_failures = 0  # Track consecutive flex failures
        self._init_client()

    def _init_client(self):
        """Initialize the provider-specific client."""
        api_key = self.config.get(
            self.provider_config['env_key'],
            os.environ.get(self.provider_config['env_key'], ''),
        )
        if not api_key:
            self.logger.warning(
                f'{self.provider}: No API key found for '
                f'{self.provider_config["env_key"]}'
            )
            self._exhausted = True
            return

        if self.provider == 'openai':
            from openai import OpenAI
            self.client = OpenAI(
                api_key=api_key,
                timeout=FLEX_TIMEOUT,  # 15min for flex processing
            )

        elif self.provider == 'anthropic':
            from anthropic import Anthropic
            self.client = Anthropic(api_key=api_key)

        elif self.provider == 'gemini':
            from google import genai
            self.client = genai.Client(api_key=api_key)

        elif self.provider == 'xai':
            from openai import OpenAI
            self.client = OpenAI(
                api_key=api_key,
                base_url='https://api.x.ai/v1',
            )

    @property
    def is_available(self) -> bool:
        return self.client is not None and not self._exhausted

    @property
    def supports_pdf(self) -> bool:
        return self.model_config.get('supports_pdf', False)

    @property
    def supports_structured_output(self) -> bool:
        return self.model_config.get('supports_structured_output', False)

    def calculate_cost(
            self,
            input_tokens: int,
            output_tokens: int,
            num_images: int = 0,
            used_flex: bool = False,
        ) -> float:
        """Calculate the cost of a single API call.
        
        When flex processing is used, the cost is reduced by the
        FLEX_COST_MULTIPLIER (50% discount on standard rates).
        """
        in_rate = self.model_config['input'] / 1_000_000
        out_rate = self.model_config['output'] / 1_000_000
        token_cost = input_tokens * in_rate + output_tokens * out_rate
        image_cost = num_images * self.model_config.get('image_cost', 0.0)
        cost = token_cost + image_cost
        if used_flex:
            cost *= FLEX_COST_MULTIPLIER
        return cost

    def parse_metadata(
            self,
            pdf_path: str,
            page_images: Optional[List[str]] = None,
            page_text: Optional[str] = None,
        ) -> Tuple[Optional[Dict], float, int, int]:
        """Parse metadata from a COA.

        Returns: (parsed_data, cost, input_tokens, output_tokens)
        """
        system_prompt = METADATA_SYSTEM_PROMPT
        user_prompt = METADATA_USER_PROMPT

        return self._call_ai(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            pdf_path=pdf_path,
            page_images=page_images,
            page_text=page_text,
            response_schema=LabTestMetadata,
            mode='metadata',
        )

    def parse_analysis(
            self,
            analysis_name: str,
            analyte_keys: List[str],
            pdf_path: str,
            page_images: Optional[List[str]] = None,
            page_text: Optional[str] = None,
        ) -> Tuple[Optional[Dict], float, int, int]:
        """Parse a specific analysis from a COA.

        Returns: (parsed_data, cost, input_tokens, output_tokens)
        """
        system_prompt = ANALYSIS_SYSTEM_PROMPT
        user_prompt = ANALYSIS_USER_PROMPT % (
            analysis_name,
            '\n'.join(analyte_keys),
        )

        return self._call_ai(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            pdf_path=pdf_path,
            page_images=page_images,
            page_text=page_text,
            response_schema=LabAnalysis,
            mode='analysis',
        )

    def parse_single_page(
            self,
            page_images: Optional[List[str]] = None,
            page_text: Optional[str] = None,
        ) -> Tuple[Optional[Dict], float, int, int]:
        """Parse a single-page COA in one shot (metadata + all results).

        Returns: (parsed_data, cost, input_tokens, output_tokens)
        where parsed_data has 'metadata', 'cannabinoids', 'terpenes', etc.
        """
        # No Pydantic schema — the response has variable top-level keys.
        return self._call_ai(
            system_prompt=SINGLE_PAGE_SYSTEM_PROMPT,
            user_prompt=SINGLE_PAGE_USER_PROMPT,
            pdf_path=None,
            page_images=page_images,
            page_text=page_text,
            response_schema=None,
            mode='single_page',
        )

    def _call_ai(
            self,
            system_prompt: str,
            user_prompt: str,
            pdf_path: Optional[str] = None,
            page_images: Optional[List[str]] = None,
            page_text: Optional[str] = None,
            response_schema: Any = None,
            mode: str = 'metadata',
        ) -> Tuple[Optional[Dict], float, int, int]:
        """Unified AI call across all providers.

        Strategy hierarchy:
        1. PDF direct (if provider supports it and we have a PDF)
        2. Images (if we have page images)
        3. Text (fallback)

        Returns: (parsed_data, cost, input_tokens, output_tokens)
        """
        if not self.is_available:
            return None, 0.0, 0, 0

        try:
            if self.provider == 'openai' or self.provider == 'xai':
                return self._call_openai(
                    system_prompt, user_prompt, pdf_path,
                    page_images, page_text, response_schema, mode,
                )
            elif self.provider == 'anthropic':
                return self._call_anthropic(
                    system_prompt, user_prompt, pdf_path,
                    page_images, page_text, mode,
                )
            elif self.provider == 'gemini':
                return self._call_gemini(
                    system_prompt, user_prompt, pdf_path,
                    page_images, page_text, response_schema, mode,
                )
        except Exception as e:
            error_str = str(e).lower()
            if '429' in error_str or 'rate' in error_str or 'quota' in error_str:
                self.logger.warning(f'{self.provider}: Rate limited / exhausted.')
                self._exhausted = True
            else:
                self.logger.error(f'{self.provider} error: {e}')
            return None, 0.0, 0, 0

    # ── OpenAI / xAI Implementation ─────────────────────────────

    def _call_openai(
            self,
            system_prompt, user_prompt, pdf_path,
            page_images, page_text, response_schema, mode,
        ) -> Tuple[Optional[Dict], float, int, int]:
        """Call OpenAI or xAI (OpenAI-compatible API).

        Two API paths:
          - PDF available (OpenAI only): Uses the Responses API
            (`client.responses.create`) with `input_file`.
            Reserved for rare cases where full PDF is needed.
          - Images/text (default): Uses the Chat Completions API
            (`client.beta.chat.completions.parse`) with `image_url`.
            This is the standard path since the orchestration layer
            sends targeted page images to minimize token usage.
        """

        # ── Path A: PDF via Responses API (OpenAI only) ─────────
        # Only used when explicitly passed a pdf_path with no images.
        if pdf_path and not page_images and self.provider == 'openai':
            return self._call_openai_responses_api(
                system_prompt, user_prompt, pdf_path,
                response_schema, mode,
            )

        # ── Path B: Images/text via Chat Completions ────────────
        user_content = [{'type': 'text', 'text': user_prompt}]

        # Safety net: If pdf_path but no images (xAI path),
        # convert PDF to images.
        _temp_dir = None
        if pdf_path and not page_images:
            import tempfile as _tf
            _temp_dir = _tf.mkdtemp()
            page_images = get_pdf_pages_as_images(
                pdf_path,
                page_indexes=list(range(5)),
                output_dir=_temp_dir,
            )
            if not page_images:
                page_text = extract_pdf_text(pdf_path)

        if page_images:
            for img_path in page_images:
                b64 = _encode_image(img_path)
                user_content.append({
                    'type': 'image_url',
                    'image_url': {'url': f'data:image/jpeg;base64,{b64}', 'detail': 'high'},
                })
        elif page_text:
            user_content[0]['text'] = f'{user_prompt}\n\nCOA Text:\n{page_text}'

        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_content},
        ]

        kwargs = {
            'model': self.model,
            'messages': messages,
        }

        # ── Flex processing: 50% cost reduction ──────────────
        # Try flex first; on 429 Resource Unavailable, retry standard.
        used_flex = False
        if self.use_flex and self.provider == 'openai':
            kwargs['service_tier'] = 'flex'
            used_flex = True

        if self.supports_structured_output and response_schema:
            try:
                completion = self.client.beta.chat.completions.parse(
                    **kwargs,
                    response_format=response_schema,
                    reasoning_effort='high',
                )
            except Exception as e:
                if used_flex and _is_flex_unavailable(e):
                    self.logger.info('Flex unavailable, retrying standard...')
                    kwargs.pop('service_tier', None)
                    used_flex = False
                    self._flex_failures += 1
                    completion = self.client.beta.chat.completions.parse(
                        **kwargs,
                        response_format=response_schema,
                        reasoning_effort='high',
                    )
                else:
                    raise
            msg = completion.choices[0].message
            if getattr(msg, 'refusal', None):
                self.logger.warning(f'Model refused: {msg.refusal}')
                return None, 0.0, 0, 0
            try:
                parsed = msg.parsed.model_dump()
            except Exception:
                return None, 0.0, 0, 0
        else:
            kwargs['max_completion_tokens'] = self.model_config.get('max_output_tokens', 16_384)
            try:
                completion = self.client.chat.completions.create(**kwargs)
            except Exception as e:
                if used_flex and _is_flex_unavailable(e):
                    self.logger.info('Flex unavailable, retrying standard...')
                    kwargs.pop('service_tier', None)
                    used_flex = False
                    self._flex_failures += 1
                    completion = self.client.chat.completions.create(**kwargs)
                else:
                    raise
            content = completion.choices[0].message.content
            parsed = _extract_json(content)
            if parsed is None:
                return None, 0.0, 0, 0

        usage = getattr(completion, 'usage', None)
        in_tok = getattr(usage, 'prompt_tokens', 0) if usage else 0
        out_tok = getattr(usage, 'completion_tokens', 0) if usage else 0
        num_images = len(page_images) if page_images else 0
        cost = self.calculate_cost(in_tok, out_tok, num_images, used_flex=used_flex)

        if _temp_dir:
            import shutil
            shutil.rmtree(_temp_dir, ignore_errors=True)

        return parsed, cost, in_tok, out_tok

    def _call_openai_responses_api(
            self,
            system_prompt: str,
            user_prompt: str,
            pdf_path: str,
            response_schema: Any,
            mode: str,
        ) -> Tuple[Optional[Dict], float, int, int]:
        """Call OpenAI's Responses API with native PDF input.

        The Responses API (`client.responses.create`) accepts PDFs
        via `input_file`. For vision-capable models, it extracts
        both text and page images and sends both to the model.

        Structured output is achieved via `text.format` with a
        JSON schema derived from the Pydantic model.
        """
        # Read and base64-encode the PDF.
        with open(_long_path(pdf_path), 'rb') as f:
            pdf_b64 = base64.b64encode(f.read()).decode('utf-8')

        # Build input content (user message with PDF + prompt).
        pdf_filename = os.path.basename(pdf_path)
        input_content = [
            {
                'type': 'input_text',
                'text': user_prompt,
            },
            {
                'type': 'input_file',
                'filename': pdf_filename,
                'file_data': f'data:application/pdf;base64,{pdf_b64}',
            },
        ]

        # Build the API call kwargs.
        # The Responses API uses `instructions` for system-level prompting.
        kwargs = {
            'model': self.model,
            'instructions': system_prompt,
            'input': [{'role': 'user', 'content': input_content}],
        }

        # Add structured output via JSON schema if we have a Pydantic model.
        if response_schema and hasattr(response_schema, 'model_json_schema'):
            schema_name = response_schema.__name__.lower()
            schema = response_schema.model_json_schema()
            _make_schema_strict(schema)
            kwargs['text'] = {
                'format': {
                    'type': 'json_schema',
                    'name': schema_name,
                    'schema': schema,
                },
            }

        # ── Flex processing: 50% cost reduction ──────────────
        used_flex = False
        if self.use_flex:
            kwargs['service_tier'] = 'flex'
            used_flex = True

        # Call the Responses API.
        try:
            response = self.client.responses.create(**kwargs)
        except Exception as e:
            if used_flex and _is_flex_unavailable(e):
                self.logger.info('Flex unavailable (Responses API), retrying standard...')
                kwargs.pop('service_tier', None)
                used_flex = False
                self._flex_failures += 1
                response = self.client.responses.create(**kwargs)
            else:
                raise

        # Parse the response.
        output_text = response.output_text
        parsed = _extract_json(output_text)
        if parsed is None:
            self.logger.warning(
                f'OpenAI Responses API: failed to parse JSON '
                f'from response ({len(output_text)} chars).'
            )
            return None, 0.0, 0, 0

        # Extract usage (Responses API uses input_tokens/output_tokens).
        usage = getattr(response, 'usage', None)
        in_tok = getattr(usage, 'input_tokens', 0) if usage else 0
        out_tok = getattr(usage, 'output_tokens', 0) if usage else 0
        cost = self.calculate_cost(in_tok, out_tok, used_flex=used_flex)

        return parsed, cost, in_tok, out_tok

    # ── Anthropic Implementation ────────────────────────────────

    def _call_anthropic(
            self,
            system_prompt, user_prompt, pdf_path,
            page_images, page_text, mode,
        ) -> Tuple[Optional[Dict], float, int, int]:
        """Call Anthropic Claude with PDF or image support."""
        content = []

        # Strategy 1: PDF direct (Anthropic supports native PDF).
        if pdf_path and self.supports_pdf:
            with open(_long_path(pdf_path), 'rb') as f:
                pdf_b64 = base64.b64encode(f.read()).decode('utf-8')
            content.append({
                'type': 'document',
                'source': {
                    'type': 'base64',
                    'media_type': 'application/pdf',
                    'data': pdf_b64,
                },
            })

        # Strategy 2: Images.
        elif page_images:
            for img_path in page_images:
                b64 = _encode_image(img_path)
                content.append({
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': 'image/jpeg',
                        'data': b64,
                    },
                })

        # Strategy 3: Text only.
        elif page_text:
            user_prompt = f'{user_prompt}\n\nCOA Text:\n{page_text}'

        # Append the user prompt.
        content.append({'type': 'text', 'text': user_prompt})

        # Build the response format instruction.
        if mode == 'metadata':
            json_schema = _metadata_json_hint()
        else:
            json_schema = _analysis_json_hint()

        enhanced_system = (
            f'{system_prompt}\n\n'
            f'IMPORTANT: Respond ONLY with valid JSON matching this schema:\n'
            f'{json_schema}'
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.model_config.get('max_output_tokens', 16_384),
            system=enhanced_system,
            messages=[{'role': 'user', 'content': content}],
        )

        # Parse response.
        text = ''.join(
            block.text for block in response.content
            if hasattr(block, 'text')
        )
        parsed = _extract_json(text)
        if parsed is None:
            return None, 0.0, 0, 0

        # Calculate cost.
        in_tok = response.usage.input_tokens
        out_tok = response.usage.output_tokens
        cost = self.calculate_cost(in_tok, out_tok)

        return parsed, cost, in_tok, out_tok

    # ── Gemini Implementation ───────────────────────────────────

    def _call_gemini(
            self,
            system_prompt, user_prompt, pdf_path,
            page_images, page_text, response_schema, mode,
        ) -> Tuple[Optional[Dict], float, int, int]:
        """Call Google Gemini with PDF or image support."""
        from google.genai import types

        contents = []

        # Strategy 1: PDF direct.
        if pdf_path and self.supports_pdf:
            with open(_long_path(pdf_path), 'rb') as f:
                pdf_bytes = f.read()
            contents.append(types.Part.from_bytes(
                data=pdf_bytes,
                mime_type='application/pdf',
            ))

        # Strategy 2: Images.
        elif page_images:
            for img_path in page_images:
                with open(img_path, 'rb') as f:
                    img_bytes = f.read()
                contents.append(types.Part.from_bytes(
                    data=img_bytes,
                    mime_type='image/jpeg',
                ))

        # Strategy 3: Text.
        elif page_text:
            user_prompt = f'{user_prompt}\n\nCOA Text:\n{page_text}'

        contents.append(user_prompt)

        # Configure generation.
        gen_config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type='application/json',
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=gen_config,
        )

        # Parse response.
        parsed = _extract_json(response.text)
        if parsed is None:
            return None, 0.0, 0, 0

        # Calculate cost.
        usage = response.usage_metadata
        in_tok = getattr(usage, 'prompt_token_count', 0)
        out_tok = getattr(usage, 'candidates_token_count', 0)
        cost = self.calculate_cost(in_tok, out_tok)

        return parsed, cost, in_tok, out_tok


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Prompts                                                          ║
# ╚══════════════════════════════════════════════════════════════════╝

METADATA_SYSTEM_PROMPT = """You are an expert cannabis Certificate of Analysis (COA) parser. Extract structured metadata from the provided COA document. Return data as JSON matching the LabTestMetadata schema.

Fields to extract:
| Field | Type | Example | Description |
|-------|------|---------|-------------|
| product_name | str | "Blue Dream Preroll (1g)" | Full product name as shown |
| strain_name | str | "Blue Dream" | Cannabis strain/cultivar name |
| product_type | str | "flower" | One of: flower, concentrate, edible, preroll, vape, tincture, topical |
| date_tested | str | "2024-01-23" | Test/analysis completion date (ISO YYYY-MM-DD) |
| date_received | str | "2024-01-22" | Sample received date (ISO YYYY-MM-DD) |
| date_collected | str | "" | Sample collection date (ISO YYYY-MM-DD) |
| batch_number | str | "BN-2024-123" | Batch, lot, or metrc batch number |
| batch_size | float | 1000.0 | Batch size in grams (convert lbs/oz if needed) |
| lab | str | "SC Laboratories" | Testing laboratory full name |
| lab_license_number | str | "C8-0000013-LIC" | Lab license — copy EVERY character carefully |
| lab_address | str | "123 Main St" | Lab street address |
| lab_city | str | "Santa Cruz" | Lab city |
| lab_state | str | "CA" | Lab state (2-letter code) |
| lab_zipcode | str | "95060" | Lab ZIP code |
| producer | str | "ABC Farms LLC" | Producer/cultivator/manufacturer name |
| producer_street | str | "789 Farm Rd" | Producer street address |
| producer_city | str | "Bend" | Producer city |
| producer_state | str | "OR" | Producer state (2-letter code) |
| producer_zipcode | str | "97701" | Producer ZIP code |
| producer_license_number | str | "C11-0005002-LIC" | Producer license — copy carefully |
| distributor | str | "" | Distributor name (if listed) |
| distributor_license_number | str | "" | Distributor license (if listed) |
| sample_id | str | "2RLS-240530-018" | Lab sample ID — copy carefully |
| sample_weight | float | 1.0 | Sample weight in grams |
| total_cannabinoids | float | 54.79 | Total cannabinoids — ALWAYS in percent (%) |
| total_cbd | float | 0.5 | Total CBD — ALWAYS in percent (%) |
| total_thc | float | 18.0 | Total THC — ALWAYS in percent (%) |
| total_terpenes | float | 2.0 | Total terpenes — ALWAYS in percent (%) |
| status | str | "pass" | Overall pass/fail status |
| analyses | list | ["cannabinoids", "terpenes"] | List of all analysis types on the COA |

CRITICAL RULES:

1. TOTALS ARE ALWAYS IN PERCENT: total_thc, total_cbd, total_cannabinoids, and total_terpenes must ALWAYS be reported in percent (%). If the COA shows these as mg/g, divide by 10 to convert to percent. A total_thc of 737.71 mg/g = 73.771%. If total_thc appears as a large number (>100), it is likely mg/g and must be converted.

2. LICENSE NUMBERS AND SAMPLE IDS: These are alphanumeric codes where every character matters. Read them very carefully — distinguish between similar characters: 0 vs O, 1 vs I vs l, 8 vs B, 5 vs S. Copy exactly as printed.

3. DATES: Convert any date format to ISO (YYYY-MM-DD). "03/11/2024" → "2024-03-11". "March 11, 2024" → "2024-03-11". Use the test completion date for date_tested, not the report date.

4. DEFAULT VALUES: Return 0.0 for numeric fields not found, "" for string fields not found.

5. PRODUCT TYPE: Use lowercase. "Pre-Roll" → "preroll". "Vape Cartridge" → "vape". "Live Resin" → "concentrate". "Gummies" → "edible"."""

METADATA_USER_PROMPT = (
    'Extract the metadata from this Certificate of Analysis (COA). '
    'Remember: total_thc, total_cbd, total_cannabinoids, and total_terpenes '
    'must be in PERCENT (%). If shown as mg/g, divide by 10. '
    'Copy license numbers and sample IDs character-by-character. '
    'Return valid JSON matching the LabTestMetadata schema.'
)

ANALYSIS_SYSTEM_PROMPT = """You are an expert cannabis Certificate of Analysis (COA) parser. Extract lab test results for a SPECIFIC analysis type from the provided COA page(s). Return data as JSON with fields:

- "analysis": The analysis type name (string)
- "results": A list of result objects, each with:
  | Field | Type | Description |
  |-------|------|-------------|
  | key | str | Standardized analyte key (snake_case) |
  | name | str | Lab's displayed analyte name (as printed) |
  | value | float | The MEASURED TEST RESULT value (see rules below) |
  | units | str | Units of the value field (see rules below) |
  | limit | float | Action/regulatory limit (0.0 if not shown) |
  | lod | float | Limit of Detection (0.0 if not shown) |
  | loq | float | Limit of Quantification (0.0 if not shown) |
  | status | str | "pass", "fail", or "" |

═══════════════════════════════════════════════════════════════
CRITICAL: HOW TO READ COA TABLES CORRECTLY
═══════════════════════════════════════════════════════════════

Cannabis COA tables typically have MULTIPLE numeric columns per analyte. It is essential to identify the correct column for each field. Common layouts include:

LAYOUT A (Cannabinoids/Terpenes — dual-unit):
  Analyte | LOD(%) | LOQ(%) | Result(%) | Result(mg/g)
  Δ9-THC  |  0.01  |  0.03  |  73.771   |  737.71

LAYOUT B (Cannabinoids/Terpenes — single-unit with separate LOD):
  Analyte | Result(%) | LOD(%) | LOQ(%) | Status
  Δ9-THC  |  73.771   |  0.01  |  0.03  |  Pass

LAYOUT C (Pesticides/Heavy Metals):
  Analyte    | Result(ppb) | LOD(ppb) | LOQ(ppb) | Limit(ppb) | Status
  Abamectin  |    ND       |   10     |   20     |   100      |  Pass

LAYOUT D (Microbials):
  Analyte        | Result(cfu/g) | Limit(cfu/g) | Status
  Total Aerobic  |    <100       |   10000      |  Pass

KEY RULES FOR IDENTIFYING THE CORRECT VALUE:

1. VALUE = the TEST RESULT, not LOD or LOQ.
   - The "Result", "Concentration", "Amount", or "Tested" column is the value.
   - LOD and LOQ are METHOD parameters (detection/quantification limits). They are NOT test results.
   - LOD is always ≤ LOQ. Both are usually small numbers near zero.
   - If you see a column header with "LOD" or "LOQ" or "Detection" or "Quantification", that column goes in the lod or loq field, NOT the value field.

2. UNIT PREFERENCE for Cannabinoids and Terpenes:
   - PREFERRED: percent (%) — report values from the "%" or "Result(%)" column.
   - If a COA shows BOTH percent AND mg/g columns, use the PERCENT column for "value" and "percent" for "units".
   - If a COA shows ONLY mg/g (no percent column), use mg/g and set units to "mg/g".
   - EXCEPTION for EDIBLES: Use "mg" (milligrams per serving/package) or "mg/g" as shown. Edible COAs commonly report potency in mg, which is correct.
   - How to tell the columns apart: percent values for cannabinoids are typically 0-100 (e.g., 73.771%). The mg/g equivalent is 10× larger (e.g., 737.71 mg/g). If you see two columns where one is exactly 10× the other, the smaller one is percent.

3. UNIT PREFERENCE for Pesticides and Heavy Metals:
   - Use the units shown on the COA: typically "ppb", "ppm", "ug/g", or "ug/kg".
   - The RESULT column contains the test result. "ND" (Not Detected) = 0.0.
   - The ACTION LIMIT column goes in the "limit" field.

4. UNIT PREFERENCE for Microbials:
   - Use "cfu/g" (colony forming units per gram) as shown.
   - For mycotoxin tests, use "ppb" or "ug/kg" as shown.

5. UNIT PREFERENCE for Moisture and Water Activity:
   - Moisture content: use "percent".
   - Water activity (aW): use "aW" (dimensionless, typically 0.0-1.0).

6. HANDLING SPECIAL VALUES:
   - "ND" (Not Detected) → value = 0.0
   - "<LOQ" → value = 0.0 (the analyte was detected but below quantification)
   - "N/A" or blank → value = 0.0
   - "Pass"/"Fail" in the result column (with no numeric value) → value = 0.0, set status field instead

7. GENERAL:
   - Extract ONLY results for the specified analysis type.
   - Use standardized analyte keys (snake_case).
   - Include ALL analytes shown in the table, even if not in the standard key list.
   - LOD and LOQ should use the same units as the value field where possible."""

ANALYSIS_USER_PROMPT = (
    'Extract ONLY the %s results from this COA page(s). '
    'Standard analyte keys for this analysis:\n\n%s\n\n'
    'Remember: "value" = the TEST RESULT column (not LOD or LOQ). '
    'For cannabinoids/terpenes, prefer the percent (%%) column over mg/g. '
    'Return valid JSON with "analysis" and "results" fields.'
)

SINGLE_PAGE_SYSTEM_PROMPT = """You are an expert cannabis Certificate of Analysis (COA) parser. This is a single-page COA. Extract ALL available data in one pass.

Return JSON with these top-level fields:
- "metadata": Object with product_name, strain_name, product_type, date_tested, date_received, date_collected, batch_number, batch_size, lab, lab_license_number, lab_address, lab_city, lab_state, lab_zipcode, producer, producer_street, producer_city, producer_state, producer_zipcode, producer_license_number, distributor, distributor_license_number, sample_id, sample_weight, total_cannabinoids, total_cbd, total_thc, total_terpenes, status, analyses.
- "cannabinoids": List of result objects for cannabinoid analytes (if present).
- "terpenes": List of result objects for terpene analytes (if present).

Each result object: {"key": "snake_case_name", "name": "Lab Display Name", "value": 0.0, "units": "percent", "limit": 0.0, "lod": 0.0, "loq": 0.0, "status": "pass"}

CRITICAL RULES:

1. TOTALS IN METADATA: total_thc, total_cbd, total_cannabinoids, total_terpenes are ALWAYS in percent (%). If shown as mg/g, divide by 10 to convert.

2. VALUE = TEST RESULT, not LOD or LOQ:
   - COA tables have multiple numeric columns. The "Result" or "Concentration" column is the value.
   - LOD (Limit of Detection) and LOQ (Limit of Quantification) are METHOD parameters — do NOT use them as the value.
   - LOD ≤ LOQ, and both are usually small numbers near zero.

3. UNIT PREFERENCE for Cannabinoids/Terpenes:
   - PREFER percent (%) over mg/g when both columns are shown.
   - If both appear, the percent column has smaller values (e.g., 73.771%) and the mg/g column is ~10× larger (e.g., 737.71 mg/g). Use the percent column.
   - EXCEPTION for edibles: use mg or mg/g as shown on the COA.

4. HANDLING SPECIAL VALUES: "ND" = 0.0. "<LOQ" = 0.0. Blank = 0.0.

5. DEFAULT VALUES: 0.0 for missing numeric fields, "" for missing strings. Dates in ISO format (YYYY-MM-DD). product_type in lowercase (flower, concentrate, edible, preroll, vape, tincture).

6. LICENSE/SAMPLE IDs: Copy EVERY character carefully — distinguish 0/O, 1/I/l, 8/B, 5/S.

7. Include ALL analytes shown on the COA. If only cannabinoids are present (e.g., hemp COAs), leave "terpenes" as an empty list. If additional analyses (pesticides, heavy metals, etc.) are present, include them as additional keys."""

SINGLE_PAGE_USER_PROMPT = (
    'Extract ALL metadata and lab test results from this single-page COA. '
    'Remember: "value" = the TEST RESULT column (not LOD/LOQ). '
    'For cannabinoids/terpenes, prefer percent (%) over mg/g. '
    'Return valid JSON with "metadata", "cannabinoids", and "terpenes" fields '
    '(plus any additional analyses found).'
)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ PDF Utilities                                                    ║
# ╚══════════════════════════════════════════════════════════════════╝

def _encode_image(
        image_path: str,
        max_width: int = 2000,
        max_height: int = 2000,
    ) -> str:
    """Encode an image as a base64 string."""
    with open(image_path, 'rb') as f:
        return base64.b64encode(f.read()).decode('utf-8')


def _extract_json(text: str) -> Optional[Dict]:
    """Extract JSON from an AI response, handling markdown code fences."""
    if not text:
        return None
    text = text.strip()
    # Remove markdown code fences.
    if text.startswith('```'):
        lines = text.split('\n')
        lines = [l for l in lines if not l.strip().startswith('```')]
        text = '\n'.join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON within the text.
        start = text.find('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    return None


def _make_schema_strict(schema: Dict) -> None:
    """Recursively make a JSON schema compatible with OpenAI structured output.

    OpenAI's Responses API structured output requires every object
    in the JSON schema to:
      1. Set ``additionalProperties`` to ``false``
      2. List ALL property keys in ``required``

    Pydantic's ``model_json_schema()`` omits both of these for
    Optional fields. This function mutates the schema in-place.
    """
    if not isinstance(schema, dict):
        return

    # Process $defs (Pydantic puts nested model schemas here).
    for defn in schema.get('$defs', {}).values():
        _make_schema_strict(defn)

    # If this is an object type, enforce strict constraints.
    if schema.get('type') == 'object':
        schema['additionalProperties'] = False
        # All properties must be required.
        if 'properties' in schema:
            schema['required'] = list(schema['properties'].keys())

    # Recurse into properties.
    for prop in schema.get('properties', {}).values():
        _make_schema_strict(prop)

    # Recurse into array items.
    if 'items' in schema:
        _make_schema_strict(schema['items'])

    # Handle anyOf / oneOf / allOf (used by Optional fields).
    for key in ('anyOf', 'oneOf', 'allOf'):
        for variant in schema.get(key, []):
            _make_schema_strict(variant)


def _metadata_json_hint() -> str:
    """Return a JSON schema hint for metadata extraction."""
    return json.dumps({
        'product_name': 'str', 'strain_name': 'str', 'product_type': 'str',
        'date_tested': 'YYYY-MM-DD', 'date_received': 'YYYY-MM-DD',
        'date_collected': 'YYYY-MM-DD',
        'batch_number': 'str', 'batch_size': 0.0,
        'lab': 'str', 'lab_license_number': 'str',
        'lab_address': 'str', 'lab_city': 'str', 'lab_state': 'str', 'lab_zipcode': 'str',
        'producer': 'str', 'producer_street': 'str', 'producer_city': 'str',
        'producer_state': 'str', 'producer_zipcode': 'str',
        'producer_license_number': 'str',
        'distributor': 'str', 'distributor_license_number': 'str',
        'sample_id': 'str', 'sample_weight': 0.0,
        'total_cannabinoids': 0.0, 'total_cbd': 0.0, 'total_thc': 0.0,
        'total_terpenes': 0.0, 'status': 'str',
        'analyses': ['cannabinoids', 'terpenes'],
    }, indent=2)


def _analysis_json_hint() -> str:
    """Return a JSON schema hint for analysis extraction."""
    return json.dumps({
        'analysis': 'str',
        'results': [
            {'key': 'str', 'name': 'str', 'value': 0.0, 'units': 'str',
             'limit': 0.0, 'lod': 0.0, 'loq': 0.0, 'status': 'str'}
        ],
    }, indent=2)


def get_pdf_info(pdf_path: str) -> Dict:
    """Get basic info about a PDF (page count, text presence)."""
    try:
        with pdfplumber.open(_long_path(pdf_path)) as pdf:
            num_pages = len(pdf.pages)
            first_page_text = (pdf.pages[0].extract_text() or '') if pdf.pages else ''
            has_text = len(first_page_text) > 50
            # Detect analyses mentioned.
            all_text = ''
            for page in pdf.pages[:5]:  # Check first 5 pages max.
                t = page.extract_text() or ''
                all_text += ' ' + t.lower()
            detected_analyses = []
            for analysis, config in ANALYSIS_CONFIGS.items():
                for keyword in config['keywords']:
                    if keyword.lower() in all_text:
                        detected_analyses.append(analysis)
                        break
            return {
                'num_pages': num_pages,
                'has_text': has_text,
                'first_page_text': first_page_text[:500],
                'detected_analyses': detected_analyses,
            }
    except Exception as e:
        return {'num_pages': 0, 'has_text': False, 'error': str(e)}


def get_pdf_pages_as_images(
        pdf_path: str,
        page_indexes: Union[int, List[int], str] = 0,
        keywords: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        resolution: int = 300,
    ) -> List[str]:
    """Convert PDF pages to JPEG images."""
    if output_dir is None:
        output_dir = tempfile.mkdtemp()
    os.makedirs(output_dir, exist_ok=True)

    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    image_paths = []

    try:
        with pdfplumber.open(_long_path(pdf_path)) as pdf:
            total_pages = len(pdf.pages)

            # Determine which pages to process.
            if isinstance(page_indexes, int):
                pages = [min(page_indexes, total_pages - 1)]
            elif isinstance(page_indexes, list):
                pages = [p for p in page_indexes if p < total_pages]
            elif page_indexes == 'all':
                pages = list(range(total_pages))
            elif page_indexes == 'keywords' and keywords:
                # Find pages that contain any of the analysis keywords.
                # Do NOT include page 0 by default — we only want
                # the pages actually containing the analysis data.
                pages = []
                for i in range(total_pages):
                    text = (pdf.pages[i].extract_text() or '').lower()
                    if any(kw.lower() in text for kw in keywords):
                        pages.append(i)
                pages = sorted(set(pages))
                # If no keyword matches, fall back to page 0.
                if not pages:
                    pages = [0]
            else:
                pages = [0]

            for idx in pages:
                out_path = os.path.join(
                    output_dir,
                    f'{pdf_name}_p{idx + 1:03d}.jpeg',
                )
                pdf.pages[idx].to_image(resolution=resolution).save(out_path)
                image_paths.append(out_path)

    except Exception as e:
        logging.getLogger(__name__).error(f'PDF image conversion failed: {e}')

    return image_paths


def extract_pdf_text(
        pdf_path: str,
        keywords: Optional[List[str]] = None,
    ) -> str:
    """Extract text from PDF pages matching keywords."""
    try:
        with pdfplumber.open(_long_path(pdf_path)) as pdf:
            if not pdf.pages:
                return ''
            if not keywords:
                return pdf.pages[0].extract_text() or ''
            texts = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    text_lower = text.lower()
                    if any(kw.lower() in text_lower for kw in keywords):
                        texts.append(text)
            return '\n\n'.join(texts) if texts else (pdf.pages[0].extract_text() or '')
    except Exception:
        return ''


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Core Parsing Engine                                              ║
# ╚══════════════════════════════════════════════════════════════════╝

class COAParser:
    """Production-grade COA parsing engine.

    Orchestrates multi-provider AI parsing with intelligent
    strategy selection, caching, and cost tracking.
    """

    def __init__(
            self,
            state: str,
            provider: str = 'openai',
            model: Optional[str] = None,
            method: str = 'auto',
            data_dir: Optional[Path] = None,
            cache_dir: Optional[Path] = None,
            log_dir: Optional[Path] = None,
            budget: Optional[float] = None,
            max_parses: Optional[int] = None,
            qrustie_path: Optional[str] = None,
            logger: Optional[logging.Logger] = None,
        ):
        self.state = state.lower()
        self.state_name = STATE_NAMES.get(self.state, self.state)
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.log_dir = log_dir or DEFAULT_LOG_DIR
        self.budget = budget
        self.max_parses = max_parses
        self.method = method  # 'auto', 'algorithm', 'ai'
        self.qrustie_path = qrustie_path

        # Ensure directories exist.
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Initialize logger.
        self.logger = logger or initialize_logs(
            f'parse_coas_{self.state}',
            prefix=f'parse-coas-{self.state}',
            log_dir=str(self.log_dir),
        )

        # Initialize AI client (only if method allows AI).
        config = dotenv_values('../.env')
        if method != 'algorithm':
            self.ai_client = AIClient(
                provider=provider,
                model=model,
                config=config,
                logger=self.logger,
            )
        else:
            self.ai_client = None

        # Initialize cost tracker.
        self.cost_tracker = CostTracker()

        # Initialize caches.
        model_tag = (model or AI_PROVIDERS[provider]['default_model']).replace('.', '_')
        self.metadata_cache = Bogart(
            str(self.cache_dir / f'results-{self.state}-metadata-{model_tag}.jsonl')
        )
        self.analysis_caches = {}
        for analysis_name in ANALYSIS_CONFIGS:
            self.analysis_caches[analysis_name] = Bogart(
                str(self.cache_dir / f'results-{self.state}-{analysis_name}-{model_tag}.jsonl')
            )

        # Separate cache for algorithm-parsed results.
        # Algorithm results are stored as flat, unified records (metadata +
        # all results in one entry) rather than the split metadata/analysis
        # format used by AI parsing. This enables:
        #   1. Clean benchmarking (same pdf_hash in both caches = comparison)
        #   2. Natural format per method (algorithms return everything at once)
        #   3. Independent re-parsing without cache pollution
        # File: results-{state}-algorithm.jsonl
        self.algo_results_cache = Bogart(
            str(self.cache_dir / f'results-{self.state}-algorithm.jsonl')
        )

        # Preload algorithmic parsers for recognized labs.
        self._algorithm_cache: Dict[str, Optional[Callable]] = {}
        self._algo_stats = {'identified': 0, 'parsed': 0, 'failed': 0, 'ai_fallback': 0}

        # Invalid PDF cache — persistent set of known-bad hashes.
        # This prevents re-attempting corrupted/non-PDF files on
        # subsequent runs. Common in AZ (Curaleaf HTML error pages),
        # NY (Reddit-sourced files), and other corpora.
        self.invalid_pdf_cache = InvalidPDFCache(
            str(self.cache_dir / f'invalid-pdfs-{self.state}.txt')
        )
        self._invalid_count = 0  # Count for this run.

    @property
    def pdf_dir(self) -> Path:
        """Get the PDF directory for this state."""
        return self.data_dir / self.state_name / 'results' / 'pdfs'

    def discover_pdfs(self, source: str = '') -> pd.DataFrame:
        """Discover all COA PDFs for this state/source."""
        search_dir = self.pdf_dir / source if source else self.pdf_dir
        if not search_dir.exists():
            self.logger.warning(f'PDF directory not found: {search_dir}')
            return pd.DataFrame()

        pdf_files = []
        skipped_long = 0
        for root, _, files in os.walk(str(search_dir)):
            for f in files:
                if f.lower().endswith('.pdf'):
                    fp = os.path.join(root, f)
                    if _safe_file_size(fp):
                        pdf_files.append(fp)
                    elif IS_WINDOWS and len(fp) > WIN_MAX_PATH:
                        skipped_long += 1

        if skipped_long:
            self.logger.warning(
                f'Skipped {skipped_long} PDFs that could not be '
                f'accessed (likely long path issues).'
            )

        pdf_files.sort()
        df = pd.DataFrame({'file_path': pdf_files})
        if df.empty:
            return df

        df['pdf_hash'] = df['file_path'].apply(
            lambda x: hash_file(_long_path(x), size=65536)
        )
        df.drop_duplicates(subset=['pdf_hash'], inplace=True)
        self.logger.info(f'Discovered {len(df):,} unique PDFs in {search_dir}')
        return df

    def parse_all(
            self,
            source: str = '',
            sample_size: Optional[int] = None,
            random_state: int = 42,
            analyses: Optional[List[str]] = None,
        ) -> Dict:
        """Parse all discovered COA PDFs.

        Args:
            source: Optional source filter (e.g., 'prr', 'flowery').
            sample_size: If set, randomly sample this many PDFs.
            random_state: Random seed for reproducibility.
            analyses: List of analyses to parse. None = all detected.

        Returns:
            Summary statistics dict.
        """
        # Discover PDFs.
        pdfs = self.discover_pdfs(source)
        if pdfs.empty:
            self.logger.info('No PDFs found.')
            return {'parsed': 0, 'skipped': 0, 'errors': 0}

        # Optionally sample.
        if sample_size and sample_size < len(pdfs):
            pdfs = pdfs.sample(n=sample_size, random_state=random_state)
            self.logger.info(f'Sampled {len(pdfs):,} PDFs.')

        # Apply max_parses limit.
        if self.max_parses:
            pdfs = pdfs.head(self.max_parses)

        total = len(pdfs)
        parsed_count = 0
        skipped_count = 0
        error_count = 0

        self.logger.info(f'Starting parse of {total:,} COAs...')

        for i, (_, row) in enumerate(pdfs.iterrows()):
            # Budget check.
            if self.budget and self.cost_tracker.total_cost >= self.budget:
                self.logger.info(
                    f'Budget exhausted (${self.cost_tracker.total_cost:.4f} '
                    f'>= ${self.budget:.2f}). Stopping.'
                )
                break

            # Provider exhaustion check (only for AI-dependent modes).
            if self.method != 'algorithm' and self.ai_client and not self.ai_client.is_available:
                self.logger.warning('AI provider exhausted. Stopping.')
                break

            pdf_hash = row['pdf_hash']
            file_path = row['file_path']
            self.logger.info(f'--- [{i + 1}/{total}] {os.path.basename(file_path)} ---')

            try:
                result = self._parse_single_coa(
                    pdf_hash=pdf_hash,
                    file_path=file_path,
                    analyses=analyses,
                )
                if result == 'skipped':
                    skipped_count += 1
                else:
                    parsed_count += 1
            except Exception as e:
                import traceback
                self.logger.error(
                    f'Error parsing {pdf_hash}: {e}\n'
                    f'{traceback.format_exc()}'
                )
                error_count += 1

        # Summary.
        summary = {
            'state': self.state,
            'method': self.method,
            'total_pdfs': total,
            'parsed': parsed_count,
            'skipped': skipped_count,
            'invalid_pdfs': self._invalid_count,
            'invalid_pdfs_total': len(self.invalid_pdf_cache),
            'errors': error_count,
            'costs': self.cost_tracker.summary(),
            'flex_enabled': self.ai_client.use_flex if self.ai_client else False,
            'flex_failures': self.ai_client._flex_failures if self.ai_client else 0,
            'algorithm_stats': self._algo_stats,
        }
        algo = self._algo_stats
        self.logger.info(
            f'Parse complete. Parsed: {parsed_count}, '
            f'Skipped: {skipped_count}, Errors: {error_count}, '
            f'Invalid: {self._invalid_count} (this run), '
            f'{len(self.invalid_pdf_cache)} (total cached). '
            f'Cost: ${self.cost_tracker.total_cost:.4f}. '
            f'Algorithm: {algo["parsed"]} parsed, {algo["identified"]} identified, '
            f'{algo["failed"]} failed, {algo["ai_fallback"]} AI fallback.'
        )
        return summary

    def _parse_single_coa(
            self,
            pdf_hash: str,
            file_path: str,
            analyses: Optional[List[str]] = None,
        ) -> str:
        """Parse a single COA PDF using the hybrid approach.

        Routing strategy:
        1. **Algorithm-first** (method='auto' or 'algorithm'):
           Identify the lab → load parser → run → validate → cache.
           If successful, return immediately (free, fast, deterministic).
        2. **AI fallback** (method='auto' with failed algorithm, or 'ai'):
           Use the multi-strategy AI parsing engine.

        Three AI parsing strategies depending on COA characteristics:
        1. **Single-page COAs** (1 page): One-shot parse extracts
           metadata + all results in a single API call.
        2. **Multi-page COAs with text** (2+ pages): Page-targeted
           extraction — page 1 for metadata, keyword-matched pages
           for each analysis.
        3. **Image-only COAs** (no extractable text): Falls back to
           sending the full PDF via the Responses API so the model
           can OCR the content.

        Returns 'parsed', 'skipped', or 'algorithm_parsed'.
        """
        # ── PRE-FLIGHT: Invalid PDF detection ─────────────────────
        # Check the persistent cache first (O(1) set lookup), then
        # validate structurally if this is a first-time encounter.
        # Catches: HTML error pages saved as .pdf (Curaleaf, Reddit),
        # corrupted downloads, empty placeholders, truncated files,
        # and PDFs with valid headers but corrupt internal structure.
        if pdf_hash in self.invalid_pdf_cache:
            self._invalid_count += 1
            # Log the first few, then go quiet to avoid spam.
            if self._invalid_count <= 5:
                self.logger.info(f'Skipped (cached invalid PDF)')
            elif self._invalid_count == 6:
                self.logger.info(
                    f'Skipped (cached invalid PDF) — '
                    f'suppressing further invalid messages'
                )
            return 'skipped'

        valid, reason = is_valid_pdf(file_path)
        if not valid:
            self.invalid_pdf_cache.add(pdf_hash, reason)
            self._invalid_count += 1
            self.logger.info(
                f'Invalid PDF detected ({reason}): '
                f'{os.path.basename(file_path)}'
            )
            return 'skipped'

        # ── HYBRID ROUTING: Try algorithmic parsing first ─────────
        if self.method in ('auto', 'algorithm'):
            algo_result = self._try_algorithmic_parse(
                pdf_hash, file_path, analyses,
            )
            if algo_result == 'algorithm_parsed':
                return 'parsed'
            elif self.method == 'algorithm':
                # Algorithm-only mode: skip if no algorithm available.
                if algo_result == 'no_algorithm':
                    self.logger.info(
                        f'No algorithm available for this COA. '
                        f'Skipping (method=algorithm).'
                    )
                    return 'skipped'
                elif algo_result == 'algorithm_failed':
                    self.logger.warning(
                        f'Algorithm failed. Skipping (method=algorithm).'
                    )
                    return 'skipped'
            # else: method='auto', algorithm failed → fall through to AI

        # ── AI PARSING: Standard multi-strategy AI engine ─────────
        if self.ai_client is None or not self.ai_client.is_available:
            self.logger.warning('AI client not available. Skipping.')
            return 'skipped'
        # ── Pre-flight: PDF info ─────────────────────────────────
        pdf_info = get_pdf_info(file_path)
        num_pages = pdf_info.get('num_pages', 1)
        has_text = pdf_info.get('has_text', False)
        detected_from_pdf = pdf_info.get('detected_analyses', [])
        is_single_page = num_pages == 1

        # ── FAST PATH: Single-page COAs ──────────────────────────
        if is_single_page:
            return self._parse_single_page_coa(
                pdf_hash, file_path, pdf_info, analyses,
            )

        # ── STANDARD PATH: Multi-page COAs ───────────────────────

        # Check for image-only PDFs (OCR needed).
        is_image_only = not has_text and not detected_from_pdf

        # ── Step 1: Parse metadata ───────────────────────────────
        # Strategy: Page 1 only. If key fields are missing
        # (cover sheet), retry with pages 1+2.

        if self.metadata_cache.get(pdf_hash):
            self.logger.info(f'Metadata cached: {pdf_hash[:12]}...')
            metadata = self.metadata_cache.get(pdf_hash)
        else:
            self.logger.info('Parsing metadata...')
            start = time.time()

            if is_image_only:
                # Image-only PDF: send full PDF for OCR.
                self.logger.info(
                    f'Strategy: full PDF via Responses API '
                    f'({num_pages} pages, image-only/OCR)'
                )
                parsed, cost, in_tok, out_tok = self.ai_client.parse_metadata(
                    pdf_path=file_path,
                )
            else:
                # Text-based PDF: page 1 image only.
                with tempfile.TemporaryDirectory() as tmpdir:
                    images = get_pdf_pages_as_images(
                        file_path, page_indexes=[0],
                        output_dir=tmpdir,
                    )
                    self.logger.info(
                        f'Strategy: page 1 image (of {num_pages} total)'
                    )
                    parsed, cost, in_tok, out_tok = self.ai_client.parse_metadata(
                        pdf_path=None,
                        page_images=images if images else None,
                        page_text=None,
                    )

                # Retry with pages 1+2 if key metadata fields are missing.
                if parsed and self._metadata_needs_retry(parsed):
                    self.logger.info(
                        'Key metadata fields missing — retrying '
                        'with pages 1+2 (likely cover sheet)...'
                    )
                    with tempfile.TemporaryDirectory() as tmpdir:
                        images = get_pdf_pages_as_images(
                            file_path, page_indexes=[0, 1],
                            output_dir=tmpdir,
                        )
                        parsed2, cost2, in2, out2 = self.ai_client.parse_metadata(
                            pdf_path=None,
                            page_images=images if images else None,
                            page_text=None,
                        )
                    if parsed2:
                        # Merge: prefer non-empty fields from retry.
                        for k, v in parsed2.items():
                            if v and (not parsed.get(k) or parsed.get(k) in ('', 0.0, [])):
                                parsed[k] = v
                        cost += cost2
                        in_tok += in2
                        out_tok += out2

            if parsed is None:
                self.logger.warning(f'Metadata parse failed: {pdf_hash[:12]}...')
                return 'skipped'

            elapsed = time.time() - start
            metadata = {
                'pdf_hash': pdf_hash,
                'parsing_method': 'ai',
                'parsing_algorithm': None,
                'parsing_model': self.ai_client.model,
                'parsing_provider': self.ai_client.provider,
                'parsing_time': round(elapsed, 2),
                'parsing_cost': round(cost, 6),
                **parsed,
            }
            self.metadata_cache.set(pdf_hash, metadata)
            self.cost_tracker.record(
                self.ai_client.provider, self.ai_client.model,
                in_tok, out_tok, cost, 'metadata', pdf_hash,
            )
            self.logger.info(
                f'Metadata parsed: ${cost:.4f}, {round(elapsed)}s'
            )

        # ── Step 2: Determine which analyses to parse ────────────

        product_type = normalize_product_type(
            metadata.get('product_type', '')
        )
        detected = metadata.get('analyses', [])

        # Combine detected analyses from metadata and PDF text scan.
        all_detected = set(detected_from_pdf)
        for a in (detected or []):
            a_lower = a.lower().replace(' ', '_')
            for config_name in ANALYSIS_CONFIGS:
                if config_name in a_lower or a_lower in config_name:
                    all_detected.add(config_name)

        # Always include cannabinoids and terpenes.
        all_detected.add('cannabinoids')
        all_detected.add('terpenes')

        # Filter by product type restrictions.
        target_analyses = []
        skipped_by_rule = []
        for analysis_name in ANALYSIS_CONFIGS:
            if analyses and analysis_name not in analyses:
                continue
            config = ANALYSIS_CONFIGS[analysis_name]
            allowed_types = config.get('product_types')
            if allowed_types and product_type not in allowed_types:
                continue

            # ── Bayesian skip rules ──────────────────────────
            # Skip analyses with near-zero prior probability for
            # this product type (e.g., terpenes for edibles).
            skip_types = ANALYSIS_SKIP_RULES.get(analysis_name, [])
            if product_type in skip_types:
                skipped_by_rule.append(analysis_name)
                continue

            if analysis_name in all_detected or not allowed_types:
                target_analyses.append(analysis_name)

        if skipped_by_rule:
            self.logger.info(
                f'Skipped by prior: {skipped_by_rule} '
                f'(product_type={product_type})'
            )

        self.logger.info(
            f'Product type: {product_type}. '
            f'Target analyses: {target_analyses}'
        )

        # ── Step 3: Parse each analysis ──────────────────────────

        for analysis_name in target_analyses:
            cache = self.analysis_caches[analysis_name]

            # Check cache.
            if cache.get(pdf_hash):
                self.logger.info(f'{analysis_name}: cached')
                continue

            # Budget check.
            if self.budget and self.cost_tracker.total_cost >= self.budget:
                self.logger.info('Budget exhausted mid-COA.')
                break

            config = ANALYSIS_CONFIGS[analysis_name]
            keywords = config['keywords']
            analyte_keys = config['keys']

            self.logger.info(f'Parsing {analysis_name}...')
            start = time.time()

            if is_image_only:
                # Image-only PDF: send full PDF for OCR.
                self.logger.info(
                    f'{analysis_name}: strategy=full PDF (image-only/OCR)'
                )
                parsed, cost, in_tok, out_tok = self.ai_client.parse_analysis(
                    analysis_name=analysis_name,
                    analyte_keys=analyte_keys,
                    pdf_path=file_path,
                )
            else:
                with tempfile.TemporaryDirectory() as tmpdir:
                    # Keyword-targeted page images.
                    images = get_pdf_pages_as_images(
                        file_path,
                        page_indexes='keywords',
                        keywords=keywords,
                        output_dir=tmpdir,
                    )
                    text = extract_pdf_text(
                        file_path, keywords=keywords,
                    ) if not images else None
                    n_pages = len(images) if images else 0
                    self.logger.info(
                        f'{analysis_name}: strategy='
                        f'{n_pages} keyword-targeted page(s)'
                        f'{" + text fallback" if text and not images else ""}'
                    )
                    parsed, cost, in_tok, out_tok = self.ai_client.parse_analysis(
                        analysis_name=analysis_name,
                        analyte_keys=analyte_keys,
                        pdf_path=None,
                        page_images=images if images else None,
                        page_text=text,
                    )

            elapsed = time.time() - start

            if parsed is not None:
                cache_entry = {
                    'pdf_hash': pdf_hash,
                    'parsing_method': 'ai',
                    'parsing_algorithm': None,
                    'parsing_model': self.ai_client.model,
                    'parsing_provider': self.ai_client.provider,
                    'parsing_time': round(elapsed, 2),
                    'parsing_cost': round(cost, 6),
                    'results': parsed.get('results', []),
                }
                cache.set(pdf_hash, cache_entry)
                self.cost_tracker.record(
                    self.ai_client.provider, self.ai_client.model,
                    in_tok, out_tok, cost, analysis_name, pdf_hash,
                )
                n_results = len(parsed.get('results', []))
                self.logger.info(
                    f'{analysis_name}: {n_results} results, '
                    f'${cost:.4f}, {round(elapsed)}s'
                )
            else:
                self.logger.warning(f'{analysis_name}: parse failed')

        return 'parsed'

    def _try_algorithmic_parse(
            self,
            pdf_hash: str,
            file_path: str,
            analyses: Optional[List[str]] = None,
        ) -> str:
        """Attempt to parse a COA using a deterministic algorithm.

        This is the algorithmic fast path. If the lab is identified
        and an algorithm is available and succeeds, the results are
        cached as a unified flat record in the algorithm cache
        (``results-{state}-algorithm.jsonl``), completely separate
        from the AI-parsed cache files.

        Returns:
            'algorithm_parsed': Successfully parsed algorithmically.
            'no_algorithm': Lab not recognized or no algorithm available.
            'algorithm_failed': Algorithm raised an exception.
        """
        # ── Check algorithm cache first ───────────────────────────
        if self.algo_results_cache.get(pdf_hash):
            self.logger.info(f'Algorithm cache hit: {pdf_hash[:12]}...')
            self._algo_stats['parsed'] += 1
            return 'algorithm_parsed'

        # ── Step 1: Identify the lab ──────────────────────────────
        lab_key = identify_lab(
            pdf_path=file_path,
            lab_registry=LAB_REGISTRY,
            deep_search=True,
            qr_fallback=bool(self.qrustie_path),
            qrustie_path=self.qrustie_path,
            logger=self.logger,
        )
        if lab_key is None:
            return 'no_algorithm'

        self._algo_stats['identified'] += 1
        lab_name = LAB_REGISTRY[lab_key]['name']
        self.logger.info(f'Lab identified: {lab_name} ({lab_key})')

        # ── Step 2: Load the algorithm ────────────────────────────
        if lab_key not in self._algorithm_cache:
            self._algorithm_cache[lab_key] = load_algorithm(
                lab_key, logger=self.logger,
            )
        algorithm = self._algorithm_cache[lab_key]
        if algorithm is None:
            self.logger.info(
                f'No loadable algorithm for {lab_name}. '
                f'Falling back to AI.'
            )
            return 'no_algorithm'

        # ── Step 3: Run the algorithm ─────────────────────────────
        self.logger.info(
            f'Running algorithmic parser: {lab_key} '
            f'v{LAB_REGISTRY[lab_key].get("version", "?")}'
        )
        start = time.time()
        try:
            raw_output = algorithm(None, file_path)
        except Exception as e:
            import traceback
            elapsed = time.time() - start
            self.logger.warning(
                f'Algorithm {lab_key} failed after {elapsed:.1f}s: {e}\n'
                f'{traceback.format_exc()}'
            )
            self._algo_stats['failed'] += 1
            return 'algorithm_failed'

        elapsed = time.time() - start

        if not raw_output or not isinstance(raw_output, dict):
            self.logger.warning(
                f'Algorithm {lab_key} returned empty/invalid output.'
            )
            self._algo_stats['failed'] += 1
            return 'algorithm_failed'

        # ── Step 4: Adapt output to hybrid cache schema ───────────
        adapted = adapt_algorithm_output(
            raw_output, pdf_hash, lab_key, elapsed=elapsed,
        )
        if adapted is None:
            self.logger.warning(
                f'Algorithm output adaptation failed for {lab_key}.'
            )
            self._algo_stats['failed'] += 1
            return 'algorithm_failed'

        # ── Step 5: Build flat unified record ─────────────────────
        # Algorithm results are stored as flat, pre-merged records
        # in a SEPARATE cache from AI results. Each record has
        # metadata fields at the top level and all analyte results
        # combined into a single 'results' JSON list. This matches
        # the shape that agg_results.py produces AFTER its AI merge
        # step, so algorithm records can be loaded directly.
        metadata = adapted['metadata']
        analysis_entries = adapted.get('analyses', {})

        # Combine all analysis results into a single list.
        all_results = []
        analyses_present = []
        for analysis_name, entry in analysis_entries.items():
            results_list = entry.get('results', [])
            if results_list:
                analyses_present.append(analysis_name)
            for r in results_list:
                if isinstance(r, dict) and 'analysis' not in r:
                    r['analysis'] = analysis_name
                all_results.append(r)

        # Build the flat record.
        version = LAB_REGISTRY[lab_key].get('version', '0.0.0')
        flat_record = {
            'pdf_hash': pdf_hash,
            'state': self.state,
            'parsing_method': 'algorithm',
            'parsing_algorithm': f'{lab_key}_v{version}',
            'parsing_model': None,
            'parsing_provider': 'local',
            'parsing_time': round(elapsed, 2),
            'parsing_cost': 0.0,
        }

        # Copy metadata fields.
        for key in [
            'product_name', 'strain_name', 'product_type',
            'date_tested', 'date_received', 'date_collected',
            'batch_number', 'batch_size', 'sample_weight',
            'lab', 'lab_license_number', 'lab_address',
            'lab_city', 'lab_state', 'lab_zipcode',
            'producer', 'producer_street', 'producer_city',
            'producer_state', 'producer_zipcode',
            'producer_license_number',
            'distributor', 'distributor_license_number',
            'sample_id', 'lab_id',
            'total_thc', 'total_cbd', 'total_cannabinoids',
            'total_terpenes', 'status',
            'lab_results_url', 'metrc_ids',
        ]:
            if key in metadata:
                flat_record[key] = metadata[key]

        # Merge the analyses list from metadata with what we found.
        meta_analyses = metadata.get('analyses', [])
        if isinstance(meta_analyses, str):
            try:
                meta_analyses = json.loads(meta_analyses)
            except (json.JSONDecodeError, TypeError):
                meta_analyses = []
        all_analyses = sorted(set(meta_analyses) | set(analyses_present))
        flat_record['analyses'] = json.dumps(all_analyses)
        flat_record['results'] = json.dumps(all_results, default=str)

        # Save to the unified algorithm cache (separate from AI caches).
        self.algo_results_cache.set(pdf_hash, flat_record)

        self._algo_stats['parsed'] += 1
        self.logger.info(
            f'Algorithm parsed: {lab_name}, '
            f'{len(analyses_present)} analyses, '
            f'{len(all_results)} results, '
            f'{elapsed:.1f}s, $0.00 '
            f'(method=algorithm, algo={lab_key})'
        )
        return 'algorithm_parsed'

    def _metadata_needs_retry(self, parsed: Dict) -> bool:
        """Check if metadata is missing key fields (likely a cover sheet).

        Returns True if product_name, date_tested, AND producer are
        all empty — a strong signal that page 1 is a cover sheet.
        """
        product_name = parsed.get('product_name', '') or ''
        date_tested = parsed.get('date_tested', '') or ''
        producer = parsed.get('producer', '') or ''
        # Retry if at least 2 of the 3 critical fields are empty.
        missing = sum(1 for v in [product_name, date_tested, producer] if not v.strip())
        return missing >= 2

    def _parse_single_page_coa(
            self,
            pdf_hash: str,
            file_path: str,
            pdf_info: Dict,
            analyses: Optional[List[str]] = None,
        ) -> str:
        """Fast path for single-page COAs.

        Extracts metadata + all results in ONE API call using the
        combined single-page prompt. This reduces 7 API calls to 1
        for simple COAs (e.g., hemp cannabinoid-only reports).

        Returns 'parsed' or 'skipped'.
        """
        # Check if everything is already cached.
        if self.metadata_cache.get(pdf_hash):
            self.logger.info(f'Metadata cached: {pdf_hash[:12]}...')
            metadata = self.metadata_cache.get(pdf_hash)

            # Check if analyses are cached too.
            all_cached = True
            for analysis_name in ANALYSIS_CONFIGS:
                if analyses and analysis_name not in analyses:
                    continue
                cache = self.analysis_caches.get(analysis_name)
                if cache and not cache.get(pdf_hash):
                    all_cached = False
                    break
            if all_cached:
                self.logger.info('All analyses cached for single-page COA.')
                return 'parsed'

        self.logger.info(f'Single-page COA — one-shot parse...')
        start = time.time()

        with tempfile.TemporaryDirectory() as tmpdir:
            images = get_pdf_pages_as_images(
                file_path, page_indexes=[0], output_dir=tmpdir,
            )
            text = extract_pdf_text(file_path) if not images else None
            parsed, cost, in_tok, out_tok = self.ai_client.parse_single_page(
                page_images=images if images else None,
                page_text=text if not images else None,
            )

        if parsed is None:
            self.logger.warning(f'Single-page parse failed: {pdf_hash[:12]}...')
            return 'skipped'

        elapsed = time.time() - start

        # ── Extract metadata from combined response ──────────────
        meta_data = parsed.get('metadata', {})
        if not meta_data:
            # If model returned flat structure, treat the whole
            # response as metadata + results mixed together.
            meta_data = {k: v for k, v in parsed.items()
                         if k not in ANALYSIS_CONFIGS}

        metadata = {
            'pdf_hash': pdf_hash,
            'parsing_method': 'ai',
            'parsing_algorithm': None,
            'parsing_model': self.ai_client.model,
            'parsing_provider': self.ai_client.provider,
            'parsing_time': round(elapsed, 2),
            'parsing_cost': round(cost, 6),
            **meta_data,
        }
        self.metadata_cache.set(pdf_hash, metadata)

        # ── Extract analysis results from combined response ──────
        analysis_count = 0
        for analysis_name in ANALYSIS_CONFIGS:
            if analyses and analysis_name not in analyses:
                continue
            cache = self.analysis_caches.get(analysis_name)
            if not cache:
                continue

            # Look for results under the analysis name key.
            results = parsed.get(analysis_name, [])
            if isinstance(results, dict):
                results = results.get('results', [])
            if not isinstance(results, list):
                results = []

            cache_entry = {
                'pdf_hash': pdf_hash,
                'parsing_method': 'ai',
                'parsing_algorithm': None,
                'parsing_model': self.ai_client.model,
                'parsing_provider': self.ai_client.provider,
                'parsing_time': round(elapsed, 2),
                'parsing_cost': round(cost / max(analysis_count + 1, 1), 6),
                'results': results,
            }
            cache.set(pdf_hash, cache_entry)
            if results:
                analysis_count += 1

        self.cost_tracker.record(
            self.ai_client.provider, self.ai_client.model,
            in_tok, out_tok, cost, 'single_page', pdf_hash,
        )

        product_type = normalize_product_type(
            meta_data.get('product_type', '')
        )
        self.logger.info(
            f'Single-page parsed: {product_type}, '
            f'{analysis_count} analyses with data, '
            f'${cost:.4f}, {round(elapsed)}s'
        )
        return 'parsed'

    def get_cache_stats(self) -> Dict:
        """Get statistics about the current cache state."""
        stats = {
            'state': self.state,
            'method': self.method,
            'model': self.ai_client.model if self.ai_client else 'N/A',
            'provider': self.ai_client.provider if self.ai_client else 'local',
            'algorithm_records': len(self.algo_results_cache.to_df()),
            'metadata': len(self.metadata_cache.to_df()),
            'invalid_pdfs': len(self.invalid_pdf_cache),
        }
        for analysis_name, cache in self.analysis_caches.items():
            df = cache.to_df()
            stats[analysis_name] = len(df)
        return stats


# ╔══════════════════════════════════════════════════════════════════╗
# ║ CLI Entry Point                                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

def main():
    """Command-line interface for COA parsing."""
    parser = argparse.ArgumentParser(
        description='Cannlytics AI COA Parsing Engine',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Parse 100 NY COAs using OpenAI
  python parse_coas.py --state ny --provider openai --max-parses 100

  # Parse CA COAs with Anthropic, $5 budget
  python parse_coas.py --state ca --provider anthropic --budget 5.00

  # Parse using Gemini free tier
  python parse_coas.py --state fl --provider gemini

  # Show cache statistics
  python parse_coas.py --state ny --cache-stats

  # Dry run - show what would be parsed
  python parse_coas.py --state ny --dry-run

Provider Priority: anthropic > openai > gemini > xai
        """,
    )

    # Required arguments.
    parser.add_argument(
        '--state', '-s', type=str, required=True,
        help='State code (e.g., ny, ca, fl)',
    )

    # Provider options.
    parser.add_argument(
        '--provider', '-p', type=str, default='openai',
        choices=['anthropic', 'openai', 'gemini', 'xai'],
        help='AI provider (default: openai)',
    )
    parser.add_argument(
        '--model', '-m', type=str, default=None,
        help='Specific model name (default: provider default)',
    )
    parser.add_argument(
        '--method', type=str, default='auto',
        choices=['auto', 'algorithm', 'ai'],
        help='Parsing method: auto (algorithm-first, AI-fallback), '
             'algorithm (deterministic only), ai (AI only). Default: auto.',
    )

    # Scope options.
    parser.add_argument(
        '--source', type=str, default='',
        help='Filter to specific source (e.g., prr, flowery)',
    )
    parser.add_argument(
        '--max-parses', '-n', type=int, default=None,
        help='Maximum number of COAs to parse',
    )
    parser.add_argument(
        '--sample-size', type=int, default=None,
        help='Random sample size (for dev/testing)',
    )
    parser.add_argument(
        '--budget', '-b', type=float, default=None,
        help='Maximum budget in dollars',
    )
    parser.add_argument(
        '--analyses', type=str, nargs='+', default=None,
        help='Specific analyses to parse (e.g., cannabinoids terpenes)',
    )

    # Path options.
    parser.add_argument(
        '--data-dir', type=str, default=None,
        help=f'Data directory (default: {DEFAULT_DATA_DIR})',
    )
    parser.add_argument(
        '--cache-dir', type=str, default=None,
        help=f'Cache directory (default: {DEFAULT_CACHE_DIR})',
    )

    # Utility commands.
    parser.add_argument(
        '--cache-stats', action='store_true',
        help='Show cache statistics and exit',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Show what would be parsed without parsing',
    )
    parser.add_argument(
        '--clear-cache', action='store_true',
        help='Delete all cache files for this state/model and exit',
    )
    parser.add_argument(
        '--triage', action='store_true',
        help='Scan corpus for invalid PDFs, report stats, and exit. '
             'Populates the invalid PDF cache for future runs.',
    )

    args = parser.parse_args()

    # Initialize parser.
    coa_parser = COAParser(
        state=args.state,
        provider=args.provider,
        model=args.model,
        method=args.method,
        data_dir=Path(args.data_dir) if args.data_dir else None,
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        budget=args.budget,
        max_parses=args.max_parses,
    )

    # Handle utility commands.
    if args.cache_stats:
        stats = coa_parser.get_cache_stats()
        print('\n=== Cache Statistics ===')
        for k, v in stats.items():
            print(f'  {k}: {v:,}' if isinstance(v, int) else f'  {k}: {v}')
        return

    if args.dry_run:
        pdfs = coa_parser.discover_pdfs(args.source)
        print(f'\nDry Run: {len(pdfs):,} PDFs discovered')
        if not pdfs.empty:
            print(f'  Directory: {coa_parser.pdf_dir}')
            print(f'  Method: {args.method}')
            if coa_parser.ai_client:
                print(f'  Provider: {args.provider} ({coa_parser.ai_client.model})')
            if args.sample_size:
                print(f'  Sample size: {args.sample_size}')
            if args.max_parses:
                print(f'  Max parses: {args.max_parses}')
            if args.budget:
                print(f'  Budget: ${args.budget:.2f}')

            # Show what's already cached.
            stats = coa_parser.get_cache_stats()
            cached = stats.get('metadata', 0)
            invalid = stats.get('invalid_pdfs', 0)
            remaining = len(pdfs) - cached - invalid
            print(f'  Already cached: {cached:,}')
            print(f'  Known invalid: {invalid:,}')
            print(f'  Remaining: {remaining:,}')
        return

    if args.clear_cache:
        model_tag = 'none'
        if coa_parser.ai_client:
            model_tag = coa_parser.ai_client.model
        print(f'\nClearing cache for {args.state.upper()} / {model_tag}...')
        cache_pattern = f'results-{args.state}-*-{model_tag.replace(".", "_")}*'
        import glob
        cache_files = glob.glob(str(coa_parser.cache_dir / cache_pattern))
        if not cache_files:
            print('  No cache files found.')
        else:
            for cf in cache_files:
                os.remove(cf)
                print(f'  Deleted: {os.path.basename(cf)}')
            print(f'  Cleared {len(cache_files)} cache file(s).')
        return

    if args.triage:
        print(f'\n🔍 PDF Corpus Triage: {args.state.upper()}')
        print(f'   Cache: {coa_parser.invalid_pdf_cache.path}')
        print(f'   Previously cached invalid: {len(coa_parser.invalid_pdf_cache):,}')
        print()

        pdfs = coa_parser.discover_pdfs(args.source)
        if pdfs.empty:
            print('  No PDFs found.')
            return

        total = len(pdfs)
        print(f'  Scanning {total:,} PDFs...')

        valid_count = 0
        invalid_counts = {}  # reason -> count
        already_cached = 0
        newly_invalid = 0

        for i, (_, row) in enumerate(pdfs.iterrows()):
            pdf_hash = row['pdf_hash']
            file_path = row['file_path']

            # Check cache first.
            if pdf_hash in coa_parser.invalid_pdf_cache:
                already_cached += 1
                invalid_counts['cached'] = invalid_counts.get('cached', 0) + 1
                continue

            valid, reason = is_valid_pdf(file_path)
            if valid:
                valid_count += 1
            else:
                coa_parser.invalid_pdf_cache.add(pdf_hash, reason)
                newly_invalid += 1
                invalid_counts[reason] = invalid_counts.get(reason, 0) + 1

            if (i + 1) % 5000 == 0:
                print(
                    f'    [{i + 1:,}/{total:,}] '
                    f'valid={valid_count:,}, '
                    f'invalid={already_cached + newly_invalid:,}'
                )

        total_invalid = already_cached + newly_invalid
        print()
        print(f'  ═══════════════════════════════════════')
        print(f'  TRIAGE RESULTS: {args.state.upper()}')
        print(f'  ═══════════════════════════════════════')
        print(f'  Total PDFs scanned:   {total:,}')
        print(f'  Valid:                {valid_count:,} ({100*valid_count/total:.1f}%)')
        print(f'  Invalid:              {total_invalid:,} ({100*total_invalid/total:.1f}%)')
        if invalid_counts:
            print(f'  --- Breakdown ---')
            for reason, count in sorted(invalid_counts.items(),
                                         key=lambda x: -x[1]):
                print(f'    {reason}: {count:,}')
        print(f'  Newly cached:         {newly_invalid:,}')
        print(f'  Previously cached:    {already_cached:,}')
        print(f'  Total in cache:       {len(coa_parser.invalid_pdf_cache):,}')
        print(f'  ═══════════════════════════════════════')
        return

    # Run parsing.
    print(f'\n🔬 Cannlytics COA Doc — Hybrid Parser')
    print(f'   State: {args.state.upper()}')
    print(f'   Method: {args.method}')
    if args.method != 'algorithm':
        print(f'   AI Provider: {args.provider} ({coa_parser.ai_client.model})')
        if coa_parser.ai_client.use_flex:
            print(f'   Pricing: flex (50% discount)')
    if args.method in ('auto', 'algorithm'):
        active_labs = [k for k, v in LAB_REGISTRY.items() if v.get('tier', 4) <= 3]
        print(f'   Algorithms: {len(active_labs)} labs registered ({", ".join(active_labs)})')
    if args.budget:
        print(f'   Budget: ${args.budget:.2f}')
    if args.max_parses:
        print(f'   Max parses: {args.max_parses:,}')
    if len(coa_parser.invalid_pdf_cache) > 0:
        print(f'   Invalid PDFs cached: {len(coa_parser.invalid_pdf_cache):,} (will be skipped)')
    print()

    summary = coa_parser.parse_all(
        source=args.source,
        sample_size=args.sample_size,
        analyses=args.analyses,
    )

    # Print final summary.
    print('\n' + '=' * 60)
    print('📋 PARSE SUMMARY')
    print('=' * 60)
    print(f'  State: {summary["state"].upper()}')
    print(f'  Method: {summary["method"]}')
    print(f'  Total PDFs: {summary["total_pdfs"]:,}')
    print(f'  Parsed: {summary["parsed"]:,}')
    print(f'  Skipped (cached): {summary["skipped"]:,}')
    print(f'  Errors: {summary["errors"]:,}')
    invalid_run = summary.get('invalid_pdfs', 0)
    invalid_total = summary.get('invalid_pdfs_total', 0)
    if invalid_run > 0 or invalid_total > 0:
        print(f'  Invalid PDFs: {invalid_run:,} (this run), {invalid_total:,} (total cached)')
    algo = summary.get('algorithm_stats', {})
    if algo.get('identified', 0) > 0 or algo.get('parsed', 0) > 0:
        print(f'  --- Algorithm Stats ---')
        print(f'  Labs identified: {algo.get("identified", 0):,}')
        print(f'  Algorithm parsed: {algo.get("parsed", 0):,}')
        print(f'  Algorithm failed: {algo.get("failed", 0):,}')
        print(f'  AI fallback: {algo.get("ai_fallback", 0):,}')
    costs = summary['costs']
    print(f'  Total AI cost: ${costs["total_cost"]:.4f}')
    print(f'  Total AI calls: {costs["total_calls"]:,}')
    if costs['by_provider']:
        print(f'  By provider: {costs["by_provider"]}')
    if summary.get('flex_enabled'):
        print(f'  Flex processing: enabled (50% discount)')
        if summary.get('flex_failures', 0) > 0:
            print(f'  Flex fallbacks: {summary["flex_failures"]}')
    print('=' * 60)


if __name__ == '__main__':
    main()