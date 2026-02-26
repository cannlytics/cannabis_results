"""
Test Suite | TerpLife Labs Collector
Copyright (c) 2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2/22/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Comprehensive tests for the TerpLife Labs COA collector.

    Covers:
        - Constants and metadata validation
        - Static helper functions (_generate_result_id, _extract_sample_id, etc.)
        - Archive merging (ZIP extraction, deduplication, validation)
        - PDF cataloging (manifest build, incremental updates)
        - LabResult conversion
        - Full pipeline integration
        - Edge cases and error handling

Usage:
    ```bash
    # Run all unit tests
    pytest tests/test_collectors/test_fl_terplife.py -v

    # Run without integration tests (no network)
    pytest tests/test_collectors/test_fl_terplife.py -v -m "not integration"

    # Run only a specific class
    pytest tests/test_collectors/test_fl_terplife.py::TestExtractSampleId -v
    ```
"""
# Standard imports:
import hashlib
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

# External imports:
import pandas as pd
import pytest

# Module under test:
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from get_results_fl_terplife import (
    # Constants
    WEBSITE_URL,
    GDRIVE_FOLDER_URL,
    GDRIVE_FOLDER_ID,
    TERPLIFE_PRODUCER,
    DEFAULT_HEADERS,
    MANIFEST_COLUMNS,
    COA_PDF_PATTERN,
    # Static helpers
    _generate_result_id,
    _extract_sample_id,
    _hash_file,
    _is_valid_pdf,
    _find_zip_files,
    _find_pdf_files,
    # Collector
    TerpLifeLabsCollector,
)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Fixtures                                                         ║
# ╚══════════════════════════════════════════════════════════════════╝

@pytest.fixture
def temp_data_dir(tmp_path):
    """Create a temporary data directory."""
    d = tmp_path / 'data'
    d.mkdir()
    return str(d)


@pytest.fixture
def temp_pdf_dir(tmp_path):
    """Create a temporary PDF directory."""
    d = tmp_path / 'pdfs' / 'terplife'
    d.mkdir(parents=True)
    return str(d)


@pytest.fixture
def temp_archive_dir(tmp_path):
    """Create a temporary archive directory."""
    d = tmp_path / 'archives'
    d.mkdir()
    return str(d)


@pytest.fixture
def collector(temp_data_dir, temp_pdf_dir):
    """Create a TerpLifeLabsCollector instance with temp directories."""
    return TerpLifeLabsCollector(
        data_dir=temp_data_dir,
        pdf_dir=temp_pdf_dir,
        verbose=False,
    )


@pytest.fixture
def populated_pdf_dir(temp_pdf_dir):
    """Create a PDF directory with sample COA files."""
    for i in range(5):
        filepath = os.path.join(temp_pdf_dir, f'{10000 + i}.pdf')
        with open(filepath, 'wb') as f:
            f.write(b'%PDF-1.4 sample COA content ' + str(i).encode())
    return temp_pdf_dir


@pytest.fixture
def sample_zip(temp_archive_dir):
    """Create a sample ZIP archive with COA PDFs."""
    zip_path = os.path.join(temp_archive_dir, 'COAS-20260222-test.zip')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i in range(3):
            content = b'%PDF-1.4 archive COA sample ' + str(i).encode()
            zf.writestr(f'COAS/{20000 + i}.pdf', content)
    return zip_path


@pytest.fixture
def valid_pdf_file(tmp_path):
    """Create a valid PDF file."""
    filepath = tmp_path / 'valid.pdf'
    filepath.write_bytes(b'%PDF-1.4 valid test content\n%%EOF')
    return str(filepath)


