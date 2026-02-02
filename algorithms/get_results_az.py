"""
Get Results | Arizona
Copyright (c) 2024 Cannlytics

Author: Keegan Skeate <https://github.com/keeganskeate>
Created: 8/24/2024
Updated: 12/15/2024
License: MIT License <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:

    Collect Arizona cannabis lab results from multiple data sources.

Data Sources:

    - High Grade: https://highgradeusa.com/testing/
    - Sticky Saguaro: https://testing.stickysaguaro.com/
    - Arizona Organix: https://arizonaorganix.org/coa-directory
    - Flow Distribution: https://flowdistribution.com/
    - Curaleaf: https://coas.curaleaf.com/transparency/

"""
# Standard imports:
import ast
import hashlib
import os
import random
from time import sleep
from typing import Optional
from urllib.parse import urljoin, quote_plus

# External imports:
import pandas as pd
import requests

# Internal imports:
from cannlytics.data.cache import Bogart
from cannlytics.data.collectors import COACollector
from cannlytics.data.web import download_google_drive_file
from cannlytics.utils.utils import remove_duplicate_files
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


class HighGradeCollector(COACollector):
    """Collector for High Grade COAs."""
    
    def get_results(
            self,
            headless: Optional[bool] = True,
        ) -> pd.DataFrame:
        """Get High Grade lab results."""

        # Initialize.
        url = 'https://highgradeusa.com/testing/'
        url_cache = Bogart(os.path.join(self.data_dir, 'urls-high-grade.jsonl'))
        self._init_selenium(headless=headless)
        self.driver.get(url)

        # Find all COA PDF URLs.
        lab_results = self.driver.find_elements(By.CSS_SELECTOR, "li[data-id]")
        self.logger.info(f"Found {len(lab_results)} results at High Grade.")
        new_urls = []
        for result in lab_results:
            try:
                batch_number = result.find_element(By.CSS_SELECTOR, "span:nth-child(1)").text
                try:
                    coa_url = result.find_element(By.XPATH, ".//a[text()='Download PDF']").get_attribute('href')
                except:
                    self.logger.info(f"No download link for batch: {batch_number}")
                    continue
                url_hash = hashlib.md5(coa_url.encode()).hexdigest()
                if url_cache.get(url_hash):
                    self.logger.info(f'Cached: {batch_number} - {coa_url}')
                    continue
                new_urls.append({
                    'batch_number': batch_number,
                    'coa_url': coa_url,
                    'url_hash': url_hash,
                })
                self.logger.info(f"Found link for batch: {batch_number}")
            except Exception as e:
                self.logger.info(f"Error processing result: {e}")

        # Close the driver.
        self.driver.quit()

        # Download the PDFs.
        for item in new_urls:
            try:
                if 'drive.google.com' in item['coa_url']:
                    file_id = item['coa_url'].split('/d/')[1].split('/')[0]
                    destination = os.path.join(self.pdf_dir, f"{item['url_hash']}.pdf")
                    download_google_drive_file(file_id, destination)
                    url_cache.set(item['url_hash'], item)
                    self.logger.info(f"Downloaded {item['batch_number']} to {destination}")
                    sleep(self.pause_time)
            except Exception as e:
                self.logger.info(f"Error downloading {item['batch_number']}: {e}")

        # Remove duplicates.
        remove_duplicate_files(self.pdf_dir, verbose=True)

        # Return the URLs..
        return pd.DataFrame(new_urls)


