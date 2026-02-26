"""
Cannlytics Cannabis Results -- Lab Results URL Extraction & Rating
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2026-02-24
Updated: 2026-02-24
License: MIT

Description:
    Extracts lab_results_url values discovered during the cannabis_licenses
    enrichment pipeline (Stage 2.5e -- get_business_info.py), links them
    back to license metadata, deduplicates, validates, rates confidence,
    flags multi-state operators (MSOs), and produces a prioritized CSV
    ready for manual review and collection algorithm development.

Pipeline Position:
    Pre-Stage 1 of the cannabis_results pipeline.
    Runs AFTER:  cannabis_licenses enrichment (get_business_info.py)
    Runs BEFORE: Manual URL review → collection algorithm development

Input Files:
    - cannabis_licenses cache:  cache/business-info-search.jsonl
    - cannabis_licenses output: output/cannlytics-cannabis-licenses-latest.csv
      (Optional -- used for enriching URLs with license metadata)

Output Files:
    - build/lab-results-urls.csv          -- Full rated URL dataset
    - build/lab-results-urls-summary.json -- Summary statistics

Dependencies:
    - pandas, requests (optional, for liveness checks)

Usage:
    # Extract URLs (no HTTP checks -- fast, offline)
    python get_results_urls.py

    # Extract URLs with HTTP liveness checks (slower, requires internet)
    python get_results_urls.py --check-liveness

    # Specify custom input paths
    python get_results_urls.py \\
        --cache "path/to/business-info-search.jsonl" \\
        --licenses "path/to/cannlytics-cannabis-licenses-latest.csv"

    # Show statistics only (no file output)
    python get_results_urls.py --stats-only

    # Limit liveness checks (e.g., top 100 high-confidence URLs)
    python get_results_urls.py --check-liveness --max-checks 100
"""

# Standard imports
import argparse
import json
import logging
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse, urlunparse

# External imports
import pandas as pd

# =============================================================================
# Configuration
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

# =============================================================================
# Path Resolution: Use results_config.py if available, else sensible defaults.
# =============================================================================
_CONFIG_LOADED = False
try:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from config.results_config import PATHS as RESULTS_PATHS
    _CONFIG_LOADED = True
    logger.info('Using centralized configuration from config/results_config.py')
except ImportError:
    _CONFIG_LOADED = False

if os.name == 'nt':  # Windows
    LICENSES_BASE = Path(r'C:\Users\keega\OneDrive\Cannlytics\huggingface\datasets\cannabis_licenses')
    RESULTS_BASE = Path(r'D:\hugging-face\cannabis_results')
else:  # Unix / macOS / CI
    LICENSES_BASE = Path.home() / 'Cannlytics' / 'huggingface' / 'datasets' / 'cannabis_licenses'
    RESULTS_BASE = Path.home() / 'Cannlytics' / 'huggingface' / 'datasets' / 'cannabis_results'

DEFAULT_PATHS = {
    'cache': str(LICENSES_BASE / 'cache' / 'business-info-search.jsonl'),
    'licenses': str(LICENSES_BASE / 'output' / 'cannlytics-cannabis-licenses-latest.csv'),
    'output_dir': str(RESULTS_BASE / 'build'),
}

# Known cannabis lab results page patterns (boost confidence)
LAB_RESULTS_URL_PATTERNS = [
    r'/lab[_-]?results?',
    r'/coa[s]?',
    r'/certificate[s]?[_-]?of[_-]?analysis',
    r'/test[_-]?results?',
    r'/quality[_-]?assurance',
    r'/lab[_-]?test',
    r'/transparency',
    r'/third[_-]?party[_-]?test',
    r'/batch[_-]?results?',
    r'/product[_-]?test',
    r'/qr/',
    r'/verify',
]

# Known cannabis platforms that host COAs (boost confidence)
KNOWN_COA_PLATFORMS = [
    'confidentcannabis.com',
    'conflabs.com',
    'reports.mcrlabs.com',
    'results.sclabs.com',
    'portal.kaycha.com',
    'greenleaflab.org',
    'cannabisreports.com',
    'terplifelabs.com',
    'cdn.confident',
    'analytics.sclabs.com',
]

