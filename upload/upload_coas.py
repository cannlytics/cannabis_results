"""
Upload COAs
Copyright (c) 2024-2025 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 10/7/2024
Updated: 2/16/2025
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:

    Archive publicly available COAs for research.

    * Reads parsed COA data from local caches.
    * Processes and standardizes the data.
        - Removes duplicates,
        - standardizes product types,
        - standardizes analytes,
        - standardizes strain names.
    * Uploads the processed results to a Firestore database.

Sources:

    ✓ California
    ✓ Florida
    ✓ Arizona
    ✓ New York

"""
# Standard imports:
from datetime import datetime
import gc
import json
import math
import os

# External imports:
from dotenv import dotenv_values
from google.cloud.firestore_v1.vector import Vector
from openai import OpenAI
import pandas as pd

# Internal imports:
from cannlytics.ai.embeddings import (
    get_embedding,
    get_results_embedding,
)
from cannlytics.ai.gen import text_to_color_ai
from cannlytics.compounds import cannabinoids, terpenes
from cannlytics.data import create_hash
from cannlytics.data.cache import Bogart
from cannlytics.data.coas import standardize_results
from cannlytics.data.coas.parsing import parse_list_column
from cannlytics.data.strains.strain_identifier import StrainIdentifier
from cannlytics.firebase import (
    initialize_firebase,
    get_file_url,
    update_document,
    upload_file,
)
from cannlytics.logs import initialize_logs
from cannlytics.stats.stats import calc_diversity_index
from cannlytics.utils import kebab_case

# DEV: Ensure the script is in the right directory.
os.chdir('D://data/cannabis_results/upload')

# Local imports:
from identify_strains import create_label_name
from process_results import (
    analyze_coa_duplicates,
    # calc_aggregate_results_stats,
    calc_image_stats,
    create_pdf_thumbnail,
    find_coa_pdfs,
    format_coa_title,
    format_data_link,
    calc_analyte_stats,
    process_and_upload_images,
    standardize_methods,
    standardize_product_type,
    standardize_statuses,
    standardize_traceability_ids, 
    standardize_urls,
)


#-----------------------------------------------------------------------
# Setup.
#-----------------------------------------------------------------------



# Define states with COAs.
STATES_WITH_COAS = {
    'ca': {'name': 'California'},
    'fl': {'name': 'Florida'},
    'az': {'name': 'Arizona'},
    'ny': {'name': 'New York'},
    # 'mo': {'name': 'Missouri'},
}

# Initialize logs.
logger = initialize_logs(
    name='upload_coas',
    prefix='upload-coas',
    log_dir='D://data/.logs',
)

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

# Initialize the COA cache.
cache_dir = 'D://data/.cache'
coas_cache = Bogart(os.path.join(cache_dir, f'coas.jsonl'))
pdf_cache = Bogart(os.path.join(cache_dir, f'pdfs.jsonl'))

# Define where curated data will be saved.
data_dir = 'D://data/coas'
if not os.path.exists(data_dir):
    os.makedirs(data_dir)

# Create a directory for the images.
image_dir = f'D://data/coas/images'
if not os.path.exists(image_dir):
    os.makedirs(image_dir)


#-----------------------------------------------------------------------
# Read all of the COA data.
#-----------------------------------------------------------------------

def read_parsed_coas(sources) -> pd.DataFrame:
    """Read all of the parsed COA results."""
    results = []
    for key in sources.keys():
        cache_path = os.path.join(cache_dir, f'results-{key}.jsonl')
        results_cache = Bogart(cache_path)
        df = results_cache.to_df()
        df['state'] = key
        df['lab_state'] = df['lab_state'].fillna(key)
        df['producer_state'] = df['producer_state'].fillna(key)
        results.append(df)
        logger.info(f'Read {len(df)} results from: {key}')
        del df
        gc.collect()
    return pd.concat(results)


