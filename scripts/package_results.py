"""
Package Cannabis Results Dataset
=================================
Cannlytics | Copyright (c) 2026 Cannlytics

Final packaging script for data delivery. Assembles all deliverables
into a dated ZIP archive, then verifies the archive contents match
expectations. Designed to run after ``test_results.py`` passes.

Pipeline Position:
    Stage 5 of 5 -- Packaging (runs AFTER test_results.py)

    agg_results → qc_results → create_results_dataset → test_results → **package_results**

Usage:
    python package_results.py [--date YYYY-MM-DD] [--output-dir PATH] [--verify-only PATH]

Deliverables included:
    cannlytics-cannabis-results-YYYY-MM-DD/
    ├── cannabis-results.csv
    ├── cannabis-results-data-dictionary.pdf
    ├── cannabis-results-statistics.json
    ├── cannabis-results-statistics.md
    ├── README.md
    └── MANIFEST.txt

Exit codes:
    0  Package created and verified successfully
    1  Error during packaging or verification
"""
# Standard imports.
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime

# External imports.
import pandas as pd


# ── Configuration ────────────────────────────────────────────────────────────

# Source file paths (relative to this script's expected location in the repo).
DEFAULT_SOURCES = {
    'csv':              '../output/cannabis-results-latest.csv',
    'data_dictionary':  '../documents/build/cannabis-results-data-dictionary.pdf',
    'statistics_json':  '../output/cannabis-results-statistics.json',
    'statistics_md':    '../output/cannabis-results-statistics.md',
    'readme':           '../README.md',
}

# Delivery directory (relative to this script's expected location).
DEFAULT_OUTPUT_DIR = '../delivery'

# Expected schema for verification.
EXPECTED_COLUMNS = 44
EXPECTED_MIN_RECORDS = 500


# ── Packaging Functions ─────────────────────────────────────────────────────