# Domains that are almost certainly NOT lab results
BLACKLISTED_DOMAINS = [
    'facebook.com',
    'instagram.com',
    'twitter.com',
    'x.com',
    'linkedin.com',
    'youtube.com',
    'tiktok.com',
    'yelp.com',
    'google.com',
    'weedmaps.com',
    'leafly.com',
    'iheartjane.com',
    'dutchie.com',
    'wikipedia.org',
    'reddit.com',
]

# URLs matching these patterns are AI artifacts, not real URLs — drop entirely
ARTIFACT_URL_PATTERNS = [
    'vertexaisearch.cloud.google.com',
    'grounding-api-redirect',
]

# HTTP request config
HTTP_TIMEOUT = 10  # seconds
HTTP_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
}

# =============================================================================
# URL Extraction from Cache
# =============================================================================

def extract_urls_from_cache(cache_path: str) -> List[Dict[str, Any]]:
    """Extract all lab_results_url entries from the business-info-search cache.

    Reads the JSONL cache line-by-line (memory-efficient for 44 MB files),
    extracts entries with a non-null lab_results_url, and captures associated
    metadata (business_key, provider, confidence, timestamp).

    Returns:
        List of dicts, each containing:
            - url: The lab results URL
            - business_key: The business identifier (NAME|STATE)
            - provider: AI provider that found this URL
            - model: Model used
            - mode: Search mode (website, contact, all, etc.)
            - confidence: Confidence from the AI response
            - source_url: Source URL from AI grounding
            - timestamp: When the search was performed
    """
    records = []
    line_count = 0
    error_count = 0

    logger.info(f'Reading cache: {cache_path}')
    if not os.path.exists(cache_path):
        logger.error(f'Cache file not found: {cache_path}')
        return records

    with open(cache_path, 'r', encoding='utf-8') as f:
        for line in f:
            line_count += 1
            try:
                data = json.loads(line.strip())
                if not isinstance(data, dict) or 'business_key' not in data:
                    continue

                # Skip entries with errors
                if data.get('error'):
                    continue

                result = data.get('result')
                if not result or not isinstance(result, dict):
                    continue

                url = result.get('lab_results_url')
                if not url or not isinstance(url, str):
                    continue

                # Clean the URL
                url = url.strip()
                if not url or url.lower() in ('null', 'none', 'n/a', ''):
                    continue

                records.append({
                    'url': url,
                    'business_key': data.get('business_key', ''),
                    'provider': data.get('provider', ''),
                    'model': data.get('model', ''),
                    'mode': data.get('mode', ''),
                    'ai_confidence': result.get('confidence', ''),
                    'source_url': result.get('source_url', ''),
                    'timestamp': data.get('timestamp', ''),
                    # Also capture business_website for cross-reference
                    'business_website': result.get('business_website', ''),
                })

            except (json.JSONDecodeError, KeyError, TypeError):
                error_count += 1
                continue

    logger.info(
        f'Scanned {line_count:,} cache lines → '
        f'{len(records):,} lab_results_url entries '
        f'({error_count} parse errors)'
    )
    return records


# =============================================================================
# URL Cleaning & Normalization
# =============================================================================

def normalize_url(url: str) -> str:
    """Normalize a URL for consistent deduplication.

    - Strips whitespace and trailing slashes
    - Lowercases the scheme and netloc
    - Removes common tracking parameters
    - Ensures https:// prefix
    """
    url = url.strip().rstrip('/')

    # Add scheme if missing
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    try:
        parsed = urlparse(url)
        # Lowercase scheme and netloc
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower().strip('.')

        # Remove www. prefix for deduplication
        if netloc.startswith('www.'):
            netloc = netloc[4:]

        # Remove common tracking params
        path = parsed.path.rstrip('/')
        # Keep query params as they may be meaningful (e.g., ?product_id=123)
        query = parsed.query

        # Rebuild
        normalized = urlunparse((scheme, netloc, path, parsed.params, query, ''))
        return normalized
    except Exception:
        return url


