"""
Cannabis Results | Arizona Collector Tests
Copyright (c) 2024-2026 Cannlytics

Description:
    Comprehensive tests for all Arizona COA collection algorithms.

    Run unit tests (no network):
        pytest tests/test_collectors/test_az.py -v -m "not integration"

    Run integration tests (requires network + Selenium):
        pytest tests/test_collectors/test_az.py -v -m integration

    Run all tests:
        pytest tests/test_collectors/test_az.py -v

    Run specific source tests:
        pytest tests/test_collectors/test_az.py -v -k "StickySaguaro"
        pytest tests/test_collectors/test_az.py -v -k "FlowDistribution"
        pytest tests/test_collectors/test_az.py -v -k "Curaleaf"
"""
# Standard imports:
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

# External imports:
import pandas as pd
import pytest

# Ensure project root is in path.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import modules under test.
from get_results_az_sticky_saguaro import (
    StickySaguaroCollector,
    TESTING_URL,
    STICKY_SAGUARO_PRODUCER,
    DEFAULT_SCRAPE_PAUSE,
    MIN_PDF_SIZE,
    _generate_result_id,
    _hash_url,
    _clean_url,
    _is_valid_pdf,
    _is_valid_pdf_file,
    _extract_product_name_from_url,
)

from get_results_az_flow_distribution import (
    FlowDistributionCollector,
    BASE_URL as FLOW_BASE_URL,
    FLOW_PRODUCER,
    DEFAULT_BIRTH_DATE,
    _generate_search_queries,
    ProgressTracker as FlowProgressTracker,
    _generate_result_id as flow_generate_result_id,
    _hash_url as flow_hash_url,
    _is_valid_pdf as flow_is_valid_pdf,
)

from get_results_curaleaf import (
    CuraleafCollector,
    BASE_URL as CURALEAF_BASE_URL,
    CURALEAF_PRODUCER,
    _generate_search_queries as curaleaf_generate_queries,
    ProgressTracker as CuraleafProgressTracker,
    _generate_result_id as curaleaf_generate_result_id,
    _hash_url as curaleaf_hash_url,
)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Fixtures                                                         ║
# ╚══════════════════════════════════════════════════════════════════╝

@pytest.fixture
def temp_dir(tmp_path):
    """Provide a temporary directory."""
    return tmp_path


@pytest.fixture
def temp_pdf_dir(tmp_path):
    """Provide a temporary PDF directory."""
    pdf_dir = tmp_path / 'pdfs'
    pdf_dir.mkdir()
    return pdf_dir


@pytest.fixture
def temp_data_dir(tmp_path):
    """Provide a temporary data directory structure."""
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def fake_pdf_data():
    """Provide valid fake PDF binary data."""
    return b'%PDF-1.4' + b'\x00' * 10000


@pytest.fixture
def fake_invalid_pdf_data():
    """Provide invalid PDF binary data."""
    return b'<html><body>Not a PDF</body></html>'


@pytest.fixture
def fake_small_pdf_data():
    """Provide a too-small PDF."""
    return b'%PDF-1.4' + b'\x00' * 100


@pytest.fixture
def sticky_saguaro_collector(temp_data_dir, temp_pdf_dir):
    """Provide a StickySaguaroCollector with temp dirs."""
    return StickySaguaroCollector(
        pdf_dir=str(temp_pdf_dir / 'sticky-saguaro'),
        data_dir=str(temp_data_dir),
        verbose=False,
    )


@pytest.fixture
def flow_distribution_collector(temp_data_dir, temp_pdf_dir):
    """Provide a FlowDistributionCollector with temp dirs."""
    return FlowDistributionCollector(
        pdf_dir=str(temp_pdf_dir / 'flow-distribution'),
        data_dir=str(temp_data_dir),
        verbose=False,
    )


