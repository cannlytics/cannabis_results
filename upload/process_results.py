"""
Analyze Results
Copyright (c) 2024 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 10/7/2024
Updated: 8/24/2025
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>
"""
# Standard imports:
import ast
from io import BytesIO
from PIL import Image
import os
from time import sleep
from urllib.parse import urlparse

# External imports:
# from cannlytics.data import create_hash
from cannlytics.data.cache import Bogart
from cannlytics.firebase import (
    get_file_url,
    upload_file,
)
from cannlytics.stats.stats import (
    calculate_colourfulness,
    calculate_purpleness,
)
import cv2
import pandas as pd
import pdfplumber
import requests


#-----------------------------------------------------------------------
# Data Quality Checks.
#-----------------------------------------------------------------------

def analyze_coa_duplicates(results: pd.DataFrame):
    """Identify any duplicate COAs."""

    # Check for null values.
    null_coas = results['coa_pdf'].isnull().sum()
    print(f"Total number of records: {results.shape[0]}")
    print(f"Number of unique 'coa_pdf' values: {results['coa_pdf'].nunique()}")
    print(f"Number of null values in 'coa_pdf': {null_coas}")
    
    # Identify duplicates.
    is_duplicate = results.duplicated(subset=['coa_pdf'], keep=False)
    duplicate_count = is_duplicate.sum()
    print(f"Number of records marked as duplicates: {duplicate_count}")
    if duplicate_count == 0:
        print("No duplicates found.")
        return
    
    # Find and count any duplicate COAs.
    duplicate_groups = results[is_duplicate]
    grouped = duplicate_groups.groupby('coa_pdf')
    for coa_pdf, group in grouped:
        print(f'\nCOA PDF: {coa_pdf}')
        unique_hashes = group['sample_hash'].unique()
        if len(unique_hashes) > 1:
            print(f'- Warning: {len(unique_hashes)} different sample_hashes found!')
            for hash_value in unique_hashes:
                count = group[group['sample_hash'] == hash_value].shape[0]
                print(f'  - Hash: {hash_value}, Count: {count}')
        else:
            print(f'- All {group.shape[0]} records have the same sample_hash: {unique_hashes[0]}')
        print(f'- Total duplicate records: {group.shape[0]}')


def find_coa_pdfs(
        results: pd.DataFrame,
        pdf_dir: str,
        verbose: bool = False
    ):
    """
    Match COA PDFs with the results.
    Args:
        results (pd.DataFrame): DataFrame containing the results
        pdf_dir (str): Directory path where PDFs are stored
        verbose (bool): Print additional information.
    Returns:
        tuple: (coa_pdfs, missing)
            coa_pdfs (dict): Dictionary mapping sample_hash to PDF path
            missing (list): List of sample_hashes with missing PDFs
    """
    coa_pdfs, missing = {}, []
    for _, result in results.iterrows():

        # Get the name of the PDF.
        identifier = result['coa_pdf']
        if identifier == 'download.pdf':
            lab_results_url = result['lab_results_url']
            identifier = lab_results_url.split('=')[-1].split('?')[0]
        
        # Try to find the matching PDF.
        pdf_found = False
        for root, _, files in os.walk(pdf_dir):
            if pdf_found:
                break
            for filename in files:
                if identifier.split('\\')[-1] in filename:
                    pdf_path = os.path.join(root, filename)
                    coa_pdfs[result['sample_hash']] = pdf_path
                    pdf_found = True
                    break

        # Record any missing PDFs
        if not pdf_found:
            if verbose:
                print(f'Missing PDF: {identifier}')
            missing.append(result['sample_hash'])

    if verbose:
        print('Total number of missing PDFs:', len(missing))
    return coa_pdfs, missing


#-----------------------------------------------------------------------
# Standardization functions.
#-----------------------------------------------------------------------

def format_data_link(id, data_type, base=''):
    """Format a link to the data."""
    return f'{base}/{data_type}/{id}'


def format_coa_title(row):
    """Format a title for the COA."""
    product_name = row.get('product_name')
    strain_name = row.get('strain_name')
    if pd.isna(product_name) and pd.isna(strain_name):
        return row.get('lab_id') or row.get('batch_number') or row['id']
    if pd.isna(product_name):
        return strain_name
    if pd.isna(strain_name):
        return product_name
    if strain_name in product_name:
        return product_name
    return f'{product_name} ({strain_name})'


def safe_eval(x):
    """Safely evaluate a value that may be a string."""
    try:
        return ast.literal_eval(x) if isinstance(x, str) else x
    except:
        return x