class StickySaguaroCollector(COACollector):
    """Collector for Sticky Saguaro COAs."""
    
    def clean_filename(self, filename: str) -> str:
        """Clean filename by removing ' - Shortcut.lnk' and other invalid characters."""
        return filename.replace(' - Shortcut.lnk', '')

    def download_pdf(self, url: str, destination: str):
        """Download PDF file."""
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(destination, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

    def get_results(
            self,
            headless: Optional[bool] = True,
        ) -> pd.DataFrame:
        """Get Sticky Saguaro lab results."""

        # Initialize.
        url = 'https://testing.stickysaguaro.com/'
        pause = self.pause_time
        url_cache = Bogart(os.path.join(self.data_dir, 'urls-sticky-saguaro.jsonl'))

        # Get the table and the total number of pages.
        self._init_selenium(download_dir=self.pdf_dir, headless=headless)
        self.driver.get(url)
        wait = WebDriverWait(self.driver, 15)
        table = wait.until(EC.presence_of_element_located((By.ID, "stickyInfo")))
        pagination = wait.until(EC.presence_of_element_located((By.ID, "stickyInfo_paginate")))
        last_page = int(pagination.find_elements(By.CSS_SELECTOR, "a.paginate_button")[-2].get_attribute("data-dt-idx"))
        self.logger.info(f"Total pages to process at Sticky Saguaro: {last_page}")

        # Find all of the PDF URLs.
        new_urls = []
        current_page = 1
        while current_page <= last_page:
            self.logger.info(f"Processing page {current_page}/{last_page}")
            sleep(pause)
            rows = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "#stickyInfo tbody tr")))
            for row in rows:
                try:
                    link_element = row.find_element(By.CSS_SELECTOR, "td a")
                    coa_url = link_element.get_attribute("href")
                    pdf_name = link_element.text
                    date = row.find_element(By.CSS_SELECTOR, "td.sorting_1").text
                    coa_url = self.clean_filename(coa_url)
                    url_hash = hashlib.md5(coa_url.encode()).hexdigest()
                    if url_cache.get(url_hash):
                        self.logger.info(f'Cached: {pdf_name}')
                        continue
                    new_urls.append({
                        'pdf_name': pdf_name,
                        'coa_url': urljoin(url, coa_url),
                        'date': date,
                        'url_hash': url_hash,
                    })
                    self.logger.info(f"Found PDF: {pdf_name}")
                except Exception as e:
                    self.logger.info(f"Error processing row: {e}")
            
            # Go to next page if not on last page.
            if current_page < last_page:
                try:
                    next_button = wait.until(EC.element_to_be_clickable((By.ID, "stickyInfo_next")))
                    next_button.click()
                    current_page += 1
                except Exception as e:
                    self.logger.info(f"Error navigating: {e}")
                    break
            else:
                break

        # Close the driver.
        self.driver.quit()

        # Download PDFs.
        self.logger.info(f"Found {len(new_urls)} new PDFs to download at Sticky Saguaro.")
        for item in new_urls:
            try:
                destination = os.path.join(self.pdf_dir, f'{item["url_hash"]}.pdf')
                self.download_pdf(item['coa_url'], destination)
                url_cache.set(item['url_hash'], item)
                self.logger.info(f"Downloaded {item['pdf_name']} to {destination}")
                sleep(pause)
            except Exception as e:
                self.logger.info(f"Error downloading {item['pdf_name']}: {e}")

        # Remove duplicates.
        remove_duplicate_files(self.pdf_dir, verbose=True)

        # Return the URLs.
        return pd.DataFrame(new_urls)


