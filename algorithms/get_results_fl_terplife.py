"""
Get Results | Florida | TerpLife Labs
Copyright (c) 2023-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 5/18/2023
Updated: 2/22/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Archive and catalog cannabis lab result COA PDFs published by
    TerpLife Labs in Florida.

    TerpLife Labs publishes their COA archive via a public Google Drive
    folder containing thousands of numbered PDF files. Their website
    search feature is acknowledged as broken. This collector focuses on:

    1. Merging ZIP archives downloaded from Google Drive into a
       canonical local directory.
    2. Building and maintaining a manifest (catalog) of all collected
       COA PDFs with deduplication by filename and content hash.
    3. Converting the manifest into standardized LabResult records.

    The actual extraction of cannabinoid/terpene/safety data from
    the PDFs is handled downstream by the COA parser (parse_coas.py).

Data Sources:
    - [TerpLife Labs COA Page](https://www.terplifelabs.com/coa/)
    - [TerpLife Labs Google Drive Archive](https://drive.google.com/drive/folders/16ZS02AaGwRTGwTpGYf9Zsq1PHqJdrAOR)

Output:
    - Manifest CSV cataloging all collected COA PDFs
    - Deduplicated PDF directory
    - Standardized CSV with LabResult schema fields

Usage:
    ```python
    from algorithms.get_results_fl_terplife import TerpLifeLabsCollector

    with TerpLifeLabsCollector() as collector:
        results = collector.get_results()
    ```

Command Line:
    ```bash
    # Run unit tests
    python algorithms/get_results_fl_terplife.py --test

    # Merge ZIP archives, catalog, and produce results
    python algorithms/get_results_fl_terplife.py

    # Merge specific archive directory
    python algorithms/get_results_fl_terplife.py --archive-dir "D:/data/florida/results/pdfs"

    # Only catalog existing PDFs (no archive merge)
    python algorithms/get_results_fl_terplife.py --catalog-only

    # Run integration test against live Google Drive
    python algorithms/get_results_fl_terplife.py --integration-test
    ```
"""
# Standard imports:
from datetime import datetime
import glob
import hashlib
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# External imports:
import pandas as pd

# Internal imports:
try:
    from config.results_config import PATHS, SOURCE_CONFIG
    from config.results_schema import LabResult, normalize_product_type
    from results_base import COACollector
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from config.results_config import PATHS, SOURCE_CONFIG
        from config.results_schema import LabResult, normalize_product_type
        from results_base import COACollector
    except ImportError:
        PATHS = None
        SOURCE_CONFIG = {}
        LabResult = None
        normalize_product_type = None
        COACollector = None


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Constants                                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

# TerpLife Labs website and archive URLs.
WEBSITE_URL = 'https://www.terplifelabs.com/coa/'
GDRIVE_FOLDER_URL = 'https://drive.google.com/drive/folders/16ZS02AaGwRTGwTpGYf9Zsq1PHqJdrAOR'
GDRIVE_FOLDER_ID = '16ZS02AaGwRTGwTpGYf9Zsq1PHqJdrAOR'

# TerpLife Labs producer metadata (from COA certificates).
TERPLIFE_PRODUCER = {
    'lab': 'TerpLife Labs',
    'lab_address': '4060 NW 126th Ave Suite 201, Coral Springs, FL 33065',
    'lab_city': 'Coral Springs',
    'lab_county': 'Broward',
    'lab_state': 'fl',
    'lab_zipcode': '33065',
    'lab_latitude': 26.2898,
    'lab_longitude': -80.2707,
    'lab_phone': '(833) 837-7543',
    'lab_website': 'https://www.terplifelabs.com',
    'lab_image_url': 'https://www.terplifelabs.com/wp-content/uploads/2023/01/logo.png',
}

# Default HTTP headers.
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/131.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

# File patterns for COA PDFs.
COA_PDF_PATTERN = re.compile(r'^[\w\-\.\s\(\)]+\.pdf$', re.IGNORECASE)

# Archive ZIP pattern (from Google Drive download).
# e.g., COAS-20260222T184510Z-1-010.zip
ARCHIVE_ZIP_PATTERN = re.compile(r'COAS.*\.zip$', re.IGNORECASE)

# Manifest column names.
MANIFEST_COLUMNS = [
    'file_name',
    'sample_id',
    'file_path',
    'file_size',
    'file_hash',
    'date_cataloged',
    'source',
]


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Static Helpers                                                    ║
# ╚══════════════════════════════════════════════════════════════════╝

def _generate_result_id(file_name: str) -> str:
    """Generate a deterministic 16-char hex ID from a filename.

    Args:
        file_name: The COA PDF filename.

    Returns:
        A 16-character hexadecimal string.
    """
    data = (file_name or '').encode('utf-8')
    return hashlib.sha256(data).hexdigest()[:16]


