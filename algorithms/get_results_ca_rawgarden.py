"""
Get Results | California | Raw Garden
Copyright (c) 2024 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 8/23/2022
Updated: 12/9/2024
License: CC-BY 4.0 <https://huggingface.co/datasets/cannlytics/cannabis_tests/blob/main/LICENSE>

Description:
    
    Collect Raw Garden's publicly published lab results.

Data Source:

    - [Raw Garden Lab Results](https://rawgarden.farm/lab-results/)

"""
# Standard imports:
import os
import time
from typing import Optional

# External imports:
from bs4 import BeautifulSoup
import pandas as pd
import requests

# Internal imports:
from cannlytics.data.collectors import COACollector
from cannlytics.utils.constants import DEFAULT_HEADERS
from cannlytics.utils import kebab_case


# Constants for Raw Garden data source.
BASE = 'https://rawgarden.farm/lab-results/'


class RawGardenCollector(COACollector):
    """Collector for Raw Garden lab results."""
    
    def get_rawgarden_products(self) -> pd.DataFrame:
        """
        Get Raw Garden's lab results page. Then get all the product
        categories and product data including `coa_pdf`, `lab_results_url`,
        `product_name`, `product_subtype`, and `date_retail`.
        
        Returns:
            DataFrame: Returns a DataFrame of product data.
        """
        observations = []
        response = requests.get(BASE, headers=DEFAULT_HEADERS)
        soup = BeautifulSoup(response.content, 'html.parser')
        categories = soup.find_all('div', attrs={'class': 'category-content'})
        for category in categories:
            subtype = category.find('h3').text
            dates = category.findAll('h5', attrs={'class': 'result-date'})
            names = category.findAll('h5')
            names = [div for div in names if div.get('class') is None]
            links = category.findAll('a')
            for i, link in enumerate(links):
                try:
                    href = link.get('href')
                    date = pd.to_datetime(dates[i].text)
                    name = names[i].text
                    if href.endswith('.pdf'):
                        observations.append({
                            'coa_pdf': href.split('/')[-1],
                            'lab_results_url': href,
                            'product_name': name,
                            'product_subtype': subtype,
                            'date_retail': date,
                        })
                except AttributeError:
                    continue
        results = pd.DataFrame(observations)
        results['date_retail'] = results['date_retail'].apply(lambda x: x.isoformat()[:19])
        return results

    def download_rawgarden_coas(
            self,
            items: pd.DataFrame,
            pause: Optional[float] = 0.24,
            verbose: Optional[bool] = True,
        ) -> None:
        """
        Download Raw Garden product COAs to `product_subtype` folders.
        
        Args:
            items (DataFrame): A DataFrame of products.
            pause (float): A pause to respect the server.
            verbose (bool): Print status messages.
        """
        if verbose:
            total = len(items)
            self.logger.info('Downloading %i PDFs, ETA > %.2fs' % (total, total * pause))

        # Create a folder of each subtype.
        subtypes = list(items['product_subtype'].unique())
        for subtype in subtypes:
            folder = kebab_case(subtype)
            subtype_folder = os.path.join(self.pdf_dir, folder)
            if not os.path.exists(subtype_folder):
                os.makedirs(subtype_folder)

        # Download each COA PDF.
        for i, row in items.iterrows():
            url = row['lab_results_url']
            subtype = row['product_subtype']
            filename = url.split('/')[-1]
            folder = kebab_case(subtype)
            outfile = os.path.join(self.pdf_dir, folder, filename)

            # Skip if file exists.
            if os.path.isfile(outfile):
                continue

            response = requests.get(url, headers=DEFAULT_HEADERS)
            with open(outfile, 'wb') as pdf:
                pdf.write(response.content)
            if verbose:
                message = f'Downloaded {i+1}/{len(items)} | {folder}/{filename}'
                self.logger.info(message)
            time.sleep(pause)

    def get_results(self) -> pd.DataFrame:
        """Get Raw Garden lab results."""
        
        # Get products.
        products = self.get_rawgarden_products()
        self._save_results(products, prefix='ca-rawgarden-products')

        # Download COA PDFs.
        self.download_rawgarden_coas(products, pause=0.24, verbose=True)

        # Return the results.
        self.logger.info('✓ Collected results for RawGarden (CA).')
        return products


# === Test ===
# [✓] Tested: 2024-12-10 by Keegan Skeate <keegan@cannlytics>
if __name__ == '__main__':

    # Get Raw Garden results.
    collector = RawGardenCollector(
        data_dir='D:/data/california/results',
        pdf_dir='D:/data/california/results/pdfs/rawgarden',
        cache_path='D://data/.cache/results-ca-rawgarden.jsonl',
        log_name='get_results_ca_rawgarden',
    )
    results = collector.get_results()
