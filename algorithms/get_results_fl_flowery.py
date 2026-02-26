"""
Get Results | Florida | The Flowery
Copyright (c) 2023-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 5/18/2023
Updated: 2/22/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Collect cannabis lab result COA PDFs from The Flowery in Florida.

    The Flowery publishes COA PDFs through their Zendesk-based support
    site, organized by "drops" (product release batches). This collector:

    1. Catalogs the existing local archive (~304 PDFs) into a
       searchable manifest with SHA-256 content hashes.
    2. Scrapes the drop information page to discover all drop
       articles and their attached COA PDFs.
    3. Downloads only NEW COA PDFs not already in the local archive,
       with respectful rate limiting.
    4. Converts the combined manifest into standardized LabResult records.

    The Flowery is a single-licensee producer (MMTC-2019-0020), so all
    PDFs are stored flat in the pdf_dir (no sub-directories needed).

    NOTE: The original algorithm had a critical bug — it filtered
    attachment links with ``href.endswith('.pdf')`` but Zendesk
    attachment URLs are like ``/hc/en-us/article_attachments/{id}``
    (no ``.pdf`` extension). The link *text* contains the product
    name ending in ``.pdf``, but the href does not. This refactor
    correctly parses the ``<li class="attachment-item">`` structure.

    Additionally, the original saved CSVs with a ``ca-`` prefix
    (California) instead of ``fl-`` (Florida) and created a
    ``MMTC-2019-0020/`` subdirectory under pdf_dir.

Data Sources:
    - [The Flowery Support](https://support.theflowery.co)
    - Drop list: https://support.theflowery.co/hc/en-us/sections/
      7240468576283-Drop-Information
    - Drop articles: https://support.theflowery.co/hc/en-us/articles/{id}
    - Attachments: https://support.theflowery.co/hc/en-us/
      article_attachments/{id}

Output:
    - PDF directory with COA files (flat, named by attachment ID)
    - Manifest CSV cataloging all collected COAs
    - Discovered URLs CSV (per-scrape snapshot)
    - Standardized CSV with LabResult schema fields

Usage:
    ```python
    from algorithms.get_results_fl_flowery import FloweryCollector

    collector = FloweryCollector()
    results = collector.get_results()
    ```

Command Line:
    ```bash
    # Full collection: catalog + scrape + download + produce results
    python algorithms/get_results_fl_flowery.py

    # Catalog-only mode (no network requests)
    python algorithms/get_results_fl_flowery.py --catalog-only

    # Scrape only — discover URLs but don't download
    python algorithms/get_results_fl_flowery.py --no-download

    # Custom directories
    python algorithms/get_results_fl_flowery.py \\
        --pdf-dir "D:/data/florida/results/pdfs/flowery" \\
        --data-dir "D:/data/florida/results"

    # Run unit tests
    python algorithms/get_results_fl_flowery.py --test

    # Run integration test (Selenium + network required)
    python algorithms/get_results_fl_flowery.py --integration-test
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

BASE_URL = 'https://support.theflowery.co'
DROPS_URL = (
    BASE_URL
    + '/hc/en-us/sections/7240468576283-Drop-Information'
)

# The Flowery producer metadata.
FLOWERY_PRODUCER = {
    'producer': 'The Flowery',
    'producer_dba': 'The Flowery',
    'producer_license_number': 'MMTC-2019-0020',
    'producer_state': 'fl',
    'producer_website': 'https://theflowery.co',
}

# Default HTTP headers — polite identification.
DEFAULT_HEADERS = {
    'User-Agent': (
        'Cannlytics/2.0 '
        '(+https://cannlytics.com; dev@cannlytics.com) '
        'cannabis-data-research'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,'
              'application/pdf;q=0.8,*/*;q=0.7',
    'Accept-Language': 'en-US,en;q=0.9',
}

# Minimum valid PDF size in bytes. Files smaller than this are likely
# HTML error pages or empty responses, not real COA PDFs.
MIN_PDF_SIZE = 10 * 1024  # 10 KB

# Default pause between requests (seconds).
DEFAULT_PAUSE = 3.0

# Maximum back-off wait time (seconds).
MAX_BACKOFF = 60

# Regex patterns for parsing drop titles.
DROP_NUMBER_RE = re.compile(r'Drop\s*#?\s*(\d+)', re.IGNORECASE)
DROP_DATE_RE = re.compile(r'\((\d{1,2}/\d{1,2}/\d{2,4})\)')

# Module-level logger.
logger = logging.getLogger('get_results_fl_flowery')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Helper Functions                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

def _generate_result_id(file_name: str) -> str:
    """Generate a deterministic 16-char hex ID from a filename.

    Args:
        file_name: The PDF filename (e.g. ``'44281628412955.pdf'``).

    Returns:
        16-character hex string.
    """
    return hashlib.sha256(file_name.encode('utf-8')).hexdigest()[:16]


def _extract_attachment_id(url: str) -> str:
    """Extract the Zendesk attachment ID from a URL.

    Handles both full URLs and relative paths::

        /hc/en-us/article_attachments/44281628412955
        https://support.theflowery.co/.../article_attachments/44281628412955

    Args:
        url: The attachment URL.

    Returns:
        The attachment ID string, or empty string if not found.
    """
    if not url:
        return ''
    match = re.search(r'article_attachments/(\d+)', url)
    return match.group(1) if match else ''


def _extract_sample_id_from_filename(filename: str) -> str:
    """Extract a sample ID from a PDF filename.

    Handles multiple naming conventions:
        - ``44281628412955.pdf``     → ``'44281628412955'``
        - ``batch-sample.pdf``       → ``'batch-sample'``
        - ``Product Name Here.pdf``  → ``'Product Name Here'``

    Args:
        filename: The PDF filename.

    Returns:
        Sample ID string (filename without ``.pdf`` extension).
    """
    if not filename:
        return ''
    name = filename
    if name.lower().endswith('.pdf'):
        name = name[:-4]
    return name.strip()


def _parse_drop_title(title: str) -> Tuple[Optional[int], Optional[str]]:
    """Parse drop number and date from a drop article title.

    Examples::

        'Drop #65 (12/15/25) Product COAs' → (65, '12/15/25')
        'Drop 64 (11/17/25) Product COAs'  → (64, '11/17/25')
        'Some Other Title'                  → (None, None)

    Args:
        title: The article title string.

    Returns:
        Tuple of (drop_number, drop_date_str).
    """
    drop_number = None
    drop_date = None
    m_num = DROP_NUMBER_RE.search(title)
    if m_num:
        drop_number = int(m_num.group(1))
    m_date = DROP_DATE_RE.search(title)
    if m_date:
        drop_date = m_date.group(1)
    return drop_number, drop_date


def _is_valid_pdf(data: bytes, min_size: int = MIN_PDF_SIZE) -> bool:
    """Check if downloaded data is a valid PDF.

    Args:
        data: Raw bytes of the downloaded file.
        min_size: Minimum acceptable file size.

    Returns:
        True if the data starts with ``%PDF-`` and meets the size
        threshold.
    """
    if len(data) < min_size:
        return False
    return data[:5] == b'%PDF-'


def _sanitize_filename(name: str, max_length: int = 100) -> str:
    """Sanitize a string for use as a filename component.

    Removes or replaces characters that are problematic on
    Windows/Linux filesystems.

    Args:
        name: Raw string (e.g. product name from Zendesk).
        max_length: Maximum length for the result.

    Returns:
        Sanitized filename-safe string.
    """
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    safe = safe.strip('. ')
    if len(safe) > max_length:
        safe = safe[:max_length].rstrip('. ')
    return safe


# ╔══════════════════════════════════════════════════════════════════╗
# ║ FloweryCollector                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

class FloweryCollector:
    """Collector for The Flowery COA PDFs in Florida.

    Scrapes the Zendesk-based support site for COA PDF attachments
    organized by product drops, catalogs them in a manifest, and
    converts to standardized LabResult records.

    The collector uses Selenium for page navigation (Zendesk returns
    403 for plain requests) and transfers cookies to a requests
    session for efficient PDF downloads.

    Attributes:
        pdf_dir: Directory where COA PDFs are stored.
        data_dir: Base data directory for datasets and outputs.
        datasets_dir: Directory for CSV outputs.
        manifest_path: Path to the manifest CSV.
        pause: Seconds to wait between requests.
        session: Requests session for downloads.
        driver: Selenium WebDriver instance (initialized on demand).
    """

    def __init__(
            self,
            pdf_dir: str = '',
            data_dir: str = '',
            pause: float = DEFAULT_PAUSE,
            verbose: bool = True,
        ):
        """Initialize the Flowery collector.

        Args:
            pdf_dir: Directory for COA PDFs.
                Defaults to ``D:/data/florida/results/pdfs/flowery``.
            data_dir: Base data directory.
                Defaults to ``D:/data/florida/results``.
            pause: Seconds to pause between requests.
            verbose: Enable verbose logging.
        """
        # Resolve defaults from config or hardcoded paths.
        if not data_dir:
            if PATHS:
                data_dir = PATHS.get('fl_data_dir', 'D:/data/florida/results')
            else:
                data_dir = 'D:/data/florida/results'
        if not pdf_dir:
            pdf_dir = os.path.join(data_dir, 'pdfs', 'flowery')

        self.pdf_dir = Path(pdf_dir)
        self.data_dir = Path(data_dir)
        self.datasets_dir = self.data_dir / 'datasets'
        self.manifest_path = self.datasets_dir / 'flowery-manifest.csv'
        self.pause = pause
        self.verbose = verbose

        # Logging.
        self.logger = logging.getLogger('get_results_fl_flowery')
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            fmt = '%(asctime)s | %(name)s | %(levelname)s | %(message)s'
            handler.setFormatter(
                logging.Formatter(fmt, datefmt='%Y-%m-%dT%H:%M:%S'),
            )
            self.logger.addHandler(handler)
        self.logger.setLevel(logging.DEBUG if verbose else logging.INFO)

        # HTTP session for downloads.
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

        Uses the project's ``driver_utils`` module when available
        for automatic ChromeDriver matching. Falls back to basic
        Chrome options with anti-detection flags to help pass
        Cloudflare challenges.

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
            opts.add_argument(
                '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/131.0.0.0 Safari/537.36'
            )
            # Anti-bot-detection flags.
            opts.add_argument(
                '--disable-blink-features=AutomationControlled'
            )
            opts.add_experimental_option(
                'excludeSwitches', ['enable-automation'],
            )
            opts.add_experimental_option(
                'useAutomationExtension', False,
            )
            opts.add_argument('--window-size=1920,1080')
            try:
                from selenium.webdriver.chrome.service import Service
                from webdriver_manager.chrome import ChromeDriverManager
                service = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(
                    service=service, options=opts,
                )
            except ImportError:
                self.driver = webdriver.Chrome(options=opts)

            # Remove navigator.webdriver flag via CDP.
            try:
                self.driver.execute_cdp_cmd(
                    'Page.addScriptToEvaluateOnNewDocument',
                    {'source': (
                        'Object.defineProperty(navigator, "webdriver", '
                        '{get: () => undefined})'
                    )},
                )
            except Exception:
                pass  # Not critical if CDP isn't available.

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
            self.driver = None

    def _transfer_cookies(self) -> None:
        """Copy Selenium session cookies into the requests session.

        This allows subsequent requests-based downloads to share
        the authenticated browser context, bypassing Zendesk's
        anti-bot protections for attachment downloads.
        """
        if self.driver is None:
            return
        for cookie in self.driver.get_cookies():
            self.session.cookies.set(
                cookie['name'],
                cookie['value'],
                domain=cookie.get('domain', ''),
            )

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
            incremental: bool = True,
            compute_hashes: bool = True,
        ) -> pd.DataFrame:
        """Build or update a manifest of existing COA PDFs.

        Scans ``pdf_dir`` for PDF files and records their metadata.
        When ``incremental=True``, only catalogs files not already
        present in the existing manifest.

        Args:
            incremental: If True, only catalog new files.
            compute_hashes: If True, compute SHA-256 hashes for
                content-based deduplication.

        Returns:
            DataFrame with columns:
                file_name, sample_id, file_path, file_size,
                file_hash, date_cataloged, source, product_name,
                drop_number, attachment_id
        """
        # Load existing manifest if incremental.
        existing = pd.DataFrame()
        if incremental and self.manifest_path.exists():
            try:
                existing = pd.read_csv(str(self.manifest_path))
                self.logger.info(
                    f'Loaded existing manifest: {len(existing)} entries'
                )
            except Exception as exc:
                self.logger.warning(f'Failed to load manifest: {exc}')

        existing_names = set()
        if len(existing) > 0 and 'file_name' in existing.columns:
            existing_names = set(existing['file_name'].astype(str))

        # Scan the PDF directory.
        pdf_files = sorted([
            f for f in os.listdir(str(self.pdf_dir))
            if f.lower().endswith('.pdf')
        ])
        self.logger.info(
            f'Found {len(pdf_files)} PDF(s) in {self.pdf_dir}'
        )

        new_rows = []
        now = datetime.now().isoformat()
        for f in pdf_files:
            if incremental and f in existing_names:
                continue

            fp = self.pdf_dir / f
            file_size = fp.stat().st_size if fp.exists() else 0

            file_hash = ''
            if compute_hashes and fp.exists():
                sha = hashlib.sha256()
                with open(str(fp), 'rb') as fh:
                    for chunk in iter(lambda: fh.read(65536), b''):
                        sha.update(chunk)
                file_hash = sha.hexdigest()

            sample_id = _extract_sample_id_from_filename(f)

            # Attachment ID: if filename is purely numeric, it IS
            # the Zendesk attachment ID.
            attachment_id = ''
            if re.match(r'^\d+$', sample_id):
                attachment_id = sample_id

            new_rows.append({
                'file_name': f,
                'sample_id': sample_id,
                'file_path': str(fp),
                'file_size': file_size,
                'file_hash': file_hash,
                'date_cataloged': now,
                'source': 'zendesk_support',
                'product_name': '',
                'drop_number': '',
                'attachment_id': attachment_id,
            })

        if new_rows:
            self.logger.info(f'Cataloged {len(new_rows)} new PDF(s)')
            new_df = pd.DataFrame(new_rows)
            manifest = pd.concat([existing, new_df], ignore_index=True)
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

    # ── Phase 2: Scrape Drop Pages ───────────────────────────────

    def _get_page_source(
            self,
            url: str,
            wait_selector: Optional[str] = None,
            wait_timeout: int = 20,
            retries: int = 2,
        ) -> str:
        """Fetch a page's HTML source using Selenium.

        Navigates to the URL and optionally waits for a CSS
        selector to appear in the DOM.  Detects Cloudflare "Just
        a moment..." challenge pages and waits for them to resolve
        before proceeding.

        Args:
            url: The URL to navigate to.
            wait_selector: CSS selector to wait for (optional).
            wait_timeout: Max seconds to wait for the selector.
            retries: Number of retry attempts on failure.

        Returns:
            The page's HTML source string.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        self.driver.get(url)
        time.sleep(2)

        # ── Cloudflare challenge handling ─────────────────────
        # Cloudflare JS challenges show "Just a moment..." as the
        # page title. If detected, poll every 3s for up to 60s
        # waiting for the challenge to auto-resolve.  Do NOT
        # reload — reloading resets the challenge timer.
        cf_wait_total = 0
        cf_max_wait = 60
        while cf_wait_total < cf_max_wait:
            title = self.driver.title or ''
            if 'just a moment' not in title.lower():
                break
            if cf_wait_total == 0:
                self.logger.info(
                    'Cloudflare challenge detected '
                    f'(title="{title}"), waiting for resolution...'
                )
            time.sleep(3)
            cf_wait_total += 3
            if cf_wait_total % 15 == 0:
                self.logger.info(
                    f'  Still waiting for Cloudflare... '
                    f'({cf_wait_total}s / {cf_max_wait}s)'
                )

        if cf_wait_total >= cf_max_wait:
            self.logger.warning(
                'Cloudflare challenge did NOT resolve after '
                f'{cf_max_wait}s.  Try running with --no-headless '
                'to manually pass the challenge.'
            )
            return self.driver.page_source

        if cf_wait_total > 0:
            self.logger.info(
                f'Cloudflare challenge resolved after {cf_wait_total}s'
            )
            # Give the real page a moment to render after challenge.
            time.sleep(3)

        # ── Wait for target selector ─────────────────────────
        for attempt in range(retries + 1):
            if wait_selector:
                try:
                    WebDriverWait(self.driver, wait_timeout).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, wait_selector)
                        )
                    )
                    self.logger.debug(
                        f'Selector "{wait_selector}" found on {url}'
                    )
                except Exception:
                    self.logger.debug(
                        f'Selector "{wait_selector}" NOT found '
                        f'after {wait_timeout}s on {url} '
                        f'(attempt {attempt + 1}/{retries + 1})'
                    )

            html = self.driver.page_source
            page_len = len(html)

            # Debug logging.
            title_tag = ''
            soup_debug = BeautifulSoup(html, 'html.parser')
            if soup_debug.title:
                title_tag = soup_debug.title.get_text(strip=True)
            self.logger.debug(
                f'Page loaded: {url} | '
                f'{page_len} chars | '
                f'title="{title_tag}"'
            )

            # Check if still on Cloudflare (shouldn't happen, but safety net).
            if 'just a moment' in title_tag.lower():
                self.logger.warning(
                    'Still on Cloudflare challenge page. '
                    'Try --no-headless to pass manually.'
                )
                return html

            # Check if selector was found.
            if wait_selector:
                found = soup_debug.select(wait_selector)
                if found:
                    self._respectful_pause(multiplier=0.3)
                    return html

                # Retry: reload and try again.
                if attempt < retries:
                    wait = 5 * (attempt + 1)
                    self.logger.info(
                        f'Retrying in {wait}s... '
                        f'(selector "{wait_selector}" not found)'
                    )
                    time.sleep(wait)
                    self.driver.get(url)
                    time.sleep(3)
                    continue

            self._respectful_pause(multiplier=0.3)
            return html

        return self.driver.page_source

    def _extract_drops_from_html(self, html: str) -> List[Dict]:
        """Extract drop article links from a page's HTML.

        Tries multiple CSS selector strategies in priority order
        to handle Zendesk theme variations:

            1. ``a.article-list-link``  (standard Zendesk)
            2. ``li.article-list-item a`` (list-item wrapper)
            3. ``ul.article-list a[href*="articles"]`` (broad)
            4. Any ``<a>`` whose href contains "articles" and
               whose text mentions "COA" (ultimate fallback)

        Args:
            html: The page's HTML source.

        Returns:
            List of drop dicts with url, title, drop_number,
            drop_date.
        """
        soup = BeautifulSoup(html, 'html.parser')

        # Strategy 1: Standard Zendesk selector.
        links = soup.select('a.article-list-link')
        self.logger.debug(
            f'Selector "a.article-list-link": {len(links)} match(es)'
        )

        # Strategy 2: List-item wrapper.
        if not links:
            links = soup.select('li.article-list-item a')
            self.logger.debug(
                f'Selector "li.article-list-item a": '
                f'{len(links)} match(es)'
            )

        # Strategy 3: Broad article-list.
        if not links:
            links = soup.select('ul.article-list a[href*="articles"]')
            self.logger.debug(
                f'Selector "ul.article-list a[href*=articles]": '
                f'{len(links)} match(es)'
            )

        # Strategy 4: Ultimate fallback — any <a> with "articles"
        # in href and "coa" in text.
        if not links:
            all_links = soup.find_all('a', href=True)
            links = [
                a for a in all_links
                if 'articles' in a.get('href', '')
                and 'coa' in a.get_text(strip=True).lower()
            ]
            self.logger.debug(
                f'Fallback (any a with articles+coa): '
                f'{len(links)} match(es)'
            )

        # Filter to COA-related articles and deduplicate.
        drops = []
        seen_urls = set()
        for link in links:
            title = link.get_text(strip=True)
            href = link.get('href', '')
            if not href or not title:
                continue

            # Only keep COA-related articles.
            if 'coa' not in title.lower():
                continue

            # Build absolute URL.
            if href.startswith('/'):
                href = BASE_URL + href

            if href in seen_urls:
                continue
            seen_urls.add(href)

            drop_number, drop_date = _parse_drop_title(title)
            drops.append({
                'url': href,
                'title': title,
                'drop_number': drop_number,
                'drop_date': drop_date,
            })

        return drops

    def scrape_drop_list(self) -> List[Dict]:
        """Scrape the drop information page for article links.

        Uses ``_get_page_source`` with an explicit wait for the
        article list, then ``_extract_drops_from_html`` with
        multi-strategy CSS selectors to handle Zendesk theme
        variations.

        Falls back to scrolling and retrying if the initial load
        yields no results (Zendesk sometimes lazy-loads content).

        Returns:
            List of dicts with keys:
                url, title, drop_number, drop_date
        """
        drops = []
        current_url = DROPS_URL
        page_num = 0

        while current_url:
            page_num += 1

            # Try with explicit wait for the article list.
            html = self._get_page_source(
                current_url,
                wait_selector='a.article-list-link, '
                              'li.article-list-item, '
                              'ul.article-list',
                wait_timeout=20,
                retries=2,
            )

            page_drops = self._extract_drops_from_html(html)

            # If nothing found, try scrolling to trigger lazy load.
            if not page_drops and self.driver:
                self.logger.info(
                    'No drops found on initial load, '
                    'trying scroll + wait...'
                )
                try:
                    self.driver.execute_script(
                        'window.scrollTo(0, document.body.scrollHeight);'
                    )
                    time.sleep(3)
                    self.driver.execute_script('window.scrollTo(0, 0);')
                    time.sleep(3)
                    html = self.driver.page_source
                    page_drops = self._extract_drops_from_html(html)
                except Exception as exc:
                    self.logger.debug(f'Scroll retry failed: {exc}')

            # If still nothing, dump page info for debugging.
            if not page_drops:
                soup = BeautifulSoup(html, 'html.parser')
                title = soup.title.get_text(strip=True) if soup.title else '(no title)'
                body_len = len(soup.get_text())
                all_links = soup.find_all('a', href=True)
                article_links = [
                    a for a in all_links
                    if 'article' in a.get('href', '').lower()
                ]
                self.logger.warning(
                    f'Page debug: title="{title}", '
                    f'body_text={body_len} chars, '
                    f'total_links={len(all_links)}, '
                    f'article_links={len(article_links)}'
                )
                # Log first few link hrefs for diagnosis.
                for a in article_links[:5]:
                    self.logger.warning(
                        f'  link: {a.get("href")} | '
                        f'text: "{a.get_text(strip=True)[:60]}"'
                    )

            drops.extend(page_drops)

            # Check for next page (Zendesk pagination).
            soup = BeautifulSoup(html, 'html.parser')
            next_link = soup.select_one('a.pagination-next-link')
            if next_link and next_link.get('href'):
                next_href = next_link['href']
                if next_href.startswith('/'):
                    next_href = BASE_URL + next_href
                current_url = next_href
                self.logger.info(
                    f'  Pagination: moving to page {page_num + 1}'
                )
            else:
                current_url = None

        self.logger.info(f'Found {len(drops)} drop article(s)')
        return drops

    def scrape_drop_page(self, drop: Dict) -> List[Dict]:
        """Scrape a single drop article for COA attachment links.

        Parses the Zendesk article page for ``<li class="attachment-item">``
        elements, extracting the attachment URL (by finding ``<a>`` tags
        whose ``href`` contains ``article_attachments``) and the
        product name from the link text.

        Uses explicit waits for the attachment list to render, with
        a fallback to broader selectors.

        Args:
            drop: Dict with ``url``, ``title``, ``drop_number``,
                  ``drop_date``.

        Returns:
            List of dicts with keys:
                attachment_url, attachment_id, product_name,
                drop_number, drop_date, drop_title
        """
        attachments = []
        html = self._get_page_source(
            drop['url'],
            wait_selector='li.attachment-item, '
                          'div.article-attachments, '
                          'ul.attachments',
            wait_timeout=15,
            retries=1,
        )
        soup = BeautifulSoup(html, 'html.parser')

        # Strategy 1: Standard Zendesk attachment items.
        items = soup.select('li.attachment-item')

        # Strategy 2: Broader — any link to article_attachments.
        if not items:
            # Build fake items from all matching links.
            all_attach_links = soup.select(
                'a[href*="article_attachments"]'
            )
            if all_attach_links:
                self.logger.debug(
                    f'Using fallback: found {len(all_attach_links)} '
                    f'attachment links without li.attachment-item'
                )
                for link in all_attach_links:
                    href = link.get('href', '')
                    product_name = link.get_text(strip=True)
                    if product_name.lower().endswith('.pdf'):
                        product_name = product_name[:-4].strip()
                    if href.startswith('/'):
                        href = BASE_URL + href
                    attachment_id = _extract_attachment_id(href)
                    if not attachment_id:
                        continue
                    attachments.append({
                        'attachment_url': href,
                        'attachment_id': attachment_id,
                        'product_name': product_name,
                        'drop_number': drop.get('drop_number'),
                        'drop_date': drop.get('drop_date'),
                        'drop_title': drop.get('title', ''),
                    })

                # Deduplicate by attachment_id.
                seen = set()
                unique = []
                for a in attachments:
                    if a['attachment_id'] not in seen:
                        seen.add(a['attachment_id'])
                        unique.append(a)
                attachments = unique

                self.logger.info(
                    f'  {drop.get("title", "?")}: '
                    f'{len(attachments)} attachment(s)'
                )
                return attachments

        # Parse standard attachment items.
        for item in items:
            link = item.select_one('a[href*="article_attachments"]')
            if not link:
                continue

            href = link.get('href', '')
            product_name = link.get_text(strip=True)

            if product_name.lower().endswith('.pdf'):
                product_name = product_name[:-4].strip()

            if href.startswith('/'):
                href = BASE_URL + href

            attachment_id = _extract_attachment_id(href)

            attachments.append({
                'attachment_url': href,
                'attachment_id': attachment_id,
                'product_name': product_name,
                'drop_number': drop.get('drop_number'),
                'drop_date': drop.get('drop_date'),
                'drop_title': drop.get('title', ''),
            })

        self.logger.info(
            f'  {drop.get("title", "?")}: '
            f'{len(attachments)} attachment(s)'
        )
        return attachments

    def scrape_all_drops(
            self,
            headless: bool = True,
        ) -> pd.DataFrame:
        """Scrape all drops for COA attachment URLs.

        Initializes Selenium, navigates through the drop list,
        then visits each drop article to extract attachment links.

        Args:
            headless: Run browser in headless mode.

        Returns:
            DataFrame with discovered attachment metadata.
        """
        self._init_selenium(headless=headless)

        # Step 1: Get the drop list.
        drops = self.scrape_drop_list()
        if not drops:
            self.logger.warning('No drop articles found')
            return pd.DataFrame()

        # Step 2: Visit each drop and extract attachments.
        all_attachments = []
        for drop in drops:
            attachments = self.scrape_drop_page(drop)
            all_attachments.extend(attachments)
            self._respectful_pause(multiplier=0.5)

        # Transfer cookies for the download phase.
        self._transfer_cookies()

        df = pd.DataFrame(all_attachments)
        self.logger.info(
            f'Total discovered: {len(df)} COA attachment(s) '
            f'across {len(drops)} drop(s)'
        )

        # Save discovered URLs snapshot.
        if len(df) > 0:
            ts = datetime.now().strftime('%Y-%m-%dT%H-%M-%S')
            urls_path = (
                self.datasets_dir
                / f'fl-lab-result-urls-flowery-{ts}.csv'
            )
            df.to_csv(str(urls_path), index=False)
            self.logger.info(f'Saved URL snapshot → {urls_path}')

        return df

    # ── Phase 3: Download New COAs ───────────────────────────────

    def _download_coa(
            self,
            attachment_url: str,
            attachment_id: str,
            max_retries: int = 2,
        ) -> Optional[str]:
        """Download a single COA PDF by attachment URL.

        Attempts to download using the requests session (with
        cookies transferred from the Selenium session).

        Args:
            attachment_url: Full URL to the attachment.
            attachment_id: The Zendesk attachment ID for naming.
            max_retries: Maximum retry attempts.

        Returns:
            Path to the saved PDF, or None on failure.
        """
        outfile = self.pdf_dir / f'{attachment_id}.pdf'

        for attempt in range(max_retries + 1):
            try:
                resp = self.session.get(
                    attachment_url,
                    timeout=60,
                    allow_redirects=True,
                )
                if resp.status_code == 429:
                    wait = min(30 * (2 ** attempt), MAX_BACKOFF)
                    self.logger.warning(
                        f'Rate limited (429), waiting {wait}s...'
                    )
                    time.sleep(wait)
                    continue

                if resp.status_code != 200:
                    self.logger.warning(
                        f'HTTP {resp.status_code} for {attachment_id}'
                    )
                    if attempt < max_retries:
                        time.sleep(5 * (attempt + 1))
                        continue
                    return None

                if not _is_valid_pdf(resp.content):
                    self.logger.warning(
                        f'Invalid PDF for {attachment_id} '
                        f'({len(resp.content)} bytes)'
                    )
                    return None

                with open(str(outfile), 'wb') as f:
                    f.write(resp.content)
                return str(outfile)

            except requests.RequestException as exc:
                self.logger.warning(
                    f'Download error for {attachment_id}: {exc}'
                )
                if attempt < max_retries:
                    time.sleep(5 * (attempt + 1))
                    continue
                return None

        return None

    def download_new_coas(
            self,
            discovered: pd.DataFrame,
            existing_ids: Optional[set] = None,
        ) -> Dict:
        """Download COA PDFs not already in the archive.

        Compares discovered attachment IDs against the existing
        set and downloads only new COAs.

        Args:
            discovered: DataFrame from ``scrape_all_drops()`` with
                ``attachment_id`` and ``attachment_url`` columns.
            existing_ids: Set of already-archived sample IDs.
                If None, scans the PDF directory.

        Returns:
            Stats dict with keys: downloaded, already_exists,
            skipped_no_id, failed.
        """
        stats = {
            'downloaded': 0,
            'already_exists': 0,
            'skipped_no_id': 0,
            'failed': 0,
        }

        if len(discovered) == 0:
            return stats

        # Build existing ID set from filesystem if not provided.
        if existing_ids is None:
            existing_ids = set()
            if self.pdf_dir.exists():
                for f in os.listdir(str(self.pdf_dir)):
                    if f.lower().endswith('.pdf'):
                        existing_ids.add(
                            _extract_sample_id_from_filename(f)
                        )

        self.logger.info(
            f'Existing archive: {len(existing_ids)} sample IDs'
        )

        for _, row in discovered.iterrows():
            attachment_id = str(row.get('attachment_id', ''))
            attachment_url = row.get('attachment_url', '')

            if not attachment_id:
                stats['skipped_no_id'] += 1
                continue

            if attachment_id in existing_ids:
                stats['already_exists'] += 1
                continue

            # Respectful pause between downloads.
            self._respectful_pause(multiplier=1.5)

            result = self._download_coa(attachment_url, attachment_id)
            if result:
                stats['downloaded'] += 1
                existing_ids.add(attachment_id)
                product = row.get('product_name', '')
                self.logger.info(
                    f'Downloaded: {attachment_id} ({product})'
                )
            else:
                stats['failed'] += 1

        self.logger.info(
            f'Download stats: {stats["downloaded"]} new, '
            f'{stats["already_exists"]} existing, '
            f'{stats["failed"]} failed'
        )
        return stats

    # ── Phase 4: Manifest Enrichment ─────────────────────────────

    def _enrich_manifest(
            self,
            manifest: pd.DataFrame,
            discovered: pd.DataFrame,
        ) -> pd.DataFrame:
        """Enrich the manifest with metadata from discovered URLs.

        Matches discovered attachments to manifest entries by
        attachment_id and fills in product_name, drop_number, etc.
        Existing non-empty values are NOT overwritten.

        Args:
            manifest: The catalog manifest.
            discovered: The discovered URLs DataFrame.

        Returns:
            Updated manifest with enriched metadata.
        """
        if 'attachment_id' not in manifest.columns:
            return manifest
        if 'attachment_id' not in discovered.columns:
            return manifest

        # Build lookup from discovered data.
        lookup = {}
        for _, row in discovered.iterrows():
            aid = str(row.get('attachment_id', ''))
            if aid:
                lookup[aid] = {
                    'product_name': row.get('product_name', ''),
                    'drop_number': row.get('drop_number', ''),
                    'drop_date': row.get('drop_date', ''),
                }

        # Enrich manifest rows.
        enriched_count = 0
        for idx, row in manifest.iterrows():
            aid = str(row.get('attachment_id', ''))
            if aid in lookup:
                enriched_count += 1
                for key, val in lookup[aid].items():
                    if key in manifest.columns and not row.get(key):
                        manifest.at[idx, key] = str(val) if val is not None else ''

        # Re-save enriched manifest.
        manifest.to_csv(str(self.manifest_path), index=False)
        self.logger.info(f'Enriched {enriched_count} manifest entries')

        return manifest

    # ── Phase 5: LabResult Conversion ────────────────────────────

    def _convert_to_lab_results(
            self,
            manifest: pd.DataFrame,
        ) -> List[Dict]:
        """Convert the manifest to standardized LabResult records.

        Populates fields knowable from filename, source metadata,
        and any enrichment from the scrape phase. Full cannabinoid/
        terpene/safety data requires downstream COA parsing.

        Args:
            manifest: DataFrame from ``catalog_existing()``.

        Returns:
            List of dicts matching the LabResult schema.
        """
        results = []

        for _, row in manifest.iterrows():
            file_name = str(row.get('file_name', '') or '')
            sample_id = str(row.get('sample_id', '') or '')
            result_id = _generate_result_id(file_name)

            # Safely extract fields, converting NaN → ''.
            product_name = row.get('product_name', '')
            if pd.isna(product_name):
                product_name = ''
            product_name = str(product_name).strip()

            attachment_id = row.get('attachment_id', '')
            if pd.isna(attachment_id):
                attachment_id = ''
            attachment_id = str(attachment_id).strip()

            file_hash = row.get('file_hash', '')
            if pd.isna(file_hash):
                file_hash = ''
            file_hash = str(file_hash).strip()

            # Build COA URL from attachment ID if available.
            coa_url = ''
            if attachment_id:
                coa_url = (
                    f'{BASE_URL}/hc/en-us/article_attachments/'
                    f'{attachment_id}'
                )

            if LabResult is not None:
                result = LabResult(
                    # Identifiers.
                    id=result_id,
                    sample_id=sample_id,
                    # Use file content hash for deduplication —
                    # avoids collisions from records that share
                    # the same (empty) product/batch fields.
                    sample_hash=file_hash or None,

                    # Product info (enriched during parsing).
                    product_name=product_name or None,

                    # Producer info.
                    producer=FLOWERY_PRODUCER.get('producer'),
                    producer_license_number=FLOWERY_PRODUCER.get(
                        'producer_license_number'
                    ),
                    producer_state=FLOWERY_PRODUCER.get('producer_state'),

                    # COA info.
                    coa_url=coa_url or None,
                    lab_results_url=coa_url or None,

                    # Metadata.
                    state='fl',
                    source='flowery',
                    # NOTE: date_collected means "when the sample
                    # was collected for lab testing", NOT when we
                    # cataloged the file.  Leave it as None until
                    # the parsing pipeline extracts it from the COA.
                    data_refreshed_date=datetime.now(),
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
                results.append(result.to_dict())
            else:
                # Fallback without LabResult schema.
                results.append({
                    'id': result_id,
                    'sample_id': sample_id,
                    'sample_hash': file_hash,
                    'product_name': product_name or '',
                    'producer': FLOWERY_PRODUCER.get('producer'),
                    'producer_license_number': FLOWERY_PRODUCER.get(
                        'producer_license_number'
                    ),
                    'producer_state': FLOWERY_PRODUCER.get(
                        'producer_state'
                    ),
                    'coa_url': coa_url,
                    'state': 'fl',
                    'source': 'flowery',
                    'data_refreshed_date': datetime.now().isoformat(),
                })

        return results

    # ── Main Collection Method ───────────────────────────────────

    def get_results(
            self,
            catalog_only: bool = False,
            scrape: bool = True,
            download: bool = True,
            headless: bool = True,
            save_results: bool = True,
        ) -> pd.DataFrame:
        """Collect Flowery COA data.

        Orchestrates the full pipeline:
            1. Catalog existing PDFs into manifest.
            2. Scrape drop pages for new COA URLs (Selenium).
            3. Download only new COA PDFs.
            4. Enrich manifest with scraped metadata.
            5. Convert manifest to LabResult records.

        Args:
            catalog_only: If True, only build the manifest (no
                network requests).
            scrape: If True, scrape drop pages for new URLs.
            download: If True, download discovered COA PDFs.
            headless: Run Selenium in headless mode.
            save_results: If True, save the results CSV.

        Returns:
            DataFrame with standardized lab result records.
        """
        self.logger.info('Starting Flowery COA collection...')
        self.logger.info(f'PDF directory: {self.pdf_dir}')
        self.logger.info(f'Manifest: {self.manifest_path}')

        # Phase 1: Catalog existing archive.
        manifest = self.catalog_existing()

        if catalog_only:
            lab_results = self._convert_to_lab_results(manifest)
            results_df = pd.DataFrame(lab_results)
            if save_results and len(results_df) > 0:
                outpath = (
                    self.datasets_dir / 'fl-results-flowery-latest.csv'
                )
                results_df.to_csv(str(outpath), index=False)
                self.logger.info(
                    f'Results saved: {len(results_df)} → {outpath}'
                )
            self.logger.info(
                f'Manifest contains {len(manifest)} COA PDF(s)'
            )
            return results_df

        # Phase 2: Scrape drop pages.
        discovered = pd.DataFrame()
        if scrape:
            discovered = self.scrape_all_drops(headless=headless)

        # Phase 3: Download new COAs.
        if download and len(discovered) > 0:
            existing_ids = set(
                manifest['sample_id'].astype(str)
            ) if len(manifest) > 0 else set()
            self.download_new_coas(discovered, existing_ids)

            # Re-catalog after downloads.
            manifest = self.catalog_existing()

        # Phase 4: Enrich manifest with discovered metadata.
        if len(discovered) > 0 and len(manifest) > 0:
            manifest = self._enrich_manifest(manifest, discovered)

        # Close Selenium.
        self._quit_driver()

        # Phase 5: Convert to LabResult records.
        lab_results = self._convert_to_lab_results(manifest)
        results_df = pd.DataFrame(lab_results)
        if save_results and len(results_df) > 0:
            outpath = (
                self.datasets_dir / 'fl-results-flowery-latest.csv'
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
            Dict with total_pdfs, total_size_bytes, total_size_gb,
            manifest_exists, manifest_entries.
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
                    pd.read_csv(str(self.manifest_path))
                )
            except Exception:
                pass

        return {
            'total_pdfs': len(pdf_files),
            'total_size_bytes': total_size,
            'total_size_gb': round(total_size / (1024 ** 3), 2),
            'manifest_exists': self.manifest_path.exists(),
            'manifest_entries': manifest_entries,
        }


# ╔══════════════════════════════════════════════════════════════════╗
# ║ CLI Entry Point                                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Collect The Flowery COA PDFs (Florida).',
    )
    parser.add_argument(
        '--pdf-dir',
        default='D:/data/florida/results/pdfs/flowery',
        help='Directory for COA PDFs.',
    )
    parser.add_argument(
        '--data-dir',
        default='D:/data/florida/results',
        help='Base data directory.',
    )
    parser.add_argument(
        '--catalog-only',
        action='store_true',
        help='Only catalog existing PDFs (no network).',
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
        '--no-headless',
        action='store_true',
        help='Show the browser window (visible mode).',
    )
    parser.add_argument(
        '--test',
        action='store_true',
        help='Run inline smoke tests and exit.',
    )
    parser.add_argument(
        '--integration-test',
        action='store_true',
        help='Run integration test (requires Selenium + network).',
    )
    args = parser.parse_args()

    # ── Smoke Tests ──────────────────────────────────────────────
    if args.test:
        print('Running inline smoke tests...')
        # _generate_result_id
        assert _generate_result_id('test.pdf') == _generate_result_id('test.pdf')
        assert len(_generate_result_id('test.pdf')) == 16
        assert _generate_result_id('a.pdf') != _generate_result_id('b.pdf')
        # _extract_attachment_id
        assert _extract_attachment_id(
            '/hc/en-us/article_attachments/44281628412955'
        ) == '44281628412955'
        assert _extract_attachment_id(
            'https://support.theflowery.co/hc/en-us/article_attachments/12345'
        ) == '12345'
        assert _extract_attachment_id('') == ''
        assert _extract_attachment_id(None) == ''
        # _extract_sample_id_from_filename
        assert _extract_sample_id_from_filename('44281628412955.pdf') == '44281628412955'
        assert _extract_sample_id_from_filename('batch-sample.pdf') == 'batch-sample'
        assert _extract_sample_id_from_filename('') == ''
        # _parse_drop_title
        dn, dd = _parse_drop_title('Drop #65 (12/15/25) Product COAs')
        assert dn == 65 and dd == '12/15/25'
        dn2, dd2 = _parse_drop_title('Some Random Title')
        assert dn2 is None and dd2 is None
        # _is_valid_pdf
        assert _is_valid_pdf(b'%PDF-1.4' + b'\x00' * 20000)
        assert not _is_valid_pdf(b'<html>')
        assert not _is_valid_pdf(b'%PDF-1.4' + b'\x00' * 100)
        # _sanitize_filename
        assert ':' not in _sanitize_filename('Test: Name')
        print('✓ All smoke tests passed')
        exit(0)

    # ── Integration Test ─────────────────────────────────────────
    if args.integration_test:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            print('Running integration test...')
            collector = FloweryCollector(
                pdf_dir=os.path.join(tmpdir, 'pdfs'),
                data_dir=tmpdir,
            )
            collector._init_selenium(headless=True)
            drops = collector.scrape_drop_list()
            print(f'  Found {len(drops)} drop(s)')
            if drops:
                attachments = collector.scrape_drop_page(drops[0])
                print(
                    f'  First drop ({drops[0]["title"]}): '
                    f'{len(attachments)} attachment(s)'
                )
            collector._quit_driver()
            print('✓ Integration test passed')
        exit(0)

    # ── Main Collection ──────────────────────────────────────────
    with FloweryCollector(
        pdf_dir=args.pdf_dir,
        data_dir=args.data_dir,
    ) as collector:
        results = collector.get_results(
            catalog_only=args.catalog_only,
            scrape=not args.no_scrape,
            download=not args.no_download,
            headless=not args.no_headless,
        )
        print(f'Total results: {len(results)}')
        stats = collector.archive_stats()
        print(f'Archive stats: {stats}')