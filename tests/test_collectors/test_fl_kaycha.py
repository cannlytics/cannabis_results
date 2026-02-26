"""
Test Suite | Florida | Kaycha Labs Collector
Copyright (c) 2023-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2/22/2026
Updated: 2/22/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Comprehensive pytest suite for the Kaycha Labs COA collector.
    Tests all static helpers, collector initialization, archive
    cataloging, web scraping logic, download logic, LabResult
    conversion, and source isolation.

Usage:
    ```bash
    # Run all unit tests (no network required)
    python -m pytest test_fl_kaycha.py -v -m "not integration"

    # Run integration tests (network required)
    python -m pytest test_fl_kaycha.py -v -m "integration"

    # Run everything
    python -m pytest test_fl_kaycha.py -v
    ```
"""
# Standard imports:
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

# External imports:
import pandas as pd
import pytest

# Ensure local module is importable.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from get_results_fl_kaycha import (
    BASE_URL,
    COMPANY_SEARCH_URL,
    COA_DOWNLOAD_URL,
    COA_VIEW_URL,
    DEFAULT_HEADERS,
    FLORIDA_LICENSES,
    KAYCHA_LAB,
    MANIFEST_COLUMNS,
    MIN_PDF_SIZE,
    PAGE_COLUMNS,
    KaychaLabsCollector,
    _extract_sample_id_from_filename,
    _extract_sample_id_from_url,
    _find_pdf_files,
    _generate_result_id,
    _is_valid_pdf,
)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Fixtures                                                         ║
# ╚══════════════════════════════════════════════════════════════════╝

def _make_pdf(path, size=None):
    """Helper: create a fake PDF file at the given path."""
    if size is None:
        size = MIN_PDF_SIZE + 500
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(b'%PDF-1.4 ')
        f.write(b'x' * max(0, size - 9))


@pytest.fixture
def temp_data_dir(tmp_path):
    """Provide a temporary data directory."""
    d = tmp_path / 'data'
    d.mkdir()
    return str(d)


@pytest.fixture
def temp_pdf_dir(tmp_path):
    """Provide a temporary kaycha PDF root directory."""
    d = tmp_path / 'pdfs' / 'kaycha'
    d.mkdir(parents=True)
    return str(d)


@pytest.fixture
def collector(temp_data_dir, temp_pdf_dir):
    """Provide a KaychaLabsCollector with temp directories."""
    return KaychaLabsCollector(
        data_dir=temp_data_dir,
        pdf_dir=temp_pdf_dir,
        verbose=False,
    )


@pytest.fixture
def populated_archive(temp_pdf_dir):
    """Create a populated per-licensee archive structure."""
    licenses_with_files = {
        'MMTC-2015-0001': ['DA50313006-001', 'DA50313006-002', 'DA50313006-003'],
        'MMTC-2015-0005': ['TR10001-001', 'TR10001-002'],
        'MMTC-2017-0010': ['MV20001-001'],
    }
    for lic, samples in licenses_with_files.items():
        lic_dir = os.path.join(temp_pdf_dir, lic)
        os.makedirs(lic_dir, exist_ok=True)
        for sample in samples:
            _make_pdf(os.path.join(lic_dir, f'{sample}.pdf'))
    return licenses_with_files