@pytest.fixture
def curaleaf_collector(temp_data_dir, temp_pdf_dir):
    """Provide a CuraleafCollector with temp dirs."""
    return CuraleafCollector(
        pdf_dir=str(temp_pdf_dir / 'curaleaf'),
        data_dir=str(temp_data_dir),
        verbose=False,
    )


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Shared Helper Tests                                              ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestHelperFunctions:
    """Tests for shared helper functions across all modules."""

    def test_generate_result_id_deterministic(self):
        """Result IDs are deterministic for the same input."""
        assert _generate_result_id('abc') == _generate_result_id('abc')

    def test_generate_result_id_length(self):
        """Result IDs are 16 characters."""
        assert len(_generate_result_id('test')) == 16

    def test_generate_result_id_unique(self):
        """Different inputs produce different IDs."""
        assert _generate_result_id('a') != _generate_result_id('b')

    def test_hash_url_deterministic(self):
        """URL hashes are deterministic."""
        url = 'https://example.com/test.pdf'
        assert _hash_url(url) == _hash_url(url)

    def test_hash_url_length(self):
        """URL hashes are 32 characters (MD5)."""
        assert len(_hash_url('test')) == 32

    def test_hash_url_unique(self):
        """Different URLs produce different hashes."""
        assert _hash_url('url1') != _hash_url('url2')

    def test_clean_url_shortcut(self):
        """Shortcut artifacts are removed from URLs."""
        assert _clean_url('test.pdf - Shortcut.lnk') == 'test.pdf'

    def test_clean_url_normal(self):
        """Normal URLs are unchanged."""
        assert _clean_url('https://example.com/test.pdf') == \
            'https://example.com/test.pdf'

    def test_clean_url_whitespace(self):
        """Whitespace is trimmed."""
        assert _clean_url('  test.pdf  ') == 'test.pdf'

    def test_is_valid_pdf_valid(self, fake_pdf_data):
        """Valid PDF data is recognized."""
        assert _is_valid_pdf(fake_pdf_data)

    def test_is_valid_pdf_html(self, fake_invalid_pdf_data):
        """HTML data is rejected."""
        assert not _is_valid_pdf(fake_invalid_pdf_data)

    def test_is_valid_pdf_too_small(self, fake_small_pdf_data):
        """Too-small PDFs are rejected."""
        assert not _is_valid_pdf(fake_small_pdf_data)

    def test_is_valid_pdf_empty(self):
        """Empty data is rejected."""
        assert not _is_valid_pdf(b'')

    def test_is_valid_pdf_file_valid(self, temp_dir, fake_pdf_data):
        """Valid PDF file is recognized."""
        path = str(temp_dir / 'test.pdf')
        with open(path, 'wb') as f:
            f.write(fake_pdf_data)
        assert _is_valid_pdf_file(path)

    def test_is_valid_pdf_file_missing(self):
        """Missing file returns False."""
        assert not _is_valid_pdf_file('/nonexistent/file.pdf')

    def test_is_valid_pdf_file_html(self, temp_dir, fake_invalid_pdf_data):
        """HTML file is rejected."""
        path = str(temp_dir / 'test.pdf')
        with open(path, 'wb') as f:
            f.write(fake_invalid_pdf_data)
        assert not _is_valid_pdf_file(path)

    def test_extract_product_name_basic(self):
        """Product name extraction from URL."""
        url = 'https://testing.stickysaguaro.com/uploads/Blue-Dream-COA.pdf'
        name = _extract_product_name_from_url(url)
        assert 'Blue' in name and 'Dream' in name

    def test_extract_product_name_empty(self):
        """Empty URL returns empty string."""
        assert _extract_product_name_from_url('') == ''

    def test_extract_product_name_no_extension(self):
        """URL without .pdf still works."""
        name = _extract_product_name_from_url(
            'https://example.com/uploads/OG-Kush'
        )
        assert 'OG' in name or 'Kush' in name


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Sticky Saguaro Tests                                             ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestStickySaguaroConstants:
    """Tests for Sticky Saguaro module constants."""

    def test_testing_url(self):
        assert TESTING_URL == 'https://testing.stickysaguaro.com/'

    def test_producer_metadata(self):
        assert STICKY_SAGUARO_PRODUCER['producer'] == 'Sticky Saguaro'
        assert STICKY_SAGUARO_PRODUCER['producer_state'] == 'az'

    def test_min_pdf_size(self):
        assert MIN_PDF_SIZE == 5 * 1024


