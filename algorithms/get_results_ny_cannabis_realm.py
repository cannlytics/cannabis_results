"""
Get Results | New York | Cannabis Realm
Copyright (c) 2025-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 4/1/2025
Updated: 2/28/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Collect cannabis lab result COA PDFs from Cannabis Realm NY,
    a licensed dispensary in New York that publishes product menus
    with lab test data and COA PDF links.

    Cannabis Realm operates a Dutchie-powered online menu at
    cannabisrealmny.com with product cards containing:
      - Product name, brand, and strain type
      - THC/CBD percentages from lab testing
      - Weight, price, and product images
      - Links to COA PDFs on product detail pages

    Collection Strategy:
        Phase 1: Catalog existing local archive of COA PDFs.
        Phase 2: Discover COAs by scraping all menu categories,
                 visiting product detail pages, and extracting
                 COA PDF links.
        Phase 3: Download new COA PDFs via HTTP requests.
        Phase 4: Convert manifest into standardized LabResult records.

    The algorithm scrolls through category pages to load all products,
    extracts product metadata from cards, visits each product page
    to find COA links, and downloads new PDFs. Progress is tracked
    in a JSON state file for resumable collection.

Data Source:
    - [Cannabis Realm NY](https://cannabisrealmny.com/)

Output:
    - PDF directory with COA files
    - Manifest CSV cataloging all collected COAs
    - Products CSV with product metadata
    - Progress JSON for resumable collection
    - Standardized CSV with LabResult schema fields

Usage:
    ```python
    from algorithms.get_results_ny_cannabis_realm import (
        CannabisRealmCollector,
    )

    with CannabisRealmCollector() as collector:
        results = collector.get_results()
    ```

Command Line:
    ```bash
    # Full collection
    python algorithms/get_results_ny_cannabis_realm.py

    # Catalog-only mode (no network requests)
    python algorithms/get_results_ny_cannabis_realm.py --catalog-only

    # Specific categories only
    python algorithms/get_results_ny_cannabis_realm.py \\
        --categories flower pre-rolls vaporizers

    # Custom directories
    python algorithms/get_results_ny_cannabis_realm.py \\
        --pdf-dir "D:/data/new-york/results/pdfs/cannabis-realm" \\
        --data-dir "D:/data/new-york/results"

    # Run unit tests
    python algorithms/get_results_ny_cannabis_realm.py --test
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

# Cannabis Realm base URL.
BASE_URL = 'https://cannabisrealmny.com'

# Cannabis Realm producer metadata.
CANNABIS_REALM_PRODUCER = {
    'producer': 'Cannabis Realm',
    'producer_dba': 'Cannabis Realm NY',
    'producer_state': 'ny',
    'producer_website': BASE_URL,
}

# Menu categories to scrape.
# Core product categories (most likely to have COAs).
CORE_CATEGORIES = [
    '/menu/categories/flower',
    '/menu/categories/pre-rolls',
    '/menu/categories/hash',
    '/menu/categories/vaporizers',
    '/menu/categories/edibles',
    '/menu/categories/tinctures',
    '/menu/categories/concentrates',
    '/menu/categories/beverages',
    '/menu/categories/topicals',
    '/menu/categories/cbd',
]

# Promotional / curated categories (may overlap with core).
PROMO_CATEGORIES = [
    '/menu/categories/spring-into-savings',
    '/menu/categories/skyworld-new-drops',
    '/menu/categories/superdope',
    '/menu/categories/new-flower',
    '/menu/categories/knock-out-deal',
    '/menu/categories/support-small-farmers',
    '/menu/categories/concentrates-deal-1g-under-25',
    '/menu/categories/buy-5-and-pay-for-4-revert-pre-rolls',
    '/menu/categories/woman-owned-brands',
    '/menu/categories/small-batch-premium-cannabis',
    '/menu/categories/staff-picks-flower-favorites',
    '/menu/categories/staff-picks-vapes',
    '/menu/categories/staff-pick-pre-rolls',
    '/menu/categories/staff-picks-edibles',
    '/menu/categories/heavy-weight',
    '/menu/categories/sleepy-time',
    '/menu/categories/indoor-flower',
]

# All categories combined.
ALL_CATEGORIES = CORE_CATEGORIES + PROMO_CATEGORIES

# Strain type map for indica/sativa percentages.
STRAIN_TYPE_MAP = {
    'HYBRID': {'indica_percentage': 0.5, 'sativa_percentage': 0.5},
    'INDICA': {'indica_percentage': 1.0, 'sativa_percentage': 0.0},
    'SATIVA': {'indica_percentage': 0.0, 'sativa_percentage': 1.0},
    'HYBRID-INDICA': {
        'indica_percentage': 0.75, 'sativa_percentage': 0.25,
    },
    'HYBRID-SATIVA': {
        'indica_percentage': 0.25, 'sativa_percentage': 0.75,
    },
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

# Default pause between page loads (seconds).
DEFAULT_PAGE_PAUSE = 2.0

# Default pause between PDF downloads (seconds).
DEFAULT_DOWNLOAD_PAUSE = 3.0

# Maximum number of scroll attempts per category page.
MAX_SCROLLS = 20

# Maximum back-off wait (seconds).
MAX_BACKOFF = 120

# Module-level logger.
logger = logging.getLogger('get_results_ny_cannabis_realm')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Helper Functions                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

def _generate_result_id(key: str) -> str:
    """Generate a deterministic 16-char hex ID.

    Args:
        key: Input string (URL, batch ID, etc.).

    Returns:
        16-character hex string.
    """
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]


def _hash_url(url: str) -> str:
    """Generate a 12-char hex hash from a URL for deduplication.

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


