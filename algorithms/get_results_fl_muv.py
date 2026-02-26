"""
Get Results | Florida | MÜV (Verano)
Copyright (c) 2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2/24/2026
Updated: 2/24/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Collect cannabis lab result COA PDFs from MÜV (Verano Holdings)
    in Florida via their COA portal at muvfl.com/coa-portal.

    MÜV publishes COA results through a batch-number lookup portal
    powered by Treefersoft. The portal requires:
      1. An age gate (location selection) before accessing the search.
      2. A batch ID search that matches against the END of the
         full Metrc tag (e.g., searching "1521" matches
         ``1A40303000017D5000071521``).
      3. Each successful search returns exactly one COA PDF link
         hosted at ``treefersoft.com/LabReports/LabReportQR/``.

    This collector systematically discovers COAs by enumerating
    numeric suffixes against the search portal, then downloads
    the corresponding COA PDFs from Treefersoft.

    Batch ID Format (Florida Metrc Tag):
        ``1A40303000017D5000071521``  (24 characters)
        ├─ ``1A4``        → Metrc state prefix (Florida)
        ├─ ``030300001``  → License/entity identifier
        ├─ ``7D5``        → Tag type code
        └─ ``000071521``  → Sequential identifier

    The search portal matches from the END of the batch ID, so
    searching "71521" uniquely identifies this tag, while "1521"
    would match any tag ending in those digits.

    Collection Strategy:
        Phase 1: Catalog existing local archive of COA PDFs.
        Phase 2: Discover COAs by enumerating numeric suffixes
                 via the MÜV search portal (Selenium-driven).
        Phase 3: Download new COA PDFs from Treefersoft.
        Phase 4: Convert manifest into standardized LabResult records.

    The algorithm includes resumption support — progress is tracked
    in a JSON state file so collection can span multiple sessions.
    With ~10-second delays between queries, a full 4-digit sweep
    (10,000 queries) takes approximately 28 hours.

    NOTE: COAs are only available for products produced after
    July 2025, per the portal notice.

Data Sources:
    - [MÜV COA Portal](https://muvfl.com/coa-portal)
    - COA PDFs: https://www.treefersoft.com/LabReports/LabReportQR/
    - Age gate: Location selection via ``verano.selectLocation(id)``

Output:
    - PDF directory with COA files (named by batch ID)
    - Manifest CSV cataloging all collected COAs
    - Discovered URLs CSV (per-search snapshot)
    - Progress JSON for resumable collection
    - Standardized CSV with LabResult schema fields

Usage:
    ```python
    from algorithms.get_results_fl_muv import MuvCollector

    with MuvCollector() as collector:
        results = collector.get_results()
    ```

Command Line:
    ```bash
    # Full collection: catalog + discover + download + produce results
    python algorithms/get_results_fl_muv.py

    # Catalog-only mode (no network requests)
    python algorithms/get_results_fl_muv.py --catalog-only

    # Discovery only — find COAs but don't download PDFs
    python algorithms/get_results_fl_muv.py --no-download

    # Custom suffix range (e.g., 5-digit suffixes 00000-99999)
    python algorithms/get_results_fl_muv.py --digits 5

    # Resume from last position
    python algorithms/get_results_fl_muv.py --resume

    # Limit number of queries per session
    python algorithms/get_results_fl_muv.py --max-queries 500

    # Custom directories
    python algorithms/get_results_fl_muv.py \\
        --pdf-dir "D:/data/florida/results/pdfs/muv" \\
        --data-dir "D:/data/florida/results"

    # Run unit tests
    python algorithms/get_results_fl_muv.py --test

    # Run integration test (Selenium + network required)
    python algorithms/get_results_fl_muv.py --integration-test
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
from urllib.parse import unquote, urlencode

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

# MÜV COA Portal URL.
PORTAL_URL = 'https://muvfl.com/coa-portal'

# Treefersoft base URL for COA PDFs.
TREEFERSOFT_BASE = 'https://www.treefersoft.com/LabReports/LabReportQR/'

# MÜV/Verano producer metadata.
MUV_PRODUCER = {
    'producer': 'MÜV',
    'producer_dba': 'MÜV (Verano Holdings)',
    'producer_license_number': 'MMTC-2017-0007',
    'producer_state': 'fl',
    'producer_website': 'https://muvfl.com',
}

# Default HTTP headers — polite identification.
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

# Minimum valid PDF size in bytes. Files smaller than this are
# likely HTML error pages or empty responses.
MIN_PDF_SIZE = 10 * 1024  # 10 KB

# Default pause between search queries (seconds).
# Set high for respectful enumeration.
DEFAULT_SEARCH_PAUSE = 10.0

# Default pause between PDF downloads (seconds).
DEFAULT_DOWNLOAD_PAUSE = 3.0

# Maximum back-off wait (seconds).
MAX_BACKOFF = 120

# Default number of suffix digits for enumeration.
DEFAULT_DIGITS = 4

# Default maximum queries per session.
DEFAULT_MAX_QUERIES = 10_000

# Location ID for the age gate (Tallahassee, FL).
# Any valid FL location works; search results don't vary by location.
DEFAULT_LOCATION_ID = '812'

# Florida Metrc tag prefix for MÜV.
METRC_PREFIX = '1A4'

# Regex to extract batch IDs from page text.
# Metrc tags are 24 alphanumeric characters starting with '1A4'.
BATCH_ID_RE = re.compile(r'1A4[A-Za-z0-9]{21}')

# Module-level logger.
logger = logging.getLogger('get_results_fl_muv')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Helper Functions                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

def _generate_result_id(batch_id: str) -> str:
    """Generate a deterministic 16-char hex ID from a batch ID.

    Args:
        batch_id: The full Metrc tag / batch ID string.

    Returns:
        16-character hex string.
    """
    return hashlib.sha256(batch_id.encode('utf-8')).hexdigest()[:16]


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


def _sanitize_batch_id(batch_id: str) -> str:
    """Clean a batch ID for use as a filename component.

    Removes any non-alphanumeric characters.

    Args:
        batch_id: Raw batch ID string.

    Returns:
        Sanitized string safe for filenames.
    """
    return re.sub(r'[^A-Za-z0-9]', '', batch_id)


def _extract_treefersoft_url(html: str) -> Optional[str]:
    """Extract the Treefersoft COA download URL from search results.

    Looks for the ``<a>`` tag containing a link to
    ``treefersoft.com/LabReports/LabReportQR/``.

    Args:
        html: The page HTML source after a search.

    Returns:
        The full Treefersoft URL, or None if not found.
    """
    # Match the href attribute containing the treefersoft URL.
    pattern = re.compile(
        r'href="(https?://(?:www\.)?treefersoft\.com'
        r'/LabReports/LabReportQR/[^"]*)"',
        re.IGNORECASE,
    )
    match = pattern.search(html)
    if match:
        return match.group(1)
    return None


def _extract_batch_id_from_html(html: str) -> Optional[str]:
    """Extract the full batch ID from the search results page.

    Looks for a 24-character string matching the Metrc tag format
    (starting with ``1A4`` followed by 21 digits).

    Args:
        html: The page HTML source after a search.

    Returns:
        The full batch ID string, or None if not found.
    """
    match = BATCH_ID_RE.search(html)
    if match:
        return match.group(0)
    return None


def _extract_lab_report_param(url: str) -> str:
    """Extract the LabReport parameter from a Treefersoft URL.

    This serves as a unique identifier for the COA in the
    Treefersoft system.

    Args:
        url: Full Treefersoft URL.

    Returns:
        The decoded LabReport parameter value, or empty string.
    """
    if 'LabReport=' not in url:
        return ''
    param = url.split('LabReport=', 1)[1]
    # Remove any trailing query parameters.
    if '&' in param:
        param = param.split('&', 1)[0]
    return unquote(param)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Progress Tracker                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

class ProgressTracker:
    """Tracks enumeration progress for resumable collection.

    Saves and loads state to a JSON file so collection can span
    multiple sessions without re-querying already-checked suffixes.

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
            'searched_suffixes': [],
            'found_batch_ids': {},
            'last_suffix': None,
            'digits': DEFAULT_DIGITS,
            'total_queries': 0,
            'total_found': 0,
            'total_not_found': 0,
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
    def searched(self) -> set:
        """Set of already-searched suffix strings."""
        return set(self.state.get('searched_suffixes', []))

    @property
    def found(self) -> Dict[str, str]:
        """Dict mapping batch_id → treefersoft_url."""
        return self.state.get('found_batch_ids', {})

    def record_search(
            self,
            suffix: str,
            batch_id: Optional[str] = None,
            coa_url: Optional[str] = None,
        ) -> None:
        """Record the result of a search query.

        Args:
            suffix: The numeric suffix that was searched.
            batch_id: The discovered batch ID (None if no result).
            coa_url: The Treefersoft COA URL (None if no result).
        """
        if suffix not in self.state['searched_suffixes']:
            self.state['searched_suffixes'].append(suffix)
        self.state['last_suffix'] = suffix
        self.state['total_queries'] = len(
            self.state['searched_suffixes'],
        )
        if batch_id and coa_url:
            self.state['found_batch_ids'][batch_id] = coa_url
            self.state['total_found'] = len(
                self.state['found_batch_ids'],
            )
        else:
            self.state['total_not_found'] = (
                self.state['total_queries']
                - self.state['total_found']
            )