class TestStickySaguaroCollectorInit:
    """Tests for Sticky Saguaro collector initialization."""

    def test_init_default_dirs(self, sticky_saguaro_collector):
        """Directories are created on initialization."""
        c = sticky_saguaro_collector
        assert c.pdf_dir.exists()
        assert c.datasets_dir.exists()

    def test_init_custom_pause(self, temp_data_dir, temp_pdf_dir):
        """Custom pause times are applied."""
        c = StickySaguaroCollector(
            pdf_dir=str(temp_pdf_dir / 'ss'),
            data_dir=str(temp_data_dir),
            scrape_pause=10.0,
            download_pause=5.0,
            verbose=False,
        )
        assert c.scrape_pause == 10.0
        assert c.download_pause == 5.0

    def test_init_manifest_path(self, sticky_saguaro_collector):
        """Manifest path is correctly set."""
        c = sticky_saguaro_collector
        assert 'sticky-saguaro-manifest.csv' in str(c.manifest_path)


class TestStickySaguaroCatalog:
    """Tests for Sticky Saguaro catalog_existing phase."""

    def test_catalog_empty_dir(self, sticky_saguaro_collector):
        """Cataloging an empty directory returns empty DataFrame."""
        manifest = sticky_saguaro_collector.catalog_existing()
        assert len(manifest) == 0

    def test_catalog_with_valid_pdf(
            self, sticky_saguaro_collector, fake_pdf_data):
        """Valid PDFs are cataloged with hashes."""
        c = sticky_saguaro_collector
        pdf_path = c.pdf_dir / 'abc123.pdf'
        with open(str(pdf_path), 'wb') as f:
            f.write(fake_pdf_data)

        manifest = c.catalog_existing()
        assert len(manifest) == 1
        assert manifest.iloc[0]['file_name'] == 'abc123.pdf'
        assert manifest.iloc[0]['file_hash'] != ''

    def test_catalog_with_invalid_pdf(
            self, sticky_saguaro_collector, fake_invalid_pdf_data):
        """Invalid PDFs are skipped."""
        c = sticky_saguaro_collector
        pdf_path = c.pdf_dir / 'invalid.pdf'
        with open(str(pdf_path), 'wb') as f:
            f.write(fake_invalid_pdf_data)

        manifest = c.catalog_existing()
        assert len(manifest) == 0

    def test_catalog_incremental(
            self, sticky_saguaro_collector, fake_pdf_data):
        """Incremental catalog doesn't re-catalog existing files."""
        c = sticky_saguaro_collector
        pdf_path = c.pdf_dir / 'test1.pdf'
        with open(str(pdf_path), 'wb') as f:
            f.write(fake_pdf_data)

        m1 = c.catalog_existing()
        assert len(m1) == 1

        # Add another PDF.
        pdf_path2 = c.pdf_dir / 'test2.pdf'
        with open(str(pdf_path2), 'wb') as f:
            f.write(fake_pdf_data + b'\x01')

        m2 = c.catalog_existing(incremental=True)
        assert len(m2) == 2

    def test_catalog_deduplication(
            self, sticky_saguaro_collector, fake_pdf_data):
        """Duplicate content is detected and removed."""
        c = sticky_saguaro_collector
        # Same content, different names.
        for name in ['dup1.pdf', 'dup2.pdf']:
            with open(str(c.pdf_dir / name), 'wb') as f:
                f.write(fake_pdf_data)

        manifest = c.catalog_existing()
        assert len(manifest) == 1  # One deduplicated.

    def test_catalog_saves_to_disk(
            self, sticky_saguaro_collector, fake_pdf_data):
        """Manifest is saved as CSV."""
        c = sticky_saguaro_collector
        with open(str(c.pdf_dir / 'test.pdf'), 'wb') as f:
            f.write(fake_pdf_data)

        c.catalog_existing()
        assert c.manifest_path.exists()


