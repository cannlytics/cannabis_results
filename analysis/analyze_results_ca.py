"""
Analyze Results | California
Copyright (c) 2023-2024 Cannlytics

Authors: Keegan Skeate <https://github.com/keeganskeate>
Created: 12/10/2023
Updated: 8/15/2024
License: MIT License <https://github.com/cannlytics/cannabis-data-science/blob/main/LICENSE>
"""
# Standard imports:
from datetime import datetime
import json
import os
from typing import List, Optional
import warnings

# External imports:
from cannlytics.data.cache import Bogart
from cannlytics.data.coas import standardize_results
from cannlytics.data.coas.parsing import (
    find_unique_analytes,
    get_coa_files,
    parse_coa_pdfs,
)
from cannlytics.firebase import initialize_firebase
from cannlytics.compounds import cannabinoids, terpenes
from dotenv import dotenv_values
import pandas as pd

# Ignore all UserWarnings
warnings.filterwarnings("ignore", category=UserWarning)

# Internal imports:
# from analyze_results import calc_results_stats, calc_aggregate_results_stats


def analyze_results_ca(
    cache_path: str,
    pdf_dir: str,
    reverse: bool = False,
) -> pd.DataFrame:
    """Analyze California lab results.
    Args:
        cache_path (str): The path to the cache file.
        pdf_dir (str): The directory where the PDFs are stored.
        output_dir (str): The directory where the datasets are saved.
        reverse (bool): Whether to reverse the order of the results.
    Returns:
        pd.DataFrame: The analyzed results.
    """
    pass


def parse_results_ca(
    cache_path: str,
    pdf_dir: str,
    reverse: bool = False,
) -> pd.DataFrame:
    """Parse all California lab results.
    Args:
        cache_path (str): The path to the cache file.
        pdf_dir (str): The directory where the PDFs are stored.
        output_dir (str): The directory where the datasets are saved.
        reverse (bool): Whether to reverse the order of the results.
    Returns:
        pd.DataFrame: The analyzed results.
    """
    # Initialize cache.
    cache = Bogart(cache_path)

    # TODO: Remove duplicates in the PDF dir.

    # Get all of the PDFs.
    pdfs = get_coa_files(pdf_dir)

    # Sort the PDFs by modified date
    pdfs.sort(key=os.path.getmtime)

    # Parse the PDFs.
    all_results = parse_coa_pdfs(pdfs, cache=cache, reverse=reverse)

    return all_results


# text-embedding-3-large or text-embedding-3-small


# TODO: Create chemical profile vector.


# TODO: Create product name vector.


# TODO: Create standard strain name vector.


# === Test ===
if __name__ == '__main__':

    # Define the state.
    state = 'ca'
    state_name = 'california'

    # Define where the data is stored.
    cache_dir = 'D://data/.cache'
    data_dir = f'D://data/cannabis_results/data/{state}'
    cache_path = os.path.join(cache_dir, f'results-{state}.jsonl')
    pdf_dir = f'D://data/{state_name}/results/pdfs'

    # Parse all of the results.
    parse_results_ca(
        cache_path=cache_path,
        pdf_dir=pdf_dir,
        reverse=True,
    )

    # Read the cache.
    results = Bogart(cache_path).to_df()
    errors = results[~results['error'].isna()]
    results = results[results['error'].isna()]
    print('Number of errors:', len(errors))
    print('Number of valid results:', len(results))

    # === DEV ===

    # def debug_parse_errors():

    # Identify all of the unique errors.
    # TODO: Fix the errors.
    unique_errors = errors['error'].unique()
    # print(errors['error'].value_counts())

    # Get example COAs for each unique error
    # error_counts = errors['error'].value_counts()
    # sorted_errors = error_counts.index.tolist()
    # pdfs_with_errors = []
    # for error in sorted_errors:
    #     error_pdfs = errors[errors['error'] == error]['coa_pdf'].to_list()
    #     example_coas.append({'error': error, 'example_coa_pdf': example_coa_pdf})
    
    # example_coas = pd.DataFrame(example_coas)
    # print("Example COAs for each unique error:")
    # print(example_coas)

    # TODO: Figure out why there are duplicates.

    # Group by `coa_pdf` to find duplicates
    duplicate_groups = results[results.duplicated(subset=['coa_pdf'], keep=False)]
    grouped = duplicate_groups.groupby('coa_pdf')
    for coa_pdf, group in grouped:
        print(f'\nCOA PDF: {coa_pdf}')
        unique_hashes = group['sample_hash'].unique()
        if len(unique_hashes) > 1:
            print(f'- Warning: Different sample_hashes found!')
        else:
            print(f'- All records have the same sample_hash.')

    # DEV: Identify the same COA parsed multiple ways.
    multiple_coas = results['coa_pdf'].value_counts()
    multiple_coas = multiple_coas[multiple_coas > 1]
    print('Number of samples with Multiple COAs:', len(multiple_coas))

    # Merge SC Labs results, removing duplicates, unfinished results,
    # and removes Colorado results.
    extra_dir = r'D:\data\california\results\datasets\sclabs'
    datafiles = [os.path.join(extra_dir, x) for x in os.listdir(extra_dir) if 'urls' not in x and 'latest' not in x]
    sclabs = pd.concat([pd.read_excel(x) for x in datafiles])
    sclabs = sclabs.drop_duplicates(subset=['sample_hash'])
    sclabs = sclabs.loc[sclabs['results'] != '[]']
    sclabs = sclabs.loc[(sclabs['lab_state'] != 'CO')]
    print('Number of SC Labs results:', len(sclabs))

    # Merge the results.
    results = pd.concat([results, sclabs])

    # Drop duplicates.
    results = results.drop_duplicates(subset=['sample_hash'])
    print('Number of unique results:', len(results))

    # Read constants for processing.
    # FIXME: This requires the script be run from this directory.
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
    state = 'CA'
    results['lab_state'] = results['lab_state'].fillna(state)
    results['producer_state'] = results['producer_state'].fillna(state)

    # Standardize time.
    results['date'] = pd.to_datetime(results['date_tested'], format='mixed', errors='coerce')
    results['date'] = results['date'].apply(lambda x: pd.Timestamp(x).tz_localize(None) if pd.notnull(x) else x)
    results = results.sort_values('date', na_position='last')

    # Save the results.
    outfile = 'D://data/cannabis_results/data/ca/ca-results-latest.xlsx'
    outfile_csv = 'D://data/cannabis_results/data/ca/ca-results-latest.csv'
    results.to_excel(outfile, index=False)
    results.to_csv(outfile_csv, index=False)
    print('Saved %i results for %s to Excel:' % (len(results), state), outfile)
    print('Saved %i results for %s to CSV:' % (len(results), state), outfile_csv)

    # Print out features.
    features = {x: 'string' for x in results.columns}
    print('Number of features:', len(features))
    print('Features:', features)