class ArizonaOrganixCollector(COACollector):
    """Collector for Arizona Organix lab results."""
    
    def __init__(self, *args, **kwargs):
        """Initialize the collector with column mappings."""
        super().__init__(*args, **kwargs)
        self.column_mapping = {
            'brand': 'brand',
            'Strain Name': 'strain_name',
            'Batch Number': 'batch_number',
            'Establishment and Point of Sale': 'retailer',
            'Establishment and Point of Sale license number': 'retailer_license_number',
            'Cultivated By': 'cultivator',
            'Cultivator License #': 'cultivator_license_number',
            'Harvest Date': 'date_harvested',
            'Manufactured By': 'manufacturer',
            'Manufactured License #': 'manufacturer_license_number',
            'Processing Date': 'date_processed',
            'Extraction Method(if applicable)': 'extraction_method',
            'COA URL': 'coa_url',
            'Warning': 'warning'
        }
        
    def _get_sheet_data(self) -> pd.DataFrame:
        """Extract data from Google Sheets."""
        published_url = 'https://docs.google.com/spreadsheets/u/0/d/e/2PACX-1vQ2QbcevWZu8oV0N8vAezdTxXuvKaR4JP9Em2UBJsMDsgn9Ho38EElt_O9ehzG5gZO0R4lkWc1FGYXt/pubhtml'
        self._init_selenium()
        try:
            self.driver.get(published_url)
            wait = WebDriverWait(self.driver, 10)
            
            # Get sheet names.
            sheet_menu = wait.until(EC.presence_of_element_located((By.ID, 'sheet-menu')))
            sheet_tabs = sheet_menu.find_elements(By.TAG_NAME, 'li')
            sheet_names = []
            for tab in sheet_tabs:
                anchor = tab.find_element(By.TAG_NAME, 'a')
                sheet_name = anchor.text.strip()
                if sheet_name:
                    sheet_names.append(sheet_name)
            
            # Extract data from each sheet.
            all_rows = []
            tables = self.driver.find_elements(By.CLASS_NAME, 'waffle')
            for i, table in enumerate(tables[1:]):
                tbody = table.find_element(By.TAG_NAME, 'tbody')
                rows = tbody.find_elements(By.TAG_NAME, 'tr')
                for row in rows:
                    row_data = [sheet_names[i + 1]]
                    cells = row.find_elements(By.TAG_NAME, 'td')
                    link_found = False
                    for cell in cells:
                        text = (cell.get_attribute('textContent') or 
                               cell.get_attribute('innerText') or 
                               cell.text)
                        links = cell.find_elements(By.TAG_NAME, 'a')
                        if links:
                            link_found = True
                            link_data = {
                                'text': links[0].get_attribute('textContent').strip(),
                                'url': links[0].get_attribute('href')
                            }
                            row_data.append(link_data)
                        else:
                            row_data.append(text.strip())
                    if link_found:
                        entry = dict(zip(self.column_mapping.values(), row_data))
                        all_rows.append(entry)
                        self.logger.info(f'Extracted data for: {entry.get("strain_name", "Unknown Strain")}')
                sleep(self.pause_time)
            return pd.DataFrame(all_rows)
        finally:
            self.driver.quit()
            
    def _download_coas(self, data: pd.DataFrame) -> None:
        """Download COA PDFs from the collected data."""
        for _, row in data.iterrows():
            obs = row.copy().to_dict()
            
            try:
                # Parse COA URL
                obs['coa_url'] = ast.literal_eval(obs['coa_url'])
                coa_url = obs['coa_url']['url']
                
                # Clean up Google Drive URLs
                if '?q=https://' in coa_url:
                    coa_url = 'https://' + coa_url.split('?q=https://')[-1]
                obs['coa_url']['url'] = coa_url
                
                # Check cache and download
                url_hash = hashlib.md5(coa_url.encode()).hexdigest()
                if self.cache.get(url_hash):
                    self.logger.info(f'Cached: {obs["strain_name"]}')
                    continue
                    
                filename = os.path.join(self.pdf_dir, f'{url_hash}.pdf')
                download_google_drive_file(coa_url, filename)
                self.cache.set(url_hash, obs)
                self.logger.info(f'Downloaded COA for {obs["strain_name"]}: {filename}')
                
                sleep(self.pause_time)
                
            except Exception as e:
                self.logger.error(f'Failed to download COA for {obs.get("strain_name", "Unknown")}: {str(e)}')
                continue
    
    def get_results(self) -> pd.DataFrame:
        """Get Arizona Organix lab results."""
        try:
            # Get data from Google Sheets.
            data = self._get_sheet_data()
            self.logger.info(f'Found {len(data)} COA entries')
            
            # Clean and save data.
            data.dropna(subset=['warning'], inplace=True)
            datafile = os.path.join(self.datasets_dir, 'arizona-organix-coas.csv')
            data.to_csv(datafile, index=False)
            self.logger.info(f'Saved COA data to {datafile}')
            
            # Download COA PDFs.
            self._download_coas(data)
            return data
            
        except Exception as e:
            self.logger.error(f'Failed to collect Arizona Organix results: {str(e)}')
            raise


