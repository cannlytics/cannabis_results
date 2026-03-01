"""
Get Results | Arizona | Sticky Saguaro
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 8/24/2024
Updated: 2/28/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Collect cannabis lab result COA PDFs from Sticky Saguaro in Arizona
    via their testing portal at testing.stickysaguaro.com.

    Sticky Saguaro publishes COA results through a DataTables-powered
    HTML table that supports pagination. Each row links to a COA PDF
    hosted on their testing subdomain.

    Collection Strategy:
        Phase 1: Catalog existing local archive of COA PDFs.
        Phase 2: Scrape the Sticky Saguaro testing page to discover
                 all COA PDF URLs across paginated results.
        Phase 3: Download new COA PDFs not already in the local archive.
        Phase 4: Convert manifest into standardized LabResult records.

Data Sources:
    - [Sticky Saguaro Testing](https://testing.stickysaguaro.com/)
    - COA PDFs hosted at: https://testing.stickysaguaro.com/wp-content/uploads/

Output:
    - PDF directory with COA files (named by URL hash)
    - Manifest CSV cataloging all collected COAs
    - Discovered URLs CSV (per-scrape snapshot)
    - Standardized CSV with LabResult schema fields

Usage:
    ```python
    from algorithms.get_results_az_sticky_saguaro import StickySaguaroCollector

    with StickySaguaroCollector() as collector:
        results = collector.get_results()
    ```

Command Line:
    ```bash
    # Full collection: catalog + scrape + download + produce results
    python algorithms/get_results_az_sticky_saguaro.py

    # Catalog-only mode (no network requests)
    python algorithms/get_results_az_sticky_saguaro.py --catalog-only

    # Scrape only — discover URLs but don't download
    python algorithms/get_results_az_sticky_saguaro.py --no-download

    # Custom directories
    python algorithms/get_results_az_sticky_saguaro.py \\
        --pdf-dir "D:/data/arizona/results/pdfs/sticky-saguaro" \\
        --data-dir "D:/data/arizona/results"

    # Run unit tests
    python algorithms/get_results_az_sticky_saguaro.py --test

    # Run integration test (Selenium + network required)
    python algorithms/get_results_az_sticky_saguaro.py --integration-test
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
from urllib.parse import urljoin

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

# Sticky Saguaro testing page URL.
TESTING_URL = 'https://testing.stickysaguaro.com/'

# Sticky Saguaro producer metadata.
STICKY_SAGUARO_PRODUCER = {
    'producer': 'Sticky Saguaro',
    'producer_dba': 'Sticky Saguaro',
    'producer_state': 'az',
    'producer_website': 'https://stickysaguaro.com',
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
MIN_PDF_SIZE = 5 * 1024  # 5 KB

# Default pause between requests (seconds).
DEFAULT_SCRAPE_PAUSE = 3.0
DEFAULT_DOWNLOAD_PAUSE = 2.0

# DataTables pagination settings.
TABLE_ID = 'stickyInfo'
TABLE_LOAD_TIMEOUT = 20

# Module-level logger.
logger = logging.getLogger('get_results_az_sticky_saguaro')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Helper Functions                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

def _generate_result_id(url: str) -> str:
    """Generate a deterministic 16-char hex ID from a URL.

    Args:
        url: The COA URL string.

    Returns:
        16-character hex string.
    """
    return hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]


def _hash_url(url: str) -> str:
    """Generate an MD5 hash of a URL.

    Args:
        url: URL to hash.

    Returns:
        32-character hex MD5 hash.
    """
    return hashlib.md5(url.encode('utf-8')).hexdigest()


def _clean_url(url: str) -> str:
    """Clean a COA URL by removing shortcut link artifacts.

    The Sticky Saguaro testing page sometimes has URLs with
    ' - Shortcut.lnk' appended from Windows shortcut files.

    Args:
        url: Raw URL string.

    Returns:
        Cleaned URL string.
    """
    return url.replace(' - Shortcut.lnk', '').strip()


def _is_valid_pdf(data: bytes, min_size: int = MIN_PDF_SIZE) -> bool:
    """Check if downloaded data is a valid PDF.

    Args:
        data: Raw bytes of the downloaded file.
        min_size: Minimum acceptable file size.

    Returns:
        True if the data starts with ``%PDF-`` and meets the
        size threshold.
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


