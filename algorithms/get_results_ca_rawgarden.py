"""
Get Results | California | Raw Garden
Copyright (c) 2022-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 8/23/2022
Updated: 2/21/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Collect cannabis lab result data published by Raw Garden.

    Raw Garden (operated by Central Coast Agriculture, Inc.) publishes
    COA PDFs organized by product category on their lab results page.
    This collector scrapes the product listings, downloads COA PDFs,
    and outputs standardized lab results following the Cannlytics schema.

    This collector does NOT require Selenium — it uses requests +
    BeautifulSoup for all data collection since Raw Garden renders
    lab result data server-side.

Data Source:
    - Raw Garden Lab Results: https://rawgarden.farm/lab-results/

Output:
    - Standardized CSV with LabResult schema fields
    - Downloaded COA PDFs in configured directory
    - Cached URLs to avoid re-downloading

Usage:
    ```python
    from algorithms.get_results_ca_rawgarden import RawGardenCollector

    with RawGardenCollector() as collector:
        results = collector.get_results()
    ```

Command Line:
    ```bash
    python algorithms/get_results_ca_rawgarden.py
    python algorithms/get_results_ca_rawgarden.py --test
    python algorithms/get_results_ca_rawgarden.py --integration-test
    ```
"""
# Standard imports:
from datetime import datetime
import hashlib
import os
import re
import time
from typing import List, Dict, Optional

# External imports:
from bs4 import BeautifulSoup
import pandas as pd
import requests

# Internal imports:
try:
    from config.results_config import PATHS, SOURCE_CONFIG, PRODUCT_TYPES
    from config.results_schema import LabResult, normalize_product_type, normalize_status
    from results_base import COACollector
except ImportError:
    # Fallback for standalone execution.
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config.results_config import PATHS, SOURCE_CONFIG, PRODUCT_TYPES
    from config.results_schema import LabResult, normalize_product_type, normalize_status
    from results_base import COACollector


# === Constants ===

BASE_URL = 'https://rawgarden.farm/lab-results/'

# Default request headers for Raw Garden.
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}

# Raw Garden producer metadata (from COA certificates).
RAW_GARDEN = {
    'business_dba_name': 'Raw Garden',
    'business_legal_name': 'Central Coast Agriculture, Inc.',
    'business_website': 'https://rawgarden.farm',
    'producer_license_number': 'CDPH-10003156',
    'distributor_license_number': 'C11-0000496-LIC',
    'producer_latitude': 34.6382,
    'producer_longitude': -120.4579,
    'producer_street_address': '1201 West Chestnut Ave, Lompoc, CA 93436',
    'producer_city': 'Lompoc',
    'producer_county': 'Santa Barbara',
    'producer_state': 'CA',
}

# Product subtype to normalized product type mapping.
SUBTYPE_TO_PRODUCT_TYPE = {
    'refined live resin cartridges': 'vape',
    'refined live resin ready-to-use': 'vape',
    'refined live resin': 'concentrate',
    'live resin': 'concentrate',
    'rg diamonds': 'concentrate',
    'rg crumble': 'concentrate',
    'rg shatter': 'concentrate',
    'rg sauce': 'concentrate',
    'rg wax': 'concentrate',
    'rg budder': 'concentrate',
    'rg live rosin': 'concentrate',
    'pre-rolls': 'preroll',
    'flower': 'flower',
    'infused pre-rolls': 'preroll',
    'gummies': 'edible',
    'edibles': 'edible',
}


def _kebab_case(text: str) -> str:
    """Convert a string to kebab-case.

    Replaces non-alphanumeric characters with hyphens and lowercases.

    Args:
        text: Input string.

    Returns:
        Kebab-case string.

    Examples:
        >>> _kebab_case('Refined Live Resin Cartridges')
        'refined-live-resin-cartridges'
        >>> _kebab_case('RG Diamonds')
        'rg-diamonds'
    """
    text = text.strip().lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')


