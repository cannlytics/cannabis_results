"""
Get All Results | Cannabis Results
Copyright (c) 2024 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 7/10/2024
Updated: 10/16/2024
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:

    Aggregate cannabis test data from states with permitted cannabis use:

    - Alaska
    ✓ California
    ✓ Colorado (SC Labs)
    - Colorado (Public records | optional)
    ✓ Connecticut
    ✓ Florida
    ✓ Hawaii
    ✓ Maryland
    ✓ Massachusetts
    ✓ Michigan
    - Mississippi
    - Missouri
    ✓ Nevada
    ✓ New York
    - Ohio
    ✓ Oregon
    ✓ Rhode Island
    ✓ Utah
    ✓ Washington

"""
# Standard imports:
from datetime import datetime
import json
import os

# External imports:
from cannlytics.compounds import cannabinoids, terpenes
from cannlytics.data import create_hash
from cannlytics.data.cache import Bogart
from cannlytics.data.coas import standardize_results
from cannlytics.data.coas.parsing import find_unique_analytes
# from cannlytics.models import LabResult, TestResult
from cannlytics.firebase import (
    initialize_firebase,
    get_file_url,
    update_document,
    upload_file,
)
from cannlytics.utils import kebab_case
from dotenv import dotenv_values
from google.cloud.firestore_v1.vector import Vector
from openai import OpenAI
import pandas as pd
import pdfplumber

# DEV: Ensure the script is in the right directory.
os.chdir('D://data/cannabis_results/upload')

# Internal imports:
from process_results import (
    analyze_coa_duplicates,
    calc_aggregate_results_stats,
    calc_analyte_stats,
    calc_diversity_index,
    calc_image_stats,
    find_coa_pdfs,
    format_coa_title,
    format_data_link,
    get_firestore_embedding,
    get_results_embedding,
    process_and_upload_images,
    standardize_methods,
    standardize_product_type,
    standardize_statuses,
    standardize_traceability_ids,
    standardize_urls,
    text_to_color_ai,
)
from identify_strains import create_label_name, identify_strains