def _extract_sample_id(file_name: str) -> str:
    """Extract a sample ID from a COA PDF filename.

    TerpLife COAs are typically named with numeric IDs:
      - '17235.pdf' → '17235'
      - 'TL-2024-001.pdf' → 'TL-2024-001'
      - 'Sample 123 (retest).pdf' → 'Sample 123 (retest)'

    Args:
        file_name: The PDF filename.

    Returns:
        The sample ID (filename without extension).
    """
    if not file_name:
        return ''
    # Strip whitespace first, then remove .pdf extension.
    name = file_name.strip()
    name = re.sub(r'\.pdf$', '', name, flags=re.IGNORECASE)
    return name.strip()


def _hash_file(filepath: str, chunk_size: int = 65536) -> str:
    """Compute SHA256 hash of a file's contents.

    Reads the file in chunks for memory efficiency with large PDFs.

    Args:
        filepath: Path to the file.
        chunk_size: Bytes to read per chunk (default 64KB).

    Returns:
        Hex-encoded SHA256 hash string.
    """
    h = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
    except (OSError, IOError):
        return ''
    return h.hexdigest()


def _is_valid_pdf(filepath: str) -> bool:
    """Check if a file is a valid PDF by reading its magic bytes.

    Args:
        filepath: Path to the file.

    Returns:
        True if the file starts with the PDF magic bytes (%PDF).
    """
    try:
        with open(filepath, 'rb') as f:
            header = f.read(5)
        return header.startswith(b'%PDF-')
    except (OSError, IOError):
        return False


def _find_zip_files(search_dir: str) -> List[str]:
    """Recursively find ZIP files in a directory.

    Matches both the COAS archive naming pattern and generic .zip files.

    Args:
        search_dir: Directory to search.

    Returns:
        Sorted list of absolute paths to ZIP files.
    """
    zips = []
    for root, dirs, files in os.walk(search_dir):
        for f in files:
            if f.lower().endswith('.zip'):
                zips.append(os.path.join(root, f))
    return sorted(zips)


def _find_pdf_files(search_dir: str) -> List[str]:
    """Recursively find PDF files in a directory.

    Args:
        search_dir: Directory to search.

    Returns:
        Sorted list of absolute paths to PDF files.
    """
    pdfs = []
    for root, dirs, files in os.walk(search_dir):
        for f in files:
            if f.lower().endswith('.pdf'):
                pdfs.append(os.path.join(root, f))
    return sorted(pdfs)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Collector                                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

