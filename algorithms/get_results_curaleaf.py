"""
Get Results | Curaleaf (Multi-State)
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 8/24/2024
Updated: 2/28/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Collect cannabis lab result COA PDFs from Curaleaf's transparency
    portal at coas.curaleaf.com/transparency/.

    Curaleaf is a multi-state operator (MSO) that publishes COA results
    through a centralized transparency portal. The portal serves COAs
    from multiple states (including AZ, NY, and others), so this
    collector is designed as a multi-state module rather than an
    AZ-specific one.

    Collection Strategy:
        Phase 1: Catalog existing local archive of COA PDFs.
        Phase 2: Discover COAs by enumerating numeric batch number
                 queries against the transparency portal.
        Phase 3: Download new COA PDFs found in search results.
        Phase 4: Convert manifest into standardized LabResult records.

    Search Strategy:
        The portal accepts batch number lookups. The collector
        systematically searches 3-digit and 4-digit numeric
        combinations. This is a slow process due to the need
        for rate limiting (Curaleaf's portal is rate-sensitive).

    NOTE: This collector gathers COAs from ALL states that Curaleaf
    serves, not just Arizona. State-specific filtering can be done
    downstream when processing results.

Data Sources:
    - [Curaleaf Transparency](https://coas.curaleaf.com/transparency/)
    - COA PDFs linked from search results

Output:
    - PDF directory with COA files (named by URL hash)
    - Manifest CSV cataloging all collected COAs
    - Discovered URLs CSV (per-scrape snapshot)
    - Standardized CSV with LabResult schema fields

Usage:
    ```python
    from algorithms.get_results_curaleaf import CuraleafCollector

    with CuraleafCollector() as collector:
        results = collector.get_results()
    ```

Command Line:
    ```bash
    # Full collection (3+4 digit queries)
    python algorithms/get_results_curaleaf.py

    # Only 3-digit queries (faster)
    python algorithms/get_results_curaleaf.py --digits 3

    # Resume from last position
    python algorithms/get_results_curaleaf.py --resume

    # Catalog only
    python algorithms/get_results_curaleaf.py --catalog-only

    # Run unit tests
    python algorithms/get_results_curaleaf.py --test
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

BASE_URL = 'https://coas.curaleaf.com/transparency/'

CURALEAF_PRODUCER = {
    'producer': 'Curaleaf',
    'producer_dba': 'Curaleaf Holdings, Inc.',
    'producer_website': 'https://curaleaf.com',
}

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
}

MIN_PDF_SIZE = 5 * 1024
DEFAULT_SEARCH_PAUSE = 5.0
DEFAULT_DOWNLOAD_PAUSE = 2.0
DEFAULT_DIGITS = 3
DEFAULT_MAX_QUERIES = 10_000

logger = logging.getLogger('get_results_curaleaf')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Helper Functions                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

def _generate_result_id(batch_id: str) -> str:
    return hashlib.sha256(batch_id.encode('utf-8')).hexdigest()[:16]

def _hash_url(url: str) -> str:
    return hashlib.md5(url.encode('utf-8')).hexdigest()

def _is_valid_pdf(data: bytes, min_size: int = MIN_PDF_SIZE) -> bool:
    if len(data) < min_size:
        return False
    return data[:5] == b'%PDF-'

def _is_valid_pdf_file(filepath: str, min_size: int = MIN_PDF_SIZE) -> bool:
    try:
        size = os.path.getsize(filepath)
        if size < min_size:
            return False
        with open(filepath, 'rb') as f:
            return f.read(5).startswith(b'%PDF-')
    except (OSError, IOError):
        return False

def _generate_search_queries(
        digits: int = DEFAULT_DIGITS,
        include_four_digit: bool = True,
    ) -> List[str]:
    """Generate shuffled numeric search queries.

    For Curaleaf, the default strategy is 3-digit (000-999)
    plus optionally 4-digit (0000-9999) queries.
    """
    queries = [str(i).zfill(digits) for i in range(10 ** digits)]
    if include_four_digit and digits == 3:
        queries += [str(i).zfill(4) for i in range(10000)]
    random.shuffle(queries)
    return queries


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Progress Tracker                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

class ProgressTracker:
    """Track search progress for resumable collection."""

    def __init__(self, path: str):
        self.path = path
        self.state = self._load()

    def _load(self) -> Dict:
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
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump(self.state, f, indent=2)

    @property
    def searched(self) -> set:
        return set(self.state.get('searched_queries', []))

    @property
    def found(self) -> Dict:
        return self.state.get('found_coas', {})

    def record_search(self, query: str, results: Optional[List[Dict]] = None):
        if query not in self.state['searched_queries']:
            self.state['searched_queries'].append(query)
        self.state['last_query'] = query
        self.state['total_queries'] = len(self.state['searched_queries'])
        if results:
            for item in results:
                url_hash = item.get('url_hash', '')
                if url_hash:
                    self.state['found_coas'][url_hash] = item
            self.state['total_found'] = len(self.state['found_coas'])


# ╔══════════════════════════════════════════════════════════════════╗
# ║ CuraleafCollector                                                ║
# ╚══════════════════════════════════════════════════════════════════╝

class CuraleafCollector:
    """Collector for Curaleaf COA PDFs (multi-state).

    Discovers COAs by enumerating batch number queries against
    the Curaleaf transparency portal, downloads PDFs, and
    converts them to standardized LabResult records.
    """

    def __init__(
            self,
            pdf_dir: str = '',
            data_dir: str = '',
            search_pause: float = DEFAULT_SEARCH_PAUSE,
            download_pause: float = DEFAULT_DOWNLOAD_PAUSE,
            verbose: bool = True,
        ):
        if not data_dir:
            data_dir = 'D:/data/multi-state/results'
        if not pdf_dir:
            pdf_dir = os.path.join(data_dir, 'pdfs', 'curaleaf')

        self.pdf_dir = Path(pdf_dir)
        self.data_dir = Path(data_dir)
        self.datasets_dir = self.data_dir / 'datasets'
        self.manifest_path = self.datasets_dir / 'curaleaf-manifest.csv'
        self.progress_path = self.datasets_dir / 'curaleaf-progress.json'
        self.search_pause = search_pause
        self.download_pause = download_pause
        self.verbose = verbose

        self.logger = logging.getLogger('get_results_curaleaf')
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

        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.driver = None

        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.datasets_dir.mkdir(parents=True, exist_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._quit_driver()
        self.session.close()

    def _init_selenium(self, headless: bool = True) -> None:
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
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    def _is_session_alive(self) -> bool:
        if self.driver is None:
            return False
        try:
            _ = self.driver.title
            return True
        except Exception:
            return False

    def _respectful_pause(self, base=None, multiplier=1.0):
        if base is None:
            base = self.search_pause
        jitter = random.uniform(0, base * 0.3)
        time.sleep(base * multiplier + jitter)

    def _clear_browser_state(self):
        """Clear cookies and storage to prevent memory buildup."""
        try:
            self.driver.delete_all_cookies()
            self.driver.execute_script(
                "try{window.localStorage.clear();"
                "window.sessionStorage.clear();}catch(e){}"
            )
        except Exception:
            pass

    # ── Phase 1: Catalog ─────────────────────────────────────────

    def catalog_existing(self, incremental=True, compute_hashes=True):
        existing = pd.DataFrame()
        if incremental and self.manifest_path.exists():
            try:
                existing = pd.read_csv(str(self.manifest_path))
                self.logger.info(f'Manifest: {len(existing)} entries')
            except Exception:
                pass

        existing_names = set()
        if len(existing) > 0 and 'file_name' in existing.columns:
            existing_names = set(existing['file_name'].astype(str))

        pdf_files = sorted([
            f for f in os.listdir(str(self.pdf_dir))
            if f.lower().endswith('.pdf')
        ]) if self.pdf_dir.exists() else []
        self.logger.info(f'{len(pdf_files)} PDF(s) in {self.pdf_dir}')

        new_rows = []
        now = datetime.now().isoformat()
        for f in pdf_files:
            if incremental and f in existing_names:
                continue
            fp = self.pdf_dir / f
            if not _is_valid_pdf_file(str(fp)):
                continue
            file_size = fp.stat().st_size
            file_hash = ''
            if compute_hashes:
                sha = hashlib.sha256()
                with open(str(fp), 'rb') as fh:
                    for chunk in iter(lambda: fh.read(65536), b''):
                        sha.update(chunk)
                file_hash = sha.hexdigest()
            url_hash = f.replace('.pdf', '').strip()
            new_rows.append({
                'file_name': f, 'url_hash': url_hash,
                'file_path': str(fp), 'file_size': file_size,
                'file_hash': file_hash, 'date_cataloged': now,
                'source': 'curaleaf', 'coa_url': '',
                'batch_number': '', 'query': '',
            })

        if new_rows:
            self.logger.info(f'Cataloged {len(new_rows)} new PDF(s)')
            manifest = pd.concat(
                [existing, pd.DataFrame(new_rows)],
                ignore_index=True,
            )
        else:
            manifest = existing if len(existing) > 0 else pd.DataFrame()

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
                self.logger.info(f'Removed {removed} duplicates')

        if len(manifest) > 0:
            manifest.to_csv(str(self.manifest_path), index=False)

        return manifest

    # ── Phase 2: Discover COAs ───────────────────────────────────

    def discover_coas(
            self,
            search_queries=None,
            digits=DEFAULT_DIGITS,
            include_four_digit=True,
            max_queries=DEFAULT_MAX_QUERIES,
            resume=True,
            headless=True,
        ):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        tracker = ProgressTracker(str(self.progress_path))
        if not tracker.state.get('started_at'):
            tracker.state['started_at'] = datetime.now().isoformat()

        if search_queries is None:
            search_queries = _generate_search_queries(
                digits, include_four_digit,
            )

        self._init_selenium(headless=headless)
        wait = WebDriverWait(self.driver, 10)

        discovered = []
        queries_this_session = 0

        self.logger.info(
            f'Starting discovery: {len(search_queries)} queries'
        )
        if resume:
            self.logger.info(
                f'Resuming: {len(tracker.searched)} searched, '
                f'{len(tracker.found)} found'
            )

        for query in search_queries:
            if queries_this_session >= max_queries:
                break
            if resume and query in tracker.searched:
                continue
            if not self._is_session_alive():
                self.logger.warning('Dead session, reinitializing...')
                tracker.save()
                self._quit_driver()
                time.sleep(2)
                self._init_selenium(headless=headless)
                wait = WebDriverWait(self.driver, 10)

            search_url = f'{BASE_URL}{quote_plus(query)}'
            try:
                self.driver.get(search_url)
            except Exception as exc:
                self.logger.warning(f'Navigation error: {exc}')
                tracker.record_search(query)
                queries_this_session += 1
                continue

            # Wait for table or timeout.
            try:
                table = wait.until(EC.presence_of_element_located(
                    (By.TAG_NAME, 'table')
                ))
            except Exception:
                tracker.record_search(query)
                queries_this_session += 1
                self._respectful_pause()
                continue

            self._respectful_pause(
                base=self.search_pause, multiplier=0.3,
            )

            rows = table.find_elements(By.TAG_NAME, 'tr')[1:]
            self.logger.info(
                f'Query "{query}": {len(rows)} results'
            )

            query_results = []
            for row in rows:
                try:
                    cells = row.find_elements(By.TAG_NAME, 'td')
                    if len(cells) < 2:
                        continue
                    batch_number = cells[0].text.strip()
                    try:
                        view_link = row.find_element(
                            By.CSS_SELECTOR, "a[href*='.pdf']",
                        )
                        coa_url = view_link.get_attribute('href')
                    except Exception:
                        continue

                    url_hash = _hash_url(coa_url)
                    item = {
                        'url_hash': url_hash,
                        'coa_url': coa_url,
                        'batch_number': batch_number,
                        'query': query,
                        'discovered_at': datetime.now().isoformat(),
                    }

                    # Download.
                    dest = str(self.pdf_dir / f'{url_hash}.pdf')
                    if not os.path.exists(dest):
                        try:
                            resp = self.session.get(
                                coa_url, allow_redirects=True,
                                timeout=30,
                            )
                            if resp.status_code == 200:
                                data = resp.content
                                if _is_valid_pdf(data):
                                    with open(dest, 'wb') as f:
                                        f.write(data)
                                    self.logger.info(
                                        f'Downloaded: {batch_number}'
                                    )
                            self._respectful_pause(
                                base=self.download_pause,
                            )
                        except Exception as e:
                            self.logger.debug(
                                f'Download error: {e}'
                            )

                    query_results.append(item)
                    discovered.append(item)
                except Exception as e:
                    self.logger.debug(f'Row error: {e}')

            tracker.record_search(query, query_results)
            queries_this_session += 1

            # Clear browser state periodically.
            if queries_this_session % 50 == 0:
                self._clear_browser_state()
                tracker.save()
                self.logger.info(
                    f'Progress: {queries_this_session} queries, '
                    f'{len(discovered)} new ({len(tracker.found)} total)'
                )

            self._respectful_pause()

        tracker.save()
        self._quit_driver()

        df = pd.DataFrame(discovered)
        self.logger.info(
            f'Discovery: {queries_this_session} queries, '
            f'{len(discovered)} new ({len(tracker.found)} total)'
        )

        if len(df) > 0:
            ts = datetime.now().strftime('%Y-%m-%dT%H-%M-%S')
            df.to_csv(
                str(self.datasets_dir / f'lab-result-urls-curaleaf-{ts}.csv'),
                index=False,
            )

        return df

    # ── Phase 4: Convert ─────────────────────────────────────────

    def _convert_to_lab_results(self, manifest):
        results = []
        for _, row in manifest.iterrows():
            url_hash = str(row.get('url_hash', ''))
            coa_url = str(row.get('coa_url', ''))
            batch_number = str(row.get('batch_number', ''))
            file_name = str(row.get('file_name', ''))
            result_id = _generate_result_id(
                coa_url if coa_url else url_hash,
            )
            record = {
                'result_id': result_id,
                'sample_id': url_hash,
                'batch_number': batch_number,
                'product_name': '',
                'product_type': '',
                'date_tested': '',
                'lab': '',
                'lab_results_url': coa_url,
                'coa_pdf': file_name,
                'source': 'curaleaf_transparency',
                'source_url': BASE_URL,
                'state': '',  # Multi-state; set downstream.
                **CURALEAF_PRODUCER,
                'date_collected': str(row.get('date_cataloged', '')),
                'file_hash': str(row.get('file_hash', '')),
            }
            results.append(record)
        return results

    # ── Main Pipeline ────────────────────────────────────────────

    def get_results(
            self,
            catalog_only=False,
            discover=True,
            download=True,
            headless=True,
            save_results=True,
            digits=DEFAULT_DIGITS,
            include_four_digit=True,
            max_queries=DEFAULT_MAX_QUERIES,
            resume=True,
            search_queries=None,
        ):
        self.logger.info('Starting Curaleaf COA collection...')

        manifest = self.catalog_existing()

        if catalog_only:
            lab_results = self._convert_to_lab_results(manifest)
            results_df = pd.DataFrame(lab_results)
            if save_results and len(results_df) > 0:
                outpath = self.datasets_dir / 'results-curaleaf-latest.csv'
                results_df.to_csv(str(outpath), index=False)
            return results_df

        discovered = pd.DataFrame()
        if discover:
            discovered = self.discover_coas(
                search_queries=search_queries,
                digits=digits,
                include_four_digit=include_four_digit,
                max_queries=max_queries,
                resume=resume,
                headless=headless,
            )

        manifest = self.catalog_existing()

        if len(discovered) > 0 and len(manifest) > 0:
            url_map = dict(zip(
                discovered['url_hash'].astype(str),
                discovered['coa_url'].astype(str),
            ))
            batch_map = dict(zip(
                discovered['url_hash'].astype(str),
                discovered['batch_number'].astype(str),
            ))
            manifest['coa_url'] = manifest['url_hash'].map(
                url_map
            ).fillna(manifest.get('coa_url', ''))
            manifest['batch_number'] = manifest['url_hash'].map(
                batch_map
            ).fillna(manifest.get('batch_number', ''))

        lab_results = self._convert_to_lab_results(manifest)
        results_df = pd.DataFrame(lab_results)
        if save_results and len(results_df) > 0:
            outpath = self.datasets_dir / 'results-curaleaf-latest.csv'
            results_df.to_csv(str(outpath), index=False)
            self.logger.info(f'Results: {len(results_df)} → {outpath}')

        self.logger.info(f'Total results: {len(results_df)}')
        return results_df

    def archive_stats(self):
        pdf_files = [
            f for f in os.listdir(str(self.pdf_dir))
            if f.lower().endswith('.pdf')
        ] if self.pdf_dir.exists() else []
        total_size = sum(
            (self.pdf_dir / f).stat().st_size for f in pdf_files
            if (self.pdf_dir / f).exists()
        )
        progress = {}
        if self.progress_path.exists():
            tracker = ProgressTracker(str(self.progress_path))
            progress = {
                'total_queries': tracker.state.get('total_queries', 0),
                'total_found': tracker.state.get('total_found', 0),
            }
        return {
            'total_pdfs': len(pdf_files),
            'total_size_mb': round(total_size / (1024 ** 2), 2),
            'discovery_progress': progress,
        }


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Unit Tests                                                       ║
# ╚══════════════════════════════════════════════════════════════════╝

def run_unit_tests():
    import tempfile
    print('Running unit tests...')

    assert len(_generate_result_id('test')) == 16
    assert len(_hash_url('test')) == 32
    assert _is_valid_pdf(b'%PDF-1.4' + b'\x00' * 10000)
    assert not _is_valid_pdf(b'<html>')
    print('  ✓ Helper functions')

    queries = _generate_search_queries(2, include_four_digit=False)
    assert len(queries) == 100
    print('  ✓ _generate_search_queries')

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, 'progress.json')
        tracker = ProgressTracker(path)
        tracker.record_search('001', [{'url_hash': 'a', 'coa_url': 'u'}])
        tracker.save()
        tracker2 = ProgressTracker(path)
        assert '001' in tracker2.searched
        assert 'a' in tracker2.found
    print('  ✓ ProgressTracker')

    with tempfile.TemporaryDirectory() as tmpdir:
        c = CuraleafCollector(
            pdf_dir=os.path.join(tmpdir, 'pdfs'),
            data_dir=tmpdir,
        )
        assert c.pdf_dir.exists()
        m = c.catalog_existing()
        assert len(m) == 0
        assert c.archive_stats()['total_pdfs'] == 0
    print('  ✓ CuraleafCollector initialization')

    print('✓ All unit tests passed')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(
        description='Collect Curaleaf COA PDFs (multi-state).',
    )
    parser.add_argument('--pdf-dir', default='D:/data/multi-state/results/pdfs/curaleaf')
    parser.add_argument('--data-dir', default='D:/data/multi-state/results')
    parser.add_argument('--catalog-only', action='store_true')
    parser.add_argument('--no-discover', action='store_true')
    parser.add_argument('--digits', type=int, default=DEFAULT_DIGITS)
    parser.add_argument('--no-four-digit', action='store_true')
    parser.add_argument('--max-queries', type=int, default=DEFAULT_MAX_QUERIES)
    parser.add_argument('--resume', action='store_true', default=True)
    parser.add_argument('--no-resume', action='store_true')
    parser.add_argument('--no-headless', action='store_true')
    parser.add_argument('--test', action='store_true')
    args = parser.parse_args()

    if args.test:
        run_unit_tests()
        exit(0)

    with CuraleafCollector(
        pdf_dir=args.pdf_dir,
        data_dir=args.data_dir,
    ) as collector:
        results = collector.get_results(
            catalog_only=args.catalog_only,
            discover=not args.no_discover,
            headless=not args.no_headless,
            digits=args.digits,
            include_four_digit=not args.no_four_digit,
            max_queries=args.max_queries,
            resume=not args.no_resume,
        )
        print(f'Total results: {len(results)}')
        print(f'Archive: {collector.archive_stats()}')
