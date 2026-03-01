"""
Get Results | New York | Jetty Extracts
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 6/24/2024
Updated: 2/28/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Collect cannabis lab result COA PDFs from Jetty Extracts,
    a cannabis brand that publishes COAs for New York products
    via Google Drive folders linked from their website.

    Jetty Extracts maintains a COA page at:
        https://jettyextracts.com/coa-new-york/

    The collector works from a static CSV datafile that maps
    products to Google Drive folder URLs containing COA PDFs.
    It can optionally scrape the website to discover new
    folder links.

    Collection Strategy:
        Phase 1: Catalog existing local archive of COA PDFs.
        Phase 2: Discover new COA folders from the Jetty website
                 or load from the existing datafile.
        Phase 3: Download new COA PDFs from Google Drive folders.
        Phase 4: Convert manifest into standardized LabResult records.

Data Source:
    - [Jetty Extracts NY COAs](https://jettyextracts.com/coa-new-york/)

Output:
    - PDF directory with COA files
    - Manifest CSV cataloging all collected COAs
    - Standardized CSV with LabResult schema fields

Usage:
    ```python
    from algorithms.get_results_ny_jetty_extracts import (
        JettyExtractsCollector,
    )

    with JettyExtractsCollector() as collector:
        results = collector.get_results()
    ```

Command Line:
    ```bash
    # Full collection from existing datafile
    python algorithms/get_results_ny_jetty_extracts.py

    # Catalog-only mode (no network)
    python algorithms/get_results_ny_jetty_extracts.py --catalog-only

    # Custom datafile
    python algorithms/get_results_ny_jetty_extracts.py \\
        --datafile path/to/jetty-extracts-coas.csv

    # Discover new folders from the website
    python algorithms/get_results_ny_jetty_extracts.py --discover

    # Run unit tests
    python algorithms/get_results_ny_jetty_extracts.py --test
    ```
"""
# Standard imports:
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
import logging
import os
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

# Jetty Extracts COA page URL.
COA_PAGE_URL = 'https://jettyextracts.com/coa-new-york/'

# Jetty Extracts producer metadata.
JETTY_PRODUCER = {
    'producer': 'Jetty Extracts',
    'producer_dba': 'Jetty Extracts',
    'producer_state': 'ny',
    'producer_website': 'https://jettyextracts.com',
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
DEFAULT_DOWNLOAD_PAUSE = 2.0

# Maximum parallel download workers.
MAX_WORKERS = 3

# Google Drive folder URL regex.
GDRIVE_FOLDER_RE = re.compile(
    r'https?://drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)',
)

# Google Drive direct download URL template.
GDRIVE_DOWNLOAD_URL = (
    'https://drive.google.com/uc?export=download&id={file_id}'
)

# Module-level logger.
logger = logging.getLogger('get_results_ny_jetty_extracts')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Helper Functions                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

def _generate_result_id(key: str) -> str:
    """Generate a deterministic 16-char hex ID.

    Args:
        key: Input string (URL, filename, etc.).

    Returns:
        16-character hex string.
    """
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]


def _hash_url(url: str) -> str:
    """Generate a 12-char hex hash from a URL.

    Args:
        url: The URL to hash.

    Returns:
        12-character hex string.
    """
    return hashlib.sha256(url.encode('utf-8')).hexdigest()[:12]


def _is_valid_pdf(data: bytes, min_size: int = MIN_PDF_SIZE) -> bool:
    """Check if downloaded data is a valid PDF.

    Args:
        data: Raw bytes of the downloaded file.
        min_size: Minimum acceptable file size.

    Returns:
        True if the data starts with ``%PDF-`` and meets
        the size threshold.
    """
    if len(data) < min_size:
        return False
    return data[:5] == b'%PDF-'