def is_valid_url(url: str) -> bool:
    """Check if a string is a plausibly valid URL."""
    try:
        parsed = urlparse(url)
        # Must have scheme and netloc
        if not parsed.scheme or not parsed.netloc:
            return False
        # Netloc must have at least one dot (domain.tld)
        if '.' not in parsed.netloc:
            return False
        # No spaces in URL
        if ' ' in url:
            return False
        # Basic TLD check
        tld = parsed.netloc.split('.')[-1].lower()
        if len(tld) < 2 or len(tld) > 10:
            return False
        return True
    except Exception:
        return False


def extract_domain(url: str) -> str:
    """Extract the registrable domain from a URL."""
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if netloc.startswith('www.'):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ''


# =============================================================================
# URL Confidence Rating
# =============================================================================

def rate_url_confidence(
    url: str,
    ai_confidence: str = '',
    domain_frequency: int = 1,
    is_mso: bool = False,
    liveness_status: Optional[int] = None,
) -> Tuple[str, List[str]]:
    """Rate a lab results URL's confidence level.

    Evaluates multiple signals to produce a composite confidence:
        - URL path patterns (does it look like a lab results page?)
        - Known COA platform domains
        - AI-reported confidence from the search
        - Domain appears on a blacklist
        - HTTP liveness check result (if available)
        - Frequency of the domain across multiple businesses

    Returns:
        (confidence_level, reasons) where confidence_level is
        'high', 'medium', or 'low'.
    """
    score = 0
    reasons = []
    domain = extract_domain(url)
    path = urlparse(url).path.lower()

    # --- Positive signals ---

    # URL path matches lab results patterns
    for pattern in LAB_RESULTS_URL_PATTERNS:
        if re.search(pattern, path, re.IGNORECASE):
            score += 3
            reasons.append(f'path_match:{pattern}')
            break  # One match is enough

    # Known COA platform
    for platform in KNOWN_COA_PLATFORMS:
        if platform in domain or platform in url.lower():
            score += 4
            reasons.append(f'known_platform:{platform}')
            break

    # AI reported high confidence
    if ai_confidence == 'high':
        score += 2
        reasons.append('ai_high')
    elif ai_confidence == 'medium':
        score += 1
        reasons.append('ai_medium')

    # Domain appears for multiple businesses (more likely real)
    if domain_frequency >= 5:
        score += 2
        reasons.append(f'domain_freq:{domain_frequency}')
    elif domain_frequency >= 2:
        score += 1
        reasons.append(f'domain_freq:{domain_frequency}')

    # HTTP liveness confirms the page exists
    if liveness_status is not None:
        if 200 <= liveness_status < 400:
            score += 3
            reasons.append(f'http_ok:{liveness_status}')
        elif liveness_status == 403:
            # Age gate or auth — still might be valid
            score += 1
            reasons.append('http_403_auth')
        elif liveness_status == 404:
            score -= 3
            reasons.append('http_404')
        elif liveness_status >= 500:
            score -= 1
            reasons.append(f'http_error:{liveness_status}')

    # --- Negative signals ---

    # Blacklisted domain (social media, aggregators)
    for blocked in BLACKLISTED_DOMAINS:
        if blocked in domain:
            score -= 4
            reasons.append(f'blacklisted:{blocked}')
            break

    # Very short path (just homepage — unlikely to be lab results)
    if path in ('', '/', '/home', '/index.html'):
        score -= 1
        reasons.append('homepage_only')

    # Determine confidence tier
    if score >= 5:
        confidence = 'high'
    elif score >= 2:
        confidence = 'medium'
    else:
        confidence = 'low'

    return confidence, reasons


# =============================================================================
# HTTP Liveness Checks
# =============================================================================

