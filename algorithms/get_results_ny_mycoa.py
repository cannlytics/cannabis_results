"""
Get Results | New York | MyCOA (MFNY)
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 6/24/2024
Updated: 2/28/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Collect cannabis lab result COA PDFs from MyCOA, the COA
    publishing platform for MFNY (Made for New York) cannabis.

    MyCOA hosts COA PDFs on Dropbox, with links listed at:
        https://www.mycoa.info/

    The collector scrapes the MyCOA website for Dropbox links,
    downloads each PDF via the Dropbox direct-download mechanism,
    and converts to standardized LabResult records.

    Collection Strategy:
        Phase 1: Catalog existing local archive of COA PDFs.
        Phase 2: Discover COA Dropbox links from mycoa.info.
        Phase 3: Download new COA PDFs from Dropbox.
        Phase 4: Convert manifest into standardized LabResult records.

Data Source:
    - [MyCOA / MFNY](https://www.mycoa.info/)

Output:
    - PDF directory with COA files
    - Manifest CSV cataloging all collected COAs
    - Discovered URLs CSV
    - Standardized CSV with LabResult schema fields

Usage:
    ```python
    from algorithms.get_results_ny_mycoa import MycoaCollector

    with MycoaCollector() as collector:
        results = collector.get_results()
    ```

Command Line:
    ```bash
    # Full collection
    python algorithms/get_results_ny_mycoa.py

    # Catalog-only mode
    python algorithms/get_results_ny_mycoa.py --catalog-only

    # Run unit tests
    python algorithms/get_results_ny_mycoa.py --test
    ```
"""
# Standard imports:
from datetime import datetime
import hashlib
import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# External imports:
import pandas as pd
import requests

# Internal imports:
try:
    from config.results_config import PATHS, SOURCE_CONFIG
    from config.results_schema import LabResult, normalize_product_type
    from results_base import COACollector
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__),
    )))
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

# MyCOA website URL.
MYCOA_URL = 'https://www.mycoa.info/'

# MyCOA / MFNY producer metadata.
MYCOA_PRODUCER = {
    'producer': 'MFNY',
    'producer_dba': 'Made for New York (MFNY)',
    'producer_state': 'ny',
    'producer_website': MYCOA_URL,
}

# Default HTTP headers.
DEFAULT_HEADERS = {
    'User-Agent': (
        'Cannlytics/2.0 '
        '(+https://cannlytics.com; dev@cannlytics.com) '
        'cannabis-data-research'
    ),
    'Accept': (
        'application/pdf,application/xhtml+xml,'
        'text/html,*/*;q=0.7'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
}

# Minimum valid PDF size in bytes.
MIN_PDF_SIZE = 10 * 1024  # 10 KB

# Default pause between downloads (seconds).
DEFAULT_DOWNLOAD_PAUSE = 3.0

# Dropbox link regex.
DROPBOX_RE = re.compile(
    r'https?://(?:www\.)?dropbox\.com/s/[a-zA-Z0-9]+/[^"\'>\s]+',
)

# Module-level logger.
logger = logging.getLogger('get_results_ny_mycoa')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Helper Functions                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

def _generate_result_id(key: str) -> str:
    """Generate a deterministic 16-char hex ID."""
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]


def _hash_url(url: str) -> str:
    """Generate a 12-char hex hash from a URL."""
    return hashlib.sha256(url.encode('utf-8')).hexdigest()[:12]


def _is_valid_pdf(data: bytes, min_size: int = MIN_PDF_SIZE) -> bool:
    """Check if downloaded data is a valid PDF."""
    if len(data) < min_size:
        return False
    return data[:5] == b'%PDF-'


def _is_valid_pdf_file(
        filepath: str,
        min_size: int = MIN_PDF_SIZE,
    ) -> bool:
    """Check if an existing file is a valid PDF."""
    try:
        size = os.path.getsize(filepath)
        if size < min_size:
            return False
        with open(filepath, 'rb') as f:
            header = f.read(5)
        return header.startswith(b'%PDF-')
    except (OSError, IOError):
        return False