# Read all of the parsed COA results.
# FIXME: I think reading all of the COAs at once is causing memory issues.
logger.info('Reading all parsed COA results...')
results = read_parsed_coas(STATES_WITH_COAS)
logger.info(f'Raw total number of results: {len(results)}')


#-----------------------------------------------------------------------
# Process the data.
#-----------------------------------------------------------------------

def process_coas(
        results: pd.DataFrame,
        processing_file: str = 'processing.json',
        coas_cache: Bogart = coas_cache,
    ) -> pd.DataFrame:

    # Read constants for processing.
    with open(processing_file, 'r') as f:
        processing = json.load(f)
        nuisance_analytes = processing['nuisance_analytes']
        nuisance_columns = processing['nuisance_columns']

    # Ensure state is lowercase.
    results['lab_state'] = results['lab_state'].str.lower()
    results['producer_state'] = results['producer_state'].str.lower()

    # Fill missing `producer_license_number` with `license_number`.
    try:
        results['producer_license_number'] = results['producer_license_number'].fillna(results['license_number'])
        results.drop(columns=['license_number'], inplace=True)
    except KeyError:
        pass

    # Future work: Fill NA with mislabeled columns:
    # E.g.: ext_batch_number

    # Drop duplicates.
    results = results.drop_duplicates(subset=['sample_hash'])
    logger.info(f'Number of unique results: {len(results)}')

    # Drop all non-standard columns.
    drop_columns = [col for col in nuisance_columns if col in results.columns]
    drop_columns += [col for col in results.columns if '_per_' in col.lower()]
    results.drop(columns=drop_columns, inplace=True)

    # Implement caching and only process and upload new data.
    try:
        uploaded_coas = coas_cache.to_df()['sample_id']
    except KeyError:
        uploaded_coas = []
    results = results[~results['sample_id'].isin(uploaded_coas)]
    logger.info(f'Number of already uploaded COAs: {len(uploaded_coas)}')
    logger.info(f'Number of new COAs to upload: {len(results)}')

    # Analyze the data to ensure there are no duplicates.
    analyze_coa_duplicates(results)

    # Standardize the methods.
    results = standardize_methods(results)

    # Standardize the `statuses`.
    results = standardize_statuses(results)

    # Standardize traceability IDs.
    results = standardize_traceability_ids(results)

    # Get `coa_url` from `coa_urls` and `image_url` from `images`.
    results['coa_url'] = results['coa_urls'].apply(standardize_urls)
    try:
        results['image_url'] = results['images'].apply(standardize_urls)
    except KeyError:
        results['image_url'] = None
    results.drop(columns=['coa_urls'], inplace=True)

    # De-duplicate duplicate parses using `sample_id`.
    # Note: Keep the observation with the latest `coa_parsed_at`.
    results = results.sort_values('coa_parsed_at', ascending=False)
    results = results.drop_duplicates(subset=['sample_id'], keep='first')
    logger.info(f'Number of unique results after de-duplication: {len(results)}')

    # # DEV: Look at analytes to see if any standardizations are needed.
    # from cannlytics.data.coas.parsing import find_unique_analytes
    # analytes = find_unique_analytes(results)
    # analytes = list(set(analytes) - set(nuisance_analytes))
    # analytes = sorted(list(analytes))
    # analytes = [x for x in analytes if '_per_' not in x]
    # print(f'Unique analytes: {str(analytes)}')

    # Add columns for the standard analytes.
    logger.info('Standardizing analytes (this takes a while)...')
    standard_analytes = list(cannabinoids.keys()) + list(terpenes.keys())
    results.drop(columns=standard_analytes, errors='ignore', inplace=True)
    results = parse_list_column(results, 'results')
    results = standardize_results(results, standard_analytes, errors='coerce')

    # Calculate analyte totals (if missing) and ratios.
    # Adds totals: `total_cannabinoids`, `total_terpenes`, `total_thc`, `total_cbd`
    # Adds ratios: `thc_cbd_ratio`, `beta_pinene_d_limonene_ratio`, etc.
    results = calc_analyte_stats(results, cannabinoids, terpenes)

    # Calculate diversity index for cannabinoids and terpenes.
    cannabinoid_keys = list(cannabinoids.keys())
    terpene_keys = list(terpenes.keys())
    results['chemical_diversity'] = calc_diversity_index(results, cannabinoid_keys + terpene_keys)
    results['cannabinoid_diversity'] = calc_diversity_index(results, cannabinoid_keys)
    results['terpene_diversity'] = calc_diversity_index(results, terpene_keys)

    def calc_cannabinoid_type(x):
        """Determine the cannabinoid type given a THC to CBD ratio.
        See: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9119530/
        """
        try:
            log_thc_cbd_ratio = math.log10(x)
            if log_thc_cbd_ratio < -0.5:
                return 'CBD Dominant'
            elif log_thc_cbd_ratio > 0.5:
                return 'THC Dominant'
            else:
                return 'Balanced THC:CBD'
        except:
            return None

    # Add `cannabinoid_type`.
    results['cannabinoid_type'] = results['thc_cbd_ratio'].apply(calc_cannabinoid_type)

    # Add `dominant_terpene` and `dominant_cannabinoid`.
    cannabinoid_columns = list(cannabinoids.keys())
    terpene_columns = list(terpenes.keys())
    results['dominant_cannabinoid'] = results[cannabinoid_columns].idxmax(axis=1)
    results['dominant_terpene'] = results[terpene_columns].idxmax(axis=1)

    # Future work: Try to get / standardize moisture content.

    # Fill in missing times.
    results['date_tested'] = results['date_tested'].fillna(results['date_collected'])

    # Standardize time.
    results['date'] = pd.to_datetime(results['date_tested'], format='mixed', errors='coerce')
    results['week'] = results['date'].dt.to_period('W').astype(str)
    results['month'] = results['date'].dt.to_period('M').astype(str)
    results['year'] = results['date'].dt.to_period('Y').astype(str)
    results = results.sort_values('date', ascending=False)

    # Return the results.
    return results