class TestStickySaguaroConvert:
    """Tests for Sticky Saguaro LabResult conversion."""

    def test_convert_empty(self, sticky_saguaro_collector):
        """Empty manifest produces empty results."""
        results = sticky_saguaro_collector._convert_to_lab_results(
            pd.DataFrame()
        )
        assert results == []

    def test_convert_single_row(self, sticky_saguaro_collector):
        """Single manifest row produces a valid LabResult."""
        manifest = pd.DataFrame([{
            'url_hash': 'abc123',
            'coa_url': 'https://example.com/coa.pdf',
            'pdf_name': 'Blue Dream COA',
            'date': '2024-01-15',
            'file_name': 'abc123.pdf',
            'date_cataloged': '2024-01-20',
            'file_hash': 'sha256hash',
        }])
        results = sticky_saguaro_collector._convert_to_lab_results(
            manifest
        )
        assert len(results) == 1
        r = results[0]
        assert r['result_id'] != ''
        assert r['product_name'] == 'Blue Dream COA'
        assert r['source'] == 'sticky_saguaro'
        assert r['state'] == 'az'
        assert r['producer'] == 'Sticky Saguaro'

    def test_convert_preserves_url(self, sticky_saguaro_collector):
        """COA URL is preserved in the result."""
        manifest = pd.DataFrame([{
            'url_hash': 'x', 'coa_url': 'https://test.com/coa.pdf',
            'pdf_name': '', 'date': '', 'file_name': '',
            'date_cataloged': '', 'file_hash': '',
        }])
        results = sticky_saguaro_collector._convert_to_lab_results(
            manifest
        )
        assert results[0]['lab_results_url'] == \
            'https://test.com/coa.pdf'


class TestStickySaguaroStats:
    """Tests for Sticky Saguaro archive stats."""

    def test_stats_empty(self, sticky_saguaro_collector):
        stats = sticky_saguaro_collector.archive_stats()
        assert stats['total_pdfs'] == 0
        assert stats['total_size_bytes'] == 0

    def test_stats_with_pdfs(
            self, sticky_saguaro_collector, fake_pdf_data):
        c = sticky_saguaro_collector
        with open(str(c.pdf_dir / 'test.pdf'), 'wb') as f:
            f.write(fake_pdf_data)
        stats = c.archive_stats()
        assert stats['total_pdfs'] == 1
        assert stats['total_size_bytes'] > 0


class TestStickySaguaroGetResults:
    """Tests for Sticky Saguaro full pipeline."""

    def test_catalog_only_mode(
            self, sticky_saguaro_collector, fake_pdf_data):
        """Catalog-only mode skips network operations."""
        c = sticky_saguaro_collector
        with open(str(c.pdf_dir / 'test.pdf'), 'wb') as f:
            f.write(fake_pdf_data)

        results = c.get_results(catalog_only=True)
        assert len(results) == 1
        assert 'result_id' in results.columns

    @pytest.mark.integration
    @pytest.mark.selenium
    @pytest.mark.skipif(
        not importlib.util.find_spec('selenium'),
        reason='selenium not installed'
    )
    def test_full_pipeline(self, sticky_saguaro_collector):
        """Full pipeline discovers and downloads COAs."""
        results = sticky_saguaro_collector.get_results(
            headless=True
        )
        assert len(results) > 0


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Flow Distribution Tests                                          ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestFlowDistributionConstants:
    """Tests for Flow Distribution module constants."""

    def test_base_url(self):
        assert FLOW_BASE_URL == 'https://flowdistribution.com/'

    def test_producer_metadata(self):
        assert FLOW_PRODUCER['producer'] == 'Flow Distribution'
        assert FLOW_PRODUCER['producer_state'] == 'az'

    def test_default_birth_date(self):
        assert DEFAULT_BIRTH_DATE == '04/12/1992'


