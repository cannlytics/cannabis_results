"""
Analyze Results | Colorado
Copyright (c) 2024 Cannlytics

Authors: Keegan Skeate <https://github.com/keeganskeate>
Created: 8/14/2024
Updated: 8/15/2024
License: MIT License <https://github.com/cannlytics/cannabis-data-science/blob/main/LICENSE>
"""
# Standard imports:
import json
import os
import warnings

# External imports:
from cannlytics.data.coas import standardize_results
from cannlytics.data.coas.parsing import find_unique_analytes
import pandas as pd

# Ignore all UserWarnings
warnings.filterwarnings("ignore", category=UserWarning)


def analyze_results_co(
        data_dir: str,
    ) -> pd.DataFrame:
    """Analyze Colorado lab results."""

    # Merge SC Labs results, removing duplicates and unfinished results.
    datafiles = [os.path.join(data_dir, x) for x in os.listdir(data_dir) if 'urls' not in x and 'latest' not in x]
    results = pd.concat([pd.read_excel(x) for x in datafiles])
    results = results.drop_duplicates(subset=['sample_hash'])
    results = results.loc[results['results'] != '[]']
    results = results.loc[(results['lab_state'] == 'CO')]
    print('Number of CO SC Labs results:', len(results))

    # Read constants for processing.
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except:
        script_dir = os.getcwd()
    processing_config = os.path.join(script_dir, 'processing.json')
    with open(processing_config, 'r') as f:
        data = json.load(f)
        nuisance_analytes = data['nuisance_analytes']
        nuisance_columns = data['nuisance_columns']

    # Drop all non-standard columns.
    results.drop(columns=nuisance_columns, errors='ignore', inplace=True)

    # FIXME: Standardize analytes.
    # analytes = find_unique_analytes(results)
    # analytes = list(set(analytes) - set(nuisance_analytes))
    # analytes = sorted(list(analytes))
    # results = standardize_results(results, analytes)

    # Standardize state.
    state = 'CO'
    results['lab_state'] = results['lab_state'].fillna(state)
    # results['producer_state'] = results['producer_state'].fillna(state)

    # Standardize time.
    results['date'] = pd.to_datetime(results['date_tested'], format='mixed', errors='coerce')
    results['date'] = results['date'].apply(lambda x: pd.Timestamp(x).tz_localize(None) if pd.notnull(x) else x)
    results = results.sort_values('date', na_position='last')

    # Save the results.
    outfile = 'D://data/cannabis_results/data/co/co-results-latest.xlsx'
    outfile_csv = 'D://data/cannabis_results/data/co/co-results-latest.csv'
    results.to_excel(outfile, index=False)
    results.to_csv(outfile_csv, index=False)
    print('Saved %i results for %s to Excel:' % (len(results), state), outfile)
    print('Saved %i results for %s to CSV:' % (len(results), state), outfile_csv)

    # Print out features.
    features = {x: 'string' for x in results.columns}
    print('Number of features:', len(features))
    print('Features:', features)


# === Test ===
# [✓] Tested: 2024-08-15 by Keegan Skeate <keegan@cannlytics>
if __name__ == '__main__':

    # Get all of the results.
    analyze_results_co(data_dir=r'D:\data\california\results\datasets\sclabs')