# Process the results.
results = process_coas(results)


#-----------------------------------------------------------------------
# Augment data: Standardize product type and subtype.
#-----------------------------------------------------------------------

def augment_product_types(
        results,
        product_types_file: str = 'product-types.json',
        product_subtypes_file: str = 'product-subtypes.json',
    ) -> pd.DataFrame:

    # Read the standard product types.
    with open(product_types_file, 'r') as f:
        product_types = json.load(f)

    # Read product subtypes.
    with open(product_subtypes_file, 'r') as f:
        product_subtypes = json.load(f)

    # Find all product types not in the standard product types.
    all_standard_names = set()
    for product_type in product_types.values():
        all_standard_names.update(name for name in product_type['names'])
    unique_product_types = set(results['product_type'].dropna().unique())
    undefined_product_types = unique_product_types - set(all_standard_names)
    if undefined_product_types:
        logger.error(
            f'Product type validation failed. Found {len(undefined_product_types)} undefined types: '
            f'{str(list(undefined_product_types))}. '
            'These must be added to product-types.json before processing can continue.'
        )
        raise ValueError('Undefined product types found. Please add them to the `product-types.json` file.')

    # Assign standard product type.
    results['standard_product_type'] = results['product_type'].apply(
        standardize_product_type,
        product_types=product_types
    )

    # Assign standard product type.
    results['product_subtype'] = results['product_type'].apply(
        standardize_product_type,
        product_types=product_subtypes
    )

    # Return the results.
    return results


# Augment the product types.
results = augment_product_types(
    results,
    product_types_file='D://data/cannabis_results/upload/product-types.json',
    product_subtypes_file='D://data/cannabis_results/upload/product-subtypes.json',
)
logger.info('Standardized product types.')


#-----------------------------------------------------------------------
# Augment data: Standardize strain name.
#-----------------------------------------------------------------------

