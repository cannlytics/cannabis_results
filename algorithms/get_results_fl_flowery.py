"""
Get Results | Florida | Flowery
Copyright (c) 2024 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 5/18/2023
Updated: 12/10/2024
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:

    Collect cannabis lab results data from The Flowery in Florida.

Data Source:

    - [The Flowery](https://support.theflowery.co)

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
from cannlytics.data.collectors import COACollector
from cannlytics.utils.constants import DEFAULT_HEADERS
from cannlytics.utils.utils import remove_duplicate_files


class FloweryCollector(COACollector):
    """Collector for The Flowery COAs in Florida."""

    def __init__(
            self,
            data_dir: str,
            pdf_dir: str,
            cache_path: Optional[str] = None,
            pause_time: float = 3.33,
            license_number: str = 'MMTC-2019-0020',
            lists_url: str = 'https://support.theflowery.co/hc/en-us/sections/7240468576283-Drop-Information',
            brand_name: str = 'Flowery',
            log_name: Optional[str] = 'get_results_fl_flowery',
        ):
        """
        Initialize the Flowery collector.
        Args:
            data_dir (str): The directory where data should be saved.
            cache_path (str, optional): Path to a cache file.
            pause_time (float, optional): Time to pause between requests.
            license_number (str): The producer license number for the brand.
            lists_url (str): The URL where COA listings are found.
            brand_name (str): The brand or producer name.
        """
        super().__init__(data_dir=data_dir, pdf_dir=pdf_dir, cache_path=cache_path, pause_time=pause_time, log_name=log_name)
        self.license_number = license_number
        self.lists_url = lists_url
        self.brand_name = brand_name

    def _get_coa_lists(self) -> list:
        """Get list pages that contain COA PDFs."""
        self.driver.get(self.lists_url)
        time.sleep(self.pause_time)
        coa_lists = []
        links = self.driver.find_elements(by=By.TAG_NAME, value='a')
        for link in links:
            if 'COAs' in link.text:
                href = link.get_attribute('href')
                if href:
                    coa_lists.append(href)
        self.logger.info(f'Found {len(coa_lists)} COA lists at {self.lists_url}')
        return coa_lists

    def _get_coa_urls(self, coa_lists: list) -> list:
        """Extract COA PDF URLs from the given COA list pages."""
        coa_urls = []
        for coa_list in coa_lists:
            self.driver.get(coa_list)
            time.sleep(self.pause_time)
            links = self.driver.find_elements(by=By.TAG_NAME, value='a')
            for link in links:
                href = link.get_attribute('href')
                if href and href.endswith('.pdf'):
                    coa_urls.append(href)
        self.logger.info(f'Found {len(coa_urls)} COA URLs.')
        return coa_urls

    def _download_pdfs(self, coa_urls: list):
        """Download COA PDFs to the local pdf directory."""
        license_pdf_dir = os.path.join(self.pdf_dir, self.license_number)
        os.makedirs(license_pdf_dir, exist_ok=True)
        for coa_url in coa_urls:

            # Skip if the file has already been downloaded.
            sample_id = coa_url.split('/')[-1].split('.')[0]
            batch_id = coa_url.split('/')[-2] if '/' in coa_url[:-1] else 'unknown-batch'
            outfile = os.path.join(license_pdf_dir, f'{batch_id}-{sample_id}.pdf')
            url_hash = self.cache.hash_url(coa_url)
            if (os.path.exists(outfile) or self.cache.get(url_hash)):
                self.logger.info(f'Cached: {coa_url}')
                continue

            # Download the file.
            time.sleep(self.pause_time)
            response = requests.get(coa_url, headers=DEFAULT_HEADERS)
            if response.status_code == 200:
                with open(outfile, 'wb') as pdf:
                    pdf.write(response.content)
                self.logger.info(f'Downloaded: {outfile}')
                self.cache.set(url_hash, {'type': 'download', 'url': coa_url, 'file': outfile})
            else:
                self.logger.error(f'Failed to download: {coa_url} (status: {response.status_code})')

        # Remove duplicates, if any.
        remove_duplicate_files(license_pdf_dir, verbose=True)

    def get_results(
            self,
            headless: Optional[bool] = True,
        ) -> pd.DataFrame:
        """Get COA results from Flowery."""

        # Initialize.
        self.logger.info('Starting Flowery COA collection...')
        self._init_selenium(download_dir=self.pdf_dir, headless=headless)
        coa_lists = self._get_coa_lists()
        coa_urls = self._get_coa_urls(coa_lists)

        # Save the COA URLs
        df = pd.DataFrame({'coa_url': coa_urls})
        date = datetime.now().isoformat()[:19].replace(':', '-')
        outfile = os.path.join(self.datasets_dir, f'ca-lab-result-urls-flowery-{date}.csv')
        df.to_csv(outfile, index=False)
        self.logger.info(f'Saved {len(df)} lab result URLs for Flowery to {outfile}')

        # Download the COA PDFs
        self._download_pdfs(coa_urls)

        # Close the driver.
        self._quit_driver()

        # Return the results.
        self.logger.info('✓ Collected results for Flowery (FL).')
        return df


# === Test ===
# [✓] Tested: 2024-12-10 by Keegan Skeate <keegan@cannlytics>
if __name__ == '__main__':

    # Get Flowery results.
    collector = FloweryCollector(
        data_dir='D:/data/florida/results',
        pdf_dir='D:/data/florida/results/pdfs/flowery',
        cache_path='D://data/.cache/results-fl-flowery.jsonl',
        log_name='get_results_fl_flowery',
    )
    results = collector.get_results(headless=True)
