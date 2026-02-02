"""
Get Results | New York
Copyright (c) 2024 Cannlytics

Authors: Keegan Skeate <https://github.com/keeganskeate>
Created: 6/24/2024
Updated: 12/10/2024
License: MIT License <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:

    Collect New York cannabis lab results from multiple data sources.

Data Sources:
    
    - [Jetty Extracts](https://jettyextracts.com/coa-new-york/)
    - [MFNY]('https://www.mycoa.info/')
    - [Hudson Cannabis](https://www.hudsoncannabis.co/coas)

"""
# Standard imports:
from concurrent.futures import ThreadPoolExecutor
import os
from typing import Optional

# External imports:
from cannlytics.data.collectors import COACollector
from cannlytics.data.web import download_google_drive_file
from cannlytics.utils.utils import remove_duplicate_files
try:
    import gdown
except:
    print('Import Error: Proceeding without `gdown`.')
import pandas as pd
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


class JettyExtractsCollector(COACollector):
    """Collector for Jetty Extracts lab results."""
    
    def get_results(self) -> pd.DataFrame:
        """Get Jetty Extracts lab results."""

        # Initialize.
        pdf_dir = os.path.join(self.pdf_dir, 'jetty-extracts')
        os.makedirs(pdf_dir, exist_ok=True)
        
        # Download CSV data.
        # FIXME: Implement CSV download
        datafile = os.path.join(self.datasets_dir, "jetty-extracts-coas.csv")
        
        # Download COAs in parallel.
        coas = pd.read_csv(datafile)
        last_column = coas.columns[-1]
        folder_urls = coas[last_column].values

        def download_folder(url: str) -> None:
            try:
                gdown.download_folder(url, output=pdf_dir, quiet=False)
            except Exception as e:
                self.logger.error(f'Failed to download {url}: {str(e)}')

        # Download folders in parallel.
        with ThreadPoolExecutor(max_workers=3) as executor:
            executor.map(download_folder, folder_urls)
        
        # Return the results.
        self.logger.info('✓ Collected results for Jetty Extracts (NY).')
        return coas


class MyCOACollector(COACollector):
    """Collector for MyCOA lab results."""
    
    def get_results(
            self,
            headless: Optional[bool] = True,
        ) -> pd.DataFrame:
        """Get MyCOA lab results."""

        # Initialize.
        pdf_dir = os.path.join(self.pdf_dir, 'my-coa')
        os.makedirs(pdf_dir, exist_ok=True)
        self._init_selenium(download_dir=pdf_dir, headless=headless)
        
        try:
            # Get PDF links.
            self.driver.get('https://www.mycoa.info/')
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//a[contains(@href, 'dropbox.com/s')]"))
            )
            pdf_links = self.driver.find_elements(By.XPATH, "//a[contains(@href, 'dropbox.com/s')]")
            pdf_urls = [link.get_attribute('href') for link in pdf_links]
            
            # Download PDFs.
            for pdf_url in pdf_urls:

                # Skip if the file has already been downloaded.
                if self.cache.get(self.cache.hash_url(pdf_url)):
                    self.logger.info(f'Cached: {pdf_url}')
                    continue

                # Download the file.
                self.driver.get(pdf_url)
                download_button = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//button[@aria-label='Download']"))
                )
                download_button.click()
                self.cache.set(self.cache.hash_url(pdf_url), {'downloaded': True})
                self.logger.info(f'Downloaded: {pdf_url}')
            
            # Remove duplicates.
            remove_duplicate_files(pdf_dir, verbose=True)

            # Return the results.
            return pd.DataFrame({'pdf_urls': pdf_urls})
        
        # Clean up.
        finally:
            self._quit_driver()
            self.logger.info('✓ Collected results for MyCOA (NY).')


class HudsonCannabisCollector(COACollector):
    """Collector for Hudson Cannabis lab results."""
    
    def get_results(
            self,
            headless: Optional[bool] = True,
        ) -> pd.DataFrame:
        """Get Hudson Cannabis lab results."""

        # Initialize.
        pdf_dir = os.path.join(self.pdf_dir, 'hudson-cannabis')
        os.makedirs(pdf_dir, exist_ok=True)
        self._init_selenium(download_dir=pdf_dir, headless=headless)
        
        try:
            # Get PDF links.
            self.driver.get('https://www.hudsoncannabis.co/coas')
            WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.ID, "root")))
            pdf_links = self.driver.find_elements(By.XPATH, "//a[contains(@href, 'drive.google.com/file')]")
            pdf_urls = [link.get_attribute('href') for link in pdf_links]
            
            # Download PDFs.
            for pdf_url in pdf_urls:
            
                # Skip if the file has already been downloaded.
                if self.cache.get(self.cache.hash_url(pdf_url)):
                    self.logger.info(f'Cached: {pdf_url}')
                    continue
                
                # Download the file.
                pdf_name = f"{pdf_url.split('/')[-2]}.pdf"
                save_path = os.path.join(pdf_dir, pdf_name)
                download_google_drive_file(pdf_url, save_path)
                self.cache.set(self.cache.hash_url(pdf_url), {'file': save_path})
                self.logger.info(f'Downloaded: {save_path}')
            
            # Remove duplicates.
            remove_duplicate_files(pdf_dir, verbose=True)

            # Return the results.
            return pd.DataFrame({'pdf_urls': pdf_urls})
        
        # Clean up.
        finally:
            self._quit_driver()
            self.logger.info('✓ Collected results for Hudson Cannabis (NY).')


# === Tests ===
# [✓] Tested: 2024-12-11 by Keegan Skeate <keegan@cannlytics>
if __name__ == '__main__':

    # # Get Jetty Extracts results.
    # collector = JettyExtractsCollector(
    #     data_dir='D:/data/new-york/results',
    #     pdf_dir='D:/data/new-york/results/pdfs/jetty-extracts',
    #     cache_path='D://data/.cache/results-ny-jetty-extracts.jsonl',
    #     log_name='get_results_ny_jetty_extracts',
    # )
    # results = collector.get_results()

    # Get MyCOA results.
    collector = MyCOACollector(
        data_dir='D:/data/new-york/results',
        pdf_dir='D:/data/new-york/results/pdfs/my-coa',
        cache_path='D://data/.cache/results-ny-my-coa.jsonl',
        log_name='get_results_ny_my_coa',
    )
    results = collector.get_results(headless=False)

    # Get Hudson Cannabis results.
    collector = HudsonCannabisCollector(
        data_dir='D:/data/new-york/results',
        pdf_dir='D:/data/new-york/results/pdfs/hudson-cannabis',
        cache_path='D://data/.cache/results-ny-hudson-cannabis.jsonl',
        log_name='get_results_ny_hudson_cannabis',
    )
    results = collector.get_results(headless=False)