class TestFlowDistributionSearchQueries:
    """Tests for search query generation."""

    def test_3_digit_queries(self):
        queries = _generate_search_queries(3)
        assert len(queries) == 1000
        assert all(len(q) == 3 for q in queries)

    def test_2_digit_queries(self):
        queries = _generate_search_queries(2)
        assert len(queries) == 100
        assert '00' in queries
        assert '99' in queries

    def test_queries_are_shuffled(self):
        q1 = _generate_search_queries(2)
        q2 = _generate_search_queries(2)
        # Extremely unlikely both have the same order.
        assert q1 != q2 or len(q1) <= 1

    def test_all_values_present(self):
        queries = _generate_search_queries(2)
        assert set(queries) == {str(i).zfill(2) for i in range(100)}


class TestFlowDistributionProgressTracker:
    """Tests for Flow Distribution progress tracker."""

    def test_init_empty(self, temp_dir):
        path = str(temp_dir / 'progress.json')
        tracker = FlowProgressTracker(path)
        assert tracker.state['total_queries'] == 0
        assert len(tracker.searched) == 0
        assert len(tracker.found) == 0

    def test_record_search_without_results(self, temp_dir):
        tracker = FlowProgressTracker(str(temp_dir / 'p.json'))
        tracker.record_search('001')
        assert '001' in tracker.searched
        assert tracker.state['total_queries'] == 1

    def test_record_search_with_results(self, temp_dir):
        tracker = FlowProgressTracker(str(temp_dir / 'p.json'))
        tracker.record_search('002', [
            {'url_hash': 'abc', 'coa_url': 'https://example.com/1'},
        ])
        assert '002' in tracker.searched
        assert 'abc' in tracker.found
        assert tracker.state['total_found'] == 1

    def test_save_and_reload(self, temp_dir):
        path = str(temp_dir / 'p.json')
        t1 = FlowProgressTracker(path)
        t1.record_search('001', [{'url_hash': 'abc', 'coa_url': 'u'}])
        t1.save()

        t2 = FlowProgressTracker(path)
        assert '001' in t2.searched
        assert 'abc' in t2.found

    def test_no_duplicate_queries(self, temp_dir):
        tracker = FlowProgressTracker(str(temp_dir / 'p.json'))
        tracker.record_search('001')
        tracker.record_search('001')
        assert tracker.state['total_queries'] == 1


class TestFlowDistributionCollectorInit:
    """Tests for Flow Distribution collector initialization."""

    def test_init_creates_dirs(self, flow_distribution_collector):
        c = flow_distribution_collector
        assert c.pdf_dir.exists()
        assert c.datasets_dir.exists()

    def test_init_parses_birth_date(self, flow_distribution_collector):
        c = flow_distribution_collector
        assert c.birth_month == '04'
        assert c.birth_day == '12'
        assert c.birth_year == '1992'

    def test_init_custom_birth_date(self, temp_data_dir, temp_pdf_dir):
        c = FlowDistributionCollector(
            pdf_dir=str(temp_pdf_dir / 'fd'),
            data_dir=str(temp_data_dir),
            birth_date='01/15/1990',
            verbose=False,
        )
        assert c.birth_month == '01'
        assert c.birth_day == '15'
        assert c.birth_year == '1990'


class TestFlowDistributionCatalog:
    """Tests for Flow Distribution catalog_existing."""

    def test_catalog_empty(self, flow_distribution_collector):
        manifest = flow_distribution_collector.catalog_existing()
        assert len(manifest) == 0

    def test_catalog_with_pdf(
            self, flow_distribution_collector, fake_pdf_data):
        c = flow_distribution_collector
        with open(str(c.pdf_dir / 'test.pdf'), 'wb') as f:
            f.write(fake_pdf_data)
        manifest = c.catalog_existing()
        assert len(manifest) == 1
        assert manifest.iloc[0]['source'] == 'flow_distribution'


