"""
Get Results | Florida | Kaycha Labs
Copyright (c) 2023-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 5/18/2023
Updated: 2/22/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Collect cannabis lab result COA PDFs from Kaycha Labs (yourcoa.com)
    for Florida MMTC licensees.

    Kaycha Labs publishes COA results through their yourcoa.com portal,
    organized by company/producer. This collector:

    1. Catalogs the existing local archive (~26K PDFs, 22 licensee
       folders, ~16.7 GB) into a searchable manifest.
    2. Scrapes the yourcoa.com company search pages to discover COA
       URLs for each Florida MMTC licensee.
    3. Downloads only NEW COA PDFs not already in the local archive,
       with respectful rate limiting and exponential backoff.
    4. Converts the combined manifest into standardized LabResult records.

    The collector preserves the per-licensee directory structure:
        kaycha/MMTC-2015-0001/sample_id.pdf
        kaycha/MMTC-2015-0002/sample_id.pdf
        ...

Data Sources:
    - [Kaycha Labs Portal](https://yourcoa.com)
    - Company search: https://yourcoa.com/company/company?t={slug}&page={n}
    - COA download: https://yourcoa.com/coa/download?sample={sample_id}
    - COA view: https://yourcoa.com/coa/coa-download/{sample_id}

Output:
    - Per-licensee PDF directories with COA files
    - Manifest CSV cataloging all collected COAs
    - Standardized CSV with LabResult schema fields

Usage:
    ```python
    from algorithms.get_results_fl_kaycha import KaychaLabsCollector

    with KaychaLabsCollector() as collector:
        results = collector.get_results()
    ```

Command Line:
    ```bash
    # Full collection: catalog existing + scrape new + produce results
    python algorithms/get_results_fl_kaycha.py

    # Catalog-only mode (no network requests)
    python algorithms/get_results_fl_kaycha.py --catalog-only

    # Collect for a single licensee
    python algorithms/get_results_fl_kaycha.py --license MMTC-2015-0005

    # Run unit tests
    python algorithms/get_results_fl_kaycha.py --test

    # Run integration test (network required)
    python algorithms/get_results_fl_kaycha.py --integration-test
    ```
"""
# Standard imports:
from datetime import datetime
import hashlib
import os
import random
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# External imports:
import pandas as pd
import requests
from bs4 import BeautifulSoup

# Internal imports:
try:
    from config.results_config import PATHS, SOURCE_CONFIG
    from config.results_schema import LabResult, normalize_product_type
    from results_base import COACollector
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from config.results_config import PATHS, SOURCE_CONFIG
        from config.results_schema import LabResult, normalize_product_type
        from results_base import COACollector
    except ImportError:
        PATHS = None
        SOURCE_CONFIG = {}
        LabResult = None
        normalize_product_type = None
        COACollector = None


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Constants                                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

BASE_URL = 'https://yourcoa.com'
COMPANY_SEARCH_URL = BASE_URL + '/company/company'
COA_DOWNLOAD_URL = BASE_URL + '/coa/download'
COA_VIEW_URL = BASE_URL + '/coa/coa-download'

# Minimum valid PDF size (bytes). Files smaller than this are likely
# HTML error pages or empty responses, not real COA PDFs.
MIN_PDF_SIZE = 21 * 1024  # 21 KB

# Kaycha Labs facility metadata (Miramar, FL location).
KAYCHA_LAB = {
    'lab': 'Kaycha Labs',
    'lab_address': '3451 Commerce Parkway, Miramar, FL 33025',
    'lab_city': 'Miramar',
    'lab_county': 'Broward',
    'lab_state': 'fl',
    'lab_zipcode': '33025',
    'lab_latitude': 25.9765,
    'lab_longitude': -80.2328,
    'lab_phone': '(954) 368-7664',
    'lab_website': 'https://www.kaychalabs.com',
}

# Default HTTP headers — polite identification.
DEFAULT_HEADERS = {
    'User-Agent': 'Cannlytics/1.0 (Cannabis Data Research; +https://cannlytics.com)',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}

