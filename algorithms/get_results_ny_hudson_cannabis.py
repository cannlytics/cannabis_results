"""
Get Results | New York | Hudson Cannabis
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 6/24/2024
Updated: 2/28/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Collect cannabis lab result COA PDFs from Hudson Cannabis,
    a New York cannabis cultivator that publishes COAs via
    Google Drive links on their website.

    Hudson Cannabis hosts a COA page at:
        https://www.hudsoncannabis.co/coas

    The page contains links to Google Drive files, each being
    a COA PDF for a specific product/batch.

    Collection Strategy:
        Phase 1: Catalog existing local archive of COA PDFs.
        Phase 2: Discover COA Google Drive links from the website.
        Phase 3: Download new COA PDFs from Google Drive.
        Phase 4: Convert manifest into standardized LabResult records.

Data Source:
    - [Hudson Cannabis COAs](https://www.hudsoncannabis.co/coas)

Output:
    - PDF directory with COA files
    - Manifest CSV cataloging all collected COAs
    - Discovered URLs CSV
    - Standardized CSV with LabResult schema fields

Usage:
    ```python
    from algorithms.get_results_ny_hudson_cannabis import (
        HudsonCannabisCollector,
    )

    with HudsonCannabisCollector() as collector:
        results = collector.get_results()
    ```

Command Line:
    ```bash
    # Full collection
    python algorithms/get_results_ny_hudson_cannabis.py

    # Catalog-only mode
    python algorithms/get_results_ny_hudson_cannabis.py --catalog-only

    # Run unit tests
    python algorithms/get_results_ny_hudson_cannabis.py --test
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
from urllib.parse import urlparse

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

# Hudson Cannabis COA page URL.
COA_PAGE_URL = 'https://www.hudsoncannabis.co/coas'

# Hudson Cannabis producer metadata.
HUDSON_PRODUCER = {
    'producer': 'Hudson Cannabis',
    'producer_dba': 'Hudson Cannabis',
    'producer_state': 'ny',
    'producer_website': 'https://www.hudsoncannabis.co',
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

# Google Drive file link regex.
GDRIVE_FILE_RE = re.compile(
    r'https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)',
)

# Google Drive direct download URL template.
GDRIVE_DOWNLOAD_TEMPLATE = (
    'https://drive.google.com/uc?export=download&id={file_id}'
)

# Module-level logger.
logger = logging.getLogger('get_results_ny_hudson_cannabis')


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


def _extract_gdrive_file_id(url: str) -> Optional[str]:
    """Extract the file ID from a Google Drive URL.

    Args:
        url: Google Drive file URL.

    Returns:
        File ID string, or None.
    """
    match = GDRIVE_FILE_RE.search(url)
    if match:
        return match.group(1)
    return None


def _gdrive_direct_url(file_id: str) -> str:
    """Build a direct download URL for a Google Drive file.

    Args:
        file_id: Google Drive file ID.

    Returns:
        Direct download URL.
    """
    return GDRIVE_DOWNLOAD_TEMPLATE.format(file_id=file_id)


def download_google_drive_file(
        url: str,
        save_path: str,
        session: Optional[requests.Session] = None,
    ) -> bool:
    """Download a file from Google Drive.

    Handles the confirmation page for large files.

    Args:
        url: Google Drive sharing URL.
        save_path: Local path to save the file.
        session: Optional requests session.

    Returns:
        True if download succeeded.
    """
    file_id = _extract_gdrive_file_id(url)
    if not file_id:
        return False

    sess = session or requests.Session()
    direct_url = _gdrive_direct_url(file_id)

    try:
        response = sess.get(direct_url, stream=True, timeout=30)

        # Handle virus scan warning for large files.
        if 'confirm' in response.url or b'confirm' in response.content[:1000]:
            for key, value in response.cookies.items():
                if key.startswith('download_warning'):
                    params = {'id': file_id, 'confirm': value}
                    response = sess.get(
                        'https://drive.google.com/uc?export=download',
                        params=params,
                        stream=True,
                        timeout=30,
                    )
                    break

        if response.status_code != 200:
            return False

        content = response.content
        if not _is_valid_pdf(content):
            return False

        with open(save_path, 'wb') as f:
            f.write(content)
        return True

    except Exception:
        return False


# ╔══════════════════════════════════════════════════════════════════╗
# ║ HudsonCannabisCollector                                          ║
# ╚══════════════════════════════════════════════════════════════════╝

class HudsonCannabisCollector:
    """Collector for Hudson Cannabis NY COA PDFs.

    Scrapes the Hudson Cannabis COA page for Google Drive links,
    downloads PDFs, and converts to standardized LabResult records.

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
        """Initialize the Hudson Cannabis collector.

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
            pdf_dir = os.path.join(
                data_dir, 'pdfs', 'hudson-cannabis',
            )

        self.pdf_dir = Path(pdf_dir)
        self.data_dir = Path(data_dir)
        self.datasets_dir = self.data_dir / 'datasets'
        self.manifest_path = (
            self.datasets_dir / 'hudson-cannabis-manifest.csv'
        )
        self.urls_path = (
            self.datasets_dir / 'hudson-cannabis-urls.csv'
        )
        self.download_pause = download_pause
        self.verbose = verbose

        # Logging.
        self.logger = logging.getLogger(
            'get_results_ny_hudson_cannabis',
        )
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

    def discover_drive_links(
            self,
            headless: bool = True,
        ) -> List[Dict]:
        """Discover Google Drive COA links from Hudson Cannabis.

        Navigates to the COA page and extracts all Google Drive
        file links.

        Args:
            headless: Run browser headlessly.

        Returns:
            List of dicts with 'gdrive_url' and 'file_id'.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        self._init_selenium(headless=headless)

        try:
            self.logger.info('Navigating to %s', COA_PAGE_URL)
            self.driver.get(COA_PAGE_URL)

            # Wait for page to load.
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.ID, 'root')),
            )
            time.sleep(2)  # Extra wait for dynamic content.

            # Find Google Drive file links.
            links = self.driver.find_elements(
                By.XPATH,
                "//a[contains(@href, 'drive.google.com/file')]",
            )

            discovered = []
            seen_ids = set()
            for link in links:
                href = link.get_attribute('href') or ''
                file_id = _extract_gdrive_file_id(href)
                if file_id and file_id not in seen_ids:
                    seen_ids.add(file_id)
                    discovered.append({
                        'gdrive_url': href,
                        'file_id': file_id,
                        'direct_url': _gdrive_direct_url(file_id),
                    })

            self.logger.info(
                'Discovered %d Google Drive links',
                len(discovered),
            )

            # Save discovered URLs.
            if discovered:
                urls_df = pd.DataFrame(discovered)
                urls_df['date_discovered'] = (
                    datetime.now().isoformat()
                )
                urls_df.to_csv(str(self.urls_path), index=False)

            return discovered

        except Exception as e:
            self.logger.error(
                'Error discovering links: %s', str(e),
            )
            return []

    # ── Phase 3: Download ────────────────────────────────────────

    def download_pdfs(
            self,
            drive_links: List[Dict],
        ) -> int:
        """Download COA PDFs from Google Drive.

        Args:
            drive_links: List of dicts with 'gdrive_url' and 'file_id'.

        Returns:
            Number of new PDFs downloaded.
        """
        downloaded = 0
        existing_files = {
            f.stem for f in self.pdf_dir.glob('*.pdf')
        }

        for item in drive_links:
            file_id = item['file_id']
            gdrive_url = item['gdrive_url']

            if file_id in existing_files:
                self.logger.debug('Already exists: %s', file_id)
                continue

            filename = f'{file_id}.pdf'
            filepath = self.pdf_dir / filename

            success = download_google_drive_file(
                gdrive_url, str(filepath), session=self.session,
            )

            if success:
                downloaded += 1
                self.logger.info('Downloaded: %s', filename)
            else:
                self.logger.warning(
                    'Failed to download: %s', gdrive_url,
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

            # Extract file ID from filename for the URL.
            file_id = file_name.replace('.pdf', '')
            gdrive_url = ''
            if GDRIVE_FILE_RE.search(file_id) or len(file_id) > 20:
                gdrive_url = (
                    f'https://drive.google.com/file/d/{file_id}/view'
                )

            result_id = _generate_result_id(file_hash)

            record = {
                'result_id': result_id,
                'sample_id': _hash_url(file_name),
                'product_name': file_name.replace(
                    '.pdf', '',
                ),
                'product_type': '',
                'date_tested': '',
                'lab': '',
                'lab_address': '',
                'lab_license_number': '',
                'lab_results_url': gdrive_url,
                'coa_pdf': file_name,
                'source': 'hudson_cannabis',
                'source_url': COA_PAGE_URL,
                'state': 'ny',
                **HUDSON_PRODUCER,
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
        self.logger.info(
            'Starting Hudson Cannabis COA collection...',
        )

        # Phase 1: Catalog.
        manifest = self.catalog_existing()

        if catalog_only:
            results = self._convert_to_lab_results(manifest)
            return pd.DataFrame(results)

        # Phase 2: Discover.
        try:
            drive_links = self.discover_drive_links(
                headless=headless,
            )
        finally:
            self._quit_driver()

        # Phase 3: Download.
        if drive_links:
            self.download_pdfs(drive_links)

        # Re-catalog.
        manifest = self.catalog_existing()

        # Phase 4: Convert.
        results = self._convert_to_lab_results(manifest)
        df = pd.DataFrame(results)

        if len(df) > 0:
            output_path = (
                self.datasets_dir / 'hudson-cannabis-results.csv'
            )
            df.to_csv(str(output_path), index=False)
            self.logger.info(
                'Results: %d records → %s', len(df), output_path,
            )

        self.logger.info(
            '✓ Hudson Cannabis complete: %d results', len(df),
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
            'source': 'hudson_cannabis',
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

    # Test _extract_gdrive_file_id.
    url = 'https://drive.google.com/file/d/1AbCdEfGhIjK/view'
    assert _extract_gdrive_file_id(url) == '1AbCdEfGhIjK'
    assert _extract_gdrive_file_id('https://example.com') is None
    print('  ✓ _extract_gdrive_file_id')

    # Test _gdrive_direct_url.
    direct = _gdrive_direct_url('abc123')
    assert 'abc123' in direct
    assert 'export=download' in direct
    print('  ✓ _gdrive_direct_url')

    # Test GDRIVE_FILE_RE.
    assert GDRIVE_FILE_RE.search(
        'https://drive.google.com/file/d/abc123/view',
    )
    assert not GDRIVE_FILE_RE.search('https://example.com')
    print('  ✓ GDRIVE_FILE_RE')

    # Test collector initialization.
    with tempfile.TemporaryDirectory() as td:
        c = HudsonCannabisCollector(
            pdf_dir=os.path.join(td, 'pdfs'),
            data_dir=os.path.join(td, 'data'),
        )
        assert c.pdf_dir.exists()
        manifest = c.catalog_existing()
        assert len(manifest) == 0
    print('  ✓ HudsonCannabisCollector initialization')

    # Test catalog with mock PDF.
    with tempfile.TemporaryDirectory() as td:
        pdf_dir = os.path.join(td, 'pdfs')
        os.makedirs(pdf_dir)
        with open(os.path.join(pdf_dir, 'test.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4' + b'\x00' * 10300)

        c = HudsonCannabisCollector(
            pdf_dir=pdf_dir,
            data_dir=os.path.join(td, 'data'),
        )
        manifest = c.catalog_existing(save=False)
        assert len(manifest) == 1
    print('  ✓ Catalog with mock PDFs')

    # Test convert.
    with tempfile.TemporaryDirectory() as td:
        c = HudsonCannabisCollector(
            pdf_dir=os.path.join(td, 'pdfs'),
            data_dir=os.path.join(td, 'data'),
        )
        manifest = pd.DataFrame([{
            'file_name': '1AbCdEfGhIjKlMnOpQrStUvWxYz.pdf',
            'file_hash': 'xyz789',
            'date_cataloged': '2026-01-01',
        }])
        results = c._convert_to_lab_results(manifest)
        assert len(results) == 1
        assert results[0]['source'] == 'hudson_cannabis'
        assert results[0]['state'] == 'ny'
        assert results[0]['producer'] == 'Hudson Cannabis'
        assert 'drive.google.com' in results[0]['lab_results_url']
    print('  ✓ Convert to LabResult')

    print('✓ All unit tests passed')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Hudson Cannabis NY COA Collector',
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

    with HudsonCannabisCollector(
        pdf_dir=args.pdf_dir,
        data_dir=args.data_dir,
    ) as collector:
        results = collector.get_results(
            catalog_only=args.catalog_only,
            headless=not args.no_headless,
        )
        print(f'\nResults: {len(results)} records')