class TestFlowDistributionConvert:
    """Tests for Flow Distribution LabResult conversion."""

    def test_convert_empty(self, flow_distribution_collector):
        results = flow_distribution_collector._convert_to_lab_results(
            pd.DataFrame()
        )
        assert results == []

    def test_convert_single(self, flow_distribution_collector):
        manifest = pd.DataFrame([{
            'url_hash': 'x', 'coa_url': 'https://test.com/coa.pdf',
            'retail_name': 'OG Kush', 'date': '2024-01-15',
            'file_name': 'x.pdf', 'date_cataloged': '',
            'file_hash': '',
        }])
        results = flow_distribution_collector._convert_to_lab_results(
            manifest
        )
        assert len(results) == 1
        assert results[0]['product_name'] == 'OG Kush'
        assert results[0]['source'] == 'flow_distribution'
        assert results[0]['producer'] == 'Flow Distribution'


class TestFlowDistributionGetResults:
    """Tests for Flow Distribution full pipeline."""

    def test_catalog_only_mode(
            self, flow_distribution_collector, fake_pdf_data):
        c = flow_distribution_collector
        with open(str(c.pdf_dir / 'test.pdf'), 'wb') as f:
            f.write(fake_pdf_data)
        results = c.get_results(catalog_only=True)
        assert len(results) == 1


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Curaleaf Tests                                                   ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestCuraleafConstants:
    """Tests for Curaleaf module constants."""

    def test_base_url(self):
        assert CURALEAF_BASE_URL == \
            'https://coas.curaleaf.com/transparency/'

    def test_producer_metadata(self):
        assert CURALEAF_PRODUCER['producer'] == 'Curaleaf'


class TestCuraleafSearchQueries:
    """Tests for Curaleaf search query generation."""

    def test_3_digit_only(self):
        queries = curaleaf_generate_queries(3, include_four_digit=False)
        assert len(queries) == 1000

    def test_3_plus_4_digit(self):
        queries = curaleaf_generate_queries(3, include_four_digit=True)
        assert len(queries) == 11000  # 1000 + 10000

    def test_2_digit(self):
        queries = curaleaf_generate_queries(2, include_four_digit=False)
        assert len(queries) == 100


class TestCuraleafProgressTracker:
    """Tests for Curaleaf progress tracker."""

    def test_init_empty(self, temp_dir):
        tracker = CuraleafProgressTracker(str(temp_dir / 'p.json'))
        assert tracker.state['total_queries'] == 0

    def test_save_reload(self, temp_dir):
        path = str(temp_dir / 'p.json')
        t = CuraleafProgressTracker(path)
        t.record_search('x', [{'url_hash': 'h', 'coa_url': 'u'}])
        t.save()
        t2 = CuraleafProgressTracker(path)
        assert 'x' in t2.searched
        assert 'h' in t2.found


class TestCuraleafCollectorInit:
    """Tests for Curaleaf collector initialization."""

    def test_init_creates_dirs(self, curaleaf_collector):
        assert curaleaf_collector.pdf_dir.exists()
        assert curaleaf_collector.datasets_dir.exists()

    def test_init_manifest_path(self, curaleaf_collector):
        assert 'curaleaf-manifest.csv' in str(
            curaleaf_collector.manifest_path
        )


class TestCuraleafCatalog:
    """Tests for Curaleaf catalog_existing."""

    def test_catalog_empty(self, curaleaf_collector):
        manifest = curaleaf_collector.catalog_existing()
        assert len(manifest) == 0

    def test_catalog_with_pdf(
            self, curaleaf_collector, fake_pdf_data):
        c = curaleaf_collector
        with open(str(c.pdf_dir / 'test.pdf'), 'wb') as f:
            f.write(fake_pdf_data)
        manifest = c.catalog_existing()
        assert len(manifest) == 1
        assert manifest.iloc[0]['source'] == 'curaleaf'


class TestCuraleafConvert:
    """Tests for Curaleaf LabResult conversion."""

    def test_convert_empty(self, curaleaf_collector):
        results = curaleaf_collector._convert_to_lab_results(
            pd.DataFrame()
        )
        assert results == []

    def test_convert_produces_multi_state_marker(
            self, curaleaf_collector):
        """Curaleaf results have empty state (set downstream)."""
        manifest = pd.DataFrame([{
            'url_hash': 'x', 'coa_url': 'https://test.com/coa.pdf',
            'batch_number': 'B001', 'file_name': 'x.pdf',
            'date_cataloged': '', 'file_hash': '', 'query': '',
        }])
        results = curaleaf_collector._convert_to_lab_results(manifest)
        assert len(results) == 1
        assert results[0]['state'] == ''
        assert results[0]['source'] == 'curaleaf_transparency'
        assert results[0]['batch_number'] == 'B001'