def _extract_product_name_from_url(url: str) -> str:
    """Extract a readable product name from a COA URL.

    The URL typically contains the PDF filename which has
    the product or strain name.

    Args:
        url: COA PDF URL.

    Returns:
        Extracted product name or empty string.
    """
    try:
        filename = url.rsplit('/', 1)[-1]
        name = filename.replace('.pdf', '').replace('.PDF', '')
        name = name.replace('-', ' ').replace('_', ' ')
        # Remove common prefixes/suffixes.
        name = re.sub(r'^\d+\s*', '', name)
        return name.strip()
    except Exception:
        return ''


# ╔══════════════════════════════════════════════════════════════════╗
# ║ StickySaguaroCollector                                           ║
# ╚══════════════════════════════════════════════════════════════════╝

class StickySaguaroCollector:
    """Collector for Sticky Saguaro COA PDFs in Arizona.

    Scrapes the DataTables-powered testing page to discover COA
    PDF URLs, downloads new PDFs, and converts them to standardized
    LabResult records.

    Attributes:
        pdf_dir: Directory where COA PDFs are stored.
        data_dir: Base data directory for datasets and outputs.
        datasets_dir: Directory for CSV outputs.
        manifest_path: Path to the manifest CSV.
        scrape_pause: Seconds between page navigations.
        download_pause: Seconds between PDF downloads.
        session: Requests session for downloads.
        driver: Selenium WebDriver instance (initialized on demand).
    """

    def __init__(
            self,
            pdf_dir: str = '',
            data_dir: str = '',
            scrape_pause: float = DEFAULT_SCRAPE_PAUSE,
            download_pause: float = DEFAULT_DOWNLOAD_PAUSE,
            verbose: bool = True,
        ):
        """Initialize the Sticky Saguaro collector.

        Args:
            pdf_dir: Directory for COA PDFs.
            data_dir: Base data directory.
            scrape_pause: Seconds between page navigations.
            download_pause: Seconds between PDF downloads.
            verbose: Enable verbose logging.
        """
        # Resolve defaults.
        if not data_dir:
            if PATHS:
                data_dir = str(PATHS.state_dir('az'))
            else:
                data_dir = 'D:/data/arizona/results'
        if not pdf_dir:
            pdf_dir = os.path.join(data_dir, 'pdfs', 'sticky-saguaro')

        self.pdf_dir = Path(pdf_dir)
        self.data_dir = Path(data_dir)
        self.datasets_dir = self.data_dir / 'datasets'
        self.manifest_path = self.datasets_dir / 'sticky-saguaro-manifest.csv'
        self.scrape_pause = scrape_pause
        self.download_pause = download_pause
        self.verbose = verbose

        # Logging.
        self.logger = logging.getLogger('get_results_az_sticky_saguaro')
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            fmt = '%(asctime)s | %(name)s | %(levelname)s | %(message)s'
            handler.setFormatter(
                logging.Formatter(fmt, datefmt='%Y-%m-%dT%H:%M:%S'),
            )
            self.logger.addHandler(handler)
        self.logger.setLevel(
            logging.DEBUG if verbose else logging.INFO,
        )

        # HTTP session for PDF downloads.
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

        # Selenium driver (initialized on demand).
        self.driver = None

        # Ensure directories exist.
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.datasets_dir.mkdir(parents=True, exist_ok=True)

    # ── Context Manager ──────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._quit_driver()
        self.session.close()

    # ── Selenium Management ──────────────────────────────────────

    def _init_selenium(self, headless: bool = True) -> None:
        """Initialize a Selenium WebDriver.

        Args:
            headless: Run the browser without a visible window.
        """
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
            opts.add_argument('--window-size=1920,1080')
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
            try:
                from selenium.webdriver.chrome.service import Service
                from webdriver_manager.chrome import ChromeDriverManager
                service = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(
                    service=service, options=opts,
                )
            except ImportError:
                self.driver = webdriver.Chrome(options=opts)
        self.logger.info(
            f'Selenium driver initialized (headless={headless})'
        )

    def _quit_driver(self) -> None:
        """Safely close the Selenium WebDriver."""
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            try:
                if hasattr(self.driver, 'service') \
                        and self.driver.service.process:
                    self.driver.service.process.kill()
            except Exception:
                pass
            self.driver = None

    # ── Rate Limiting ────────────────────────────────────────────

    def _respectful_pause(
            self,
            base: Optional[float] = None,
            multiplier: float = 1.0,
        ) -> None:
        """Sleep with jitter for respectful rate limiting.

        Args:
            base: Base pause duration (defaults to scrape_pause).
            multiplier: Factor to multiply the base pause.
        """
        if base is None:
            base = self.scrape_pause
        jitter = random.uniform(0, base * 0.3)
        wait = base * multiplier + jitter
        time.sleep(wait)

    # ── Phase 1: Catalog Existing Archive ────────────────────────

    def catalog_existing(
            self,
            incremental: bool = True,
            compute_hashes: bool = True,
        ) -> pd.DataFrame:
        """Build or update a manifest of existing COA PDFs.

        Scans ``pdf_dir`` for PDF files and records their metadata.
        When ``incremental=True``, only catalogs files not already
        present in the existing manifest.

        Args:
            incremental: If True, only catalog new files.
            compute_hashes: If True, compute SHA-256 content hashes.

        Returns:
            DataFrame with manifest columns.
        """
        existing = pd.DataFrame()
        if incremental and self.manifest_path.exists():
            try:
                existing = pd.read_csv(str(self.manifest_path))
                self.logger.info(
                    f'Loaded existing manifest: '
                    f'{len(existing)} entries'
                )
            except Exception as exc:
                self.logger.warning(
                    f'Failed to load manifest: {exc}'
                )

        existing_names = set()
        if len(existing) > 0 and 'file_name' in existing.columns:
            existing_names = set(existing['file_name'].astype(str))

        # Scan the PDF directory.
        pdf_files = sorted([
            f for f in os.listdir(str(self.pdf_dir))
            if f.lower().endswith('.pdf')
        ]) if self.pdf_dir.exists() else []
        self.logger.info(
            f'Found {len(pdf_files)} PDF(s) in {self.pdf_dir}'
        )

        new_rows = []
        now = datetime.now().isoformat()
        for f in pdf_files:
            if incremental and f in existing_names:
                continue

            fp = self.pdf_dir / f
            if not _is_valid_pdf_file(str(fp)):
                self.logger.debug(f'Skipping invalid PDF: {f}')
                continue

            file_size = fp.stat().st_size if fp.exists() else 0
            file_hash = ''
            if compute_hashes and fp.exists():
                sha = hashlib.sha256()
                with open(str(fp), 'rb') as fh:
                    for chunk in iter(lambda: fh.read(65536), b''):
                        sha.update(chunk)
                file_hash = sha.hexdigest()

            url_hash = f.replace('.pdf', '').strip()
            new_rows.append({
                'file_name': f,
                'url_hash': url_hash,
                'file_path': str(fp),
                'file_size': file_size,
                'file_hash': file_hash,
                'date_cataloged': now,
                'source': 'sticky_saguaro',
                'coa_url': '',
                'pdf_name': '',
                'date': '',
            })

        if new_rows:
            self.logger.info(f'Cataloged {len(new_rows)} new PDF(s)')
            new_df = pd.DataFrame(new_rows)
            manifest = pd.concat(
                [existing, new_df], ignore_index=True,
            )
        else:
            self.logger.info('No new PDFs to catalog')
            manifest = existing if len(existing) > 0 else pd.DataFrame()

        # Content deduplication by file_hash.
        if len(manifest) > 0 and 'file_hash' in manifest.columns:
            before = len(manifest)
            non_empty = manifest[manifest['file_hash'] != '']
            empty_hash = manifest[manifest['file_hash'] == '']
            deduped = non_empty.drop_duplicates(
                subset='file_hash', keep='first',
            )
            manifest = pd.concat(
                [deduped, empty_hash], ignore_index=True,
            )
            removed = before - len(manifest)
            if removed > 0:
                self.logger.info(
                    f'Removed {removed} content-duplicate(s)'
                )

        # Save manifest.
        if len(manifest) > 0:
            manifest.to_csv(str(self.manifest_path), index=False)
            self.logger.info(
                f'Manifest saved: {len(manifest)} entries '
                f'→ {self.manifest_path}'
            )

        return manifest

    # ── Phase 2: Discover COA URLs ───────────────────────────────

    def discover_coas(
            self,
            headless: bool = True,
        ) -> pd.DataFrame:
        """Discover COA PDF URLs by scraping the testing page.

        Navigates through all pages of the DataTables-powered
        testing table, collecting PDF URLs and metadata from
        each row.

        Args:
            headless: Run browser in headless mode.

        Returns:
            DataFrame with discovered COA metadata:
                url_hash, coa_url, pdf_name, date, discovered_at.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        # Initialize Selenium.
        self._init_selenium(headless=headless)
        self.driver.get(TESTING_URL)

        # Wait for the DataTable to load.
        wait = WebDriverWait(self.driver, TABLE_LOAD_TIMEOUT)
        try:
            wait.until(EC.presence_of_element_located(
                (By.ID, TABLE_ID)
            ))
        except Exception as exc:
            self.logger.error(
                f'Failed to load testing table: {exc}'
            )
            return pd.DataFrame()

        # Determine total number of pages.
        try:
            pagination = wait.until(EC.presence_of_element_located(
                (By.ID, f'{TABLE_ID}_paginate')
            ))
            page_buttons = pagination.find_elements(
                By.CSS_SELECTOR, 'a.paginate_button'
            )
            # The last button before "Next" is the page count.
            page_numbers = []
            for btn in page_buttons:
                try:
                    idx = btn.get_attribute('data-dt-idx')
                    if idx and idx.isdigit():
                        page_numbers.append(int(idx))
                except Exception:
                    pass
            last_page = max(page_numbers) if page_numbers else 1
        except Exception:
            last_page = 1
        self.logger.info(
            f'Total pages to process: {last_page}'
        )

        # Scrape all pages.
        discovered = []
        current_page = 1
        while current_page <= last_page:
            self.logger.info(
                f'Processing page {current_page}/{last_page}'
            )
            self._respectful_pause()

            rows = self.driver.find_elements(
                By.CSS_SELECTOR, f'#{TABLE_ID} tbody tr'
            )
            for row in rows:
                try:
                    link_element = row.find_element(
                        By.CSS_SELECTOR, 'td a'
                    )
                    coa_url = link_element.get_attribute('href')
                    pdf_name = link_element.text.strip()
                    date = ''
                    try:
                        date = row.find_element(
                            By.CSS_SELECTOR, 'td.sorting_1'
                        ).text.strip()
                    except Exception:
                        pass

                    # Clean the URL.
                    coa_url = _clean_url(coa_url)
                    if not coa_url:
                        continue

                    # Resolve relative URLs.
                    if not coa_url.startswith('http'):
                        coa_url = urljoin(TESTING_URL, coa_url)

                    url_hash = _hash_url(coa_url)
                    discovered.append({
                        'url_hash': url_hash,
                        'coa_url': coa_url,
                        'pdf_name': pdf_name,
                        'date': date,
                        'discovered_at': datetime.now().isoformat(),
                    })
                except Exception as e:
                    self.logger.debug(f'Error processing row: {e}')

            # Navigate to next page.
            if current_page < last_page:
                try:
                    next_button = wait.until(
                        EC.element_to_be_clickable(
                            (By.ID, f'{TABLE_ID}_next')
                        )
                    )
                    next_button.click()
                    current_page += 1
                    time.sleep(1)  # Allow table to re-render.
                except Exception as e:
                    self.logger.warning(
                        f'Error navigating to page {current_page + 1}: {e}'
                    )
                    break
            else:
                break

        # Close the driver.
        self._quit_driver()

        # Build DataFrame.
        df = pd.DataFrame(discovered)
        self.logger.info(
            f'Discovery complete: {len(discovered)} COA URLs found '
            f'across {current_page} pages'
        )

        # Save discovered URLs snapshot.
        if len(df) > 0:
            ts = datetime.now().strftime('%Y-%m-%dT%H-%M-%S')
            urls_path = (
                self.datasets_dir
                / f'az-lab-result-urls-sticky-saguaro-{ts}.csv'
            )
            df.to_csv(str(urls_path), index=False)
            self.logger.info(f'Saved URL snapshot → {urls_path}')

        return df

    # ── Phase 3: Download New COAs ───────────────────────────────

    def _download_coa(
            self,
            coa_url: str,
            url_hash: str,
            max_retries: int = 3,
        ) -> Optional[str]:
        """Download a single COA PDF.

        Args:
            coa_url: The download URL.
            url_hash: Hash of the URL (used for filename).
            max_retries: Maximum download attempts.

        Returns:
            Path to the downloaded PDF, or None on failure.
        """
        outfile = str(self.pdf_dir / f'{url_hash}.pdf')

        # Skip if already downloaded.
        if os.path.exists(outfile) and _is_valid_pdf_file(outfile):
            self.logger.debug(f'Already downloaded: {url_hash}')
            return outfile

        for attempt in range(max_retries):
            try:
                response = self.session.get(
                    coa_url,
                    timeout=30,
                    stream=True,
                    allow_redirects=True,
                )
                response.raise_for_status()

                # Read the full content.
                data = response.content

                # Validate PDF.
                if not _is_valid_pdf(data):
                    self.logger.warning(
                        f'Invalid PDF from {coa_url} '
                        f'({len(data)} bytes)'
                    )
                    return None

                # Save the file.
                with open(outfile, 'wb') as f:
                    f.write(data)

                self.logger.info(
                    f'Downloaded: {url_hash}.pdf '
                    f'({len(data):,} bytes)'
                )
                return outfile

            except requests.RequestException as e:
                if attempt < max_retries - 1:
                    delay = (2 ** attempt) + random.uniform(0, 1)
                    self.logger.warning(
                        f'Download failed (attempt {attempt + 1}/'
                        f'{max_retries}): {e}. '
                        f'Retrying in {delay:.1f}s...'
                    )
                    time.sleep(delay)
                else:
                    self.logger.error(
                        f'Failed to download after {max_retries} '
                        f'attempts: {coa_url}'
                    )
        return None

    def download_new_coas(
            self,
            discovered: pd.DataFrame,
            existing_hashes: Optional[set] = None,
        ) -> int:
        """Download COA PDFs that aren't already in the archive.

        Args:
            discovered: DataFrame with 'url_hash' and 'coa_url' columns.
            existing_hashes: Set of already-downloaded URL hashes.

        Returns:
            Number of successfully downloaded COAs.
        """
        if existing_hashes is None:
            existing_hashes = set()

        # Filter to new COAs only.
        to_download = []
        for _, row in discovered.iterrows():
            url_hash = str(row.get('url_hash', ''))
            coa_url = str(row.get('coa_url', ''))
            if not url_hash or not coa_url:
                continue
            pdf_path = self.pdf_dir / f'{url_hash}.pdf'
            if url_hash in existing_hashes:
                continue
            if pdf_path.exists() and _is_valid_pdf_file(str(pdf_path)):
                continue
            to_download.append((url_hash, coa_url))

        if not to_download:
            self.logger.info('No new COAs to download')
            return 0

        self.logger.info(
            f'Downloading {len(to_download)} new COA PDF(s)...'
        )

        downloaded = 0
        for url_hash, coa_url in to_download:
            result = self._download_coa(coa_url, url_hash)
            if result:
                downloaded += 1
            self._respectful_pause(
                base=self.download_pause,
                multiplier=1.0,
            )

        self.logger.info(
            f'Downloaded {downloaded}/{len(to_download)} COA PDF(s)'
        )
        return downloaded

    # ── Phase 4: Convert to LabResult Records ────────────────────

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
            url_hash = str(row.get('url_hash', ''))
            coa_url = str(row.get('coa_url', ''))
            pdf_name = str(row.get('pdf_name', ''))
            date = str(row.get('date', ''))
            file_name = str(row.get('file_name', ''))

            result_id = _generate_result_id(
                coa_url if coa_url else url_hash,
            )
            product_name = pdf_name or _extract_product_name_from_url(
                coa_url,
            )

            record = {
                'result_id': result_id,
                'sample_id': url_hash,
                'product_name': product_name,
                'product_type': '',
                'date_tested': date,
                'lab': '',
                'lab_address': '',
                'lab_license_number': '',
                'lab_results_url': coa_url,
                'coa_pdf': file_name,
                'source': 'sticky_saguaro',
                'source_url': TESTING_URL,
                'state': 'az',
                **STICKY_SAGUARO_PRODUCER,
                'date_collected': str(
                    row.get('date_cataloged', ''),
                ),
                'file_hash': str(row.get('file_hash', '')),
            }
            results.append(record)

        return results

    # ── Main Pipeline ────────────────────────────────────────────

    def get_results(
            self,
            catalog_only: bool = False,
            discover: bool = True,
            download: bool = True,
            headless: bool = True,
            save_results: bool = True,
        ) -> pd.DataFrame:
        """Execute the full collection pipeline.

        Phase 1: Catalog existing PDFs in the local archive.
        Phase 2: Discover COA URLs by scraping the testing page.
        Phase 3: Download new COA PDFs.
        Phase 4: Convert manifest to standardized LabResult records.

        Args:
            catalog_only: If True, only build the manifest.
            discover: If True, run the URL scraping.
            download: If True, download discovered COA PDFs.
            headless: Run Selenium in headless mode.
            save_results: If True, save the results CSV.

        Returns:
            DataFrame with standardized lab result records.
        """
        self.logger.info('Starting Sticky Saguaro COA collection...')
        self.logger.info(f'PDF directory: {self.pdf_dir}')
        self.logger.info(f'Manifest: {self.manifest_path}')

        # Phase 1: Catalog existing archive.
        manifest = self.catalog_existing()

        if catalog_only:
            lab_results = self._convert_to_lab_results(manifest)
            results_df = pd.DataFrame(lab_results)
            if save_results and len(results_df) > 0:
                outpath = (
                    self.datasets_dir
                    / 'az-results-sticky-saguaro-latest.csv'
                )
                results_df.to_csv(str(outpath), index=False)
                self.logger.info(
                    f'Results saved: {len(results_df)} → {outpath}'
                )
            return results_df

        # Phase 2: Discover COA URLs.
        discovered = pd.DataFrame()
        if discover:
            discovered = self.discover_coas(headless=headless)

        # Phase 3: Download new COAs.
        if download and len(discovered) > 0:
            existing_hashes = set()
            if len(manifest) > 0 and 'url_hash' in manifest.columns:
                existing_hashes = set(
                    manifest['url_hash'].astype(str)
                )
            self.download_new_coas(discovered, existing_hashes)

            # Re-catalog after downloads.
            manifest = self.catalog_existing()

        # Close Selenium.
        self._quit_driver()

        # Enrich manifest with discovered metadata.
        if len(discovered) > 0 and len(manifest) > 0:
            url_map = dict(zip(
                discovered['url_hash'].astype(str),
                discovered['coa_url'].astype(str),
            ))
            name_map = dict(zip(
                discovered['url_hash'].astype(str),
                discovered['pdf_name'].astype(str),
            ))
            date_map = dict(zip(
                discovered['url_hash'].astype(str),
                discovered['date'].astype(str),
            ))
            manifest['coa_url'] = manifest['url_hash'].map(
                url_map,
            ).fillna(manifest.get('coa_url', ''))
            manifest['pdf_name'] = manifest['url_hash'].map(
                name_map,
            ).fillna(manifest.get('pdf_name', ''))
            manifest['date'] = manifest['url_hash'].map(
                date_map,
            ).fillna(manifest.get('date', ''))

        # Phase 4: Convert to LabResult records.
        lab_results = self._convert_to_lab_results(manifest)
        results_df = pd.DataFrame(lab_results)
        if save_results and len(results_df) > 0:
            outpath = (
                self.datasets_dir
                / 'az-results-sticky-saguaro-latest.csv'
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
            Dict with total_pdfs, total_size_bytes, etc.
        """
        pdf_files = []
        if self.pdf_dir.exists():
            pdf_files = [
                f for f in os.listdir(str(self.pdf_dir))
                if f.lower().endswith('.pdf')
            ]

        total_size = sum(
            (self.pdf_dir / f).stat().st_size
            for f in pdf_files
            if (self.pdf_dir / f).exists()
        )

        manifest_entries = 0
        if self.manifest_path.exists():
            try:
                manifest_entries = len(
                    pd.read_csv(str(self.manifest_path)),
                )
            except Exception:
                pass

        return {
            'total_pdfs': len(pdf_files),
            'total_size_bytes': total_size,
            'total_size_mb': round(total_size / (1024 ** 2), 2),
            'manifest_entries': manifest_entries,
        }


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Unit Tests                                                       ║
# ╚══════════════════════════════════════════════════════════════════╝