def _extract_percentage(text: str) -> Optional[float]:
    """Extract a percentage value from text like "24.5%".

    Args:
        text: Text containing a percentage.

    Returns:
        Float percentage value, or None.
    """
    if not text:
        return None
    try:
        return float(text.replace('%', '').strip())
    except (ValueError, TypeError):
        return None


def _extract_weight_grams(text: str) -> Optional[float]:
    """Extract weight in grams from text like "3.5g" or "1/8oz".

    Args:
        text: Text containing a weight.

    Returns:
        Weight in grams, or None.
    """
    if not text:
        return None
    text_lower = text.lower().strip()

    # Grams (e.g., "3.5g", "1g").
    if 'g' in text_lower and 'oz' not in text_lower:
        try:
            weight_str = text_lower.split('g')[0].strip()
            if '/' in weight_str:
                num, denom = weight_str.split('/')
                return float(num) / float(denom)
            return float(weight_str)
        except (ValueError, IndexError, ZeroDivisionError):
            return None

    # Ounces (e.g., "1oz", "1/8oz").
    if 'oz' in text_lower:
        try:
            weight_str = text_lower.split('oz')[0].strip()
            if '/' in weight_str:
                num, denom = weight_str.split('/')
                return float(num) / float(denom) * 28.35
            return float(weight_str) * 28.35
        except (ValueError, IndexError, ZeroDivisionError):
            return None

    return None


def _price_to_float(price_str: str) -> Optional[float]:
    """Convert a price string to float.

    Args:
        price_str: Price like "$45.00".

    Returns:
        Float price value, or None.
    """
    if not price_str:
        return None
    try:
        return float(price_str.replace('$', '').replace(',', '').strip())
    except (ValueError, TypeError):
        return None


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


def _is_coa_link(href: str, text: str) -> bool:
    """Determine if a link points to a COA PDF.

    Args:
        href: The link's href attribute.
        text: The link's visible text.

    Returns:
        True if this appears to be a COA link.
    """
    if not href:
        return False
    href_lower = href.lower()
    text_lower = (text or '').lower()

    # Direct PDF link with COA-related keywords.
    if '.pdf' in href_lower:
        return True
    coa_keywords = ['coa', 'lab', 'test', 'certificate', 'analysis']
    return any(kw in href_lower or kw in text_lower for kw in coa_keywords)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Progress Tracker                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

class ProgressTracker:
    """Tracks collection progress for resumable sessions.

    Saves and loads state to a JSON file so collection can span
    multiple sessions.

    Attributes:
        path: Path to the progress JSON file.
        state: The current state dictionary.
    """

    def __init__(self, path: str):
        """Initialize the progress tracker.

        Args:
            path: Path to the JSON state file.
        """
        self.path = Path(path)
        self.state = self._load()

    def _load(self) -> Dict:
        """Load state from disk, or return defaults."""
        if self.path.exists():
            try:
                with open(str(self.path), 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {
            'scraped_categories': [],
            'scraped_product_urls': [],
            'discovered_coa_urls': {},
            'downloaded_pdfs': [],
            'total_products_found': 0,
            'total_coas_found': 0,
            'total_coas_downloaded': 0,
            'started_at': None,
            'last_updated': None,
        }

    def save(self) -> None:
        """Persist current state to disk."""
        self.state['last_updated'] = datetime.now().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(self.path), 'w') as f:
            json.dump(self.state, f, indent=2)

    @property
    def scraped_categories(self) -> set:
        """Set of already-scraped category paths."""
        return set(self.state.get('scraped_categories', []))

    @property
    def scraped_product_urls(self) -> set:
        """Set of already-visited product URLs."""
        return set(self.state.get('scraped_product_urls', []))

    @property
    def discovered_coas(self) -> Dict[str, str]:
        """Dict mapping product_url → coa_url."""
        return self.state.get('discovered_coa_urls', {})

    @property
    def downloaded(self) -> set:
        """Set of already-downloaded COA URLs."""
        return set(self.state.get('downloaded_pdfs', []))

    def record_category(self, category: str) -> None:
        """Record that a category has been fully scraped."""
        if category not in self.state['scraped_categories']:
            self.state['scraped_categories'].append(category)

    def record_product_visited(self, url: str) -> None:
        """Record that a product page was visited."""
        if url not in self.state['scraped_product_urls']:
            self.state['scraped_product_urls'].append(url)

    def record_coa_discovered(
            self, product_url: str, coa_url: str,
        ) -> None:
        """Record a discovered COA URL."""
        self.state['discovered_coa_urls'][product_url] = coa_url
        self.state['total_coas_found'] = len(
            self.state['discovered_coa_urls'],
        )

    def record_download(self, coa_url: str) -> None:
        """Record a successful PDF download."""
        if coa_url not in self.state['downloaded_pdfs']:
            self.state['downloaded_pdfs'].append(coa_url)
        self.state['total_coas_downloaded'] = len(
            self.state['downloaded_pdfs'],
        )


