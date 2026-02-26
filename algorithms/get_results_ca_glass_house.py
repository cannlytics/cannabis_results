"""
Get Results | California | Glass House Farms
Copyright (c) 2023-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
    Candace O'Sullivan-Sutherland <https://github.com/candy-o>
Created: 5/25/2023
Updated: 2/19/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Collect cannabis lab result data published by Glass House Farms.

    This collector scrapes strain and COA data from the Glass House Farms
    website, downloads COA PDFs, and outputs standardized lab results
    following the Cannlytics schema.

    Glass House Farms is a vertically integrated cannabis company based
    in Carpinteria, CA (Santa Barbara County). They publish strain pages
    with linked COA PDFs, lineage information, and indica/sativa
    classification data.

Data Source:
    - Glass House Farms Strains: https://glasshousefarms.org/strains/

Output:
    - Standardized CSV with LabResult schema fields
    - Downloaded COA PDFs in configured directory
    - Cached URLs to avoid re-downloading

Usage:
    ```python
    from algorithms.get_results_ca_glass_house import GlassHouseFarmsCollector

    with GlassHouseFarmsCollector() as collector:
        results = collector.get_results()
    ```

Command Line:
    ```bash
    python algorithms/get_results_ca_glass_house.py
    python algorithms/get_results_ca_glass_house.py --test
    python algorithms/get_results_ca_glass_house.py --integration-test
    ```
"""
# Standard imports:
from datetime import datetime
import hashlib
import os
import time
from typing import List, Dict, Optional

# External imports:
import pandas as pd
import requests
from bs4 import BeautifulSoup

# Internal imports:
try:
    from config.results_config import PATHS, SOURCE_CONFIG, PRODUCT_TYPES
    from config.results_schema import LabResult, normalize_product_type, normalize_status
    from results_base import COACollector
except ImportError:
    # Fallback for standalone execution
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config.results_config import PATHS, SOURCE_CONFIG, PRODUCT_TYPES
    from config.results_schema import LabResult, normalize_product_type, normalize_status
    from results_base import COACollector


# === Constants ===

BASE_URL = 'https://glasshousefarms.org/strains/'

# Glass House Farms producer metadata.
GLASS_HOUSE_FARMS = {
    'business_dba_name': 'Glass House Farms',
    'business_website': 'https://glasshousefarms.org',
    'business_image_url': 'https://glassfarms.wpenginepowered.com/wp-content/uploads/2021/10/new-ghf-menu.svg',
    'producer_license_number': 'CCL18-0000512',
    'producer_latitude': 34.404930,
    'producer_longitude': -119.518250,
    'producer_street_address': '5601 Casitas Pass Rd, Carpinteria, CA 93013',
    'producer_city': 'Carpinteria',
    'producer_county': 'Santa Barbara',
    'producer_state': 'CA',
}

# Strain type classification to indica/sativa percentages.
STRAIN_TYPES = {
    'sativa': {'classification': 'Sativa', 'sativa_percentage': 1.0, 'indica_percentage': 0.0},
    'sativaDominant': {'classification': 'S-Hybrid', 'sativa_percentage': 0.75, 'indica_percentage': 0.25},
    'hybrid': {'classification': 'Hybrid', 'sativa_percentage': 0.5, 'indica_percentage': 0.5},
    'indica': {'classification': 'Indica', 'sativa_percentage': 0.0, 'indica_percentage': 1.0},
    'indicaDominant': {'classification': 'I-Hybrid', 'sativa_percentage': 0.25, 'indica_percentage': 0.75},
    'cbd': {'classification': 'CBD', 'sativa_percentage': 0.0, 'indica_percentage': 0.0},
    'cbdt': {'classification': 'CBD', 'sativa_percentage': 0.0, 'indica_percentage': 0.0},
}