def check_url_liveness(
    url: str,
    timeout: int = HTTP_TIMEOUT,
) -> Dict[str, Any]:
    """Perform a lightweight HTTP HEAD/GET check on a URL.

    Returns:
        Dict with 'status_code', 'redirect_url', 'content_type', 'error'.
    """
    try:
        import requests
    except ImportError:
        return {'status_code': None, 'error': 'requests not installed'}

    result = {
        'status_code': None,
        'redirect_url': None,
        'content_type': None,
        'error': None,
    }

    try:
        # Try HEAD first (faster, less bandwidth)
        resp = requests.head(
            url,
            headers=HTTP_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
        result['status_code'] = resp.status_code
        result['content_type'] = resp.headers.get('Content-Type', '')
        if resp.url != url:
            result['redirect_url'] = resp.url

    except requests.exceptions.SSLError:
        # Try without SSL verification
        try:
            resp = requests.head(
                url,
                headers=HTTP_HEADERS,
                timeout=timeout,
                allow_redirects=True,
                verify=False,
            )
            result['status_code'] = resp.status_code
            result['content_type'] = resp.headers.get('Content-Type', '')
            if resp.url != url:
                result['redirect_url'] = resp.url
        except Exception as e:
            result['error'] = f'ssl_retry_failed:{type(e).__name__}'

    except requests.exceptions.ConnectionError:
        result['error'] = 'connection_error'
    except requests.exceptions.Timeout:
        result['error'] = 'timeout'
    except requests.exceptions.TooManyRedirects:
        result['error'] = 'too_many_redirects'
    except Exception as e:
        result['error'] = f'{type(e).__name__}:{str(e)[:100]}'

    return result


def batch_check_liveness(
    urls: List[str],
    max_checks: Optional[int] = None,
    delay: float = 0.5,
) -> Dict[str, Dict[str, Any]]:
    """Check liveness for a batch of URLs with rate limiting.

    Args:
        urls: List of URLs to check.
        max_checks: Maximum number of checks to perform (None = all).
        delay: Seconds between requests.

    Returns:
        Dict mapping URL → liveness result.
    """
    results = {}
    check_urls = urls[:max_checks] if max_checks else urls
    total = len(check_urls)

    logger.info(f'Checking liveness for {total:,} URLs...')
    for i, url in enumerate(check_urls):
        if (i + 1) % 25 == 0 or i == 0:
            logger.info(f'  [{i + 1}/{total}] Checking: {url[:80]}...')
        results[url] = check_url_liveness(url)
        if delay > 0 and i < total - 1:
            time.sleep(delay)

    # Summarize
    status_counts = Counter()
    for r in results.values():
        if r['status_code']:
            status_counts[r['status_code']] += 1
        elif r['error']:
            status_counts[f'error:{r["error"][:30]}'] += 1

    logger.info(f'Liveness check complete. Status distribution:')
    for status, count in status_counts.most_common(10):
        logger.info(f'  {status}: {count}')

    return results


# =============================================================================
# License Metadata Enrichment
# =============================================================================

def load_license_metadata(
    licenses_path: str,
) -> Optional[pd.DataFrame]:
    """Load license data for enriching URLs with business metadata.

    Returns a DataFrame indexed by business_key with columns useful for
    cross-referencing: license_number, business_brand, license_category,
    premise_state, premise_city, etc.
    """
    if not os.path.exists(licenses_path):
        logger.warning(f'License file not found: {licenses_path}')
        return None

    logger.info(f'Loading license data: {licenses_path}')
    df = pd.read_csv(licenses_path, low_memory=False)
    logger.info(f'  Loaded {len(df):,} license records')

    # Build business_key to match the cache format:
    #   DBA_NAME (or LEGAL_NAME) | STATE
    def make_key(row):
        dba = row.get('business_dba_name')
        legal = row.get('business_legal_name')
        state = row.get('premise_state', '')
        name = dba if pd.notna(dba) and str(dba).strip() else legal
        if pd.isna(name) or not str(name).strip():
            return f"LICENSE:{row.get('license_number', 'UNKNOWN')}"
        name = str(name).upper().strip()
        for suffix in [' LLC', ' INC', ' CORP', ' CO', ' LTD', ' LP', ' LLP']:
            name = name.replace(suffix, '')
        return f'{name}|{state}'

    df['_business_key'] = df.apply(make_key, axis=1)

    # Identify MSOs: brands operating in multiple states
    if 'business_brand' in df.columns:
        brand_states = (
            df.dropna(subset=['business_brand'])
            .groupby('business_brand')['premise_state']
            .nunique()
        )
        mso_brands = set(brand_states[brand_states >= 2].index)
        df['_is_mso'] = df['business_brand'].isin(mso_brands)
        logger.info(f'  Identified {len(mso_brands):,} MSO brands')
    else:
        df['_is_mso'] = False

    # Brand license counts
    if 'business_brand' in df.columns:
        brand_counts = df['business_brand'].value_counts()
        df['_brand_license_count'] = df['business_brand'].map(brand_counts).fillna(0).astype(int)
    else:
        df['_brand_license_count'] = 0

    return df


def enrich_with_license_data(
    url_records: List[Dict[str, Any]],
    licenses_df: Optional[pd.DataFrame],
) -> List[Dict[str, Any]]:
    """Enrich URL records with license metadata where possible.

    Matches on business_key (NAME|STATE) to pull in:
        - license_number (first match)
        - business_brand
        - license_category
        - premise_state, premise_city
        - is_mso flag
        - brand_license_count
    """
    if licenses_df is None:
        logger.info('No license data available for enrichment')
        for rec in url_records:
            # Parse state from business_key
            bk = rec.get('business_key', '')
            if '|' in bk:
                parts = bk.split('|', 1)
                rec['business_name'] = parts[0]
                rec['premise_state'] = parts[1]
            else:
                rec['business_name'] = bk
                rec['premise_state'] = ''
            rec['license_number'] = ''
            rec['business_brand'] = ''
            rec['license_category'] = ''
            rec['premise_city'] = ''
            rec['is_mso'] = False
            rec['brand_license_count'] = 0
        return url_records

    # Build a lookup: business_key → first matching row
    key_lookup = {}
    for _, row in licenses_df.iterrows():
        bk = row['_business_key']
        if bk not in key_lookup:
            key_lookup[bk] = row

    matched = 0
    for rec in url_records:
        bk = rec.get('business_key', '')
        row = key_lookup.get(bk)

        # Parse name and state from business_key regardless
        if '|' in bk:
            parts = bk.split('|', 1)
            rec['business_name'] = parts[0]
            rec['premise_state'] = parts[1]
        else:
            rec['business_name'] = bk
            rec['premise_state'] = ''

        if row is not None:
            matched += 1
            rec['license_number'] = str(row.get('license_number', '')) if pd.notna(row.get('license_number')) else ''
            rec['business_brand'] = str(row.get('business_brand', '')) if pd.notna(row.get('business_brand')) else ''
            rec['license_category'] = str(row.get('license_category', '')) if pd.notna(row.get('license_category')) else ''
            rec['premise_city'] = str(row.get('premise_city', '')) if pd.notna(row.get('premise_city')) else ''
            rec['is_mso'] = bool(row.get('_is_mso', False))
            rec['brand_license_count'] = int(row.get('_brand_license_count', 0))
            if not rec['premise_state']:
                rec['premise_state'] = str(row.get('premise_state', ''))
        else:
            rec['license_number'] = ''
            rec['business_brand'] = ''
            rec['license_category'] = ''
            rec['premise_city'] = ''
            rec['is_mso'] = False
            rec['brand_license_count'] = 0

    logger.info(
        f'  Enriched {matched:,} / {len(url_records):,} URL records '
        f'with license metadata'
    )
    return url_records


# =============================================================================
# Main Pipeline
# =============================================================================

def build_url_dataset(
    cache_path: str,
    licenses_path: str,
    check_liveness: bool = False,
    max_checks: Optional[int] = None,
    liveness_delay: float = 0.5,
) -> pd.DataFrame:
    """Full pipeline: extract → clean → deduplicate → enrich → rate → sort.

    Returns a DataFrame of unique, rated lab results URLs.
    """
    start_time = datetime.now()

    # Step 1: Extract from cache
    raw_records = extract_urls_from_cache(cache_path)
    if not raw_records:
        logger.warning('No lab_results_url entries found in cache')
        return pd.DataFrame()

    # Step 2: Clean & normalize URLs, filter invalid
    valid_records = []
    invalid_count = 0
    artifact_count = 0
    for rec in raw_records:
        normalized = normalize_url(rec['url'])

        # Filter out AI grounding artifacts (Gemini redirect URLs)
        is_artifact = any(p in normalized.lower() for p in ARTIFACT_URL_PATTERNS)
        if is_artifact:
            artifact_count += 1
            continue

        if is_valid_url(normalized):
            rec['url_normalized'] = normalized
            rec['domain'] = extract_domain(normalized)
            valid_records.append(rec)
        else:
            invalid_count += 1

    logger.info(
        f'Validated URLs: {len(valid_records):,} valid, '
        f'{invalid_count} invalid, {artifact_count} AI artifacts removed'
    )

    # Step 3: Deduplicate by normalized URL
    # Keep the record with the highest AI confidence for each unique URL.
    confidence_rank = {'high': 3, 'medium': 2, 'low': 1, '': 0}
    seen = {}  # url_normalized → best record
    for rec in valid_records:
        key = rec['url_normalized']
        existing = seen.get(key)
        if existing is None:
            rec['_duplicate_count'] = 1
            seen[key] = rec
        else:
            existing['_duplicate_count'] = existing.get('_duplicate_count', 1) + 1
            # Keep higher-confidence record
            if confidence_rank.get(rec.get('ai_confidence', ''), 0) > \
               confidence_rank.get(existing.get('ai_confidence', ''), 0):
                rec['_duplicate_count'] = existing['_duplicate_count']
                seen[key] = rec

    deduped = list(seen.values())
    logger.info(
        f'Deduplicated: {len(valid_records):,} → {len(deduped):,} unique URLs'
    )

    # Step 4: Compute domain frequency (how many businesses share a domain)
    domain_freq = Counter(rec['domain'] for rec in deduped)

    # Step 5: Load and enrich with license metadata
    licenses_df = load_license_metadata(licenses_path)
    deduped = enrich_with_license_data(deduped, licenses_df)

    # Step 6: Optional HTTP liveness checks
    liveness_results = {}
    if check_liveness:
        # Sort by AI confidence (high first) for prioritized checking
        sorted_for_check = sorted(
            deduped,
            key=lambda r: confidence_rank.get(r.get('ai_confidence', ''), 0),
            reverse=True,
        )
        urls_to_check = [r['url_normalized'] for r in sorted_for_check]
        liveness_results = batch_check_liveness(
            urls_to_check,
            max_checks=max_checks,
            delay=liveness_delay,
        )

    # Step 7: Rate confidence for each URL
    for rec in deduped:
        url = rec['url_normalized']
        liveness_status = None
        liveness_info = liveness_results.get(url)
        if liveness_info:
            liveness_status = liveness_info.get('status_code')
            rec['http_status'] = liveness_info.get('status_code')
            rec['http_redirect'] = liveness_info.get('redirect_url', '')
            rec['http_error'] = liveness_info.get('error', '')
        else:
            rec['http_status'] = None
            rec['http_redirect'] = ''
            rec['http_error'] = ''

        confidence, reasons = rate_url_confidence(
            url=url,
            ai_confidence=rec.get('ai_confidence', ''),
            domain_frequency=domain_freq.get(rec['domain'], 1),
            is_mso=rec.get('is_mso', False),
            liveness_status=liveness_status,
        )
        rec['confidence'] = confidence
        rec['confidence_reasons'] = '; '.join(reasons)

    # Step 8: Build DataFrame and sort
    df = pd.DataFrame(deduped)

    # Select and order final columns
    output_columns = [
        'confidence',
        'url_normalized',
        'domain',
        'business_name',
        'business_brand',
        'premise_state',
        'premise_city',
        'license_number',
        'license_category',
        'is_mso',
        'brand_license_count',
        'ai_confidence',
        'confidence_reasons',
        'http_status',
        'http_redirect',
        'http_error',
        'provider',
        'model',
        'source_url',
        'business_website',
        'timestamp',
        '_duplicate_count',
    ]
    # Only include columns that exist
    output_columns = [c for c in output_columns if c in df.columns]
    df = df[output_columns]

    # Sort: high confidence first, then MSOs, then brand_license_count
    confidence_sort = {'high': 0, 'medium': 1, 'low': 2}
    df['_confidence_sort'] = df['confidence'].map(confidence_sort).fillna(3)
    df['_mso_sort'] = (~df['is_mso']).astype(int) if 'is_mso' in df.columns else 0
    df = df.sort_values(
        ['_confidence_sort', '_mso_sort', 'brand_license_count', 'domain'],
        ascending=[True, True, False, True],
    ).reset_index(drop=True)
    df = df.drop(columns=['_confidence_sort', '_mso_sort'], errors='ignore')

    elapsed = datetime.now() - start_time
    logger.info(f'Pipeline complete in {elapsed}. Total URLs: {len(df):,}')

    return df


def generate_summary(df: pd.DataFrame) -> Dict[str, Any]:
    """Generate summary statistics for the URL dataset."""
    if df.empty:
        return {'total_urls': 0}

    summary = {
        'generated_at': datetime.now().isoformat(),
        'total_urls': len(df),
        'by_confidence': df['confidence'].value_counts().to_dict(),
        'by_state': (
            df['premise_state'].value_counts().head(20).to_dict()
            if 'premise_state' in df.columns else {}
        ),
        'top_domains': (
            df['domain'].value_counts().head(20).to_dict()
        ),
        'mso_urls': int(df['is_mso'].sum()) if 'is_mso' in df.columns else 0,
        'unique_domains': int(df['domain'].nunique()),
        'unique_states': (
            int(df['premise_state'].nunique())
            if 'premise_state' in df.columns else 0
        ),
        'by_license_category': (
            df['license_category'].value_counts().to_dict()
            if 'license_category' in df.columns else {}
        ),
        'by_provider': (
            df['provider'].value_counts().to_dict()
            if 'provider' in df.columns else {}
        ),
    }

    # Liveness stats if available
    if 'http_status' in df.columns:
        checked = df[df['http_status'].notna()]
        if len(checked) > 0:
            summary['liveness_checked'] = len(checked)
            summary['liveness_ok'] = int(
                ((checked['http_status'] >= 200) & (checked['http_status'] < 400)).sum()
            )
            summary['liveness_not_found'] = int((checked['http_status'] == 404).sum())
            summary['liveness_error'] = int((checked['http_status'] >= 500).sum())

    return summary


def print_summary(summary: Dict[str, Any]):
    """Pretty-print the summary to console."""
    print('\n' + '=' * 70)
    print('LAB RESULTS URL EXTRACTION — SUMMARY')
    print('=' * 70)
    print(f'Total unique URLs:  {summary["total_urls"]:,}')
    print(f'Unique domains:     {summary.get("unique_domains", "?")}')
    print(f'Unique states:      {summary.get("unique_states", "?")}')
    print(f'MSO URLs:           {summary.get("mso_urls", "?")}')

    print('\nBy Confidence:')
    for level, count in sorted(
        summary.get('by_confidence', {}).items(),
        key=lambda x: {'high': 0, 'medium': 1, 'low': 2}.get(x[0], 3),
    ):
        bar = '█' * min(count, 50)
        print(f'  {level:>8}: {count:>5,}  {bar}')

    print('\nTop 10 States:')
    for state, count in list(summary.get('by_state', {}).items())[:10]:
        print(f'  {state:>4}: {count:>5,}')

    print('\nTop 10 Domains:')
    for domain, count in list(summary.get('top_domains', {}).items())[:10]:
        print(f'  {domain:>40}: {count:>4,}')

    if 'liveness_checked' in summary:
        print('\nHTTP Liveness:')
        print(f'  Checked:    {summary["liveness_checked"]:,}')
        print(f'  OK (2xx):   {summary["liveness_ok"]:,}')
        print(f'  Not Found:  {summary["liveness_not_found"]:,}')
        print(f'  Errors:     {summary["liveness_error"]:,}')

    print('\nBy License Category:')
    for cat, count in summary.get('by_license_category', {}).items():
        if cat and count:
            print(f'  {cat:>30}: {count:>5,}')

    print('=' * 70)


# =============================================================================
# CLI Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            'Extract, clean, rate, and save lab results URLs from the '
            'cannabis_licenses enrichment cache for use in the '
            'cannabis_results collection pipeline.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic extraction (fast, no HTTP checks)
  python get_results_urls.py

  # With liveness checks (slower)
  python get_results_urls.py --check-liveness --max-checks 200

  # Custom paths
  python get_results_urls.py \\
      --cache /path/to/business-info-search.jsonl \\
      --licenses /path/to/cannlytics-cannabis-licenses-latest.csv \\
      --output-dir /path/to/cannabis_results/build

  # Stats only (no output files)
  python get_results_urls.py --stats-only
        """,
    )

    parser.add_argument(
        '--cache', '-c',
        type=str,
        default=DEFAULT_PATHS['cache'],
        help=f'Path to business-info-search.jsonl (default: {DEFAULT_PATHS["cache"]})',
    )
    parser.add_argument(
        '--licenses', '-l',
        type=str,
        default=DEFAULT_PATHS['licenses'],
        help=f'Path to cannabis licenses CSV (default: {DEFAULT_PATHS["licenses"]})',
    )
    parser.add_argument(
        '--output-dir', '-o',
        type=str,
        default=DEFAULT_PATHS['output_dir'],
        help=f'Output directory for results (default: {DEFAULT_PATHS["output_dir"]})',
    )
    parser.add_argument(
        '--check-liveness',
        action='store_true',
        help='Perform HTTP HEAD checks on URLs (slower but more accurate)',
    )
    parser.add_argument(
        '--max-checks',
        type=int,
        default=None,
        help='Maximum number of liveness checks to perform',
    )
    parser.add_argument(
        '--liveness-delay',
        type=float,
        default=0.5,
        help='Delay between HTTP checks in seconds (default: 0.5)',
    )
    parser.add_argument(
        '--stats-only',
        action='store_true',
        help='Print statistics only, do not write output files',
    )
    parser.add_argument(
        '--min-confidence',
        type=str,
        choices=['high', 'medium', 'low'],
        default=None,
        help='Filter output to URLs at or above this confidence level',
    )

    args = parser.parse_args()

    # Run the pipeline
    logger.info('=' * 70)
    logger.info('Cannlytics — Lab Results URL Extraction')
    logger.info('=' * 70)

    df = build_url_dataset(
        cache_path=args.cache,
        licenses_path=args.licenses,
        check_liveness=args.check_liveness,
        max_checks=args.max_checks,
        liveness_delay=args.liveness_delay,
    )

    if df.empty:
        logger.warning('No URLs extracted. Check your cache path.')
        sys.exit(1)

    # Apply confidence filter if requested
    if args.min_confidence:
        confidence_order = {'high': 0, 'medium': 1, 'low': 2}
        threshold = confidence_order[args.min_confidence]
        df = df[df['confidence'].map(confidence_order) <= threshold]
        logger.info(f'Filtered to {args.min_confidence}+ confidence: {len(df):,} URLs')

    # Generate summary
    summary = generate_summary(df)
    print_summary(summary)

    # Save outputs
    if not args.stats_only:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save CSV
        csv_path = output_dir / 'lab-results-urls.csv'
        df.to_csv(csv_path, index=False)
        logger.info(f'Saved URL dataset: {csv_path}')

        # Save summary JSON
        json_path = output_dir / 'lab-results-urls-summary.json'
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, default=str)
        logger.info(f'Saved summary: {json_path}')

        # Save a high-confidence-only subset for quick review
        high_df = df[df['confidence'] == 'high']
        if len(high_df) > 0:
            high_path = output_dir / 'lab-results-urls-high-confidence.csv'
            high_df.to_csv(high_path, index=False)
            logger.info(f'Saved high-confidence subset ({len(high_df):,} URLs): {high_path}')

        print(f'\n📁 Output files saved to: {output_dir}')
        print(f'   • lab-results-urls.csv              ({len(df):,} URLs)')
        print(f'   • lab-results-urls-summary.json')
        if len(high_df) > 0:
            print(f'   • lab-results-urls-high-confidence.csv ({len(high_df):,} URLs)')

    print('\n✅ Done. Next step: review high-confidence URLs and begin')
    print('   implementing collection algorithms for promising sources.')


if __name__ == '__main__':
    main()