def _is_valid_pdf_file(
        filepath: str,
        min_size: int = MIN_PDF_SIZE,
    ) -> bool:
    """Check if an existing file is a valid PDF.

    Args:
        filepath: Path to the file.
        min_size: Minimum acceptable file size.

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


def _sanitize_filename(name: str) -> str:
    """Create a safe filename from arbitrary text.

    Args:
        name: Text to convert to a filename.

    Returns:
        Sanitized string safe for filenames.
    """
    safe = re.sub(r'[^\w\s\-.]', '', name)
    safe = re.sub(r'\s+', '-', safe).strip('-')
    return safe[:100] if safe else 'unknown'


# ╔══════════════════════════════════════════════════════════════════╗
# ║ JettyExtractsCollector                                           ║
# ╚══════════════════════════════════════════════════════════════════╝

class JettyExtractsCollector:
    """Collector for Jetty Extracts NY COA PDFs.

    Reads a CSV datafile mapping products to Google Drive folder
    URLs and downloads COA PDFs. Optionally scrapes the Jetty
    Extracts website to discover new folder links.

    The collector follows the 4-phase pipeline:
        1. Catalog existing local PDFs
        2. Discover COA URLs from datafile or website
        3. Download new COA PDFs from Google Drive
        4. Convert manifest to LabResult records

    Attributes:
        pdf_dir: Directory where COA PDFs are stored.
        data_dir: Base data directory.
        datasets_dir: Directory for CSV outputs.
        manifest_path: Path to the manifest CSV.
        datafile_path: Path to the COA datafile CSV.
        download_pause: Seconds between downloads.
        session: Requests session for downloads.
    """

    def __init__(
            self,
            pdf_dir: str = '',
            data_dir: str = '',
            datafile: str = '',
            download_pause: float = DEFAULT_DOWNLOAD_PAUSE,
            verbose: bool = True,
        ):
        """Initialize the Jetty Extracts collector.

        Args:
            pdf_dir: Directory for COA PDFs.
            data_dir: Base data directory.
            datafile: Path to existing COA datafile CSV.
            download_pause: Seconds between downloads.
            verbose: Enable verbose logging.
        """
        # Resolve defaults.
        if not data_dir:
            if PATHS:
                data_dir = PATHS.get(
                    'ny_data_dir', 'D:/data/new-york/results',
                )
            else:
                data_dir = 'D:/data/new-york/results'
        if not pdf_dir:
            pdf_dir = os.path.join(data_dir, 'pdfs', 'jetty-extracts')

        self.pdf_dir = Path(pdf_dir)
        self.data_dir = Path(data_dir)
        self.datasets_dir = self.data_dir / 'datasets'
        self.manifest_path = (
            self.datasets_dir / 'jetty-extracts-manifest.csv'
        )
        self.datafile_path = Path(datafile) if datafile else (
            self.datasets_dir / 'jetty-extracts-coas.csv'
        )
        self.download_pause = download_pause
        self.verbose = verbose

        # Logging.
        self.logger = logging.getLogger(
            'get_results_ny_jetty_extracts',
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

        # HTTP session for downloads.
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

        # Ensure directories exist.
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.datasets_dir.mkdir(parents=True, exist_ok=True)

    # ── Context Manager ──────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.session.close()

    # ── Phase 1: Catalog ─────────────────────────────────────────

    def catalog_existing(
            self,
            save: bool = True,
        ) -> pd.DataFrame:
        """Catalog all existing COA PDFs in the archive.

        Scans the PDF directory for valid PDF files, computes
        SHA-256 content hashes for deduplication.

        Args:
            save: Save manifest to disk.

        Returns:
            DataFrame with manifest columns.
        """
        self.logger.info(
            'Cataloging PDFs in %s', self.pdf_dir,
        )
        records = []
        seen_hashes = set()

        pdf_files = sorted(self.pdf_dir.rglob('*.pdf'))
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
            self.logger.info(
                'Manifest: %d entries → %s',
                len(manifest), self.manifest_path,
            )

        return manifest

    # ── Phase 2: Discover ────────────────────────────────────────

    def discover_folder_urls(
            self,
            from_website: bool = False,
            headless: bool = True,
        ) -> List[str]:
        """Get Google Drive folder URLs from datafile or website.

        Args:
            from_website: Scrape the website for folder URLs.
            headless: Run browser headlessly.

        Returns:
            List of Google Drive folder URLs.
        """
        # Try loading from datafile first.
        if self.datafile_path.exists() and not from_website:
            self.logger.info(
                'Loading from datafile: %s', self.datafile_path,
            )
            try:
                df = pd.read_csv(str(self.datafile_path))
                # The folder URLs are typically in the last column.
                last_col = df.columns[-1]
                urls = df[last_col].dropna().tolist()
                folder_urls = [
                    u for u in urls if GDRIVE_FOLDER_RE.search(str(u))
                ]
                self.logger.info(
                    'Found %d folder URLs in datafile',
                    len(folder_urls),
                )
                return folder_urls
            except Exception as e:
                self.logger.warning(
                    'Error reading datafile: %s', str(e),
                )

        # Scrape website for folder URLs.
        if from_website:
            return self._scrape_website_for_folders(headless)

        self.logger.warning(
            'No datafile found at %s', self.datafile_path,
        )
        return []

    def _scrape_website_for_folders(
            self, headless: bool = True,
        ) -> List[str]:
        """Scrape the Jetty Extracts COA page for folder URLs.

        Args:
            headless: Run browser headlessly.

        Returns:
            List of Google Drive folder URLs.
        """
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By

            opts = Options()
            if headless:
                opts.add_argument('--headless=new')
            opts.add_argument('--no-sandbox')
            opts.add_argument('--disable-dev-shm-usage')

            driver = webdriver.Chrome(options=opts)
            try:
                driver.get(COA_PAGE_URL)
                time.sleep(3)

                links = driver.find_elements(By.TAG_NAME, 'a')
                folder_urls = []
                for link in links:
                    href = link.get_attribute('href') or ''
                    if GDRIVE_FOLDER_RE.search(href):
                        folder_urls.append(href)

                self.logger.info(
                    'Found %d folder URLs on website',
                    len(folder_urls),
                )
                return list(set(folder_urls))
            finally:
                driver.quit()

        except ImportError:
            self.logger.warning(
                'Selenium not available for website scraping',
            )
            return []

    # ── Phase 3: Download ────────────────────────────────────────

    def download_from_folders(
            self,
            folder_urls: List[str],
            max_workers: int = MAX_WORKERS,
        ) -> int:
        """Download COA PDFs from Google Drive folder URLs.

        Uses gdown to download entire folders in parallel.

        Args:
            folder_urls: Google Drive folder URLs.
            max_workers: Parallel download workers.

        Returns:
            Number of folders processed.
        """
        try:
            import gdown
        except ImportError:
            self.logger.error(
                'gdown not installed. '
                'Install with: pip install gdown',
            )
            return 0

        processed = 0

        def _download_folder(url: str) -> bool:
            try:
                gdown.download_folder(
                    url,
                    output=str(self.pdf_dir),
                    quiet=True,
                )
                self.logger.info('Downloaded folder: %s', url)
                return True
            except Exception as e:
                self.logger.error(
                    'Failed to download %s: %s', url, str(e),
                )
                return False

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_download_folder, url): url
                for url in folder_urls
            }
            for future in as_completed(futures):
                if future.result():
                    processed += 1

        self.logger.info(
            'Processed %d/%d folders',
            processed, len(folder_urls),
        )
        return processed

    # ── Phase 4: Convert ─────────────────────────────────────────

    def _convert_to_lab_results(
            self,
            manifest: pd.DataFrame,
        ) -> List[Dict]:
        """Convert manifest entries to standardized LabResult records.

        Args:
            manifest: DataFrame with manifest columns.

        Returns:
            List of LabResult dictionaries.
        """
        results = []
        for _, row in manifest.iterrows():
            file_name = str(row.get('file_name', ''))
            file_hash = str(row.get('file_hash', ''))

            result_id = _generate_result_id(file_hash)
            product_name = file_name.replace('.pdf', '').replace(
                '-', ' ',
            ).replace('_', ' ')

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
                'source': 'jetty_extracts',
                'source_url': COA_PAGE_URL,
                'state': 'ny',
                **JETTY_PRODUCER,
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
            from_website: bool = False,
            headless: bool = True,
        ) -> pd.DataFrame:
        """Run the full collection pipeline.

        Args:
            catalog_only: Only catalog existing PDFs.
            from_website: Scrape website for new folder URLs.
            headless: Run browser headlessly.

        Returns:
            DataFrame of standardized LabResult records.
        """
        self.logger.info(
            'Starting Jetty Extracts COA collection...',
        )

        # Phase 1: Catalog.
        manifest = self.catalog_existing()

        if catalog_only:
            results = self._convert_to_lab_results(manifest)
            return pd.DataFrame(results)

        # Phase 2: Discover folder URLs.
        folder_urls = self.discover_folder_urls(
            from_website=from_website,
            headless=headless,
        )

        # Phase 3: Download.
        if folder_urls:
            self.download_from_folders(folder_urls)

        # Re-catalog after downloads.
        manifest = self.catalog_existing()

        # Phase 4: Convert.
        results = self._convert_to_lab_results(manifest)
        df = pd.DataFrame(results)

        if len(df) > 0:
            output_path = (
                self.datasets_dir / 'jetty-extracts-results.csv'
            )
            df.to_csv(str(output_path), index=False)
            self.logger.info(
                'Results: %d records → %s', len(df), output_path,
            )

        self.logger.info(
            '✓ Jetty Extracts complete: %d results', len(df),
        )
        return df

    # ── Archive Stats ────────────────────────────────────────────

    def archive_stats(self) -> Dict:
        """Return summary statistics for the local archive."""
        pdf_count = 0
        total_size = 0
        for f in self.pdf_dir.rglob('*.pdf'):
            if _is_valid_pdf_file(str(f)):
                pdf_count += 1
                total_size += f.stat().st_size

        return {
            'source': 'jetty_extracts',
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
    assert _sanitize_filename('Test File!') == 'Test-File'
    print('  ✓ Helper functions')

    # Test GDRIVE_FOLDER_RE.
    url = 'https://drive.google.com/drive/folders/abc123_-XYZ'
    assert GDRIVE_FOLDER_RE.search(url)
    assert not GDRIVE_FOLDER_RE.search('https://example.com')
    print('  ✓ GDRIVE_FOLDER_RE')

    # Test collector initialization.
    with tempfile.TemporaryDirectory() as td:
        c = JettyExtractsCollector(
            pdf_dir=os.path.join(td, 'pdfs'),
            data_dir=os.path.join(td, 'data'),
        )
        assert c.pdf_dir.exists()
        manifest = c.catalog_existing()
        assert len(manifest) == 0
    print('  ✓ JettyExtractsCollector initialization')

    # Test catalog with mock PDF.
    with tempfile.TemporaryDirectory() as td:
        pdf_dir = os.path.join(td, 'pdfs')
        os.makedirs(pdf_dir)
        with open(os.path.join(pdf_dir, 'test.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4' + b'\x00' * 10300)

        c = JettyExtractsCollector(
            pdf_dir=pdf_dir,
            data_dir=os.path.join(td, 'data'),
        )
        manifest = c.catalog_existing(save=False)
        assert len(manifest) == 1
    print('  ✓ Catalog with mock PDFs')

    # Test discover with missing datafile.
    with tempfile.TemporaryDirectory() as td:
        c = JettyExtractsCollector(
            pdf_dir=os.path.join(td, 'pdfs'),
            data_dir=os.path.join(td, 'data'),
        )
        urls = c.discover_folder_urls()
        assert len(urls) == 0
    print('  ✓ Discover with missing datafile')

    # Test convert.
    with tempfile.TemporaryDirectory() as td:
        c = JettyExtractsCollector(
            pdf_dir=os.path.join(td, 'pdfs'),
            data_dir=os.path.join(td, 'data'),
        )
        manifest = pd.DataFrame([{
            'file_name': 'test-product.pdf',
            'file_hash': 'abc123',
            'date_cataloged': '2026-01-01',
        }])
        results = c._convert_to_lab_results(manifest)
        assert len(results) == 1
        assert results[0]['source'] == 'jetty_extracts'
        assert results[0]['state'] == 'ny'
        assert results[0]['producer'] == 'Jetty Extracts'
    print('  ✓ Convert to LabResult')

    print('✓ All unit tests passed')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Jetty Extracts NY COA Collector',
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
        '--datafile', default='',
        help='Path to COA datafile CSV',
    )
    parser.add_argument(
        '--catalog-only', action='store_true',
        help='Only catalog existing PDFs',
    )
    parser.add_argument(
        '--discover', action='store_true',
        help='Scrape website for new folder URLs',
    )
    parser.add_argument(
        '--test', action='store_true',
        help='Run inline unit tests',
    )
    args = parser.parse_args()

    if args.test:
        _run_tests()
        raise SystemExit(0)

    with JettyExtractsCollector(
        pdf_dir=args.pdf_dir,
        data_dir=args.data_dir,
        datafile=args.datafile,
    ) as collector:
        results = collector.get_results(
            catalog_only=args.catalog_only,
            from_website=args.discover,
        )
        print(f'\nResults: {len(results)} records')