# ╔══════════════════════════════════════════════════════════════════╗
# ║ CannabisRealmCollector                                           ║
# ╚══════════════════════════════════════════════════════════════════╝

class CannabisRealmCollector:
    """Collector for Cannabis Realm NY COA PDFs.

    Scrapes the Cannabis Realm dispensary menu to discover products,
    visits product detail pages to find COA links, downloads COA PDFs,
    and converts to standardized LabResult records.

    The collector follows the 4-phase pipeline:
        1. Catalog existing local PDFs
        2. Discover COAs from dispensary menu
        3. Download new COA PDFs
        4. Convert manifest to LabResult records

    Attributes:
        pdf_dir: Directory where COA PDFs are stored.
        data_dir: Base data directory for datasets and outputs.
        datasets_dir: Directory for CSV outputs.
        manifest_path: Path to the manifest CSV.
        progress_path: Path to the progress JSON.
        products_path: Path to the products CSV.
        page_pause: Seconds between page loads.
        download_pause: Seconds between PDF downloads.
        session: Requests session for downloads.
        driver: Selenium WebDriver instance.
    """

    def __init__(
            self,
            pdf_dir: str = '',
            data_dir: str = '',
            page_pause: float = DEFAULT_PAGE_PAUSE,
            download_pause: float = DEFAULT_DOWNLOAD_PAUSE,
            verbose: bool = True,
        ):
        """Initialize the Cannabis Realm collector.

        Args:
            pdf_dir: Directory for COA PDFs.
            data_dir: Base data directory.
            page_pause: Seconds between page loads.
            download_pause: Seconds between PDF downloads.
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
            pdf_dir = os.path.join(data_dir, 'pdfs', 'cannabis-realm')

        self.pdf_dir = Path(pdf_dir)
        self.data_dir = Path(data_dir)
        self.datasets_dir = self.data_dir / 'datasets'
        self.manifest_path = (
            self.datasets_dir / 'cannabis-realm-manifest.csv'
        )
        self.progress_path = (
            self.datasets_dir / 'cannabis-realm-progress.json'
        )
        self.products_path = (
            self.datasets_dir / 'cannabis-realm-products.csv'
        )
        self.page_pause = page_pause
        self.download_pause = download_pause
        self.verbose = verbose

        # Logging.
        self.logger = logging.getLogger(
            'get_results_ny_cannabis_realm',
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
        """Initialize a Selenium WebDriver with anti-detection.

        Args:
            headless: Run browser without visible window.
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
            opts.add_argument('--window-size=1920,1080')
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

            # Remove navigator.webdriver flag.
            try:
                self.driver.execute_cdp_cmd(
                    'Page.addScriptToEvaluateOnNewDocument',
                    {'source': (
                        'Object.defineProperty(navigator,'
                        '"webdriver",{get:()=>undefined})'
                    )},
                )
            except Exception:
                pass

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
        """Catalog all existing COA PDFs in the archive.

        Scans the PDF directory for valid PDF files, computes
        SHA-256 content hashes for deduplication, and creates
        a manifest DataFrame.

        Args:
            save: Save manifest to disk.

        Returns:
            DataFrame with manifest columns.
        """
        self.logger.info(
            'Cataloging existing PDFs in %s', self.pdf_dir,
        )
        records = []
        seen_hashes = set()

        # Find all PDF files.
        pdf_files = sorted(self.pdf_dir.glob('*.pdf'))
        self.logger.info('Found %d PDF(s)', len(pdf_files))

        for pdf_path in pdf_files:
            if not _is_valid_pdf_file(str(pdf_path)):
                self.logger.debug(
                    'Skipping invalid PDF: %s', pdf_path.name,
                )
                continue

            # Compute content hash for deduplication.
            with open(str(pdf_path), 'rb') as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            if file_hash in seen_hashes:
                self.logger.debug(
                    'Duplicate: %s', pdf_path.name,
                )
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
            self.manifest_path.parent.mkdir(
                parents=True, exist_ok=True,
            )
            manifest.to_csv(
                str(self.manifest_path), index=False,
            )
            self.logger.info(
                'Manifest saved: %d entries → %s',
                len(manifest), self.manifest_path,
            )

        return manifest

    # ── Phase 2: Discover ────────────────────────────────────────

    def _scroll_to_load_all(
            self,
            wait_time: float = 1.0,
            max_scrolls: int = MAX_SCROLLS,
        ) -> None:
        """Scroll the page to load all lazy-loaded products.

        Scrolls to the bottom of the page and clicks "Load More"
        buttons if present, until no new content appears.

        Args:
            wait_time: Seconds to wait after each scroll.
            max_scrolls: Maximum scroll attempts.
        """
        from selenium.common.exceptions import NoSuchElementException
        prev_height = self.driver.execute_script(
            'return document.body.scrollHeight',
        )
        for _ in range(max_scrolls):
            self.driver.execute_script(
                'window.scrollTo(0, document.body.scrollHeight);',
            )
            time.sleep(wait_time)
            new_height = self.driver.execute_script(
                'return document.body.scrollHeight',
            )
            if new_height == prev_height:
                # Try clicking a "load more" button.
                try:
                    btn = self.driver.find_element(
                        By.CSS_SELECTOR, '.show-more-button',
                    )
                    btn.click()
                    time.sleep(wait_time)
                except (NoSuchElementException, Exception):
                    break
            prev_height = new_height

    def _scrape_category(
            self,
            category_path: str,
        ) -> List[Dict]:
        """Scrape all product cards from a category page.

        Args:
            category_path: Category URL path (e.g., "/menu/categories/flower").

        Returns:
            List of product metadata dictionaries.
        """
        from selenium.webdriver.common.by import By
        from selenium.common.exceptions import NoSuchElementException

        url = f'{BASE_URL}{category_path}'
        category_name = category_path.split('/')[-1]
        self.logger.info('Scraping category: %s', url)
        self.driver.get(url)
        time.sleep(self.page_pause)

        # Scroll to load all products.
        self._scroll_to_load_all()

        # Parse product cards.
        products = []
        cards = self.driver.find_elements(
            By.CSS_SELECTOR, '.card-inner',
        )
        self.logger.info(
            'Found %d product cards in %s',
            len(cards), category_name,
        )

        for card in cards:
            try:
                product = self._parse_product_card(
                    card, category_name,
                )
                if product:
                    products.append(product)
            except Exception as e:
                self.logger.debug(
                    'Error parsing card: %s', str(e),
                )
                continue

        return products

    def _parse_product_card(
            self,
            card,
            category: str,
        ) -> Optional[Dict]:
        """Extract product metadata from a Dutchie product card.

        Args:
            card: Selenium WebElement for the product card.
            category: Category name for this product.

        Returns:
            Product metadata dictionary, or None.
        """
        from selenium.webdriver.common.by import By
        from selenium.common.exceptions import NoSuchElementException

        def _get_text(selector, attr=None):
            try:
                el = card.find_element(By.CSS_SELECTOR, selector)
                if attr:
                    return el.get_attribute(attr)
                return el.text.strip()
            except NoSuchElementException:
                return None

        # Product name.
        product_name = _get_text(
            "[data-testid^='product-name-']",
        )
        if not product_name:
            return None

        # Brand / producer.
        brand = _get_text(
            "[data-testid^='product-card-brand-name-']",
        )

        # Strain type.
        strain_type_text = _get_text(
            "[data-testid^='product-card-cannabis-type-tag-']",
        )
        strain_type = (strain_type_text or 'HYBRID').upper()

        # THC percentage.
        total_thc = None
        try:
            cannabinoid_line = card.find_element(
                By.CSS_SELECTOR,
                "[data-testid='product-card-cannabinoid-line']",
            )
            thc_el = cannabinoid_line.find_element(
                By.XPATH, "./div[contains(text(), 'THC')]",
            )
            thc_text = thc_el.text
            if 'THC' in thc_text:
                total_thc = _extract_percentage(
                    thc_text.split('THC')[1].strip(),
                )
        except Exception:
            pass

        # CBD percentage.
        total_cbd = None
        try:
            cannabinoid_line = card.find_element(
                By.CSS_SELECTOR,
                "[data-testid='product-card-cannabinoid-line']",
            )
            cbd_el = cannabinoid_line.find_element(
                By.XPATH, "./div[contains(text(), 'CBD')]",
            )
            cbd_text = cbd_el.text
            if 'CBD' in cbd_text:
                total_cbd = _extract_percentage(
                    cbd_text.split('CBD')[1].strip(),
                )
        except Exception:
            pass

        # Weight.
        weight = _extract_weight_grams(
            _get_text("[data-testid^='variant-weight-']"),
        )

        # Price.
        price = _price_to_float(
            _get_text("[data-testid^='variant-price-']"),
        )

        # Product URL.
        product_url = _get_text(
            "a[data-testid='product-card-menu-link-body']",
            attr='href',
        )

        # Image URL.
        image_url = _get_text('img', attr='src')

        # Strain info.
        strain_info = STRAIN_TYPE_MAP.get(
            strain_type, STRAIN_TYPE_MAP['HYBRID'],
        )

        # Generate product ID.
        product_id = _generate_result_id(
            f'{product_name}|{brand}|{total_thc or ""}',
        )

        return {
            'product_id': product_id,
            'product_name': product_name,
            'brand': brand,
            'category': category,
            'strain_type': strain_type,
            'indica_percentage': strain_info['indica_percentage'],
            'sativa_percentage': strain_info['sativa_percentage'],
            'total_thc': total_thc,
            'total_cbd': total_cbd,
            'product_weight_g': weight,
            'total_price': price,
            'product_url': product_url,
            'product_image_url': image_url,
        }

    def _discover_coa_on_product_page(
            self,
            product_url: str,
        ) -> Optional[str]:
        """Visit a product detail page and find the COA link.

        Looks for links containing COA/lab/test keywords or
        pointing to PDF files.

        Args:
            product_url: Full URL of the product page.

        Returns:
            COA URL if found, else None.
        """
        from selenium.webdriver.common.by import By

        try:
            self.driver.get(product_url)
            time.sleep(self.page_pause)

            links = self.driver.find_elements(By.TAG_NAME, 'a')
            for link in links:
                try:
                    href = link.get_attribute('href') or ''
                    text = link.text or ''
                    if _is_coa_link(href, text):
                        return href
                except Exception:
                    continue
        except Exception as e:
            self.logger.debug(
                'Error visiting product page %s: %s',
                product_url, str(e),
            )
        return None

    def discover_coas(
            self,
            headless: bool = True,
            categories: Optional[List[str]] = None,
            resume: bool = True,
        ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Discover COAs by scraping the Cannabis Realm menu.

        Scrapes all menu categories, deduplicates products by
        product_id, visits each product page to find COA links,
        and downloads any new COA PDFs.

        Args:
            headless: Run browser headlessly.
            categories: Specific categories to scrape (default: all).
            resume: Resume from previous progress.

        Returns:
            Tuple of (products_df, coas_df).
        """
        from selenium.webdriver.common.by import By

        self._init_selenium(headless=headless)

        # Load progress.
        progress = ProgressTracker(str(self.progress_path))
        if not resume:
            progress.state = progress._load.__func__(progress)

        if not progress.state.get('started_at'):
            progress.state['started_at'] = (
                datetime.now().isoformat()
            )

        cats = categories or ALL_CATEGORIES

        # ── Step 1: Scrape products from categories ──────────────
        all_products = []
        for cat in cats:
            if resume and cat in progress.scraped_categories:
                self.logger.info(
                    'Skipping already-scraped category: %s', cat,
                )
                continue

            try:
                products = self._scrape_category(cat)
                all_products.extend(products)
                progress.record_category(cat)
                progress.state['total_products_found'] = (
                    progress.state.get('total_products_found', 0)
                    + len(products)
                )
                progress.save()
            except Exception as e:
                self.logger.error(
                    'Error scraping %s: %s', cat, str(e),
                )
                continue

        # Deduplicate products by product_id.
        seen_ids = set()
        unique_products = []
        for p in all_products:
            pid = p.get('product_id')
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                unique_products.append(p)

        self.logger.info(
            'Discovered %d unique products across %d categories',
            len(unique_products), len(cats),
        )

        # Save products.
        products_df = pd.DataFrame(unique_products)
        if len(products_df) > 0:
            products_df.to_csv(
                str(self.products_path), index=False,
            )

        # ── Step 2: Visit product pages for COA links ────────────
        coa_records = []
        for product in unique_products:
            product_url = product.get('product_url')
            if not product_url:
                continue
            if resume and product_url in progress.scraped_product_urls:
                # Re-load previously discovered COA.
                if product_url in progress.discovered_coas:
                    coa_records.append({
                        **product,
                        'coa_url': progress.discovered_coas[product_url],
                    })
                continue

            coa_url = self._discover_coa_on_product_page(product_url)
            progress.record_product_visited(product_url)

            if coa_url:
                progress.record_coa_discovered(product_url, coa_url)
                coa_records.append({**product, 'coa_url': coa_url})
                self.logger.info(
                    'COA found: %s → %s',
                    product.get('product_name', '?'), coa_url,
                )
            else:
                self.logger.debug(
                    'No COA: %s',
                    product.get('product_name', '?'),
                )

            progress.save()
            time.sleep(
                self.page_pause + random.uniform(0.5, 1.5),
            )

        coas_df = pd.DataFrame(coa_records)
        self.logger.info(
            'Found %d products with COA links', len(coa_records),
        )

        return products_df, coas_df

    # ── Phase 3: Download ────────────────────────────────────────

    def download_coas(
            self,
            coas_df: pd.DataFrame,
            resume: bool = True,
        ) -> int:
        """Download COA PDFs from discovered URLs.

        Args:
            coas_df: DataFrame with 'coa_url' and 'product_id' columns.
            resume: Skip already-downloaded URLs.

        Returns:
            Number of new PDFs downloaded.
        """
        if coas_df is None or len(coas_df) == 0:
            return 0

        progress = ProgressTracker(str(self.progress_path))
        downloaded_count = 0

        for _, row in coas_df.iterrows():
            coa_url = row.get('coa_url')
            if not coa_url:
                continue

            # Skip already downloaded.
            if resume and coa_url in progress.downloaded:
                continue

            product_id = row.get(
                'product_id',
                _hash_url(coa_url),
            )
            filename = f'{product_id}.pdf'
            filepath = self.pdf_dir / filename

            try:
                response = self.session.get(
                    coa_url, timeout=30,
                )
                if response.status_code != 200:
                    self.logger.warning(
                        'HTTP %d: %s',
                        response.status_code, coa_url,
                    )
                    continue

                if not _is_valid_pdf(response.content):
                    self.logger.warning(
                        'Invalid PDF (size=%d): %s',
                        len(response.content), coa_url,
                    )
                    continue

                with open(str(filepath), 'wb') as f:
                    f.write(response.content)

                progress.record_download(coa_url)
                progress.save()
                downloaded_count += 1
                self.logger.info(
                    'Downloaded: %s → %s', coa_url, filename,
                )

            except Exception as e:
                self.logger.error(
                    'Download error: %s → %s', coa_url, str(e),
                )

            time.sleep(
                self.download_pause + random.uniform(0.5, 1.5),
            )

        self.logger.info(
            'Downloaded %d new COA PDF(s)', downloaded_count,
        )
        return downloaded_count

    # ── Phase 4: Convert ─────────────────────────────────────────

    def _convert_to_lab_results(
            self,
            manifest: pd.DataFrame,
            products_df: Optional[pd.DataFrame] = None,
        ) -> List[Dict]:
        """Convert manifest entries to standardized LabResult records.

        Args:
            manifest: DataFrame with manifest columns.
            products_df: Optional product metadata for enrichment.

        Returns:
            List of LabResult dictionaries.
        """
        # Build product lookup by file_name → product data.
        product_lookup = {}
        if products_df is not None and len(products_df) > 0:
            for _, row in products_df.iterrows():
                pid = row.get('product_id', '')
                if pid:
                    product_lookup[f'{pid}.pdf'] = row.to_dict()

        results = []
        for _, row in manifest.iterrows():
            file_name = str(row.get('file_name', ''))
            file_hash = str(row.get('file_hash', ''))

            # Look up product metadata.
            product = product_lookup.get(file_name, {})
            product_name = product.get(
                'product_name',
                file_name.replace('.pdf', '').replace('-', ' '),
            )
            brand = product.get('brand', '')
            coa_url = product.get('coa_url', '')

            result_id = _generate_result_id(
                coa_url if coa_url else file_hash,
            )

            record = {
                'result_id': result_id,
                'sample_id': _hash_url(file_name),
                'product_name': product_name,
                'product_type': product.get('category', ''),
                'strain_name': product_name,
                'strain_type': product.get('strain_type', ''),
                'total_thc': product.get('total_thc'),
                'total_cbd': product.get('total_cbd'),
                'date_tested': '',
                'lab': '',
                'lab_address': '',
                'lab_license_number': '',
                'lab_results_url': coa_url,
                'coa_pdf': file_name,
                'source': 'cannabis_realm',
                'source_url': BASE_URL,
                'state': 'ny',
                **CANNABIS_REALM_PRODUCER,
                'producer': brand or CANNABIS_REALM_PRODUCER['producer'],
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
            discover: bool = True,
            download: bool = True,
            headless: bool = True,
            categories: Optional[List[str]] = None,
            resume: bool = True,
        ) -> pd.DataFrame:
        """Run the full collection pipeline.

        Args:
            catalog_only: Only catalog existing PDFs (no network).
            discover: Run discovery phase.
            download: Download discovered PDFs.
            headless: Run browser headlessly.
            categories: Specific categories to scrape.
            resume: Resume from previous progress.

        Returns:
            DataFrame of standardized LabResult records.
        """
        self.logger.info('Starting Cannabis Realm COA collection...')
        self.logger.info('PDF directory: %s', self.pdf_dir)
        self.logger.info('Manifest: %s', self.manifest_path)

        # Phase 1: Catalog existing PDFs.
        manifest = self.catalog_existing()

        if catalog_only:
            results = self._convert_to_lab_results(manifest)
            df = pd.DataFrame(results)
            if len(df) > 0:
                output_path = (
                    self.datasets_dir
                    / 'cannabis-realm-results.csv'
                )
                df.to_csv(str(output_path), index=False)
            return df

        # Phase 2: Discover COAs.
        products_df = pd.DataFrame()
        coas_df = pd.DataFrame()
        if discover:
            try:
                products_df, coas_df = self.discover_coas(
                    headless=headless,
                    categories=categories,
                    resume=resume,
                )
            finally:
                self._quit_driver()

        # Phase 3: Download new COA PDFs.
        if download and len(coas_df) > 0:
            self.download_coas(coas_df, resume=resume)

        # Re-catalog after downloads.
        manifest = self.catalog_existing()

        # Phase 4: Convert to LabResult records.
        # Merge products for enrichment.
        if self.products_path.exists():
            try:
                products_df = pd.read_csv(
                    str(self.products_path),
                )
            except Exception:
                pass

        results = self._convert_to_lab_results(
            manifest, products_df,
        )
        df = pd.DataFrame(results)

        if len(df) > 0:
            output_path = (
                self.datasets_dir / 'cannabis-realm-results.csv'
            )
            df.to_csv(str(output_path), index=False)
            self.logger.info(
                'Results saved: %d records → %s',
                len(df), output_path,
            )

        self.logger.info(
            '✓ Cannabis Realm collection complete: '
            '%d results', len(df),
        )
        return df

    # ── Archive Stats ────────────────────────────────────────────

    def archive_stats(self) -> Dict:
        """Return summary statistics for the local archive.

        Returns:
            Dictionary with archive statistics.
        """
        pdf_count = 0
        total_size = 0
        for f in self.pdf_dir.glob('*.pdf'):
            if _is_valid_pdf_file(str(f)):
                pdf_count += 1
                total_size += f.stat().st_size

        # Load progress if available.
        progress_data = {}
        if self.progress_path.exists():
            try:
                with open(str(self.progress_path), 'r') as f:
                    progress_data = json.load(f)
            except Exception:
                pass

        return {
            'source': 'cannabis_realm',
            'state': 'ny',
            'total_pdfs': pdf_count,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'categories_scraped': len(
                progress_data.get('scraped_categories', []),
            ),
            'products_found': progress_data.get(
                'total_products_found', 0,
            ),
            'coas_found': progress_data.get(
                'total_coas_found', 0,
            ),
            'coas_downloaded': progress_data.get(
                'total_coas_downloaded', 0,
            ),
        }


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Inline Unit Tests                                                ║
# ╚══════════════════════════════════════════════════════════════════╝

def _run_tests():
    """Run inline unit tests."""
    import tempfile

    print('Running unit tests...')

    # Test _generate_result_id.
    assert len(_generate_result_id('test')) == 16
    assert _generate_result_id('a') == _generate_result_id('a')
    assert _generate_result_id('a') != _generate_result_id('b')
    print('  ✓ _generate_result_id')

    # Test _hash_url.
    assert len(_hash_url('https://example.com')) == 12
    assert _hash_url('x') == _hash_url('x')
    print('  ✓ _hash_url')

    # Test _is_valid_pdf.
    assert _is_valid_pdf(b'%PDF-' + b'\x00' * 10240)
    assert not _is_valid_pdf(b'<html>')
    assert not _is_valid_pdf(b'%PDF-short')
    print('  ✓ _is_valid_pdf')

    # Test _extract_percentage.
    assert _extract_percentage('24.5%') == 24.5
    assert _extract_percentage('0.1%') == 0.1
    assert _extract_percentage(None) is None
    assert _extract_percentage('abc') is None
    print('  ✓ _extract_percentage')

    # Test _extract_weight_grams.
    assert _extract_weight_grams('3.5g') == 3.5
    assert _extract_weight_grams('1g') == 1.0
    assert abs(_extract_weight_grams('1oz') - 28.35) < 0.01
    assert _extract_weight_grams(None) is None
    assert _extract_weight_grams('abc') is None
    print('  ✓ _extract_weight_grams')

    # Test _price_to_float.
    assert _price_to_float('$45.00') == 45.0
    assert _price_to_float('$1,299') == 1299.0
    assert _price_to_float(None) is None
    print('  ✓ _price_to_float')

    # Test _sanitize_filename.
    assert _sanitize_filename('Hello World!') == 'Hello-World'
    assert _sanitize_filename('') == 'unknown'
    print('  ✓ _sanitize_filename')

    # Test _is_coa_link.
    assert _is_coa_link('https://example.com/coa.pdf', '')
    assert _is_coa_link('https://example.com/report', 'lab results')
    assert not _is_coa_link('', '')
    assert not _is_coa_link('https://example.com/shop', 'Buy now')
    print('  ✓ _is_coa_link')

    # Test ProgressTracker.
    with tempfile.TemporaryDirectory() as td:
        pt = ProgressTracker(os.path.join(td, 'progress.json'))
        assert len(pt.scraped_categories) == 0
        pt.record_category('/menu/categories/flower')
        assert '/menu/categories/flower' in pt.scraped_categories
        pt.record_coa_discovered('http://prod', 'http://coa.pdf')
        assert pt.discovered_coas['http://prod'] == 'http://coa.pdf'
        pt.save()
        pt2 = ProgressTracker(os.path.join(td, 'progress.json'))
        assert '/menu/categories/flower' in pt2.scraped_categories
    print('  ✓ ProgressTracker')

    # Test CannabisRealmCollector initialization.
    with tempfile.TemporaryDirectory() as td:
        c = CannabisRealmCollector(
            pdf_dir=os.path.join(td, 'pdfs'),
            data_dir=os.path.join(td, 'data'),
        )
        assert c.pdf_dir.exists()
        assert c.datasets_dir.exists()
        manifest = c.catalog_existing()
        assert len(manifest) == 0
    print('  ✓ CannabisRealmCollector initialization')

    # Test catalog with mock PDFs.
    with tempfile.TemporaryDirectory() as td:
        pdf_dir = os.path.join(td, 'pdfs')
        os.makedirs(pdf_dir)
        # Create a valid mock PDF.
        with open(os.path.join(pdf_dir, 'test.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4' + b'\x00' * 10300)

        c = CannabisRealmCollector(
            pdf_dir=pdf_dir,
            data_dir=os.path.join(td, 'data'),
        )
        manifest = c.catalog_existing(save=False)
        assert len(manifest) == 1
        assert manifest.iloc[0]['file_name'] == 'test.pdf'
    print('  ✓ Catalog with mock PDFs')

    print('✓ All unit tests passed')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ CLI Entry Point                                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Cannabis Realm NY COA Collector',
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
        help='Only catalog existing PDFs (no network)',
    )
    parser.add_argument(
        '--no-download', action='store_true',
        help='Discover COAs but skip downloading',
    )
    parser.add_argument(
        '--categories', nargs='+', default=None,
        help='Specific category slugs to scrape',
    )
    parser.add_argument(
        '--headless', action='store_true', default=True,
        help='Run browser headlessly',
    )
    parser.add_argument(
        '--no-headless', action='store_true',
        help='Show browser window',
    )
    parser.add_argument(
        '--resume', action='store_true', default=True,
        help='Resume from previous progress',
    )
    parser.add_argument(
        '--fresh', action='store_true',
        help='Start fresh (ignore progress)',
    )
    parser.add_argument(
        '--test', action='store_true',
        help='Run inline unit tests',
    )
    args = parser.parse_args()

    if args.test:
        _run_tests()
        raise SystemExit(0)

    # Map category slugs to paths if provided.
    categories = None
    if args.categories:
        categories = [
            f'/menu/categories/{c}' if not c.startswith('/')
            else c
            for c in args.categories
        ]

    headless = args.headless and not args.no_headless
    resume = args.resume and not args.fresh

    with CannabisRealmCollector(
        pdf_dir=args.pdf_dir,
        data_dir=args.data_dir,
    ) as collector:
        results = collector.get_results(
            catalog_only=args.catalog_only,
            discover=True,
            download=not args.no_download,
            headless=headless,
            categories=categories,
            resume=resume,
        )
        print(f'\nResults: {len(results)} records')