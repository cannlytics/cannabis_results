"""
Get Results | California | Glass House Farms
Copyright (c) 2023-2024 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
    Candace O'Sullivan-Sutherland <https://github.com/candy-o>
Created: 5/25/2023
Updated: 12/10/2024
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:

    Archive Glass House Farms cannabis lab result data.

Data Sources:
    
    - [Glass House Farms Strains](https://glasshousefarms.org/strains/)

"""
# Standard imports:
import os
import time

# External imports:
import pandas as pd
import requests
from bs4 import BeautifulSoup

# Internal imports:
from cannlytics.data.collectors import COACollector

# Glass House Farms constants.
GLASS_HOUSE_FARMS = {
    'business_dba_name': 'Glass House Farms',
    'business_website': 'https://glasshousefarms.org',
    'business_image_url': 'https://glassfarms.wpenginepowered.com/wp-content/uploads/2021/10/new-ghf-menu.svg',
    'producer_license_number': 'CCL18-0000512',
    'producer_latitude': 34.404930,
    'producer_longitude': -119.518250,
    'producer_street_address': '5601 Casitas Pass Rd, Carpinteria, CA 93013',
    'producer_city': 'Carpinteria',
    'producer_county': 'Santa Barbara',
    'producer_state': 'CA',
}

# Strain type constants.
STRAIN_TYPES = {
    'sativa': {'sativa_percentage': 1.0, 'indica_percentage': 0.0},
    'sativaDominant': {'sativa_percentage': 0.75, 'indica_percentage': 0.25},
    'hybrid': {'sativa_percentage': 0.5, 'indica_percentage': 0.5},
    'indica': {'sativa_percentage': 0.0, 'indica_percentage': 1.0},
    'indicaDominant': {'sativa_percentage': 0.25, 'indica_percentage': 0.75},
    'cbd': {'sativa_percentage': 0.0, 'indica_percentage': 0.0},
    'cbdt': {'sativa_percentage': 0.0, 'indica_percentage': 0.0},
}


class GlassHouseFarmsCollector(COACollector):
    """Collector for Glass House Farms lab results."""

    BASE_URL = 'https://glasshousefarms.org/strains/'

    def get_strain_data(self) -> pd.DataFrame:
        """Get strain data from the Glass House Farms strains page."""
        response = requests.get(self.BASE_URL)
        soup = BeautifulSoup(response.content, 'html.parser')
        strains = soup.find_all(class_='item')
        observations = []
        for strain in strains:
            obs = {}
            # Extract image URL.
            img_tag = strain.find('img')
            obs['image_url'] = img_tag['src'] if img_tag else None

            # Extract strain type.
            strain_type = strain.find('h5').text if strain.find('h5') else None
            obs['strain_type'] = strain_type

            # Extract strain name.
            strain_name = strain.find('h4').text if strain.find('h4') else None
            if strain_name and strain_type:
                strain_name = strain_name.replace('\n', '').replace(strain_type, '').strip()
            obs['strain_name'] = strain_name

            # Extract strain URL.
            exp_link = strain.find('a', class_='exp')
            if exp_link:
                obs['strain_url'] = exp_link.get('href', '')
                obs['strain_id'] = obs['strain_url'].rstrip('/').split('/')[-1]
            else:
                obs['strain_url'] = None
                obs['strain_id'] = None

            # Determine indica/sativa percentages.
            wave = strain.find('div', class_='wave')
            if wave:
                wave_class = wave.get('class', [])
                wave_class = [cls for cls in wave_class if cls != 'wave']
                for cls in wave_class:
                    if cls in STRAIN_TYPES:
                        obs['indica_percentage'] = STRAIN_TYPES[cls]['indica_percentage']
                        obs['sativa_percentage'] = STRAIN_TYPES[cls]['sativa_percentage']
                        break
            else:
                obs['indica_percentage'] = None
                obs['sativa_percentage'] = None

            # Record the observation.
            observations.append(obs)

        # Return the results.
        df = pd.DataFrame(observations)
        self.logger.info(f'Found {len(df)} strains from Glass House Farms.')
        return df

    def get_strain_coas(self, strains: pd.DataFrame) -> pd.DataFrame:
        """Get the COA PDF links for each strain and related lineage."""
        lab_results = []
        license_number = GLASS_HOUSE_FARMS['producer_license_number']
        license_pdf_dir = os.path.join(self.pdf_dir, license_number)
        os.makedirs(license_pdf_dir, exist_ok=True)
        for _, obs in strains.iterrows():

            # Skip if no strain URL.
            strain_url = obs.get('strain_url')
            if not strain_url:
                continue

            # Get the strain page.
            time.sleep(self.pause_time)
            response = requests.get(strain_url)
            soup = BeautifulSoup(response.content, 'html.parser')

            # Get lineage
            try:
                content = soup.find('div', class_='content')
                divs = content.find_all('div', class_='et_pb_column')
                lineage = divs[2].text.split('Lineage')[1].replace('\n', '').strip()
                obs['lineage'] = lineage.split(' x ')
            except Exception:
                obs['lineage'] = []
                self.logger.debug(f"No lineage found for {obs['strain_name']}")

            # Find all PDF links
            pdf_links = []
            for link in soup.find_all('a'):
                href = link.get('href')
                if href and href.endswith('.pdf'):
                    pdf_links.append(href)

            # Store the metadata for each COA
            for link in pdf_links:
                lab_result_id = link.split('/')[-1].split('.')[0]
                result = {
                    'coa_url': link,
                    'lab_result_id': lab_result_id
                }
                record = {**GLASS_HOUSE_FARMS, **obs.to_dict(), **result}
                lab_results.append(record)

                # Download the COA PDF if not cached
                outfile = os.path.join(license_pdf_dir, f'{lab_result_id}.pdf')
                if not os.path.exists(outfile):
                    time.sleep(1)
                    r = requests.get(link)
                    if r.status_code == 200:
                        with open(outfile, 'wb') as pdf:
                            pdf.write(r.content)
                        self.logger.info(f"Downloaded: {outfile}")
                    else:
                        self.logger.warning(f"Failed to download {link}: HTTP {r.status_code}")

        # Return the results.
        df = pd.DataFrame(lab_results)
        self.logger.info(f"Found {len(df)} lab results from Glass House Farms.")
        return df

    def get_results(self) -> pd.DataFrame:
        """Get Glass House Farms lab results and strains."""

        # Get strain data.
        strains = self.get_strain_data()
        self._save_results(strains, prefix='ca-glass-house-strains')

        # Get COAs for each strain.
        results = self.get_strain_coas(strains)
        self._save_results(results, prefix='ca-glass-house-coas')

        # Return the results.
        self.logger.info('✓ Collected results for Glass House (CA).')
        return results


# === Test ===
# [✓] Tested: 2024-12-09 by Keegan Skeate <keegan@cannlytics>
if __name__ == '__main__':

    # Get Glass House Farms results.
    collector = GlassHouseFarmsCollector(
        data_dir='D:/data/california/results',
        pdf_dir='D:/data/california/results/pdfs/glass-house-farms',
        cache_path='D://data/.cache/results-ca-glass-house.jsonl',
        log_name='get_results_ca_glass_house',
    )
    results = collector.get_results()