def run_unit_tests():
    """Run inline smoke tests for all helper functions."""
    import tempfile

    print('Running unit tests...')

    # _generate_result_id
    assert _generate_result_id('test') == _generate_result_id('test')
    assert len(_generate_result_id('test')) == 16
    assert _generate_result_id('a') != _generate_result_id('b')
    print('  ✓ _generate_result_id')

    # _hash_url
    h = _hash_url('https://example.com/test.pdf')
    assert len(h) == 32
    assert h == _hash_url('https://example.com/test.pdf')
    print('  ✓ _hash_url')

    # _clean_url
    assert _clean_url('test.pdf - Shortcut.lnk') == 'test.pdf'
    assert _clean_url('normal.pdf') == 'normal.pdf'
    assert _clean_url('  spaced.pdf  ') == 'spaced.pdf'
    print('  ✓ _clean_url')

    # _is_valid_pdf
    assert _is_valid_pdf(b'%PDF-1.4' + b'\x00' * 10000)
    assert not _is_valid_pdf(b'<html>')
    assert not _is_valid_pdf(b'%PDF-1.4' + b'\x00' * 100)
    print('  ✓ _is_valid_pdf')

    # _extract_product_name_from_url
    url = 'https://testing.stickysaguaro.com/wp-content/uploads/2024/Blue-Dream-COA.pdf'
    name = _extract_product_name_from_url(url)
    assert 'Blue' in name and 'Dream' in name
    assert _extract_product_name_from_url('') == ''
    print('  ✓ _extract_product_name_from_url')

    # StickySaguaroCollector initialization
    with tempfile.TemporaryDirectory() as tmpdir:
        collector = StickySaguaroCollector(
            pdf_dir=os.path.join(tmpdir, 'pdfs'),
            data_dir=tmpdir,
        )
        assert collector.pdf_dir.exists()
        assert collector.datasets_dir.exists()

        # catalog_existing on empty dir.
        manifest = collector.catalog_existing()
        assert len(manifest) == 0

        # _convert_to_lab_results on empty.
        results = collector._convert_to_lab_results(manifest)
        assert results == []

        # archive_stats.
        stats = collector.archive_stats()
        assert stats['total_pdfs'] == 0
    print('  ✓ StickySaguaroCollector initialization and empty ops')

    # Test with mock PDF files.
    with tempfile.TemporaryDirectory() as tmpdir:
        collector = StickySaguaroCollector(
            pdf_dir=os.path.join(tmpdir, 'pdfs'),
            data_dir=tmpdir,
            verbose=False,
        )
        # Create a fake valid PDF.
        fake_pdf = collector.pdf_dir / 'abc123.pdf'
        with open(str(fake_pdf), 'wb') as f:
            f.write(b'%PDF-1.4' + b'\x00' * 10000)

        manifest = collector.catalog_existing()
        assert len(manifest) == 1
        assert manifest.iloc[0]['file_name'] == 'abc123.pdf'

        stats = collector.archive_stats()
        assert stats['total_pdfs'] == 1
    print('  ✓ StickySaguaroCollector catalog with mock PDFs')

    print('✓ All unit tests passed')


