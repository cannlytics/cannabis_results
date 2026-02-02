"""
Create Embeddings in a Batch
Copyright (c) 2024 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 10/8/2024
Updated: 12/15/2024
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>
"""
# Standard imports:
import json
import os
from time import sleep

# External imports:
from dotenv import dotenv_values
from firebase_admin import initialize_app
from google.cloud import firestore
from google.cloud.firestore_v1 import aggregation
from google.cloud.firestore_v1.base_query import FieldFilter
from openai import OpenAI
import pandas as pd

# Internal imports:
from cannlytics.ai.embeddings import (
    create_batch_embeddings,
    run_batch_embeddings,
    upload_batch_embeddings,
)
from cannlytics.data import create_hash
from cannlytics.data.cache import Bogart
from cannlytics.firebase import initialize_firebase


def read_cached_results(
        cache_dir: str,
        source: str,
        field: str = '',
    ) -> pd.DataFrame:
    """Read cached lab results for a given state and optionally filter by a field."""

    # Read cached results for the given source.
    results_cache = Bogart(os.path.join(cache_dir, f'results-{source}.jsonl'))
    results = results_cache.to_df()

    # Filter out errors and sort by the results.
    results = results[results['error'].isna()]
    results = results.sort_values('coa_parsed_at', ascending=False)

    # Drop duplicates and null values for the specified field if given.
    if field:
        results = results.drop_duplicates(subset=[field], keep='first')
        results = results.dropna(subset=[field])

    # Return the filtered results.
    return results


class EmbeddingBatchManager:
    """A manager class for creating and uploading text embeddings."""

    def __init__(
            self,
            field: str,
            model: str = 'text-embedding-3-small',
            data_dir: str = 'D://data/.cache/embeddings',
        ):
        self.field = field
        self.model = model
        self.db = initialize_firebase()
        self.config = dotenv_values('.env')
        self.openai_api_key = self.config.get('OPENAI_API_KEY')
        os.environ['OPENAI_API_KEY'] = self.openai_api_key
        self.client = OpenAI()
        self.data_dir = data_dir
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)

    def create_and_run_batch(
            self,
            results: pd.DataFrame,
            source: str,
            pause: int = 60 * 5,
        ) -> str:
        """
        Create a batch of embeddings for a given source and field,
        run the batch job, and return the batch results file path.
        """
        # Define the batch file paths.
        batch_file = os.path.join(self.data_dir, f'{self.field.replace("_", "-")}-embeddings-{source}.jsonl')
        batch_results_file = os.path.join(self.data_dir, f'{self.field.replace("_", "-")}-embeddings-{source}-results.jsonl')

        # Create batch embeddings.
        create_batch_embeddings(
            results=results,
            text_field=self.field,
            batch_file=batch_file,
            custom_id='sample_id',
            model=self.model,
        )
        print(f'Created batch file: {batch_file}')

        # Run the batch job, waiting for the job to complete.
        job = run_batch_embeddings(self.client, batch_file)
        while True:
            job = self.client.batches.retrieve(job.id)
            if job.status == 'completed':
                break
            elif job.status == 'failed':
                raise RuntimeError("Batch job failed.")
            print('Job status:', job.status)
            sleep(pause)
        print('Job completed:', job.id)

        # Save the results of the batch job.
        batch_results = self.client.files.content(job.output_file_id)
        batch_results.write_to_file(batch_results_file)
        print('Saved batch results:', batch_results_file)
        return batch_results_file

    def upload_results_to_firestore(self, batch_results_file: str):
        """Upload the batch embeddings results to Firestore."""
        upload_batch_embeddings(
            batch_results_file=batch_results_file,
            model=self.model,
            db=self.db,
            col='public/ai/embeddings',
            verbose=True,
        )

# === Tests ===
if __name__ == "__main__":

    # === Setup ===

    # Define the fields that need embeddings.
    fields = ['product_name', 'strain_name']

    # Choose the field to embed (DEV: Using product_name)
    field = 'product_name'

    # Define states with COAs
    STATES_WITH_COAS = {
        'ca': {'name': 'California'},
        'fl': {'name': 'Florida'},
        'az': {'name': 'Arizona'},
        'ny': {'name': 'New York'},
    }

    # Define the model.
    model = 'text-embedding-3-small'

    # Define the cache directory.
    cache_dir = 'D://data/.cache'

    # Define the embeddings directory.
    embeddings_dir = 'D://data/.cache/embeddings'

    # Initialize Firebase.
    try:
        initialize_app()
    except ValueError:
        pass
    db = firestore.Client()

    # Initialize embedding batches.
    manager = EmbeddingBatchManager(field=field, model=model)

    # Create embeddings for each state.
    for state, state_data in STATES_WITH_COAS.items():

        # Read the results.
        results = read_cached_results(cache_dir, state, field)
        print('Number of unique results after de-duplication:', len(results))

        # Create and run the batch embeddings for the given state.
        batch_results_file = manager.create_and_run_batch(
            results,
            state,
            pause=60 * 5,
        )

        # Upload embeddings to Firestore.
        manager.upload_results_to_firestore(batch_results_file)
        print(f'Saved {len(results)} embeddings to Firestore.')

    # === Create a local cache of all of the embeddings (optional) ===

    # Get all datafiles in the embeddings directory if they end in -results.jsonl
    all_batch_results = []
    for root, dirs, files in os.walk(embeddings_dir):
        for file in files:
            if file.endswith('-results.jsonl'):
                all_batch_results.append(os.path.join(root, file))

    # Read all of the results from all states.
    all_results = []
    for s in STATES_WITH_COAS.keys():
        results_cache = Bogart(os.path.join(cache_dir, f'results-{s}.jsonl'))
        all_results.append(results_cache.to_df())
    all_results = pd.concat(all_results)

    # Cache each embedding locally.
    embedding_cache = Bogart(os.path.join(cache_dir, 'embeddings.jsonl'))
    for results_file in all_batch_results:
        if 'product-name' in results_file:
            key = 'product_name'
        else:
            key = 'strain_name'
        with open(results_file, 'r') as f:
            batch_results = [json.loads(line) for line in f]
        for result in batch_results:
            custom_id = result['custom_id']
            embedding = result['response']['body']['data'][0]['embedding']
            try:
                text = all_results[all_results['sample_id'] == custom_id].iloc[0][key]
            except IndexError:
                continue
            text_hash = create_hash(text.strip().lower())
            embedding_cache.set(text_hash, {
                'text': text,
                'embedding': embedding,
                'model': model,
                'dimensions': len(embedding),
            })
    print(f'Cached embeddings: {len(embedding_cache.to_df())}')

    # === Optional: Count the number of embeddings in Firestore. ===

    def count_firestore_embeddings(ref, model: str) -> int:
        """
        Count the number of embeddings in Firestore.
        Args:
            db: The Firestore database client.
            ref: The Firestore collection reference.
            model: The model used to generate the embeddings.
        """
        query = ref.where(filter=FieldFilter('model', '==', model))
        aggregate_query = aggregation.AggregationQuery(query)
        aggregate_query.count(alias='all')
        response = aggregate_query.get()
        count = response[0][0].value
        return count

    # Count embeddings in Firestore,
    ref = db.collection('public').document('ai').collection('embeddings')
    count = count_firestore_embeddings(ref, model)
    print(f'Number of embeddings in Firestore: {count}')
