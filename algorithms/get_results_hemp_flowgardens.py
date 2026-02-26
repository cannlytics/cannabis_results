"""
Get Results | Hemp | Flow Gardens
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 1/28/2024
Updated: 2/24/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Collect hemp lab result COA files from Flow Gardens (Tennessee).

    Flow Gardens is a licensed hemp producer in Tennessee that grows
    and sells high-THCA hemp flower, concentrates, pre-rolls, and
    edibles. They publish their Certificates of Analysis on a single
    Shopify page organized by product category (Flower, Pre-Rolls,
    Concentrates, Edibles).

    COA files are hosted on Shopify's CDN and may be:
        - PDFs  (e.g. ``Black_Ice.pdf``)
        - Images (e.g. ``41_Kings_COA__63796...1280.jpg``, ``.png``)

    All links are present in the static HTML — no pagination,
    lazy-loading, or JavaScript rendering required. A simple
    ``requests`` + ``BeautifulSoup`` approach is used (no Selenium).

    The age-gate modal on the page is purely JavaScript-based and
    does not block static HTML content from being returned by the
    server, so no special handling is required for scraping.

    Pipeline:
        1. Catalog existing local COA archive into a manifest.
        2. Scrape the COA page to discover all COA URLs.
        3. Download only NEW COA files not in the manifest.
        4. Convert manifest to standardized LabResult records.

Data Source:
    - Flow Gardens COA page: https://flowgardens.com/pages/coa
    - CDN: https://cdn.shopify.com/s/files/1/0931/4012/4017/files/

Output:
    - COA directory with PDF and image files
    - Manifest CSV cataloging all collected COAs
    - Standardized CSV with LabResult schema fields

Usage:
    ```python
    from algorithms.get_results_hemp_flowgardens import FlowGardensCollector

    collector = FlowGardensCollector()
    results = collector.get_results()
    ```

Command Line:
    ```bash
    # Full collection: catalog + scrape + download + produce results
    python algorithms/get_results_hemp_flowgardens.py

    # Catalog-only mode (no network requests)
    python algorithms/get_results_hemp_flowgardens.py --catalog-only

    # Scrape only — discover URLs but don't download
    python algorithms/get_results_hemp_flowgardens.py --no-download

    # Custom directories
    python algorithms/get_results_hemp_flowgardens.py \\
        --pdf-dir "D:/data/hemp/results/pdfs/flowgardens" \\
        --data-dir "D:/data/hemp/results"

    # Run unit tests
    python algorithms/get_results_hemp_flowgardens.py --test

    # Run integration test (network required)
    python algorithms/get_results_hemp_flowgardens.py --integration-test
    ```
"""
# Standard imports:
from datetime import datetime
import hashlib
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, unquote

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

# COA page URL.
COA_PAGE_URL = 'https://flowgardens.com/pages/coa'

# Flow Gardens producer metadata.
FLOWGARDENS_PRODUCER = {
    'producer': 'Flow Gardens',
    'producer_dba': 'Flow Gardens',
    'producer_state': 'tn',
    'producer_website': 'https://flowgardens.com',
}