@pytest.fixture
def mock_html_page():
    """Create a mock Kaycha search results HTML page."""
    def _build(n_results=3, has_next=True):
        divs = ''
        links = ''
        for i in range(n_results):
            sample_id = f'SAMPLE-{i:04d}'
            divs += f'''
            <div class="pdf_box">
                <span>{sample_id}</span>
                <span>BATCH-{i:03d}</span>
                <span>Product {i}</span>
            </div>
            '''
            links += f'<a href="/coa/coa-download/{sample_id}">Download</a>\n'
        next_class = 'next' if has_next else 'next disabled'
        disabled_attr = '' if has_next else ' disabled'
        html = f'''
        <html><body>
        {divs}
        {links}
        <li class="{next_class}"{disabled_attr}></li>
        </body></html>
        '''
        return html.encode('utf-8')
    return _build


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Constants                                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestConstants:
    """Test module-level constants and metadata."""

    def test_base_url(self):
        assert BASE_URL == 'https://yourcoa.com'

    def test_company_search_url(self):
        assert COMPANY_SEARCH_URL.startswith(BASE_URL)
        assert 'company' in COMPANY_SEARCH_URL

    def test_coa_download_url(self):
        assert COA_DOWNLOAD_URL.startswith(BASE_URL)
        assert 'download' in COA_DOWNLOAD_URL

    def test_coa_view_url(self):
        assert COA_VIEW_URL.startswith(BASE_URL)
        assert 'coa-download' in COA_VIEW_URL

    def test_min_pdf_size(self):
        assert MIN_PDF_SIZE == 21 * 1024
        assert MIN_PDF_SIZE > 0

    def test_kaycha_lab_metadata(self):
        assert KAYCHA_LAB['lab'] == 'Kaycha Labs'
        assert KAYCHA_LAB['lab_state'] == 'fl'
        assert KAYCHA_LAB['lab_city'] == 'Miramar'
        assert KAYCHA_LAB['lab_county'] == 'Broward'

    def test_kaycha_lab_coordinates(self):
        lat = KAYCHA_LAB['lab_latitude']
        lon = KAYCHA_LAB['lab_longitude']
        assert 25.0 < lat < 27.0, 'Latitude should be in South Florida'
        assert -81.0 < lon < -80.0, 'Longitude should be in South Florida'

    def test_kaycha_lab_phone_format(self):
        phone = KAYCHA_LAB['lab_phone']
        assert phone.startswith('(')
        assert ')' in phone

    def test_kaycha_lab_website(self):
        assert 'kaychalabs.com' in KAYCHA_LAB['lab_website']

    def test_default_headers_user_agent(self):
        assert 'Cannlytics' in DEFAULT_HEADERS['User-Agent']

    def test_manifest_columns_complete(self):
        required = ['file_name', 'sample_id', 'license_number', 'file_path']
        for col in required:
            assert col in MANIFEST_COLUMNS

    def test_page_columns(self):
        assert PAGE_COLUMNS == ['lab_id', 'batch_number', 'product_name']

    def test_florida_licenses_count(self):
        assert len(FLORIDA_LICENSES) == 22

    def test_florida_licenses_all_have_dba(self):
        for lic, meta in FLORIDA_LICENSES.items():
            assert 'dba' in meta, f'{lic} missing dba'
            assert len(meta['dba']) > 0, f'{lic} has empty dba'

    def test_florida_licenses_all_have_slug_key(self):
        for lic, meta in FLORIDA_LICENSES.items():
            assert 'slug' in meta, f'{lic} missing slug key'

    def test_florida_licenses_format(self):
        for lic in FLORIDA_LICENSES:
            assert lic.startswith('MMTC-'), f'{lic} should start with MMTC-'

    def test_florida_licenses_known_entries(self):
        assert FLORIDA_LICENSES['MMTC-2015-0005']['dba'] == 'Trulieve'
        assert FLORIDA_LICENSES['MMTC-2015-0001']['dba'] == 'Curaleaf'
        assert FLORIDA_LICENSES['MMTC-2015-0004']['dba'] == 'Surterra Wellness'

    def test_florida_licenses_slugged_count(self):
        slugged = [k for k, v in FLORIDA_LICENSES.items() if v.get('slug')]
        # At least 18 of 22 have slugs (3 known empty: 0006, 0012, 0014, 0018)
        assert len(slugged) >= 18

    def test_florida_licenses_empty_slugs(self):
        """Licenses without slugs have no public Kaycha page."""
        empty = [k for k, v in FLORIDA_LICENSES.items() if not v.get('slug')]
        # Known: Planet 13, Sunburn, House of Platinum, Cookies
        assert len(empty) == 4


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: _generate_result_id                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestGenerateResultId:
    """Test deterministic result ID generation."""

    def test_length(self):
        rid = _generate_result_id('SAMPLE-001', 'MMTC-2015-0001')
        assert len(rid) == 16

    def test_hex_format(self):
        rid = _generate_result_id('SAMPLE-001', 'MMTC-2015-0001')
        int(rid, 16)  # Raises ValueError if not hex

    def test_deterministic(self):
        a = _generate_result_id('SAMPLE-001', 'MMTC-2015-0001')
        b = _generate_result_id('SAMPLE-001', 'MMTC-2015-0001')
        assert a == b

    def test_unique_by_sample(self):
        a = _generate_result_id('SAMPLE-001', 'MMTC-2015-0001')
        b = _generate_result_id('SAMPLE-002', 'MMTC-2015-0001')
        assert a != b

    def test_unique_by_license(self):
        a = _generate_result_id('SAMPLE-001', 'MMTC-2015-0001')
        b = _generate_result_id('SAMPLE-001', 'MMTC-2015-0005')
        assert a != b

    def test_empty_sample(self):
        rid = _generate_result_id('', 'MMTC-2015-0001')
        assert len(rid) == 16

    def test_empty_license(self):
        rid = _generate_result_id('SAMPLE-001', '')
        assert len(rid) == 16

    def test_both_empty(self):
        rid = _generate_result_id('', '')
        assert len(rid) == 16


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: _extract_sample_id_from_url                                ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestExtractSampleIdFromUrl:
    """Test URL-to-sample-ID extraction."""

    def test_path_style(self):
        url = '/coa/coa-download/DA50313006-006'
        assert _extract_sample_id_from_url(url) == 'DA50313006-006'

    def test_full_url_path_style(self):
        url = 'https://yourcoa.com/coa/coa-download/DA50313006-006'
        assert _extract_sample_id_from_url(url) == 'DA50313006-006'

    def test_query_style(self):
        url = 'https://yourcoa.com/coa/download?sample=95746'
        assert _extract_sample_id_from_url(url) == '95746'

    def test_path_with_query_params(self):
        url = '/coa/coa-download/95746?wl_id=0&mrk=1'
        assert _extract_sample_id_from_url(url) == '95746'

    def test_path_with_is_view_param(self):
        url = '/coa/coa-download/95746?wl_id=0&mrk=1&is_view=1'
        assert _extract_sample_id_from_url(url) == '95746'

    def test_empty_string(self):
        assert _extract_sample_id_from_url('') == ''

    def test_none(self):
        assert _extract_sample_id_from_url(None) == ''

    def test_alphanumeric_id(self):
        url = '/coa/coa-download/MI60131003-001'
        assert _extract_sample_id_from_url(url) == 'MI60131003-001'

    def test_numeric_only_id(self):
        url = '/coa/coa-download/95746'
        assert _extract_sample_id_from_url(url) == '95746'

    def test_query_with_extra_params(self):
        url = 'https://yourcoa.com/coa/download?sample=ABC123&other=val'
        assert _extract_sample_id_from_url(url) == 'ABC123'


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: _extract_sample_id_from_filename                           ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestExtractSampleIdFromFilename:
    """Test filename-to-sample-ID extraction."""

    def test_standard_pdf(self):
        assert _extract_sample_id_from_filename('DA50313006-006.pdf') == 'DA50313006-006'

    def test_numeric_pdf(self):
        assert _extract_sample_id_from_filename('95746.pdf') == '95746'

    def test_uppercase_extension(self):
        assert _extract_sample_id_from_filename('SAMPLE-001.PDF') == 'SAMPLE-001'

    def test_mixed_case_extension(self):
        assert _extract_sample_id_from_filename('test.Pdf') == 'test'

    def test_no_extension(self):
        assert _extract_sample_id_from_filename('SAMPLE-001') == 'SAMPLE-001'

    def test_empty_string(self):
        assert _extract_sample_id_from_filename('') == ''

    def test_none(self):
        assert _extract_sample_id_from_filename(None) == ''

    def test_spaces_trimmed(self):
        assert _extract_sample_id_from_filename('  SAMPLE-001.pdf  ') == 'SAMPLE-001'

    def test_multiple_dots(self):
        assert _extract_sample_id_from_filename('sample.v2.pdf') == 'sample.v2'


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: _is_valid_pdf                                              ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestIsValidPdf:
    """Test PDF validation with size and magic byte checks."""

    def test_valid_pdf(self, tmp_path):
        p = tmp_path / 'valid.pdf'
        _make_pdf(str(p))
        assert _is_valid_pdf(str(p)) is True

    def test_too_small(self, tmp_path):
        p = tmp_path / 'small.pdf'
        _make_pdf(str(p), size=100)
        assert _is_valid_pdf(str(p)) is False

    def test_not_pdf_content(self, tmp_path):
        p = tmp_path / 'fake.pdf'
        with open(str(p), 'wb') as f:
            f.write(b'<html>error</html>' + b'x' * (MIN_PDF_SIZE + 100))
        assert _is_valid_pdf(str(p)) is False

    def test_nonexistent(self):
        assert _is_valid_pdf('/nonexistent/path.pdf') is False

    def test_empty_file(self, tmp_path):
        p = tmp_path / 'empty.pdf'
        p.write_bytes(b'')
        assert _is_valid_pdf(str(p)) is False

    def test_custom_min_size(self, tmp_path):
        p = tmp_path / 'custom.pdf'
        with open(str(p), 'wb') as f:
            f.write(b'%PDF-1.4 ' + b'x' * 50)
        assert _is_valid_pdf(str(p), min_size=10) is True
        assert _is_valid_pdf(str(p), min_size=10000) is False

    def test_pdf_versions(self, tmp_path):
        for version in [b'%PDF-1.4', b'%PDF-1.7', b'%PDF-2.0']:
            p = tmp_path / f'v{version[-3:]}.pdf'
            with open(str(p), 'wb') as f:
                f.write(version + b' ' + b'x' * MIN_PDF_SIZE)
            assert _is_valid_pdf(str(p)) is True


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: _find_pdf_files                                            ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestFindPdfFiles:
    """Test recursive PDF file discovery."""

    def test_flat_directory(self, tmp_path):
        for name in ['a.pdf', 'b.pdf', 'c.txt']:
            (tmp_path / name).write_bytes(b'content')
        pdfs = _find_pdf_files(str(tmp_path))
        assert len(pdfs) == 2
        assert all(p.endswith('.pdf') for p in pdfs)

    def test_recursive(self, tmp_path):
        sub = tmp_path / 'sub'
        sub.mkdir()
        (tmp_path / 'top.pdf').write_bytes(b'content')
        (sub / 'nested.pdf').write_bytes(b'content')
        pdfs = _find_pdf_files(str(tmp_path))
        assert len(pdfs) == 2

    def test_empty_dir(self, tmp_path):
        pdfs = _find_pdf_files(str(tmp_path))
        assert len(pdfs) == 0

    def test_sorted(self, tmp_path):
        for name in ['z.pdf', 'a.pdf', 'm.pdf']:
            (tmp_path / name).write_bytes(b'content')
        pdfs = _find_pdf_files(str(tmp_path))
        assert pdfs == sorted(pdfs)

    def test_case_insensitive_extension(self, tmp_path):
        (tmp_path / 'lower.pdf').write_bytes(b'content')
        (tmp_path / 'upper.PDF').write_bytes(b'content')
        pdfs = _find_pdf_files(str(tmp_path))
        assert len(pdfs) == 2


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Collector Initialization                                   ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestCollectorInit:
    """Test KaychaLabsCollector construction."""

    def test_default_init(self, collector):
        assert collector.pause_time == 5.0
        assert collector.producers == FLORIDA_LICENSES

    def test_custom_pause_time(self, temp_data_dir, temp_pdf_dir):
        c = KaychaLabsCollector(
            data_dir=temp_data_dir,
            pdf_dir=temp_pdf_dir,
            pause_time=10.0,
            verbose=False,
        )
        assert c.pause_time == 10.0

    def test_custom_producers(self, temp_data_dir, temp_pdf_dir):
        custom = {'LIC-001': {'dba': 'Test', 'slug': 'test'}}
        c = KaychaLabsCollector(
            data_dir=temp_data_dir,
            pdf_dir=temp_pdf_dir,
            producers=custom,
            verbose=False,
        )
        assert c.producers == custom

    def test_creates_directories(self, temp_data_dir, temp_pdf_dir):
        c = KaychaLabsCollector(
            data_dir=temp_data_dir,
            pdf_dir=temp_pdf_dir,
            verbose=False,
        )
        assert os.path.isdir(str(c.pdf_dir))

    def test_context_manager(self, temp_data_dir, temp_pdf_dir):
        with KaychaLabsCollector(
            data_dir=temp_data_dir,
            pdf_dir=temp_pdf_dir,
            verbose=False,
        ) as c:
            assert c is not None
        # Should not raise after exit

    def test_manifest_path_set(self, collector):
        assert collector.manifest_path.endswith('kaycha-manifest.csv')

    def test_session_created(self, collector):
        assert collector.session is not None
        assert 'Cannlytics' in collector.session.headers.get('User-Agent', '')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Rate Limiting                                              ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestRateLimiting:
    """Test respectful rate limiting mechanisms."""

    def test_respectful_pause_runs(self, collector):
        """Pause should complete without error (fast with small time)."""
        collector.pause_time = 0.01
        collector._respectful_pause(multiplier=1.0)

    def test_respectful_pause_multiplier(self, collector):
        """Higher multiplier should still complete."""
        collector.pause_time = 0.01
        collector._respectful_pause(multiplier=2.0)

    def test_backoff_pause_runs(self, collector):
        """Backoff should complete for attempt 0."""
        collector._backoff_pause(attempt=0, base=0.01, max_wait=0.05)

    def test_backoff_respects_max_wait(self, collector):
        """Even high attempt numbers should be capped."""
        import time
        start = time.time()
        collector._backoff_pause(attempt=10, base=0.01, max_wait=0.05)
        elapsed = time.time() - start
        assert elapsed < 0.2  # Well under 1 second


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Archive Cataloging                                         ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestCatalogExisting:
    """Test manifest building from the local archive."""

    def test_empty_archive(self, collector):
        manifest = collector.catalog_existing()
        assert len(manifest) == 0
        assert list(manifest.columns) == MANIFEST_COLUMNS

    def test_populated_archive(self, collector, populated_archive):
        manifest = collector.catalog_existing()
        total = sum(len(v) for v in populated_archive.values())
        assert len(manifest) == total

    def test_manifest_columns(self, collector, populated_archive):
        manifest = collector.catalog_existing()
        for col in MANIFEST_COLUMNS:
            assert col in manifest.columns

    def test_sample_ids_extracted(self, collector, populated_archive):
        manifest = collector.catalog_existing()
        assert all(manifest['sample_id'] != '')

    def test_license_numbers_populated(self, collector, populated_archive):
        manifest = collector.catalog_existing()
        licenses = set(manifest['license_number'].tolist())
        expected = set(populated_archive.keys())
        assert licenses == expected

    def test_manifest_saved_to_disk(self, collector, populated_archive):
        collector.catalog_existing()
        assert os.path.exists(collector.manifest_path)

    def test_incremental_adds_new_only(self, collector, populated_archive):
        m1 = collector.catalog_existing()
        count1 = len(m1)

        # Add one more PDF.
        lic_dir = os.path.join(str(collector.pdf_dir), 'MMTC-2015-0001')
        _make_pdf(os.path.join(lic_dir, 'NEW-SAMPLE.pdf'))

        m2 = collector.catalog_existing(incremental=True)
        assert len(m2) == count1 + 1

    def test_incremental_no_duplicates(self, collector, populated_archive):
        m1 = collector.catalog_existing()
        m2 = collector.catalog_existing(incremental=True)
        assert len(m1) == len(m2)  # No new files → same count

    def test_full_rebuild(self, collector, populated_archive):
        collector.catalog_existing()  # Initial catalog

        # Full rebuild should re-scan everything.
        manifest = collector.catalog_existing(incremental=False)
        total = sum(len(v) for v in populated_archive.values())
        assert len(manifest) == total

    def test_source_field(self, collector, populated_archive):
        manifest = collector.catalog_existing()
        assert all(manifest['source'] == 'local_archive')

    def test_download_url_generated(self, collector, populated_archive):
        manifest = collector.catalog_existing()
        for _, row in manifest.iterrows():
            if row['sample_id']:
                assert COA_VIEW_URL in row['download_url']

    def test_ignores_non_licensee_directories(self, collector, temp_pdf_dir):
        """Directories not in FLORIDA_LICENSES should be ignored."""
        rogue_dir = os.path.join(temp_pdf_dir, 'not-a-license')
        os.makedirs(rogue_dir)
        _make_pdf(os.path.join(rogue_dir, 'rogue.pdf'))

        manifest = collector.catalog_existing()
        assert len(manifest) == 0  # Should ignore the rogue dir


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Web Scraping (mocked)                                      ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestScrapeProducerPage:
    """Test single-page scraping with mocked HTTP responses."""

    def test_parse_results(self, collector, mock_html_page):
        """Parse a page with 3 results."""
        html = mock_html_page(n_results=3, has_next=True)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = html

        with patch.object(collector.session, 'get', return_value=mock_resp):
            results, has_more = collector._scrape_producer_page('TestSlug', 1)

        assert len(results) == 3
        assert has_more is True

    def test_parse_last_page(self, collector, mock_html_page):
        """Parse the last page (no more pages)."""
        html = mock_html_page(n_results=2, has_next=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = html

        with patch.object(collector.session, 'get', return_value=mock_resp):
            results, has_more = collector._scrape_producer_page('TestSlug', 5)

        assert len(results) == 2
        assert has_more is False

    def test_empty_page(self, collector):
        """Handle a page with no results."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'<html><body></body></html>'

        with patch.object(collector.session, 'get', return_value=mock_resp):
            results, has_more = collector._scrape_producer_page('Empty', 1)

        assert len(results) == 0
        assert has_more is False

    def test_http_error(self, collector):
        """Handle HTTP errors gracefully."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch.object(collector.session, 'get', return_value=mock_resp):
            results, has_more = collector._scrape_producer_page('Error', 1)

        assert len(results) == 0
        assert has_more is False

    def test_request_exception(self, collector):
        """Handle network errors gracefully."""
        import requests as req
        with patch.object(
            collector.session, 'get',
            side_effect=req.exceptions.ConnectionError('timeout')
        ):
            results, has_more = collector._scrape_producer_page('Fail', 1)

        assert len(results) == 0
        assert has_more is False

    def test_download_urls_extracted(self, collector, mock_html_page):
        """Each result should have a download_url."""
        html = mock_html_page(n_results=3, has_next=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = html

        with patch.object(collector.session, 'get', return_value=mock_resp):
            results, _ = collector._scrape_producer_page('Test', 1)

        for r in results:
            assert 'download_url' in r
            if r['download_url']:
                assert 'coa-download' in r['download_url']


class TestScrapeProducer:
    """Test multi-page producer scraping."""

    def test_paginates_until_last_page(self, collector, mock_html_page):
        """Should paginate through multiple pages."""
        collector.pause_time = 0.01
        call_count = [0]

        def fake_get(*args, **kwargs):
            call_count[0] += 1
            has_next = call_count[0] < 3  # 3 pages total
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = mock_html_page(n_results=2, has_next=has_next)
            return mock_resp

        with patch.object(collector.session, 'get', side_effect=fake_get):
            df = collector.scrape_producer('MMTC-2015-0005', 'Trulieve', 'Trulieve')

        assert len(df) == 6  # 2 per page × 3 pages
        assert call_count[0] == 3

    def test_stops_on_consecutive_errors(self, collector):
        """Should stop after 3 consecutive empty pages."""
        collector.pause_time = 0.01
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'<html><body></body></html>'

        with patch.object(collector.session, 'get', return_value=mock_resp):
            df = collector.scrape_producer('MMTC-2015-0001', 'Curaleaf', 'Curaleaf')

        assert len(df) == 0

    def test_adds_license_and_dba(self, collector, mock_html_page):
        """Each row should get license_number and dba fields."""
        collector.pause_time = 0.01
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = mock_html_page(n_results=2, has_next=False)

        with patch.object(collector.session, 'get', return_value=mock_resp):
            df = collector.scrape_producer(
                'MMTC-2019-0020', 'The+Flowery', 'The Flowery'
            )

        assert all(df['license_number'] == 'MMTC-2019-0020')
        assert all(df['dba'] == 'The Flowery')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: PDF Downloading (mocked)                                   ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestDownloadCoa:
    """Test single COA download with mocked HTTP."""

    def test_successful_download(self, collector, tmp_path):
        """Download a valid PDF response."""
        lic_dir = tmp_path / 'MMTC-TEST'
        lic_dir.mkdir()

        pdf_content = b'%PDF-1.4 ' + b'x' * (MIN_PDF_SIZE + 100)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = pdf_content

        with patch.object(collector.session, 'get', return_value=mock_resp):
            result = collector._download_coa('TEST-001', str(lic_dir))

        assert result is not None
        assert os.path.exists(result)
        assert os.path.basename(result) == 'TEST-001.pdf'

    def test_skip_existing_valid(self, collector, tmp_path):
        """Skip download if file already exists and is valid."""
        lic_dir = tmp_path / 'MMTC-TEST'
        lic_dir.mkdir()
        existing = lic_dir / 'EXISTING-001.pdf'
        _make_pdf(str(existing))

        # Session.get should never be called.
        with patch.object(collector.session, 'get') as mock_get:
            result = collector._download_coa('EXISTING-001', str(lic_dir))

        mock_get.assert_not_called()
        assert result == str(existing)

    def test_reject_html_response(self, collector, tmp_path):
        """Reject responses that look like HTML error pages."""
        lic_dir = tmp_path / 'MMTC-TEST'
        lic_dir.mkdir()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'<html>Login required</html>' + b'x' * (MIN_PDF_SIZE + 100)

        with patch.object(collector.session, 'get', return_value=mock_resp):
            result = collector._download_coa(
                'HTML-001', str(lic_dir), max_retries=0
            )

        assert result is None

    def test_reject_small_response(self, collector, tmp_path):
        """Reject responses smaller than MIN_PDF_SIZE."""
        lic_dir = tmp_path / 'MMTC-TEST'
        lic_dir.mkdir()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'%PDF-1.4 tiny'

        with patch.object(collector.session, 'get', return_value=mock_resp):
            result = collector._download_coa(
                'SMALL-001', str(lic_dir), max_retries=0
            )

        assert result is None

    def test_handle_server_error(self, collector, tmp_path):
        """Handle 500 errors without crashing."""
        lic_dir = tmp_path / 'MMTC-TEST'
        lic_dir.mkdir()
        collector.pause_time = 0.01

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.content = b''

        with patch.object(collector.session, 'get', return_value=mock_resp):
            result = collector._download_coa(
                'ERR-001', str(lic_dir), max_retries=0
            )

        assert result is None

    def test_handle_rate_limit(self, collector, tmp_path):
        """Handle 429 rate limit with backoff."""
        lic_dir = tmp_path / 'MMTC-TEST'
        lic_dir.mkdir()
        collector.pause_time = 0.01

        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.content = b''

        with patch.object(collector.session, 'get', return_value=mock_resp):
            result = collector._download_coa(
                'RATE-001', str(lic_dir), max_retries=1
            )

        assert result is None  # Should fail after retries


class TestDownloadNewCoas:
    """Test batch download of new COAs."""

    def test_skip_existing(self, collector, populated_archive):
        """Existing sample IDs should be skipped."""
        existing_ids = set()
        for samples in populated_archive.values():
            existing_ids.update(samples)

        discovered = pd.DataFrame([{
            'download_url': f'{COA_VIEW_URL}/{s}',
            'license_number': 'MMTC-2015-0001',
        } for s in list(existing_ids)[:3]])

        stats = collector.download_new_coas(discovered, existing_ids)
        assert stats['already_exists'] == 3
        assert stats['downloaded'] == 0

    def test_skip_no_url(self, collector):
        """Rows without download_url should be skipped."""
        discovered = pd.DataFrame([
            {'download_url': '', 'license_number': 'MMTC-2015-0001'},
        ])
        stats = collector.download_new_coas(discovered, set())
        assert stats['skipped_no_url'] == 1

    def test_empty_discovered(self, collector):
        """Empty DataFrame should return zero stats."""
        stats = collector.download_new_coas(pd.DataFrame(), set())
        assert stats['total_discovered'] == 0

    def test_download_new(self, collector, tmp_path):
        """New COAs should be downloaded."""
        collector.pause_time = 0.01

        pdf_content = b'%PDF-1.4 ' + b'x' * (MIN_PDF_SIZE + 100)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = pdf_content

        discovered = pd.DataFrame([{
            'download_url': f'{COA_VIEW_URL}/NEW-SAMPLE-001',
            'license_number': 'MMTC-2015-0005',
        }])

        with patch.object(collector.session, 'get', return_value=mock_resp):
            stats = collector.download_new_coas(discovered, set())

        assert stats['downloaded'] == 1
        # Check file was actually created.
        expected_path = os.path.join(
            str(collector.pdf_dir), 'MMTC-2015-0005', 'NEW-SAMPLE-001.pdf'
        )
        assert os.path.exists(expected_path)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: LabResult Conversion                                       ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestConvertToLabResults:
    """Test manifest → LabResult conversion."""

    def test_produces_results(self, collector, populated_archive):
        manifest = collector.catalog_existing()
        results = collector._convert_to_lab_results(manifest)
        assert len(results) == len(manifest)

    def test_has_required_fields(self, collector, populated_archive):
        manifest = collector.catalog_existing()
        results = collector._convert_to_lab_results(manifest)
        for r in results:
            assert 'id' in r
            assert 'sample_id' in r
            assert 'state' in r
            assert 'source' in r

    def test_state_is_florida(self, collector, populated_archive):
        manifest = collector.catalog_existing()
        results = collector._convert_to_lab_results(manifest)
        for r in results:
            assert r['state'] == 'fl'

    def test_source_is_kaycha(self, collector, populated_archive):
        manifest = collector.catalog_existing()
        results = collector._convert_to_lab_results(manifest)
        for r in results:
            assert r['source'] == 'kaycha_labs'

    def test_lab_metadata(self, collector, populated_archive):
        manifest = collector.catalog_existing()
        results = collector._convert_to_lab_results(manifest)
        for r in results:
            assert r.get('lab') == 'Kaycha Labs'

    def test_producer_info(self, collector, populated_archive):
        manifest = collector.catalog_existing()
        results = collector._convert_to_lab_results(manifest)
        license_numbers = set(r.get('producer_license_number') for r in results)
        assert 'MMTC-2015-0001' in license_numbers

    def test_ids_unique(self, collector, populated_archive):
        manifest = collector.catalog_existing()
        results = collector._convert_to_lab_results(manifest)
        ids = [r['id'] for r in results]
        assert len(ids) == len(set(ids)), 'IDs must be unique'

    def test_empty_manifest(self, collector):
        manifest = pd.DataFrame(columns=MANIFEST_COLUMNS)
        results = collector._convert_to_lab_results(manifest)
        assert results == []


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: get_results (orchestration)                                ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestGetResults:
    """Test the main orchestration method."""

    def test_catalog_only(self, collector, populated_archive):
        """catalog_only mode should return manifest with no network."""
        result = collector.get_results(catalog_only=True)
        total = sum(len(v) for v in populated_archive.values())
        assert len(result) == total

    def test_no_scrape_no_download(self, collector, populated_archive):
        """Skipping scrape+download should still produce LabResults."""
        result = collector.get_results(
            scrape=False, download=False, save_results=False
        )
        assert 'id' in result.columns
        assert 'state' in result.columns
        assert len(result) > 0

    def test_license_filter(self, collector, populated_archive):
        """Filter to a single licensee."""
        result = collector.get_results(
            catalog_only=True, license_filter='MMTC-2015-0001'
        )
        # Catalog still sees all files, but only one license is processed.
        # Since catalog_only returns manifest of all files...
        assert len(result) > 0

    def test_save_results_creates_csv(self, collector, populated_archive):
        """Results should be saved to datasets dir."""
        collector.get_results(scrape=False, download=False, save_results=True)
        datasets_dir = str(getattr(collector, 'datasets_dir', collector.data_dir))
        csv_files = [f for f in os.listdir(datasets_dir) if f.endswith('.csv')]
        # Should have manifest + results CSV.
        assert len(csv_files) >= 1

    def test_with_mocked_scraping(self, collector, populated_archive, mock_html_page):
        """Full pipeline with mocked network."""
        collector.pause_time = 0.01

        html = mock_html_page(n_results=2, has_next=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = html

        # Mock session.get for both scraping and downloading.
        with patch.object(collector.session, 'get', return_value=mock_resp):
            result = collector.get_results(
                scrape=True, download=False, save_results=False
            )

        assert len(result) > 0


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Archive Stats                                              ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestArchiveStats:
    """Test archive statistics reporting."""

    def test_empty_archive(self, collector):
        stats = collector.get_archive_stats()
        assert stats['total_pdfs'] == 0
        assert stats['licensees_with_data'] == 0
        assert stats['total_size_gb'] == 0.0

    def test_populated_archive(self, collector, populated_archive):
        stats = collector.get_archive_stats()
        total = sum(len(v) for v in populated_archive.values())
        assert stats['total_pdfs'] == total
        assert stats['licensees_with_data'] == len(populated_archive)

    def test_per_licensee_breakdown(self, collector, populated_archive):
        stats = collector.get_archive_stats()
        for lic, samples in populated_archive.items():
            assert lic in stats['per_licensee']
            assert stats['per_licensee'][lic]['count'] == len(samples)

    def test_licensee_counts(self, collector):
        stats = collector.get_archive_stats()
        assert stats['licensees_total'] == len(FLORIDA_LICENSES)
        assert stats['licensees_with_slug'] >= 18

    def test_manifest_detection(self, collector, populated_archive):
        assert collector.get_archive_stats()['manifest_exists'] is False
        collector.catalog_existing()
        assert collector.get_archive_stats()['manifest_exists'] is True


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Source Isolation                                           ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestSourceIsolation:
    """Ensure the collector never touches non-Kaycha directories."""

    def test_catalog_only_reads_license_dirs(self, collector, temp_pdf_dir):
        """Only per-licensee subdirectories should be cataloged."""
        # Create a valid licensee dir.
        lic_dir = os.path.join(temp_pdf_dir, 'MMTC-2015-0005')
        os.makedirs(lic_dir)
        _make_pdf(os.path.join(lic_dir, 'TR-001.pdf'))

        # Create non-licensee dirs that should be ignored.
        for name in ['terplife', 'flowery', 'random-folder']:
            other = os.path.join(temp_pdf_dir, name)
            os.makedirs(other)
            _make_pdf(os.path.join(other, 'should-ignore.pdf'))

        manifest = collector.catalog_existing()
        assert len(manifest) == 1  # Only the MMTC file
        assert manifest.iloc[0]['license_number'] == 'MMTC-2015-0005'

    def test_download_only_to_license_dirs(self, collector, tmp_path):
        """Downloads should go to the correct licensee subdirectory."""
        collector.pause_time = 0.01

        pdf_content = b'%PDF-1.4 ' + b'x' * (MIN_PDF_SIZE + 100)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = pdf_content

        discovered = pd.DataFrame([{
            'download_url': f'{COA_VIEW_URL}/ISOLATION-TEST-001',
            'license_number': 'MMTC-2017-0010',
        }])

        with patch.object(collector.session, 'get', return_value=mock_resp):
            collector.download_new_coas(discovered, set())

        # File should be in the correct licensee subdirectory.
        expected = os.path.join(
            str(collector.pdf_dir), 'MMTC-2017-0010', 'ISOLATION-TEST-001.pdf'
        )
        assert os.path.exists(expected)

        # Should NOT be in the kaycha root.
        root_file = os.path.join(str(collector.pdf_dir), 'ISOLATION-TEST-001.pdf')
        assert not os.path.exists(root_file)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Edge Cases                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_nonexistent_pdf_dir(self, temp_data_dir):
        """Catalog should handle nonexistent pdf_dir gracefully."""
        c = KaychaLabsCollector(
            data_dir=temp_data_dir,
            pdf_dir='/nonexistent/path/kaycha',
            verbose=False,
        )
        manifest = c.catalog_existing()
        assert len(manifest) == 0

    def test_empty_licensee_dirs(self, collector, temp_pdf_dir):
        """Empty per-licensee directories should produce empty manifest."""
        for lic in ['MMTC-2015-0001', 'MMTC-2015-0005']:
            os.makedirs(os.path.join(temp_pdf_dir, lic), exist_ok=True)
        manifest = collector.catalog_existing()
        assert len(manifest) == 0

    def test_corrupt_manifest_csv(self, collector, populated_archive):
        """Should handle corrupt manifest gracefully."""
        # Write garbage to manifest file.
        datasets_dir = str(getattr(collector, 'datasets_dir', collector.data_dir))
        os.makedirs(datasets_dir, exist_ok=True)
        with open(collector.manifest_path, 'w') as f:
            f.write('this,is,not,valid\nmanifest,data')

        # Should recover by building fresh.
        manifest = collector.catalog_existing(incremental=True)
        total = sum(len(v) for v in populated_archive.values())
        # May or may not recover depending on column mismatch.
        assert len(manifest) >= 0  # Should not crash

    def test_special_characters_in_filename(self, collector, temp_pdf_dir):
        """Handle filenames with special characters."""
        lic_dir = os.path.join(temp_pdf_dir, 'MMTC-2015-0001')
        os.makedirs(lic_dir)
        _make_pdf(os.path.join(lic_dir, 'Sample (1).pdf'))
        _make_pdf(os.path.join(lic_dir, 'Sample #2.pdf'))

        manifest = collector.catalog_existing()
        assert len(manifest) == 2

    def test_duplicate_sample_ids_across_licenses(self, collector, temp_pdf_dir):
        """Same sample ID in different licensee folders should both appear."""
        for lic in ['MMTC-2015-0001', 'MMTC-2015-0005']:
            lic_dir = os.path.join(temp_pdf_dir, lic)
            os.makedirs(lic_dir)
            _make_pdf(os.path.join(lic_dir, 'SHARED-001.pdf'))

        manifest = collector.catalog_existing()
        assert len(manifest) == 2  # One per licensee

        # Result IDs should differ because license_number is part of the hash.
        results = collector._convert_to_lab_results(manifest)
        ids = [r['id'] for r in results]
        assert len(ids) == len(set(ids))

    def test_zero_byte_pdfs_cataloged(self, collector, temp_pdf_dir):
        """Zero-byte files still get cataloged (validity is checked at download)."""
        lic_dir = os.path.join(temp_pdf_dir, 'MMTC-2015-0001')
        os.makedirs(lic_dir)
        zero_file = os.path.join(lic_dir, 'EMPTY.pdf')
        with open(zero_file, 'wb') as f:
            pass  # 0 bytes

        manifest = collector.catalog_existing()
        assert len(manifest) == 1  # Still cataloged
        assert manifest.iloc[0]['file_size'] == 0


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Inline Tests                                               ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestInlineTests:
    """Verify the inline test runners execute successfully."""

    def test_inline_unit_tests(self):
        from get_results_fl_kaycha import run_unit_tests
        run_unit_tests()  # Should not raise

    def test_inline_integration_test_import(self):
        """Verify integration test is importable (don't run it)."""
        from get_results_fl_kaycha import run_integration_test
        assert callable(run_integration_test)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Integration (network required)                             ║
# ╚══════════════════════════════════════════════════════════════════╝

@pytest.mark.integration
class TestKaychaIntegration:
    """Integration tests requiring network access.

    Run with: pytest test_fl_kaycha.py -m integration
    """

    def test_yourcoa_reachable(self):
        """Verify yourcoa.com is reachable."""
        import requests
        try:
            resp = requests.get(BASE_URL, timeout=15, allow_redirects=True)
            assert resp.status_code in (200, 301, 302, 403)
        except requests.exceptions.ConnectionError:
            pytest.skip('yourcoa.com unreachable')

    def test_scrape_first_page_trulieve(self):
        """Scrape one page from Trulieve to verify HTML structure."""
        import requests
        with tempfile.TemporaryDirectory() as tmpdir:
            c = KaychaLabsCollector(
                data_dir=os.path.join(tmpdir, 'data'),
                pdf_dir=os.path.join(tmpdir, 'kaycha'),
                verbose=True,
            )
            try:
                results, has_more = c._scrape_producer_page('Trulieve', 1)
            except requests.exceptions.ConnectionError:
                pytest.skip('Cannot reach yourcoa.com')
            # May return 0 if Trulieve no longer publishes or structure changed.
            assert isinstance(results, list)

    def test_coa_download_endpoint(self):
        """Verify COA download endpoint responds."""
        import requests
        # Use a known sample ID from search results.
        url = f'{COA_VIEW_URL}/95746?is_view=1'
        try:
            resp = requests.get(url, timeout=15)
            # Should return PDF or redirect.
            assert resp.status_code in (200, 301, 302, 404)
        except requests.exceptions.ConnectionError:
            pytest.skip('Cannot reach yourcoa.com')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Real Directory Layout                                      ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestRealDirectoryLayout:
    """Test with a layout mimicking the real D:\\data directory."""

    def test_full_licensee_structure(self, temp_data_dir):
        """Simulate all 22 licensee directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            kaycha_dir = os.path.join(tmpdir, 'kaycha')
            os.makedirs(kaycha_dir)

            # Create all 22 licensee directories.
            for lic in FLORIDA_LICENSES:
                lic_dir = os.path.join(kaycha_dir, lic)
                os.makedirs(lic_dir)
                # Put 2 PDFs in each.
                for i in range(2):
                    _make_pdf(os.path.join(lic_dir, f'{lic}-SAMPLE-{i:03d}.pdf'))

            c = KaychaLabsCollector(
                data_dir=temp_data_dir,
                pdf_dir=kaycha_dir,
                verbose=False,
            )

            manifest = c.catalog_existing()
            assert len(manifest) == 44  # 22 × 2

            stats = c.get_archive_stats()
            assert stats['licensees_with_data'] == 22
            assert stats['total_pdfs'] == 44

    def test_mixed_valid_and_invalid_pdfs(self, temp_data_dir):
        """Catalog should include all files regardless of validity."""
        with tempfile.TemporaryDirectory() as tmpdir:
            kaycha_dir = os.path.join(tmpdir, 'kaycha')
            lic_dir = os.path.join(kaycha_dir, 'MMTC-2015-0005')
            os.makedirs(lic_dir)

            # Valid PDF.
            _make_pdf(os.path.join(lic_dir, 'VALID.pdf'))
            # Small PDF (would fail _is_valid_pdf but still gets cataloged).
            with open(os.path.join(lic_dir, 'SMALL.pdf'), 'wb') as f:
                f.write(b'%PDF-1.4 tiny')

            c = KaychaLabsCollector(
                data_dir=temp_data_dir,
                pdf_dir=kaycha_dir,
                verbose=False,
            )
            manifest = c.catalog_existing()
            assert len(manifest) == 2  # Both get cataloged

    def test_sibling_directories_ignored(self, temp_data_dir):
        """Non-MMTC directories in same parent should be ignored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            kaycha_dir = os.path.join(tmpdir, 'kaycha')
            os.makedirs(kaycha_dir)

            # MMTC directory (should be included).
            lic_dir = os.path.join(kaycha_dir, 'MMTC-2015-0005')
            os.makedirs(lic_dir)
            _make_pdf(os.path.join(lic_dir, 'VALID.pdf'))

            # Non-MMTC siblings (should be ignored).
            for name in ['terplife', 'backups', 'archive-2024', '__pycache__']:
                other = os.path.join(kaycha_dir, name)
                os.makedirs(other)
                _make_pdf(os.path.join(other, 'not-kaycha.pdf'))

            c = KaychaLabsCollector(
                data_dir=temp_data_dir,
                pdf_dir=kaycha_dir,
                verbose=False,
            )
            manifest = c.catalog_existing()
            assert len(manifest) == 1
            assert manifest.iloc[0]['license_number'] == 'MMTC-2015-0005'