"""
Get Results | Arizona | Flow Distribution
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 8/24/2024
Updated: 2/28/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Collect cannabis lab result COA PDFs from Flow Distribution in
    Arizona via their WordPress-based COA archive at
    flowdistribution.com.

    Flow Distribution publishes COA results as individual WordPress
    posts, searchable via the site's search functionality. Each post
    links to a COA PDF or contains embedded results. The site uses
    an age gate that requires date-of-birth confirmation before
    content is accessible.

    Age Gate Automation:
        The Flow Distribution website requires users to confirm
        they are 21+ via a date-of-birth input. This collector
        automatically bypasses the age gate by entering the
        configured birth date (default: 04/12/1992) into the
        age verification form.

    Collection Strategy:
        Phase 1: Catalog existing local archive of COA PDFs.
        Phase 2: Scrape Flow Distribution via search queries to
                 discover COA post URLs and download PDFs.
        Phase 3: Download new COA PDFs not already cached.
        Phase 4: Convert manifest into standardized LabResult records.

    Search Strategy:
        The collector systematically searches 3-digit numeric
        combinations (000-999) to discover COA posts. These
        queries match batch numbers, lot numbers, and other
        numeric identifiers in the COA post titles.

Data Sources:
    - [Flow Distribution](https://flowdistribution.com/)
    - COA posts: https://flowdistribution.com/?s={query}

Output:
    - PDF directory with COA files (named by URL hash)
    - Manifest CSV cataloging all collected COAs
    - Discovered URLs CSV (per-scrape snapshot)
    - Standardized CSV with LabResult schema fields

Usage:
    ```python
    from algorithms.get_results_az_flow_distribution import FlowDistributionCollector

    with FlowDistributionCollector() as collector:
        results = collector.get_results()
    ```

Command Line:
    ```bash
    # Full collection
    python algorithms/get_results_az_flow_distribution.py

    # Catalog-only mode
    python algorithms/get_results_az_flow_distribution.py --catalog-only

    # Custom search queries
    python algorithms/get_results_az_flow_distribution.py --digits 3

    # Run unit tests
    python algorithms/get_results_az_flow_distribution.py --test

    # Run integration test
    python algorithms/get_results_az_flow_distribution.py --integration-test
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
from urllib.parse import quote_plus

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

# Flow Distribution base URL.
BASE_URL = 'https://flowdistribution.com/'

# Flow Distribution producer metadata.
FLOW_PRODUCER = {
    'producer': 'Flow Distribution',
    'producer_dba': 'Flow Distribution',
    'producer_state': 'az',
    'producer_website': 'https://flowdistribution.com',
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

# Default birth date for age gate (MM/DD/YYYY).
DEFAULT_BIRTH_DATE = '04/12/1992'

# Default pauses (seconds).
DEFAULT_SEARCH_PAUSE = 5.0
DEFAULT_DOWNLOAD_PAUSE = 2.0

# Default search digit count.
DEFAULT_DIGITS = 3

# Default maximum queries per session.
DEFAULT_MAX_QUERIES = 1_000

# Age gate timeout (seconds).
AGE_GATE_TIMEOUT = 15

# Module-level logger.
logger = logging.getLogger('get_results_az_flow_distribution')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Helper Functions                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

def _generate_result_id(url: str) -> str:
    """Generate a deterministic 16-char hex ID from a URL."""
    return hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]


def _hash_url(url: str) -> str:
    """Generate an MD5 hash of a URL."""
    return hashlib.md5(url.encode('utf-8')).hexdigest()


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


def _generate_search_queries(digits: int = DEFAULT_DIGITS) -> List[str]:
    """Generate shuffled numeric search queries.

    Args:
        digits: Number of digits (3 = 000-999, 4 = 0000-9999).

    Returns:
        Shuffled list of zero-padded numeric strings.
    """
    queries = [str(i).zfill(digits) for i in range(10 ** digits)]
    random.shuffle(queries)
    return queries


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Progress Tracker                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

class ProgressTracker:
    """Track search progress for resumable collection sessions.

    Stores state in a JSON file so the collection can span
    multiple sessions without re-searching already-queried terms.

    Attributes:
        path: Path to the progress JSON file.
        state: Current progress state dictionary.
    """

    def __init__(self, path: str):
        self.path = path
        self.state = self._load()

    def _load(self) -> Dict:
        """Load existing state or return defaults."""
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {
            'searched_queries': [],
            'found_coas': {},
            'total_queries': 0,
            'total_found': 0,
            'started_at': None,
            'last_query': None,
        }

    def save(self) -> None:
        """Persist the current state to disk."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump(self.state, f, indent=2)

    @property
    def searched(self) -> set:
        """Set of already-searched queries."""
        return set(self.state.get('searched_queries', []))

    @property
    def found(self) -> Dict[str, Dict]:
        """Dict mapping url_hash → metadata."""
        return self.state.get('found_coas', {})

    def record_search(
            self,
            query: str,
            results: Optional[List[Dict]] = None,
        ) -> None:
        """Record the result of a search query."""
        if query not in self.state['searched_queries']:
            self.state['searched_queries'].append(query)
        self.state['last_query'] = query
        self.state['total_queries'] = len(
            self.state['searched_queries'],
        )
        if results:
            for item in results:
                url_hash = item.get('url_hash', '')
                if url_hash:
                    self.state['found_coas'][url_hash] = item
            self.state['total_found'] = len(
                self.state['found_coas'],
            )