def augment_strain_names(
        results,
        cache_dir: str,
        client,
        db,
    ) -> pd.DataFrame:
    # Get any standard strain names from the `label_name`.
    strain_names_cache = Bogart(os.path.join(cache_dir, 'standard-strain-names.jsonl'))

    # Initialize strain identifier.
    identifier = StrainIdentifier(
        client=client,
        model='gpt-4o-mini'
    )

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
    for _, row in missing_strain_names.iterrows():
        text, label_hash = row['label_name'], row['label_hash']
        strain_names, _ = identifier.identify_strains(text)
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

    # TODO: Map standard names to human-reviewed strain names.

    print('Standardized strain names.')

    # Return the results.
    return results


# Augment strain names.
results = augment_strain_names(
    results,
    cache_dir=cache_dir,
    client=client,
    db=db,
)
logger.info('Standardized strain names.')


#-----------------------------------------------------------------------
# Augment data: Vector embeddings.
#-----------------------------------------------------------------------

def augment_vector_embeddings(
        results,
        cache_dir: str,
        client,
        db,
    ) -> pd.DataFrame:

    # Read the cached embeddings.
    embeddings_cache = Bogart(os.path.join(cache_dir, 'embeddings.jsonl'))

    # Create `results_embedding`.
    logger.info('Creating results embeddings...')
    standard_analytes = list(cannabinoids.keys()) + list(terpenes.keys())
    results['results_embedding'] = results.apply(
        lambda row: get_results_embedding(row.to_dict(), standard_analytes),
        axis=1,
    )

    # Create product name embeddings.
    logger.info('Creating product name embeddings...')
    results['product_name_embedding'] = results['product_name'].apply(
        lambda x: get_embedding(x, client=client, db=db, cache=embeddings_cache, verbose=True) if pd.notna(x) and x != '' else None
    )

    # Create strain name embeddings with the standard strain name.
    logger.info('Creating strain name embeddings...')
    results['strain_name_embedding'] = results['standard_strain_name'].apply(
        lambda x: get_embedding(x, client=client, db=db, cache=embeddings_cache, verbose=True) if pd.notna(x) and x != '' else None
    )

    # Return the results.
    return results


# Augment vector embeddings.
results = augment_vector_embeddings(
    results,
    cache_dir=cache_dir,
    client=client,
    db=db,
)
logger.info('Created vector embeddings.')


#-----------------------------------------------------------------------
# Augment data: Metadata
#-----------------------------------------------------------------------    

def augment_metadata(
        results,
        data_type: str = '',
        id_field: str = 'sample_id',
        created_by: str = 'cannlytics',
    ) -> pd.DataFrame:
    """Add metadata."""
    timestamp = pd.Timestamp.now().isoformat()
    results['id'] = results[id_field]
    results['updated_at'] = timestamp
    results['created_at'] = timestamp
    results['created_by'] = created_by
    results['data_type'] = data_type
    results['link'] = results[id_field].apply(format_data_link, data_type=data_type)
    results['title'] = results.apply(format_coa_title, axis=1)
    return results


# Add metadata.
results = augment_metadata(results, data_type='coas')
logger.info('Added metadata.')


#-----------------------------------------------------------------------
# Augment data: Tag the data.
#-----------------------------------------------------------------------

def augment_tags(
        results,
        default_tag,
        tag_columns,
        cache_dir: str,
        client,
        db,
    ):
    """Create tags for the data."""

    # Use a color cache.
    color_cache = Bogart(os.path.join(cache_dir, 'colors.jsonl'))

    # Create tags.
    all_tags = []
    for _, row in results.iterrows():
        tags = []
        tags.append(default_tag)
        for col, link in tag_columns:
            text = row[col]
            if pd.isna(text) or not text:
                continue
            slug = kebab_case(text)
            text_hash = create_hash(text.strip().lower())
            tag_color = color_cache.get(text_hash)
            if tag_color is None:
                tag_color, _ = text_to_color_ai(
                    text,
                    client=client,
                    db=db,
                    verbose=False,
                    temperature=0.42,
                )
                color_cache.set(text_hash, tag_color)
            tags.append({
                'tag_id': slug,
                'tag_name': text,
                'tag_color': tag_color,
                'tag_link': link % slug,
            })
        all_tags.append(tags)
        print('Created tags for:', row['sample_id'])
    results['tags'] = all_tags
    return results