def run_integration_test():
    """Run integration test requiring Selenium and network."""
    import tempfile

    print('Running integration test...')
    print('  This test requires Selenium + Chrome + network access.')

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = StickySaguaroCollector(
            pdf_dir=os.path.join(tmpdir, 'pdfs'),
            data_dir=tmpdir,
            scrape_pause=2.0,
            download_pause=1.0,
        )

        # Test 1: Discover COA URLs.
        print('  [1] Discovering COA URLs...')
        discovered = collector.discover_coas(headless=True)
        print(f'      Found {len(discovered)} URLs')
        assert len(discovered) > 0, 'Expected at least one COA URL'

        # Test 2: Download a single COA.
        if len(discovered) > 0:
            first = discovered.iloc[0]
            print(f'  [2] Downloading: {first["pdf_name"]}...')
            result = collector._download_coa(
                first['coa_url'], first['url_hash'],
            )
            if result:
                size = os.path.getsize(result)
                print(f'      ✓ Downloaded ({size:,} bytes)')
            else:
                print('      ⚠ Download returned None')

    print('✓ Integration test completed')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ CLI Entry Point                                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Collect Sticky Saguaro COA PDFs (Arizona).',
    )
    parser.add_argument(
        '--pdf-dir',
        default='D:/data/arizona/results/pdfs/sticky-saguaro',
        help='Directory for COA PDFs.',
    )
    parser.add_argument(
        '--data-dir',
        default='D:/data/arizona/results',
        help='Base data directory.',
    )
    parser.add_argument(
        '--catalog-only',
        action='store_true',
        help='Only catalog existing PDFs (no network).',
    )
    parser.add_argument(
        '--no-discover',
        action='store_true',
        help='Skip the COA discovery phase.',
    )
    parser.add_argument(
        '--no-download',
        action='store_true',
        help='Skip the PDF download phase.',
    )
    parser.add_argument(
        '--no-headless',
        action='store_true',
        help='Show the browser window (visible mode).',
    )
    parser.add_argument(
        '--test',
        action='store_true',
        help='Run unit tests and exit.',
    )
    parser.add_argument(
        '--integration-test',
        action='store_true',
        help='Run integration test (Selenium + network).',
    )
    args = parser.parse_args()

    if args.test:
        run_unit_tests()
        exit(0)

    if args.integration_test:
        run_integration_test()
        exit(0)

    with StickySaguaroCollector(
        pdf_dir=args.pdf_dir,
        data_dir=args.data_dir,
    ) as collector:
        results = collector.get_results(
            catalog_only=args.catalog_only,
            discover=not args.no_discover,
            download=not args.no_download,
            headless=not args.no_headless,
        )
        print(f'Total results: {len(results)}')
        stats = collector.archive_stats()
        print(f'Archive stats: {stats}')