class FlowDistributionCollector(COACollector):
    """Collector for Flow Distribution COAs."""
    
    def get_results(
            self,
            headless: Optional[bool] = True,
            search_queries: Optional[list[str]] = None,
        ) -> pd.DataFrame:
        """Get Flow Distribution lab results."""

        # Initialize.
        url_cache = Bogart(os.path.join(self.data_dir, 'urls-flow-distribution.jsonl'))
        pause = self.pause_time
        self._init_selenium(download_dir=self.pdf_dir, headless=headless)
        base_url = 'https://flowdistribution.com/'

        # Allow user to specify search queries.
        if search_queries is None:
            search_queries = [str(x)+str(y)+str(z) for x in range(10) for y in range(10) for z in range(10)]
            random.shuffle(search_queries)

        # Handle the age-gate manually.
        self.driver.get(base_url)
        sleep(5)

        # Search for COAs.
        collected = []
        for query in search_queries:
            search_url = f"{base_url}?s={quote_plus(query)}"
            self.driver.get(search_url)
            sleep(pause)
            posts = self.driver.find_elements(By.CLASS_NAME, "wp-block-post")
            self.logger.info(f"Found {len(posts)} results for query: {query}")
            if not posts:
                continue
            for post in posts:
                try:
                    date_element = post.find_element(By.CLASS_NAME, "wp-block-post-date")
                    date = date_element.find_element(By.TAG_NAME, "time").get_attribute("datetime")
                    title_element = post.find_element(By.CLASS_NAME, "wp-block-post-title")
                    link = title_element.find_element(By.TAG_NAME, "a")
                    retail_name = link.text.strip()
                    coa_url = link.get_attribute("href")
                    url_hash = hashlib.md5(coa_url.encode()).hexdigest()
                    if url_cache.get(url_hash):
                        self.logger.info(f'Cached: {retail_name}')
                        continue
                    destination = os.path.join(self.pdf_dir, f"{url_hash}.pdf")
                    response = requests.get(coa_url, allow_redirects=True)
                    if response.status_code == 200:
                        with open(destination, 'wb') as f:
                            f.write(response.content)
                        url_cache.set(url_hash, {'retail_name': retail_name, 'coa_url': coa_url, 'date': date, 'url_hash': url_hash})
                        self.logger.info(f"Downloaded {retail_name} to {destination}")
                        collected.append({'retail_name': retail_name, 'coa_url': coa_url, 'date': date, 'url_hash': url_hash})
                    else:
                        self.logger.info(f"Failed to download {retail_name}: HTTP {response.status_code}")
                    sleep(pause)
                except Exception as e:
                    self.logger.info(f"Error processing post: {e}")
        
        # Close the driver.
        self.driver.quit()

        # Remove duplicates.
        remove_duplicate_files(self.pdf_dir, verbose=True)

        # Return the results.
        return pd.DataFrame(collected)