# Define the default tag.
default_tag = {
    'tag_id': 'coas',
    'tag_name': 'COA',
    'tag_color': '#3498db',
    'tag_link': '/coas',
}

# Define the tag columns.
tag_columns = [
    ('state', '/coas?state=%s'),
    ('standard_product_type', '/coas?product_type=%s'),
    ('standard_strain_name', '/strains/%s'),
    ('producer', '/licenses/%s'),
    ('lab', '/licenses/%s'),
]

# Augment tags.
results = augment_tags(
    results,
    default_tag=default_tag,
    tag_columns=tag_columns,
    cache_dir=cache_dir,
    client=client,
    db=db,
)
logger.info('Created tags.')


#-----------------------------------------------------------------------
# Upload COA PDFs to Google Cloud Storage and data to Firestore.
#-----------------------------------------------------------------------

def upload_coa_pdfs(
        results,
        bucket_name: str,
    ):
    """Upload COA PDFs to Google Cloud Storage."""

    # Get the COAs for each state.
    # Note: There will be missing COAs for each state.
    all_coa_pdfs = {}
    states = list(results['state'].unique())
    for state in states:
        state_name = STATES_WITH_COAS[state]['name']
        pdf_dir = f'D://data/{state_name.lower()}/results/pdfs'
        coa_pdfs, _ = find_coa_pdfs(results, pdf_dir, verbose=False)
        all_coa_pdfs = {**all_coa_pdfs, **coa_pdfs}

    # Upload PDFs to Google Cloud Storage.
    # Note: Checks if the file has been uploaded according to the local cache.
    logger.info(f'Number of unique COA PDFs: {len(all_coa_pdfs)}')
    for sample_hash, pdf_path in all_coa_pdfs.items():

        # Check if each unique PDF has already been uploaded.
        pdf_hash = pdf_cache.hash_file(pdf_path)
        if pdf_cache.get(pdf_hash):
            logger.info(f'Cached: {pdf_path}')
            cached = pdf_cache.get(pdf_hash)
            results.loc[results['sample_hash'] == sample_hash, cached.keys()] = cached.values()
            continue

        # Upload the file.
        file_ref = f'coas/{pdf_hash}.pdf'
        upload_file(
            destination_blob_name=file_ref,
            source_file_name=pdf_path,
            bucket_name=bucket_name,
        )
        download_url = get_file_url(file_ref, bucket_name=bucket_name)

        # Create a thumbnail of the COA PDF.
        thumbnail_file = os.path.join(image_dir, f'{pdf_hash}.png')
        create_pdf_thumbnail(pdf_path, thumbnail_file)
        thumbnail_ref = f'public/images/thumbnails/{pdf_hash}.png'
        upload_file(
            destination_blob_name=thumbnail_ref,
            source_file_name=thumbnail_file,
            bucket_name=bucket_name,
        )
        thumbnail_url = get_file_url(thumbnail_ref, bucket_name=bucket_name)

        # Update the results DataFrame.
        obs = {
            'pdf_hash': pdf_hash,
            'pdf_ref': file_ref,
            'pdf_url': download_url,
            'thumbnail_ref': thumbnail_ref,
            'thumbnail_url': thumbnail_url,
        }
        results.loc[results['sample_hash'] == sample_hash, obs.keys()] = obs.values()

        # Cache the PDF.
        obs['sample_hash'] = sample_hash
        pdf_cache.set(pdf_hash, obs)
        logger.info(f'Uploaded: {pdf_path} -> {download_url}')
    
    # Return the results.
    return results