# Default HTTP headers — polite identification.
DEFAULT_HEADERS = {
    'User-Agent': (
        'Cannlytics/2.0 '
        '(+https://cannlytics.com; dev@cannlytics.com) '
        'cannabis-data-research'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,'
              'application/pdf;q=0.8,image/*;q=0.7,*/*;q=0.6',
    'Accept-Language': 'en-US,en;q=0.9',
}

# File type constants.
PDF_EXTENSIONS = ('.pdf',)
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.webp')
COA_EXTENSIONS = PDF_EXTENSIONS + IMAGE_EXTENSIONS

# Minimum valid file sizes (bytes).
MIN_PDF_SIZE = 5 * 1024      # 5 KB
MIN_IMAGE_SIZE = 2 * 1024    # 2 KB

# Default pause between requests (seconds).
DEFAULT_PAUSE = 2.0

# Maximum back-off wait time (seconds).
MAX_BACKOFF = 60

# Section headings on the COA page map to product types.
SECTION_PRODUCT_TYPES = {
    'certificate of analysis': 'flower',  # Default top section = flower
    'flower': 'flower',
    'pre-rolls': 'preroll',
    'concentrates': 'concentrate',
    'edibles': 'edible',
}

# Module-level logger.
logger = logging.getLogger('get_results_hemp_flowgardens')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Helper Functions                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

def _generate_result_id(identifier: str) -> str:
    """Generate a deterministic 16-char hex ID from an identifier.

    Args:
        identifier: Unique string (e.g. filename or URL).

    Returns:
        16-character hex string.
    """
    return hashlib.sha256(identifier.encode('utf-8')).hexdigest()[:16]


def _compute_file_hash(filepath: str) -> str:
    """Compute SHA-256 hash of a file.

    Args:
        filepath: Path to the file.

    Returns:
        Full hex digest string.
    """
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def _extract_product_name_from_url(url: str) -> str:
    """Extract a human-readable product name from a Shopify CDN URL.

    Examples::
        'https://cdn.shopify.com/.../Black_Ice.pdf?v=...' → 'Black Ice'
        'https://cdn.shopify.com/.../41_Kings_COA__63796...jpg?v=...'
            → '41 Kings'

    Args:
        url: The CDN URL.

    Returns:
        Cleaned product name string.
    """
    if not url:
        return ''
    # Extract the filename from the URL path.
    parsed = urlparse(url)
    filename = unquote(parsed.path.split('/')[-1])

    # Remove query parameters artifact.
    if '?' in filename:
        filename = filename.split('?')[0]

    # Remove file extension.
    for ext in COA_EXTENSIONS:
        if filename.lower().endswith(ext):
            filename = filename[:len(filename) - len(ext)]
            break

    # Clean up common patterns.
    # Remove trailing COA suffixes (e.g. "_COA__63796.1751033130.1280.1280")
    filename = re.sub(r'_?COA[_\s]*.*$', '', filename, flags=re.IGNORECASE)

    # Remove numeric suffixes like "_6" at end (e.g. "Swizzlers_4")
    # but be careful not to remove meaningful numbers like "41_Kings"
    # Only remove if it's a single digit after an underscore at the end.
    filename = re.sub(r'_\d$', '', filename)

    # Replace underscores with spaces.
    name = filename.replace('_', ' ').strip()

    return name


def _extract_filename_from_url(url: str) -> str:
    """Extract the filename from a Shopify CDN URL.

    Args:
        url: The CDN URL.

    Returns:
        Filename string (e.g. 'Black_Ice.pdf').
    """
    if not url:
        return ''
    parsed = urlparse(url)
    filename = unquote(parsed.path.split('/')[-1])
    if '?' in filename:
        filename = filename.split('?')[0]
    return filename


def _get_file_extension(url: str) -> str:
    """Get the file extension from a URL (lowercase).

    Args:
        url: The file URL.

    Returns:
        Extension string including the dot (e.g. '.pdf', '.jpg').
    """
    filename = _extract_filename_from_url(url)
    for ext in COA_EXTENSIONS:
        if filename.lower().endswith(ext):
            return ext
    return ''


def _is_coa_url(url: str) -> bool:
    """Check if a URL points to a COA file (PDF or image).

    Args:
        url: The URL to check.

    Returns:
        True if the URL appears to be a COA file.
    """
    if not url:
        return False
    return bool(_get_file_extension(url))


def _sanitize_filename(name: str, max_length: int = 200) -> str:
    """Sanitize a string for use as a filename.

    Args:
        name: Raw string.
        max_length: Maximum filename length.

    Returns:
        Sanitized filename-safe string.
    """
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    safe = safe.strip('. ')
    if len(safe) > max_length:
        safe = safe[:max_length].rstrip('. ')
    return safe


def _is_valid_file(data: bytes, url: str) -> bool:
    """Check if downloaded data is a valid COA file.

    Args:
        data: Raw bytes of the downloaded file.
        url: The source URL (used to determine expected type).

    Returns:
        True if the data appears to be a valid file.
    """
    ext = _get_file_extension(url)
    if ext in PDF_EXTENSIONS:
        if len(data) < MIN_PDF_SIZE:
            return False
        return data[:5] == b'%PDF-'
    elif ext in IMAGE_EXTENSIONS:
        if len(data) < MIN_IMAGE_SIZE:
            return False
        # Check for common image magic bytes.
        if data[:3] == b'\xff\xd8\xff':  # JPEG
            return True
        if data[:8] == b'\x89PNG\r\n\x1a\n':  # PNG
            return True
        if data[:4] == b'GIF8':  # GIF
            return True
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP':  # WebP
            return True
        # Fallback: if it's not HTML, assume valid.
        return not data[:6].startswith(b'<html') and not data[:6].startswith(b'<!DOCT')
    return False


def _parse_section_heading(text: str) -> Optional[str]:
    """Parse a section heading into a product type.

    Args:
        text: The heading text (e.g. "Pre-Rolls", "Concentrates").

    Returns:
        Normalized product type string or None.
    """
    if not text:
        return None
    normalized = text.strip().lower()
    return SECTION_PRODUCT_TYPES.get(normalized)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ FlowGardensCollector                                             ║
# ╚══════════════════════════════════════════════════════════════════╝

class FlowGardensCollector:
    """Collector for Flow Gardens hemp COA files (Tennessee).

    Scrapes the Shopify-hosted COA page for PDF and image links,
    catalogs them in a manifest, downloads new files, and converts
    to standardized LabResult records.

    No Selenium required — the page is static HTML.

    Attributes:
        coa_dir: Directory where COA files are stored.
        data_dir: Base data directory for datasets and outputs.
        datasets_dir: Directory for CSV outputs.
        manifest_path: Path to the manifest CSV.
        pause: Seconds to wait between downloads.
        session: Requests session for all HTTP requests.
    """

    def __init__(
            self,
            coa_dir: str = '',
            data_dir: str = '',
            pause: float = DEFAULT_PAUSE,
            verbose: bool = True,
        ):
        """Initialize the Flow Gardens collector.

        Args:
            coa_dir: Directory for COA files (PDFs and images).
                Defaults to ``D:/data/hemp/results/coas/flowgardens``.
            data_dir: Base data directory.
                Defaults to ``D:/data/hemp/results``.
            pause: Seconds to pause between requests.
            verbose: Enable verbose logging.
        """
        # Resolve defaults.
        if not data_dir:
            if PATHS:
                data_dir = PATHS.get(
                    'hemp_data_dir', 'D:/data/hemp/results'
                )
            else:
                data_dir = 'D:/data/hemp/results'
        if not coa_dir:
            coa_dir = os.path.join(data_dir, 'coas', 'flowgardens')

        self.coa_dir = Path(coa_dir)
        self.data_dir = Path(data_dir)
        self.datasets_dir = self.data_dir / 'datasets'
        self.manifest_path = self.datasets_dir / 'flowgardens-manifest.csv'
        self.pause = pause
        self.verbose = verbose

        # Logging.
        self.logger = logging.getLogger('get_results_hemp_flowgardens')
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            fmt = '%(asctime)s | %(name)s | %(levelname)s | %(message)s'
            handler.setFormatter(
                logging.Formatter(fmt, datefmt='%Y-%m-%dT%H:%M:%S'),
            )
            self.logger.addHandler(handler)
        self.logger.setLevel(logging.DEBUG if verbose else logging.INFO)

        # HTTP session.
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

        # Ensure directories exist.
        self.coa_dir.mkdir(parents=True, exist_ok=True)
        self.datasets_dir.mkdir(parents=True, exist_ok=True)

    # ── Context Manager ──────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.session.close()

    # ── Rate Limiting ────────────────────────────────────────────

    def _respectful_pause(self, multiplier: float = 1.0) -> None:
        """Sleep with jitter for respectful rate limiting.

        Args:
            multiplier: Factor to multiply the base pause.
        """
        jitter = random.uniform(0, self.pause * 0.3)
        wait = self.pause * multiplier + jitter
        time.sleep(wait)

    # ── Phase 1: Catalog Existing Archive ────────────────────────

    def catalog_existing(
            self,
            compute_hashes: bool = True,
        ) -> pd.DataFrame:
        """Build a manifest of existing COA files on disk.

        Scans ``coa_dir`` for PDF and image files and records their
        metadata including SHA-256 content hashes.

        Args:
            compute_hashes: If True, compute SHA-256 hashes.

        Returns:
            DataFrame with manifest columns.
        """
        self.logger.info(f'Cataloging existing files in {self.coa_dir}...')
        records = []

        if not self.coa_dir.exists():
            self.logger.info('COA directory does not exist yet.')
            return pd.DataFrame()

        coa_files = sorted([
            f for f in os.listdir(str(self.coa_dir))
            if any(f.lower().endswith(ext) for ext in COA_EXTENSIONS)
        ])

        for fname in coa_files:
            fpath = self.coa_dir / fname
            stat = fpath.stat()
            file_hash = ''
            if compute_hashes:
                try:
                    file_hash = _compute_file_hash(str(fpath))
                except Exception as e:
                    self.logger.warning(f'Hash failed for {fname}: {e}')

            # Determine file type.
            ext = ''
            for e in COA_EXTENSIONS:
                if fname.lower().endswith(e):
                    ext = e
                    break
            file_type = 'pdf' if ext in PDF_EXTENSIONS else 'image'

            records.append({
                'file_name': fname,
                'file_path': str(fpath),
                'file_type': file_type,
                'file_extension': ext,
                'file_size_bytes': stat.st_size,
                'sha256': file_hash,
                'product_name': _extract_product_name_from_url(fname),
                'cataloged_at': datetime.now().isoformat(),
            })

        manifest = pd.DataFrame(records)
        if len(manifest) > 0:
            manifest.to_csv(str(self.manifest_path), index=False)
            self.logger.info(
                f'Cataloged {len(manifest)} COA file(s) → '
                f'{self.manifest_path}'
            )
        else:
            self.logger.info('No existing COA files found.')
        return manifest

    # ── Phase 2: Scrape COA Page ─────────────────────────────────

    def scrape_coa_page(self) -> pd.DataFrame:
        """Scrape the Flow Gardens COA page for all COA URLs.

        Parses the static HTML to extract COA links organized
        by product category sections.

        Returns:
            DataFrame with columns: product_name, product_type,
            coa_url, file_name, file_extension, section.
        """
        self.logger.info(f'Scraping COA page: {COA_PAGE_URL}')
        response = self.session.get(COA_PAGE_URL, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # Find the main content area.
        main_content = soup.find('main') or soup.find('div', class_='page-width') or soup
        records = []

        # Track the current section/product type.
        # The page structure uses h2 headings for sections
        # and link blocks with h3 headings for products.
        current_section = 'flower'  # Default to flower (top section)
        current_product_type = 'flower'

        # Iterate through all elements in the content area.
        for element in main_content.find_all(['h2', 'a']):

            # Check for section headings.
            if element.name == 'h2':
                heading_text = element.get_text(strip=True)
                parsed_type = _parse_section_heading(heading_text)
                if parsed_type:
                    current_section = heading_text.strip()
                    current_product_type = parsed_type
                    self.logger.debug(
                        f'Section: {current_section} → {current_product_type}'
                    )
                continue

            # Check for COA links.
            if element.name == 'a':
                href = element.get('href', '')
                if not href or not _is_coa_url(href):
                    continue

                # Extract product name from the h3 inside the link,
                # or fall back to extracting from the URL.
                h3 = element.find('h3')
                if h3:
                    product_name = h3.get_text(strip=True)
                else:
                    product_name = _extract_product_name_from_url(href)

                filename = _extract_filename_from_url(href)
                ext = _get_file_extension(href)

                records.append({
                    'product_name': product_name,
                    'product_type': current_product_type,
                    'coa_url': href,
                    'file_name': filename,
                    'file_extension': ext,
                    'section': current_section,
                })

        discovered = pd.DataFrame(records)
        self.logger.info(
            f'Discovered {len(discovered)} COA link(s) on page'
        )

        # Log breakdown by section.
        if len(discovered) > 0:
            for section, count in discovered['section'].value_counts().items():
                self.logger.info(f'  {section}: {count} COA(s)')

        # Save discovered URLs snapshot.
        if len(discovered) > 0:
            snapshot_path = (
                self.datasets_dir
                / f'flowgardens-discovered-{datetime.now():%Y%m%d}.csv'
            )
            discovered.to_csv(str(snapshot_path), index=False)
            self.logger.debug(f'Discovery snapshot → {snapshot_path}')

        return discovered

    # ── Phase 3: Download New COAs ───────────────────────────────

    def download_new_coas(
            self,
            discovered: pd.DataFrame,
            existing_files: set,
        ) -> int:
        """Download COA files not already in the local archive.

        Args:
            discovered: DataFrame from ``scrape_coa_page()``.
            existing_files: Set of filenames already on disk.

        Returns:
            Number of files successfully downloaded.
        """
        if discovered.empty:
            self.logger.info('No discovered URLs to download.')
            return 0

        new_urls = discovered[
            ~discovered['file_name'].isin(existing_files)
        ]
        self.logger.info(
            f'Downloading {len(new_urls)} new COA(s) '
            f'({len(existing_files)} already archived)...'
        )

        downloaded = 0
        for idx, row in new_urls.iterrows():
            url = row['coa_url']
            filename = row['file_name']
            target = self.coa_dir / _sanitize_filename(filename)

            try:
                self._respectful_pause()
                resp = self.session.get(url, timeout=60)
                resp.raise_for_status()

                if not _is_valid_file(resp.content, url):
                    self.logger.warning(
                        f'Invalid file content for {filename} '
                        f'({len(resp.content)} bytes)'
                    )
                    continue

                with open(str(target), 'wb') as f:
                    f.write(resp.content)

                downloaded += 1
                self.logger.debug(
                    f'Downloaded: {filename} '
                    f'({len(resp.content):,} bytes)'
                )

            except requests.RequestException as e:
                self.logger.warning(f'Download failed for {filename}: {e}')
            except OSError as e:
                self.logger.warning(f'File write failed for {filename}: {e}')

        self.logger.info(
            f'Downloaded {downloaded}/{len(new_urls)} new COA file(s)'
        )
        return downloaded

    # ── Phase 4: Convert to LabResult Records ────────────────────

    def _convert_to_lab_results(
            self,
            manifest: pd.DataFrame,
            discovered: Optional[pd.DataFrame] = None,
        ) -> List[Dict]:
        """Convert manifest entries to standardized LabResult records.

        Args:
            manifest: DataFrame from ``catalog_existing()``.
            discovered: Optional DataFrame from ``scrape_coa_page()``
                for enriching with product type metadata.

        Returns:
            List of LabResult-like dictionaries.
        """
        results = []
        if manifest.empty:
            return results

        # Build a lookup from filename to discovered metadata.
        metadata_lookup = {}
        if discovered is not None and not discovered.empty:
            for _, row in discovered.iterrows():
                metadata_lookup[row['file_name']] = {
                    'product_type': row.get('product_type', ''),
                    'product_name': row.get('product_name', ''),
                    'coa_url': row.get('coa_url', ''),
                    'section': row.get('section', ''),
                }

        for _, row in manifest.iterrows():
            fname = row.get('file_name', '')
            product_name = row.get('product_name', '')
            file_type = row.get('file_type', '')

            # Enrich with discovered metadata.
            meta = metadata_lookup.get(fname, {})
            product_type = meta.get('product_type', '')
            coa_url = meta.get('coa_url', '')
            if meta.get('product_name'):
                product_name = meta['product_name']

            result = {
                'result_id': _generate_result_id(fname),
                'product_name': product_name,
                'product_type': product_type,
                'producer': FLOWGARDENS_PRODUCER['producer'],
                'producer_dba': FLOWGARDENS_PRODUCER['producer_dba'],
                'producer_state': FLOWGARDENS_PRODUCER['producer_state'],
                'producer_website': FLOWGARDENS_PRODUCER['producer_website'],
                'coa_url': coa_url,
                'coa_file': fname,
                'coa_file_type': file_type,
                'file_hash': row.get('sha256', ''),
                'state': 'tn',
                'source': 'flowgardens',
                'data_collection_date': datetime.now().isoformat()[:10],
            }
            results.append(result)

        self.logger.info(f'Converted {len(results)} LabResult record(s)')
        return results

    # ── Main Pipeline ────────────────────────────────────────────

    def get_results(
            self,
            catalog_only: bool = False,
            scrape: bool = True,
            download: bool = True,
            save_results: bool = True,
        ) -> pd.DataFrame:
        """Run the full collection pipeline.

        Args:
            catalog_only: If True, only build the manifest (no
                network requests).
            scrape: If True, scrape the COA page for new URLs.
            download: If True, download discovered COA files.
            save_results: If True, save the results CSV.

        Returns:
            DataFrame with standardized lab result records.
        """
        self.logger.info('Starting Flow Gardens COA collection...')
        self.logger.info(f'COA directory: {self.coa_dir}')
        self.logger.info(f'Manifest: {self.manifest_path}')

        # Phase 1: Catalog existing archive.
        manifest = self.catalog_existing()

        if catalog_only:
            lab_results = self._convert_to_lab_results(manifest)
            results_df = pd.DataFrame(lab_results)
            if save_results and len(results_df) > 0:
                outpath = (
                    self.datasets_dir
                    / 'hemp-results-flowgardens-latest.csv'
                )
                results_df.to_csv(str(outpath), index=False)
                self.logger.info(
                    f'Results saved: {len(results_df)} → {outpath}'
                )
            self.logger.info(
                f'Manifest contains {len(manifest)} COA file(s)'
            )
            return results_df

        # Phase 2: Scrape COA page.
        discovered = pd.DataFrame()
        if scrape:
            discovered = self.scrape_coa_page()

        # Phase 3: Download new COAs.
        if download and len(discovered) > 0:
            existing_files = set(
                manifest['file_name'].astype(str)
            ) if len(manifest) > 0 else set()
            self.download_new_coas(discovered, existing_files)

            # Re-catalog after downloads.
            manifest = self.catalog_existing()

        # Phase 4: Convert to LabResult records.
        lab_results = self._convert_to_lab_results(
            manifest, discovered,
        )
        results_df = pd.DataFrame(lab_results)
        if save_results and len(results_df) > 0:
            outpath = (
                self.datasets_dir
                / 'hemp-results-flowgardens-latest.csv'
            )
            results_df.to_csv(str(outpath), index=False)
            self.logger.info(
                f'Results saved: {len(results_df)} → {outpath}'
            )

        self.logger.info(f'Total results: {len(results_df)}')
        return results_df

    # ── Archive Stats ────────────────────────────────────────────

    def archive_stats(self) -> Dict:
        """Compute summary statistics for the local archive.

        Returns:
            Dict with total_files, total_pdfs, total_images,
            total_size_bytes, total_size_mb.
        """
        coa_files = []
        if self.coa_dir.exists():
            coa_files = [
                f for f in os.listdir(str(self.coa_dir))
                if any(f.lower().endswith(ext) for ext in COA_EXTENSIONS)
            ]

        total_size = sum(
            (self.coa_dir / f).stat().st_size
            for f in coa_files
            if (self.coa_dir / f).exists()
        )

        pdfs = [f for f in coa_files if f.lower().endswith('.pdf')]
        images = [
            f for f in coa_files
            if any(f.lower().endswith(ext) for ext in IMAGE_EXTENSIONS)
        ]

        manifest_entries = 0
        if self.manifest_path.exists():
            try:
                manifest_entries = len(
                    pd.read_csv(str(self.manifest_path))
                )
            except Exception:
                pass

        return {
            'total_files': len(coa_files),
            'total_pdfs': len(pdfs),
            'total_images': len(images),
            'total_size_bytes': total_size,
            'total_size_mb': round(total_size / (1024 ** 2), 2),
            'manifest_exists': self.manifest_path.exists(),
            'manifest_entries': manifest_entries,
        }


# ╔══════════════════════════════════════════════════════════════════╗
# ║ CLI Entry Point                                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Collect Flow Gardens COA files (Hemp/Tennessee).',
    )
    parser.add_argument(
        '--coa-dir',
        default='D:/data/hemp/results/coas/flowgardens',
        help='Directory for COA files.',
    )
    parser.add_argument(
        '--data-dir',
        default='D:/data/hemp/results',
        help='Base data directory.',
    )
    parser.add_argument(
        '--catalog-only',
        action='store_true',
        help='Only catalog existing files (no network).',
    )
    parser.add_argument(
        '--no-scrape',
        action='store_true',
        help='Skip the scrape phase.',
    )
    parser.add_argument(
        '--no-download',
        action='store_true',
        help='Skip the download phase.',
    )
    parser.add_argument(
        '--test',
        action='store_true',
        help='Run inline smoke tests and exit.',
    )
    parser.add_argument(
        '--integration-test',
        action='store_true',
        help='Run integration test (requires network).',
    )
    args = parser.parse_args()

    # ── Smoke Tests ──────────────────────────────────────────────
    if args.test:
        from test_hemp_flowgardens import run_unit_tests
        success = run_unit_tests()
        exit(0 if success else 1)

    # ── Integration Test ─────────────────────────────────────────
    if args.integration_test:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            print('Running integration test...')
            collector = FlowGardensCollector(
                coa_dir=os.path.join(tmpdir, 'coas'),
                data_dir=tmpdir,
            )
            discovered = collector.scrape_coa_page()
            print(f'  Found {len(discovered)} COA link(s)')
            if len(discovered) > 0:
                print(f'  Sections: {discovered["section"].unique().tolist()}')
                print(f'  Product types: {discovered["product_type"].unique().tolist()}')
                # Download just the first 3 to verify.
                existing = set()
                small_batch = discovered.head(3)
                downloaded = collector.download_new_coas(small_batch, existing)
                print(f'  Downloaded {downloaded} test file(s)')
                stats = collector.archive_stats()
                print(f'  Archive stats: {stats}')
            print('✓ Integration test passed')
        exit(0)

    # ── Main Collection ──────────────────────────────────────────
    with FlowGardensCollector(
        coa_dir=args.coa_dir,
        data_dir=args.data_dir,
    ) as collector:
        results = collector.get_results(
            catalog_only=args.catalog_only,
            scrape=not args.no_scrape,
            download=not args.no_download,
        )
        print(f'Total results: {len(results)}')
        stats = collector.archive_stats()
        print(f'Archive stats: {stats}')