def safe_ratio(a, b):
    """Safely calculate a ratio."""
    try:
        return float(a) / float(b) if float(b) != 0 else pd.NA
    except:
        return pd.NA


def standardize_methods(df: pd.DataFrame) -> pd.DataFrame:
    """
    Format `methods` from any current `methods` or `{analysis}_method`'s.
    E.g. [{"analysis": "cannabinoids", "method": "HPLC"}]
    """
    df['methods'] = df['methods'].apply(safe_eval)
    df['methods'] = df['methods'].apply(
        lambda x: [{"analysis": None, "method": m} for m in x] 
        if isinstance(x, list) 
        else [] if pd.isna(x) or x == '' 
        else [{"analysis": None, "method": str(x)}]
    )
    method_columns = [col for col in df.columns if col.endswith('_method') and col != 'methods']
    for col in method_columns:
        analysis = col.replace('_method', '')
        df['methods'] = df.apply(
            lambda row: row['methods'] + [{"analysis": analysis, "method": row[col]}] 
            if pd.notna(row[col]) and row[col] != '' 
            else row['methods'],
            axis=1
        )
    df.drop(columns=method_columns, inplace=True)
    return df


def standardize_product_type(x, product_types):
    """Assign a standard product type given a dictionary of types with standard `names`."""
    for key, values in product_types.items():
        names = values['names']
        if x in names:
            return key
    return None


def standardize_statuses(df: pd.DataFrame) -> pd.DataFrame:
    """
    Format `statuses` from any columns ending with '_status'.
    E.g. [{"analysis": "pesticides", "status": "pass"}]
    """
    df['statuses'] = [[]] * len(df)
    status_columns = [col for col in df.columns if col.endswith('_status')]
    for col in status_columns:
        analysis = col.replace('_status', '')
        df['statuses'] = df.apply(
            lambda row: row['statuses'] + [{"analysis": analysis, "status": row[col].lower()}]
            if pd.notna(row[col]) and row[col] != ''
            else row['statuses'],
            axis=1
        )
    df['statuses'] = df['statuses'].apply(lambda x: list({(d['analysis'], d['status']): d for d in x}.values()) if x else [])
    df.drop(columns=status_columns, inplace=True)
    return df


def standardize_traceability_ids(
        df: pd.DataFrame,
        provider: str = 'metrc',
        fields: list[str] = ['batch_id', 'batch_number', 'traceability_id', 'source_id'],
        keep: list[str] = ['batch_number']
    ) -> pd.DataFrame:
    """
    Standardize traceability IDs by creating a new 'traceability_ids' column.

    Args:
        df (pd.DataFrame): Input DataFrame.
        provider (str): Provider name (default: 'metrc').
        fields (List[str]): Fields to include in traceability_ids.
        keep (List[str]): Fields to keep in the DataFrame after processing.

    Returns:
        pd.DataFrame: DataFrame with standardized traceability IDs.
    """
    def process_row(row):
        ids = []
        for col in row.index:
            if col == f'{provider}_ids':
                val = safe_eval(row[col])
                if isinstance(val, list):
                    ids.extend(map(str, val))
                elif pd.notna(val) and val != '':
                    ids.append(str(val))
            elif col.startswith(provider) or col in fields:
                if pd.notna(row[col]) and row[col] != '':
                    ids.append(str(row[col]))
        return list(set(ids)) if ids else None

    # Process all relevant columns.
    df['traceability_ids'] = df.apply(process_row, axis=1)

    # Determine the columns to drop.
    cols_to_drop = [col for col in df.columns 
                    if col.startswith(provider) 
                    or (col in fields and col not in keep)]

    # Drop any unused columns and return the data.
    df = df.drop(columns=cols_to_drop)
    return df


def standardize_urls(url_list, index: int = 0):
    """Extract the first URL from a list of dictionaries."""
    try:
        urls = ast.literal_eval(url_list) if isinstance(url_list, str) else url_list
        return urls[index]['url'] if urls else pd.NA
    except:
        return pd.NA


#-----------------------------------------------------------------------
# Image processing
#-----------------------------------------------------------------------

def create_pdf_thumbnail(
        pdf_path: str,
        image_file: str,
        size: tuple = (512, 512),
        resolution: int = 96,
    ):
    """Create a thumbnail of a PDF using pdfplumber.
    Args:
        pdf_path (str): Path to the input PDF file.
        output_path (str): Path to save the thumbnail.
        size (tuple): Maximum width and height of the thumbnail.
        resolution (int): Resolution for initial PDF rendering.
    """
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        img = page.to_image(resolution=resolution)
        pil_img = img.original
        pil_img.thumbnail(size)
        pil_img.save(image_file, format='PNG')