def _dropbox_to_direct_url(url: str) -> str:
    """Convert a Dropbox sharing URL to a direct download URL.

    Replaces ``dl=0`` with ``dl=1`` or appends ``dl=1`` to force
    direct download instead of the preview page.

    Args:
        url: Dropbox sharing URL.

    Returns:
        Direct download URL.
    """
    if 'dl=0' in url:
        return url.replace('dl=0', 'dl=1')
    if 'dl=1' in url:
        return url
    separator = '&' if '?' in url else '?'
    return f'{url}{separator}dl=1'


def _extract_filename_from_dropbox(url: str) -> str:
    """Extract filename from a Dropbox URL.

    Args:
        url: Dropbox sharing URL.

    Returns:
        Filename string, or hash-based fallback.
    """
    # Parse the path component.
    from urllib.parse import urlparse, unquote
    parsed = urlparse(url)
    path_parts = parsed.path.rstrip('/').split('/')
    if path_parts:
        name = unquote(path_parts[-1])
        # Remove query parameters from the name.
        name = name.split('?')[0]
        if name and '.' in name:
            return name
    return f'{_hash_url(url)}.pdf'


# ╔══════════════════════════════════════════════════════════════════╗
# ║ MycoaCollector                                                   ║
# ╚══════════════════════════════════════════════════════════════════╝