def upload_results_dataset(
        results: pd.DataFrame,
        state: str,
    ):

    print('Processing %i results...' % (len(results)))

    # Initialize the cache.
    results_cache = Bogart(os.path.join(cache_dir, f'uploaded-results-{state}.jsonl'))
    image_cache = Bogart(os.path.join(cache_dir, f'images-results-{state}.jsonl'))

    # FIXME: Assign a unique ID.
    # Function to concatenate non-null values into a unique ID
    def generate_unique_id(row):
        return '-'.join(str(val) for val in [row['sample_id'], row['label'], row['lab_test_detail_id'], row['package_id'], row['sample_hash']] if pd.notnull(val))

    # Apply the function to each row to create the 'unique_id' field
    results['id'] = results.apply(generate_unique_id, axis=1)

    # Optionally, check for duplicates in the new unique_id column
    duplicates = results['unique_id'].duplicated().sum()
    print(f"Number of duplicate IDs: {duplicates}")

    # # Drop duplicates.
    # results = results.drop_duplicates(subset=['sample_hash'])
    # print('Number of unique results:', len(results))

    # FIXME: Process the results.
    # - remove duplicates
    # - remove any non-standard columns

    # === Processing ===

    # Read constants for processing.
    with open('processing.json', 'r') as f:
        processing = json.load(f)
        nuisance_analytes = processing['nuisance_analytes']
        nuisance_columns = processing['nuisance_columns']

    # Drop duplicates.
    results = results.drop_duplicates(subset=['sample_hash'])
    print('Number of unique results:', len(results))

    # Drop all non-standard columns.
    drop_columns = [col for col in nuisance_columns if col in results.columns]
    drop_columns += [col for col in results.columns if '_per_' in col.lower()]
    results.drop(columns=drop_columns, inplace=True)

    # Standardize the methods.
    results = standardize_methods(results)

    # Standardize the `statuses`.
    results = standardize_statuses(results)

    # Standardize traceability IDs.
    results = standardize_traceability_ids(results)

    # Get `coa_url` from `coa_urls` and `image_url` from `images`.
    results['coa_url'] = results['coa_urls'].apply(standardize_urls)
    results['image_url'] = results['images'].apply(standardize_urls)
    results.drop(columns=['coa_urls', 'images'], inplace=True)

    # Download and upload images for archival and accessibility.
    # Note: First downloads images, then uploads images to Firebase storage.
    # Gets `image_ref` and `image_download_url`, and caches uploaded images.
    results = process_and_upload_images(
        results,
        image_dir,
        bucket_name,
        folder='public/images/results',
        image_cache=image_cache,
    )

    # For results with images, calculate `colorfulness` and `purpleness`.
    results = calc_image_stats(
        results,
        image_dir=image_dir,
        image_cache=image_cache,
    )

    # Read the standard product types.
    with open('product-types.json', 'r') as f:
        product_types = json.load(f)

    # Assign standard product type.
    results['standard_product_type'] = results['product_type'].apply(
        standardize_product_type,
        product_types=product_types
    )

    # Read product subtypes.
    with open('product-subtypes.json', 'r') as f:
        product_subtypes = json.load(f)

    # Assign standard product type.
    results['product_subtype'] = results['product_type'].apply(
        standardize_product_type,
        product_types=product_subtypes
    )

    # DEV: Look at all unique analytes to see if any standardizations are needed.
    analytes = find_unique_analytes(results)
    analytes = list(set(analytes) - set(nuisance_analytes))
    analytes = sorted(list(analytes))
    analytes = [x for x in analytes if '_per_' not in x]
    print('Unique analytes:', analytes)

    # Add columns for the standard analytes.
    print('Standardizing analytes (this takes a while)...')
    standard_analytes = list(cannabinoids.keys()) + list(terpenes.keys())
    results = standardize_results(results, standard_analytes)

    # Calculate analyte totals (if missing) and ratios.
    # Adds totals: `total_cannabinoids`, `total_terpenes`, `total_thc`, `total_cbd`
    # Adds ratios: `cbd_to_thc_ratio`, `beta_pinene_d_limonene_ratio`
    results = calc_analyte_stats(results, cannabinoids, terpenes)

    # Calculate diversity index for cannabinoids and terpenes.
    cannabinoid_keys = list(cannabinoids.keys())
    terpene_keys = list(terpenes.keys())
    results['chemical_diversity'] = calc_diversity_index(results, cannabinoid_keys + terpene_keys)
    results['cannabinoid_diversity'] = calc_diversity_index(results, cannabinoid_keys)
    results['terpene_diversity'] = calc_diversity_index(results, terpene_keys)

    # Standardize time.
    results['date'] = pd.to_datetime(results['date_tested'], format='mixed')
    results['week'] = results['date'].dt.to_period('W').astype(str)
    results['month'] = results['date'].dt.to_period('M').astype(str)
    results['year'] = results['date'].dt.to_period('Y').astype(str)
    results = results.sort_values('date')

    # Add metadata.
    timestamp = pd.Timestamp.now().isoformat()
    results['id'] = results['sample_id']
    results['updated_at'] = timestamp
    results['created_at'] = timestamp
    results['created_by'] = 'cannlytics'
    results['data_type'] = 'coas'
    results['link'] = results['sample_id'].apply(format_data_link, data_type='coas')
    results['title'] = results.apply(format_coa_title, axis=1)
    print('Added metadata.')

    # === Augment Strain Names ===

    # Get any standard strain names from the `label_name`.
    strain_names_cache = Bogart(os.path.join(cache_dir, 'standard-strain-names.jsonl'))

    # Begin assigning `standard_strain_name`.
    fields = ['product_name', 'strain_name']
    for field in fields:
        results[field] = results[field].fillna('')
    results['label_name'] = results[fields].apply(create_label_name, axis=1)
    results['label_hash'] = results['label_name'].apply(lambda x: create_hash(str(x).strip().lower()))
    results['standard_strain_name'] = results['label_hash'].map(strain_names_cache.cache)

    # Use `identify_strains` to identify any non-identified strain names.
    # Note: Cache and save these newly parsed `strain_names` to Firestore.
    collection = 'public/ai/standard_strain_names'
    missing_strain_names = results[results['standard_strain_name'].isna()]
    print('Number of labels missing strain names:', len(missing_strain_names))
    for index, row in missing_strain_names.iterrows():
        text, label_hash = row['label_name'], row['label_hash']
        parsed, usage = identify_strains(text, client)
        strain_names = parsed['strain_names']
        doc = {
            'strain_names': strain_names,
            'number_of_strains': len(strain_names),
            'updated_at': datetime.now().isoformat()
        }
        ref = f'{collection}/{label_hash}'
        update_document(ref, doc, database=db)
        print(f"Saved strain names to Firestore: {strain_names}")
        strain_names_cache.set(label_hash, strain_names)

    # Assign `standard_strain_name` again (after identifying any missing strain names).
    results['strain_names'] = results['label_hash'].map(strain_names_cache.cache)
    results['standard_strain_name'] = results['strain_names'].apply(
        lambda x: ' × '.join(x) if isinstance(x, list) else x
    )
    print('Standardized results.')


    # === Vector Embeddings ===

    # Create `results_embedding`.
    print('Creating results embeddings...')
    results['results_embedding'] = results.apply(
        lambda row: get_results_embedding(row.to_dict(), standard_analytes),
        axis=1,
    )

    # Create product name embeddings.
    print('Creating product name embeddings...')
    results['product_name_embedding'] = results['product_name'].apply(
        lambda x: get_firestore_embedding(x, client=client, db=db) if pd.notna(x) and x != '' else None
    )

    # Create strain name embeddings with the standard strain name.
    print('Creating strain name embeddings...')
    results['strain_name_embedding'] = results['standard_strain_name'].apply(
        lambda x: get_firestore_embedding(x, client=client, db=db) if pd.notna(x) and x != '' else None
    )

    # === Tag the data ===

    # FIXME: Add `tags` to the data.


    # === Upload the datafile ===

    # TODO: Upload the datafile.


    # === Upload the data to Firestore ===

    # TODO: Upload the results to Firestore.


    # TODO: Implement caching by state (to handle large file sizes).