# ╔══════════════════════════════════════════════════════════════════╗
# ║ MuvCollector                                                     ║
# ╚══════════════════════════════════════════════════════════════════╝

class MuvCollector:
    """Collector for MÜV (Verano) COA PDFs in Florida.

    Discovers COAs by enumerating numeric suffixes against the
    MÜV batch search portal, downloads PDFs from Treefersoft,
    and converts to standardized LabResult records.

    The collector uses Selenium for portal interaction (age gate
    bypass and batch search form) and a requests session for
    efficient PDF downloads.

    Attributes:
        pdf_dir: Directory where COA PDFs are stored.
        data_dir: Base data directory for datasets and outputs.
        datasets_dir: Directory for CSV outputs.
        manifest_path: Path to the manifest CSV.
        progress_path: Path to the progress JSON.
        search_pause: Seconds between search queries.
        download_pause: Seconds between PDF downloads.
        session: Requests session for downloads.
        driver: Selenium WebDriver instance (initialized on demand).
    """

    def __init__(
            self,
            pdf_dir: str = '',
            data_dir: str = '',
            search_pause: float = DEFAULT_SEARCH_PAUSE,
            download_pause: float = DEFAULT_DOWNLOAD_PAUSE,
            verbose: bool = True,
        ):
        """Initialize the MÜV collector.

        Args:
            pdf_dir: Directory for COA PDFs.
                Defaults to ``D:/data/florida/results/pdfs/muv``.
            data_dir: Base data directory.
                Defaults to ``D:/data/florida/results``.
            search_pause: Seconds between search queries.
            download_pause: Seconds between PDF downloads.
            verbose: Enable verbose logging.
        """
        # Resolve defaults.
        if not data_dir:
            if PATHS:
                data_dir = PATHS.get(
                    'fl_data_dir', 'D:/data/florida/results',
                )
            else:
                data_dir = 'D:/data/florida/results'
        if not pdf_dir:
            pdf_dir = os.path.join(data_dir, 'pdfs', 'muv')

        self.pdf_dir = Path(pdf_dir)
        self.data_dir = Path(data_dir)
        self.datasets_dir = self.data_dir / 'datasets'
        self.manifest_path = self.datasets_dir / 'muv-manifest.csv'
        self.progress_path = self.datasets_dir / 'muv-progress.json'
        self.search_pause = search_pause
        self.download_pause = download_pause
        self.verbose = verbose

        # Logging.
        self.logger = logging.getLogger('get_results_fl_muv')
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            fmt = (
                '%(asctime)s | %(name)s | %(levelname)s | %(message)s'
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

        Uses the project's ``driver_utils`` module when available.
        Falls back to basic Chrome options with anti-detection
        flags.

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
            opts.add_argument(
                '--user-agent=Mozilla/5.0 '
                '(Windows NT 10.0; Win64; x64) '
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
                from webdriver_manager.chrome import (
                    ChromeDriverManager,
                )
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
        """Safely close the Selenium WebDriver.

        Handles already-dead sessions gracefully — the driver
        process may have already exited (e.g., Chrome crash,
        OS kill), so all cleanup errors are swallowed.
        """
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            # Also try to kill any orphaned chromedriver processes.
            try:
                if hasattr(self.driver, 'service') \
                        and self.driver.service.process:
                    self.driver.service.process.kill()
            except Exception:
                pass
            self.driver = None

    def _is_session_alive(self) -> bool:
        """Check if the Selenium session is still usable.

        Performs a lightweight operation (reading the page title)
        to verify the browser process and DevTools connection
        are both intact.

        Returns:
            True if the session is alive and responsive.
        """
        if self.driver is None:
            return False
        try:
            # A simple property access that requires a live session.
            _ = self.driver.title
            return True
        except Exception:
            return False

    def _reinitialize_driver(
            self,
            headless: bool = True,
            reason: str = '',
        ) -> bool:
        """Kill the current driver and spin up a fresh one.

        Creates a new Chrome instance and bypasses the age gate.
        Used to recover from dead sessions (Chrome crash, OS kill,
        memory pressure, etc.).

        Args:
            headless: Run browser in headless mode.
            reason: Human-readable reason for reinitialization
                (logged for diagnostics).

        Returns:
            True if reinitialization succeeded and the portal
            is ready for queries.
        """
        if reason:
            self.logger.warning(
                f'Reinitializing driver: {reason}'
            )
        else:
            self.logger.warning('Reinitializing driver...')

        # Kill the old driver (swallows all errors).
        self._quit_driver()

        # Small pause to let OS clean up the process.
        time.sleep(2)

        # Spin up a fresh driver.
        try:
            self._init_selenium(headless=headless)
        except Exception as exc:
            self.logger.error(
                f'Failed to reinitialize Selenium: {exc}'
            )
            return False

        # Bypass the age gate on the fresh session.
        try:
            if not self._bypass_age_gate():
                self.logger.error(
                    'Failed to bypass age gate after '
                    'driver reinitialization'
                )
                return False
        except Exception as exc:
            self.logger.error(
                f'Age gate bypass failed after '
                f'reinitialization: {exc}'
            )
            return False

        self.logger.info(
            '✓ Driver reinitialized and portal ready'
        )
        return True

    # ── Rate Limiting ────────────────────────────────────────────

    def _respectful_pause(
            self,
            base: Optional[float] = None,
            multiplier: float = 1.0,
        ) -> None:
        """Sleep with jitter for respectful rate limiting.

        Args:
            base: Base pause duration (defaults to search_pause).
            multiplier: Factor to multiply the base pause.
        """
        if base is None:
            base = self.search_pause
        jitter = random.uniform(0, base * 0.3)
        wait = base * multiplier + jitter
        time.sleep(wait)

    # ── Age Gate ─────────────────────────────────────────────────

    def _bypass_age_gate(
            self,
            location_id: str = DEFAULT_LOCATION_ID,
        ) -> bool:
        """Bypass the MÜV age gate by selecting a dispensary.

        The age gate requires selecting a Florida dispensary
        location. The search results do not vary by location,
        so any valid location ID works.

        Tries JavaScript-based selection first (faster), then
        falls back to clicking the location card via Selenium.

        Args:
            location_id: The location ID to select
                (default: ``'812'`` = Pensacola, FL).

        Returns:
            True if the age gate was successfully bypassed.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        self.logger.info('Navigating to MÜV COA portal...')
        self.driver.get(PORTAL_URL)
        time.sleep(5)

        # Check if the age gate is present.
        page_source = self.driver.page_source
        if 'age-gate' not in page_source.lower() \
                and 'SELECT A DISPENSARY' not in page_source:
            self.logger.info(
                'No age gate detected, portal already accessible'
            )
            return True

        # Strategy 1: JavaScript-based location selection.
        try:
            self.logger.info(
                f'Selecting location {location_id} via JS...'
            )
            self.driver.execute_script(
                f'verano.selectLocation("{location_id}")'
            )
            time.sleep(5)

            # Verify the age gate is dismissed.
            page_source = self.driver.page_source
            if 'coa-batch-input' in page_source \
                    or 'Batch Number Lookup' in page_source:
                self.logger.info('✓ Age gate bypassed via JS')
                return True
        except Exception as exc:
            self.logger.debug(
                f'JS location selection failed: {exc}'
            )

        # Strategy 2: Click a location card directly.
        try:
            self.logger.info(
                'Trying direct click on location card...'
            )
            location_btn = WebDriverWait(self.driver, 15).until(
                EC.element_to_be_clickable((
                    By.CSS_SELECTOR,
                    f'button[onclick*="selectLocation'
                    f'(\'{location_id}\')"]',
                ))
            )
            location_btn.click()
            time.sleep(5)

            page_source = self.driver.page_source
            if 'coa-batch-input' in page_source \
                    or 'Batch Number Lookup' in page_source:
                self.logger.info(
                    '✓ Age gate bypassed via click'
                )
                return True
        except Exception as exc:
            self.logger.debug(
                f'Click location selection failed: {exc}'
            )

        # Strategy 3: Try any available location button.
        try:
            self.logger.info(
                'Trying any available location button...'
            )
            buttons = self.driver.find_elements(
                By.CSS_SELECTOR,
                'button[data-id="age-gate-location-card"]',
            )
            if buttons:
                buttons[0].click()
                time.sleep(5)
                page_source = self.driver.page_source
                if 'coa-batch-input' in page_source \
                        or 'Batch Number Lookup' in page_source:
                    self.logger.info(
                        '✓ Age gate bypassed via fallback click'
                    )
                    return True
        except Exception as exc:
            self.logger.debug(f'Fallback click failed: {exc}')

        self.logger.warning('Failed to bypass age gate')
        return False

    # ── Search Portal ────────────────────────────────────────────

    def _bypass_age_gate_only(
            self,
            location_id: str = DEFAULT_LOCATION_ID,
        ) -> bool:
        """Bypass the age gate WITHOUT re-navigating.

        Assumes the driver is already on a page showing the age
        gate. Only calls ``verano.selectLocation()`` via JS.
        Used by ``_click_search_again`` after a page refresh
        to avoid a full navigation cycle.

        Args:
            location_id: The location ID to select.

        Returns:
            True if the age gate was bypassed.
        """
        try:
            self.driver.execute_script(
                f'verano.selectLocation("{location_id}")'
            )
            time.sleep(4)
            if 'coa-batch-input' in self.driver.page_source:
                self.logger.debug(
                    '✓ Age gate re-bypassed (in-page)'
                )
                return True
        except Exception:
            pass

        # Fallback: click any location card.
        from selenium.webdriver.common.by import By
        try:
            buttons = self.driver.find_elements(
                By.CSS_SELECTOR,
                'button[data-id="age-gate-location-card"]',
            )
            if buttons:
                buttons[0].click()
                time.sleep(4)
                if 'coa-batch-input' in self.driver.page_source:
                    self.logger.debug(
                        '✓ Age gate re-bypassed (click)'
                    )
                    return True
        except Exception:
            pass

        self.logger.warning('Failed to re-bypass age gate in-page')
        return False

    def _search_batch(
            self,
            query: str,
            retry_count: int = 2,
        ) -> Tuple[Optional[str], Optional[str]]:
        """Search for a batch ID on the MÜV COA portal.

        Enters the query into the batch search input and submits
        the form. Parses the result page for the Treefersoft
        download URL and the full batch ID.

        On a successful find, clicks "Search Again" to reset.
        On no result, the form stays visible — just clears
        the input for the next query (no page reload needed).

        Args:
            query: The search query (numeric suffix).
            retry_count: Number of retry attempts on failure.

        Returns:
            Tuple of (batch_id, treefersoft_url) or (None, None)
            if no result was found.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        for attempt in range(retry_count + 1):
            try:
                # Find the search input.
                search_input = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((
                        By.CSS_SELECTOR,
                        'input[data-testid="coa-batch-input"]',
                    ))
                )

                # Clear and enter the search query.
                search_input.clear()
                time.sleep(0.3)
                search_input.send_keys(query)
                time.sleep(0.3)

                # Submit the form.
                search_input.send_keys(Keys.RETURN)
                time.sleep(3)

                # Wait for results or "not found" response.
                page_source = self.driver.page_source

                # Check for a successful result.
                coa_url = _extract_treefersoft_url(page_source)
                if coa_url:
                    # Try to extract the full batch ID from:
                    # 1. The page HTML (may contain the Metrc tag).
                    batch_id = _extract_batch_id_from_html(
                        page_source,
                    )

                    # 2. If not found in HTML, try extracting
                    #    from any visible text on the page.
                    if not batch_id:
                        try:
                            body_text = self.driver.find_element(
                                By.TAG_NAME, 'body',
                            ).text
                            batch_id = _extract_batch_id_from_html(
                                body_text,
                            )
                        except Exception:
                            pass

                    # 3. Fallback: construct a placeholder using
                    #    the suffix (the PDF parser will extract
                    #    the real batch ID downstream).
                    if not batch_id:
                        batch_id = f'MUV-suffix-{query}'

                    self.logger.info(
                        f'  ✓ Found: "{query}" → {batch_id}'
                    )

                    # Click "Search Again" to reset for next query.
                    self._click_search_again()
                    return batch_id, coa_url

                # Check if "Certificate of Analysis Found" is shown
                # but we missed the URL via regex.
                if 'Certificate of Analysis Found' in page_source:
                    try:
                        link = self.driver.find_element(
                            By.CSS_SELECTOR,
                            'a[href*="treefersoft.com"]',
                        )
                        coa_url = link.get_attribute('href')
                        batch_id = _extract_batch_id_from_html(
                            page_source,
                        ) or f'MUV-suffix-{query}'
                        self.logger.info(
                            f'  ✓ Found (fallback): "{query}" → '
                            f'{batch_id}'
                        )
                        self._click_search_again()
                        return batch_id, coa_url
                    except Exception:
                        pass

                # No result found — the form should still be
                # visible, so we do NOT call _click_search_again.
                # Just clear the input for the next query.
                self.logger.debug(f'  ✗ No result: "{query}"')
                try:
                    # Re-find the input (may have been re-rendered).
                    inp = self.driver.find_element(
                        By.CSS_SELECTOR,
                        'input[data-testid="coa-batch-input"]',
                    )
                    inp.clear()
                except Exception:
                    # If the input isn't found, the page may have
                    # changed; try a lightweight reset.
                    self._click_search_again()

                return None, None

            except Exception as exc:
                exc_str = str(exc).lower()
                is_dead_session = (
                    'invalid session' in exc_str
                    or 'session deleted' in exc_str
                    or 'not connected' in exc_str
                    or 'disconnected' in exc_str
                    or 'chrome not reachable' in exc_str
                    or 'no such window' in exc_str
                )

                if attempt < retry_count:
                    wait = 5 * (attempt + 1)
                    self.logger.warning(
                        f'Search error for "{query}" '
                        f'(attempt {attempt + 1}): '
                        f'{type(exc).__name__}. '
                        f'Retrying in {wait}s...'
                    )
                    time.sleep(wait)

                    if is_dead_session:
                        # Driver is dead — must reinitialize.
                        success = self._reinitialize_driver(
                            headless=True,
                            reason=(
                                f'Dead session detected during '
                                f'search for "{query}"'
                            ),
                        )
                        if not success:
                            self.logger.error(
                                'Driver reinitialization failed. '
                                'Cannot continue.'
                            )
                            return None, None
                    else:
                        # Driver may still be alive — try refresh.
                        try:
                            self.driver.refresh()
                            time.sleep(4)
                            page = self.driver.page_source
                            if 'age-gate' in page.lower():
                                self._bypass_age_gate_only()
                        except Exception:
                            # Refresh also failed — reinitialize.
                            success = self._reinitialize_driver(
                                headless=True,
                                reason=(
                                    'Refresh failed during '
                                    f'search for "{query}"'
                                ),
                            )
                            if not success:
                                return None, None
                else:
                    self.logger.error(
                        f'Search failed for "{query}" after '
                        f'{retry_count + 1} attempts: '
                        f'{type(exc).__name__}'
                    )
                    return None, None

        return None, None

    def _click_search_again(self) -> None:
        """Click the 'Search Again' button to reset the form.

        Tries multiple strategies in order:
          1. JavaScript click via querySelector (most reliable
             for React/SPA pages).
          2. Selenium XPATH click.
          3. Selenium CSS fallback for underlined buttons.
          4. Last resort: full page reload (avoids re-navigating
             to the portal URL to preserve session/cookies).

        Only triggers age gate bypass if absolutely necessary.
        """
        from selenium.webdriver.common.by import By

        # Strategy 1: JS click — most reliable for SPAs.
        try:
            clicked = self.driver.execute_script("""
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    if (buttons[i].textContent.trim()
                            .toLowerCase().includes('search again')) {
                        buttons[i].click();
                        return true;
                    }
                }
                return false;
            """)
            if clicked:
                time.sleep(2)
                # Verify the search input is back.
                page = self.driver.page_source
                if 'coa-batch-input' in page:
                    return
        except Exception:
            pass

        # Strategy 2: XPATH click.
        try:
            buttons = self.driver.find_elements(
                By.XPATH,
                '//button[contains(text(), "Search Again")]',
            )
            if buttons:
                buttons[0].click()
                time.sleep(2)
                if 'coa-batch-input' in self.driver.page_source:
                    return
        except Exception:
            pass

        # Strategy 3: CSS fallback — the button has class
        # "underline" per the HTML.
        try:
            buttons = self.driver.find_elements(
                By.CSS_SELECTOR,
                'button.underline, button.min-w-fit',
            )
            for btn in buttons:
                try:
                    txt = btn.text.strip().lower()
                except Exception:
                    continue
                if 'search' in txt:
                    try:
                        btn.click()
                    except Exception:
                        self.driver.execute_script(
                            'arguments[0].click();', btn,
                        )
                    time.sleep(2)
                    if 'coa-batch-input' in self.driver.page_source:
                        return
        except Exception:
            pass

        # Strategy 4: Reload the current page (preserves session).
        try:
            self.driver.refresh()
            time.sleep(4)
            page = self.driver.page_source
            if 'coa-batch-input' in page:
                return
            # If age gate returned, bypass it.
            if 'age-gate' in page.lower() \
                    or 'SELECT A DISPENSARY' in page:
                self._bypass_age_gate_only()
        except Exception:
            pass

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
            DataFrame with columns: file_name, batch_id, file_path,
            file_size, file_hash, date_cataloged, source, coa_url.
        """
        # Load existing manifest if incremental.
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
            existing_names = set(
                existing['file_name'].astype(str),
            )

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
                    for chunk in iter(
                        lambda: fh.read(65536), b'',
                    ):
                        sha.update(chunk)
                file_hash = sha.hexdigest()

            # Extract batch ID from filename.
            batch_id = f.replace('.pdf', '').strip()

            new_rows.append({
                'file_name': f,
                'batch_id': batch_id,
                'file_path': str(fp),
                'file_size': file_size,
                'file_hash': file_hash,
                'date_cataloged': now,
                'source': 'muv_coa_portal',
                'coa_url': '',
            })

        if new_rows:
            self.logger.info(f'Cataloged {len(new_rows)} new PDF(s)')
            new_df = pd.DataFrame(new_rows)
            manifest = pd.concat(
                [existing, new_df], ignore_index=True,
            )
        else:
            self.logger.info('No new PDFs to catalog')
            manifest = (
                existing if len(existing) > 0 else pd.DataFrame()
            )

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

    # ── Phase 2: Discover COAs ───────────────────────────────────

    def discover_coas(
            self,
            digits: int = DEFAULT_DIGITS,
            start: Optional[int] = None,
            end: Optional[int] = None,
            max_queries: int = DEFAULT_MAX_QUERIES,
            resume: bool = True,
            headless: bool = True,
        ) -> pd.DataFrame:
        """Discover COAs by enumerating numeric suffixes.

        Systematically searches the MÜV portal with numeric
        suffixes of the specified digit length. Tracks progress
        for resumable collection across sessions.

        Args:
            digits: Number of suffix digits (default: 4).
                4 digits → 10,000 queries, covers most batches.
                5 digits → 100,000 queries, near-complete coverage.
            start: Starting suffix number (inclusive).
                Defaults to 0.
            end: Ending suffix number (exclusive).
                Defaults to 10^digits.
            max_queries: Maximum queries per session.
            resume: If True, skip already-searched suffixes.
            headless: Run browser in headless mode.

        Returns:
            DataFrame with discovered COA metadata:
                suffix, batch_id, coa_url, discovered_at.
        """
        # Initialize progress tracker.
        tracker = ProgressTracker(str(self.progress_path))
        if not tracker.state.get('started_at'):
            tracker.state['started_at'] = datetime.now().isoformat()
        tracker.state['digits'] = digits

        # Determine the search range.
        if start is None:
            start = 0
        if end is None:
            end = 10 ** digits

        # Initialize Selenium and bypass age gate.
        self._init_selenium(headless=headless)
        if not self._bypass_age_gate():
            self.logger.error(
                'Cannot proceed without bypassing age gate'
            )
            return pd.DataFrame()

        # Enumerate suffixes.
        discovered = []
        queries_this_session = 0
        consecutive_misses = 0
        max_consecutive_misses = 500  # Safety valve.

        self.logger.info(
            f'Starting COA discovery: {digits}-digit suffixes, '
            f'range [{start}, {end}), '
            f'max {max_queries} queries/session'
        )
        if resume:
            already = len(tracker.searched)
            self.logger.info(
                f'Resuming: {already} suffixes already searched, '
                f'{len(tracker.found)} COAs found so far'
            )

        for suffix_num in range(start, end):
            # Format as zero-padded string.
            suffix = str(suffix_num).zfill(digits)

            # Skip already-searched suffixes.
            if resume and suffix in tracker.searched:
                continue

            # Check session limits.
            if queries_this_session >= max_queries:
                self.logger.info(
                    f'Reached session limit of {max_queries} '
                    f'queries. Saving progress.'
                )
                break

            # ── Pre-query health check ────────────────────────
            # Detect dead Chrome sessions BEFORE attempting a
            # query. This catches crashes that happened during
            # the sleep between queries (the exact scenario that
            # caused the original failure).
            if not self._is_session_alive():
                self.logger.warning(
                    f'Dead session detected before query '
                    f'"{suffix}". Reinitializing...'
                )
                tracker.save()  # Save progress first!
                success = self._reinitialize_driver(
                    headless=headless,
                    reason='Dead session detected in main loop',
                )
                if not success:
                    self.logger.error(
                        'Driver reinitialization failed. '
                        'Saving progress and stopping.'
                    )
                    break

            # Search for this suffix.
            batch_id, coa_url = self._search_batch(suffix)

            # Record the result.
            tracker.record_search(suffix, batch_id, coa_url)
            queries_this_session += 1

            if batch_id and coa_url:
                discovered.append({
                    'suffix': suffix,
                    'batch_id': batch_id,
                    'coa_url': coa_url,
                    'lab_report_param': _extract_lab_report_param(
                        coa_url,
                    ),
                    'discovered_at': datetime.now().isoformat(),
                })
                consecutive_misses = 0
            else:
                consecutive_misses += 1

            # Save progress frequently — every 10 queries.
            # This ensures minimal data loss if Chrome crashes.
            if queries_this_session % 10 == 0:
                tracker.save()
                self.logger.info(
                    f'Progress: {queries_this_session} queries, '
                    f'{len(discovered)} new COAs found '
                    f'({len(tracker.found)} total)'
                )

            # Respectful pause between queries.
            self._respectful_pause()

        # Final save.
        tracker.save()

        # Build DataFrame.
        df = pd.DataFrame(discovered)
        self.logger.info(
            f'Discovery complete: {queries_this_session} queries, '
            f'{len(discovered)} new COAs found this session '
            f'({len(tracker.found)} total across all sessions)'
        )

        # Save discovered URLs snapshot.
        if len(df) > 0:
            ts = datetime.now().strftime('%Y-%m-%dT%H-%M-%S')
            urls_path = (
                self.datasets_dir
                / f'fl-lab-result-urls-muv-{ts}.csv'
            )
            df.to_csv(str(urls_path), index=False)
            self.logger.info(f'Saved URL snapshot → {urls_path}')

        return df

    # ── Phase 3: Download New COAs ───────────────────────────────

    def _download_coa(
            self,
            coa_url: str,
            batch_id: str,
            max_retries: int = 3,
        ) -> Optional[str]:
        """Download a single COA PDF from Treefersoft.

        Args:
            coa_url: The Treefersoft download URL.
            batch_id: The full batch ID (used for filename).
            max_retries: Maximum download attempts.

        Returns:
            Path to the downloaded PDF, or None on failure.
        """
        safe_id = _sanitize_batch_id(batch_id) if batch_id else ''
        if not safe_id:
            # Use hash of the URL as filename.
            safe_id = hashlib.sha256(
                coa_url.encode('utf-8'),
            ).hexdigest()[:24]

        outfile = str(self.pdf_dir / f'{safe_id}.pdf')

        # Skip if already downloaded.
        if os.path.exists(outfile) and _is_valid_pdf_file(outfile):
            self.logger.debug(f'Already downloaded: {safe_id}')
            return outfile

        # Download with retries and exponential backoff.
        for attempt in range(max_retries):
            try:
                response = self.session.get(
                    coa_url,
                    timeout=60,
                    allow_redirects=True,
                )
                response.raise_for_status()

                data = response.content
                if _is_valid_pdf(data):
                    with open(outfile, 'wb') as f:
                        f.write(data)
                    self.logger.info(
                        f'Downloaded: {safe_id}.pdf '
                        f'({len(data):,} bytes)'
                    )
                    return outfile
                else:
                    self.logger.warning(
                        f'Invalid PDF for {safe_id} '
                        f'({len(data)} bytes, '
                        f'header={data[:10]})'
                    )
                    # If it's HTML, it might be a redirect page.
                    # Try following any links in the response.
                    if data[:6] in (b'<html>', b'<!DOCT', b'<!doct'):
                        text = data.decode('utf-8', errors='ignore')
                        redirect_url = _extract_treefersoft_url(text)
                        if redirect_url and redirect_url != coa_url:
                            self.logger.info(
                                f'Following redirect: '
                                f'{redirect_url[:60]}...'
                            )
                            coa_url = redirect_url
                            continue
                    return None

            except requests.RequestException as exc:
                wait = min(
                    MAX_BACKOFF,
                    (2 ** attempt) * 5 + random.uniform(0, 3),
                )
                self.logger.warning(
                    f'Download error for {safe_id} '
                    f'(attempt {attempt + 1}/{max_retries}): '
                    f'{exc}. Retrying in {wait:.0f}s...'
                )
                time.sleep(wait)

        self.logger.error(
            f'Failed to download {safe_id} '
            f'after {max_retries} attempts'
        )
        return None

    def download_new_coas(
            self,
            discovered: pd.DataFrame,
            existing_batch_ids: Optional[set] = None,
        ) -> int:
        """Download COA PDFs for newly discovered batch IDs.

        Skips batch IDs already present in the local archive.

        Args:
            discovered: DataFrame with batch_id and coa_url columns.
            existing_batch_ids: Set of already-downloaded batch IDs.

        Returns:
            Number of successfully downloaded PDFs.
        """
        if existing_batch_ids is None:
            existing_batch_ids = set()

        # Also check existing files on disk.
        on_disk = set()
        if self.pdf_dir.exists():
            on_disk = {
                f.replace('.pdf', '')
                for f in os.listdir(str(self.pdf_dir))
                if f.lower().endswith('.pdf')
            }

        skip_ids = existing_batch_ids | on_disk
        to_download = []

        for _, row in discovered.iterrows():
            batch_id = row.get('batch_id', '')
            coa_url = row.get('coa_url', '')
            if not batch_id or not coa_url:
                continue
            safe_id = _sanitize_batch_id(batch_id)
            if safe_id in skip_ids:
                continue
            to_download.append((batch_id, coa_url))

        if not to_download:
            self.logger.info('No new COAs to download')
            return 0

        self.logger.info(
            f'Downloading {len(to_download)} new COA PDF(s)...'
        )

        downloaded = 0
        for batch_id, coa_url in to_download:
            result = self._download_coa(coa_url, batch_id)
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

        Each manifest row becomes a LabResult dict with producer
        metadata, source attribution, and the batch ID as the
        primary identifier. Actual cannabinoid/terpene data is
        extracted downstream by the COA parser (parse_coas.py).

        Args:
            manifest: DataFrame with manifest columns.

        Returns:
            List of LabResult dictionaries.
        """
        results = []
        for _, row in manifest.iterrows():
            batch_id = str(row.get('batch_id', ''))
            if not batch_id:
                continue

            result_id = _generate_result_id(batch_id)
            file_name = str(row.get('file_name', ''))
            coa_url = str(row.get('coa_url', ''))

            record = {
                'result_id': result_id,
                'sample_id': batch_id,
                'batch_number': batch_id,
                'product_name': '',
                'product_type': '',
                'date_tested': '',
                'lab': '',
                'lab_address': '',
                'lab_license_number': '',
                'lab_results_url': coa_url,
                'coa_pdf': file_name,
                'source': 'muv_coa_portal',
                'source_url': PORTAL_URL,
                'state': 'fl',
                **MUV_PRODUCER,
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
        ) -> pd.DataFrame:
        """Execute the full collection pipeline.

        Phase 1: Catalog existing PDFs in the local archive.
        Phase 2: Discover COAs via search enumeration.
        Phase 3: Download new COA PDFs from Treefersoft.
        Phase 4: Convert manifest to standardized LabResult records.

        Args:
            catalog_only: If True, only build the manifest.
            discover: If True, run the search enumeration.
            download: If True, download discovered COA PDFs.
            headless: Run Selenium in headless mode.
            save_results: If True, save the results CSV.
            digits: Number of suffix digits for enumeration.
            max_queries: Maximum queries per session.
            resume: If True, skip already-searched suffixes.

        Returns:
            DataFrame with standardized lab result records.
        """
        self.logger.info('Starting MÜV COA collection...')
        self.logger.info(f'PDF directory: {self.pdf_dir}')
        self.logger.info(f'Manifest: {self.manifest_path}')

        # Phase 1: Catalog existing archive.
        manifest = self.catalog_existing()

        if catalog_only:
            lab_results = self._convert_to_lab_results(manifest)
            results_df = pd.DataFrame(lab_results)
            if save_results and len(results_df) > 0:
                outpath = (
                    self.datasets_dir / 'fl-results-muv-latest.csv'
                )
                results_df.to_csv(str(outpath), index=False)
                self.logger.info(
                    f'Results saved: {len(results_df)} '
                    f'→ {outpath}'
                )
            self.logger.info(
                f'Manifest contains {len(manifest)} COA PDF(s)'
            )
            return results_df

        # Phase 2: Discover COAs.
        discovered = pd.DataFrame()
        if discover:
            discovered = self.discover_coas(
                digits=digits,
                max_queries=max_queries,
                resume=resume,
                headless=headless,
            )

        # Also load all discovered COAs from progress tracker.
        all_discovered = pd.DataFrame()
        if self.progress_path.exists():
            tracker = ProgressTracker(str(self.progress_path))
            all_found = tracker.found
            if all_found:
                all_discovered = pd.DataFrame([
                    {
                        'batch_id': bid,
                        'coa_url': url,
                    }
                    for bid, url in all_found.items()
                ])

        # Phase 3: Download new COAs.
        if download and len(all_discovered) > 0:
            existing_ids = set()
            if len(manifest) > 0 and 'batch_id' in manifest.columns:
                existing_ids = set(
                    manifest['batch_id'].astype(str),
                )
            self.download_new_coas(all_discovered, existing_ids)

            # Re-catalog after downloads.
            manifest = self.catalog_existing()

        # Close Selenium.
        self._quit_driver()

        # Enrich manifest with discovered metadata.
        if len(all_discovered) > 0 and len(manifest) > 0:
            url_map = dict(
                zip(
                    all_discovered['batch_id'].astype(str),
                    all_discovered['coa_url'].astype(str),
                )
            )
            manifest['coa_url'] = manifest['batch_id'].map(
                url_map,
            ).fillna(manifest.get('coa_url', ''))

        # Phase 4: Convert to LabResult records.
        lab_results = self._convert_to_lab_results(manifest)
        results_df = pd.DataFrame(lab_results)
        if save_results and len(results_df) > 0:
            outpath = (
                self.datasets_dir / 'fl-results-muv-latest.csv'
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
            manifest_entries, discovery_progress.
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

        progress = {}
        if self.progress_path.exists():
            tracker = ProgressTracker(str(self.progress_path))
            progress = {
                'total_queries': tracker.state.get(
                    'total_queries', 0,
                ),
                'total_found': tracker.state.get(
                    'total_found', 0,
                ),
                'total_not_found': tracker.state.get(
                    'total_not_found', 0,
                ),
                'last_suffix': tracker.state.get(
                    'last_suffix',
                ),
            }

        return {
            'total_pdfs': len(pdf_files),
            'total_size_bytes': total_size,
            'total_size_gb': round(total_size / (1024 ** 3), 2),
            'manifest_entries': manifest_entries,
            'discovery_progress': progress,
        }


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Unit Tests                                                       ║
# ╚══════════════════════════════════════════════════════════════════╝

def run_unit_tests():
    """Run inline smoke tests for all helper functions."""
    print('Running unit tests...')

    # _generate_result_id
    assert _generate_result_id('test') == _generate_result_id('test')
    assert len(_generate_result_id('test')) == 16
    assert _generate_result_id('a') != _generate_result_id('b')
    print('  ✓ _generate_result_id')

    # _is_valid_pdf
    assert _is_valid_pdf(b'%PDF-1.4' + b'\x00' * 20000)
    assert not _is_valid_pdf(b'<html>')
    assert not _is_valid_pdf(b'%PDF-1.4' + b'\x00' * 100)
    print('  ✓ _is_valid_pdf')

    # _sanitize_batch_id
    assert _sanitize_batch_id('1A40303000017D5000071521') == \
        '1A40303000017D5000071521'
    assert _sanitize_batch_id('abc-123/456') == 'abc123456'
    assert _sanitize_batch_id('') == ''
    print('  ✓ _sanitize_batch_id')

    # _extract_treefersoft_url
    html_with_url = (
        '<a href="https://www.treefersoft.com/LabReports/'
        'LabReportQR/?LabReport=abc123">Download</a>'
    )
    assert _extract_treefersoft_url(html_with_url) == (
        'https://www.treefersoft.com/LabReports/'
        'LabReportQR/?LabReport=abc123'
    )
    assert _extract_treefersoft_url('<p>No link here</p>') is None
    print('  ✓ _extract_treefersoft_url')

    # _extract_batch_id_from_html
    html_with_batch = '<p>Batch: 1A40303000017D5000071521</p>'
    assert _extract_batch_id_from_html(html_with_batch) == \
        '1A40303000017D5000071521'
    assert _extract_batch_id_from_html('<p>No batch</p>') is None
    print('  ✓ _extract_batch_id_from_html')

    # _extract_lab_report_param
    url = (
        'https://www.treefersoft.com/LabReports/LabReportQR/'
        '?LabReport=0L5Ep8%2bLPa51b2l3BlnbowRAFuH8eetxeDczsEWfBoM%3d'
    )
    param = _extract_lab_report_param(url)
    assert param == '0L5Ep8+LPa51b2l3BlnbowRAFuH8eetxeDczsEWfBoM='
    assert _extract_lab_report_param('https://example.com') == ''
    print('  ✓ _extract_lab_report_param')

    # ProgressTracker
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, 'test-progress.json')
        tracker = ProgressTracker(path)
        assert tracker.state['total_queries'] == 0
        assert len(tracker.searched) == 0

        tracker.record_search('0001', 'BATCH001', 'https://url1')
        tracker.record_search('0002', None, None)
        tracker.save()

        assert '0001' in tracker.searched
        assert '0002' in tracker.searched
        assert 'BATCH001' in tracker.found
        assert tracker.state['total_found'] == 1

        # Reload from disk.
        tracker2 = ProgressTracker(path)
        assert '0001' in tracker2.searched
        assert 'BATCH001' in tracker2.found
    print('  ✓ ProgressTracker')

    # MuvCollector initialization
    with tempfile.TemporaryDirectory() as tmpdir:
        collector = MuvCollector(
            pdf_dir=os.path.join(tmpdir, 'pdfs'),
            data_dir=tmpdir,
        )
        assert collector.pdf_dir.exists()
        assert collector.datasets_dir.exists()

        # catalog_existing on empty dir
        manifest = collector.catalog_existing()
        assert len(manifest) == 0

        # _convert_to_lab_results on empty
        results = collector._convert_to_lab_results(manifest)
        assert results == []

        # archive_stats
        stats = collector.archive_stats()
        assert stats['total_pdfs'] == 0
    print('  ✓ MuvCollector initialization and empty operations')

    print('✓ All unit tests passed')


def run_integration_test():
    """Run integration test requiring Selenium and network."""
    import tempfile

    print('Running integration test...')
    print('  This test requires Selenium + Chrome + network access.')

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = MuvCollector(
            pdf_dir=os.path.join(tmpdir, 'pdfs'),
            data_dir=tmpdir,
            search_pause=5.0,
        )

        # Test 1: Initialize Selenium and bypass age gate.
        print('  [1] Initializing Selenium...')
        collector._init_selenium(headless=True)

        print('  [2] Bypassing age gate...')
        gate_result = collector._bypass_age_gate()
        print(f'      Age gate bypassed: {gate_result}')

        if gate_result:
            # Test 2: Search for a known suffix.
            print('  [3] Searching for suffix "1521"...')
            batch_id, coa_url = collector._search_batch('1521')
            print(f'      Batch ID: {batch_id}')
            print(f'      COA URL: {coa_url}')

            if coa_url:
                # Test 3: Download the COA PDF.
                print('  [4] Downloading COA PDF...')
                pdf_path = collector._download_coa(
                    coa_url, batch_id or 'test-batch',
                )
                if pdf_path:
                    size = os.path.getsize(pdf_path)
                    print(
                        f'      ✓ Downloaded: '
                        f'{os.path.basename(pdf_path)} '
                        f'({size:,} bytes)'
                    )
                else:
                    print('      ⚠ Download returned None')

            # Test 4: Search for a non-existent suffix.
            print('  [5] Searching for non-existent suffix...')
            time.sleep(5)
            batch_id2, coa_url2 = collector._search_batch('99999')
            print(f'      Result: {batch_id2}, {coa_url2}')

        collector._quit_driver()

    print('✓ Integration test completed')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ CLI Entry Point                                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Collect MÜV (Verano) COA PDFs (Florida).',
    )
    parser.add_argument(
        '--pdf-dir',
        default='D:/data/florida/results/pdfs/muv',
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
        '--digits',
        type=int,
        default=DEFAULT_DIGITS,
        help=f'Number of suffix digits (default: {DEFAULT_DIGITS}).',
    )
    parser.add_argument(
        '--start',
        type=int,
        default=None,
        help='Starting suffix number (inclusive).',
    )
    parser.add_argument(
        '--end',
        type=int,
        default=None,
        help='Ending suffix number (exclusive).',
    )
    parser.add_argument(
        '--max-queries',
        type=int,
        default=DEFAULT_MAX_QUERIES,
        help=f'Max queries per session '
             f'(default: {DEFAULT_MAX_QUERIES}).',
    )
    parser.add_argument(
        '--resume',
        action='store_true',
        default=True,
        help='Resume from last position (default: True).',
    )
    parser.add_argument(
        '--no-resume',
        action='store_true',
        help='Start fresh (ignore previous progress).',
    )
    parser.add_argument(
        '--search-pause',
        type=float,
        default=DEFAULT_SEARCH_PAUSE,
        help=f'Seconds between search queries '
             f'(default: {DEFAULT_SEARCH_PAUSE}).',
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

    # ── Tests ─────────────────────────────────────────────────────
    if args.test:
        run_unit_tests()
        exit(0)

    if args.integration_test:
        run_integration_test()
        exit(0)

    # ── Main Collection ───────────────────────────────────────────
    with MuvCollector(
        pdf_dir=args.pdf_dir,
        data_dir=args.data_dir,
        search_pause=args.search_pause,
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