# Florida MMTC licensees and their yourcoa.com search slugs.
# Licenses with empty slugs have no public Kaycha COA page.
FLORIDA_LICENSES = {
    'MMTC-2015-0001': {'dba': 'Curaleaf', 'slug': 'CURALEAF+FLORIDA+LLC'},
    'MMTC-2015-0002': {'dba': 'Ayr Cannabis Dispensary', 'slug': 'Liberty+Health+Sciences%2C+FL'},
    'MMTC-2015-0003': {'dba': 'Fluent', 'slug': 'Fluent'},
    'MMTC-2015-0004': {'dba': 'Surterra Wellness', 'slug': 'Surterra+Wellness'},
    'MMTC-2015-0005': {'dba': 'Trulieve', 'slug': 'Trulieve'},
    'MMTC-2016-0006': {'dba': 'Planet 13 Florida, Inc.', 'slug': ''},
    'MMTC-2016-0007': {'dba': 'GrowHealthy', 'slug': 'GrowHealthy'},
    'MMTC-2017-0008': {'dba': 'Sunnyside*', 'slug': 'Sunnyside'},
    'MMTC-2017-0009': {'dba': 'VidaCann', 'slug': 'VidaCann'},
    'MMTC-2017-0010': {'dba': 'MüV', 'slug': 'Altmed+Florida'},
    'MMTC-2017-0011': {'dba': 'Cannabist', 'slug': 'Cannabist'},
    'MMTC-2017-0012': {'dba': 'Sunburn', 'slug': ''},
    'MMTC-2017-0013': {'dba': 'GTI (Rise Dispensaries)', 'slug': 'GTI'},
    'MMTC-2018-0014': {'dba': 'House of Platinum Cannabis', 'slug': ''},
    'MMTC-2019-0015': {'dba': 'Jungle Boys', 'slug': 'Jungle+Boys'},
    'MMTC-2019-0016': {'dba': 'Insa - Cannabis for Real Life', 'slug': 'Insa'},
    'MMTC-2019-0017': {'dba': 'Sanctuary Cannabis', 'slug': 'Sanctuary'},
    'MMTC-2019-0018': {'dba': 'Cookies Florida, Inc.', 'slug': ''},
    'MMTC-2019-0019': {'dba': 'Gold Leaf', 'slug': 'Gold+Leaf'},
    'MMTC-2019-0020': {'dba': 'The Flowery', 'slug': 'The+Flowery'},
    'MMTC-2019-0021': {'dba': 'Green Dragon', 'slug': 'Green+Dragon'},
    'MMTC-2019-0022': {'dba': 'Revolution Florida', 'slug': 'Revolution'},
}

# Manifest column names.
MANIFEST_COLUMNS = [
    'file_name',
    'sample_id',
    'license_number',
    'dba',
    'file_path',
    'file_size',
    'download_url',
    'date_cataloged',
    'source',
]

# HTML parse columns from yourcoa.com pdf_box divs.
PAGE_COLUMNS = ['lab_id', 'batch_number', 'product_name']


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Static Helpers                                                    ║
# ╚══════════════════════════════════════════════════════════════════╝

def _generate_result_id(sample_id: str, license_number: str = '') -> str:
    """Generate a deterministic 16-char hex ID from sample + license.

    Args:
        sample_id: The COA sample identifier.
        license_number: The MMTC license number.

    Returns:
        A 16-character hexadecimal string.
    """
    data = f'{license_number}:{sample_id}'.encode('utf-8')
    return hashlib.sha256(data).hexdigest()[:16]


def _extract_sample_id_from_url(url: str) -> str:
    """Extract the sample ID from a Kaycha download URL.

    Examples:
        '/coa/coa-download/DA50313006-006' → 'DA50313006-006'
        'https://yourcoa.com/coa/download?sample=95746' → '95746'
        '/coa/coa-download/95746?wl_id=0&mrk=1' → '95746'

    Args:
        url: A Kaycha COA URL.

    Returns:
        The sample ID string.
    """
    if not url:
        return ''
    # Handle query-string style: ?sample=XXXX
    if 'sample=' in url:
        match = re.search(r'sample=([^&]+)', url)
        return match.group(1) if match else ''
    # Handle path-style: /coa-download/XXXX or /coa/coa-download/XXXX
    path = url.split('?')[0].split('#')[0]
    parts = path.rstrip('/').split('/')
    return parts[-1] if parts else ''


def _extract_sample_id_from_filename(file_name: str) -> str:
    """Extract sample ID from a COA PDF filename.

    Args:
        file_name: The PDF filename (e.g., 'DA50313006-006.pdf').

    Returns:
        The sample ID (filename without .pdf extension).
    """
    if not file_name:
        return ''
    name = file_name.strip()
    name = re.sub(r'\.pdf$', '', name, flags=re.IGNORECASE)
    return name.strip()


def _is_valid_pdf(filepath: str, min_size: int = MIN_PDF_SIZE) -> bool:
    """Check if a file is a valid PDF of sufficient size.

    Args:
        filepath: Path to the file.
        min_size: Minimum acceptable file size in bytes.

    Returns:
        True if the file is a valid PDF above the minimum size.
    """
    try:
        size = os.path.getsize(filepath)
        if size < min_size:
            return False
        with open(filepath, 'rb') as f:
            header = f.read(5)
        return header.startswith(b'%PDF-')
    except (OSError, IOError):
        return False


def _find_pdf_files(search_dir: str) -> List[str]:
    """Recursively find PDF files in a directory.

    Args:
        search_dir: Directory to search.

    Returns:
        Sorted list of absolute paths to PDF files.
    """
    pdfs = []
    for root, dirs, files in os.walk(search_dir):
        for f in files:
            if f.lower().endswith('.pdf'):
                pdfs.append(os.path.join(root, f))
    return sorted(pdfs)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Collector                                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

