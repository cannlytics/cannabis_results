"""
Get Results | California | Flower Company
Copyright (c) 2023-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 12/8/2023
Updated: 2/2/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Collect cannabis lab result data published by the Flower Company.
    
    This collector scrapes product and COA data from the Flower Company
    website, downloads COA PDFs, and outputs standardized lab results
    following the Cannlytics schema.

Data Source:
    - Flower Company: https://flowercompany.com/

Output:
    - Standardized CSV with LabResult schema fields
    - Downloaded COA PDFs in configured directory
    - Cached URLs to avoid re-downloading

Usage:
    ```python
    from algorithms.get_results_ca_flower_co import FlowerCompanyCollector
    
    with FlowerCompanyCollector() as collector:
        results = collector.get_results(headless=True)
    ```

Command Line:
    ```bash
    python algorithms/get_results_ca_flower_co.py
    ```
"""
# Standard imports:
from datetime import datetime
import os
import time
from typing import List, Dict, Optional, Any

# External imports:
import pandas as pd
import requests
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.common.exceptions import (
    NoSuchElementException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
    ElementNotInteractableException,
    TimeoutException,
)

# Internal imports:
try:
    from config.results_config import PATHS, SOURCE_CONFIG, PRODUCT_TYPES
    from config.results_schema import LabResult, normalize_product_type, normalize_status
    from config.driver_utils import initialize_driver
    from results_base import COACollector
except ImportError:
    # Fallback for standalone execution
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config.results_config import PATHS, SOURCE_CONFIG, PRODUCT_TYPES
    from config.results_schema import LabResult, normalize_product_type, normalize_status
    from config.driver_utils import initialize_driver
    from results_base import COACollector


# === Constants ===

BASE_URL = 'https://flowercompany.com/'

CATEGORY_PAGES = [
    'category/fire-flower',
    'category/cartridges',
    'category/concentrates',
    'category/edibles',
    'category/prerolls',
    'category/top-shelf-nugs',
    'category/just-weed',
    'category/wellness',
    'category/the-freshest',
    'category/staff-picks',
    'category/latest-drops',
]

# Classification to indica/sativa percentages
INDICA_PERCENTAGES = {
    'Indica': 1.0,
    'I-Hybrid': 0.75,
    'Hybrid': 0.5,
    'S-Hybrid': 0.25,
    'Sativa': 0.0,
}

# Category to product type mapping
CATEGORY_TO_PRODUCT_TYPE = {
    'fire-flower': 'flower',
    'top-shelf-nugs': 'flower',
    'just-weed': 'flower',
    'cartridges': 'vape',
    'concentrates': 'concentrate',
    'edibles': 'edible',
    'prerolls': 'preroll',
    'wellness': 'tincture',
    'the-freshest': None,  # Mixed
    'staff-picks': None,   # Mixed
    'latest-drops': None,  # Mixed
}


