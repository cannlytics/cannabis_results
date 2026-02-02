"""
Parse COAs
Copyright (c) 2024 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 10/7/2024
Updated: 12/31/2024
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>
"""
# Standard imports:
from datetime import datetime
import os

# External imports:
import pandas as pd

# Internal imports:
from cannlytics.data.cache import Bogart
from cannlytics.data.coas import CoADoc
from cannlytics.data.coas.parsing import get_coa_files, parse_coa_pdfs
from cannlytics.utils.utils import hash_file

# Filter out the specific warning about CropBox.
import logging
logging.getLogger("pdfminer").setLevel(logging.ERROR)

def parse_coas(
        cache_path: str,
        pdf_dir: str,
        reverse: bool = True,
        log_dir: str = 'D://data/.logs',
        verbose: bool = False,
    ) -> pd.DataFrame:
    """Parse all COA PDFs in a given directory.
    Args:
        cache_path (str): The path to the cache file.
        pdf_dir (str): The directory where the PDFs are stored.
        reverse (bool): Whether to reverse the order of the results.
    Returns:
        pd.DataFrame: The analyzed results.
    """
    cache = Bogart(cache_path)
    parser = CoADoc()
    pdfs = get_coa_files(pdf_dir)
    # FIXME: Make optional:
    # pdfs.sort(key=os.path.getmtime)
    parse_coa_pdfs(
        pdfs,
        parser=parser,
        cache=cache,
        reverse=reverse,
        log_dir=log_dir,
        verbose=verbose,
    )
    del cache
    del pdfs
    gc.collect()


def remove_duplicate_files(
        directory: str,
        size: int = 65536,
        keep_oldest: bool = True,
        dry_run: bool = True,
        verbose: bool = True,
    ):
    """Remove duplicate PDFs from a directory, keeping oldest/newest version based on creation time.
    Args:
        directory: Directory containing PDF files
        size: Chunk size for hash computation
        keep_oldest: If True, keeps oldest version; if False, keeps newest
        dry_run: If True, only simulates deletion
        verbose: If True, prints detailed information
    """
    # Dictionary to store file hashes and their details.
    # Key: file hash, Value: list of (filepath, creation_time) tuples
    hashes = {}
    stats = {
        'total_files': 0,
        'unique_files': 0,
        'files_to_remove': 0,
        'removed_files': [],
        'errors': []
    }

    # Scan files.
    for filename in os.listdir(directory):
        if filename.endswith('.pdf'):
            stats['total_files'] += 1
            filepath = os.path.join(directory, filename)
            file_hash = hash_file(filepath, size=size)
            creation_time = os.path.getctime(filepath)
            
            if file_hash not in hashes:
                hashes[file_hash] = []
            hashes[file_hash].append((filepath, creation_time))

    # Process duplicates.
    for file_hash, files in hashes.items():
        if len(files) > 1:
            files.sort(key=lambda x: x[1])
            files_to_remove = files[1:] if keep_oldest else files[:-1]
            for filepath, creation_time in files_to_remove:
                stats['files_to_remove'] += 1
                if verbose:
                    created_at = datetime.fromtimestamp(creation_time).strftime('%Y-%m-%d %H:%M:%S')
                    print(f"{'Would remove' if dry_run else 'Removing'} duplicate file: {filepath} (created: {created_at})")
                if not dry_run:
                    try:
                        os.remove(filepath)
                        stats['removed_files'].append(filepath)
                    except Exception as e:
                        stats['errors'].append(f"Error removing {filepath}: {str(e)}")

    # Print summary.
    stats['unique_files'] = len(hashes)
    if verbose:
        print(f"=== Duplicate PDF Summary ===")
        print(f"Total files scanned: {stats['total_files']:,}")
        print(f"Number of duplicates: {stats['files_to_remove']:,}")
    if not dry_run:
        print(f"Removed duplicates.")
        if stats['errors']:
            print(f"Errors encountered: {len(stats['errors'])}")
    else:
        print("\nDry run: no files were actually removed.")
        print("Set dry_run=False to perform the actual cleanup.\n")
    return stats


def clean_directory(
        pdf_dir: str,
        dry_run: bool = False,
        size: int = 65536,
        keep_oldest: bool = True,
        verbose: bool = True,
    ):
    """Clean up a directory by removing duplicate PDF files.
    Args:
        pdf_dir: Directory containing PDF files.
        dry_run: If True, only simulates deletion.
    """
    if not os.path.exists(pdf_dir):
        raise FileNotFoundError(f"Directory not found: {pdf_dir}")

    # Get all subdirectories.
    subdirs = [x[0] for x in os.walk(pdf_dir)]
    print(f"Found {len(subdirs):,} directories to clean.")

    # Process each directory separately.
    total_stats = {
        'total_files': 0,
        'unique_files': 0,
        'files_to_remove': 0,
        'removed_files': [],
        'errors': []
    }
    for subdir in subdirs:
        print(f"Cleaning directory: {subdir}")
        stats = remove_duplicate_files(
            subdir,
            dry_run=dry_run,
            size=size,
            keep_oldest=keep_oldest,
            verbose=verbose,
        )
        total_stats['total_files'] += stats['total_files']
        total_stats['unique_files'] += stats['unique_files']
        total_stats['files_to_remove'] += stats['files_to_remove']
        total_stats['removed_files'].extend(stats['removed_files'])
        total_stats['errors'].extend(stats['errors'])

    # Print overall summary
    print(f"Cleaning complete.")
    print(f"Directories processed: {len(subdirs):,}")
    print(f"Total files scanned: {total_stats['total_files']:,}")
    print(f"Total unique files: {total_stats['unique_files']:,}")
    print(f"Total files marked for removal: {total_stats['files_to_remove']:,}")
    if total_stats['errors']:
        print(f"Total errors encountered: {len(total_stats['errors'])}")


# === Test ===
if __name__ == '__main__':

    import gc

    # Define states with COAs.
    STATES_WITH_COAS = {
        'ca': {'name': 'California'},
        'fl': {'name': 'Florida'},
        'az': {'name': 'Arizona'},
        # 'ny': {'name': 'New York'},
        # 'mo': {'name': 'Missouri'},
    }

    # Parse COAs for each state.
    for state, state_data in STATES_WITH_COAS.items():

        # Define the paths.
        print(f'Parsing COAs: {state}')
        state_name = state_data['name'].lower().replace(' ', '-')
        cache_path = f'D://data/.cache/results-{state}.jsonl'
        pdf_dir = f'D://data/{state_name}/results/pdfs'

        # Clean the directory.
        # clean_directory(pdf_dir, dry_run=False, verbose=True)

        # Parse the results.
        parse_coas(
            cache_path=cache_path,
            pdf_dir=pdf_dir,
            reverse=True,
            log_dir='D://data/.logs',
        )
        print(f'Completed parsing COAs for state: {state}')

    # Complete parsing.
    print('=== Completed all parsing COAs. ===')


    # DEBUG: Example
    # parser = CoADoc()
    # pdf_hash = '50b2085bb3747958f004f7efcd8df7b088d60a687d422e76e4eabc92ba64de7d'
    # pdf = f'D://data/new-york/results/pdfs\\cannabis-realm\\{pdf_hash}.pdf'
    # coa_data = parser.parse_pdf(pdf, verbose=True)
    # pdfplumber.open(pdf)