def download_image(url, save_path):
    """Download an image from a URL and save it locally."""
    response = requests.get(url)
    if response.status_code == 200:
        img = Image.open(BytesIO(response.content))
        img.save(save_path)
        return True
    return False


def process_and_upload_images(
        results,
        image_dir: str,
        bucket_name: str,
        folder: str = 'public/images',
        image_cache: Bogart = None,
        pause: float = 1.0,
    ):
    """Process, upload images to Firebase, and update results DataFrame."""
    for index, row in results.iterrows():
        image_url = row['image_url']
        if pd.isna(image_url) or not image_url:
            continue

        # Generate a unique hash for the image URL
        image_hash = image_cache.hash_url(image_url)

        # Check if the image has already been processed
        if image_cache:
            if image_cache.get(image_hash):
                print(f"Image already processed: {image_url}")
                cached = image_cache.get(image_hash)
                results.loc[index, cached.keys()] = cached.values()
                continue

        # Parse the URL to get the file extension
        parsed_url = urlparse(image_url)
        file_extension = os.path.splitext(parsed_url.path)[1]
        if not file_extension:
            file_extension = '.jpg'  # Default to .jpg if no extension found

        # Download the image
        local_image_path = os.path.join(image_dir, f"{image_hash}{file_extension}")
        if not download_image(image_url, local_image_path):
            print(f"Failed to download image: {image_url}")
            continue

        # Upload the image to Firebase storage
        firebase_image_path = f'{folder}/{image_hash}{file_extension}'
        upload_file(
            destination_blob_name=firebase_image_path,
            source_file_name=local_image_path,
            bucket_name=bucket_name
        )

        # Get the download URL
        image_download_url = get_file_url(firebase_image_path, bucket_name=bucket_name)
        print(f"Uploaded image: {image_url} -> {image_download_url}")

        # Update the results DataFrame
        image_data = {
            'image_url': image_url,
            'image_hash': image_hash,
            'image_ref': firebase_image_path,
            'image_download_url': image_download_url,
            'sample_hash': row['sample_hash'],
        }
        results.loc[index, image_data.keys()] = image_data.values()

        # Update the image cache
        if image_cache:
            image_cache.set(image_hash, image_data)
        sleep(pause)

    return results


def analyze_image(image_file):
    """Analyze the purpleness and colorfulness of an image."""
    image = cv2.imread(image_file)
    cropped_img_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    mean_color = cropped_img_rgb.mean(axis=0).mean(axis=0)
    purpleness = calculate_purpleness(mean_color)
    colorfulness = calculate_colourfulness(cropped_img_rgb)
    return {'purpleness': purpleness, 'colorfulness': colorfulness}


def calc_image_stats(
        df: pd.DataFrame,
        image_dir: str,
        image_cache=None,
        key: str = 'image_hash',
    ):
    # For results with images, calculate `colorfulness` and `purpleness`.
    colorfulness, purpleness = {}, {}
    image_hashes = df[df[key].notna()][key].unique()
    for image_hash in image_hashes:

        # Check if the image has already been analyzed.
        if image_cache:
            cached_image = image_cache.get(image_hash)
            if cached_image is None:
                print('Missing:', image_hash)
                continue
            if cached_image.get('colorfulness') and cached_image.get('purpleness'):
                colorfulness[image_hash] = cached_image['colorfulness']
                purpleness[image_hash] = cached_image['purpleness']
                print('Cached:', image_hash)
                continue

        # Analyze the image.
        filename = cached_image['image_ref'].split('/')[-1]
        image_path = os.path.join(image_dir, filename)
        color_data = analyze_image(image_path)
        if image_cache:
            image_cache.set(image_hash, {**cached_image, **color_data})
        colorfulness[image_hash] = color_data['colorfulness']
        purpleness[image_hash] = color_data['purpleness']
        print('Analyzed:', image_hash)

    # Add colorfulness and purpleness to the results.
    df['colorfulness'] = df[key].map(colorfulness)
    df['purpleness'] = df[key].map(purpleness)
    return df


#-----------------------------------------------------------------------
# Results statistics.
#-----------------------------------------------------------------------