class KaychaLabsCollector(COACollector if COACollector else object):
    """Collector for Kaycha Labs COA PDFs.

    Manages collection and cataloging of COA PDFs from yourcoa.com
    for Florida MMTC licensees. Preserves the per-licensee directory
    structure and supports efficient incremental collection.

    Attributes:
        state: Always 'fl' for Florida.
        source: Always 'kaycha_labs'.
        producers: Dict mapping license numbers to metadata/slugs.
        manifest_path: Path to the manifest CSV.

    Example:
        ```python
        with KaychaLabsCollector() as collector:
            # Full pipeline: catalog + scrape + download + convert
            results = collector.get_results()

            # Or just catalog existing archive
            manifest = collector.catalog_existing()
        ```
    """

    def __init__(
            self,
            data_dir: Optional[str] = None,
            pdf_dir: Optional[str] = None,
            cache_path: Optional[str] = None,
            log_dir: Optional[str] = None,
            log_name: Optional[str] = None,
            pause_time: Optional[float] = None,
            verbose: bool = True,
            producers: Optional[Dict[str, Dict]] = None,
        ):
        """Initialize the Kaycha Labs collector.

        Args:
            data_dir: Override for data directory.
            pdf_dir: Override for PDF storage directory (root kaycha/ dir).
            cache_path: Override for cache file path.
            log_dir: Override for log directory.
            log_name: Override for log file name.
            pause_time: Seconds between requests (default: 5.0).
            verbose: Enable verbose logging.
            producers: Override the Florida licensee mapping.
        """
        source_config = SOURCE_CONFIG.get('kaycha_labs', {}) if SOURCE_CONFIG else {}
        default_pause = source_config.get('pause_time', 5.0)

        if COACollector is not None:
            super().__init__(
                state='fl',
                source='kaycha_labs',
                data_dir=data_dir,
                pdf_dir=pdf_dir,
                cache_path=cache_path,
                log_dir=log_dir,
                log_name=log_name or 'get_results_fl_kaycha',
                pause_time=pause_time or default_pause,
                verbose=verbose,
            )
        else:
            self.data_dir = Path(data_dir) if data_dir else Path('.')
            self.pdf_dir = Path(pdf_dir) if pdf_dir else self.data_dir / 'pdfs' / 'kaycha'
            self.datasets_dir = self.data_dir / 'datasets'
            self.verbose = verbose
            self.pause_time = pause_time or default_pause
            os.makedirs(self.pdf_dir, exist_ok=True)
            os.makedirs(self.datasets_dir, exist_ok=True)

            import logging
            self.logger = logging.getLogger(log_name or 'get_results_fl_kaycha')
            if not self.logger.handlers:
                handler = logging.StreamHandler()
                handler.setFormatter(logging.Formatter(
                    '%(asctime)s | %(name)s | %(levelname)s | %(message)s'
                ))
                self.logger.addHandler(handler)
                self.logger.setLevel(logging.INFO if verbose else logging.WARNING)

        self.producers = producers or FLORIDA_LICENSES
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

        datasets_dir = getattr(self, 'datasets_dir', self.data_dir)
        self.manifest_path = os.path.join(str(datasets_dir), 'kaycha-manifest.csv')

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.session.close()
        return False

    # ── Rate Limiting ────────────────────────────────────────────

    def _respectful_pause(self, multiplier: float = 1.0):
        """Pause with jitter to be respectful to the server.

        Adds 0-30% random jitter to the base pause time to avoid
        creating predictable request patterns.

        Args:
            multiplier: Multiply the pause time (e.g., 2.0 for downloads).
        """
        base = self.pause_time * multiplier
        jitter = base * random.uniform(0.0, 0.3)
        time.sleep(base + jitter)

    def _backoff_pause(self, attempt: int, base: float = 5.0, max_wait: float = 120.0):
        """Exponential backoff pause for retries.

        Args:
            attempt: The retry attempt number (0-indexed).
            base: Base wait time in seconds.
            max_wait: Maximum wait time in seconds.
        """
        wait = min(base * (2 ** attempt), max_wait)
        jitter = wait * random.uniform(0.0, 0.2)
        self.logger.info(f'Backoff: waiting {wait + jitter:.1f}s (attempt {attempt + 1})')
        time.sleep(wait + jitter)

    # ── Archive Cataloging ───────────────────────────────────────

    def catalog_existing(self, incremental: bool = True) -> pd.DataFrame:
        """Build or update a manifest of all COA PDFs in the archive.

        Scans each per-licensee subdirectory under pdf_dir and records
        metadata for each PDF. Respects the existing directory structure:
            kaycha/MMTC-2015-0001/*.pdf
            kaycha/MMTC-2015-0002/*.pdf
            ...

        Args:
            incremental: If True, only catalog files not already in
                the manifest.

        Returns:
            DataFrame with manifest columns.
        """
        pdf_dir = Path(str(self.pdf_dir))
        if not pdf_dir.exists():
            self.logger.warning(f'PDF directory not found: {pdf_dir}')
            return pd.DataFrame(columns=MANIFEST_COLUMNS)

        # Load existing manifest if incremental.
        existing_manifest = pd.DataFrame(columns=MANIFEST_COLUMNS)
        if incremental and os.path.exists(self.manifest_path):
            try:
                existing_manifest = pd.read_csv(self.manifest_path)
                self.logger.info(f'Loaded existing manifest: {len(existing_manifest)} entries')
            except Exception as e:
                self.logger.warning(f'Failed to load manifest: {e}')

        known_paths = set(existing_manifest['file_path'].tolist()) \
            if not existing_manifest.empty and 'file_path' in existing_manifest.columns else set()

        new_entries = []
        now = datetime.now().isoformat()

        # Scan each licensee subdirectory.
        for license_number, meta in self.producers.items():
            license_dir = pdf_dir / license_number
            if not license_dir.exists():
                continue

            dba = meta.get('dba', meta.get('business_dba_name', ''))
            pdfs = _find_pdf_files(str(license_dir))

            for pdf_path in pdfs:
                if pdf_path in known_paths:
                    continue

                file_name = os.path.basename(pdf_path)
                sample_id = _extract_sample_id_from_filename(file_name)
                file_size = os.path.getsize(pdf_path)
                download_url = f'{COA_VIEW_URL}/{sample_id}' if sample_id else ''

                new_entries.append({
                    'file_name': file_name,
                    'sample_id': sample_id,
                    'license_number': license_number,
                    'dba': dba,
                    'file_path': pdf_path,
                    'file_size': file_size,
                    'download_url': download_url,
                    'date_cataloged': now,
                    'source': 'local_archive',
                })

        if new_entries:
            new_df = pd.DataFrame(new_entries, columns=MANIFEST_COLUMNS)
            manifest = pd.concat([existing_manifest, new_df], ignore_index=True)
            self.logger.info(f'Cataloged {len(new_entries)} new PDF(s)')
        else:
            manifest = existing_manifest
            self.logger.info('No new PDFs to catalog')

        # Deduplicate by file_path.
        before = len(manifest)
        manifest.drop_duplicates(subset=['file_path'], keep='first', inplace=True)
        if len(manifest) < before:
            self.logger.info(f'Removed {before - len(manifest)} duplicate(s)')

        manifest.reset_index(drop=True, inplace=True)
        manifest.to_csv(self.manifest_path, index=False)
        self.logger.info(f'Manifest saved: {len(manifest)} entries → {self.manifest_path}')
        return manifest

    # ── Web Scraping ─────────────────────────────────────────────

    def _scrape_producer_page(
            self,
            slug: str,
            page: int,
        ) -> Tuple[List[Dict], bool]:
        """Scrape a single page of COA listings for a producer.

        Args:
            slug: The yourcoa.com company slug (URL-encoded).
            page: The page number (1-indexed).

        Returns:
            Tuple of:
                - List of observation dicts with keys from PAGE_COLUMNS
                  plus 'download_url'.
                - Boolean indicating if there are more pages.
        """
        url = f'{COMPANY_SEARCH_URL}?t={slug}&page={page}'
        try:
            response = self.session.get(url, timeout=30)
        except requests.exceptions.RequestException as e:
            self.logger.error(f'Request failed for page {page}: {e}')
            return [], False

        if response.status_code != 200:
            self.logger.warning(
                f'HTTP {response.status_code} for page {page} of {slug}'
            )
            return [], False

        soup = BeautifulSoup(response.content, 'html.parser')

        # Find all COA listing boxes.
        divs = soup.find_all(class_='pdf_box')
        if not divs:
            return [], False

        # Extract download links.
        all_links = soup.find_all('a', href=True)
        download_links = []
        for a in all_links:
            href = a['href']
            if 'coa-download' in href:
                full_url = href if href.startswith('http') else BASE_URL + href
                download_links.append(full_url)
        # Deduplicate while preserving order.
        seen = set()
        unique_links = []
        for link in download_links:
            if link not in seen:
                seen.add(link)
                unique_links.append(link)

        # Parse each listing box.
        observations = []
        for i, div in enumerate(divs):
            obs = {}
            spans = div.find_all('span')[:len(PAGE_COLUMNS)]
            for k, span in enumerate(spans):
                obs[PAGE_COLUMNS[k]] = span.get_text(strip=True)
            if i < len(unique_links):
                obs['download_url'] = unique_links[i]
            else:
                obs['download_url'] = ''
            observations.append(obs)

        # Check for next page.
        next_el = soup.find(class_='next')
        has_more = bool(next_el and 'disabled' not in next_el.get('class', []))

        return observations, has_more

    def scrape_producer(
            self,
            license_number: str,
            slug: str,
            dba: str,
            max_pages: int = 500,
        ) -> pd.DataFrame:
        """Scrape all COA listings for a single producer.

        Paginates through yourcoa.com with respectful rate limiting
        and exponential backoff on errors.

        Args:
            license_number: The MMTC license number.
            slug: The yourcoa.com search slug.
            dba: The business DBA name.
            max_pages: Safety limit on page count.

        Returns:
            DataFrame with discovered COA URLs and metadata.
        """
        self.logger.info(f'Scraping {dba} ({license_number}), slug={slug}')
        all_observations = []
        consecutive_errors = 0

        for page in range(1, max_pages + 1):
            observations, has_more = self._scrape_producer_page(slug, page)

            if observations:
                for obs in observations:
                    obs['license_number'] = license_number
                    obs['dba'] = dba
                all_observations.extend(observations)
                consecutive_errors = 0
                self.logger.info(
                    f'  Page {page}: {len(observations)} COA(s) for {dba}'
                )
            else:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    self.logger.warning(
                        f'  3 consecutive empty pages for {dba}, stopping.'
                    )
                    break

            if not has_more:
                self.logger.info(f'  Reached last page ({page}) for {dba}')
                break

            # Respectful pause between pages.
            self._respectful_pause(multiplier=1.0)

        df = pd.DataFrame(all_observations)
        self.logger.info(f'  Total: {len(df)} COA(s) discovered for {dba}')
        return df

    # ── PDF Downloading ──────────────────────────────────────────

    def _download_coa(
            self,
            sample_id: str,
            license_dir: str,
            download_url: str = '',
            max_retries: int = 2,
        ) -> Optional[str]:
        """Download a single COA PDF.

        Tries the direct download endpoint first, then falls back
        to the view/download URL.

        Args:
            sample_id: The COA sample identifier.
            license_dir: Directory to save the PDF.
            download_url: The full download URL (fallback).
            max_retries: Number of retry attempts.

        Returns:
            The filepath if successful, None otherwise.
        """
        outfile = os.path.join(license_dir, f'{sample_id}.pdf')

        # Skip if already downloaded.
        if os.path.exists(outfile) and _is_valid_pdf(outfile):
            return outfile

        # Try direct download endpoint.
        urls_to_try = [
            f'{COA_DOWNLOAD_URL}?sample={sample_id}',
        ]
        if download_url and download_url not in urls_to_try:
            urls_to_try.append(download_url)

        for attempt in range(max_retries + 1):
            for url in urls_to_try:
                try:
                    response = self.session.get(url, timeout=60)
                    if response.status_code == 200 and len(response.content) >= MIN_PDF_SIZE:
                        # Validate it's actually a PDF.
                        if response.content[:5] == b'%PDF-':
                            with open(outfile, 'wb') as f:
                                f.write(response.content)
                            return outfile
                        else:
                            self.logger.debug(
                                f'Response not PDF for {sample_id} from {url}'
                            )
                    elif response.status_code == 429:
                        self.logger.warning(f'Rate limited (429). Backing off.')
                        self._backoff_pause(attempt, base=30.0)
                        break  # Break inner loop, retry via outer loop.
                    elif response.status_code >= 500:
                        self.logger.warning(
                            f'Server error ({response.status_code}) for {sample_id}'
                        )
                        break
                except requests.exceptions.RequestException as e:
                    self.logger.error(f'Download error for {sample_id}: {e}')

            if attempt < max_retries:
                self._backoff_pause(attempt)

        return None

    def download_new_coas(
            self,
            discovered: pd.DataFrame,
            existing_sample_ids: Optional[set] = None,
        ) -> Dict[str, int]:
        """Download only new COAs not already in the local archive.

        Args:
            discovered: DataFrame from scrape_producer() with
                'download_url', 'license_number', and optionally
                'sample_id' columns.
            existing_sample_ids: Set of sample IDs already in the
                archive. If None, builds from file system.

        Returns:
            Dictionary with download statistics.
        """
        stats = {
            'total_discovered': len(discovered),
            'already_exists': 0,
            'downloaded': 0,
            'failed': 0,
            'skipped_no_url': 0,
        }

        if discovered.empty:
            return stats

        # Build existing sample IDs from file system if not provided.
        if existing_sample_ids is None:
            existing_sample_ids = set()
            for license_number in self.producers:
                license_dir = Path(str(self.pdf_dir)) / license_number
                if license_dir.exists():
                    for f in os.listdir(str(license_dir)):
                        if f.lower().endswith('.pdf'):
                            existing_sample_ids.add(
                                _extract_sample_id_from_filename(f)
                            )

        self.logger.info(
            f'Existing archive: {len(existing_sample_ids)} sample IDs'
        )

        for _, row in discovered.iterrows():
            download_url = row.get('download_url', '')
            if not download_url:
                stats['skipped_no_url'] += 1
                continue

            sample_id = _extract_sample_id_from_url(download_url)
            if not sample_id:
                stats['skipped_no_url'] += 1
                continue

            # Check if already downloaded.
            if sample_id in existing_sample_ids:
                stats['already_exists'] += 1
                continue

            license_number = row.get('license_number', '')
            license_dir = os.path.join(str(self.pdf_dir), license_number)
            os.makedirs(license_dir, exist_ok=True)

            # Download with respectful pacing.
            self._respectful_pause(multiplier=1.5)
            result = self._download_coa(
                sample_id, license_dir, download_url
            )

            if result:
                stats['downloaded'] += 1
                existing_sample_ids.add(sample_id)
                self.logger.info(f'Downloaded: {sample_id} → {license_number}/')
            else:
                stats['failed'] += 1
                self.logger.warning(f'Failed: {sample_id}')

        self.logger.info(
            f'Download stats: {stats["downloaded"]} new, '
            f'{stats["already_exists"]} existing, '
            f'{stats["failed"]} failed'
        )
        return stats

    # ── LabResult Conversion ─────────────────────────────────────

    def _convert_to_lab_results(
            self,
            manifest: pd.DataFrame,
        ) -> List[Dict]:
        """Convert the manifest to standardized LabResult records.

        Args:
            manifest: DataFrame from catalog_existing().

        Returns:
            List of dicts matching the LabResult schema.
        """
        results = []

        for _, row in manifest.iterrows():
            sample_id = row.get('sample_id', '')
            license_number = row.get('license_number', '')
            result_id = _generate_result_id(sample_id, license_number)
            dba = row.get('dba', '')
            download_url = row.get('download_url', '')

            if LabResult is not None:
                result = LabResult(
                    id=result_id,
                    sample_id=sample_id,
                    lab=KAYCHA_LAB.get('lab'),
                    lab_address=KAYCHA_LAB.get('lab_address'),
                    lab_city=KAYCHA_LAB.get('lab_city'),
                    lab_county=KAYCHA_LAB.get('lab_county'),
                    lab_state=KAYCHA_LAB.get('lab_state'),
                    lab_zipcode=KAYCHA_LAB.get('lab_zipcode'),
                    lab_latitude=KAYCHA_LAB.get('lab_latitude'),
                    lab_longitude=KAYCHA_LAB.get('lab_longitude'),
                    lab_phone=KAYCHA_LAB.get('lab_phone'),
                    producer=dba,
                    producer_license_number=license_number,
                    coa_url=download_url,
                    lab_results_url=download_url,
                    state='fl',
                    source='kaycha_labs',
                    date_collected=row.get('date_cataloged'),
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
                results.append(result.to_dict())
            else:
                results.append({
                    'id': result_id,
                    'sample_id': sample_id,
                    'lab': KAYCHA_LAB.get('lab'),
                    'lab_state': KAYCHA_LAB.get('lab_state'),
                    'producer': dba,
                    'producer_license_number': license_number,
                    'coa_url': download_url,
                    'state': 'fl',
                    'source': 'kaycha_labs',
                    'date_collected': row.get('date_cataloged'),
                })

        return results

    # ── Main Collection Method ───────────────────────────────────

    def get_results(
            self,
            catalog_only: bool = False,
            scrape: bool = True,
            download: bool = True,
            save_results: bool = True,
            license_filter: Optional[str] = None,
        ) -> pd.DataFrame:
        """Collect Kaycha Labs COA data.

        Orchestrates the full pipeline:
            1. Catalog all existing PDFs into a manifest
            2. Scrape yourcoa.com for new COA URLs per licensee
            3. Download only new COAs not in the archive
            4. Re-catalog to include new downloads
            5. Convert to standardized LabResult records

        Args:
            catalog_only: If True, skip scraping/downloading.
            scrape: If True, scrape yourcoa.com for new COAs.
            download: If True, download discovered COAs.
            save_results: Save the final results CSV.
            license_filter: If set, only process this one license
                number (e.g., 'MMTC-2015-0005').

        Returns:
            DataFrame with standardized lab results (or manifest
            if catalog_only).
        """
        self.logger.info('Starting Kaycha Labs COA collection...')
        self.logger.info(f'PDF directory: {self.pdf_dir}')

        # Determine which producers to process.
        producers = self.producers
        if license_filter:
            if license_filter in producers:
                producers = {license_filter: producers[license_filter]}
                self.logger.info(f'Filtered to license: {license_filter}')
            else:
                self.logger.warning(f'License not found: {license_filter}')

        # Phase 1: Catalog existing archive.
        manifest = self.catalog_existing(incremental=True)
        self.logger.info(f'Archive contains {len(manifest)} COA PDF(s)')

        if catalog_only:
            return manifest

        # Phase 2: Scrape yourcoa.com for new COA URLs.
        all_discovered = []
        if scrape:
            for license_number, meta in producers.items():
                slug = meta.get('slug', '')
                dba = meta.get('dba', meta.get('business_dba_name', ''))
                if not slug:
                    self.logger.info(f'No slug for {dba} ({license_number}), skipping.')
                    continue

                df = self.scrape_producer(license_number, slug, dba)
                if not df.empty:
                    all_discovered.append(df)

                    # Save per-producer URL list.
                    datasets_dir = str(getattr(self, 'datasets_dir', self.data_dir))
                    os.makedirs(datasets_dir, exist_ok=True)
                    ts = datetime.now().strftime('%Y-%m-%dT%H%M%S')
                    filename = f'kaycha-urls-{license_number}-{ts}.csv'
                    df.to_csv(os.path.join(datasets_dir, filename), index=False)

                # Pause between producers to be respectful.
                self._respectful_pause(multiplier=2.0)

        # Phase 3: Download new COAs.
        if download and all_discovered:
            discovered = pd.concat(all_discovered, ignore_index=True)
            self.logger.info(f'Total discovered: {len(discovered)} COA(s)')

            # Build existing sample ID set from manifest.
            existing_ids = set(manifest['sample_id'].tolist()) \
                if not manifest.empty else set()

            download_stats = self.download_new_coas(
                discovered, existing_sample_ids=existing_ids
            )
            self.logger.info(f'Download stats: {download_stats}')

            # Re-catalog to include new downloads.
            manifest = self.catalog_existing(incremental=True)

        # Phase 4: Convert to LabResult records.
        lab_results = self._convert_to_lab_results(manifest)
        df = pd.DataFrame(lab_results)

        if save_results and not df.empty:
            self.save_results(df, prefix='fl-kaycha-labs-results')

        self.logger.info(f'✓ Collected {len(df)} results for Kaycha Labs (FL).')
        return df

    # ── Convenience Methods ──────────────────────────────────────

    def save_results(self, df: pd.DataFrame, prefix: str = 'fl-kaycha-labs-results'):
        """Save results to a timestamped CSV."""
        datasets_dir = getattr(self, 'datasets_dir', self.data_dir)
        os.makedirs(str(datasets_dir), exist_ok=True)
        timestamp = datetime.now().strftime('%Y-%m-%dT%H%M%S')
        filename = f'{prefix}-{timestamp}.csv'
        outpath = os.path.join(str(datasets_dir), filename)
        df.to_csv(outpath, index=False)
        self.logger.info(f'Saved {len(df)} results → {outpath}')

    def get_archive_stats(self) -> Dict:
        """Get summary statistics about the current archive."""
        pdf_dir = Path(str(self.pdf_dir))
        stats = {
            'total_pdfs': 0,
            'total_size_bytes': 0,
            'total_size_gb': 0.0,
            'licensees_with_data': 0,
            'licensees_total': len(self.producers),
            'licensees_with_slug': sum(
                1 for m in self.producers.values() if m.get('slug')
            ),
            'per_licensee': {},
            'manifest_exists': os.path.exists(self.manifest_path),
        }

        for license_number in self.producers:
            license_dir = pdf_dir / license_number
            if not license_dir.exists():
                continue
            pdfs = [f for f in os.listdir(str(license_dir))
                    if f.lower().endswith('.pdf')]
            if pdfs:
                stats['licensees_with_data'] += 1
                total_size = sum(
                    os.path.getsize(str(license_dir / f)) for f in pdfs
                )
                stats['per_licensee'][license_number] = {
                    'count': len(pdfs),
                    'size_bytes': total_size,
                }
                stats['total_pdfs'] += len(pdfs)
                stats['total_size_bytes'] += total_size

        stats['total_size_gb'] = round(stats['total_size_bytes'] / (1024**3), 2)
        return stats


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Inline Tests & CLI                                               ║
# ╚══════════════════════════════════════════════════════════════════╝

def run_unit_tests():
    """Run quick inline smoke tests."""
    import tempfile
    print('Running Kaycha Labs unit tests...')

    # Test _generate_result_id
    rid = _generate_result_id('DA50313006-006', 'MMTC-2015-0001')
    assert len(rid) == 16
    assert rid == _generate_result_id('DA50313006-006', 'MMTC-2015-0001')
    assert rid != _generate_result_id('DA50313006-007', 'MMTC-2015-0001')

    # Test _extract_sample_id_from_url
    assert _extract_sample_id_from_url(
        '/coa/coa-download/DA50313006-006'
    ) == 'DA50313006-006'
    assert _extract_sample_id_from_url(
        'https://yourcoa.com/coa/download?sample=95746'
    ) == '95746'
    assert _extract_sample_id_from_url(
        '/coa/coa-download/95746?wl_id=0&mrk=1'
    ) == '95746'
    assert _extract_sample_id_from_url('') == ''
    assert _extract_sample_id_from_url(None) == ''

    # Test _extract_sample_id_from_filename
    assert _extract_sample_id_from_filename('DA50313006-006.pdf') == 'DA50313006-006'
    assert _extract_sample_id_from_filename('95746.pdf') == '95746'
    assert _extract_sample_id_from_filename('') == ''

    # Test _is_valid_pdf
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as f:
        f.write(b'%PDF-1.4 ' + b'x' * (MIN_PDF_SIZE + 100))
        valid = f.name
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as f:
        f.write(b'%PDF-1.4 tiny')
        small = f.name
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as f:
        f.write(b'<html>not a pdf</html>' + b'x' * (MIN_PDF_SIZE + 100))
        fake = f.name
    assert _is_valid_pdf(valid) is True
    assert _is_valid_pdf(small) is False  # Too small
    assert _is_valid_pdf(fake) is False   # Not PDF
    os.unlink(valid)
    os.unlink(small)
    os.unlink(fake)

    # Test FLORIDA_LICENSES metadata
    assert len(FLORIDA_LICENSES) == 22
    assert 'MMTC-2015-0005' in FLORIDA_LICENSES
    assert FLORIDA_LICENSES['MMTC-2015-0005']['dba'] == 'Trulieve'
    slugged = [k for k, v in FLORIDA_LICENSES.items() if v.get('slug')]
    assert len(slugged) >= 18

    # Test collector init and catalog.
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_dir = os.path.join(tmpdir, 'kaycha')
        data_dir = os.path.join(tmpdir, 'data')

        # Create per-licensee directories with sample PDFs.
        for lic in ['MMTC-2015-0001', 'MMTC-2015-0005']:
            lic_dir = os.path.join(pdf_dir, lic)
            os.makedirs(lic_dir)
            for i in range(3):
                filepath = os.path.join(lic_dir, f'SAMPLE-{lic[-4:]}-{i:03d}.pdf')
                with open(filepath, 'wb') as f:
                    f.write(b'%PDF-1.4 ' + b'x' * (MIN_PDF_SIZE + 50))

        collector = KaychaLabsCollector(
            data_dir=data_dir,
            pdf_dir=pdf_dir,
            verbose=False,
        )

        # Test catalog.
        manifest = collector.catalog_existing()
        assert len(manifest) == 6, f'Expected 6 entries, got {len(manifest)}'
        assert 'sample_id' in manifest.columns
        assert 'license_number' in manifest.columns

        # Test get_results (catalog only, no network).
        results = collector.get_results(catalog_only=True)
        assert len(results) == 6

        # Test get_results (no scrape, no download).
        results = collector.get_results(
            scrape=False, download=False, save_results=False
        )
        assert len(results) == 6
        assert 'id' in results.columns
        assert 'state' in results.columns

        # Test archive stats.
        stats = collector.get_archive_stats()
        assert stats['total_pdfs'] == 6
        assert stats['licensees_with_data'] == 2

    print('✓ All unit tests passed')


def run_integration_test():
    """Run integration test with live network (single page only)."""
    import tempfile
    print('Running Kaycha Labs integration test (network required)...')

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = KaychaLabsCollector(
            data_dir=os.path.join(tmpdir, 'data'),
            pdf_dir=os.path.join(tmpdir, 'kaycha'),
            verbose=True,
        )

        # Scrape just the first page for Trulieve.
        observations, has_more = collector._scrape_producer_page('Trulieve', 1)
        print(f'  Page 1 results: {len(observations)} COA(s)')
        print(f'  Has more pages: {has_more}')

        if observations:
            sample_url = observations[0].get('download_url', '')
            sample_id = _extract_sample_id_from_url(sample_url)
            print(f'  First sample: {sample_id} → {sample_url}')

            # Test single download.
            lic_dir = os.path.join(tmpdir, 'kaycha', 'MMTC-2015-0005')
            os.makedirs(lic_dir, exist_ok=True)
            result = collector._download_coa(sample_id, lic_dir, sample_url)
            if result:
                print(f'  ✓ Downloaded: {os.path.basename(result)} ({os.path.getsize(result)} bytes)')
            else:
                print(f'  ⚠ Download returned None (may be expected if COA unavailable)')

    print('✓ Integration test completed')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Kaycha Labs COA Collector')
    parser.add_argument('--test', action='store_true', help='Run unit tests')
    parser.add_argument('--integration-test', action='store_true',
                        help='Run integration test (network)')
    parser.add_argument('--catalog-only', action='store_true',
                        help='Only catalog existing PDFs')
    parser.add_argument('--no-scrape', action='store_true',
                        help='Skip web scraping')
    parser.add_argument('--no-download', action='store_true',
                        help='Skip downloading')
    parser.add_argument('--license', type=str, default=None,
                        help='Process only this license number')
    parser.add_argument('--data-dir', type=str,
                        default='D:/data/florida/results',
                        help='Data directory')
    parser.add_argument('--pdf-dir', type=str,
                        default='D:/data/florida/results/pdfs/kaycha',
                        help='PDF storage directory')
    parser.add_argument('--cache-path', type=str,
                        default='D:/data/.cache/results-fl-kaycha.jsonl',
                        help='Cache file path')
    args = parser.parse_args()

    if args.test:
        run_unit_tests()
    elif args.integration_test:
        run_integration_test()
    else:
        collector = KaychaLabsCollector(
            data_dir=args.data_dir,
            pdf_dir=args.pdf_dir,
            cache_path=args.cache_path,
        )
        results = collector.get_results(
            catalog_only=args.catalog_only,
            scrape=not args.no_scrape,
            download=not args.no_download,
            license_filter=args.license,
        )
        print(f'Total results: {len(results)}')
        stats = collector.get_archive_stats()
        print(f'Archive: {stats["total_pdfs"]} PDFs, {stats["total_size_gb"]} GB')
        print(f'Licensees with data: {stats["licensees_with_data"]}/{stats["licensees_total"]}')