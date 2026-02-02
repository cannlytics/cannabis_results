"""
Get Results | Florida | Jungle Boys
Copyright (c) 2024 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 5/18/2023
Updated: 12/10/2024
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:

    Collect cannabis lab results data from Jungle Boys in Florida.

Data Sources:

    - [Jungle Boys Florida](https://jungleboysflorida.com)

"""
# Standard imports:
import os
import time
from datetime import datetime
from typing import Optional

# External imports:
import pandas as pd
import requests
from selenium.webdriver.common.by import By

# Internal imports:
from cannlytics.data.collectors import COACollector
from cannlytics.utils.constants import DEFAULT_HEADERS
from cannlytics.utils.utils import remove_duplicate_files


class JungleBoysCollector(COACollector):
    """Collector for Jungle Boys COAs in California."""
    
    def __init__(
            self,
            data_dir: str,
            cache_path: Optional[str] = None,
            pause_time: float = 3.33,
            products_url: str = 'https://jungleboys.com/products/',
            coas_url: str = 'https://jungleboys.com/coa/',
        ):
        """
        Initialize the Jungle Boys collector.

        Args:
            data_dir (str): The directory where data should be saved.
            cache_path (str, optional): Path to a cache file.
            pause_time (float, optional): Time to pause between requests.
            products_url (str): The URL of Jungle Boys products page.
            coas_url (str): The URL where COAs can be searched.
        """
        super().__init__(data_dir=data_dir, cache_path=cache_path, pause_time=pause_time)
        self.products_url = products_url
        self.coas_url = coas_url

    def _verify_age(self, driver):
        """Verify age gate on Jungle Boys' website if needed."""
        try:
            checkbox_js = "document.querySelector('input[type=checkbox].chakra-checkbox__input').click();"
            driver.execute_script(checkbox_js)
            time.sleep(self.pause_time)
            buttons = driver.find_elements(By.CSS_SELECTOR, 'button.chakra-button:not([disabled])')
            if buttons:
                buttons[0].click()
                time.sleep(self.pause_time)
        except Exception as e:
            self.logger.info(f'No age gate or error verifying age: {str(e)}')

    def _get_products(self, driver) -> list:
        """Get products listed on the Jungle Boys site."""
        driver.get(self.products_url)
        time.sleep(self.pause_time)

        # Verify age if needed.
        self._verify_age(driver)

        # This logic may need to be updated based on how products are displayed.
        product_cards = driver.find_elements(By.CSS_SELECTOR, '.wp-block-dovetail-ecommerce-product-list-list-view')
        products = []
        for el in product_cards:
            try:
                name = el.find_element(By.CSS_SELECTOR, 'div.dovetail-ecommerce-advanced-text').text
            except:
                continue
            image_url = el.find_element(By.TAG_NAME, 'img').get_attribute('src')
            # Extract category, strain, THC, price, etc. as needed.
            category = None
            strain_type = None
            price = None
            # Extract additional details as needed.
            products.append({
                'name': name,
                'image_url': image_url,
                'category': category,
                'strain_type': strain_type,
                'price': price,
            })
        self.logger.info(f'Found {len(products)} products.')
        return products

    def _search_and_download_coas(self, driver, products: list):
        """Search COAs for each product and download the associated PDFs."""
        # Ensure directory for COAs.
        license_pdf_dir = os.path.join(self.pdf_dir, 'jungleboys')
        os.makedirs(license_pdf_dir, exist_ok=True)

        # Search for COAs.
        driver.get(self.coas_url)
        time.sleep(self.pause_time)
        for product in products:
            product_name = product.get('name')
            if not product_name:
                continue
            self.logger.info(f'Searching COAs for: {product_name}')

            # Search.
            search_div = driver.find_element(By.CSS_SELECTOR, "div.wp-block-create-block-coa__search")
            search_box = search_div.find_element(By.TAG_NAME, 'input')
            search_box.clear()
            search_box.send_keys(product_name)
            time.sleep(self.pause_time)

            # Find PDF links.
            pdf_links = driver.find_elements(By.CSS_SELECTOR, ".wp-block-create-block-coa__result a[target='_blank']")
            pdf_urls = []
            for link in pdf_links:
                # Ensure not hidden.
                parent_li = link.find_element(By.XPATH, "./ancestor::li[1]")
                if "hidden" not in parent_li.get_attribute("class"):
                    pdf_urls.append(link.get_attribute('href'))

            self.logger.info(f'Found {len(pdf_urls)} PDFs for {product_name}.')
            for pdf_url in pdf_urls:
                pdf_name = pdf_url.split('/')[-1]
                outfile = os.path.join(license_pdf_dir, pdf_name)
                url_hash = self.cache.hash_url(pdf_url)
                if (os.path.exists(outfile) or self.cache.get(url_hash)):
                    self.logger.info(f'Cached: {pdf_url}')
                    continue
                response = requests.get(pdf_url, headers=DEFAULT_HEADERS)
                if response.status_code == 200:
                    with open(outfile, 'wb') as f:
                        f.write(response.content)
                    self.logger.info(f'Downloaded: {outfile}')
                    self.cache.set(url_hash, {'type': 'download', 'url': pdf_url, 'file': outfile})
                else:
                    self.logger.error(f'Failed to download {pdf_url}: HTTP {response.status_code}')

        # Remove duplicates, if any.
        remove_duplicate_files(license_pdf_dir, verbose=True)

    def get_results(
            self,
            headless: Optional[bool] = True,
        ) -> pd.DataFrame:
        """Get COA results from Jungle Boys."""
        self.logger.info('Starting Jungle Boys COA collection...')
        self._init_selenium(headless=headless)
        try:

            # Get products.
            products = self._get_products(self.driver)
            if not products:
                self.logger.warning('No products found. Possibly update selectors or URLs.')

            # Search for COAs and download PDFs.
            self._search_and_download_coas(self.driver, products)

            # Save product data (COA URLs recorded in logs, not as a dataset here).
            df = pd.DataFrame(products)
            timestamp = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
            outfile = os.path.join(self.datasets_dir, f'ca-jungleboys-products-{timestamp}.csv')
            df.to_csv(outfile, index=False)
            self.logger.info(f'Saved {len(df)} product records to {outfile}')
            return df
        finally:
            self._quit_driver()
            self.logger.info('✓ Collected results for Jungle Boys (FL).')


# === Test ===
# [✓] Tested: 2024-12-10 by Keegan Skeate <keegan@cannlytics>
if __name__ == '__main__':

    # Get Jungle Boys results.
    collector = JungleBoysCollector(
        data_dir='D:/data/florida/results',
        pdf_dir='D:/data/florida/results/pdfs/jungleboys',
        cache_path='D://data/.cache/results-fl-jungleboys.jsonl',
        log_name='get_results_fl_jungleboys',
    )
    results = collector.get_results(headless=False)