class GlassHouseFarmsCollector(COACollector):
    """Collector for Glass House Farms cannabis lab results.

    Collects strain listings with COA PDF links from the Glass House
    Farms website, downloads COA PDFs, and outputs standardized lab
    results following the Cannlytics schema.

    This collector does NOT require Selenium — it uses requests +
    BeautifulSoup for all data collection since Glass House Farms
    renders strain data server-side.

    Attributes:
        state: Always 'ca' for California.
        source: Always 'glass_house'.

    Example:
        ```python
        with GlassHouseFarmsCollector() as collector:
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
        """Initialize the Glass House Farms collector.

        Args:
            data_dir: Override for data directory.
            pdf_dir: Override for PDF storage directory.
            cache_path: Override for cache file path.
            log_dir: Override for log directory.
            log_name: Override for log file name.
            pause_time: Seconds between requests (default from config).
            verbose: Enable verbose logging.
        """
        # Get source-specific pause time from config.
        source_config = SOURCE_CONFIG.get('glass_house', {})
        default_pause = source_config.get('pause_time', 3.0)

        super().__init__(
            state='ca',
            source='glass_house',
            data_dir=data_dir,
            pdf_dir=pdf_dir,
            cache_path=cache_path,
            log_dir=log_dir,
            log_name=log_name or 'get_results_ca_glass_house',
            pause_time=pause_time or default_pause,
            verbose=verbose,
        )

    # === Data Extraction Helpers ===

    @staticmethod
    def _generate_result_id(strain_name: str, coa_url: str) -> str:
        """Generate a unique result ID from strain name and COA URL.

        Args:
            strain_name: Name of the strain.
            coa_url: URL of the COA PDF.

        Returns:
            16-character hex ID.
        """
        key_data = f"{strain_name or ''}{coa_url or ''}"
        return hashlib.sha256(key_data.encode()).hexdigest()[:16]

    @staticmethod
    def _extract_strain_type(wave_element) -> Dict:
        """Extract indica/sativa classification from wave div classes.

        Args:
            wave_element: BeautifulSoup element with wave classification.

        Returns:
            Dictionary with classification, indica_percentage, and
            sativa_percentage.
        """
        if not wave_element:
            return {
                'classification': None,
                'indica_percentage': None,
                'sativa_percentage': None,
            }

        wave_classes = wave_element.get('class', [])
        wave_classes = [cls for cls in wave_classes if cls != 'wave']

        for cls in wave_classes:
            if cls in STRAIN_TYPES:
                return {
                    'classification': STRAIN_TYPES[cls]['classification'],
                    'indica_percentage': STRAIN_TYPES[cls]['indica_percentage'],
                    'sativa_percentage': STRAIN_TYPES[cls]['sativa_percentage'],
                }

        return {
            'classification': None,
            'indica_percentage': None,
            'sativa_percentage': None,
        }

    # === Data Collection Methods ===

    def _get_strains(self) -> List[Dict]:
        """Scrape strain data from the Glass House Farms strains page.

        Returns:
            List of strain dictionaries with name, type, URL, image,
            and indica/sativa percentages.
        """
        self.logger.info(f'Fetching strains from {BASE_URL}')
        response = requests.get(BASE_URL, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        strain_items = soup.find_all(class_='item')

        strains = []
        for item in strain_items:
            obs = {}

            # Extract image URL.
            img_tag = item.find('img')
            obs['image_url'] = img_tag['src'] if img_tag else None

            # Extract strain type text (e.g., "Sativa", "Indica").
            strain_type_el = item.find('h5')
            obs['strain_type'] = strain_type_el.text.strip() if strain_type_el else None

            # Extract strain name (removing the strain type prefix).
            strain_name_el = item.find('h4')
            strain_name = strain_name_el.text.strip() if strain_name_el else None
            if strain_name and obs['strain_type']:
                strain_name = strain_name.replace('\n', '').replace(obs['strain_type'], '').strip()
            obs['strain_name'] = strain_name

            # Extract strain URL and ID.
            exp_link = item.find('a', class_='exp')
            if exp_link:
                obs['strain_url'] = exp_link.get('href', '')
                obs['strain_id'] = obs['strain_url'].rstrip('/').split('/')[-1]
            else:
                obs['strain_url'] = None
                obs['strain_id'] = None

            # Determine classification from wave div CSS classes.
            wave = item.find('div', class_='wave')
            type_info = self._extract_strain_type(wave)
            obs.update(type_info)

            strains.append(obs)

        self.logger.info(f'Found {len(strains)} strains from Glass House Farms')
        return strains

    def _get_strain_details(self, strain: Dict) -> Dict:
        """Visit a strain page to extract lineage and COA PDF links.

        Args:
            strain: Strain dictionary with at least 'strain_url'.

        Returns:
            Augmented strain dictionary with 'lineage' and 'coa_urls'.
        """
        strain_url = strain.get('strain_url')
        if not strain_url:
            strain['lineage'] = []
            strain['coa_urls'] = []
            return strain

        self.rate_limit()
        try:
            response = requests.get(strain_url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            self.logger.warning(f"Failed to fetch {strain_url}: {e}")
            strain['lineage'] = []
            strain['coa_urls'] = []
            return strain

        soup = BeautifulSoup(response.content, 'html.parser')

        # Extract lineage.
        try:
            content = soup.find('div', class_='content')
            divs = content.find_all('div', class_='et_pb_column')
            lineage_text = divs[2].text.split('Lineage')[1].replace('\n', '').strip()
            strain['lineage'] = [x.strip() for x in lineage_text.split(' x ') if x.strip()]
        except (AttributeError, IndexError):
            strain['lineage'] = []
            self.logger.debug(f"No lineage found for {strain.get('strain_name')}")

        # Find all COA PDF links on the strain page.
        coa_urls = []
        for link in soup.find_all('a'):
            href = link.get('href')
            if href and href.endswith('.pdf'):
                coa_urls.append(href)
        strain['coa_urls'] = coa_urls

        self.logger.debug(
            f"Strain '{strain.get('strain_name')}': "
            f"{len(coa_urls)} COAs, lineage={strain['lineage']}"
        )
        return strain

    def _download_coas(self, lab_results: List[Dict]) -> int:
        """Download COA PDFs for all lab results.

        Uses the base class cache to avoid re-downloading.

        Args:
            lab_results: List of result dictionaries with 'coa_url'.

        Returns:
            Number of PDFs downloaded.
        """
        downloaded = 0
        license_number = GLASS_HOUSE_FARMS['producer_license_number']

        for item in lab_results:
            url = item.get('coa_url')
            if not url:
                continue

            # Check cache.
            url_hash = self.cache.hash_url(url)
            if self.cache.get(url_hash):
                self.logger.debug(f'Skipped (cached): {url}')
                self._stats['cached'] += 1
                continue

            # Download PDF.
            filename = f"{item.get('lab_result_id', url_hash)}.pdf"
            filepath = self.pdf_dir / license_number / filename

            # Ensure subdirectory exists.
            filepath.parent.mkdir(parents=True, exist_ok=True)

            success = self.download_file(url, str(filepath))
            if success:
                self.cache.set(url_hash, {
                    'type': 'download',
                    'url': url,
                    'file': str(filepath),
                    'strain_name': item.get('strain_name'),
                    'downloaded_at': datetime.now().isoformat(),
                })
                downloaded += 1

            self.rate_limit(multiplier=2.0)

        self.logger.info(f'Downloaded {downloaded} COA PDFs')
        return downloaded

    def _convert_to_lab_results(self, raw_results: List[Dict]) -> List[Dict]:
        """Convert raw strain/COA data to standardized LabResult schema.

        Args:
            raw_results: List of raw result dictionaries.

        Returns:
            List of dictionaries matching LabResult schema.
        """
        results = []

        for item in raw_results:
            # Build standardized result.
            result = LabResult(
                # Identifiers
                id=item.get('lab_result_id'),
                sample_id=item.get('lab_result_id'),

                # Product info
                product_name=item.get('strain_name'),
                product_type='flower',
                strain_name=item.get('strain_name'),

                # Classification
                classification=item.get('classification'),
                indica_percentage=item.get('indica_percentage'),
                sativa_percentage=item.get('sativa_percentage'),

                # Lineage
                lineage=item.get('lineage'),

                # Producer info
                producer=GLASS_HOUSE_FARMS['business_dba_name'],
                producer_license_number=GLASS_HOUSE_FARMS['producer_license_number'],
                producer_latitude=GLASS_HOUSE_FARMS['producer_latitude'],
                producer_longitude=GLASS_HOUSE_FARMS['producer_longitude'],
                producer_address=GLASS_HOUSE_FARMS['producer_street_address'],
                producer_city=GLASS_HOUSE_FARMS['producer_city'],
                producer_county=GLASS_HOUSE_FARMS['producer_county'],
                producer_state=GLASS_HOUSE_FARMS['producer_state'],

                # COA info
                coa_url=item.get('coa_url'),
                lab_results_url=item.get('coa_url'),

                # Images
                images=[{'url': item.get('image_url')}] if item.get('image_url') else [],

                # Metadata
                state='ca',
                source='glass_house',
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
        """Collect Glass House Farms lab results.

        Pipeline phases:
            1. Scrape strains from main strains page
            2. Visit each strain page for lineage + COA links
            3. Expand strains × COAs into individual lab results
            4. Download COA PDFs
            5. Convert to standardized LabResult schema

        Args:
            download_pdfs: Whether to download COA PDFs.
            save_intermediate: Save intermediate data files.

        Returns:
            DataFrame with standardized lab results.
        """
        self.logger.info('Starting Glass House Farms collection...')

        # Phase 1: Get strain listings.
        strains = self._get_strains()
        strains_df = pd.DataFrame(strains)

        if save_intermediate:
            self.save_results(
                strains_df,
                prefix='ca-glass-house-strains',
                save_latest=False,
            )

        if strains_df.empty:
            self.logger.warning('No strains found')
            return pd.DataFrame()

        # Phase 2: Visit each strain page for details.
        self.logger.info(f'Collecting details for {len(strains)} strains...')
        detailed_strains = []
        for idx, strain in enumerate(strains, 1):
            self.logger.info(
                f'[{idx}/{len(strains)}] Fetching details: '
                f'{strain.get("strain_name", "Unknown")}'
            )
            detailed = self._get_strain_details(strain)
            detailed_strains.append(detailed)

        # Phase 3: Expand strains × COAs into individual results.
        raw_results = []
        for strain in detailed_strains:
            coa_urls = strain.get('coa_urls', [])
            if not coa_urls:
                # Still record the strain even without COAs.
                result = {**strain}
                result['coa_url'] = None
                result['lab_result_id'] = self._generate_result_id(
                    strain.get('strain_name'), ''
                )
                raw_results.append(result)
                continue

            for coa_url in coa_urls:
                lab_result_id = coa_url.split('/')[-1].split('.')[0]
                result = {**strain}
                result['coa_url'] = coa_url
                result['lab_result_id'] = lab_result_id
                raw_results.append(result)

        self.logger.info(
            f'Expanded {len(detailed_strains)} strains into '
            f'{len(raw_results)} lab results'
        )

        if save_intermediate:
            raw_df = pd.DataFrame(raw_results)
            self.save_results(
                raw_df,
                prefix='ca-glass-house-coas-raw',
                save_latest=False,
            )

        # Phase 4: Download COA PDFs.
        if download_pdfs:
            self._download_coas(raw_results)

        # Phase 5: Convert to standardized schema.
        standardized = self._convert_to_lab_results(raw_results)
        results_df = pd.DataFrame(standardized)

        # Save final results.
        self.save_results(
            results_df,
            prefix='ca-glass-house-results',
            save_latest=True,
        )

        self.logger.info(f'✓ Collected {len(results_df)} results from Glass House Farms (CA)')
        return results_df


# =============================================================================
# Testing
# =============================================================================

def run_unit_tests():
    """Run unit tests for helper methods.

    These tests can run without network access.
    """
    print('\n' + '=' * 60)
    print('UNIT TESTS: GlassHouseFarmsCollector')
    print('=' * 60)

    passed = 0
    failed = 0

    # --- Test: _generate_result_id ---
    print('\n--- Test: _generate_result_id ---')

    id1 = GlassHouseFarmsCollector._generate_result_id('Blue Dream', 'https://example.com/a.pdf')
    id2 = GlassHouseFarmsCollector._generate_result_id('Blue Dream', 'https://example.com/a.pdf')
    id3 = GlassHouseFarmsCollector._generate_result_id('OG Kush', 'https://example.com/a.pdf')
    id4 = GlassHouseFarmsCollector._generate_result_id('Blue Dream', 'https://example.com/b.pdf')

    tests = [
        ('ID length is 16', len(id1) == 16),
        ('Same inputs produce same ID', id1 == id2),
        ('Different strain produces different ID', id1 != id3),
        ('Different COA URL produces different ID', id1 != id4),
    ]

    for desc, result in tests:
        status = '✓' if result else '✗'
        print(f"  {status} {desc}")
        if result:
            passed += 1
        else:
            failed += 1

    # --- Test: _extract_strain_type ---
    print('\n--- Test: _extract_strain_type ---')

    # Create mock BeautifulSoup elements.
    from bs4 import BeautifulSoup as BS

    test_cases = [
        ('<div class="wave sativa"></div>', 'Sativa', 1.0, 0.0),
        ('<div class="wave indica"></div>', 'Indica', 0.0, 1.0),
        ('<div class="wave hybrid"></div>', 'Hybrid', 0.5, 0.5),
        ('<div class="wave indicaDominant"></div>', 'I-Hybrid', 0.25, 0.75),
        ('<div class="wave sativaDominant"></div>', 'S-Hybrid', 0.75, 0.25),
        ('<div class="wave cbd"></div>', 'CBD', 0.0, 0.0),
        ('<div class="wave unknown"></div>', None, None, None),
    ]

    for html, expected_class, expected_sativa, expected_indica in test_cases:
        soup = BS(html, 'html.parser')
        wave = soup.find('div')
        result = GlassHouseFarmsCollector._extract_strain_type(wave)

        match = (
            result['classification'] == expected_class
            and result['sativa_percentage'] == expected_sativa
            and result['indica_percentage'] == expected_indica
        )
        status = '✓' if match else '✗'
        if match:
            passed += 1
        else:
            failed += 1
        print(f"  {status} '{html}' → classification={result['classification']}, "
              f"sativa={result['sativa_percentage']}, indica={result['indica_percentage']}")

    # --- Test: _extract_strain_type with None ---
    print('\n--- Test: _extract_strain_type with None input ---')
    result = GlassHouseFarmsCollector._extract_strain_type(None)
    match = result['classification'] is None
    status = '✓' if match else '✗'
    if match:
        passed += 1
    else:
        failed += 1
    print(f"  {status} None input returns None classification")

    # --- Summary ---
    print('\n' + '-' * 60)
    print(f'Results: {passed} passed, {failed} failed')
    print('=' * 60)

    return failed == 0


def run_integration_test():
    """Run integration test with actual network requests.

    Requires network access to glasshousefarms.org.
    """
    print('\n' + '=' * 60)
    print('INTEGRATION TEST: GlassHouseFarmsCollector')
    print('=' * 60)

    try:
        with GlassHouseFarmsCollector(verbose=True) as collector:
            # Test strain listing fetch.
            strains = collector._get_strains()
            print(f'✓ Found {len(strains)} strains')

            if not strains:
                print('✗ No strains found — site may have changed')
                return False

            # Validate first strain has expected fields.
            first = strains[0]
            required_fields = ['strain_name', 'strain_url', 'strain_id']
            missing = [f for f in required_fields if not first.get(f)]
            if missing:
                print(f'✗ First strain missing fields: {missing}')
                return False
            print(f'✓ First strain: {first["strain_name"]}')

            # Test strain detail fetch (just the first one).
            detailed = collector._get_strain_details(first.copy())
            print(f'✓ Lineage: {detailed.get("lineage", [])}')
            print(f'✓ COA URLs: {len(detailed.get("coa_urls", []))}')

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
        description='Collect Glass House Farms lab results'
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
        # Run full collection.
        collector = GlassHouseFarmsCollector(
            data_dir=args.data_dir,
            pdf_dir=args.pdf_dir,
        )

        with collector:
            results = collector.get_results(
                download_pdfs=args.download_pdfs,
            )
            print(f'\nCollected {len(results)} results')