class FlowerCompanyCollector(COACollector):
    """Collector for Flower Company cannabis lab results.
    
    Collects product listings, augments with detailed product data,
    downloads COA PDFs, and outputs standardized lab results.
    
    Attributes:
        state: Always 'ca' for California.
        source: Always 'flower_company'.
        
    Example:
        ```python
        with FlowerCompanyCollector() as collector:
            df = collector.get_results(headless=True)
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
        """Initialize the Flower Company collector.
        
        Args:
            data_dir: Override for data directory.
            pdf_dir: Override for PDF storage directory.
            cache_path: Override for cache file path.
            log_dir: Override for log directory.
            log_name: Override for log file name.
            pause_time: Seconds between requests (default: 4.0).
            verbose: Enable verbose logging.
        """
        # Get source-specific pause time from config
        source_config = SOURCE_CONFIG.get('flower_company', {})
        default_pause = source_config.get('pause_time', 4.0)
        
        super().__init__(
            state='ca',
            source='flower_company',
            data_dir=data_dir,
            pdf_dir=pdf_dir,
            cache_path=cache_path,
            log_dir=log_dir,
            log_name=log_name or 'get_results_ca_flower_co',
            pause_time=pause_time or default_pause,
            verbose=verbose,
        )
    
    # === Selenium Helpers ===
    
    def _init_selenium(self, headless: bool = True, **kwargs) -> None:
        """Initialize Selenium using the driver_utils module.
        
        Uses webdriver-manager for automatic ChromeDriver version matching.
        
        Args:
            headless: Run browser in headless mode.
            **kwargs: Additional options (unused).
        """
        self.driver = initialize_driver(headless=headless, verbose=self.verbose)
        self.logger.info(f'Selenium driver initialized (headless={headless})')
    
    def _click_age_gate(self) -> None:
        """Click the 'Yes' age verification button if present.
        
        Handles common Selenium interactability issues by:
        1. Waiting for the element to be present
        2. Scrolling it into view
        3. Waiting for it to be clickable
        4. Using JavaScript click as a fallback
        """
        try:
            # Wait for age gate button to be present
            try:
                yes_button = WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.CLASS_NAME, 'age-gate-yes-button'))
                )
            except TimeoutException:
                # No age gate present
                return
            
            # Scroll the button into view
            self.driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'instant', block: 'center'});",
                yes_button
            )
            time.sleep(0.5)  # Allow scroll to complete
            
            # Try regular click first
            try:
                yes_button = WebDriverWait(self.driver, 3).until(
                    EC.element_to_be_clickable((By.CLASS_NAME, 'age-gate-yes-button'))
                )
                yes_button.click()
            except (ElementClickInterceptedException, ElementNotInteractableException, TimeoutException):
                # Fallback: Use JavaScript click (bypasses interactability checks)
                self.driver.execute_script("arguments[0].click();", yes_button)
            
            time.sleep(2)  # Wait for page to respond
            self.logger.debug('Age gate clicked')
            
        except NoSuchElementException:
            pass  # No age gate present
        except StaleElementReferenceException:
            # Page changed, try once more
            time.sleep(1)
            self._click_age_gate()
    
    def _click_show_more(self, max_clicks: int = 200) -> int:
        """Click 'Show More' button until all products are loaded.
        
        This method handles common Selenium interactability issues by:
        1. Scrolling the button into view
        2. Waiting for it to be clickable
        3. Using JavaScript click as a fallback
        
        The method detects when all products have been loaded by checking:
        - Whether the button has been removed from the DOM
        - Whether the button is disabled (attribute or CSS class)
        - Whether the button is hidden (display:none, visibility:hidden)
        - Whether the product count has stopped increasing after clicks
        
        Args:
            max_clicks: Maximum number of clicks to prevent infinite loops.
            
        Returns:
            Number of times the button was clicked.
        """        
        clicks = 0
        consecutive_failures = 0
        max_consecutive_failures = 3
        no_new_products_count = 0
        max_no_new_products = 3  # Stop after 3 clicks yield no new products
        
        # Get initial product count
        previous_product_count = len(
            self.driver.find_elements(By.CLASS_NAME, 'product-card-wrapper')
        )
        
        while clicks < max_clicks and consecutive_failures < max_consecutive_failures:
            try:
                # Wait for button to be present (with timeout)
                try:
                    more_button = WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((By.CLASS_NAME, 'show-more-button'))
                    )
                except TimeoutException:
                    # No button found - we've loaded all products
                    self.logger.debug('Show More button not found in DOM')
                    break
                
                # Check if button is disabled (attribute or class)
                is_disabled = more_button.get_attribute('disabled')
                button_classes = more_button.get_attribute('class') or ''
                if is_disabled or 'disabled' in button_classes:
                    self.logger.debug('Show More button is disabled — all products loaded')
                    break
                
                # Check if button is hidden
                if not more_button.is_displayed():
                    self.logger.debug('Show More button is hidden — all products loaded')
                    break
                
                # Scroll the button into view (center of viewport)
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                    more_button
                )
                time.sleep(0.5)  # Allow scroll animation to complete
                
                # Try regular click first
                try:
                    # Wait for element to be clickable
                    more_button = WebDriverWait(self.driver, 3).until(
                        EC.element_to_be_clickable((By.CLASS_NAME, 'show-more-button'))
                    )
                    more_button.click()
                except (ElementClickInterceptedException, ElementNotInteractableException, TimeoutException):
                    # Fallback: Use JavaScript click (bypasses interactability checks)
                    self.driver.execute_script("arguments[0].click();", more_button)
                
                clicks += 1
                consecutive_failures = 0  # Reset on success
                self.logger.debug(f'Show More clicked ({clicks} times)')
                time.sleep(3)  # Wait for new products to load
                
                # Check if new products were actually loaded
                current_product_count = len(
                    self.driver.find_elements(By.CLASS_NAME, 'product-card-wrapper')
                )
                if current_product_count > previous_product_count:
                    previous_product_count = current_product_count
                    no_new_products_count = 0  # Reset counter
                else:
                    no_new_products_count += 1
                    self.logger.debug(
                        f'No new products after click {clicks} '
                        f'({no_new_products_count}/{max_no_new_products})'
                    )
                    if no_new_products_count >= max_no_new_products:
                        self.logger.debug(
                            'No new products after multiple clicks — all products loaded'
                        )
                        break
                
            except NoSuchElementException:
                # No more "Show More" button - we've loaded all products
                break
            except StaleElementReferenceException:
                # Page changed while we were working, retry
                consecutive_failures += 1
                time.sleep(1)
            except Exception as e:
                self.logger.debug(f'Show More click attempt failed: {e}')
                consecutive_failures += 1
                time.sleep(1)
        
        if clicks > 0:
            self.logger.info(f'Clicked Show More {clicks} times (loaded {previous_product_count} products)')
        
        return clicks
    
    # === Data Extraction Helpers ===
    
    @staticmethod
    def _extract_weight(amount_str: str) -> Optional[float]:
        """Extract weight in grams from amount string.
        
        Args:
            amount_str: String like "1/8 (3.5g)" or "7g".
            
        Returns:
            Weight in grams or None if not parsable.
        """
        if not amount_str:
            return None
        
        # Try to find pattern like "(3.5g)"
        if '(' in amount_str:
            parts = amount_str.split('(')
            if len(parts) > 1:
                weight_part = parts[1].split('g')[0].strip()
                try:
                    return float(weight_part)
                except ValueError:
                    pass
        
        # Try to find pattern like "7g"
        import re
        match = re.search(r'(\d+\.?\d*)\s*g', amount_str.lower())
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
        
        return None
    
    @staticmethod
    def _price_to_float(price_str: str) -> Optional[float]:
        """Convert price string to float.
        
        Args:
            price_str: String like "$50.00" or "$50".
            
        Returns:
            Float price or None.
        """
        if not price_str:
            return None
        try:
            return float(price_str.replace('$', '').replace(',', '').strip())
        except (ValueError, AttributeError):
            return None
    
    @staticmethod
    def _parse_thc_value(thc_str: str) -> tuple:
        """Parse THC string into value and units.
        
        Args:
            thc_str: String like "28.5% THC" or "100mg THC".
            
        Returns:
            Tuple of (value: float, units: str) or (None, None).
        """
        if not thc_str:
            return None, None
        
        thc_lower = thc_str.lower().strip()
        
        # Determine units
        if '%' in thc_lower:
            units = 'percent'
        elif 'mg' in thc_lower:
            units = 'mg'
        else:
            units = 'percent'  # Default assumption
        
        # Extract numeric value
        import re
        match = re.search(r'(\d+\.?\d*)', thc_lower)
        if match:
            try:
                value = float(match.group(1))
                return value, units
            except ValueError:
                pass
        
        return None, None
    
    @staticmethod
    def _generate_product_id(product_name: str, producer: str, total_thc: Any) -> str:
        """Generate a unique product ID.
        
        Args:
            product_name: Name of the product.
            producer: Producer/brand name.
            total_thc: THC value for uniqueness.
            
        Returns:
            16-character hex ID.
        """
        import hashlib
        key_data = f"{product_name or ''}{producer or ''}{total_thc or ''}"
        return hashlib.sha256(key_data.encode()).hexdigest()[:16]
    
    # === Data Collection Methods ===
    
    def _get_product_pages(self) -> List[str]:
        """Get list of category and brand pages to scrape.
        
        Returns:
            List of page paths (without base URL).
        """
        self.driver.get(BASE_URL + 'menu')
        self._click_age_gate()
        self.rate_limit()
        
        # Get brand pages from menu
        brand_pages = []
        try:
            div = self.driver.find_element(By.CLASS_NAME, 'special-content-brand-row')
            links = div.find_elements(By.TAG_NAME, 'a')
            brand_pages = [
                link.get_attribute('href').replace(BASE_URL, '')
                for link in links
                if link.get_attribute('href')
            ]
        except NoSuchElementException:
            self.logger.warning('Brand row not found, using categories only')
        
        all_pages = CATEGORY_PAGES + brand_pages
        self.logger.info(f'Found {len(all_pages)} pages to scrape')
        return all_pages
    
    def _collect_products(self, pages: List[str]) -> List[Dict]:
        """Collect product listings from category/brand pages.
        
        Args:
            pages: List of page paths to scrape.
            
        Returns:
            List of product dictionaries with basic info.
        """
        products = []
        recorded_urls = set()  # Deduplicate within this run only (not across runs)
        
        for page in pages:
            self.driver.get(BASE_URL + page)
            self._click_age_gate()
            self._click_show_more()
            self.rate_limit()
            
            cards = self.driver.find_elements(By.CLASS_NAME, 'product-card-wrapper')
            self.logger.info(f'Found {len(cards)} products for page: {page}')
            
            for card in cards:
                product = self._extract_product_card(card, page)
                if product and product['product_url'] not in recorded_urls:
                    recorded_urls.add(product['product_url'])
                    products.append(product)
        
        # Cache recorded URLs
        self.cache.set('product_urls', list(recorded_urls))
        self.logger.info(f'Collected {len(products)} unique products')
        return products
    
    def _extract_product_card(self, card, page: str) -> Optional[Dict]:
        """Extract product data from a product card element.
        
        Args:
            card: Selenium WebElement for the product card.
            page: Current page path (for category).
            
        Returns:
            Product dictionary or None if extraction fails.
        """
        try:
            # Producer/brand
            try:
                producer = card.find_element(By.CSS_SELECTOR, '.favorite-company a').text.strip()
            except NoSuchElementException:
                producer = None
            
            # Product name and URL
            try:
                name_element = card.find_element(By.CSS_SELECTOR, '.favorite-product-name a')
                product_name = name_element.text.strip()
                product_url = name_element.get_attribute('href')
            except NoSuchElementException:
                return None  # Skip products without name/URL
            
            if not product_url:
                return None
            
            # THC content
            try:
                thc_text = card.find_element(By.CSS_SELECTOR, '.product-card-thc').text.strip()
                total_thc, thc_units = self._parse_thc_value(thc_text)
            except NoSuchElementException:
                total_thc, thc_units = None, None
            
            # Prices
            try:
                discount_price_str = card.find_element(
                    By.CSS_SELECTOR, '.price.product-card-price-actual'
                ).text.strip()
                discount_price = self._price_to_float(discount_price_str)
            except NoSuchElementException:
                discount_price = None
            
            try:
                price_str = card.find_element(
                    By.CSS_SELECTOR, '.price.retail.product-card-price-retail'
                ).text.strip()
                price = self._price_to_float(price_str)
            except NoSuchElementException:
                price = discount_price
            
            # Amount/weight
            amount_txt = None
            try:
                amount_txt = card.find_element(By.CSS_SELECTOR, '.solo-variant-toggle').text.strip()
            except NoSuchElementException:
                try:
                    select_element = card.find_element(
                        By.CSS_SELECTOR, 'select.new-product-card-variant-select'
                    )
                    select_obj = Select(select_element)
                    if select_obj.options:
                        amount_txt = select_obj.options[0].text.strip()
                except NoSuchElementException:
                    pass
            
            amount = self._extract_weight(amount_txt)
            
            # Classification (Indica/Sativa/Hybrid)
            classification = None
            try:
                class_element = card.find_element(By.CSS_SELECTOR, '.product-card-type-icon')
                classification = class_element.text.strip()
            except NoSuchElementException:
                pass
            
            indica_pct = INDICA_PERCENTAGES.get(classification, 0.5) if classification else None
            sativa_pct = (1 - indica_pct) if indica_pct is not None else None
            
            # Determine category from page
            category = page.split('/')[-1]
            
            return {
                'product_name': product_name,
                'producer': producer,
                'category': category,
                'product_url': product_url,
                'total_thc': total_thc,
                'total_thc_units': thc_units,
                'price': price,
                'discount_price': discount_price,
                'discount': (price - discount_price) if price and discount_price else None,
                'amount': amount,
                'classification': classification,
                'indica_percentage': indica_pct,
                'sativa_percentage': sativa_pct,
            }
            
        except Exception as e:
            self.logger.warning(f'Error extracting product card: {e}')
            return None
    
    def _augment_products(self, products: pd.DataFrame) -> List[Dict]:
        """Visit each product page to collect detailed data.
        
        Args:
            products: DataFrame of basic product info.
            
        Returns:
            List of augmented product dictionaries.
        """
        augmented = []
        total = len(products)
        
        for idx, (_, product) in enumerate(products.iterrows(), 1):
            product_url = product['product_url']
            self.logger.info(f'[{idx}/{total}] Collecting details: {product_url}')
            
            try:
                details = self._collect_product_details(product.to_dict())
                augmented.append(details)
            except Exception as e:
                self.logger.error(f'Error collecting details for {product_url}: {e}')
                augmented.append(product.to_dict())
            
            self.rate_limit()
        
        return augmented
    
    def _collect_product_details(self, product: Dict) -> Dict:
        """Collect detailed data from a single product page.
        
        Args:
            product: Basic product dictionary.
            
        Returns:
            Augmented product dictionary with all details.
        """
        self.driver.get(product['product_url'])
        time.sleep(2)
        self._click_age_gate()
        
        # Product type and subtype
        try:
            types = self.driver.find_elements(By.CSS_SELECTOR, '.detail-product-type')
            product_type = types[0].text.strip() if types else None
            product_subtype = types[1].text.strip() if len(types) >= 2 else None
        except (NoSuchElementException, IndexError):
            product_type = None
            product_subtype = None
        
        # Description
        try:
            product_description = self.driver.find_element(
                By.CSS_SELECTOR, '.product-view-description'
            ).text.strip()
        except NoSuchElementException:
            product_description = None
        
        # Info rows (contents, effects, aromas, lineage, lab results)
        contents, effects, aromas, lineage, lab_results_url = '', '', '', '', ''
        info_rows = self.driver.find_elements(By.CSS_SELECTOR, '.row.product-view-row')
        for row in info_rows:
            parts = row.text.split('\n')
            field = parts[0].lower() if parts else ''
            
            if 'contents' in field:
                contents = parts[-1] if len(parts) > 1 else ''
            elif 'effects' in field:
                effects = parts[-1] if len(parts) > 1 else ''
            elif 'aromas' in field:
                aromas = parts[-1] if len(parts) > 1 else ''
            elif 'lineage' in field:
                lineage = parts[-1] if len(parts) > 1 else ''
            elif 'tested' in field:
                try:
                    link = row.find_element(By.TAG_NAME, 'a')
                    lab_results_url = link.get_attribute('href')
                except NoSuchElementException:
                    pass
        
        # Distributor info
        distributor = None
        distributor_license = None
        try:
            els = self.driver.find_elements(By.CSS_SELECTOR, '.row.d-block .detail-sub-text')
            if len(els) >= 2:
                distributor = els[-2].text.strip()
                distributor_license = els[-1].text.strip()
        except (NoSuchElementException, IndexError):
            pass
        
        # Image URL
        try:
            image_url = self.driver.find_element(
                By.CSS_SELECTOR, '.product-image-lg'
            ).get_attribute('src')
        except NoSuchElementException:
            image_url = None
        
        # Re-extract THC/CBD if missing
        if not product.get('total_thc'):
            try:
                thc_text = self.driver.find_element(By.CSS_SELECTOR, '.product-card-thc').text
                product['total_thc'], product['total_thc_units'] = self._parse_thc_value(thc_text)
            except NoSuchElementException:
                pass
        
        # CBD
        total_cbd = None
        try:
            cbd_text = self.driver.find_element(By.CSS_SELECTOR, '.product-card-cbd').text
            total_cbd, _ = self._parse_thc_value(cbd_text)
        except NoSuchElementException:
            pass
        
        # Re-extract classification if missing
        if not product.get('classification'):
            try:
                class_el = self.driver.find_element(By.CSS_SELECTOR, '.product-detail-type-container')
                classification = class_el.text.split('\n')[0]
                product['classification'] = classification
                product['indica_percentage'] = INDICA_PERCENTAGES.get(classification, 0.5)
                product['sativa_percentage'] = 1 - product['indica_percentage']
            except NoSuchElementException:
                pass
        
        # Generate product ID
        product_id = self._generate_product_id(
            product.get('product_name'),
            product.get('producer'),
            product.get('total_thc')
        )
        
        # Merge all data
        product.update({
            'product_id': product_id,
            'product_type': product_type,
            'product_subtype': product_subtype,
            'product_description': product_description,
            'product_contents': contents,
            'predicted_effects': effects,
            'predicted_aromas': aromas.split(', ') if aromas else [],
            'lineage': lineage,
            'lab_results_url': lab_results_url,
            'image_url': image_url,
            'distributor': distributor,
            'distributor_license_number': distributor_license,
            'total_cbd': total_cbd,
        })
        
        return product
    
    def _download_coas(
            self,
            items: List[Dict],
            url_key: str = 'lab_results_url',
            id_key: str = 'product_id',
        ) -> int:
        """Download COA PDFs for all items.
        
        Args:
            items: List of product dictionaries.
            url_key: Key containing COA URL.
            id_key: Key for generating filename.
            
        Returns:
            Number of PDFs downloaded.
        """
        downloaded = 0
        
        for item in items:
            url = item.get(url_key)
            if not url:
                continue
            
            # Check cache
            url_hash = self.cache.hash_url(url)
            if self.cache.get(url_hash):
                self.logger.info(f'Skipped (cached): {url}')
                self._stats['cached'] += 1
                continue
            
            # Download PDF
            filename = f"{item.get(id_key, url_hash)}.pdf"
            filepath = self.pdf_dir / filename
            
            success = self.download_file(url, str(filepath))
            if success:
                self.cache.set(url_hash, {
                    'type': 'download',
                    'url': url,
                    'file': str(filepath),
                    'downloaded_at': datetime.now().isoformat(),
                })
                downloaded += 1
            
            self.rate_limit(multiplier=2.5)  # Extra delay for downloads
        
        self.logger.info(f'Downloaded {downloaded} COA PDFs')
        return downloaded
    
    def _convert_to_lab_results(self, items: List[Dict]) -> List[Dict]:
        """Convert raw product data to standardized LabResult schema.
        
        Args:
            items: List of augmented product dictionaries.
            
        Returns:
            List of dictionaries matching LabResult schema.
        """
        results = []
        
        for item in items:
            # Normalize product type
            raw_type = item.get('product_type') or item.get('category')
            normalized_type = normalize_product_type(raw_type)
            if not normalized_type:
                # Try mapping from category
                category = item.get('category', '')
                normalized_type = CATEGORY_TO_PRODUCT_TYPE.get(category)
            
            # Build standardized result
            result = LabResult(
                # Identifiers
                id=item.get('product_id'),
                sample_id=item.get('product_id'),
                
                # Product info
                product_name=item.get('product_name'),
                product_type=normalized_type,
                product_subtype=item.get('product_subtype'),
                strain_name=item.get('lineage'),
                batch_size=item.get('amount'),
                
                # Producer info
                producer=item.get('producer'),
                
                # Distributor info
                distributor=item.get('distributor'),
                distributor_license_number=item.get('distributor_license_number'),
                
                # Cannabinoids
                total_thc=item.get('total_thc'),
                total_cbd=item.get('total_cbd'),
                
                # Classification
                classification=item.get('classification'),
                indica_percentage=item.get('indica_percentage'),
                sativa_percentage=item.get('sativa_percentage'),
                
                # COA info
                lab_results_url=item.get('lab_results_url'),
                coa_url=item.get('lab_results_url'),
                
                # Images
                images=[{'url': item.get('image_url')}] if item.get('image_url') else [],
                
                # Metadata
                state='ca',
                source='flower_company',
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            
            results.append(result.to_dict())
        
        return results
    
    # === Main Collection Method ===
    
    def get_results(
            self,
            headless: bool = True,
            download_pdfs: bool = True,
            save_intermediate: bool = True,
        ) -> pd.DataFrame:
        """Collect Flower Company lab results.
        
        Args:
            headless: Run browser in headless mode.
            download_pdfs: Whether to download COA PDFs.
            save_intermediate: Save intermediate data files.
            
        Returns:
            DataFrame with standardized lab results.
        """
        self.logger.info('Starting Flower Company collection...')
        
        # Initialize Selenium
        self._init_selenium(headless=headless)
        
        try:
            # Phase 1: Get pages to scrape
            pages = self._get_product_pages()
            
            # Phase 2: Collect basic product listings
            products = self._collect_products(pages)
            products_df = pd.DataFrame(products)
            
            if save_intermediate:
                self.save_results(
                    products_df,
                    prefix='ca-flower-company-products-initial',
                    save_latest=False,
                )
            
            if products_df.empty:
                self.logger.warning('No products found')
                return pd.DataFrame()
            
            # Phase 3: Augment with detailed data
            augmented = self._augment_products(products_df)
            augmented_df = pd.DataFrame(augmented)
            
            if save_intermediate:
                self.save_results(
                    augmented_df,
                    prefix='ca-flower-company-products-augmented',
                    save_latest=False,
                )
            
            # Phase 4: Download COA PDFs
            if download_pdfs:
                self._download_coas(augmented)
            
            # Phase 5: Convert to standardized schema
            standardized = self._convert_to_lab_results(augmented)
            results_df = pd.DataFrame(standardized)
            
            # Save final results
            self.save_results(
                results_df,
                prefix='ca-flower-company-results',
                save_latest=True,
            )
            
            self.logger.info(f'✓ Collected {len(results_df)} results from Flower Company (CA)')
            return results_df
            
        finally:
            # Note: log_stats() is called by __exit__ when using context manager
            # Only quit driver here - it's idempotent (checks self.driver is not None)
            self._quit_driver()


# =============================================================================
# Testing
# =============================================================================

def run_unit_tests():
    """Run unit tests for helper methods.
    
    These tests can run without Selenium or network access.
    """
    print('\n' + '='*60)
    print('UNIT TESTS: FlowerCompanyCollector')
    print('='*60)
    
    passed = 0
    failed = 0
    
    # Test _extract_weight
    print('\n--- Test: _extract_weight ---')
    test_cases = [
        ('1/8 (3.5g)', 3.5),
        ('7g', 7.0),
        ('14g', 14.0),
        ('1oz (28g)', 28.0),
        ('', None),
        (None, None),
        ('invalid', None),
    ]
    for input_val, expected in test_cases:
        result = FlowerCompanyCollector._extract_weight(input_val)
        status = '✓' if result == expected else '✗'
        if result == expected:
            passed += 1
        else:
            failed += 1
        print(f"  {status} _extract_weight('{input_val}') = {result} (expected {expected})")
    
    # Test _price_to_float
    print('\n--- Test: _price_to_float ---')
    test_cases = [
        ('$50', 50.0),
        ('$50.00', 50.0),
        ('$1,250.00', 1250.0),
        ('', None),
        (None, None),
    ]
    for input_val, expected in test_cases:
        result = FlowerCompanyCollector._price_to_float(input_val)
        status = '✓' if result == expected else '✗'
        if result == expected:
            passed += 1
        else:
            failed += 1
        print(f"  {status} _price_to_float('{input_val}') = {result} (expected {expected})")
    
    # Test _parse_thc_value
    print('\n--- Test: _parse_thc_value ---')
    test_cases = [
        ('28.5% THC', (28.5, 'percent')),
        ('100mg THC', (100.0, 'mg')),
        ('24.5%', (24.5, 'percent')),
        ('', (None, None)),
        (None, (None, None)),
    ]
    for input_val, expected in test_cases:
        result = FlowerCompanyCollector._parse_thc_value(input_val)
        status = '✓' if result == expected else '✗'
        if result == expected:
            passed += 1
        else:
            failed += 1
        print(f"  {status} _parse_thc_value('{input_val}') = {result} (expected {expected})")
    
    # Test _generate_product_id
    print('\n--- Test: _generate_product_id ---')
    id1 = FlowerCompanyCollector._generate_product_id('Blue Dream', 'Test Farm', 24.5)
    id2 = FlowerCompanyCollector._generate_product_id('Blue Dream', 'Test Farm', 24.5)
    id3 = FlowerCompanyCollector._generate_product_id('OG Kush', 'Test Farm', 24.5)
    
    status1 = '✓' if len(id1) == 16 else '✗'
    status2 = '✓' if id1 == id2 else '✗'
    status3 = '✓' if id1 != id3 else '✗'
    
    print(f"  {status1} ID length is 16: {len(id1)}")
    print(f"  {status2} Same inputs produce same ID: {id1 == id2}")
    print(f"  {status3} Different inputs produce different IDs: {id1 != id3}")
    
    passed += 3 if status1 == '✓' else 0
    passed += 1 if status2 == '✓' else 0
    passed += 1 if status3 == '✓' else 0
    failed += 0 if status1 == '✓' else 1
    failed += 0 if status2 == '✓' else 1
    failed += 0 if status3 == '✓' else 1
    
    # Summary
    print('\n' + '-'*60)
    print(f'Results: {passed} passed, {failed} failed')
    print('='*60)
    
    return failed == 0


def run_integration_test(headless: bool = True):
    """Run integration test with actual collection.
    
    Requires network access and Selenium.
    
    Args:
        headless: Run in headless mode.
    """
    print('\n' + '='*60)
    print('INTEGRATION TEST: FlowerCompanyCollector')
    print('='*60)
    
    try:
        with FlowerCompanyCollector(verbose=True) as collector:
            # Test with limited scope
            collector.logger.info('Running integration test...')
            
            # Initialize driver
            collector._init_selenium(headless=headless)
            
            # Test page navigation
            collector.driver.get(BASE_URL)
            collector._click_age_gate()
            
            # Get just one page
            collector.driver.get(BASE_URL + 'category/fire-flower')
            collector._click_age_gate()
            time.sleep(2)
            
            cards = collector.driver.find_elements(By.CLASS_NAME, 'product-card-wrapper')
            print(f'✓ Found {len(cards)} products on test page')
            
            # Test product extraction
            if cards:
                product = collector._extract_product_card(cards[0], 'category/fire-flower')
                print(f'✓ Extracted product: {product.get("product_name") if product else "None"}')
            
            print('\n✓ Integration test passed')
            return True
            
    except Exception as e:
        print(f'\n✗ Integration test failed: {e}')
        return False


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Collect Flower Company lab results')
    parser.add_argument('--headless', action='store_true', default=True,
                        help='Run in headless mode (default: True)')
    parser.add_argument('--no-headless', dest='headless', action='store_false',
                        help='Run with visible browser')
    parser.add_argument('--test', action='store_true',
                        help='Run unit tests only')
    parser.add_argument('--integration-test', action='store_true',
                        help='Run integration test')
    parser.add_argument('--data-dir', type=str, default=None,
                        help='Override data directory')
    parser.add_argument('--pdf-dir', type=str, default=None,
                        help='Override PDF directory')
    
    args = parser.parse_args()
    
    if args.test:
        # Run unit tests
        success = run_unit_tests()
        exit(0 if success else 1)
    
    elif args.integration_test:
        # Run integration test
        success = run_integration_test(headless=args.headless)
        exit(0 if success else 1)
    
    else:
        # Run full collection
        collector = FlowerCompanyCollector(
            data_dir=args.data_dir,
            pdf_dir=args.pdf_dir,
        )
        
        with collector:
            results = collector.get_results(headless=args.headless)
            print(f'\nCollected {len(results)} results')