# === Test ===
# [✓] Tested: 2024-10-16 by Keegan Skeate <keegan@cannlytics>
if __name__ == '__main__':

    # === Setup ===

    # Read environment variables.
    config = dotenv_values('../.env')

    # Initialize OpenAI.
    openai_api_key = config['OPENAI_API_KEY']
    os.environ['OPENAI_API_KEY'] = openai_api_key
    client = OpenAI()

    # Initialize Firebase.
    credentials = config['GOOGLE_APPLICATION_CREDENTIALS']
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = credentials
    bucket_name = config['FIREBASE_STORAGE_BUCKET']
    db = initialize_firebase()

    # Define where the data lives.
    cache_dir = 'D://data/.cache'
    data_dir = 'D://data/cannabis_results/data'

    # Define where curated data will be saved.
    stats_dir = 'D://data/results'
    if not os.path.exists(stats_dir):
        os.makedirs(stats_dir)

    # Create a directory for the images.
    image_dir = f'D://data/results/images'
    if not os.path.exists(image_dir):
        os.makedirs(image_dir)

    # === Read the data ===

    # Read all of the latest results datafiles.
    states, datafiles = [], []
    for root, _, files in os.walk(data_dir):
        for file in files:
            if 'all' in file:
                continue
            if file.endswith('latest.csv') or file.endswith('latest.xlsx'):
                state = root.split('\\')[-1]
                if state in states:
                    continue
                file_path = os.path.join(root, file)
                datafiles.append(file_path)
                states.append(state)

    # Iterate over each datafile to read all of the tests.
    for state, datafile in zip(states[-1:], datafiles[-1:]):

        # Read the datafile.
        print(f'Uploading {state} results:', datafile)
        if datafile.endswith('.csv'):
            df = pd.read_csv(datafile)
        else:
            df = pd.read_excel(datafile)

        # TODO: Upload the results to Firestore.
