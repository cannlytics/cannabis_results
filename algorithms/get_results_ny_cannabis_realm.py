"""
Get Results | New York | Cannabis Realm
Copyright (c) 2025 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 4/1/2025
Updated: 4/1/2025
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:

    Collect cannabis lab result data published by Cannabis Realm NY.

Data Source:

    - [Cannabis Realm NY](https://cannabisrealmny.com/)

"""
# Standard imports:
import os
import time
from typing import List, Dict

# External imports:
import pandas as pd
import requests
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException

# Internal imports:
from cannlytics.data.collectors import COACollector
from cannlytics.data import create_sample_id


# Base URL and categories
BASE_URL = 'https://cannabisrealmny.com'
STRAIN_TYPE_MAP = {
    'HYBRID': {'indica_percentage': 0.5, 'sativa_percentage': 0.5},
    'INDICA': {'indica_percentage': 1.0, 'sativa_percentage': 0.0},
    'SATIVA': {'indica_percentage': 0.0, 'sativa_percentage': 1.0},
    'HYBRID-INDICA': {'indica_percentage': 0.75, 'sativa_percentage': 0.25},
    'HYBRID-SATIVA': {'indica_percentage': 0.25, 'sativa_percentage': 0.75},
}


class CannabisRealmNYCollector(COACollector):
    """Collector for Cannabis Realm cannabis lab results."""

    def _scroll_to_load_more(self, wait_time=1.0, max_scrolls=10):
        """Scroll down to load more products."""
        prev_height = self.driver.execute_script("return document.body.scrollHeight")
        scrolls = 0
        while scrolls < max_scrolls:
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(wait_time)
            new_height = self.driver.execute_script("return document.body.scrollHeight")
            if new_height == prev_height:
                break
            prev_height = new_height
            scrolls += 1
            try:
                load_more = self.driver.find_element(By.CSS_SELECTOR, ".show-more-button")
                load_more.click()
                time.sleep(wait_time)
            except NoSuchElementException:
                pass
    
    def _extract_weight(self, text: str) -> float:
        """Extract weight in grams from text."""
        if not text:
            return None
        if 'g' in text.lower():
            try:
                weight_str = text.lower().split('g')[0].strip()
                if '/' in weight_str:
                    num, denom = weight_str.split('/')
                    return float(num) / float(denom) * 28.35  # Convert to grams
                return float(weight_str)
            except (ValueError, IndexError):
                pass
        elif 'oz' in text.lower():
            try:
                weight_str = text.lower().split('oz')[0].strip()
                if '/' in weight_str:
                    num, denom = weight_str.split('/')
                    return float(num) / float(denom) * 28.35  # Convert to grams
                return float(weight_str) * 28.35  # Convert to grams
            except (ValueError, IndexError):
                pass
        
        return None
    
    def _price_to_float(self, price_str: str) -> float:
        """Convert a price string to float."""
        if not price_str:
            return None
        try:
            return float(price_str.replace('$', '').replace(',', '').strip())
        except ValueError:
            return None
    
    def _extract_percentage(self, text: str) -> float:
        """Extract percentage value from text."""
        if not text:
            return None
        try:
            return float(text.replace('%', '').strip())
        except ValueError:
            return None
    
    def _collect_products_from_category(self, category_url: str) -> List[Dict]:
        """Collect products from a category page."""
        full_url = f"{BASE_URL}{category_url}"
        self.logger.info(f"Collecting products from: {full_url}")
        self.driver.get(full_url)
        
        # Extract category name from URL.
        category = category_url.split('/')[-1]
        
        # Scroll to load all products.
        self._scroll_to_load_more()
        
        # Get all product cards.
        products = []
        product_cards = self.driver.find_elements(By.CSS_SELECTOR, ".card-inner")
        self.logger.info(f"Found {len(product_cards)} products in {category}")

        for card in product_cards:
            try:
                # Extract product information.
                # Product name
                try:
                    product_name_el = card.find_element(By.CSS_SELECTOR, "[data-testid^='product-name-']")
                    product_name = product_name_el.text.strip()
                except NoSuchElementException:
                    product_name = None
                
                # Producer/brand
                try:
                    producer_el = card.find_element(By.CSS_SELECTOR, "[data-testid^='product-card-brand-name-']")
                    producer = producer_el.text.strip()
                except NoSuchElementException:
                    producer = None
                
                # Strain type
                try:
                    strain_type_el = card.find_element(By.CSS_SELECTOR, "[data-testid^='product-card-cannabis-type-tag-']")
                    strain_type = strain_type_el.text.strip().upper()
                except NoSuchElementException:
                    strain_type = "HYBRID"  # Default to hybrid if not found
                
                # Extract THC percentage
                try:
                    cannabinoid_line = card.find_element(By.CSS_SELECTOR, "[data-testid='product-card-cannabinoid-line']")
                    thc_text = cannabinoid_line.find_element(By.XPATH, "./div[contains(text(), 'THC')]").text
                    total_thc = self._extract_percentage(thc_text.split('THC')[1].strip()) if 'THC' in thc_text else None
                except (NoSuchElementException, IndexError):
                    total_thc = None
                
                # Extract CBD percentage if available
                try:
                    cannabinoid_line = card.find_element(By.CSS_SELECTOR, "[data-testid='product-card-cannabinoid-line']")
                    cbd_text = cannabinoid_line.find_element(By.XPATH, "./div[contains(text(), 'CBD')]").text
                    total_cbd = self._extract_percentage(cbd_text.split('CBD')[1].strip()) if 'CBD' in cbd_text else None
                except (NoSuchElementException, IndexError):
                    total_cbd = None
                
                # Weight
                try:
                    weight_el = card.find_element(By.CSS_SELECTOR, "[data-testid^='variant-weight-']")
                    product_weight = self._extract_weight(weight_el.text.strip())
                except NoSuchElementException:
                    product_weight = None
                
                # Price
                try:
                    price_el = card.find_element(By.CSS_SELECTOR, "[data-testid^='variant-price-']")
                    total_price = self._price_to_float(price_el.text.strip())
                except NoSuchElementException:
                    total_price = None
                
                # Get product URL
                try:
                    link_el = card.find_element(By.CSS_SELECTOR, "a[data-testid='product-card-menu-link-body']")
                    product_url = link_el.get_attribute('href')
                except NoSuchElementException:
                    product_url = None
                
                # Get image URL
                try:
                    img_el = card.find_element(By.CSS_SELECTOR, "img")
                    image_url = img_el.get_attribute('src')
                except NoSuchElementException:
                    image_url = None
                
                # Get strain type percentages
                strain_info = STRAIN_TYPE_MAP.get(strain_type, STRAIN_TYPE_MAP['HYBRID'])
                
                # Create product ID
                product_id = create_sample_id(
                    private_key=str(total_thc or ''),
                    public_key=product_name or '',
                    salt=producer or '',
                )
                
                # Add product to list
                product_data = {
                    'product_id': product_id,
                    'product_name': product_name,
                    'producer': producer,
                    'category': category,
                    'strain_type': strain_type,
                    'indica_percentage': strain_info['indica_percentage'],
                    'sativa_percentage': strain_info['sativa_percentage'],
                    'total_thc': total_thc,
                    'total_cbd': total_cbd,
                    'product_weight': product_weight,
                    'total_price': total_price,
                    'product_url': product_url,
                    'product_image_url': image_url,
                }
                
                products.append(product_data)
                
            except Exception as e:
                print(e)
                self.logger.error(f"Error extracting product information: {str(e)}")
                continue
        
        return products
    
    def _get_product_details(self, products: List[Dict]) -> List[Dict]:
        """Get detailed information from each product page."""
        product_details = []
        
        for product in products:
            if not product.get('product_url'):
                product_details.append(product)
                continue
                
            try:
                self.logger.info(f"Getting details for: {product['product_name']}")
                self.driver.get(product['product_url'])
                time.sleep(self.pause_time)
                
                # Get COA URL.
                coa_url = None
                try:
                    links = self.driver.find_elements(By.TAG_NAME, "a")
                    for link in links:
                        href = link.get_attribute('href')
                        text = link.text.lower()
                        if (href and ('coa' in href.lower() or '.pdf' in href.lower())) or \
                           (text and ('coa' in text or 'lab' in text or 'test' in text)):
                            coa_url = href
                            break
                except Exception:
                    pass


                
                # Update product with the COA URL.
                product.update({'coa_url': coa_url})
                product_details.append(product)
                
            except Exception as e:
                self.logger.error(f"Error getting details for {product.get('product_name')}: {str(e)}")
                product_details.append(product)
                
            # Pause between requests
            time.sleep(self.pause_time)
        
        return product_details
        
    def _download_coa_pdfs(
            self,
            items: List[Dict],
            url_key='coa_url',
            id_key='product_id',
            verbose=True,
            pause=10.0
        ):
        """Download all COA PDFs from the given items."""
        for obs in items:
            url = obs.get(url_key)
            if not url:
                continue
            url_hash = self.cache.hash_url(url)
            if self.cache.get(url_hash):
                if verbose:
                    self.logger.info(f'Skipped (cached): {url}')
                continue
            try:
                response = requests.get(url)
                if response.status_code == 200:
                    filename = os.path.join(self.pdf_dir, obs[id_key] + '.pdf')
                    with open(filename, 'wb') as pdf_file:
                        pdf_file.write(response.content)
                    self.cache.set(url_hash, {'type': 'download', 'url': url, 'file': filename})
                    if verbose:
                        self.logger.info(f'Downloaded PDF: {filename}')
                else:
                    self.logger.warning(f'Failed to download {url}: HTTP {response.status_code}')
            except Exception as e:
                self.logger.error(f'Error downloading {url}: {str(e)}')
            time.sleep(pause)

    def get_results(
            self,
            headless: bool = True,
        ) -> pd.DataFrame:
        """Get Cannabis Realm results."""

        # Initialize.
        self._init_selenium(
            headless=headless,
            download_dir=self.pdf_dir,
        )

        # Define the categories.
        categories = [
            "/menu/categories/flower",
            "/menu/categories/pre-rolls",
            "/menu/categories/hash",
            "/menu/categories/vaporizers",
            "/menu/categories/edibles",
            "/menu/categories/tinctures",
            "/menu/categories/concentrates",
            "/menu/categories/beverages",
            "/menu/categories/topicals",
            "/menu/categories/spring-into-savings",
            "/menu/categories/skyworld-new-drops",
            "/menu/categories/superdope",
            "/menu/categories/new-flower",
            "/menu/categories/knock-out-deal",
            "/menu/categories/support-small-farmers",
            "/menu/categories/concentrates-deal-1g-under-25",
            "/menu/categories/buy-5-and-pay-for-4-revert-pre-rolls",
            "/menu/categories/woman-owned-brands",
            "/menu/categories/small-batch-premium-cannabis",
            "/menu/categories/staff-picks-flower-favorites",
            "/menu/categories/staff-picks-vapes",
            "/menu/categories/staff-pick-pre-rolls",
            "/menu/categories/staff-picks-edibles",
            "/menu/categories/heavy-weight",
            "/menu/categories/sleepy-time",
            "/menu/categories/indoor-flower",
            "/menu/categories/cbd"
        ]

        # Collect products from all categories
        all_products = []
        for category in categories:
            products = self._collect_products_from_category(category)
            all_products.extend(products)

        # Get detailed product information
        detailed_products = self._get_product_details(all_products)
        detailed_df = pd.DataFrame(detailed_products)
        self._save_results(detailed_df, prefix='ny-cannabis-realm-products-detailed')

        # Download COAs
        self._download_coa_pdfs(detailed_products, verbose=True)

        # Clean up Selenium
        if self.driver:
            self._quit_driver()

        # Return the results
        self.logger.info('✓ Collected results for Cannabis Realm (NY).')
        return detailed_df


# === Test ===
# [✓] Tested: 2025-04-01 by Keegan Skeate <keegan@cannlytics>
if __name__ == '__main__':

    # Get Cannabis Realm NY results.
    collector = CannabisRealmNYCollector(
        data_dir='D:/data/new-york/results',
        pdf_dir='D:/data/new-york/results/pdfs/cannabis-realm',
        cache_path='D://data/.cache/results-ny-cannabis-realm.jsonl',
        log_name='get_results_ny_cannabis_realm',
    )
    results = collector.get_results(headless=False)

    # # DEV:
    # data_dir='D:/data/new-york/results'
    # pdf_dir='D:/data/new-york/results/pdfs/cannabis-realm'
    # cache_path='D://data/.cache/results-ny-cannabis-realm.jsonl'
    # log_name='get_results_ny_cannabis_realm'
    # headless = False
