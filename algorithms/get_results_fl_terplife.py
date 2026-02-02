"""
Get Results | Florida | TerpLife Labs
Copyright (c) 2023-2024 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 5/18/2023
Updated: 12/10/2024
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:

    Collect cannabis lab results data published by TerpLife Labs.

Data Source:

    - [TerpLife Labs](https://www.terplifelabs.com)

"""
# Standard imports:
import itertools
import os
import random
import string
import time
from typing import List

# External imports:
import pandas as pd
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# Internal imports:
from cannlytics.data.collectors import COACollector


class TerpLifeLabsCollector(COACollector):
    """Collector for TerpLife Labs COAs."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.url = 'https://www.terplifelabs.com/coa/'

    def _get_search_box(self):
        """Find the search box input element."""
        igd_body = self.driver.find_element(By.CLASS_NAME, 'igd-body')
        inputs = igd_body.find_elements(By.TAG_NAME, 'input')
        for input_el in inputs:
            if input_el.get_attribute('placeholder') == 'Enter a keyword to search':
                return input_el
        return None

    def _query_search_box(self, query: str):
        """Enter a query into the search box and initiate search."""
        search_box = self._get_search_box()
        if search_box:
            self.driver.execute_script('arguments[0].scrollIntoView();', search_box)
            time.sleep(0.3)
            search_box.clear()
            search_box.send_keys(query)
            time.sleep(0.3)
            # Assuming the next sibling is the search button.
            search_button = search_box.find_element(By.XPATH, 'following-sibling::*[1]')
            search_button.click()
            self.logger.info(f'Querying: {query}')
        else:
            self.logger.warning('No search box found!')

    def _download_search_results(self, wait=120):
        """Download the results of a search."""

        # TODO: Refactor to make easier to debug.

        # Wait for results to load.
        time.sleep(wait)
        load = EC.presence_of_element_located((By.CLASS_NAME, 'file-list'))
        table = WebDriverWait(self.driver, wait).until(load)
        rows = table.find_elements(By.CLASS_NAME, 'file-item')
        self.logger.info(f'Found {len(rows)} rows of results.')

        # Download each file in the search results.
        results_data = []
        for row in rows:
            file_name = ''
            try:
                file_name = row.find_element(By.CLASS_NAME, 'file-item-name').text
                if file_name == 'COAS':
                    continue
                outfile = os.path.join(self.pdf_dir, file_name)
                if os.path.exists(outfile):
                    self.logger.info(f'Cached: {outfile}')
                    file_hash = self.cache.hash_file(outfile)
                    self.cache.set(file_hash, {'type': 'download', 'file': outfile})
                    continue
            except Exception as e:
                self.logger.error(f'Error finding file name: {str(e)}')
                time.sleep(60)
                continue

            # Click on the row to open modal.
            try:
                self.driver.execute_script('arguments[0].scrollIntoView();', row)
                time.sleep(3.33)
                row.click()
            except Exception as e:
                self.logger.error(f'Error clicking: {file_name} - {str(e)}')
                continue

            # Download file in the modal.
            try:
                time.sleep(random.uniform(wait, wait + 1))
                download_button = self.driver.find_element(By.CLASS_NAME, 'lg-download')
                download_button.click()
                self.logger.info(f'Downloaded: {file_name}')
                # Wait for download to complete (approx)
                time.sleep(random.uniform(wait, wait + 1))
                file_hash = self.cache.hash_file(os.path.join(self.pdf_dir, file_name))
                self.cache.set(file_hash, {'type': 'download', 'file': os.path.join(self.pdf_dir, file_name)})
                results_data.append({'file_name': file_name, 'coa_path': os.path.join(self.pdf_dir, file_name)})
            except Exception as e:
                self.logger.error(f'Error downloading {file_name}: {str(e)}')
                # Attempt to close the modal even if fail
            finally:
                # Close the modal
                try:
                    close_button = self.driver.find_element(By.CLASS_NAME, 'lg-close')
                    close_button.click()
                except:
                    self.logger.warning(f'Error closing modal for {file_name}')

        return pd.DataFrame(results_data)

    @staticmethod
    def _get_day_month_combinations():
        """Get all day-month combinations as queries."""
        day_month_combinations = []
        for month in range(1, 13):
            if month in [4, 6, 9, 11]:
                days_in_month = 30
            elif month == 2:
                days_in_month = 29
            else:
                days_in_month = 31
            for day in range(1, days_in_month + 1):
                combination = f'{month:02d}{day:02d}'
                day_month_combinations.append(combination)
        return day_month_combinations

    @staticmethod
    def _add_digits(strings):
        """Add digits 0-9 to each string in a list."""
        return [s + str(digit) for s in strings for digit in range(10)]

    @staticmethod
    def _add_letters(strings):
        """Add letters a-z to each string in a list."""
        return [s + letter for s in strings for letter in string.ascii_lowercase]

    def _perform_queries(
            self,
            queries: List[str],
            wait: int = 120,
            pause: float = 3.33,
        ) -> pd.DataFrame:
        """Perform queries and collect all downloaded results."""
        all_results = []
        for query in queries:
            self._query_search_box(query)
            df = self._download_search_results(wait=wait)
            all_results.append(df)
            time.sleep(pause)
        if all_results:
            return pd.concat(all_results, ignore_index=True)
        return pd.DataFrame()

    def get_results(
            self,
            headless: bool = True,
        ) -> pd.DataFrame:
        """Get TerpLife Labs COAs by performing various queries."""

        # Initialize.
        self._init_selenium(
            download_dir=self.pdf_dir,
            headless=headless,
        )
        self.driver.get(self.url)
        time.sleep(self.pause_time)

        # Example: generate a set of digit queries.
        # TODO: Allow reverse to be an option.
        queries = self._get_day_month_combinations()
        queries += [''.join(map(str, x)) for x in itertools.product(range(10), repeat=2)]
        long_digits = ['81', '61', '51', '41', '40', '30', '20']
        queries += self._add_digits(long_digits)
        # queries.reverse()
        random.shuffle(queries)
        self.logger.info(f'Performing {len(queries)} queries (digit-based).')
        # FIXME: This is crashing!
        # ElementNotInteractableException 
        digit_results = self._perform_queries(queries, wait=60)

        # Example: Query by alphabetic combinations.
        specific_letters = list(string.ascii_lowercase)
        letter_queries = [a + b for a in specific_letters for b in string.ascii_lowercase]
        letter_queries.reverse()
        self.logger.info(f'Performing {len(letter_queries)} queries (alphabetic-based).')
        letter_results = self._perform_queries(letter_queries, wait=60)

        # Close the driver.
        self._quit_driver()

        # Combine all results.
        all_results = pd.concat([digit_results, letter_results], ignore_index=True)
        all_results.drop_duplicates(subset=['file_name'], inplace=True)
        self._save_results(all_results, prefix='terplife-labs-coas')
        self.logger.info('✓ Collected results for TerpLife Labs (FL).')
        return all_results


# === Test ===
# [✓] Tested: 2024-12-10 by Keegan Skeate <keegan@cannlytics>
if __name__ == '__main__':

    # Get TerpLife Labs results.
    collector = TerpLifeLabsCollector(
        data_dir='D:/data/florida/results',
        pdf_dir='D:/data/florida/results/pdfs/terplife',
        cache_path='D://data/.cache/results-fl-terplife.jsonl',
        log_name='get_results_fl_terplife',
    )
    results = collector.get_results(headless=False)
