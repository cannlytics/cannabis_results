"""
Identify Strain Names
Copyright (c) 2024 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 10/13/2024
Updated: 12/15/2024
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>
"""
# Standard imports:
from datetime import datetime
import os

# External imports:
from dotenv import dotenv_values
from openai import OpenAI
import pandas as pd

# Internal imports:
from cannlytics.data import create_hash
from cannlytics.data.cache import Bogart
from cannlytics.data.strains.strain_identifier import StrainIdentifier
from cannlytics.firebase import (
    initialize_firebase,
    update_document,
)


def create_label_name(row) -> str:
    """Create a label name from a product name and strain name."""
    product_name = str(row['product_name']).strip()
    strain_name = str(row['strain_name']).strip()
    if not product_name:
        return strain_name
    if not strain_name or (strain_name.lower() in product_name.lower()):
        return product_name
    return f"{product_name} {strain_name}"


# === Tests ===
if __name__ == '__main__':

    # Define states with COAs.
    STATES_WITH_COAS = {
        'ca': {'name': 'California'},
        'fl': {'name': 'Florida'},
        'az': {'name': 'Arizona'},
        'ny': {'name': 'New York'},
    }

    # Define the cache directory.
    cache_dir = 'D://data/.cache'
    batch_dir = 'D://data//.cache/standard-strain-names'

    # Initialize OpenAI.
    config = dotenv_values('.env')
    openai_api_key = config['OPENAI_API_KEY']
    os.environ['OPENAI_API_KEY'] = openai_api_key
    client = OpenAI()

    # Read cached strain names.
    strain_names_cache = Bogart(os.path.join(cache_dir, 'standard-strain-names.jsonl'))
    df = pd.DataFrame.from_records(list(strain_names_cache.cache.values()))
    print(f"Current number of labels parsed: {len(df)}")

    # Read results from all states and combine.
    all_results = []
    for state in STATES_WITH_COAS.keys():
        results_cache = Bogart(os.path.join(cache_dir, f'results-{state}.jsonl'))
        all_results.append(results_cache.to_df())
    all_results = pd.concat(all_results)

    # Combine product_name and strain_name into a single label_name.
    fields = ['product_name', 'strain_name']
    for field in fields:
        all_results[field] = all_results[field].fillna('')
    all_results['label_name'] = all_results[fields].apply(create_label_name, axis=1)
    all_results['label_hash'] = all_results['label_name'].apply(
        lambda x: create_hash(x.strip().lower())
    )
    all_results.drop_duplicates(subset='label_hash', inplace=True)
    print(f"Total number of unique labels: {len(all_results)}")

    # Restrict to only results that are not cached.
    cached_labels = list(strain_names_cache.cache.keys())
    sample = all_results[~all_results['label_hash'].isin(cached_labels)]
    print(f"Number of labels to parse: {len(sample)}")

    # Initialize StrainIdentifier from gen.py
    identifier = StrainIdentifier(
        client=client,
        model='gpt-4o-mini'
    )

    # Parse a batch of strain names.
    strain_dict = identifier.identify_strains_batch(
        data=sample,
        text_column='label_name',
        id_column='label_hash',
        batch_dir=batch_dir,
        verbose=True,
        pause=60 * 5,
    )
    print(f"Parsed strains: {len(strain_dict)}")

    # Initialize Firebase and save parsed strain names.
    db = initialize_firebase()
    collection = 'public/ai/standard_strain_names'
    for label_hash, strain_names in strain_dict.items():
        if strain_names_cache.get(label_hash):
            print(f"Strain names already cached: {strain_names}")
            continue
        doc = {
            'strain_names': strain_names,
            'number_of_strains': len(strain_names),
            'updated_at': datetime.now().isoformat()
        }
        ref = f'{collection}/{label_hash}'
        update_document(ref, doc, database=db)
        print(f"Saved strain names to Firestore: {strain_names}")
        strain_names_cache.set(label_hash, strain_names)

    # Count the number of labels parsed.
    df = pd.DataFrame.from_records(list(strain_names_cache.cache.values()))
    print(f"Total number of labels parsed: {len(df)}")
