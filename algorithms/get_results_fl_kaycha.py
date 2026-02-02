"""
Get Results | Florida | Kaycha Labs
Copyright (c) 2023-2024 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 5/18/2023
Updated: 12/10/2024
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:

    Collect cannabis lab results data from Kaycha Labs in Florida.

Data Source:

    - [Kaycha Labs](https://yourcoa.com)

"""
# Standard imports:
from datetime import datetime
import os
import time
from typing import Optional, List, Dict

# External imports:
import pandas as pd
import requests
from bs4 import BeautifulSoup

# Internal imports:
from cannlytics.data.collectors import COACollector
from cannlytics.data.web import download_file_with_selenium
from cannlytics.utils.utils import remove_duplicate_files


class KaychaLabsCollector(COACollector):
    """Collector for Kaycha Labs COAs."""

    BASE_URL = 'https://yourcoa.com'
    MIN_FILE_SIZE = 21 * 1024
    FLORIDA_LICENSES = {
        'MMTC-2015-0002': {'business_dba_name': 'Ayr Cannabis Dispensary', 'slug': 'Liberty+Health+Sciences%2C+FL'},
        'MMTC-2015-0001': {'business_dba_name': 'Curaleaf', 'slug': 'CURALEAF+FLORIDA+LLC'},
        'MMTC-2015-0003': {'business_dba_name': 'Fluent ', 'slug': 'Fluent'},
        'MMTC-2015-0004': {'business_dba_name': 'Surterra Wellness', 'slug': 'Surterra+Wellness'},
        'MMTC-2015-0005': {'business_dba_name': 'Trulieve', 'slug': 'Trulieve'},
        'MMTC-2016-0006': {'business_dba_name': 'Planet 13 Florida, Inc.', 'slug': ''},
        'MMTC-2016-0007': {'business_dba_name': 'GrowHealthy', 'slug': 'GrowHealthy'},
        'MMTC-2017-0008': {'business_dba_name': 'Sunnyside*', 'slug': 'Sunnyside'},
        'MMTC-2017-0009': {'business_dba_name': 'VidaCann', 'slug': 'VidaCann'},
        'MMTC-2017-0010': {'business_dba_name': 'MüV', 'slug': 'Altmed+Florida'},
        'MMTC-2017-0011': {'business_dba_name': 'Cannabist', 'slug': 'Cannabist'},
        'MMTC-2017-0012': {'business_dba_name': 'Sunburn', 'slug': ''},
        'MMTC-2017-0013': {'business_dba_name': 'GTI (Rise Dispensaries)', 'slug': 'GTI'},
        'MMTC-2018-0014': {'business_dba_name': 'House of Platinum Cannabis', 'slug': ''},
        'MMTC-2019-0015': {'business_dba_name': 'Jungle Boys', 'slug': 'Jungle+Boys'},
        'MMTC-2019-0016': {'business_dba_name': 'Insa - Cannabis for Real Life', 'slug': 'Insa'},
        'MMTC-2019-0017': {'business_dba_name': 'Sanctuary Cannabis', 'slug': 'Sanctuary'},
        'MMTC-2019-0018': {'business_dba_name': 'Cookies Florida, Inc.', 'slug': ''},
        'MMTC-2019-0019': {'business_dba_name': 'Gold Leaf', 'slug': 'Gold+Leaf'},
        'MMTC-2019-0020': {'business_dba_name': 'The Flowery', 'slug': 'The+Flowery'},
        'MMTC-2019-0021': {'business_dba_name': 'Green Dragon', 'slug': 'Green+Dragon'},
        'MMTC-2019-0022': {'business_dba_name': 'Revolution Florida', 'slug': 'Revolution'}
    }

    def __init__(
            self,
            data_dir: str,
            pdf_dir: str,
            log_name: Optional[str] = 'get_results_fl_kaycha',
            cache_path: Optional[str] = None,
            pause_time: float = 3.33,
            producers: Optional[Dict[str, Dict[str, str]]] = None,
        ):
        """
        Initialize the Kaycha Labs collector.

        Args:
            data_dir (str): The directory where data should be saved.
            cache_path (str, optional): Path to a cache file.
            pause_time (float, optional): Time to pause between requests.
            producers (dict, optional): Mapping of producer_license_number to metadata and Kaycha Labs slug.
        """
        super().__init__(data_dir=data_dir, cache_path=cache_path, pause_time=pause_time, pdf_dir=pdf_dir, log_name=log_name)
        self.producers = producers or self.FLORIDA_LICENSES
        # Optional: Shuffle the list of licenses.
        self.producers = dict(sorted(self.producers.items(), key=lambda x: x[0]))

    def _get_producer_urls(self, slug: str, dba: str, producer_license_number: str, columns: List[str], pause: float) -> pd.DataFrame:
        """Get COA URLs for a given producer (by slug) from Kaycha Labs."""
        observations = []
        page = 0
        iterate = True
        while iterate:
            page += 1
            url = f'{self.BASE_URL}/company/company?t={slug}&page={page}'
            response = requests.get(url)
            if response.status_code != 200:
                self.logger.info(f'Request failed with status {response.status_code} for {dba}.')
                break

            # Get the download URLs.
            soup = BeautifulSoup(response.content, 'html.parser')
            divs = soup.find_all(class_='pdf_box')
            self.logger.info(f'Found {len(divs)} samples on page {page} for {dba}.')
            links = soup.find_all('a')
            links = [x['href'] for x in links if 'coa-download' in x['href']]
            links = list(set(links))
            links = [self.BASE_URL + x for x in links]

            # Get the details from the page.
            for n, div in enumerate(divs):
                observation = {}
                spans = div.find_all('span')[:len(columns)]
                values = [x.text for x in spans]
                for k, value in enumerate(values):
                    observation[columns[k]] = value
                if n < len(links):
                    observation['download_url'] = links[n]
                observation['business_dba_name'] = dba
                observation['producer_license_number'] = producer_license_number
                observations.append(observation)

            # Check if next button is disabled
            next_element = soup.find(class_='next')
            if not next_element or ('disabled' in next_element.get('class', [])):
                iterate = False
            time.sleep(pause)

        return pd.DataFrame(observations)

    def _download_pdfs(self, df: pd.DataFrame, license_pdf_dir: str, overwrite: bool = False):
        """Download PDF files for all COAs listed in a DataFrame."""
        for _, row in df.iterrows():
            time.sleep(self.pause_time)
            download_url = row['download_url']
            if not download_url.startswith('http'):
                download_url = self.BASE_URL + download_url
            sample_id = download_url.split('/')[-1].split('?')[0].split('&')[0]
            outfile = os.path.join(license_pdf_dir, f'{sample_id}.pdf')
            url_hash = self.cache.hash_url(download_url)

            # Check if the file is already downloaded.
            if (os.path.exists(outfile) or self.cache.get(url_hash)) and not overwrite:
                self.logger.info(f'Cached: {download_url}')
                self.cache.set(url_hash, {'type': 'download', 'url': download_url, 'file': outfile})
                continue

            # Download the file.
            self.cache.set(url_hash, {'type': 'download', 'url': download_url, 'file': outfile})
            coa_url = f'{self.BASE_URL}/coa/download?sample={sample_id}'
            response = requests.get(coa_url)
            if response.status_code == 200:
                if len(response.content) < self.MIN_FILE_SIZE:
                    # Retry with Selenium download
                    self.logger.info(f'File small, retry Selenium: {download_url}')
                    response = requests.get(download_url, allow_redirects=True)
                    if response.status_code == 200:
                        redirected_url = response.url
                        download_file_with_selenium(
                            redirected_url,
                            download_dir=license_pdf_dir,
                        )
                        self.logger.info(f'Downloaded with Selenium: {redirected_url}')
                        self.cache.set(url_hash, {'type': 'download', 'url': download_url, 'redirect_url': redirected_url})
                else:
                    with open(outfile, 'wb') as pdf:
                        pdf.write(response.content)
                    self.logger.info(f'Downloaded: {outfile}')
                    self.cache.set(url_hash, {'type': 'download', 'url': download_url, 'coa_url': coa_url, 'file': outfile})
            else:
                # Another fallback with Selenium
                self.logger.info(f'Retrying with Selenium: {coa_url}')
                response = requests.get(download_url, allow_redirects=True)
                if response.status_code == 200:
                    redirected_url = response.url
                    download_file_with_selenium(
                        redirected_url,
                        download_dir=license_pdf_dir,
                    )
                    self.logger.info(f'Downloaded with Selenium: {redirected_url}')
                    self.cache.set(url_hash, {'type': 'download', 'url': download_url, 'redirect_url': redirected_url})

    def get_results(self) -> pd.DataFrame:
        """Get Kaycha Labs COAs for configured producers."""
        all_coa_urls = []
        columns = ['lab_id', 'batch_number', 'product_name']

        # Iterate over each producer and fetch COA URLs
        # FIXME: Allow for reverse iterations.
        # for producer_license_number, meta in self.producers.items():
        for producer_license_number, meta in reversed(self.producers.items()):
            dba = meta.get('business_dba_name', 'Unknown Producer')
            slug = meta.get('slug', '')
            if not slug:
                self.logger.info(f'No slug for {dba}, skipping.')
                continue

            # Get the COA URLs for the producer.
            self.logger.info(f'Getting COAs for {dba} ({producer_license_number})')
            df = self._get_producer_urls(slug, dba, producer_license_number, columns, pause=self.pause_time)

            # Save the observed lab result URLs for each producer.
            date = datetime.now().isoformat()[:19].replace(':', '-')
            filename = f'lab-result-urls-{slug}-{date}.csv'
            outfile = os.path.join(self.datasets_dir, filename)
            df.to_csv(outfile, index=False)
            self.logger.info(f'Saved {len(df)} lab result URLs for {slug} to {outfile}')

            # Create a directory for COA PDFs for the producer
            license_pdf_dir = os.path.join(self.pdf_dir, producer_license_number)
            os.makedirs(license_pdf_dir, exist_ok=True)

            # Download the PDFs.
            self._download_pdfs(df, license_pdf_dir, overwrite=False)

            # Remove duplicates.
            remove_duplicate_files(license_pdf_dir, verbose=True)

            # Append the results to the list of all COA URLs.
            all_coa_urls.append(df)

        # Combine all COA URLs into a single DataFrame.
        if all_coa_urls:
            data = pd.concat(all_coa_urls)
        else:
            data = pd.DataFrame(columns=columns + ['download_url', 'business_dba_name', 'producer_license_number'])

        # Save all combined URLs
        date = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
        combined_file = self._save_results(data, prefix='kaycha-labs-coas')
        self.logger.info(f'Saved {len(data)} lab result URLs total.')
        self.logger.info('✓ Collected results for Kaycha Labs (FL).')
        return data


# === Test ===
# [✓] Tested: 2024-12-10 by Keegan Skeate <keegan@cannlytics>
if __name__ == '__main__':

    # Get Raw Garden results.
    collector = KaychaLabsCollector(
        data_dir='D:/data/florida/results',
        pdf_dir='D:/data/florida/results/pdfs/kaycha',
        cache_path='D://data/.cache/results-fl-kaycha.jsonl',
        log_name='get_results_fl_kaycha',
    )
    results = collector.get_results()