# ╔══════════════════════════════════════════════════════════════════╗
# ║ FlowDistributionCollector                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

class FlowDistributionCollector:
    """Collector for Flow Distribution COA PDFs in Arizona.

    Discovers COAs by searching the WordPress-based archive with
    numeric queries, downloads COA PDFs from post pages, and
    converts them to standardized LabResult records.

    Includes automated age gate bypass using date-of-birth entry.

    Attributes:
        pdf_dir: Directory where COA PDFs are stored.
        data_dir: Base data directory for datasets and outputs.
        datasets_dir: Directory for CSV outputs.
        manifest_path: Path to the manifest CSV.
        progress_path: Path to the progress JSON.
        birth_date: Date of birth for age gate (MM/DD/YYYY).
        search_pause: Seconds between search queries.
        download_pause: Seconds between PDF downloads.
        session: Requests session for downloads.
        driver: Selenium WebDriver instance (initialized on demand).
    """

    def __init__(
            self,
            pdf_dir: str = '',
            data_dir: str = '',
            birth_date: str = DEFAULT_BIRTH_DATE,
            search_pause: float = DEFAULT_SEARCH_PAUSE,
            download_pause: float = DEFAULT_DOWNLOAD_PAUSE,
            verbose: bool = True,
        ):
        """Initialize the Flow Distribution collector.

        Args:
            pdf_dir: Directory for COA PDFs.
            data_dir: Base data directory.
            birth_date: Date of birth for age gate (MM/DD/YYYY).
            search_pause: Seconds between search queries.
            download_pause: Seconds between PDF downloads.
            verbose: Enable verbose logging.
        """
        if not data_dir:
            if PATHS:
                data_dir = str(PATHS.state_dir('az'))
            else:
                data_dir = 'D:/data/arizona/results'
        if not pdf_dir:
            pdf_dir = os.path.join(data_dir, 'pdfs', 'flow-distribution')

        self.pdf_dir = Path(pdf_dir)
        self.data_dir = Path(data_dir)
        self.datasets_dir = self.data_dir / 'datasets'
        self.manifest_path = self.datasets_dir / 'flow-distribution-manifest.csv'
        self.progress_path = self.datasets_dir / 'flow-distribution-progress.json'
        self.birth_date = birth_date
        self.search_pause = search_pause
        self.download_pause = download_pause
        self.verbose = verbose

        # Parse birth date components.
        parts = birth_date.split('/')
        self.birth_month = parts[0] if len(parts) >= 1 else '04'
        self.birth_day = parts[1] if len(parts) >= 2 else '12'
        self.birth_year = parts[2] if len(parts) >= 3 else '1992'

        # Logging.
        self.logger = logging.getLogger(
            'get_results_az_flow_distribution',
        )
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
        """Initialize a Selenium WebDriver with anti-detection."""
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
            opts.add_experimental_option(
                'useAutomationExtension', False,
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

            # Remove navigator.webdriver flag.
            try:
                self.driver.execute_cdp_cmd(
                    'Page.addScriptToEvaluateOnNewDocument',
                    {'source': (
                        'Object.defineProperty(navigator, '
                        '"webdriver", {get: () => undefined})'
                    )},
                )
            except Exception:
                pass

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

    def _is_session_alive(self) -> bool:
        """Check if the Selenium session is still usable."""
        if self.driver is None:
            return False
        try:
            _ = self.driver.title
            return True
        except Exception:
            return False

    # ── Rate Limiting ────────────────────────────────────────────

    def _respectful_pause(
            self,
            base: Optional[float] = None,
            multiplier: float = 1.0,
        ) -> None:
        """Sleep with jitter for respectful rate limiting."""
        if base is None:
            base = self.search_pause
        jitter = random.uniform(0, base * 0.3)
        wait = base * multiplier + jitter
        time.sleep(wait)

    # ── Age Gate ─────────────────────────────────────────────────

    def _bypass_age_gate(self) -> bool:
        """Bypass the Flow Distribution age gate automatically.

        The age gate typically appears as a form requiring the user
        to enter their date of birth. This method tries multiple
        strategies to fill in the form and submit.

        Strategies:
            1. Fill date-of-birth input fields (month/day/year).
            2. Click submit/enter/verify buttons.
            3. Use JavaScript to set cookies or bypass directly.

        Returns:
            True if the age gate was successfully bypassed.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        self.logger.info('Navigating to Flow Distribution...')
        self.driver.get(BASE_URL)
        time.sleep(5)

        page_source = self.driver.page_source.lower()

        # Check if age gate is present.
        age_gate_indicators = [
            'age-gate', 'age_gate', 'agegate',
            'age verification', 'verify your age',
            'are you 21', 'are you over',
            'date of birth', 'enter your age',
        ]
        has_age_gate = any(
            indicator in page_source
            for indicator in age_gate_indicators
        )

        if not has_age_gate:
            self.logger.info(
                'No age gate detected, site already accessible'
            )
            return True

        self.logger.info('Age gate detected, attempting bypass...')

        # Strategy 1: Find and fill date inputs.
        try:
            success = self._fill_age_gate_form()
            if success:
                time.sleep(3)
                new_source = self.driver.page_source.lower()
                if not any(i in new_source for i in age_gate_indicators):
                    self.logger.info('✓ Age gate bypassed via form fill')
                    return True
        except Exception as exc:
            self.logger.debug(f'Form fill strategy failed: {exc}')

        # Strategy 2: Set age verification cookie via JS.
        try:
            self.driver.execute_script("""
                document.cookie = 'age_verified=1; path=/; max-age=86400';
                document.cookie = 'age-verified=1; path=/; max-age=86400';
                document.cookie = 'is_legal=1; path=/; max-age=86400';
                document.cookie = 'birthdate=1992-04-12; path=/; max-age=86400';
                localStorage.setItem('age_verified', 'true');
                localStorage.setItem('agegate_passed', 'true');
            """)
            self.driver.refresh()
            time.sleep(5)
            new_source = self.driver.page_source.lower()
            if not any(i in new_source for i in age_gate_indicators):
                self.logger.info('✓ Age gate bypassed via cookie')
                return True
        except Exception as exc:
            self.logger.debug(f'Cookie strategy failed: {exc}')

        # Strategy 3: Click all "Enter" / "Yes" / "Submit" buttons.
        try:
            success = self._click_enter_buttons()
            if success:
                time.sleep(3)
                new_source = self.driver.page_source.lower()
                if not any(i in new_source for i in age_gate_indicators):
                    self.logger.info('✓ Age gate bypassed via button click')
                    return True
        except Exception as exc:
            self.logger.debug(f'Button click strategy failed: {exc}')

        self.logger.warning(
            'Age gate bypass failed after all strategies. '
            'You may need to run with --no-headless and '
            'manually confirm the age gate.'
        )
        return False

    def _fill_age_gate_form(self) -> bool:
        """Attempt to fill the age gate date-of-birth form.

        Looks for input fields related to month, day, and year
        and fills them with the configured birth date.

        Returns:
            True if at least one form field was filled and submitted.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        filled = False

        # Common selectors for age gate forms.
        # Many age gate plugins use these patterns.
        selectors_month = [
            'input[name*="month"]', 'input[placeholder*="MM"]',
            'input[id*="month"]', 'select[name*="month"]',
            'input[name*="mm"]', '#month',
        ]
        selectors_day = [
            'input[name*="day"]', 'input[placeholder*="DD"]',
            'input[id*="day"]', 'select[name*="day"]',
            'input[name*="dd"]', '#day',
        ]
        selectors_year = [
            'input[name*="year"]', 'input[placeholder*="YYYY"]',
            'input[id*="year"]', 'select[name*="year"]',
            'input[name*="yyyy"]', '#year',
        ]

        # Try to fill month.
        for sel in selectors_month:
            try:
                elements = self.driver.find_elements(
                    By.CSS_SELECTOR, sel,
                )
                for el in elements:
                    if el.is_displayed():
                        el.clear()
                        el.send_keys(self.birth_month)
                        filled = True
                        self.logger.debug(f'Filled month: {sel}')
                        break
                if filled:
                    break
            except Exception:
                continue

        # Try to fill day.
        for sel in selectors_day:
            try:
                elements = self.driver.find_elements(
                    By.CSS_SELECTOR, sel,
                )
                for el in elements:
                    if el.is_displayed():
                        el.clear()
                        el.send_keys(self.birth_day)
                        filled = True
                        self.logger.debug(f'Filled day: {sel}')
                        break
                if filled:
                    break
            except Exception:
                continue

        # Try to fill year.
        for sel in selectors_year:
            try:
                elements = self.driver.find_elements(
                    By.CSS_SELECTOR, sel,
                )
                for el in elements:
                    if el.is_displayed():
                        el.clear()
                        el.send_keys(self.birth_year)
                        filled = True
                        self.logger.debug(f'Filled year: {sel}')
                        break
                if filled:
                    break
            except Exception:
                continue

        # Try a single combined date input field.
        if not filled:
            combined_selectors = [
                'input[type="date"]',
                'input[name*="birth"]',
                'input[id*="birth"]',
                'input[placeholder*="date"]',
            ]
            for sel in combined_selectors:
                try:
                    elements = self.driver.find_elements(
                        By.CSS_SELECTOR, sel,
                    )
                    for el in elements:
                        if el.is_displayed():
                            el.clear()
                            # Try ISO format first (YYYY-MM-DD).
                            el.send_keys(
                                f'{self.birth_year}-'
                                f'{self.birth_month}-'
                                f'{self.birth_day}'
                            )
                            filled = True
                            self.logger.debug(
                                f'Filled combined date: {sel}'
                            )
                            break
                    if filled:
                        break
                except Exception:
                    continue

        if filled:
            # Try to submit the form.
            self._click_enter_buttons()

        return filled

    def _click_enter_buttons(self) -> bool:
        """Click submit/enter/verify buttons on the age gate.

        Returns:
            True if a button was clicked.
        """
        from selenium.webdriver.common.by import By

        button_texts = [
            'enter', 'submit', 'verify', 'yes',
            'confirm', 'i am 21', 'i\'m 21',
            'i am over 21', 'continue',
        ]

        # Try buttons.
        for tag in ['button', 'input[type="submit"]', 'a']:
            try:
                elements = self.driver.find_elements(
                    By.CSS_SELECTOR, tag,
                )
                for el in elements:
                    try:
                        text = el.text.strip().lower()
                        value = (
                            el.get_attribute('value') or ''
                        ).strip().lower()
                        if any(
                            bt in text or bt in value
                            for bt in button_texts
                        ):
                            if el.is_displayed():
                                try:
                                    el.click()
                                except Exception:
                                    self.driver.execute_script(
                                        'arguments[0].click();', el,
                                    )
                                self.logger.debug(
                                    f'Clicked age gate button: "{text}"'
                                )
                                return True
                    except Exception:
                        continue
            except Exception:
                continue

        return False

    # ── Phase 1: Catalog Existing Archive ────────────────────────

    def catalog_existing(
            self,
            incremental: bool = True,
            compute_hashes: bool = True,
        ) -> pd.DataFrame:
        """Build or update a manifest of existing COA PDFs."""
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
                'source': 'flow_distribution',
                'coa_url': '',
                'retail_name': '',
                'date': '',
            })

        if new_rows:
            self.logger.info(f'Cataloged {len(new_rows)} new PDF(s)')
            new_df = pd.DataFrame(new_rows)
            manifest = pd.concat(
                [existing, new_df], ignore_index=True,
            )
        else:
            manifest = existing if len(existing) > 0 else pd.DataFrame()

        # Deduplication.
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

        if len(manifest) > 0:
            manifest.to_csv(str(self.manifest_path), index=False)
            self.logger.info(
                f'Manifest saved: {len(manifest)} entries'
            )

        return manifest

    # ── Phase 2: Discover & Download COAs ────────────────────────

    def discover_coas(
            self,
            search_queries: Optional[List[str]] = None,
            digits: int = DEFAULT_DIGITS,
            max_queries: int = DEFAULT_MAX_QUERIES,
            resume: bool = True,
            headless: bool = True,
        ) -> pd.DataFrame:
        """Discover and download COAs by searching the site.

        For each search query, the collector:
        1. Loads the search results page.
        2. Parses COA post titles, dates, and URLs.
        3. Downloads the COA content (PDF or page).

        Args:
            search_queries: Custom list of queries (overrides digits).
            digits: Number of digits for auto-generated queries.
            max_queries: Maximum queries per session.
            resume: If True, skip already-searched queries.
            headless: Run browser in headless mode.

        Returns:
            DataFrame with discovered COA metadata.
        """
        from selenium.webdriver.common.by import By

        # Initialize progress tracker.
        tracker = ProgressTracker(str(self.progress_path))
        if not tracker.state.get('started_at'):
            tracker.state['started_at'] = datetime.now().isoformat()

        # Generate search queries.
        if search_queries is None:
            search_queries = _generate_search_queries(digits)

        # Initialize Selenium and bypass age gate.
        self._init_selenium(headless=headless)
        if not self._bypass_age_gate():
            self.logger.warning(
                'Age gate bypass may have failed. '
                'Attempting to continue...'
            )

        # Search for COAs.
        discovered = []
        queries_this_session = 0

        self.logger.info(
            f'Starting COA discovery: {len(search_queries)} queries, '
            f'max {max_queries} per session'
        )
        if resume:
            already = len(tracker.searched)
            self.logger.info(
                f'Resuming: {already} queries already searched, '
                f'{len(tracker.found)} COAs found so far'
            )

        for query in search_queries:
            # Check session limits.
            if queries_this_session >= max_queries:
                self.logger.info(
                    f'Reached session limit of {max_queries} queries'
                )
                break

            # Skip already-searched queries.
            if resume and query in tracker.searched:
                continue

            # Check browser health.
            if not self._is_session_alive():
                self.logger.warning(
                    'Dead session detected, reinitializing...'
                )
                tracker.save()
                self._quit_driver()
                time.sleep(2)
                self._init_selenium(headless=headless)
                if not self._bypass_age_gate():
                    self.logger.error(
                        'Failed to reinitialize. Stopping.'
                    )
                    break

            # Perform the search.
            search_url = f'{BASE_URL}?s={quote_plus(query)}'
            try:
                self.driver.get(search_url)
            except Exception as exc:
                self.logger.warning(f'Navigation error: {exc}')
                tracker.record_search(query)
                queries_this_session += 1
                continue

            self._respectful_pause(
                base=self.search_pause, multiplier=0.5,
            )

            # Parse search results.
            posts = self.driver.find_elements(
                By.CLASS_NAME, 'wp-block-post',
            )
            self.logger.info(
                f'Query "{query}": {len(posts)} results'
            )

            query_results = []
            for post in posts:
                try:
                    # Extract date.
                    date = ''
                    try:
                        date_el = post.find_element(
                            By.CLASS_NAME, 'wp-block-post-date',
                        )
                        time_el = date_el.find_element(
                            By.TAG_NAME, 'time',
                        )
                        date = time_el.get_attribute('datetime')
                    except Exception:
                        pass

                    # Extract title and URL.
                    title_el = post.find_element(
                        By.CLASS_NAME, 'wp-block-post-title',
                    )
                    link = title_el.find_element(By.TAG_NAME, 'a')
                    retail_name = link.text.strip()
                    coa_url = link.get_attribute('href')

                    if not coa_url:
                        continue

                    url_hash = _hash_url(coa_url)
                    item = {
                        'url_hash': url_hash,
                        'coa_url': coa_url,
                        'retail_name': retail_name,
                        'date': date,
                        'query': query,
                        'discovered_at': datetime.now().isoformat(),
                    }

                    # Download the COA PDF.
                    destination = str(
                        self.pdf_dir / f'{url_hash}.pdf'
                    )
                    if not os.path.exists(destination):
                        try:
                            response = self.session.get(
                                coa_url,
                                allow_redirects=True,
                                timeout=30,
                            )
                            if response.status_code == 200:
                                content = response.content
                                if _is_valid_pdf(content):
                                    with open(destination, 'wb') as f:
                                        f.write(content)
                                    self.logger.info(
                                        f'Downloaded: {retail_name}'
                                    )
                                else:
                                    self.logger.debug(
                                        f'Not a PDF: {retail_name}'
                                    )
                        except Exception as e:
                            self.logger.debug(
                                f'Download error for '
                                f'{retail_name}: {e}'
                            )

                    query_results.append(item)
                    discovered.append(item)

                except Exception as e:
                    self.logger.debug(
                        f'Error processing post: {e}'
                    )

            tracker.record_search(query, query_results)
            queries_this_session += 1

            # Save progress every 20 queries.
            if queries_this_session % 20 == 0:
                tracker.save()
                self.logger.info(
                    f'Progress: {queries_this_session} queries, '
                    f'{len(discovered)} new COAs found '
                    f'({len(tracker.found)} total)'
                )

            self._respectful_pause()

        # Final save.
        tracker.save()

        # Close Selenium.
        self._quit_driver()

        # Build DataFrame.
        df = pd.DataFrame(discovered)
        self.logger.info(
            f'Discovery complete: {queries_this_session} queries, '
            f'{len(discovered)} new COAs this session '
            f'({len(tracker.found)} total)'
        )

        # Save snapshot.
        if len(df) > 0:
            ts = datetime.now().strftime('%Y-%m-%dT%H-%M-%S')
            urls_path = (
                self.datasets_dir
                / f'az-lab-result-urls-flow-distribution-{ts}.csv'
            )
            df.to_csv(str(urls_path), index=False)
            self.logger.info(f'Saved URL snapshot → {urls_path}')

        return df

    # ── Phase 4: Convert to LabResult Records ────────────────────

    def _convert_to_lab_results(
            self,
            manifest: pd.DataFrame,
        ) -> List[Dict]:
        """Convert manifest entries to standardized LabResult records."""
        results = []
        for _, row in manifest.iterrows():
            url_hash = str(row.get('url_hash', ''))
            coa_url = str(row.get('coa_url', ''))
            retail_name = str(row.get('retail_name', ''))
            date = str(row.get('date', ''))
            file_name = str(row.get('file_name', ''))

            result_id = _generate_result_id(
                coa_url if coa_url else url_hash,
            )

            record = {
                'result_id': result_id,
                'sample_id': url_hash,
                'product_name': retail_name,
                'product_type': '',
                'date_tested': date,
                'lab': '',
                'lab_results_url': coa_url,
                'coa_pdf': file_name,
                'source': 'flow_distribution',
                'source_url': BASE_URL,
                'state': 'az',
                **FLOW_PRODUCER,
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
            digits: int = DEFAULT_DIGITS,
            max_queries: int = DEFAULT_MAX_QUERIES,
            resume: bool = True,
            search_queries: Optional[List[str]] = None,
        ) -> pd.DataFrame:
        """Execute the full collection pipeline.

        Args:
            catalog_only: If True, only build the manifest.
            discover: If True, run search enumeration.
            download: If True, download PDFs during discovery.
            headless: Run Selenium in headless mode.
            save_results: If True, save the results CSV.
            digits: Number of digits for search queries.
            max_queries: Maximum queries per session.
            resume: If True, skip already-searched queries.
            search_queries: Custom list of search queries.

        Returns:
            DataFrame with standardized lab result records.
        """
        self.logger.info(
            'Starting Flow Distribution COA collection...'
        )

        # Phase 1: Catalog existing archive.
        manifest = self.catalog_existing()

        if catalog_only:
            lab_results = self._convert_to_lab_results(manifest)
            results_df = pd.DataFrame(lab_results)
            if save_results and len(results_df) > 0:
                outpath = (
                    self.datasets_dir
                    / 'az-results-flow-distribution-latest.csv'
                )
                results_df.to_csv(str(outpath), index=False)
            return results_df

        # Phase 2 + 3: Discover and download COAs.
        discovered = pd.DataFrame()
        if discover:
            discovered = self.discover_coas(
                search_queries=search_queries,
                digits=digits,
                max_queries=max_queries,
                resume=resume,
                headless=headless,
            )

        # Re-catalog after downloads.
        manifest = self.catalog_existing()

        # Enrich manifest with discovered metadata.
        if len(discovered) > 0 and len(manifest) > 0:
            url_map = dict(zip(
                discovered['url_hash'].astype(str),
                discovered['coa_url'].astype(str),
            ))
            name_map = dict(zip(
                discovered['url_hash'].astype(str),
                discovered['retail_name'].astype(str),
            ))
            date_map = dict(zip(
                discovered['url_hash'].astype(str),
                discovered['date'].astype(str),
            ))
            manifest['coa_url'] = manifest['url_hash'].map(
                url_map,
            ).fillna(manifest.get('coa_url', ''))
            manifest['retail_name'] = manifest['url_hash'].map(
                name_map,
            ).fillna(manifest.get('retail_name', ''))
            manifest['date'] = manifest['url_hash'].map(
                date_map,
            ).fillna(manifest.get('date', ''))

        # Phase 4: Convert to LabResult records.
        lab_results = self._convert_to_lab_results(manifest)
        results_df = pd.DataFrame(lab_results)
        if save_results and len(results_df) > 0:
            outpath = (
                self.datasets_dir
                / 'az-results-flow-distribution-latest.csv'
            )
            results_df.to_csv(str(outpath), index=False)
            self.logger.info(
                f'Results saved: {len(results_df)} → {outpath}'
            )

        self.logger.info(f'Total results: {len(results_df)}')
        return results_df

    # ── Archive Stats ────────────────────────────────────────────

    def archive_stats(self) -> Dict:
        """Compute summary statistics for the local archive."""
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
        progress = {}
        if self.progress_path.exists():
            tracker = ProgressTracker(str(self.progress_path))
            progress = {
                'total_queries': tracker.state.get('total_queries', 0),
                'total_found': tracker.state.get('total_found', 0),
                'last_query': tracker.state.get('last_query'),
            }
        return {
            'total_pdfs': len(pdf_files),
            'total_size_bytes': total_size,
            'total_size_mb': round(total_size / (1024 ** 2), 2),
            'discovery_progress': progress,
        }


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Unit Tests                                                       ║
# ╚══════════════════════════════════════════════════════════════════╝

