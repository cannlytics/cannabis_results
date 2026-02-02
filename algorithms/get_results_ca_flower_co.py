"""
Get Results | California | Flower Company
Copyright (c) 2023-2024 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 12/8/2023
Updated: 12/10/2024
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:

    Collect cannabis lab result data published by the Flower Company.

Data Source:

    - [Flower Company](https://flowercompany.com/)

"""
# Standard imports:
import os
import time
from typing import List, Dict

# External imports:
import pandas as pd
import requests
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

# Internal imports:
from cannlytics.data.collectors import COACollector
from cannlytics.data import create_sample_id

# Base URL and categories
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

INDICA_PERCENTAGES = {
    'Indica': 1.0,
    'I-Hybrid': 0.75,
    'Hybrid': 0.5,
    'S-Hybrid': 0.25,
    'Sativa': 0.0,
}


class FlowerCompanyCollector(COACollector):
    """Collector for Flower Company cannabis lab results."""

    def _click_yes_button(self):
        """Click the 'Yes' age gate button if present."""
        try:
            yes_button = self.driver.find_element(By.CLASS_NAME, 'age-gate-yes-button')
            yes_button.click()
            time.sleep(2)
        except Exception:
            pass

    def _click_show_more_button(self):
        """Click 'Show More' until no longer found."""
        while True:
            try:
                more_button = self.driver.find_element(By.CLASS_NAME, 'show-more-button')
                more_button.click()
                time.sleep(3)
            except Exception:
                break

    def _extract_weight(self, amount_str: str):
        """Extract the numerical weight in grams from the amount string."""
        if amount_str:
            parts = amount_str.split('(')
            if len(parts) > 1:
                weight = parts[1].split('g')[0].strip()
                try:
                    return float(weight)
                except ValueError:
                    pass
        return None

    def _price_to_float(self, price_str: str):
        """Convert a price string like '$50' to float."""
        try:
            return float(price_str.replace('$', ''))
        except:
            return None

    def _download_coa_pdfs(
            self,
            items: List[Dict],
            url_key='lab_results_url',
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

    def _get_product_pages(self) -> List[str]:
        """Get a list of brand and category pages."""
        self.driver.get(BASE_URL + 'menu')
        self._click_yes_button()
        time.sleep(self.pause_time)
        try:
            div = self.driver.find_element(By.CLASS_NAME, 'special-content-brand-row')
            links = div.find_elements(By.TAG_NAME, 'a')
            brand_pages = [link.get_attribute('href').replace(BASE_URL, '') for link in links]
        except Exception:
            brand_pages = []
        return CATEGORY_PAGES + brand_pages

    def _collect_products(self, pages: List[str]) -> List[Dict]:
        """Collect product listings from each page."""
        products = []
        recorded = set(self.cache.get('product_urls') or [])
        for page in pages:
            self.driver.get(BASE_URL + page)
            self._click_yes_button()
            self._click_show_more_button()
            time.sleep(self.pause_time)
            cards = self.driver.find_elements(By.CLASS_NAME, 'product-card-wrapper')
            self.logger.info(f'Found {len(cards)} products for page: {page}')
            for card in cards:
                # Extract product details.
                try:
                    producer = card.find_element(By.CSS_SELECTOR, '.favorite-company a').text.strip()
                except:
                    producer = None
                try:
                    product_name = card.find_element(By.CSS_SELECTOR, '.favorite-product-name a').text.strip()
                except:
                    product_name = None
                try:
                    product_url = card.find_element(By.CSS_SELECTOR, '.favorite-product-name a').get_attribute('href')
                except:
                    product_url = None

                if not product_url or product_url in recorded:
                    continue
                recorded.add(product_url)
                try:
                    total_thc_txt = card.find_element(By.CSS_SELECTOR, '.product-card-thc').text.strip()
                except:
                    total_thc_txt = ''
                try:
                    discount_price_str = card.find_element(By.CSS_SELECTOR, '.price.product-card-price-actual').text.strip()
                except:
                    discount_price_str = '$0'
                try:
                    price_str = card.find_element(By.CSS_SELECTOR, '.price.retail.product-card-price-retail').text.strip()
                except:
                    price_str = discount_price_str

                # Get the amount (weight).
                try:
                    amount_txt = card.find_element(By.CSS_SELECTOR, '.solo-variant-toggle').text.strip()
                except:
                    # If solo variant not found, try select element.
                    try:
                        select_element = card.find_element(By.CSS_SELECTOR, 'select.new-product-card-variant-select')
                        select_object = Select(select_element)
                        amount_options = [option.text.strip() for option in select_object.options]
                        amount_txt = amount_options[0] if amount_options else None
                    except:
                        amount_txt = None

                # Classification (assuming first line = classification).
                classification = card.text.split('\n')[0] if card.text else 'Hybrid'
                indica_perc = INDICA_PERCENTAGES.get(classification, 0.5)
                sativa_perc = 1 - indica_perc

                # Clean total THC.
                total_thc_units = 'percent' if '%' in total_thc_txt.lower() else 'mg'
                try:
                    total_thc = float(total_thc_txt.lower().replace('% thc', '').replace('mg thc', '').strip())
                except:
                    total_thc = None

                # Clean the price.
                price = self._price_to_float(price_str)
                discount_price = self._price_to_float(discount_price_str)
                discount = (price - discount_price) if price and discount_price else 0
                amount = self._extract_weight(amount_txt)

                # Add the product to the list.
                products.append({
                    'product_name': product_name,
                    'category': page.split('/')[-1],
                    'producer': producer,
                    'total_thc': total_thc,
                    'total_thc_units': total_thc_units,
                    'price': price,
                    'discount_price': discount_price,
                    'discount': discount,
                    'amount': amount,
                    'classification': classification,
                    'indica_percentage': indica_perc,
                    'sativa_percentage': sativa_perc,
                    'product_url': product_url,
                })

        # Save the product URLs.
        self.cache.set('product_urls', list(recorded))
        return products

    def _augment_product_data(self, products: pd.DataFrame) -> List[Dict]:
        """For each product, visit the product page to collect additional data."""
        data = []
        for _, product in products.iterrows():
            product_url = product['product_url']
            self.logger.info(f'Collecting details for: {product_url}')
            self.driver.get(product_url)
            time.sleep(self.pause_time)
            self._click_yes_button()

            # Extract additional details.
            try:
                types = self.driver.find_elements(By.CSS_SELECTOR, '.detail-product-type')
                product_type = types[0].text.strip() if types else 'Unknown'
                product_subtype = types[1].text.strip() if len(types) >= 2 else None
            except:
                product_type = 'Unknown'
                product_subtype = None

            try:
                product_description = self.driver.find_element(By.CSS_SELECTOR, '.product-view-description').text.strip()
            except:
                product_description = None
            info_rows = self.driver.find_elements(By.CSS_SELECTOR, '.row.product-view-row')
            contents, effects, aromas, lineage, lab_results_url = '', '', '', '', ''
            for row in info_rows:
                parts = row.text.split('\n')
                field = parts[0].lower() if parts else ''
                if 'contents' in field:
                    contents = parts[-1]
                elif 'effects' in field:
                    effects = parts[-1]
                elif 'aromas' in field:
                    aromas = parts[-1]
                elif 'lineage' in field:
                    lineage = parts[-1]
                elif 'tested' in field:
                    try:
                        el = row.find_element(By.TAG_NAME, 'a')
                        lab_results_url = el.get_attribute('href')
                    except:
                        pass

            # Distributor info.
            els = self.driver.find_elements(By.CSS_SELECTOR, '.row.d-block .detail-sub-text')
            distributor = els[-2].text.strip() if len(els) > 1 else None
            distributor_license_number = els[-1].text.strip() if len(els) > 1 else None

            # Image URL.
            try:
                image_url = self.driver.find_element(By.CSS_SELECTOR, '.product-image-lg').get_attribute('src')
            except:
                image_url = None

            # Ensure price / amount updated if missing.
            if pd.isnull(product['price']):
                # Try to re-extract price and amount from product page.
                try:
                    price_element = self.driver.find_element(By.ID, 'variant-price-retail')
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", price_element)
                    time.sleep(0.33)
                    price_str = price_element.text
                    discount_price_str = self.driver.find_element(By.ID, 'variant-price').text
                    amount_txt = self.driver.find_element(By.CSS_SELECTOR, '.variant-toggle').text
                    product['amount'] = self._extract_weight(amount_txt)
                    product['price'] = self._price_to_float(price_str)
                    product['discount_price'] = self._price_to_float(discount_price_str)
                    product['discount'] = (product['price'] - product['discount_price']
                                           if product['price'] and product['discount_price'] else 0)
                except:
                    pass

            # Ensure THC/CBD updated if missing.
            if pd.isnull(product['total_thc']):
                try:
                    total_thc_txt = self.driver.find_element(By.CSS_SELECTOR, '.product-card-thc').text
                    product['total_thc'] = float(total_thc_txt.lower().replace('% thc', '').replace('mg thc', '').strip())
                    product['total_thc_units'] = 'percent' if '%' in total_thc_txt.lower() else 'mg'
                except:
                    pass

            if 'total_cbd' not in product or pd.isnull(product['total_cbd']):
                try:
                    total_cbd_txt = self.driver.find_element(By.CSS_SELECTOR, '.product-card-cbd').text
                    product['total_cbd'] = float(total_cbd_txt.lower().replace('% cbd', '').replace('mg cbd', '').strip())
                    product['total_cbd_units'] = 'percent' if '%' in total_cbd_txt.lower() else 'mg'
                except:
                    product['total_cbd'] = None
                    product['total_cbd_units'] = None

            # Classification check.
            if not product['classification']:
                try:
                    el = self.driver.find_element(By.CSS_SELECTOR, '.product-detail-type-container')
                    classification = el.text.split('\n')[0]
                    product['classification'] = classification
                    product['indica_percentage'] = INDICA_PERCENTAGES.get(classification, 0.5)
                    product['sativa_percentage'] = 1 - product['indica_percentage']
                except:
                    pass

            # Create unique product ID.
            product_id = create_sample_id(
                private_key=str(product.get('total_thc', '')),
                public_key=product['product_name'] or '',
                salt=product['producer'] or '',
            )

            # Record the product item details.
            record = product.to_dict()
            record.update({
                'product_id': product_id,
                'lab_results_url': lab_results_url,
                'image_url': image_url,
                'product_type': product_type,
                'product_subtype': product_subtype,
                'product_description': product_description,
                'product_contents': contents,
                'predicted_effects': effects,
                'predicted_aromas': aromas.split(', ') if aromas else [],
                'lineage': lineage,
                'distributor': distributor,
                'distributor_license_number': distributor_license_number,
            })
            data.append(record)

        return data

    def get_results(
            self,
            headless: bool = True,
        ) -> pd.DataFrame:
        """Get Flower Company results."""

        # Initialize.
        self._init_selenium(
            headless=headless,
            download_dir=self.pdf_dir,
        )
        pages = self._get_product_pages()
        products = self._collect_products(pages)
        product_df = pd.DataFrame(products)

        # Save intermediate product data.
        self._save_results(product_df, prefix='ca-flower-company-products-initial')

        # Augment products with details.
        augmented_data = self._augment_product_data(product_df)
        augmented_df = pd.DataFrame(augmented_data)
        self._save_results(augmented_df, prefix='ca-flower-company-products-augmented')

        # Download COAs.
        self._download_coa_pdfs(augmented_data, verbose=True)

        # Clean up Selenium.
        if self.driver:
            self._quit_driver()

        # Return the results.
        self.logger.info('✓ Collected results for Flower Company (CA).')
        return augmented_df


# === Test ===
# [✓] Tested: 2024-12-10 by Keegan Skeate <keegan@cannlytics>
if __name__ == '__main__':

    # Get Flower Company results.
    collector = FlowerCompanyCollector(
        data_dir='D:/data/california/results',
        pdf_dir='D:/data/california/results/pdfs/flower-company',
        cache_path='D://data/.cache/results-ca-flower-company.jsonl',
        log_name='get_results_ca_flower_co',
    )
    results = collector.get_results(headless=True)