class RawGardenCollector(COACollector):
    """Collector for Raw Garden cannabis lab results.

    Scrapes product listings with COA PDF links from Raw Garden's
    lab results page, downloads COA PDFs, and outputs standardized
    lab results following the Cannlytics schema.

    Attributes:
        state: Always 'ca' for California.
        source: Always 'raw_garden'.

    Example:
        ```python
        with RawGardenCollector() as collector:
            df = collector.get_results()
            print(f"Collected {len(df)} results")
        ```
    """

    def __init__(
            self,
            data_dir: Optional[str] = None,
            pdf_dir: Optional[str] = None,
            cache_path: Optional[str] = None,
            log_dir: Optional[str] = None,
            log_name: Optional[str] = None,
            pause_time: Optional[float] = None,
            verbose: bool = True,
        ):
        """Initialize the Raw Garden collector.

        Args:
            data_dir: Override for data directory.
            pdf_dir: Override for PDF storage directory.
            cache_path: Override for cache file path.
            log_dir: Override for log directory.
            log_name: Override for log file name.
            pause_time: Seconds between requests (default from config).
            verbose: Enable verbose logging.
        """
        source_config = SOURCE_CONFIG.get('raw_garden', {})
        default_pause = source_config.get('pause_time', 1.0)

        super().__init__(
            state='ca',
            source='raw_garden',
            data_dir=data_dir,
            pdf_dir=pdf_dir,
            cache_path=cache_path,
            log_dir=log_dir,
            log_name=log_name or 'get_results_ca_rawgarden',
            pause_time=pause_time or default_pause,
            verbose=verbose,
        )

    # === Data Extraction Helpers ===

    @staticmethod
    def _generate_result_id(product_name: str, coa_url: str) -> str:
        """Generate a unique result ID from product name and COA URL.

        Args:
            product_name: Name of the product.
            coa_url: URL of the COA PDF.

        Returns:
            16-character hex ID.
        """
        key_data = f"{product_name or ''}{coa_url or ''}"
        return hashlib.sha256(key_data.encode()).hexdigest()[:16]

    @staticmethod
    def _normalize_subtype(subtype: str) -> Optional[str]:
        """Map a Raw Garden product subtype to a standard product type.

        Args:
            subtype: Raw subtype string from the website (e.g.,
                'Refined Live Resin Cartridges').

        Returns:
            Normalized product type string or None.
        """
        if not subtype:
            return None
        key = subtype.strip().lower()
        # Exact match first.
        if key in SUBTYPE_TO_PRODUCT_TYPE:
            return SUBTYPE_TO_PRODUCT_TYPE[key]
        # Partial match.
        for pattern, product_type in SUBTYPE_TO_PRODUCT_TYPE.items():
            if pattern in key or key in pattern:
                return product_type
        return normalize_product_type(subtype)

    @staticmethod
    def _parse_date(date_str: str) -> Optional[str]:
        """Parse a date string into ISO format.

        Args:
            date_str: Date string from the website.

        Returns:
            ISO-formatted date string or None.
        """
        if not date_str:
            return None
        try:
            dt = pd.to_datetime(date_str)
            return dt.isoformat()[:19]
        except (ValueError, TypeError):
            return None

    # === Data Collection Methods ===

    def _get_products(self) -> List[Dict]:
        """Scrape product listings from the Raw Garden lab results page.

        Parses category sections on the page, extracting product name,
        subtype, date, and COA PDF URL for each product.

        Returns:
            List of product dictionaries.
        """
        self.logger.info(f'Fetching products from {BASE_URL}')
        response = requests.get(BASE_URL, headers=DEFAULT_HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        categories = soup.find_all('div', attrs={'class': 'category-content'})
        self.logger.info(f'Found {len(categories)} product categories')

        products = []
        for category in categories:
            # Extract subtype from category heading.
            h3 = category.find('h3')
            subtype = h3.text.strip() if h3 else 'Unknown'

            # Extract dates, names, and links.
            dates = category.find_all('h5', attrs={'class': 'result-date'})
            names = [
                el for el in category.find_all('h5')
                if el.get('class') is None
            ]
            links = category.find_all('a')

            for i, link in enumerate(links):
                try:
                    href = link.get('href')
                    if not href or not href.endswith('.pdf'):
                        continue

                    # Extract name and date (with index safety).
                    name = names[i].text.strip() if i < len(names) else None
                    date_str = dates[i].text.strip() if i < len(dates) else None

                    products.append({
                        'product_name': name,
                        'product_subtype': subtype,
                        'date_retail': self._parse_date(date_str),
                        'coa_pdf': href.split('/')[-1],
                        'coa_url': href,
                    })
                except (AttributeError, IndexError) as e:
                    self.logger.debug(f'Skipped product in {subtype}: {e}')
                    continue

        self.logger.info(f'Found {len(products)} products from Raw Garden')
        return products

    def _download_coas(self, products: List[Dict]) -> int:
        """Download COA PDFs for all products.

        Organizes PDFs into subtype subdirectories (kebab-case).
        Uses the base class cache to avoid re-downloading.

        Args:
            products: List of product dictionaries with 'coa_url'.

        Returns:
            Number of PDFs downloaded.
        """
        downloaded = 0

        for item in products:
            url = item.get('coa_url')
            if not url:
                continue

            # Check cache.
            url_hash = self.cache.hash_url(url)
            if self.cache.get(url_hash):
                self.logger.debug(f'Skipped (cached): {url}')
                self._stats['cached'] += 1
                continue

            # Determine subdirectory from subtype.
            subtype = item.get('product_subtype', 'unknown')
            subdir = _kebab_case(subtype)
            target_dir = self.pdf_dir / subdir
            target_dir.mkdir(parents=True, exist_ok=True)

            # Download PDF.
            filename = item.get('coa_pdf', f'{url_hash}.pdf')
            filepath = target_dir / filename

            success = self.download_file(url, str(filepath))
            if success:
                self.cache.set(url_hash, {
                    'type': 'download',
                    'url': url,
                    'file': str(filepath),
                    'product_name': item.get('product_name'),
                    'subtype': subtype,
                    'downloaded_at': datetime.now().isoformat(),
                })
                downloaded += 1

            self.rate_limit(multiplier=1.5)

        self.logger.info(f'Downloaded {downloaded} COA PDFs')
        return downloaded

    def _convert_to_lab_results(self, products: List[Dict]) -> List[Dict]:
        """Convert raw product data to standardized LabResult schema.

        Args:
            products: List of raw product dictionaries.

        Returns:
            List of dictionaries matching LabResult schema.
        """
        results = []

        for item in products:
            # Generate a unique ID.
            result_id = self._generate_result_id(
                item.get('product_name'),
                item.get('coa_url'),
            )

            # Normalize product type from subtype.
            product_type = self._normalize_subtype(
                item.get('product_subtype')
            )

            # Build standardized result.
            result = LabResult(
                # Identifiers
                id=result_id,
                sample_id=result_id,

                # Product info
                product_name=item.get('product_name'),
                product_type=product_type,
                product_subtype=item.get('product_subtype'),
                date_retail=item.get('date_retail'),

                # Producer info
                producer=RAW_GARDEN['business_dba_name'],
                producer_license_number=RAW_GARDEN['producer_license_number'],
                producer_latitude=RAW_GARDEN['producer_latitude'],
                producer_longitude=RAW_GARDEN['producer_longitude'],
                producer_address=RAW_GARDEN['producer_street_address'],
                producer_city=RAW_GARDEN['producer_city'],
                producer_county=RAW_GARDEN['producer_county'],
                producer_state=RAW_GARDEN['producer_state'],

                # Distributor info
                distributor=RAW_GARDEN['business_legal_name'],
                distributor_license_number=RAW_GARDEN['distributor_license_number'],

                # COA info
                coa_url=item.get('coa_url'),
                lab_results_url=item.get('coa_url'),

                # Metadata
                state='ca',
                source='raw_garden',
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )

            results.append(result.to_dict())

        return results

    # === Main Collection Method ===

    def get_results(
            self,
            download_pdfs: bool = True,
            save_intermediate: bool = True,
        ) -> pd.DataFrame:
        """Collect Raw Garden lab results.

        Pipeline phases:
            1. Scrape product listings from lab results page
            2. Download COA PDFs (organized by subtype)
            3. Convert to standardized LabResult schema

        Args:
            download_pdfs: Whether to download COA PDFs.
            save_intermediate: Save intermediate data files.

        Returns:
            DataFrame with standardized lab results.
        """
        self.logger.info('Starting Raw Garden collection...')

        # Phase 1: Get product listings.
        products = self._get_products()
        products_df = pd.DataFrame(products)

        if save_intermediate:
            self.save_results(
                products_df,
                prefix='ca-rawgarden-products',
                save_latest=False,
            )

        if products_df.empty:
            self.logger.warning('No products found')
            return pd.DataFrame()

        # Phase 2: Download COA PDFs.
        if download_pdfs:
            self._download_coas(products)

        # Phase 3: Convert to standardized schema.
        standardized = self._convert_to_lab_results(products)
        results_df = pd.DataFrame(standardized)

        # Save final results.
        self.save_results(
            results_df,
            prefix='ca-rawgarden-results',
            save_latest=True,
        )

        self.logger.info(
            f'✓ Collected {len(results_df)} results from Raw Garden (CA)'
        )
        return results_df


# =============================================================================
# Testing
# =============================================================================

def run_unit_tests():
    """Run unit tests for helper methods.

    These tests can run without network access.
    """
    print('\n' + '=' * 60)
    print('UNIT TESTS: RawGardenCollector')
    print('=' * 60)

    passed = 0
    failed = 0

    # --- Test: _generate_result_id ---
    print('\n--- Test: _generate_result_id ---')
    id1 = RawGardenCollector._generate_result_id('Product A', 'https://example.com/a.pdf')
    id2 = RawGardenCollector._generate_result_id('Product A', 'https://example.com/a.pdf')
    id3 = RawGardenCollector._generate_result_id('Product B', 'https://example.com/a.pdf')

    tests = [
        ('ID length is 16', len(id1) == 16),
        ('Same inputs produce same ID', id1 == id2),
        ('Different inputs produce different ID', id1 != id3),
        ('None inputs handled', len(RawGardenCollector._generate_result_id(None, None)) == 16),
    ]
    for desc, result in tests:
        status = '✓' if result else '✗'
        print(f'  {status} {desc}')
        passed += 1 if result else 0
        failed += 0 if result else 1

    # --- Test: _normalize_subtype ---
    print('\n--- Test: _normalize_subtype ---')
    subtype_tests = [
        ('Refined Live Resin Cartridges', 'vape'),
        ('Refined Live Resin', 'concentrate'),
        ('Pre-Rolls', 'preroll'),
        ('Flower', 'flower'),
        ('Gummies', 'edible'),
        ('', None),
        (None, None),
    ]
    for input_val, expected in subtype_tests:
        result = RawGardenCollector._normalize_subtype(input_val)
        match = result == expected
        status = '✓' if match else '✗'
        print(f"  {status} _normalize_subtype('{input_val}') = '{result}' (expected '{expected}')")
        passed += 1 if match else 0
        failed += 0 if match else 1

    # --- Test: _parse_date ---
    print('\n--- Test: _parse_date ---')
    date_tests = [
        ('2024-01-15', '2024-01-15T00:00:00'),
        ('January 15, 2024', '2024-01-15T00:00:00'),
        ('', None),
        (None, None),
        ('not a date', None),
    ]
    for input_val, expected in date_tests:
        result = RawGardenCollector._parse_date(input_val)
        match = result == expected
        status = '✓' if match else '✗'
        print(f"  {status} _parse_date('{input_val}') = '{result}' (expected '{expected}')")
        passed += 1 if match else 0
        failed += 0 if match else 1

    # --- Test: _kebab_case ---
    print('\n--- Test: _kebab_case ---')
    kebab_tests = [
        ('Refined Live Resin Cartridges', 'refined-live-resin-cartridges'),
        ('RG Diamonds', 'rg-diamonds'),
        ('Pre-Rolls', 'pre-rolls'),
        ('  Flower  ', 'flower'),
    ]
    for input_val, expected in kebab_tests:
        result = _kebab_case(input_val)
        match = result == expected
        status = '✓' if match else '✗'
        print(f"  {status} _kebab_case('{input_val}') = '{result}' (expected '{expected}')")
        passed += 1 if match else 0
        failed += 0 if match else 1

    # --- Summary ---
    print('\n' + '-' * 60)
    print(f'Results: {passed} passed, {failed} failed')
    print('=' * 60)

    return failed == 0


def run_integration_test():
    """Run integration test with actual network requests.

    Requires network access to rawgarden.farm.
    """
    print('\n' + '=' * 60)
    print('INTEGRATION TEST: RawGardenCollector')
    print('=' * 60)

    try:
        with RawGardenCollector(verbose=True) as collector:
            # Test product listing fetch.
            products = collector._get_products()
            print(f'✓ Found {len(products)} products')

            if not products:
                print('✗ No products found — site may have changed')
                return False

            # Validate first product structure.
            first = products[0]
            required_fields = ['product_name', 'coa_url', 'product_subtype']
            missing = [f for f in required_fields if not first.get(f)]
            if missing:
                print(f'✗ First product missing fields: {missing}')
                return False
            print(f'✓ First product: {first["product_name"]}')
            print(f'  Subtype: {first["product_subtype"]}')
            print(f'  COA URL: {first["coa_url"]}')

            # Check that COA URLs point to PDFs.
            pdf_products = [p for p in products if p.get('coa_url', '').endswith('.pdf')]
            print(f'✓ {len(pdf_products)}/{len(products)} products have PDF URLs')

            # Check that subtypes are present.
            subtypes = set(p.get('product_subtype') for p in products if p.get('product_subtype'))
            print(f'✓ Product subtypes found: {subtypes}')

            print('\n✓ Integration test passed')
            return True

    except Exception as e:
        print(f'\n✗ Integration test failed: {e}')
        import traceback
        traceback.print_exc()
        return False


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Collect Raw Garden lab results'
    )
    parser.add_argument(
        '--test', action='store_true',
        help='Run unit tests only',
    )
    parser.add_argument(
        '--integration-test', action='store_true',
        help='Run integration test',
    )
    parser.add_argument(
        '--no-download', dest='download_pdfs', action='store_false',
        default=True,
        help='Skip PDF downloads',
    )
    parser.add_argument(
        '--data-dir', type=str, default=None,
        help='Override data directory',
    )
    parser.add_argument(
        '--pdf-dir', type=str, default=None,
        help='Override PDF directory',
    )

    args = parser.parse_args()

    if args.test:
        success = run_unit_tests()
        exit(0 if success else 1)

    elif args.integration_test:
        success = run_integration_test()
        exit(0 if success else 1)

    else:
        collector = RawGardenCollector(
            data_dir=args.data_dir,
            pdf_dir=args.pdf_dir,
        )

        with collector:
            results = collector.get_results(
                download_pdfs=args.download_pdfs,
            )
            print(f'\nCollected {len(results)} results')