def run_unit_tests():
    """Run inline smoke tests."""
    import tempfile

    print('Running unit tests...')

    # _generate_result_id
    assert _generate_result_id('test') == _generate_result_id('test')
    assert len(_generate_result_id('test')) == 16
    print('  ✓ _generate_result_id')

    # _hash_url
    assert len(_hash_url('test')) == 32
    print('  ✓ _hash_url')

    # _is_valid_pdf
    assert _is_valid_pdf(b'%PDF-1.4' + b'\x00' * 10000)
    assert not _is_valid_pdf(b'<html>')
    print('  ✓ _is_valid_pdf')

    # _generate_search_queries
    queries = _generate_search_queries(2)
    assert len(queries) == 100
    assert all(len(q) == 2 for q in queries)
    assert '00' in queries and '99' in queries
    print('  ✓ _generate_search_queries')

    # ProgressTracker
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, 'test-progress.json')
        tracker = ProgressTracker(path)
        assert tracker.state['total_queries'] == 0
        tracker.record_search('001', [
            {'url_hash': 'abc', 'coa_url': 'https://example.com/1'},
        ])
        tracker.record_search('002')
        tracker.save()
        assert '001' in tracker.searched
        assert '002' in tracker.searched
        assert 'abc' in tracker.found

        # Reload.
        tracker2 = ProgressTracker(path)
        assert '001' in tracker2.searched
        assert 'abc' in tracker2.found
    print('  ✓ ProgressTracker')

    # FlowDistributionCollector initialization
    with tempfile.TemporaryDirectory() as tmpdir:
        collector = FlowDistributionCollector(
            pdf_dir=os.path.join(tmpdir, 'pdfs'),
            data_dir=tmpdir,
        )
        assert collector.pdf_dir.exists()
        assert collector.birth_month == '04'
        assert collector.birth_day == '12'
        assert collector.birth_year == '1992'

        manifest = collector.catalog_existing()
        assert len(manifest) == 0

        results = collector._convert_to_lab_results(manifest)
        assert results == []

        stats = collector.archive_stats()
        assert stats['total_pdfs'] == 0
    print('  ✓ FlowDistributionCollector initialization')

    print('✓ All unit tests passed')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ CLI Entry Point                                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            'Collect Flow Distribution COA PDFs (Arizona).'
        ),
    )
    parser.add_argument(
        '--pdf-dir',
        default='D:/data/arizona/results/pdfs/flow-distribution',
    )
    parser.add_argument(
        '--data-dir',
        default='D:/data/arizona/results',
    )
    parser.add_argument(
        '--birth-date',
        default=DEFAULT_BIRTH_DATE,
        help='Date of birth for age gate (MM/DD/YYYY).',
    )
    parser.add_argument(
        '--catalog-only',
        action='store_true',
    )
    parser.add_argument(
        '--no-discover',
        action='store_true',
    )
    parser.add_argument(
        '--no-download',
        action='store_true',
    )
    parser.add_argument(
        '--digits',
        type=int,
        default=DEFAULT_DIGITS,
    )
    parser.add_argument(
        '--max-queries',
        type=int,
        default=DEFAULT_MAX_QUERIES,
    )
    parser.add_argument(
        '--resume',
        action='store_true',
        default=True,
    )
    parser.add_argument(
        '--no-resume',
        action='store_true',
    )
    parser.add_argument(
        '--no-headless',
        action='store_true',
    )
    parser.add_argument('--test', action='store_true')
    parser.add_argument('--integration-test', action='store_true')
    args = parser.parse_args()

    if args.test:
        run_unit_tests()
        exit(0)

    if args.integration_test:
        print('Integration test: run manually with --no-headless')
        exit(0)

    with FlowDistributionCollector(
        pdf_dir=args.pdf_dir,
        data_dir=args.data_dir,
        birth_date=args.birth_date,
    ) as collector:
        results = collector.get_results(
            catalog_only=args.catalog_only,
            discover=not args.no_discover,
            download=not args.no_download,
            headless=not args.no_headless,
            digits=args.digits,
            max_queries=args.max_queries,
            resume=not args.no_resume,
        )
        print(f'Total results: {len(results)}')
        stats = collector.archive_stats()
        print(f'Archive stats: {stats}')