class MycoaCollector:
    """Collector for MyCOA / MFNY COA PDFs.

    Scrapes the MyCOA website for Dropbox links, downloads PDFs,
    and converts to standardized LabResult records.

    Attributes:
        pdf_dir: Directory where COA PDFs are stored.
        data_dir: Base data directory.
        datasets_dir: Directory for CSV outputs.
        manifest_path: Path to the manifest CSV.
        download_pause: Seconds between downloads.
        session: Requests session for downloads.
        driver: Selenium WebDriver (initialized on demand).
    """

    def __init__(
            self,
            pdf_dir: str = '',
            data_dir: str = '',
            download_pause: float = DEFAULT_DOWNLOAD_PAUSE,
            verbose: bool = True,
        ):
        """Initialize the MyCOA collector.

        Args:
            pdf_dir: Directory for COA PDFs.
            data_dir: Base data directory.
            download_pause: Seconds between downloads.
            verbose: Enable verbose logging.
        """
        if not data_dir:
            if PATHS:
                data_dir = PATHS.get(
                    'ny_data_dir', 'D:/data/new-york/results',
                )
            else:
                data_dir = 'D:/data/new-york/results'
        if not pdf_dir:
            pdf_dir = os.path.join(data_dir, 'pdfs', 'my-coa')

        self.pdf_dir = Path(pdf_dir)
        self.data_dir = Path(data_dir)
        self.datasets_dir = self.data_dir / 'datasets'
        self.manifest_path = (
            self.datasets_dir / 'mycoa-manifest.csv'
        )
        self.urls_path = self.datasets_dir / 'mycoa-urls.csv'
        self.download_pause = download_pause
        self.verbose = verbose

        # Logging.
        self.logger = logging.getLogger('get_results_ny_mycoa')
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            fmt = (
                '%(asctime)s | %(name)s | %(levelname)s | '
                '%(message)s'
            )
            handler.setFormatter(
                logging.Formatter(fmt, datefmt='%Y-%m-%dT%H:%M:%S'),
            )
            self.logger.addHandler(handler)
        self.logger.setLevel(
            logging.DEBUG if verbose else logging.INFO,
        )

        # HTTP session.
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

        # Selenium driver.
        self.driver = None

        # Ensure directories.
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.datasets_dir.mkdir(parents=True, exist_ok=True)

    # ── Context Manager ──────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._quit_driver()
        self.session.close()

    # ── Selenium ─────────────────────────────────────────────────

    def _init_selenium(self, headless: bool = True) -> None:
        """Initialize Selenium WebDriver."""
        if self.driver is not None:
            return
        try:
            from config.driver_utils import initialize_driver
            self.driver = initialize_driver(
                headless=headless, verbose=self.verbose,
            )
        except ImportError:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            opts = Options()
            if headless:
                opts.add_argument('--headless=new')
            opts.add_argument('--no-sandbox')
            opts.add_argument('--disable-dev-shm-usage')
            opts.add_argument('--disable-gpu')
            opts.add_argument(
                '--user-agent=Mozilla/5.0 '
                '(Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/131.0.0.0 Safari/537.36'
            )
            opts.add_argument(
                '--disable-blink-features=AutomationControlled'
            )
            opts.add_experimental_option(
                'excludeSwitches', ['enable-automation'],
            )
            opts.add_experimental_option(
                'useAutomationExtension', False,
            )
            try:
                from selenium.webdriver.chrome.service import Service
                from webdriver_manager.chrome import (
                    ChromeDriverManager,
                )
                service = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(
                    service=service, options=opts,
                )
            except ImportError:
                self.driver = webdriver.Chrome(options=opts)

    def _quit_driver(self) -> None:
        """Safely quit the Selenium WebDriver."""
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    # ── Phase 1: Catalog ─────────────────────────────────────────

    def catalog_existing(
            self,
            save: bool = True,
        ) -> pd.DataFrame:
        """Catalog existing COA PDFs in the archive."""
        self.logger.info('Cataloging PDFs in %s', self.pdf_dir)
        records = []
        seen_hashes = set()

        pdf_files = sorted(self.pdf_dir.glob('*.pdf'))
        self.logger.info('Found %d PDF(s)', len(pdf_files))

        for pdf_path in pdf_files:
            if not _is_valid_pdf_file(str(pdf_path)):
                continue

            with open(str(pdf_path), 'rb') as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            if file_hash in seen_hashes:
                continue
            seen_hashes.add(file_hash)

            records.append({
                'file_name': pdf_path.name,
                'file_path': str(pdf_path),
                'file_hash': file_hash,
                'file_size': pdf_path.stat().st_size,
                'date_cataloged': datetime.now().isoformat(),
            })

        manifest = pd.DataFrame(records)
        self.logger.info('Cataloged %d unique PDF(s)', len(manifest))

        if save and len(manifest) > 0:
            manifest.to_csv(str(self.manifest_path), index=False)

        return manifest

    # ── Phase 2: Discover ────────────────────────────────────────

    def discover_dropbox_links(
            self,
            headless: bool = True,
        ) -> List[str]:
        """Discover Dropbox COA links from mycoa.info.

        Navigates to the MyCOA website and extracts all Dropbox
        sharing links.

        Args:
            headless: Run browser headlessly.

        Returns:
            List of Dropbox URLs.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        self._init_selenium(headless=headless)

        try:
            self.logger.info('Navigating to %s', MYCOA_URL)
            self.driver.get(MYCOA_URL)

            # Wait for Dropbox links to load.
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//a[contains(@href, 'dropbox.com/s')]"),
                ),
            )

            links = self.driver.find_elements(
                By.XPATH, "//a[contains(@href, 'dropbox.com/s')]",
            )
            urls = []
            for link in links:
                href = link.get_attribute('href')
                if href and 'dropbox.com/s' in href:
                    urls.append(href)

            # Deduplicate.
            unique_urls = list(dict.fromkeys(urls))
            self.logger.info(
                'Discovered %d Dropbox links', len(unique_urls),
            )

            # Save discovered URLs.
            if unique_urls:
                urls_df = pd.DataFrame({
                    'dropbox_url': unique_urls,
                    'direct_url': [
                        _dropbox_to_direct_url(u) for u in unique_urls
                    ],
                    'filename': [
                        _extract_filename_from_dropbox(u)
                        for u in unique_urls
                    ],
                    'date_discovered': datetime.now().isoformat(),
                })
                urls_df.to_csv(str(self.urls_path), index=False)

            return unique_urls

        except Exception as e:
            self.logger.error(
                'Error discovering links: %s', str(e),
            )
            return []

    # ── Phase 3: Download ────────────────────────────────────────

    def download_pdfs(
            self,
            dropbox_urls: List[str],
        ) -> int:
        """Download COA PDFs from Dropbox URLs.

        Converts sharing URLs to direct download URLs and
        downloads PDFs via HTTP requests.

        Args:
            dropbox_urls: Dropbox sharing URLs.

        Returns:
            Number of new PDFs downloaded.
        """
        downloaded = 0
        existing_files = {
            f.name for f in self.pdf_dir.glob('*.pdf')
        }

        for url in dropbox_urls:
            filename = _extract_filename_from_dropbox(url)
            if filename in existing_files:
                self.logger.debug('Already exists: %s', filename)
                continue

            direct_url = _dropbox_to_direct_url(url)
            filepath = self.pdf_dir / filename

            try:
                response = self.session.get(
                    direct_url, timeout=30, allow_redirects=True,
                )
                if response.status_code != 200:
                    self.logger.warning(
                        'HTTP %d: %s',
                        response.status_code, url,
                    )
                    continue

                if not _is_valid_pdf(response.content):
                    self.logger.warning(
                        'Invalid PDF from %s (size=%d)',
                        url, len(response.content),
                    )
                    continue

                with open(str(filepath), 'wb') as f:
                    f.write(response.content)

                downloaded += 1
                self.logger.info('Downloaded: %s', filename)

            except Exception as e:
                self.logger.error(
                    'Error downloading %s: %s', url, str(e),
                )

            time.sleep(
                self.download_pause + random.uniform(0.5, 1.5),
            )

        self.logger.info(
            'Downloaded %d new PDF(s)', downloaded,
        )
        return downloaded

    # ── Phase 4: Convert ─────────────────────────────────────────

    def _convert_to_lab_results(
            self,
            manifest: pd.DataFrame,
        ) -> List[Dict]:
        """Convert manifest to standardized LabResult records."""
        results = []
        for _, row in manifest.iterrows():
            file_name = str(row.get('file_name', ''))
            file_hash = str(row.get('file_hash', ''))

            result_id = _generate_result_id(file_hash)
            product_name = file_name.replace(
                '.pdf', '',
            ).replace('-', ' ').replace('_', ' ')

            record = {
                'result_id': result_id,
                'sample_id': _hash_url(file_name),
                'product_name': product_name,
                'product_type': '',
                'date_tested': '',
                'lab': '',
                'lab_address': '',
                'lab_license_number': '',
                'lab_results_url': '',
                'coa_pdf': file_name,
                'source': 'mycoa',
                'source_url': MYCOA_URL,
                'state': 'ny',
                **MYCOA_PRODUCER,
                'date_collected': str(
                    row.get('date_cataloged', ''),
                ),
                'file_hash': file_hash,
            }
            results.append(record)

        return results

    # ── Main Pipeline ────────────────────────────────────────────

    def get_results(
            self,
            catalog_only: bool = False,
            headless: bool = True,
        ) -> pd.DataFrame:
        """Run the full collection pipeline.

        Args:
            catalog_only: Only catalog existing PDFs.
            headless: Run browser headlessly.

        Returns:
            DataFrame of standardized LabResult records.
        """
        self.logger.info('Starting MyCOA COA collection...')

        # Phase 1: Catalog.
        manifest = self.catalog_existing()

        if catalog_only:
            results = self._convert_to_lab_results(manifest)
            return pd.DataFrame(results)

        # Phase 2: Discover.
        try:
            dropbox_urls = self.discover_dropbox_links(
                headless=headless,
            )
        finally:
            self._quit_driver()

        # Phase 3: Download.
        if dropbox_urls:
            self.download_pdfs(dropbox_urls)

        # Re-catalog.
        manifest = self.catalog_existing()

        # Phase 4: Convert.
        results = self._convert_to_lab_results(manifest)
        df = pd.DataFrame(results)

        if len(df) > 0:
            output_path = self.datasets_dir / 'mycoa-results.csv'
            df.to_csv(str(output_path), index=False)
            self.logger.info(
                'Results: %d records → %s', len(df), output_path,
            )

        self.logger.info(
            '✓ MyCOA complete: %d results', len(df),
        )
        return df

    # ── Archive Stats ────────────────────────────────────────────

    def archive_stats(self) -> Dict:
        """Return summary statistics for the local archive."""
        pdf_count = 0
        total_size = 0
        for f in self.pdf_dir.glob('*.pdf'):
            if _is_valid_pdf_file(str(f)):
                pdf_count += 1
                total_size += f.stat().st_size

        return {
            'source': 'mycoa',
            'state': 'ny',
            'total_pdfs': pdf_count,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
        }


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Inline Unit Tests                                                ║
# ╚══════════════════════════════════════════════════════════════════╝

def _run_tests():
    """Run inline unit tests."""
    import tempfile

    print('Running unit tests...')

    # Test helpers.
    assert len(_generate_result_id('test')) == 16
    assert len(_hash_url('test')) == 12
    assert _is_valid_pdf(b'%PDF-' + b'\x00' * 10240)
    assert not _is_valid_pdf(b'<html>')
    print('  ✓ Helper functions')

    # Test _dropbox_to_direct_url.
    url1 = 'https://www.dropbox.com/s/abc123/file.pdf?dl=0'
    assert _dropbox_to_direct_url(url1).endswith('dl=1')
    url2 = 'https://www.dropbox.com/s/abc123/file.pdf'
    assert 'dl=1' in _dropbox_to_direct_url(url2)
    url3 = 'https://www.dropbox.com/s/abc123/file.pdf?dl=1'
    assert _dropbox_to_direct_url(url3) == url3
    print('  ✓ _dropbox_to_direct_url')

    # Test _extract_filename_from_dropbox.
    url = 'https://www.dropbox.com/s/abc123/My-COA-Report.pdf?dl=0'
    assert _extract_filename_from_dropbox(url) == 'My-COA-Report.pdf'
    url_no_ext = 'https://www.dropbox.com/s/abc123'
    name = _extract_filename_from_dropbox(url_no_ext)
    assert name.endswith('.pdf')
    print('  ✓ _extract_filename_from_dropbox')

    # Test DROPBOX_RE.
    assert DROPBOX_RE.search(
        'https://www.dropbox.com/s/abc123/file.pdf?dl=0',
    )
    assert not DROPBOX_RE.search('https://example.com')
    print('  ✓ DROPBOX_RE')

    # Test collector initialization.
    with tempfile.TemporaryDirectory() as td:
        c = MycoaCollector(
            pdf_dir=os.path.join(td, 'pdfs'),
            data_dir=os.path.join(td, 'data'),
        )
        assert c.pdf_dir.exists()
        manifest = c.catalog_existing()
        assert len(manifest) == 0
    print('  ✓ MycoaCollector initialization')

    # Test catalog with mock PDF.
    with tempfile.TemporaryDirectory() as td:
        pdf_dir = os.path.join(td, 'pdfs')
        os.makedirs(pdf_dir)
        with open(os.path.join(pdf_dir, 'test.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4' + b'\x00' * 10300)

        c = MycoaCollector(
            pdf_dir=pdf_dir,
            data_dir=os.path.join(td, 'data'),
        )
        manifest = c.catalog_existing(save=False)
        assert len(manifest) == 1
    print('  ✓ Catalog with mock PDFs')

    # Test convert.
    with tempfile.TemporaryDirectory() as td:
        c = MycoaCollector(
            pdf_dir=os.path.join(td, 'pdfs'),
            data_dir=os.path.join(td, 'data'),
        )
        manifest = pd.DataFrame([{
            'file_name': 'mfny-coa.pdf',
            'file_hash': 'xyz789',
            'date_cataloged': '2026-01-01',
        }])
        results = c._convert_to_lab_results(manifest)
        assert len(results) == 1
        assert results[0]['source'] == 'mycoa'
        assert results[0]['state'] == 'ny'
        assert results[0]['producer'] == 'MFNY'
    print('  ✓ Convert to LabResult')

    print('✓ All unit tests passed')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='MyCOA (MFNY) NY COA Collector',
    )
    parser.add_argument(
        '--pdf-dir', default='',
        help='PDF output directory',
    )
    parser.add_argument(
        '--data-dir', default='',
        help='Base data directory',
    )
    parser.add_argument(
        '--catalog-only', action='store_true',
        help='Only catalog existing PDFs',
    )
    parser.add_argument(
        '--no-headless', action='store_true',
        help='Show browser window',
    )
    parser.add_argument(
        '--test', action='store_true',
        help='Run inline unit tests',
    )
    args = parser.parse_args()

    if args.test:
        _run_tests()
        raise SystemExit(0)

    with MycoaCollector(
        pdf_dir=args.pdf_dir,
        data_dir=args.data_dir,
    ) as collector:
        results = collector.get_results(
            catalog_only=args.catalog_only,
            headless=not args.no_headless,
        )
        print(f'\nResults: {len(results)} records')