def compute_sha256(filepath: str) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def format_size(size_bytes: int) -> str:
    """Format file size in human-readable form."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f'{size_bytes:.1f} {unit}'
        size_bytes /= 1024
    return f'{size_bytes:.1f} TB'


def resolve_sources(sources: dict, script_dir: str) -> dict:
    """Resolve source paths relative to the script directory."""
    resolved = {}
    for key, path in sources.items():
        abs_path = os.path.normpath(os.path.join(script_dir, path))
        resolved[key] = abs_path
    return resolved


def generate_manifest(
    delivery_dir: str,
    file_mapping: dict,
    delivery_date: str,
) -> str:
    """Generate a MANIFEST.txt with file checksums and metadata.

    Returns the path to the created manifest file.
    """
    lines = [
        'CANNLYTICS CANNABIS RESULTS — DELIVERY MANIFEST',
        f'Date: {delivery_date}',
        f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
        '',
        'Files:',
    ]
    for key, dest_name in file_mapping.items():
        filepath = os.path.join(delivery_dir, dest_name)
        if os.path.exists(filepath):
            size = format_size(os.path.getsize(filepath))
            sha = compute_sha256(filepath)
            lines.append(f'  {dest_name}')
            lines.append(f'    Size:   {size}')
            lines.append(f'    SHA256: {sha}')
    lines.append('')

    manifest_path = os.path.join(delivery_dir, 'MANIFEST.txt')
    with open(manifest_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return manifest_path


def create_package(
    sources: dict,
    output_dir: str,
    delivery_date: str,
) -> str:
    """
    Create the delivery package.

    Returns the path to the created ZIP file.
    """
    # Validate all source files exist.
    print('  Checking source files...')
    missing = []
    for name, path in sources.items():
        if not os.path.exists(path):
            missing.append(f'  {name}: {path}')
        else:
            size = format_size(os.path.getsize(path))
            print(f'    ✅ {name}: {path} ({size})')

    if missing:
        print('\n  ❌ Missing source files:')
        for m in missing:
            print(f'    {m}')
        raise FileNotFoundError('One or more source files are missing.')

    # Create the delivery directory.
    folder_name = f'cannlytics-cannabis-results-{delivery_date}'
    delivery_dir = os.path.join(output_dir, folder_name)
    os.makedirs(delivery_dir, exist_ok=True)

    # File mapping: source key -> destination filename.
    file_mapping = {
        'csv':              'cannabis-results.csv',
        'data_dictionary':  'cannabis-results-data-dictionary.pdf',
        'statistics_json':  'cannabis-results-statistics.json',
        'statistics_md':    'cannabis-results-statistics.md',
        'readme':           'README.md',
    }

    # Copy files to delivery directory.
    print('\n  Copying deliverables...')
    for key, dest_name in file_mapping.items():
        src = sources[key]
        dest = os.path.join(delivery_dir, dest_name)
        shutil.copy2(src, dest)
        size = format_size(os.path.getsize(dest))
        sha = compute_sha256(dest)[:12]
        print(f'    📄 {dest_name} ({size}) sha256:{sha}...')

    # Generate manifest with file checksums.
    manifest_path = generate_manifest(
        delivery_dir, file_mapping, delivery_date,
    )
    print(f'    📋 MANIFEST.txt')

    # Create the ZIP archive.
    zip_filename = f'{folder_name}.zip'
    zip_path = os.path.join(output_dir, zip_filename)

    print(f'\n  Creating ZIP archive: {zip_filename}')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for key, dest_name in file_mapping.items():
            file_path = os.path.join(delivery_dir, dest_name)
            arcname = os.path.join(folder_name, dest_name)
            zf.write(file_path, arcname)
        # Include the manifest.
        zf.write(manifest_path, os.path.join(folder_name, 'MANIFEST.txt'))

    zip_size = format_size(os.path.getsize(zip_path))
    zip_sha = compute_sha256(zip_path)
    print(f'    📦 {zip_filename} ({zip_size})')
    print(f'    🔒 SHA-256: {zip_sha}')

    # Clean up the unzipped delivery directory (ZIP is the deliverable).
    shutil.rmtree(delivery_dir)
    print(f'\n  Cleaned up temporary directory: {delivery_dir}')

    return zip_path


# ── Verification Functions ───────────────────────────────────────────────────

def verify_package(zip_path: str) -> bool:
    """
    Extract and verify the ZIP archive contents.

    Returns True if all checks pass, False otherwise.
    """
    print(f'\n  Verifying: {zip_path}')
    errors = []

    # Check ZIP integrity.
    if not zipfile.is_zipfile(zip_path):
        print('  ❌ Not a valid ZIP file')
        return False

    with zipfile.ZipFile(zip_path, 'r') as zf:
        # Check for ZIP corruption.
        bad = zf.testzip()
        if bad is not None:
            print(f'  ❌ Corrupt file in ZIP: {bad}')
            return False

        # List contents.
        names = zf.namelist()
        print(f'  Archive contains {len(names)} files:')
        for n in names:
            info = zf.getinfo(n)
            size = format_size(info.file_size)
            print(f'    • {n} ({size})')

    # Extract to temp directory for verification.
    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(tmpdir)

        # Find the delivery folder.
        folders = [d for d in os.listdir(tmpdir)
                   if os.path.isdir(os.path.join(tmpdir, d))]
        if len(folders) != 1:
            errors.append(f'Expected 1 top-level folder, found {len(folders)}')
            if not folders:
                print(f'  ❌ {errors[-1]}')
                return False

        delivery_dir = os.path.join(tmpdir, folders[0])

        # Check expected files.
        expected_files = {
            'cannabis-results.csv',
            'cannabis-results-data-dictionary.pdf',
            'cannabis-results-statistics.json',
            'cannabis-results-statistics.md',
            'README.md',
            'MANIFEST.txt',
        }
        actual_files = set(os.listdir(delivery_dir))
        missing = expected_files - actual_files
        extra = actual_files - expected_files

        if missing:
            errors.append(f'Missing files: {sorted(missing)}')
        if extra:
            errors.append(f'Unexpected files: {sorted(extra)}')

        # Verify CSV.
        csv_path = os.path.join(delivery_dir, 'cannabis-results.csv')
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path, dtype=str,
                                 keep_default_na=False)

                # Record count.
                if len(df) < EXPECTED_MIN_RECORDS:
                    errors.append(
                        f'CSV has {len(df):,} records'
                        f' (expected ≥ {EXPECTED_MIN_RECORDS:,})')
                else:
                    print(f'  ✅ Records: {len(df):,}')

                # Column count.
                if len(df.columns) != EXPECTED_COLUMNS:
                    errors.append(
                        f'CSV has {len(df.columns)} columns'
                        f' (expected {EXPECTED_COLUMNS})')
                else:
                    print(f'  ✅ Columns: {len(df.columns)}')

                # State coverage.
                if 'state' in df.columns:
                    states = sorted(
                        df['state'].loc[df['state'] != ''].unique())
                    print(f'  ✅ States: {len(states)}'
                          f' ({", ".join(states)})')

                # Unique labs.
                if 'lab' in df.columns:
                    n_labs = df['lab'].loc[df['lab'] != ''].nunique()
                    print(f'  ✅ Unique labs: {n_labs:,}')

                # Unique producers.
                if 'producer' in df.columns:
                    n_producers = (
                        df['producer']
                        .loc[df['producer'] != '']
                        .nunique()
                    )
                    print(f'  ✅ Unique producers: {n_producers:,}')

                # THC coverage.
                if 'total_thc' in df.columns:
                    thc_cov = (df['total_thc'] != '').mean()
                    print(f'  ✅ THC coverage: {thc_cov:.1%}')

                # Analysis coverage.
                if 'analyses' in df.columns:
                    analyses_cov = (
                        (df['analyses'] != '')
                        & (df['analyses'] != '[]')
                    ).mean()
                    print(f'  ✅ Analysis coverage: {analyses_cov:.1%}')

                # Total analyte measurements.
                if 'results' in df.columns:
                    total_analytes = 0
                    for val in df['results']:
                        if val and val not in ('', '[]'):
                            try:
                                parsed = json.loads(val)
                                if isinstance(parsed, list):
                                    total_analytes += len(parsed)
                            except (json.JSONDecodeError, TypeError):
                                pass
                    print(f'  ✅ Total analyte measurements:'
                          f' {total_analytes:,}')

            except Exception as e:
                errors.append(f'CSV verification error: {e}')
        else:
            errors.append('CSV file not found in archive')

        # Verify data dictionary PDF.
        dd_name = 'cannabis-results-data-dictionary.pdf'
        dd_path = os.path.join(delivery_dir, dd_name)
        if os.path.exists(dd_path):
            size = os.path.getsize(dd_path)
            if size < 1000:
                errors.append(
                    f'{dd_name} is suspiciously small ({size} bytes)')
            else:
                print(f'  ✅ {dd_name}: {format_size(size)}')
        else:
            errors.append(f'{dd_name} not found')

        # Verify statistics JSON.
        stats_json_name = 'cannabis-results-statistics.json'
        stats_json_path = os.path.join(delivery_dir, stats_json_name)
        if os.path.exists(stats_json_path):
            try:
                with open(stats_json_path, 'r', encoding='utf-8') as f:
                    stats = json.load(f)
                if not isinstance(stats, dict):
                    errors.append(
                        f'{stats_json_name}: root is not a JSON object')
                elif 'summary' not in stats:
                    errors.append(
                        f'{stats_json_name}: missing "summary" key')
                else:
                    total = stats['summary'].get('total_records', 'N/A')
                    print(f'  ✅ {stats_json_name}: valid'
                          f' (total_records: {total})')
            except json.JSONDecodeError as e:
                errors.append(f'{stats_json_name}: invalid JSON ({e})')
        else:
            errors.append(f'{stats_json_name} not found')

        # Verify statistics Markdown.
        stats_md_name = 'cannabis-results-statistics.md'
        stats_md_path = os.path.join(delivery_dir, stats_md_name)
        if os.path.exists(stats_md_path):
            size = os.path.getsize(stats_md_path)
            if size < 100:
                errors.append(
                    f'{stats_md_name} is suspiciously small'
                    f' ({size} bytes)')
            else:
                print(f'  ✅ {stats_md_name}: {format_size(size)}')
        else:
            errors.append(f'{stats_md_name} not found')

        # Verify README.
        readme_path = os.path.join(delivery_dir, 'README.md')
        if os.path.exists(readme_path):
            size = os.path.getsize(readme_path)
            if size < 100:
                errors.append(
                    f'README.md is suspiciously small ({size} bytes)')
            else:
                print(f'  ✅ README.md: {format_size(size)}')
        else:
            errors.append('README.md not found')

        # Verify manifest.
        manifest_path = os.path.join(delivery_dir, 'MANIFEST.txt')
        if os.path.exists(manifest_path):
            size = os.path.getsize(manifest_path)
            print(f'  ✅ MANIFEST.txt: {format_size(size)}')
        else:
            errors.append('MANIFEST.txt not found')

    # Summary.
    print()
    if errors:
        print('  ❌ VERIFICATION FAILED:')
        for e in errors:
            print(f'     • {e}')
        return False
    else:
        print('  ✅ VERIFICATION PASSED — Package is ready for delivery.')
        return True


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Package the Cannlytics Cannabis Results dataset'
                    ' for delivery.',
    )
    parser.add_argument(
        '--date', default=None,
        help='Delivery date in YYYY-MM-DD format (default: today).',
    )
    parser.add_argument(
        '--output-dir', default=None,
        help=f'Output directory for the package'
             f' (default: {DEFAULT_OUTPUT_DIR}).',
    )
    parser.add_argument(
        '--verify-only', default=None, metavar='ZIP',
        help='Only verify an existing ZIP package (skip creation).',
    )
    parser.add_argument(
        '--csv', default=None,
        help='Override path to the CSV file.',
    )
    parser.add_argument(
        '--data-dictionary', default=None,
        help='Override path to the data dictionary PDF.',
    )
    parser.add_argument(
        '--statistics-json', default=None,
        help='Override path to the statistics JSON file.',
    )
    parser.add_argument(
        '--statistics-md', default=None,
        help='Override path to the statistics Markdown file.',
    )
    parser.add_argument(
        '--readme', default=None,
        help='Override path to the README.md.',
    )
    args = parser.parse_args()

    # Resolve delivery date.
    if args.date:
        delivery_date = args.date
    else:
        delivery_date = datetime.now().strftime('%Y-%m-%d')

    print('=' * 72)
    print('  CANNLYTICS CANNABIS RESULTS — DELIVERY PACKAGING')
    print(f'  Date: {delivery_date}')
    print(f'  Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print('=' * 72)
    print()

    # Verify-only mode.
    if args.verify_only:
        if not os.path.exists(args.verify_only):
            print(f'  ERROR: ZIP not found: {args.verify_only}')
            sys.exit(1)
        success = verify_package(args.verify_only)
        sys.exit(0 if success else 1)

    # Resolve source paths.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sources = resolve_sources(DEFAULT_SOURCES, script_dir)

    # Apply overrides.
    if args.csv:
        sources['csv'] = os.path.abspath(args.csv)
    if args.data_dictionary:
        sources['data_dictionary'] = os.path.abspath(args.data_dictionary)
    if args.statistics_json:
        sources['statistics_json'] = os.path.abspath(args.statistics_json)
    if args.statistics_md:
        sources['statistics_md'] = os.path.abspath(args.statistics_md)
    if args.readme:
        sources['readme'] = os.path.abspath(args.readme)

    # Resolve output directory.
    output_dir = args.output_dir or os.path.normpath(
        os.path.join(script_dir, DEFAULT_OUTPUT_DIR)
    )
    os.makedirs(output_dir, exist_ok=True)

    # Create the package.
    try:
        zip_path = create_package(sources, output_dir, delivery_date)
    except FileNotFoundError:
        print('\n  ████ PACKAGING FAILED ████')
        print('  Resolve missing files and try again.')
        sys.exit(1)

    # Verify the package.
    success = verify_package(zip_path)

    if success:
        print()
        print('  ┌──────────────────────────────────────────────────────────┐')
        print(f'  │  📦 PACKAGE READY: {os.path.basename(zip_path):<38} │')
        print(f'  │  📂 Location: {output_dir:<43} │')
        print(f'  │  📅 Delivery Date: {delivery_date:<37} │')
        print('  └──────────────────────────────────────────────────────────┘')
        print()
    else:
        print('\n  ████ VERIFICATION FAILED ████')
        print('  Review errors above before delivering.')

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()