#-----------------------------------------------------------------------
# Calculate statistics.
#-----------------------------------------------------------------------

    # # Calculate results statistics.
    # results = calc_results_stats(
    #     results,
    #     cannabinoid_keys=cannabinoid_keys,
    #     terpene_keys=terpene_keys,
    # )

    # # Calculate aggregate statistics.
    # stats = calc_aggregate_results_stats(
    #     results,
    #     cannabinoid_keys=cannabinoid_keys,
    #     terpene_keys=terpene_keys,
    # )


#-----------------------------------------------------------------------
# Upload COA PDFs to Google Cloud Storage and data to Firestore.
#-----------------------------------------------------------------------

# FIXME: Refactor into re-usable functions.

# # Match COA PDFs with the results.
# pdf_dir = 'D://data/florida/results/pdfs'
# coa_pdfs = {}
# for index, result in all_results.iterrows():

#     # Get the name of the PDF.
#     identifier = result['coa_pdf']
#     if identifier == 'download.pdf':
#         lab_results_url = result['lab_results_url']
#         identifier = lab_results_url.split('=')[-1].split('?')[0]
    
#     # Find the matching PDF.
#     for root, _, files in os.walk(pdf_dir):
#         for filename in files:
#             if identifier in filename:
#                 pdf_path = os.path.join(root, filename)
#                 coa_pdfs[result['sample_hash']] = pdf_path
#                 break

# # Initialize Firebase.
# config = dotenv_values('.env')
# db = initialize_firebase()
# bucket_name = config['FIREBASE_STORAGE_BUCKET']
# firebase_api_key = config['FIREBASE_API_KEY']

# # Upload datafiles to Google Cloud Storage.
# # Checks if the file has been uploaded according to the local cache.
# # FIXME:
# for datafile in datafiles:
#     filename = os.path.split(datafile)[-1]
#     if filename not in cache.get('datafiles', []):
#         file_ref = f'data/results/florida/datasets/{filename}'
#         # upload_file(
#         #     destination_blob_name=file_ref,
#         #     source_file_name=datafile,
#         #     bucket_name=bucket_name,
#         # )
#         print('Uploaded:', file_ref)
#         # FIXME:
#         # cache.setdefault('datafiles', []).append(filename)

# # Upload PDFs to Google Cloud Storage.
# # Checks if the file has been uploaded according to the local cache.
# print('Number of unique COA PDFs:', len(coa_pdfs))
# for sample_hash, pdf_path in coa_pdfs.items():
#     print('Uploading:', pdf_path)
#     pdf_hash = cache.hash_file(pdf_path)

#     if pdf_hash not in cache.get('pdfs', []):

#         # Upload the file.
#         file_ref = f'data/results/florida/pdfs/{pdf_hash}.pdf'
#         # upload_file(
#         #     destination_blob_name=file_ref,
#         #     source_file_name=pdf_path,
#         #     bucket_name=bucket_name,
#         # )

#         # # Get download URL and create a short URL.
#         # download_url, short_url = None, None
#         # try:
#         #     download_url = get_file_url(file_ref, bucket_name=bucket_name)
#         #     short_url = create_short_url(
#         #         api_key=firebase_api_key,
#         #         long_url=download_url,
#         #         project_name=db.project
#         #     )
#         # except Exception as e:
#         #     print('Failed to get download URL:', e)

#         # # Keep track of the file reference and download URLs.
#         # all_results.loc[all_results['sample_hash'] == sample_hash, 'file_ref'] = file_ref
#         # all_results.loc[all_results['sample_hash'] == sample_hash, 'download_url'] = download_url
#         # all_results.loc[all_results['sample_hash'] == sample_hash, 'short_url'] = short_url

#         # Cache the PDF.
#         # FIXME:
#         # cache.setdefault('pdfs', []).append(pdf_hash)

# # Upload the raw data to Firestore.
# # Checks if the data has been uploaded according to the local cache.
# refs, updates = [], []
# collection = 'results'
# for _, obs in all_results.iterrows():
#     doc_id = obs['sample_hash']
#     if doc_id not in cache.get('results', []):
#         refs.append(f'{collection}/{doc_id}')
#         updates.append(obs.to_dict())
#         # FIXME:
#         # cache.setdefault('results', []).append(doc_id)
# # if refs:
# #     update_documents(refs, updates, database=db)
# #     print('Uploaded %i results to Firestore.' % len(refs))

# # TODO: Save the statistics to Firestore.

# # Save the updated cache
# # with open(cache_file, 'w') as f:
# #     json.dump(cache, f)
# #     print('Saved cache:', cache_file)