@pytest.fixture
def invalid_pdf_file(tmp_path):
    """Create a file that is not a valid PDF."""
    filepath = tmp_path / 'invalid.pdf'
    filepath.write_bytes(b'This is not a PDF file at all')
    return str(filepath)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Constants                                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestTerpLifeConstants:
    """Test that all constants are correctly defined."""

    def test_website_url(self):
        """Test website URL is HTTPS and points to TerpLife."""
        assert WEBSITE_URL.startswith('https://')
        assert 'terplifelabs.com' in WEBSITE_URL

    def test_gdrive_folder_url(self):
        """Test Google Drive folder URL is valid."""
        assert 'drive.google.com' in GDRIVE_FOLDER_URL
        assert GDRIVE_FOLDER_ID in GDRIVE_FOLDER_URL

    def test_gdrive_folder_id_format(self):
        """Test Google Drive folder ID is a valid-length string."""
        assert len(GDRIVE_FOLDER_ID) > 20
        assert GDRIVE_FOLDER_ID.isalnum() or '-' in GDRIVE_FOLDER_ID or '_' in GDRIVE_FOLDER_ID

    def test_producer_lab_name(self):
        """Test lab name is correct."""
        assert TERPLIFE_PRODUCER['lab'] == 'TerpLife Labs'

    def test_producer_state(self):
        """Test lab state is Florida."""
        assert TERPLIFE_PRODUCER['lab_state'] == 'fl'

    def test_producer_city(self):
        """Test lab city is Coral Springs."""
        assert TERPLIFE_PRODUCER['lab_city'] == 'Coral Springs'

    def test_producer_county(self):
        """Test lab county is Broward."""
        assert TERPLIFE_PRODUCER['lab_county'] == 'Broward'

    def test_producer_zipcode(self):
        """Test lab ZIP code is valid Florida ZIP."""
        assert TERPLIFE_PRODUCER['lab_zipcode'] == '33065'
        assert TERPLIFE_PRODUCER['lab_zipcode'].startswith('3')

    def test_producer_coordinates_valid(self):
        """Test lat/lon are within reasonable bounds for South Florida."""
        lat = TERPLIFE_PRODUCER['lab_latitude']
        lon = TERPLIFE_PRODUCER['lab_longitude']
        assert 25.0 < lat < 27.0, f'Latitude {lat} not in South FL range'
        assert -81.0 < lon < -80.0, f'Longitude {lon} not in South FL range'

    def test_producer_phone_format(self):
        """Test phone number is formatted correctly."""
        phone = TERPLIFE_PRODUCER['lab_phone']
        assert phone.startswith('(')
        assert len(phone) >= 12

    def test_producer_website(self):
        """Test lab website is HTTPS."""
        assert TERPLIFE_PRODUCER['lab_website'].startswith('https://')

    def test_default_headers_has_user_agent(self):
        """Test that default headers include a User-Agent."""
        assert 'User-Agent' in DEFAULT_HEADERS
        assert 'Mozilla' in DEFAULT_HEADERS['User-Agent']

    def test_manifest_columns_complete(self):
        """Test that manifest columns include all required fields."""
        required = ['file_name', 'sample_id', 'file_path', 'file_size',
                     'file_hash', 'date_cataloged', 'source']
        for col in required:
            assert col in MANIFEST_COLUMNS, f'Missing manifest column: {col}'


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: _generate_result_id                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestGenerateResultId:
    """Test result ID generation."""

    def test_id_length(self):
        """Test ID is exactly 16 characters."""
        assert len(_generate_result_id('17235.pdf')) == 16

    def test_id_is_hex_string(self):
        """Test ID contains only hexadecimal characters."""
        rid = _generate_result_id('17235.pdf')
        assert all(c in '0123456789abcdef' for c in rid)

    def test_id_deterministic(self):
        """Test same input produces same ID."""
        assert _generate_result_id('17235.pdf') == _generate_result_id('17235.pdf')

    def test_id_unique_by_filename(self):
        """Test different filenames produce different IDs."""
        assert _generate_result_id('17235.pdf') != _generate_result_id('17236.pdf')

    def test_id_handles_none(self):
        """Test None input doesn't crash."""
        rid = _generate_result_id(None)
        assert len(rid) == 16

    def test_id_handles_empty_string(self):
        """Test empty string input."""
        rid = _generate_result_id('')
        assert len(rid) == 16

    def test_id_none_and_empty_produce_same(self):
        """Test None and empty string produce the same ID."""
        assert _generate_result_id(None) == _generate_result_id('')

    def test_id_case_sensitive(self):
        """Test IDs differ for different cases."""
        assert _generate_result_id('Test.pdf') != _generate_result_id('test.pdf')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: _extract_sample_id                                         ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestExtractSampleId:
    """Test sample ID extraction from filenames."""

    def test_numeric_id(self):
        """Test purely numeric filename."""
        assert _extract_sample_id('17235.pdf') == '17235'

    def test_alphanumeric_id(self):
        """Test alphanumeric filename."""
        assert _extract_sample_id('TL-2024-001.pdf') == 'TL-2024-001'

    def test_spaces_in_name(self):
        """Test filename with spaces."""
        assert _extract_sample_id('Sample 123 (retest).pdf') == 'Sample 123 (retest)'

    def test_uppercase_extension(self):
        """Test uppercase .PDF extension."""
        assert _extract_sample_id('17235.PDF') == '17235'

    def test_mixed_case_extension(self):
        """Test mixed case .Pdf extension."""
        assert _extract_sample_id('17235.Pdf') == '17235'

    def test_empty_string(self):
        """Test empty string returns empty."""
        assert _extract_sample_id('') == ''

    def test_none(self):
        """Test None returns empty."""
        assert _extract_sample_id(None) == ''

    def test_no_extension(self):
        """Test filename without .pdf extension."""
        assert _extract_sample_id('17235') == '17235'

    def test_multiple_dots(self):
        """Test filename with multiple dots."""
        assert _extract_sample_id('sample.v2.pdf') == 'sample.v2'

    def test_whitespace_trimmed(self):
        """Test leading/trailing whitespace is trimmed."""
        assert _extract_sample_id('  17235.pdf  ') == '17235'


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: _hash_file                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestHashFile:
    """Test file hashing."""

    def test_hash_length(self, valid_pdf_file):
        """Test hash is 64 hex characters (SHA256)."""
        h = _hash_file(valid_pdf_file)
        assert len(h) == 64

    def test_hash_is_hex(self, valid_pdf_file):
        """Test hash contains only hex characters."""
        h = _hash_file(valid_pdf_file)
        assert all(c in '0123456789abcdef' for c in h)

    def test_hash_deterministic(self, valid_pdf_file):
        """Test same file produces same hash."""
        assert _hash_file(valid_pdf_file) == _hash_file(valid_pdf_file)

    def test_different_content_different_hash(self, tmp_path):
        """Test different file contents produce different hashes."""
        f1 = tmp_path / 'a.pdf'
        f2 = tmp_path / 'b.pdf'
        f1.write_bytes(b'%PDF-1.4 content A')
        f2.write_bytes(b'%PDF-1.4 content B')
        assert _hash_file(str(f1)) != _hash_file(str(f2))

    def test_nonexistent_file_returns_empty(self):
        """Test nonexistent file returns empty string."""
        assert _hash_file('/nonexistent/file.pdf') == ''

    def test_empty_file(self, tmp_path):
        """Test empty file produces a valid hash."""
        f = tmp_path / 'empty.pdf'
        f.write_bytes(b'')
        h = _hash_file(str(f))
        assert len(h) == 64  # SHA256 of empty is a known value


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: _is_valid_pdf                                              ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestIsValidPdf:
    """Test PDF magic byte validation."""

    def test_valid_pdf(self, valid_pdf_file):
        """Test valid PDF is recognized."""
        assert _is_valid_pdf(valid_pdf_file) is True

    def test_invalid_pdf(self, invalid_pdf_file):
        """Test non-PDF file is rejected."""
        assert _is_valid_pdf(invalid_pdf_file) is False

    def test_nonexistent_file(self):
        """Test nonexistent file returns False."""
        assert _is_valid_pdf('/nonexistent/file.pdf') is False

    def test_empty_file(self, tmp_path):
        """Test empty file returns False."""
        f = tmp_path / 'empty.pdf'
        f.write_bytes(b'')
        assert _is_valid_pdf(str(f)) is False

    def test_pdf_14(self, tmp_path):
        """Test PDF version 1.4 magic bytes."""
        f = tmp_path / 'test.pdf'
        f.write_bytes(b'%PDF-1.4\n')
        assert _is_valid_pdf(str(f)) is True

    def test_pdf_17(self, tmp_path):
        """Test PDF version 1.7 magic bytes."""
        f = tmp_path / 'test.pdf'
        f.write_bytes(b'%PDF-1.7\n')
        assert _is_valid_pdf(str(f)) is True

    def test_pdf_20(self, tmp_path):
        """Test PDF version 2.0 magic bytes."""
        f = tmp_path / 'test.pdf'
        f.write_bytes(b'%PDF-2.0\n')
        assert _is_valid_pdf(str(f)) is True


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: _find_zip_files / _find_pdf_files                          ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestFileFinders:
    """Test file discovery functions."""

    def test_find_zips_in_flat_dir(self, tmp_path):
        """Test finding ZIPs in a flat directory."""
        (tmp_path / 'a.zip').touch()
        (tmp_path / 'b.zip').touch()
        (tmp_path / 'c.pdf').touch()
        assert len(_find_zip_files(str(tmp_path))) == 2

    def test_find_zips_recursive(self, tmp_path):
        """Test finding ZIPs recursively."""
        sub = tmp_path / 'sub'
        sub.mkdir()
        (tmp_path / 'a.zip').touch()
        (sub / 'b.zip').touch()
        assert len(_find_zip_files(str(tmp_path))) == 2

    def test_find_zips_empty_dir(self, tmp_path):
        """Test finding ZIPs in empty directory."""
        assert len(_find_zip_files(str(tmp_path))) == 0

    def test_find_pdfs_in_flat_dir(self, tmp_path):
        """Test finding PDFs in a flat directory."""
        (tmp_path / 'a.pdf').touch()
        (tmp_path / 'b.PDF').touch()
        (tmp_path / 'c.zip').touch()
        assert len(_find_pdf_files(str(tmp_path))) == 2

    def test_find_pdfs_recursive(self, tmp_path):
        """Test finding PDFs recursively."""
        sub = tmp_path / 'sub'
        sub.mkdir()
        (tmp_path / 'a.pdf').touch()
        (sub / 'b.pdf').touch()
        assert len(_find_pdf_files(str(tmp_path))) == 2

    def test_find_pdfs_sorted(self, tmp_path):
        """Test results are sorted."""
        (tmp_path / 'c.pdf').touch()
        (tmp_path / 'a.pdf').touch()
        (tmp_path / 'b.pdf').touch()
        pdfs = _find_pdf_files(str(tmp_path))
        names = [os.path.basename(p) for p in pdfs]
        assert names == sorted(names)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Collector Initialization                                   ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestCollectorInit:
    """Test collector initialization."""

    def test_init_default(self, temp_data_dir, temp_pdf_dir):
        """Test default initialization."""
        c = TerpLifeLabsCollector(
            data_dir=temp_data_dir,
            pdf_dir=temp_pdf_dir,
        )
        assert str(c.pdf_dir).endswith('terplife')

    def test_init_creates_pdf_dir(self, tmp_path):
        """Test that init creates PDF directory if needed."""
        pdf_dir = str(tmp_path / 'new' / 'pdf' / 'dir')
        c = TerpLifeLabsCollector(
            data_dir=str(tmp_path / 'data'),
            pdf_dir=pdf_dir,
        )
        assert os.path.isdir(pdf_dir)

    def test_context_manager(self, temp_data_dir, temp_pdf_dir):
        """Test context manager protocol."""
        with TerpLifeLabsCollector(
            data_dir=temp_data_dir,
            pdf_dir=temp_pdf_dir,
        ) as c:
            assert c is not None

    def test_manifest_path_set(self, collector):
        """Test manifest path is set on init."""
        assert collector.manifest_path.endswith('terplife-manifest.csv')

    def test_verbose_flag(self, temp_data_dir, temp_pdf_dir):
        """Test verbose flag is respected."""
        c = TerpLifeLabsCollector(
            data_dir=temp_data_dir,
            pdf_dir=temp_pdf_dir,
            verbose=False,
        )
        assert c.verbose is False


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Archive Merging                                            ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestMergeArchives:
    """Test ZIP archive extraction and merging."""

    def test_merge_single_zip(self, collector, sample_zip, temp_archive_dir):
        """Test merging a single ZIP file."""
        stats = collector.merge_archives(temp_archive_dir)
        assert stats['zips_found'] == 1
        assert stats['zips_extracted'] == 1
        assert stats['pdfs_new'] == 3

    def test_merge_filters_zips_by_pattern(self, collector, temp_archive_dir):
        """Test that ZIPs not matching the pattern are skipped."""
        # Create a COAS ZIP (should match).
        coas_zip = os.path.join(temp_archive_dir, 'COAS-test.zip')
        with zipfile.ZipFile(coas_zip, 'w') as zf:
            zf.writestr('a.pdf', b'%PDF-1.4 coas file')
        # Create a non-COAS ZIP (should be skipped).
        other_zip = os.path.join(temp_archive_dir, 'flowery-backup.zip')
        with zipfile.ZipFile(other_zip, 'w') as zf:
            zf.writestr('b.pdf', b'%PDF-1.4 flowery file')

        stats = collector.merge_archives(temp_archive_dir, zip_pattern='COAS')
        assert stats['zips_found'] == 1, 'Should only find the COAS ZIP'
        assert stats['zips_skipped_pattern'] == 1, 'Should skip the flowery ZIP'
        assert stats['pdfs_new'] == 1

    def test_merge_deduplicates_by_filename(
        self, collector, temp_archive_dir, populated_pdf_dir
    ):
        """Test that merging skips files already in pdf_dir."""
        # Create ZIP with a duplicate filename (must match COAS pattern).
        zip_path = os.path.join(temp_archive_dir, 'COAS-dup.zip')
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr('10000.pdf', b'%PDF-1.4 duplicate')
            zf.writestr('99999.pdf', b'%PDF-1.4 new file')

        # Point collector to populated dir.
        collector.pdf_dir = Path(populated_pdf_dir)
        stats = collector.merge_archives(temp_archive_dir)
        assert stats['pdfs_skipped'] >= 1
        assert stats['pdfs_new'] >= 1

    def test_merge_nonexistent_dir(self, collector):
        """Test merging from a nonexistent directory."""
        stats = collector.merge_archives('/nonexistent/dir')
        assert stats['zips_found'] == 0

    def test_merge_empty_dir(self, collector, temp_archive_dir):
        """Test merging from an empty directory."""
        stats = collector.merge_archives(temp_archive_dir)
        assert stats['zips_found'] == 0
        assert stats['pdfs_new'] == 0

    def test_merge_bad_zip(self, collector, temp_archive_dir):
        """Test handling of corrupted ZIP files."""
        bad_zip = os.path.join(temp_archive_dir, 'COAS-bad.zip')
        with open(bad_zip, 'wb') as f:
            f.write(b'not a zip file')
        stats = collector.merge_archives(temp_archive_dir)
        assert stats['zips_found'] == 1
        assert stats['zips_extracted'] == 0

    def test_merge_skips_macosx_entries(self, collector, temp_archive_dir):
        """Test that __MACOSX entries in ZIPs are skipped."""
        zip_path = os.path.join(temp_archive_dir, 'COAS-macosx.zip')
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr('real.pdf', b'%PDF-1.4 real')
            zf.writestr('__MACOSX/._real.pdf', b'macosx metadata')
        stats = collector.merge_archives(temp_archive_dir)
        assert stats['pdfs_new'] == 1

    def test_merge_loose_pdfs_from_extracted_dirs(self, collector, temp_archive_dir):
        """Test merging loose PDFs from extracted archive folders."""
        # Create a simulated extracted folder structure (matches COAS pattern).
        extracted = os.path.join(temp_archive_dir, 'COAS-extracted', 'COAS')
        os.makedirs(extracted)
        for i in range(3):
            filepath = os.path.join(extracted, f'{30000 + i}.pdf')
            with open(filepath, 'wb') as f:
                f.write(b'%PDF-1.4 loose COA ' + str(i).encode())

        stats = collector.merge_archives(temp_archive_dir)
        assert stats['pdfs_new'] == 3
        assert stats['dirs_scanned'] >= 1

    def test_merge_skips_invalid_pdfs(self, collector, temp_archive_dir):
        """Test that non-PDF files named .pdf are flagged."""
        extracted = os.path.join(temp_archive_dir, 'COAS-extracted')
        os.makedirs(extracted)
        filepath = os.path.join(extracted, 'fake.pdf')
        with open(filepath, 'w') as f:
            f.write('This is not a PDF')

        stats = collector.merge_archives(temp_archive_dir)
        assert stats['pdfs_invalid'] == 1

    def test_merge_skips_other_source_directories(self, collector, temp_archive_dir):
        """CRITICAL: Verify other-source directories are never touched."""
        # Create other-source directories with PDFs.
        for source in ['flowery', 'jungleboys', 'kaycha', 'FLMedicalTrees']:
            source_dir = os.path.join(temp_archive_dir, source)
            os.makedirs(source_dir)
            for i in range(3):
                filepath = os.path.join(source_dir, f'{source}-{i}.pdf')
                with open(filepath, 'wb') as f:
                    f.write(b'%PDF-1.4 ' + source.encode())

        # Create a COAS directory with TerpLife PDFs.
        coas_dir = os.path.join(temp_archive_dir, 'COAS-test-extract')
        os.makedirs(coas_dir)
        with open(os.path.join(coas_dir, '50000.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4 terplife coa')

        stats = collector.merge_archives(temp_archive_dir)

        # Only the COAS directory should have been scanned.
        assert stats['dirs_scanned'] == 1
        assert stats['dirs_skipped'] == 4  # flowery, jungleboys, kaycha, FLMedicalTrees
        assert stats['pdfs_new'] == 1  # Only the COAS PDF

        # CRITICAL: Verify no contamination.
        terplife_dir = str(collector.pdf_dir)
        for f in os.listdir(terplife_dir):
            for source in ['flowery', 'jungleboys', 'kaycha', 'FLMedicalTrees']:
                assert source not in f.lower(), \
                    f'CONTAMINATION: {source} file "{f}" leaked into terplife dir!'

    def test_merge_with_real_directory_layout(self, temp_data_dir):
        """Test with a layout mimicking the real D:\\data directory."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            # Recreate the real directory layout.
            pdfs_dir = Path(tmpdir) / 'pdfs'
            terplife = pdfs_dir / 'terplife'
            terplife.mkdir(parents=True)

            # Existing TerpLife COAs.
            for i in range(3):
                (terplife / f'{i}.pdf').write_bytes(b'%PDF-1.4 existing')

            # Other sources — must NOT be touched.
            for source in ['flowery', 'jungleboys', 'kaycha', 'FLMedicalTrees', 'MMTC-2015-0001']:
                d = pdfs_dir / source
                d.mkdir()
                for i in range(5):
                    (d / f'{source}-{i}.pdf').write_bytes(b'%PDF-1.4 other')

            # COAS ZIP.
            zip_path = pdfs_dir / 'COAS-20260222.zip'
            with zipfile.ZipFile(str(zip_path), 'w') as zf:
                zf.writestr('COAS/99999.pdf', b'%PDF-1.4 from zip')

            # COAS extracted directory.
            coas_ext = pdfs_dir / 'COAS-20260222-010' / 'COAS'
            coas_ext.mkdir(parents=True)
            (coas_ext / '88888.pdf').write_bytes(b'%PDF-1.4 from extracted')

            collector = TerpLifeLabsCollector(
                data_dir=temp_data_dir,
                pdf_dir=str(terplife),
                verbose=False,
            )
            stats = collector.merge_archives(str(pdfs_dir))

            # Verify only COAS content was merged.
            assert stats['zips_found'] == 1
            assert stats['pdfs_new'] == 2  # 99999.pdf + 88888.pdf
            assert stats['dirs_skipped'] >= 5

            # CRITICAL: Verify no contamination.
            terplife_files = set(os.listdir(str(terplife)))
            for source in ['flowery', 'jungleboys', 'kaycha', 'FLMedicalTrees', 'MMTC']:
                contaminated = [f for f in terplife_files if source.lower() in f.lower()]
                assert len(contaminated) == 0, \
                    f'CONTAMINATION: {contaminated} from {source} in terplife dir!'


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: PDF Cataloging                                             ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestCatalogPdfs:
    """Test manifest building and updating."""

    def test_catalog_empty_dir(self, collector):
        """Test cataloging an empty PDF directory."""
        manifest = collector.catalog_pdfs()
        assert len(manifest) == 0
        assert list(manifest.columns) == MANIFEST_COLUMNS

    def test_catalog_populated_dir(self, collector, populated_pdf_dir):
        """Test cataloging a populated PDF directory."""
        collector.pdf_dir = Path(populated_pdf_dir)
        manifest = collector.catalog_pdfs(compute_hashes=True)
        assert len(manifest) == 5
        assert 'file_name' in manifest.columns
        assert 'file_hash' in manifest.columns
        assert all(manifest['file_hash'] != '')

    def test_catalog_saves_manifest_file(self, collector, populated_pdf_dir):
        """Test that catalog saves a manifest CSV."""
        collector.pdf_dir = Path(populated_pdf_dir)
        collector.catalog_pdfs()
        assert os.path.exists(collector.manifest_path)

    def test_catalog_incremental_adds_new_only(self, collector, populated_pdf_dir):
        """Test incremental cataloging only adds new files."""
        collector.pdf_dir = Path(populated_pdf_dir)

        # First catalog.
        m1 = collector.catalog_pdfs()
        assert len(m1) == 5

        # Add one more PDF.
        new_pdf = os.path.join(populated_pdf_dir, '99999.pdf')
        with open(new_pdf, 'wb') as f:
            f.write(b'%PDF-1.4 brand new COA')

        # Incremental catalog.
        m2 = collector.catalog_pdfs(incremental=True)
        assert len(m2) == 6

    def test_catalog_full_rebuild(self, collector, populated_pdf_dir):
        """Test full rebuild ignores existing manifest."""
        collector.pdf_dir = Path(populated_pdf_dir)

        # First catalog.
        collector.catalog_pdfs()

        # Full rebuild.
        manifest = collector.catalog_pdfs(incremental=False)
        assert len(manifest) == 5

    def test_catalog_without_hashes(self, collector, populated_pdf_dir):
        """Test cataloging without hash computation."""
        collector.pdf_dir = Path(populated_pdf_dir)
        manifest = collector.catalog_pdfs(compute_hashes=False)
        assert len(manifest) == 5
        assert all(manifest['file_hash'] == '')

    def test_catalog_deduplicates_by_filename(self, collector, populated_pdf_dir):
        """Test that duplicate filenames are removed."""
        collector.pdf_dir = Path(populated_pdf_dir)

        # Create initial manifest with a duplicate.
        initial = pd.DataFrame([{
            'file_name': '10000.pdf',
            'sample_id': '10000',
            'file_path': '/old/path/10000.pdf',
            'file_size': 100,
            'file_hash': 'oldhash',
            'date_cataloged': '2025-01-01',
            'source': 'website_search',
        }])
        initial.to_csv(collector.manifest_path, index=False)

        manifest = collector.catalog_pdfs(incremental=True)
        # Should have 5 unique files, not 6 (the old duplicate should be deduped).
        name_counts = manifest['file_name'].value_counts()
        assert name_counts.max() == 1, 'Duplicates found in manifest'

    def test_catalog_deduplicates_by_hash(self, collector, temp_pdf_dir):
        """Test that content-identical files are deduplicated."""
        collector.pdf_dir = Path(temp_pdf_dir)

        # Create two files with identical content but different names.
        content = b'%PDF-1.4 identical content for dedup test'
        with open(os.path.join(temp_pdf_dir, 'original.pdf'), 'wb') as f:
            f.write(content)
        with open(os.path.join(temp_pdf_dir, 'copy_of_original.pdf'), 'wb') as f:
            f.write(content)
        # One unique file.
        with open(os.path.join(temp_pdf_dir, 'unique.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4 unique content')

        manifest = collector.catalog_pdfs(compute_hashes=True)
        # Should have 2 entries (one deduped by hash).
        assert len(manifest) == 2

    def test_catalog_sample_id_extraction(self, collector, populated_pdf_dir):
        """Test that sample_id is correctly extracted."""
        collector.pdf_dir = Path(populated_pdf_dir)
        manifest = collector.catalog_pdfs()
        sample_ids = manifest['sample_id'].tolist()
        assert '10000' in sample_ids
        assert '10004' in sample_ids

    def test_catalog_source_field(self, collector, populated_pdf_dir):
        """Test that source field is populated."""
        collector.pdf_dir = Path(populated_pdf_dir)
        manifest = collector.catalog_pdfs()
        assert all(manifest['source'] == 'gdrive_archive')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: LabResult Conversion                                       ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestConvertToLabResults:
    """Test conversion from manifest to LabResult records."""

    def test_convert_produces_results(self, collector, populated_pdf_dir):
        """Test that conversion produces result records."""
        collector.pdf_dir = Path(populated_pdf_dir)
        manifest = collector.catalog_pdfs(compute_hashes=False)
        results = collector._convert_to_lab_results(manifest)
        assert len(results) == 5

    def test_convert_has_id(self, collector, populated_pdf_dir):
        """Test each result has an ID."""
        collector.pdf_dir = Path(populated_pdf_dir)
        manifest = collector.catalog_pdfs(compute_hashes=False)
        results = collector._convert_to_lab_results(manifest)
        for r in results:
            assert 'id' in r
            assert len(r['id']) == 16

    def test_convert_has_sample_id(self, collector, populated_pdf_dir):
        """Test each result has a sample_id."""
        collector.pdf_dir = Path(populated_pdf_dir)
        manifest = collector.catalog_pdfs(compute_hashes=False)
        results = collector._convert_to_lab_results(manifest)
        sample_ids = [r['sample_id'] for r in results]
        assert '10000' in sample_ids

    def test_convert_state_is_florida(self, collector, populated_pdf_dir):
        """Test all results have state='fl'."""
        collector.pdf_dir = Path(populated_pdf_dir)
        manifest = collector.catalog_pdfs(compute_hashes=False)
        results = collector._convert_to_lab_results(manifest)
        for r in results:
            assert r['state'] == 'fl'

    def test_convert_source_is_terplife(self, collector, populated_pdf_dir):
        """Test all results have source='terplife_labs'."""
        collector.pdf_dir = Path(populated_pdf_dir)
        manifest = collector.catalog_pdfs(compute_hashes=False)
        results = collector._convert_to_lab_results(manifest)
        for r in results:
            assert r['source'] == 'terplife_labs'

    def test_convert_lab_metadata(self, collector, populated_pdf_dir):
        """Test results include lab metadata."""
        collector.pdf_dir = Path(populated_pdf_dir)
        manifest = collector.catalog_pdfs(compute_hashes=False)
        results = collector._convert_to_lab_results(manifest)
        for r in results:
            assert r.get('lab') == 'TerpLife Labs'

    def test_convert_coa_url(self, collector, populated_pdf_dir):
        """Test results include COA URL pointing to Google Drive."""
        collector.pdf_dir = Path(populated_pdf_dir)
        manifest = collector.catalog_pdfs(compute_hashes=False)
        results = collector._convert_to_lab_results(manifest)
        for r in results:
            assert 'drive.google.com' in r.get('coa_url', '')

    def test_convert_empty_manifest(self, collector):
        """Test converting an empty manifest produces empty list."""
        empty = pd.DataFrame(columns=MANIFEST_COLUMNS)
        results = collector._convert_to_lab_results(empty)
        assert results == []

    def test_convert_ids_are_unique(self, collector, populated_pdf_dir):
        """Test all generated IDs are unique."""
        collector.pdf_dir = Path(populated_pdf_dir)
        manifest = collector.catalog_pdfs(compute_hashes=False)
        results = collector._convert_to_lab_results(manifest)
        ids = [r['id'] for r in results]
        assert len(ids) == len(set(ids)), 'Duplicate IDs found'


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Full Pipeline (get_results)                                ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestGetResults:
    """Test the full get_results() pipeline."""

    def test_get_results_with_existing_pdfs(self, collector, populated_pdf_dir):
        """Test full pipeline with pre-existing PDFs."""
        collector.pdf_dir = Path(populated_pdf_dir)
        df = collector.get_results(save_results=False)
        assert len(df) == 5
        assert 'id' in df.columns
        assert 'state' in df.columns

    def test_get_results_with_archive_merge(
        self, collector, populated_pdf_dir, temp_archive_dir, sample_zip
    ):
        """Test full pipeline with archive merging."""
        collector.pdf_dir = Path(populated_pdf_dir)
        df = collector.get_results(
            archive_dir=temp_archive_dir,
            save_results=False,
        )
        # 5 existing + 3 from ZIP = 8
        assert len(df) == 8

    def test_get_results_catalog_only(self, collector, populated_pdf_dir):
        """Test catalog_only mode returns manifest."""
        collector.pdf_dir = Path(populated_pdf_dir)
        df = collector.get_results(catalog_only=True)
        assert 'file_name' in df.columns
        assert 'file_hash' in df.columns
        assert len(df) == 5

    def test_get_results_saves_csv(self, collector, populated_pdf_dir):
        """Test that results are saved to CSV when requested."""
        collector.pdf_dir = Path(populated_pdf_dir)
        collector.get_results(save_results=True)
        datasets_dir = str(collector.datasets_dir)
        csv_files = [
            f for f in os.listdir(datasets_dir)
            if f.startswith('fl-terplife') and f.endswith('.csv')
        ]
        assert len(csv_files) >= 1

    def test_get_results_idempotent(self, collector, populated_pdf_dir):
        """Test running twice produces the same result count."""
        collector.pdf_dir = Path(populated_pdf_dir)
        df1 = collector.get_results(save_results=False)
        df2 = collector.get_results(save_results=False)
        assert len(df1) == len(df2)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Archive Stats                                              ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestArchiveStats:
    """Test archive statistics reporting."""

    def test_stats_empty_dir(self, collector):
        """Test stats for an empty PDF directory."""
        stats = collector.get_archive_stats()
        assert stats['total_pdfs'] == 0
        assert stats['total_size_bytes'] == 0

    def test_stats_populated_dir(self, collector, populated_pdf_dir):
        """Test stats for a populated PDF directory."""
        collector.pdf_dir = Path(populated_pdf_dir)
        stats = collector.get_archive_stats()
        assert stats['total_pdfs'] == 5
        assert stats['total_size_bytes'] > 0
        assert stats['numeric_id_min'] == 10000
        assert stats['numeric_id_max'] == 10004

    def test_stats_manifest_exists(self, collector, populated_pdf_dir):
        """Test stats detect an existing manifest."""
        collector.pdf_dir = Path(populated_pdf_dir)
        collector.catalog_pdfs()
        stats = collector.get_archive_stats()
        assert stats['manifest_exists'] is True
        assert stats['manifest_entries'] == 5


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Edge Cases                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_catalog_nonexistent_pdf_dir(self, temp_data_dir):
        """Test cataloging when PDF dir doesn't exist yet."""
        c = TerpLifeLabsCollector(
            data_dir=temp_data_dir,
            pdf_dir='/definitely/does/not/exist/pdfs',
        )
        # catalog_pdfs should handle gracefully.
        manifest = c.catalog_pdfs()
        assert len(manifest) == 0

    def test_merge_nested_zip_structure(self, collector, temp_archive_dir):
        """Test ZIP with nested directory structure."""
        zip_path = os.path.join(temp_archive_dir, 'COAS-nested.zip')
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr('level1/level2/deep.pdf', b'%PDF-1.4 deep nested')
            zf.writestr('shallow.pdf', b'%PDF-1.4 shallow')
        stats = collector.merge_archives(temp_archive_dir)
        assert stats['pdfs_new'] == 2

    def test_large_numeric_sample_ids(self, collector, temp_pdf_dir):
        """Test handling of large numeric sample IDs."""
        collector.pdf_dir = Path(temp_pdf_dir)
        for sid in ['1', '99999', '100000', '9999999']:
            filepath = os.path.join(temp_pdf_dir, f'{sid}.pdf')
            with open(filepath, 'wb') as f:
                f.write(b'%PDF-1.4 content ' + sid.encode())

        manifest = collector.catalog_pdfs(compute_hashes=False)
        sample_ids = manifest['sample_id'].tolist()
        assert '1' in sample_ids
        assert '9999999' in sample_ids

    def test_special_characters_in_filename(self, collector, temp_pdf_dir):
        """Test handling of special characters in filenames."""
        collector.pdf_dir = Path(temp_pdf_dir)
        filepath = os.path.join(temp_pdf_dir, 'Sample (retest) v2.pdf')
        with open(filepath, 'wb') as f:
            f.write(b'%PDF-1.4 special chars')

        manifest = collector.catalog_pdfs(compute_hashes=False)
        assert len(manifest) == 1
        assert manifest.iloc[0]['sample_id'] == 'Sample (retest) v2'

    def test_manifest_survives_interrupted_catalog(self, collector, populated_pdf_dir):
        """Test that manifest is saved even if cataloging is interrupted."""
        collector.pdf_dir = Path(populated_pdf_dir)

        # First catalog creates the manifest.
        collector.catalog_pdfs()
        assert os.path.exists(collector.manifest_path)

        # Delete one PDF — manifest should still load.
        os.remove(os.path.join(populated_pdf_dir, '10000.pdf'))
        manifest = collector.catalog_pdfs(incremental=True)
        # Original 5 entries remain in manifest (incremental doesn't remove).
        # But no new ones are added since remaining 4 are already known.
        assert len(manifest) >= 4


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Integration (requires network)                             ║
# ╚══════════════════════════════════════════════════════════════════╝

@pytest.mark.integration
class TestTerpLifeIntegration:
    """Integration tests that verify the Google Drive source.

    These tests require network access and are skipped in CI.
    Run with: pytest -m integration
    """

    def test_gdrive_folder_accessible(self):
        """Test that the Google Drive folder URL is reachable."""
        import requests
        response = requests.head(
            GDRIVE_FOLDER_URL,
            headers=DEFAULT_HEADERS,
            timeout=15,
            allow_redirects=True,
        )
        assert response.status_code == 200

    def test_website_reachable(self):
        """Test that the TerpLife website is reachable."""
        import requests
        response = requests.get(
            WEBSITE_URL,
            headers=DEFAULT_HEADERS,
            timeout=15,
        )
        assert response.status_code == 200

    def test_website_mentions_gdrive_link(self):
        """Test that the TerpLife website still links to Google Drive."""
        import requests
        response = requests.get(
            WEBSITE_URL,
            headers=DEFAULT_HEADERS,
            timeout=15,
        )
        assert 'drive.google.com' in response.text

    def test_inline_unit_tests_pass(self):
        """Run the inline smoke tests as a sanity check."""
        from get_results_fl_terplife import run_unit_tests
        run_unit_tests()  # Will raise AssertionError on failure.


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Inline Tests Runner                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestInlineTests:
    """Test that inline smoke tests from the module work."""

    def test_inline_unit_tests(self):
        """Run the module's built-in unit tests."""
        from get_results_fl_terplife import run_unit_tests
        run_unit_tests()

    def test_inline_integration_test(self):
        """Run the module's built-in integration test."""
        from get_results_fl_terplife import run_integration_test
        run_integration_test()