class TerpLifeLabsCollector(COACollector if COACollector else object):
    """Collector for TerpLife Labs COA PDFs.

    Manages a local archive of TerpLife Labs COA PDFs sourced from
    their public Google Drive folder and (historically) from their
    website search interface.

    The collector operates in three phases:
        1. **Merge**: Extract ZIP archives into the canonical PDF directory
        2. **Catalog**: Build a manifest of all PDFs with deduplication
        3. **Convert**: Transform the manifest into LabResult records

    Attributes:
        state: Always 'fl' for Florida.
        source: Always 'terplife_labs'.
        manifest_path: Path to the manifest CSV.

    Example:
        ```python
        with TerpLifeLabsCollector() as collector:
            # Merge new ZIP downloads and catalog all PDFs
            results = collector.get_results(
                archive_dir='D:/data/florida/results/pdfs'
            )
            print(f"Cataloged {len(results)} COA PDFs")
        ```
    """

    def __init__(
            self,
            data_dir: Optional[str] = None,
            pdf_dir: Optional[str] = None,
            cache_path: Optional[str] = None,
            log_dir: Optional[str] = None,
            log_name: Optional[str] = None,
            pause_time: Optional[float] = None,
            verbose: bool = True,
        ):
        """Initialize the TerpLife Labs collector.

        Args:
            data_dir: Override for data directory.
            pdf_dir: Override for PDF storage directory.
            cache_path: Override for cache file path.
            log_dir: Override for log directory.
            log_name: Override for log file name.
            pause_time: Seconds between operations (default: 1.0).
            verbose: Enable verbose logging.
        """
        source_config = SOURCE_CONFIG.get('terplife_labs', {}) if SOURCE_CONFIG else {}
        default_pause = source_config.get('pause_time', 1.0)

        if COACollector is not None:
            super().__init__(
                state='fl',
                source='terplife_labs',
                data_dir=data_dir,
                pdf_dir=pdf_dir,
                cache_path=cache_path,
                log_dir=log_dir,
                log_name=log_name or 'get_results_fl_terplife',
                pause_time=pause_time or default_pause,
                verbose=verbose,
            )
        else:
            # Standalone mode without base class.
            self.data_dir = Path(data_dir) if data_dir else Path('.')
            self.pdf_dir = Path(pdf_dir) if pdf_dir else self.data_dir / 'pdfs' / 'terplife'
            self.datasets_dir = self.data_dir / 'datasets'
            self.verbose = verbose
            self.pause_time = pause_time or default_pause
            os.makedirs(self.pdf_dir, exist_ok=True)
            os.makedirs(self.datasets_dir, exist_ok=True)

            import logging
            self.logger = logging.getLogger(log_name or 'get_results_fl_terplife')
            if not self.logger.handlers:
                handler = logging.StreamHandler()
                handler.setFormatter(logging.Formatter(
                    '%(asctime)s | %(name)s | %(levelname)s | %(message)s'
                ))
                self.logger.addHandler(handler)
                self.logger.setLevel(logging.INFO if verbose else logging.WARNING)

        # Manifest path — lives alongside the datasets.
        datasets_dir = getattr(self, 'datasets_dir', self.data_dir)
        self.manifest_path = os.path.join(str(datasets_dir), 'terplife-manifest.csv')

    def __enter__(self):
        """Support context manager protocol."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up on context exit."""
        return False

    # ── Archive Merging ──────────────────────────────────────────

    def merge_archives(
            self,
            archive_dir: str,
            zip_pattern: str = 'COAS',
            dir_pattern: str = 'COAS',
            delete_after_extract: bool = False,
        ) -> Dict[str, int]:
        """Extract ZIP archives and merge PDFs into the canonical directory.

        SAFETY: This method is source-aware. It will ONLY process:
            1. ZIP files whose names contain `zip_pattern` (default: 'COAS')
            2. Extracted directories whose names contain `dir_pattern` (default: 'COAS')

        This prevents cross-contamination with COAs from other sources
        (Flowery, Jungle Boys, Kaycha, etc.) that may live in sibling
        directories.

        For your directory layout:
            D:\\data\\florida\\results\\pdfs\\
            ├── COAS-*.zip              ← ✅ Processed (ZIP pattern matches)
            ├── COAS-*-010/COAS/        ← ✅ Processed (dir pattern matches)
            ├── terplife/               ← ✅ This is pdf_dir (destination)
            ├── flowery/                ← 🚫 SKIPPED (not a COAS directory)
            ├── jungleboys/             ← 🚫 SKIPPED (not a COAS directory)
            ├── kaycha/                 ← 🚫 SKIPPED (not a COAS directory)
            ├── FLMedicalTrees/         ← 🚫 SKIPPED (not a COAS directory)
            └── MMTC-2015-0001/         ← 🚫 SKIPPED (not a COAS directory)

        Args:
            archive_dir: Directory containing ZIP files and/or
                extracted archive folders.
            zip_pattern: Only process ZIP files whose names contain
                this string (case-insensitive). Default: 'COAS'.
                Set to '' to process ALL ZIPs (use with caution).
            dir_pattern: Only scan subdirectories whose names contain
                this string for loose PDFs (case-insensitive).
                Default: 'COAS'. Set to '' to scan ALL dirs (DANGEROUS).
            delete_after_extract: If True, delete ZIP files after
                successful extraction.

        Returns:
            Dictionary with merge statistics:
                - zips_found: Number of matching ZIP files discovered
                - zips_extracted: Number successfully extracted
                - zips_skipped_pattern: ZIPs skipped (didn't match pattern)
                - pdfs_found: Total PDFs found across matching archives
                - pdfs_new: PDFs that were new (not already in pdf_dir)
                - pdfs_skipped: PDFs skipped (already existed)
                - pdfs_invalid: Files that weren't valid PDFs
                - dirs_scanned: Directories scanned for loose PDFs
                - dirs_skipped: Directories skipped (didn't match pattern)
        """
        stats = {
            'zips_found': 0,
            'zips_extracted': 0,
            'zips_skipped_pattern': 0,
            'pdfs_found': 0,
            'pdfs_new': 0,
            'pdfs_skipped': 0,
            'pdfs_invalid': 0,
            'dirs_scanned': 0,
            'dirs_skipped': 0,
        }

        archive_path = Path(archive_dir)
        if not archive_path.exists():
            self.logger.warning(f'Archive directory not found: {archive_dir}')
            return stats

        pdf_dir = Path(str(self.pdf_dir))
        os.makedirs(pdf_dir, exist_ok=True)

        # Build a set of existing filenames for fast lookup.
        existing_files = set()
        for f in os.listdir(pdf_dir):
            if f.lower().endswith('.pdf'):
                existing_files.add(f.lower())

        # ── Phase 1: Extract matching ZIP files ──────────────────

        all_zips = _find_zip_files(str(archive_path))
        if zip_pattern:
            matching_zips = [
                z for z in all_zips
                if zip_pattern.lower() in os.path.basename(z).lower()
            ]
            stats['zips_skipped_pattern'] = len(all_zips) - len(matching_zips)
            if stats['zips_skipped_pattern'] > 0:
                self.logger.info(
                    f'Skipped {stats["zips_skipped_pattern"]} ZIP(s) not '
                    f'matching pattern "{zip_pattern}"'
                )
        else:
            matching_zips = all_zips

        stats['zips_found'] = len(matching_zips)
        self.logger.info(
            f'Found {len(matching_zips)} matching ZIP file(s) in {archive_dir}'
        )

        for zip_path in matching_zips:
            try:
                with zipfile.ZipFile(zip_path, 'r') as zf:
                    # Only extract PDF files, skip __MACOSX junk.
                    pdf_members = [
                        m for m in zf.namelist()
                        if m.lower().endswith('.pdf')
                        and not m.startswith('__MACOSX')
                    ]
                    self.logger.info(
                        f'Extracting {len(pdf_members)} PDFs from '
                        f'{os.path.basename(zip_path)}'
                    )
                    for member in pdf_members:
                        base_name = os.path.basename(member)
                        if not base_name:
                            continue
                        dest = pdf_dir / base_name
                        if base_name.lower() in existing_files:
                            stats['pdfs_skipped'] += 1
                            continue
                        # Extract the member directly into pdf_dir.
                        with zf.open(member) as src:
                            with open(dest, 'wb') as dst:
                                shutil.copyfileobj(src, dst)
                        existing_files.add(base_name.lower())
                        stats['pdfs_new'] += 1
                        stats['pdfs_found'] += 1

                stats['zips_extracted'] += 1

                if delete_after_extract:
                    os.remove(zip_path)
                    self.logger.info(f'Deleted: {zip_path}')

            except (zipfile.BadZipFile, OSError) as e:
                self.logger.error(f'Failed to extract {zip_path}: {e}')

        # ── Phase 2: Merge loose PDFs from MATCHING directories ──
        #
        # SAFETY: Only scan subdirectories whose names match the
        # dir_pattern. This prevents cross-contamination with COAs
        # from other Florida sources (Flowery, Jungle Boys, etc.)
        # that live in sibling directories.

        safe_dirs = []
        try:
            for entry in os.scandir(str(archive_path)):
                if not entry.is_dir():
                    continue
                # Never scan the destination directory itself.
                if Path(entry.path).resolve() == pdf_dir.resolve():
                    continue
                # Check if directory name matches the pattern.
                if dir_pattern and dir_pattern.lower() not in entry.name.lower():
                    stats['dirs_skipped'] += 1
                    self.logger.debug(
                        f'Skipped directory (no pattern match): {entry.name}'
                    )
                    continue
                safe_dirs.append(entry.path)
                stats['dirs_scanned'] += 1
        except OSError as e:
            self.logger.error(f'Failed to scan {archive_dir}: {e}')

        if stats['dirs_skipped'] > 0:
            self.logger.info(
                f'Skipped {stats["dirs_skipped"]} non-COAS director(ies) '
                f'(flowery, jungleboys, kaycha, etc.)'
            )

        for safe_dir in safe_dirs:
            loose_pdfs = _find_pdf_files(safe_dir)
            self.logger.info(
                f'Found {len(loose_pdfs)} loose PDF(s) in {os.path.basename(safe_dir)}/'
            )

            for pdf_path in loose_pdfs:
                base_name = os.path.basename(pdf_path)
                stats['pdfs_found'] += 1

                if base_name.lower() in existing_files:
                    stats['pdfs_skipped'] += 1
                    continue

                # Validate it's actually a PDF.
                if not _is_valid_pdf(pdf_path):
                    stats['pdfs_invalid'] += 1
                    self.logger.warning(f'Invalid PDF skipped: {pdf_path}')
                    continue

                # Copy to canonical directory.
                dest = pdf_dir / base_name
                try:
                    shutil.copy2(pdf_path, dest)
                    existing_files.add(base_name.lower())
                    stats['pdfs_new'] += 1
                except (OSError, IOError) as e:
                    self.logger.error(f'Failed to copy {pdf_path}: {e}')

        self.logger.info(
            f'Merge complete: {stats["pdfs_new"]} new, '
            f'{stats["pdfs_skipped"]} skipped, '
            f'{stats["pdfs_invalid"]} invalid, '
            f'{stats["dirs_scanned"]} dir(s) scanned, '
            f'{stats["dirs_skipped"]} dir(s) skipped (other sources)'
        )
        return stats

    # ── PDF Cataloging ───────────────────────────────────────────

    def catalog_pdfs(
            self,
            compute_hashes: bool = True,
            incremental: bool = True,
        ) -> pd.DataFrame:
        """Build or update a manifest of all COA PDFs in the archive.

        Scans `self.pdf_dir` for PDF files and records metadata for
        each one. If `incremental` is True and a manifest already exists,
        only new files (not in the existing manifest) are cataloged.

        Args:
            compute_hashes: If True, compute SHA256 hashes for each
                file (slower but enables content deduplication).
            incremental: If True, only catalog files not already in
                the manifest. If False, rebuild from scratch.

        Returns:
            DataFrame with manifest columns:
                file_name, sample_id, file_path, file_size,
                file_hash, date_cataloged, source
        """
        pdf_dir = Path(str(self.pdf_dir))
        if not pdf_dir.exists():
            self.logger.warning(f'PDF directory not found: {pdf_dir}')
            return pd.DataFrame(columns=MANIFEST_COLUMNS)

        # Load existing manifest if incremental.
        existing_manifest = pd.DataFrame(columns=MANIFEST_COLUMNS)
        if incremental and os.path.exists(self.manifest_path):
            try:
                existing_manifest = pd.read_csv(self.manifest_path)
                self.logger.info(
                    f'Loaded existing manifest: {len(existing_manifest)} entries'
                )
            except Exception as e:
                self.logger.warning(f'Failed to load manifest: {e}')

        known_files = set(existing_manifest['file_name'].tolist()) \
            if not existing_manifest.empty else set()

        # Scan for PDFs.
        all_pdfs = _find_pdf_files(str(pdf_dir))
        self.logger.info(f'Found {len(all_pdfs)} PDF(s) in {pdf_dir}')

        new_entries = []
        now = datetime.now().isoformat()

        for pdf_path in all_pdfs:
            file_name = os.path.basename(pdf_path)

            if file_name in known_files:
                continue

            file_size = os.path.getsize(pdf_path)
            file_hash = _hash_file(pdf_path) if compute_hashes else ''
            sample_id = _extract_sample_id(file_name)

            new_entries.append({
                'file_name': file_name,
                'sample_id': sample_id,
                'file_path': pdf_path,
                'file_size': file_size,
                'file_hash': file_hash,
                'date_cataloged': now,
                'source': 'gdrive_archive',
            })

        if new_entries:
            new_df = pd.DataFrame(new_entries, columns=MANIFEST_COLUMNS)
            manifest = pd.concat(
                [existing_manifest, new_df],
                ignore_index=True,
            )
            self.logger.info(f'Cataloged {len(new_entries)} new PDF(s)')
        else:
            manifest = existing_manifest
            self.logger.info('No new PDFs to catalog')

        # Deduplicate by file_name (keep first occurrence).
        before = len(manifest)
        manifest.drop_duplicates(subset=['file_name'], keep='first', inplace=True)
        if len(manifest) < before:
            self.logger.info(
                f'Removed {before - len(manifest)} duplicate filename(s)'
            )

        # Deduplicate by file_hash if hashes are available.
        if compute_hashes and 'file_hash' in manifest.columns:
            hashed = manifest[manifest['file_hash'] != '']
            if not hashed.empty:
                before = len(manifest)
                # Keep the entry with the shorter filename (likely canonical).
                manifest = manifest.sort_values(
                    'file_name', key=lambda s: s.str.len()
                )
                manifest.drop_duplicates(
                    subset=['file_hash'], keep='first', inplace=True
                )
                manifest.sort_values('file_name', inplace=True)
                if len(manifest) < before:
                    self.logger.info(
                        f'Removed {before - len(manifest)} content-duplicate(s)'
                    )

        manifest.reset_index(drop=True, inplace=True)

        # Save manifest.
        manifest.to_csv(self.manifest_path, index=False)
        self.logger.info(
            f'Manifest saved: {len(manifest)} entries → {self.manifest_path}'
        )

        return manifest

    # ── LabResult Conversion ─────────────────────────────────────

    def _convert_to_lab_results(
            self,
            manifest: pd.DataFrame,
        ) -> List[Dict]:
        """Convert a manifest of cataloged PDFs to LabResult records.

        Populates the fields that can be known from the filename and
        source metadata alone. Full cannabinoid/terpene/safety data
        requires downstream COA parsing.

        Args:
            manifest: DataFrame from catalog_pdfs().

        Returns:
            List of dictionaries matching the LabResult schema.
        """
        results = []

        for _, row in manifest.iterrows():
            file_name = row.get('file_name', '')
            sample_id = row.get('sample_id', '')
            result_id = _generate_result_id(file_name)

            if LabResult is not None:
                result = LabResult(
                    # Identifiers.
                    id=result_id,
                    sample_id=sample_id,

                    # Lab info.
                    lab=TERPLIFE_PRODUCER.get('lab'),
                    lab_address=TERPLIFE_PRODUCER.get('lab_address'),
                    lab_city=TERPLIFE_PRODUCER.get('lab_city'),
                    lab_county=TERPLIFE_PRODUCER.get('lab_county'),
                    lab_state=TERPLIFE_PRODUCER.get('lab_state'),
                    lab_zipcode=TERPLIFE_PRODUCER.get('lab_zipcode'),
                    lab_latitude=TERPLIFE_PRODUCER.get('lab_latitude'),
                    lab_longitude=TERPLIFE_PRODUCER.get('lab_longitude'),
                    lab_phone=TERPLIFE_PRODUCER.get('lab_phone'),

                    # COA info.
                    coa_url=GDRIVE_FOLDER_URL,
                    lab_results_url=GDRIVE_FOLDER_URL,

                    # Metadata.
                    state='fl',
                    source='terplife_labs',
                    date_collected=row.get('date_cataloged'),
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
                results.append(result.to_dict())
            else:
                # Fallback without LabResult schema.
                results.append({
                    'id': result_id,
                    'sample_id': sample_id,
                    'lab': TERPLIFE_PRODUCER.get('lab'),
                    'lab_address': TERPLIFE_PRODUCER.get('lab_address'),
                    'lab_city': TERPLIFE_PRODUCER.get('lab_city'),
                    'lab_state': TERPLIFE_PRODUCER.get('lab_state'),
                    'coa_url': GDRIVE_FOLDER_URL,
                    'state': 'fl',
                    'source': 'terplife_labs',
                    'date_collected': row.get('date_cataloged'),
                })

        return results

    # ── Main Collection Method ───────────────────────────────────

    def get_results(
            self,
            archive_dir: Optional[str] = None,
            catalog_only: bool = False,
            compute_hashes: bool = True,
            save_results: bool = True,
        ) -> pd.DataFrame:
        """Collect TerpLife Labs COA data.

        Orchestrates the full pipeline:
            1. Merge any new ZIP archives into pdf_dir
            2. Catalog all PDFs into a manifest
            3. Convert the manifest to standardized LabResult records

        Args:
            archive_dir: Directory containing ZIP files or extracted
                archive folders to merge. If None, skips merge step.
            catalog_only: If True, only catalog existing PDFs without
                merging archives or converting to LabResult.
            compute_hashes: Compute SHA256 hashes during cataloging.
            save_results: Save the final results CSV.

        Returns:
            DataFrame with standardized lab results (or manifest if
            catalog_only is True).
        """
        self.logger.info('Starting TerpLife Labs COA collection...')
        self.logger.info(f'PDF directory: {self.pdf_dir}')
        self.logger.info(f'Manifest: {self.manifest_path}')

        # Phase 1: Merge archives if provided.
        if archive_dir and not catalog_only:
            self.logger.info(f'Merging archives from: {archive_dir}')
            merge_stats = self.merge_archives(archive_dir)
            self.logger.info(f'Merge stats: {merge_stats}')

        # Phase 2: Catalog all PDFs.
        manifest = self.catalog_pdfs(
            compute_hashes=compute_hashes,
            incremental=True,
        )
        self.logger.info(f'Manifest contains {len(manifest)} COA PDF(s)')

        if catalog_only:
            return manifest

        # Phase 3: Convert to LabResult records.
        lab_results = self._convert_to_lab_results(manifest)
        df = pd.DataFrame(lab_results)

        # Save results.
        if save_results and not df.empty:
            self.save_results(df, prefix='fl-terplife-labs-results')

        self.logger.info(f'✓ Collected {len(df)} results for TerpLife Labs (FL).')
        return df

    # ── Convenience Methods ──────────────────────────────────────

    def save_results(self, df: pd.DataFrame, prefix: str = 'fl-terplife-labs-results'):
        """Save results to a timestamped CSV.

        Args:
            df: DataFrame to save.
            prefix: Filename prefix.
        """
        datasets_dir = getattr(self, 'datasets_dir', self.data_dir)
        os.makedirs(str(datasets_dir), exist_ok=True)
        timestamp = datetime.now().strftime('%Y-%m-%dT%H%M%S')
        filename = f'{prefix}-{timestamp}.csv'
        outpath = os.path.join(str(datasets_dir), filename)
        df.to_csv(outpath, index=False)
        self.logger.info(f'Saved {len(df)} results → {outpath}')

    def get_archive_stats(self) -> Dict:
        """Get summary statistics about the current archive.

        Returns:
            Dictionary with archive statistics.
        """
        pdf_dir = Path(str(self.pdf_dir))
        pdfs = _find_pdf_files(str(pdf_dir)) if pdf_dir.exists() else []

        # Extract numeric IDs to find the range.
        numeric_ids = []
        for p in pdfs:
            sid = _extract_sample_id(os.path.basename(p))
            try:
                numeric_ids.append(int(sid))
            except (ValueError, TypeError):
                pass

        total_size = sum(os.path.getsize(p) for p in pdfs)

        stats = {
            'total_pdfs': len(pdfs),
            'total_size_bytes': total_size,
            'total_size_gb': round(total_size / (1024 ** 3), 2),
            'numeric_id_min': min(numeric_ids) if numeric_ids else None,
            'numeric_id_max': max(numeric_ids) if numeric_ids else None,
            'numeric_id_count': len(numeric_ids),
            'non_numeric_count': len(pdfs) - len(numeric_ids),
            'manifest_exists': os.path.exists(self.manifest_path),
        }

        if os.path.exists(self.manifest_path):
            try:
                m = pd.read_csv(self.manifest_path)
                stats['manifest_entries'] = len(m)
            except Exception:
                stats['manifest_entries'] = 0

        return stats


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Inline Tests & CLI                                               ║
# ╚══════════════════════════════════════════════════════════════════╝

def run_unit_tests():
    """Run quick inline smoke tests."""
    print('Running TerpLife Labs unit tests...')
    errors = 0

    # Test _generate_result_id
    rid = _generate_result_id('17235.pdf')
    assert len(rid) == 16, f'Expected 16-char ID, got {len(rid)}'
    assert rid == _generate_result_id('17235.pdf'), 'ID not deterministic'
    assert rid != _generate_result_id('17236.pdf'), 'IDs should differ'

    # Test _extract_sample_id
    assert _extract_sample_id('17235.pdf') == '17235'
    assert _extract_sample_id('TL-2024-001.PDF') == 'TL-2024-001'
    assert _extract_sample_id('Sample 123 (retest).pdf') == 'Sample 123 (retest)'
    assert _extract_sample_id('') == ''
    assert _extract_sample_id(None) == ''

    # Test _hash_file
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as f:
        f.write(b'%PDF-1.4 test content')
        tmp = f.name
    h = _hash_file(tmp)
    assert len(h) == 64, f'Expected 64-char hex hash, got {len(h)}'
    assert h == _hash_file(tmp), 'Hash not deterministic'
    os.unlink(tmp)

    # Test _is_valid_pdf
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as f:
        f.write(b'%PDF-1.4 valid pdf content')
        valid_pdf = f.name
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as f:
        f.write(b'not a pdf at all')
        invalid_pdf = f.name
    assert _is_valid_pdf(valid_pdf) is True
    assert _is_valid_pdf(invalid_pdf) is False
    os.unlink(valid_pdf)
    os.unlink(invalid_pdf)

    # Test _find_zip_files / _find_pdf_files
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test files.
        open(os.path.join(tmpdir, 'test.zip'), 'w').close()
        open(os.path.join(tmpdir, 'test.pdf'), 'w').close()
        open(os.path.join(tmpdir, 'test.txt'), 'w').close()
        assert len(_find_zip_files(tmpdir)) == 1
        assert len(_find_pdf_files(tmpdir)) == 1

    # Test TERPLIFE_PRODUCER metadata
    assert TERPLIFE_PRODUCER['lab'] == 'TerpLife Labs'
    assert TERPLIFE_PRODUCER['lab_state'] == 'fl'
    assert TERPLIFE_PRODUCER['lab_city'] == 'Coral Springs'

    print(f'✓ All unit tests passed (0 errors)')


def run_integration_test():
    """Run integration test with real file operations."""
    import tempfile
    print('Running TerpLife Labs integration test...')

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = os.path.join(tmpdir, 'data')
        pdf_dir = os.path.join(tmpdir, 'pdfs', 'terplife')
        archive_dir = os.path.join(tmpdir, 'pdfs')
        os.makedirs(pdf_dir)

        # Create fake existing TerpLife COA PDFs.
        for i in range(5):
            filepath = os.path.join(pdf_dir, f'{10000 + i}.pdf')
            with open(filepath, 'wb') as f:
                f.write(b'%PDF-1.4 fake COA content for sample ' + str(i).encode())

        # Create a COAS-* ZIP with additional TerpLife PDFs.
        zip_path = os.path.join(archive_dir, 'COAS-test.zip')
        with zipfile.ZipFile(zip_path, 'w') as zf:
            for i in range(3):
                content = b'%PDF-1.4 archive COA ' + str(i).encode()
                zf.writestr(f'COAS/{20000 + i}.pdf', content)
            # Add one duplicate.
            zf.writestr('COAS/10000.pdf', b'%PDF-1.4 duplicate content')

        # Create an extracted COAS-* directory with loose PDFs.
        coas_dir = os.path.join(archive_dir, 'COAS-extracted', 'COAS')
        os.makedirs(coas_dir)
        for i in range(2):
            filepath = os.path.join(coas_dir, f'{30000 + i}.pdf')
            with open(filepath, 'wb') as f:
                f.write(b'%PDF-1.4 extracted COA ' + str(i).encode())

        # CRITICAL: Create sibling directories for OTHER sources.
        # These must NOT be touched by the TerpLife collector.
        for other_source in ['flowery', 'jungleboys', 'kaycha']:
            other_dir = os.path.join(archive_dir, other_source)
            os.makedirs(other_dir)
            for i in range(3):
                filepath = os.path.join(other_dir, f'{other_source}-{i}.pdf')
                with open(filepath, 'wb') as f:
                    f.write(b'%PDF-1.4 ' + other_source.encode() + b' COA')

        # Create collector pointing to the terplife subdirectory.
        collector = TerpLifeLabsCollector(
            data_dir=data_dir,
            pdf_dir=pdf_dir,
        )

        # Test merge — should only touch COAS-* files/dirs.
        stats = collector.merge_archives(archive_dir)
        assert stats['zips_found'] == 1, f'Expected 1 ZIP, got {stats["zips_found"]}'
        assert stats['zips_extracted'] == 1
        assert stats['pdfs_new'] == 5, f'Expected 5 new (3 ZIP + 2 loose), got {stats["pdfs_new"]}'
        assert stats['pdfs_skipped'] == 1, f'Expected 1 skipped (dup), got {stats["pdfs_skipped"]}'
        assert stats['dirs_skipped'] >= 3, f'Expected ≥3 skipped dirs, got {stats["dirs_skipped"]}'
        assert stats['dirs_scanned'] >= 1, f'Expected ≥1 scanned dir, got {stats["dirs_scanned"]}'

        # CRITICAL SAFETY CHECK: Verify no other-source PDFs leaked in.
        terplife_files = os.listdir(pdf_dir)
        for f in terplife_files:
            for other_source in ['flowery', 'jungleboys', 'kaycha']:
                assert other_source not in f.lower(), \
                    f'CONTAMINATION: {other_source} file "{f}" found in terplife dir!'

        # Test catalog.
        manifest = collector.catalog_pdfs(compute_hashes=True)
        assert len(manifest) >= 10, f'Expected ≥10 entries, got {len(manifest)}'
        assert 'file_name' in manifest.columns
        assert 'file_hash' in manifest.columns

        # Test get_results.
        results = collector.get_results(save_results=False)
        assert len(results) >= 10
        assert 'id' in results.columns
        assert 'state' in results.columns

        # Test archive stats.
        archive_stats = collector.get_archive_stats()
        assert archive_stats['total_pdfs'] >= 10

    print('✓ Integration test passed (source isolation verified)')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='TerpLife Labs COA Collector'
    )
    parser.add_argument('--test', action='store_true',
                        help='Run unit tests')
    parser.add_argument('--integration-test', action='store_true',
                        help='Run integration test')
    parser.add_argument('--catalog-only', action='store_true',
                        help='Only catalog existing PDFs')
    parser.add_argument('--archive-dir', type=str, default=None,
                        help='Directory containing ZIP files to merge')
    parser.add_argument('--data-dir', type=str,
                        default='D:/data/florida/results',
                        help='Data directory')
    parser.add_argument('--pdf-dir', type=str,
                        default='D:/data/florida/results/pdfs/terplife',
                        help='PDF storage directory')
    parser.add_argument('--no-hashes', action='store_true',
                        help='Skip hash computation (faster)')
    args = parser.parse_args()

    if args.test:
        run_unit_tests()
    elif args.integration_test:
        run_integration_test()
    else:
        collector = TerpLifeLabsCollector(
            data_dir=args.data_dir,
            pdf_dir=args.pdf_dir,
        )
        results = collector.get_results(
            archive_dir=args.archive_dir,
            catalog_only=args.catalog_only,
            compute_hashes=not args.no_hashes,
        )
        print(f'Total results: {len(results)}')

        # Print archive stats.
        stats = collector.get_archive_stats()
        print(f'Archive stats: {stats}')