class TestCuraleafStats:
    """Tests for Curaleaf archive stats."""

    def test_stats_empty(self, curaleaf_collector):
        stats = curaleaf_collector.archive_stats()
        assert stats['total_pdfs'] == 0

    def test_stats_with_pdfs(
            self, curaleaf_collector, fake_pdf_data):
        c = curaleaf_collector
        with open(str(c.pdf_dir / 'test.pdf'), 'wb') as f:
            f.write(fake_pdf_data)
        stats = c.archive_stats()
        assert stats['total_pdfs'] == 1


class TestCuraleafGetResults:
    """Tests for Curaleaf full pipeline."""

    def test_catalog_only(self, curaleaf_collector, fake_pdf_data):
        c = curaleaf_collector
        with open(str(c.pdf_dir / 'test.pdf'), 'wb') as f:
            f.write(fake_pdf_data)
        results = c.get_results(catalog_only=True)
        assert len(results) == 1
        assert 'result_id' in results.columns


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Cross-Module Consistency Tests                                   ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestCrossModuleConsistency:
    """Verify all modules follow the same patterns."""

    def test_all_collectors_have_context_manager(
            self,
            sticky_saguaro_collector,
            flow_distribution_collector,
            curaleaf_collector):
        """All collectors support context manager protocol."""
        for c in [
            sticky_saguaro_collector,
            flow_distribution_collector,
            curaleaf_collector,
        ]:
            assert hasattr(c, '__enter__')
            assert hasattr(c, '__exit__')

    def test_all_collectors_have_get_results(
            self,
            sticky_saguaro_collector,
            flow_distribution_collector,
            curaleaf_collector):
        """All collectors have get_results method."""
        for c in [
            sticky_saguaro_collector,
            flow_distribution_collector,
            curaleaf_collector,
        ]:
            assert hasattr(c, 'get_results')
            assert callable(c.get_results)

    def test_all_collectors_have_catalog_existing(
            self,
            sticky_saguaro_collector,
            flow_distribution_collector,
            curaleaf_collector):
        """All collectors have catalog_existing method."""
        for c in [
            sticky_saguaro_collector,
            flow_distribution_collector,
            curaleaf_collector,
        ]:
            assert hasattr(c, 'catalog_existing')
            assert callable(c.catalog_existing)

    def test_all_collectors_have_archive_stats(
            self,
            sticky_saguaro_collector,
            flow_distribution_collector,
            curaleaf_collector):
        """All collectors have archive_stats method."""
        for c in [
            sticky_saguaro_collector,
            flow_distribution_collector,
            curaleaf_collector,
        ]:
            assert hasattr(c, 'archive_stats')

    def test_result_id_consistency_across_modules(self):
        """All modules produce consistent result ID format."""
        for fn in [
            _generate_result_id,
            flow_generate_result_id,
            curaleaf_generate_result_id,
        ]:
            rid = fn('test')
            assert len(rid) == 16
            assert all(c in '0123456789abcdef' for c in rid)

    def test_hash_url_consistency_across_modules(self):
        """All modules produce consistent URL hashes."""
        url = 'https://example.com/test.pdf'
        hashes = [
            _hash_url(url),
            flow_hash_url(url),
            curaleaf_hash_url(url),
        ]
        # All should produce the same hash for the same URL.
        assert len(set(hashes)) == 1

    def test_all_collectors_have_required_dirs(
            self,
            sticky_saguaro_collector,
            flow_distribution_collector,
            curaleaf_collector):
        """All collectors create required directories."""
        for c in [
            sticky_saguaro_collector,
            flow_distribution_collector,
            curaleaf_collector,
        ]:
            assert c.pdf_dir.exists()
            assert c.datasets_dir.exists()