# Upload COA PDFs to Google Cloud Storage.
results = upload_coa_pdfs(results, bucket_name=bucket_name)
logger.info('Finished uploading COA PDFs.')


#-----------------------------------------------------------------------
# Image analysis.
#-----------------------------------------------------------------------

# Download and upload images for archival and accessibility.
# Note: First downloads images, then uploads images to Firebase storage.
# Gets `image_ref` and `image_download_url`, and caches uploaded images.
image_cache = Bogart(os.path.join(cache_dir, 'images.jsonl'))
results = process_and_upload_images(
    results,
    image_dir,
    bucket_name,
    folder='public/images/coas',
    image_cache=image_cache,
)

# For results with images, calculate `colorfulness` and `purpleness`.
try:
    results = calc_image_stats(
        results,
        image_dir=image_dir,
        image_cache=image_cache,
    )
    logger.info('Calculated image stats.')
except KeyError:
    logger.info('No images to analyze.')


#-----------------------------------------------------------------------
# Finalize the standardization.
#-----------------------------------------------------------------------

def finalize_standardization(
        results,
        drop_columns: list = ['license_number'],
    ):
    """Finalize the standardization of the results."""

    # Remove unused features.
    if drop_columns:
        results.drop(columns=drop_columns, inplace=True, errors='ignore')

    # Final formatting of data.
    results = results.where(pd.notnull(results), None)

    # Return the results.
    return results


# Finish standardization.
results = finalize_standardization(
    results,
    drop_columns=['public'],
)
logger.info('Finalized standardization.')


#-----------------------------------------------------------------------
# Upload data.
#-----------------------------------------------------------------------

def safe_vector(x):
    """Convert input to Vector if not None."""
    return Vector(x) if x is not None else None

def vectorize_columns(obj, columns: list):
    """Convert columns to Vector objects."""
    for col in columns:
        if isinstance(obj, dict):
            obj[col] = safe_vector(obj[col])
        else:
            obj[col] = obj[col].apply(safe_vector)
    return obj


def standardize_dates(obs: dict) -> dict:
    """Turn dates to ISO format."""
    date_columns = [x for x in obs.keys() if x.startswith('date') and x != 'date']
    for date_column in date_columns:
        try:
            obs[date_column] = pd.to_datetime(obs[date_column], format='mixed', errors='coerce').isoformat()
        except:
            pass
    return obs


def upload_coa_data(
        results,
        embedding_columns,
        coas_cache,
        db,
        id_field='id',
        data_type='coas',
        update=False,
    ):
    """Upload the raw data to Firestore.
    Note: Checks the cache if the data has been uploaded.
    """
    for _, row in results.iterrows():

        # Check if the data has already been uploaded.
        doc_id = row[id_field]
        if coas_cache.get(doc_id) and not update:
            logger.info(f'Cached: {doc_id}')
            continue

        # Prepare the data for upload.
        ref = f'{data_type}/{doc_id}'
        obs = row.to_dict()
        entry = vectorize_columns(obs.copy(), embedding_columns)
        entry = standardize_dates(entry)
        try:
            obs['date'] = obs['date'].isoformat()
            if obs['date'] == 'NaT':
                raise AttributeError
        except AttributeError:
            obs['date'] = None
            for key in ['date', 'date_tested', 'week', 'month', 'year']:
                if key in entry:
                    entry[key] = None

        # Upload the data to Firestore.
        update_document(ref, entry, database=db)
        coas_cache.set(doc_id, obs)
        logger.info(f'Uploaded to Firestore: {ref}')


# Define column embeddings to convert to Vector objects.
embedding_columns = [
    'results_embedding',
    'product_name_embedding',
    'strain_name_embedding',
]

# Upload the data to Firestore.
upload_coa_data(
    results,
    embedding_columns,
    coas_cache,
    db,
    id_field='id',
    data_type='coas',
)
logger.info('✓ Completed uploading COA data to Firestore.')