class CuraleafCollector(COACollector):
    """Collector for Curaleaf COAs."""
    
    def get_results(
            self,
            headless: Optional[bool] = True,
            search_queries: Optional[list[str]] = None,    
        ) -> pd.DataFrame:
        """Get Curaleaf lab results."""

        # Initialize.
        url_cache = Bogart(os.path.join(self.data_dir, 'urls-curaleaf.jsonl'))
        pause = self.pause_time
        self._init_selenium(download_dir=self.pdf_dir, headless=headless)
        base_url = 'https://coas.curaleaf.com/transparency/'

        # Allow user to specify search queries.
        if search_queries is None:
            search_queries = [str(x)+str(y)+str(z) for x in range(10) for y in range(10) for z in range(10)]
            search_queries += [str(a)+str(b)+str(c)+str(d) for a in range(10) for b in range(10) for c in range(10) for d in range(10)]
            random.shuffle(search_queries)

        # Search for COAs.
        collected = []
        wait = WebDriverWait(self.driver, 10)
        for query in reversed(search_queries):
            search_url = f"{base_url}{quote_plus(query)}"
            self.driver.get(search_url)
            try:
                table = wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
            except:
                self.logger.info(f"No results found for query: {query}")
                continue
            sleep(pause)
            rows = table.find_elements(By.TAG_NAME, "tr")[1:]  # skip header
            self.logger.info(f"Found {len(rows)} results for query: {query}")
            if not rows:
                continue
            for row in rows:
                try:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) < 2:
                        continue
                    batch_number = cells[0].text.strip()
                    try:
                        view_link = row.find_element(By.CSS_SELECTOR, "a[href*='.pdf']")
                        coa_url = view_link.get_attribute("href")
                    except:
                        self.logger.info(f"No PDF link for batch: {batch_number}")
                        continue
                    url_hash = hashlib.md5(coa_url.encode()).hexdigest()
                    if url_cache.get(url_hash):
                        self.logger.info(f'Cached: {batch_number}')
                        continue
                    destination = os.path.join(self.pdf_dir, f"{url_hash}.pdf")
                    response = requests.get(coa_url, allow_redirects=True)
                    if response.status_code == 200:
                        with open(destination, 'wb') as f:
                            f.write(response.content)
                        url_cache.set(url_hash, {'batch_number': batch_number, 'coa_url': coa_url, 'query': query, 'url_hash': url_hash})
                        self.logger.info(f"Downloaded batch {batch_number} to {destination}")
                        collected.append({'batch_number': batch_number, 'coa_url': coa_url, 'query': query, 'url_hash': url_hash})
                    else:
                        self.logger.info(f"Failed to download {batch_number}: HTTP {response.status_code}")
                    sleep(pause)
                except Exception as e:
                    self.logger.info(f"Error processing row: {e}")
            
            # Clear browser memory between queries.
            self.driver.delete_all_cookies()
            try:
                self.driver.execute_script("window.localStorage.clear(); window.sessionStorage.clear();")
            except:
                pass

        # Close the driver.
        self.driver.quit()

        # Remove duplicates.
        remove_duplicate_files(self.pdf_dir, verbose=True)

        # Return the results.
        return pd.DataFrame(collected)


# === Tests ===
# [✓] Tested: 2024-12-11 by Keegan Skeate <keegan@cannlytics>
if __name__ == '__main__':

    # Get Flow Distribution results.
    collector = FlowDistributionCollector(
        data_dir='D:/data/arizona/results',
        pdf_dir='D:/data/arizona/results/pdfs/flow-distribution',
        cache_path='D://data/.cache/results-az-flow-distribution.jsonl',
        log_name='get_results_az_flow_distribution',
    )
    results = collector.get_results(headless=False)

    # # Get High Grade results.
    # # BROKEN
    # collector = HighGradeCollector(
    #     data_dir='D:/data/arizona/results',
    #     pdf_dir='D:/data/arizona/results/pdfs/high-grade',
    #     cache_path='D://data/.cache/results-az-high-grade.jsonl',
    #     log_name='get_results_az_high_grade',
    # )
    # results = collector.get_results(headless=False)

    # Get Sticky Saguaro results.
    collector = StickySaguaroCollector(
        data_dir='D:/data/arizona/results',
        pdf_dir='D:/data/arizona/results/pdfs/sticky-saguaro',
        cache_path='D://data/.cache/results-az-sticky-saguaro.jsonl',
        log_name='get_results_az_sticky_saguaro',
    )
    results = collector.get_results(headless=False)

    # # Get Arizona Organix results.
    # # BROKEN
    # collector = ArizonaOrganixCollector(
    #     data_dir='D:/data/arizona/results',
    #     pdf_dir='D:/data/arizona/results/pdfs/arizona-organix',
    #     cache_path='D://data/.cache/results-az-arizona-organix.jsonl',
    #     log_name='get_results_az_arizona_organix',
    # )
    # results = collector.get_results()

    # Get Curaleaf results.
    collector = CuraleafCollector(
        data_dir='D:/data/arizona/results',
        pdf_dir='D:/data/arizona/results/pdfs/curaleaf',
        cache_path='D://data/.cache/results-az-curaleaf.jsonl',
        log_name='get_results_az_curaleaf',
    )
    results = collector.get_results(headless=False)