def calc_analyte_stats(
        results: pd.DataFrame,
        cannabinoids: dict,
        terpenes: dict,
    ) -> pd.DataFrame:
    """
    Calculate statistics for cannabis analysis results efficiently.
    Args:
        results (pd.DataFrame): The dataframe containing the results
        cannabinoids (dict): Dictionary of standard cannabinoids
        terpenes (dict): Dictionary of standard terpenes
    Returns:
        pd.DataFrame: The results dataframe with added or updated statistics
    """
    # Create a copy to avoid modifying the original
    df = results.copy()
    
    # Define all the calculations to be performed
    TOTAL_CALCULATIONS = [
        ('total_cannabinoids', list(cannabinoids.keys())),
        ('total_terpenes', [col for col in terpenes.keys() if col in df.columns])
    ]
    
    COMPOUND_CALCULATIONS = [
        ('total_thc', ('delta_9_thc', 'thca', 0.877)),
        ('total_cbd', ('cbd', 'cbda', 0.877))
    ]
    
    RATIO_PAIRS = [
        ('thc_cbd_ratio', 'total_thc', 'total_cbd'),
        ('beta_pinene_d_limonene_ratio', 'beta_pinene', 'd_limonene'),
        ('alpha_humulene_beta_caryophyllene_ratio', 'alpha_humulene', 'beta_caryophyllene'),
        ('camphene_d_limonene_ratio', 'camphene', 'd_limonene'),
        ('beta_myrcene_beta_pinene_ratio', 'beta_myrcene', 'beta_pinene'),
        ('beta_caryophyllene_d_limonene_ratio', 'beta_caryophyllene', 'd_limonene')
    ]

    # Calculate total sums (like total_cannabinoids, total_terpenes).
    for col_name, source_cols in TOTAL_CALCULATIONS:
        if col_name not in df.columns and source_cols:
            df[col_name] = df[source_cols].sum(axis=1, min_count=1)

    # Calculate compound totals (like total_thc, total_cbd).
    for col_name, (base_col, acid_col, conversion_factor) in COMPOUND_CALCULATIONS:
        if col_name not in df.columns:
            df[col_name] = (
                df[base_col].fillna(0) + 
                conversion_factor * df[acid_col].fillna(0)
            )

    # Calculate all ratios.
    for ratio_name, numerator_col, denominator_col in RATIO_PAIRS:
        if all(col in df.columns for col in [numerator_col, denominator_col]):
            df[ratio_name] = df.apply(
                lambda row: safe_ratio(
                    row[numerator_col], 
                    row[denominator_col]
                ), 
                axis=1
            )
        else:
            df[ratio_name] = pd.NA

    return df

    # Future work: Identify mono and sesquiterpenes.
    # results['monoterpene_to_sesquiterpene_ratio'] = results['total_monoterpenes'] / results['total_sesquiterpenes']


def calc_aggregate_results_stats(
        results: pd.DataFrame,
        cannabinoid_keys: list[str] = None,
        terpene_keys: list[str] = None,
    ) -> pd.DataFrame:
    """
    Calculate aggregate statistics for the results.
    """

    def calculate_statistics(group: pd.DataFrame, name: str) -> pd.DataFrame:
        """Calculate mean, median, std, percentiles, and maximum for a given group."""
        stats = group.describe(percentiles=[.25, .50, .75]).T
        stats['period'] = name
        stats['max'] = group.max()
        stats = stats[['period', 'mean', 'std', '25%', '50%', '75%', 'max']].rename(
            columns={'50%': 'median', '25%': 'percentile_25', '75%': 'percentile_75'})
        return stats
    
    # FIXME: Vary by `standard_product_type`.

    # TODO: Calculate:
    # - chemical_diversity
    # - cannabinoid_diversity
    # - terpene_diversity
    
    # Create the timeseries.
    results['date_tested'] = pd.to_datetime(results['date_tested'])
    results.set_index('date_tested', inplace=True)
    periods = {
        'daily': results.resample('D'),
        'weekly': results.resample('W'),
        'monthly': results.resample('M'),
        'quarterly': results.resample('Q'),
        'yearly': results.resample('Y')
    }
    
    # Calculate statistics for each period.
    all_stats = []
    for period_name, period_group in periods.items():
        if cannabinoid_keys:
            cannabinoid_stats = calculate_statistics(period_group[cannabinoid_keys], period_name)
            cannabinoid_stats['type'] = 'cannabinoid'
            all_stats.append(cannabinoid_stats)
        if terpene_keys:
            terpene_stats = calculate_statistics(period_group[terpene_keys], period_name)
            terpene_stats['type'] = 'terpene'
            all_stats.append(terpene_stats)
        
        # TODO: Add diversity here.

    # Return the statistics.
    stats = pd.concat(all_stats).reset_index().rename(columns={'index': 'compound'})
    return stats
