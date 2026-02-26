"""
Test Suite | Florida | The Flowery
Copyright (c) 2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2/22/2026
Updated: 2/22/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Comprehensive test suite for the Flowery COA collector.

Usage:
    pytest test_fl_flowery.py -v
    pytest test_fl_flowery.py -v -m integration
"""
# Standard imports:
import hashlib
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

# External imports:
import pandas as pd
import pytest
import requests

# Module under test:
from get_results_fl_flowery import (
    BASE_URL,
    DROPS_URL,
    FLOWERY_PRODUCER,
    DEFAULT_HEADERS,
    MIN_PDF_SIZE,
    DEFAULT_PAUSE,
    DROP_NUMBER_RE,
    DROP_DATE_RE,
    _generate_result_id,
    _extract_attachment_id,
    _extract_sample_id_from_filename,
    _parse_drop_title,
    _is_valid_pdf,
    _sanitize_filename,
    FloweryCollector,
)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Fixtures                                                         ║
# ╚══════════════════════════════════════════════════════════════════╝

@pytest.fixture
def tmp_dirs(tmp_path):
    pdf_dir = tmp_path / 'pdfs'
    data_dir = tmp_path / 'data'
    pdf_dir.mkdir()
    data_dir.mkdir()
    (data_dir / 'datasets').mkdir()
    return pdf_dir, data_dir


@pytest.fixture
def collector(tmp_dirs):
    pdf_dir, data_dir = tmp_dirs
    return FloweryCollector(
        pdf_dir=str(pdf_dir),
        data_dir=str(data_dir),
        pause=0.01,
        verbose=False,
    )


@pytest.fixture
def sample_pdf_bytes():
    return b'%PDF-1.4' + b'\x00' * 50000


@pytest.fixture
def small_pdf_bytes():
    return b'%PDF-1.4' + b'\x00' * 100


@pytest.fixture
def html_bytes():
    return b'<html><body>Error 403</body></html>'


@pytest.fixture
def populated_pdf_dir(tmp_dirs):
    pdf_dir, _ = tmp_dirs
    files = [
        '44281628412955.pdf',
        '44281628422811.pdf',
        'batch1-sample1.pdf',
        'batch2-sample2.pdf',
        'Product Name Here.pdf',
    ]
    for f in files:
        fp = pdf_dir / f
        fp.write_bytes(b'%PDF-1.4' + os.urandom(20000))
    return pdf_dir, files


@pytest.fixture
def drop_list_html():
    return '''
    <html><body>
    <ul class="article-list">
        <li class="article-list-item f-section visible">
            <a href="/hc/en-us/articles/44281615890715-Drop-65-12-15-25-Product-COAs"
               class="article-list-link">
                Drop #65 (12/15/25) Product COAs
            </a>
        </li>
        <li class="article-list-item f-section visible">
            <a href="/hc/en-us/articles/43241242430747-Drop-64-11-17-25-Product-COAs"
               class="article-list-link">
                Drop #64 (11/17/25) Product COAs
            </a>
        </li>
        <li class="article-list-item f-section visible">
            <a href="/hc/en-us/articles/99999999-Some-Non-COA-Article"
               class="article-list-link">
                General Information Page
            </a>
        </li>
    </ul>
    </body></html>
    '''


@pytest.fixture
def drop_page_html():
    return '''
    <html><body>
    <div class="article-attachments f-section visible">
        <ul class="attachments">
            <li class="attachment-item">
                <a href="/hc/en-us/article_attachments/44281628412955"
                   target="_blank">Lemon Heads #4 + Z Live Rosin Vape 1g.pdf</a>
                <div class="attachment-meta meta-group">
                    <span class="attachment-meta-item meta-data">900 KB</span>
                    <a href="/hc/en-us/article_attachments/44281628412955"
                       target="_blank" class="attachment-meta-item meta-data">Download</a>
                </div>
            </li>
            <li class="attachment-item">
                <a href="/hc/en-us/article_attachments/44281628422811"
                   target="_blank">Z Pie #9 Persy Flower 14g.pdf</a>
                <div class="attachment-meta meta-group">
                    <span class="attachment-meta-item meta-data">1000 KB</span>
                    <a href="/hc/en-us/article_attachments/44281628422811"
                       target="_blank" class="attachment-meta-item meta-data">Download</a>
                </div>
            </li>
            <li class="attachment-item">
                <a href="/hc/en-us/article_attachments/44281628434203"
                   target="_blank">GG4 + GG4 Persy Thumbprint 2.5g [Close Friends].pdf</a>
                <div class="attachment-meta meta-group">
                    <span class="attachment-meta-item meta-data">900 KB</span>
                    <a href="/hc/en-us/article_attachments/44281628434203"
                       target="_blank" class="attachment-meta-item meta-data">Download</a>
                </div>
            </li>
        </ul>
    </div>
    </body></html>
    '''


@pytest.fixture
def empty_drop_page_html():
    return '<html><body><div class="article-body"><p>Coming soon!</p></div></body></html>'


@pytest.fixture
def paginated_drop_list_html():
    return '''
    <html><body>
    <ul class="article-list">
        <li class="article-list-item">
            <a href="/hc/en-us/articles/111-Drop-1-01-01-24-Product-COAs"
               class="article-list-link">
                Drop #1 (01/01/24) Product COAs
            </a>
        </li>
    </ul>
    <a class="pagination-next-link" href="/hc/en-us/sections/123?page=2">Next</a>
    </body></html>
    '''


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Constants                                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestConstants:

    def test_base_url(self):
        assert BASE_URL == 'https://support.theflowery.co'

    def test_drops_url_starts_with_base(self):
        assert DROPS_URL.startswith(BASE_URL)

    def test_drops_url_contains_section_id(self):
        assert '7240468576283' in DROPS_URL

    def test_producer_name(self):
        assert FLOWERY_PRODUCER['producer'] == 'The Flowery'

    def test_producer_license(self):
        assert FLOWERY_PRODUCER['producer_license_number'] == 'MMTC-2019-0020'

    def test_producer_state(self):
        assert FLOWERY_PRODUCER['producer_state'] == 'fl'

    def test_producer_website(self):
        assert 'theflowery.co' in FLOWERY_PRODUCER['producer_website']

    def test_default_headers_user_agent(self):
        assert 'User-Agent' in DEFAULT_HEADERS
        assert 'cannlytics' in DEFAULT_HEADERS['User-Agent'].lower()

    def test_min_pdf_size(self):
        assert MIN_PDF_SIZE == 10 * 1024

    def test_default_pause(self):
        assert DEFAULT_PAUSE == 3.0

    def test_drop_number_regex(self):
        assert DROP_NUMBER_RE.search('Drop #65').group(1) == '65'

    def test_drop_date_regex(self):
        assert DROP_DATE_RE.search('(12/15/25)').group(1) == '12/15/25'


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: _generate_result_id                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestGenerateResultId:

    def test_length(self):
        assert len(_generate_result_id('test.pdf')) == 16

    def test_hex_chars(self):
        rid = _generate_result_id('test.pdf')
        assert all(c in '0123456789abcdef' for c in rid)

    def test_deterministic(self):
        assert _generate_result_id('a.pdf') == _generate_result_id('a.pdf')

    def test_unique(self):
        assert _generate_result_id('a.pdf') != _generate_result_id('b.pdf')

    def test_empty_string(self):
        assert len(_generate_result_id('')) == 16


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: _extract_attachment_id                                     ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestExtractAttachmentId:

    def test_relative_path(self):
        assert _extract_attachment_id('/hc/en-us/article_attachments/44281628412955') == '44281628412955'

    def test_full_url(self):
        assert _extract_attachment_id('https://support.theflowery.co/hc/en-us/article_attachments/12345') == '12345'

    def test_no_attachment(self):
        assert _extract_attachment_id('/hc/en-us/articles/99999') == ''

    def test_empty_string(self):
        assert _extract_attachment_id('') == ''

    def test_none(self):
        assert _extract_attachment_id(None) == ''

    def test_trailing_slash(self):
        assert _extract_attachment_id('/hc/en-us/article_attachments/12345/') == '12345'


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: _extract_sample_id_from_filename                           ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestExtractSampleIdFromFilename:

    def test_numeric(self):
        assert _extract_sample_id_from_filename('44281628412955.pdf') == '44281628412955'

    def test_batch_sample(self):
        assert _extract_sample_id_from_filename('batch1-sample1.pdf') == 'batch1-sample1'

    def test_product_name(self):
        assert _extract_sample_id_from_filename('Blue Dream Flower.pdf') == 'Blue Dream Flower'

    def test_uppercase_ext(self):
        assert _extract_sample_id_from_filename('TEST.PDF') == 'TEST'

    def test_empty(self):
        assert _extract_sample_id_from_filename('') == ''

    def test_no_extension(self):
        assert _extract_sample_id_from_filename('no_ext') == 'no_ext'

    def test_mixed_case_ext(self):
        assert _extract_sample_id_from_filename('data.Pdf') == 'data'


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: _parse_drop_title                                          ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestParseDropTitle:

    def test_standard(self):
        num, date = _parse_drop_title('Drop #65 (12/15/25) Product COAs')
        assert num == 65 and date == '12/15/25'

    def test_no_hash(self):
        num, date = _parse_drop_title('Drop 64 (11/17/25) Product COAs')
        assert num == 64 and date == '11/17/25'

    def test_four_digit_year(self):
        num, date = _parse_drop_title('Drop #10 (3/5/2024) COAs')
        assert num == 10 and date == '3/5/2024'

    def test_no_match(self):
        num, date = _parse_drop_title('Some Random Article')
        assert num is None and date is None

    def test_number_only(self):
        num, date = _parse_drop_title('Drop #42 Product COAs')
        assert num == 42 and date is None

    def test_date_only(self):
        num, date = _parse_drop_title('Release (6/1/24) COAs')
        assert num is None and date == '6/1/24'

    def test_empty(self):
        num, date = _parse_drop_title('')
        assert num is None and date is None


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: _is_valid_pdf                                              ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestIsValidPdf:

    def test_valid(self, sample_pdf_bytes):
        assert _is_valid_pdf(sample_pdf_bytes) is True

    def test_too_small(self, small_pdf_bytes):
        assert _is_valid_pdf(small_pdf_bytes) is False

    def test_html(self, html_bytes):
        assert _is_valid_pdf(html_bytes) is False

    def test_empty(self):
        assert _is_valid_pdf(b'') is False

    def test_custom_min_size(self):
        data = b'%PDF-1.4' + b'\x00' * 500
        assert _is_valid_pdf(data, min_size=100) is True
        assert _is_valid_pdf(data, min_size=1000) is False

    def test_pdf_15(self):
        assert _is_valid_pdf(b'%PDF-1.5' + b'\x00' * 50000) is True

    def test_pdf_20(self):
        assert _is_valid_pdf(b'%PDF-2.0' + b'\x00' * 50000) is True


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: _sanitize_filename                                         ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestSanitizeFilename:

    def test_removes_colons(self):
        assert ':' not in _sanitize_filename('Test: Name')

    def test_removes_slashes(self):
        r = _sanitize_filename('Path/Name\\Here')
        assert '/' not in r and '\\' not in r

    def test_removes_quotes(self):
        assert '"' not in _sanitize_filename('"quoted"')

    def test_max_length(self):
        assert len(_sanitize_filename('A' * 200, max_length=50)) <= 50

    def test_strips_dots_spaces(self):
        r = _sanitize_filename('  test. . ')
        assert not r.startswith(' ') and not r.endswith('.')

    def test_passthrough(self):
        assert _sanitize_filename('simple-name') == 'simple-name'

    def test_question_mark(self):
        assert '?' not in _sanitize_filename('What?')

    def test_asterisk(self):
        assert '*' not in _sanitize_filename('star*name')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Collector Initialization                                   ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestCollectorInit:

    def test_basic_init(self, tmp_dirs):
        pdf_dir, data_dir = tmp_dirs
        c = FloweryCollector(pdf_dir=str(pdf_dir), data_dir=str(data_dir))
        assert c.pdf_dir == pdf_dir
        assert c.data_dir == data_dir

    def test_datasets_dir_created(self, collector):
        assert collector.datasets_dir.exists()

    def test_pdf_dir_created(self, collector):
        assert collector.pdf_dir.exists()

    def test_manifest_path(self, collector):
        assert 'flowery-manifest.csv' in str(collector.manifest_path)

    def test_session_headers(self, collector):
        assert 'User-Agent' in collector.session.headers

    def test_driver_starts_none(self, collector):
        assert collector.driver is None

    def test_context_manager(self, tmp_dirs):
        pdf_dir, data_dir = tmp_dirs
        with FloweryCollector(pdf_dir=str(pdf_dir), data_dir=str(data_dir)) as c:
            assert c is not None

    def test_custom_pause(self, tmp_dirs):
        pdf_dir, data_dir = tmp_dirs
        c = FloweryCollector(pdf_dir=str(pdf_dir), data_dir=str(data_dir), pause=10.0)
        assert c.pause == 10.0

    def test_creates_nonexistent_dirs(self, tmp_path):
        c = FloweryCollector(
            pdf_dir=str(tmp_path / 'deep' / 'nested' / 'pdfs'),
            data_dir=str(tmp_path / 'deep' / 'data'),
        )
        assert c.pdf_dir.exists()
        assert c.datasets_dir.exists()


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Catalog Existing                                           ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestCatalogExisting:

    def test_empty_directory(self, collector):
        manifest = collector.catalog_existing()
        assert len(manifest) == 0

    def test_populated_directory(self, collector, populated_pdf_dir):
        pdf_dir, files = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        manifest = collector.catalog_existing()
        assert len(manifest) == len(files)

    def test_manifest_columns(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        manifest = collector.catalog_existing()
        for col in ['file_name', 'sample_id', 'file_path', 'file_size',
                     'file_hash', 'date_cataloged', 'source']:
            assert col in manifest.columns

    def test_file_sizes_positive(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        manifest = collector.catalog_existing()
        assert (manifest['file_size'] > 0).all()

    def test_hashes_computed(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        manifest = collector.catalog_existing(compute_hashes=True)
        assert (manifest['file_hash'] != '').all()
        assert all(len(h) == 64 for h in manifest['file_hash'])

    def test_hashes_skipped(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        manifest = collector.catalog_existing(compute_hashes=False)
        assert (manifest['file_hash'] == '').all()

    def test_manifest_saved(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        collector.catalog_existing()
        assert collector.manifest_path.exists()

    def test_incremental_stable(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        m1 = collector.catalog_existing()
        m2 = collector.catalog_existing(incremental=True)
        assert len(m2) == len(m1)

    def test_rebuild_full(self, collector, populated_pdf_dir):
        pdf_dir, files = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        collector.catalog_existing()
        m = collector.catalog_existing(incremental=False)
        assert len(m) == len(files)

    def test_content_dedup(self, collector, tmp_dirs):
        pdf_dir, _ = tmp_dirs
        collector.pdf_dir = pdf_dir
        content = b'%PDF-1.4' + b'\x00' * 20000
        (pdf_dir / 'copy_a.pdf').write_bytes(content)
        (pdf_dir / 'copy_b.pdf').write_bytes(content)
        manifest = collector.catalog_existing()
        assert len(manifest) == 1

    def test_numeric_attachment_id(self, collector, tmp_dirs):
        pdf_dir, _ = tmp_dirs
        collector.pdf_dir = pdf_dir
        (pdf_dir / '44281628412955.pdf').write_bytes(b'%PDF-1.4' + os.urandom(20000))
        manifest = collector.catalog_existing()
        assert manifest.iloc[0]['attachment_id'] == '44281628412955'

    def test_non_numeric_no_attachment_id(self, collector, tmp_dirs):
        pdf_dir, _ = tmp_dirs
        collector.pdf_dir = pdf_dir
        (pdf_dir / 'product-name.pdf').write_bytes(b'%PDF-1.4' + os.urandom(20000))
        manifest = collector.catalog_existing()
        assert manifest.iloc[0]['attachment_id'] == ''

    def test_source_is_zendesk(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        manifest = collector.catalog_existing()
        assert (manifest['source'] == 'zendesk_support').all()

    def test_incremental_adds_new_file(self, collector, tmp_dirs):
        pdf_dir, _ = tmp_dirs
        collector.pdf_dir = pdf_dir
        (pdf_dir / 'first.pdf').write_bytes(b'%PDF-1.4' + os.urandom(20000))
        m1 = collector.catalog_existing()
        assert len(m1) == 1
        (pdf_dir / 'second.pdf').write_bytes(b'%PDF-1.4' + os.urandom(20000))
        m2 = collector.catalog_existing(incremental=True)
        assert len(m2) == 2


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Scrape Drop List                                           ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestExtractDropsFromHtml:
    """Test the multi-strategy HTML extraction method."""

    def test_standard_selector(self, collector, drop_list_html):
        drops = collector._extract_drops_from_html(drop_list_html)
        assert len(drops) == 2

    def test_extracts_numbers(self, collector, drop_list_html):
        drops = collector._extract_drops_from_html(drop_list_html)
        assert {d['drop_number'] for d in drops} == {64, 65}

    def test_extracts_dates(self, collector, drop_list_html):
        drops = collector._extract_drops_from_html(drop_list_html)
        assert {d['drop_date'] for d in drops} == {'11/17/25', '12/15/25'}

    def test_excludes_non_coa(self, collector, drop_list_html):
        drops = collector._extract_drops_from_html(drop_list_html)
        assert not any('General' in d['title'] for d in drops)

    def test_absolute_urls(self, collector, drop_list_html):
        for d in collector._extract_drops_from_html(drop_list_html):
            assert d['url'].startswith('https://')

    def test_empty_html(self, collector):
        assert len(collector._extract_drops_from_html('<html></html>')) == 0

    def test_fallback_no_class(self, collector):
        """Fallback should find links without article-list-link class."""
        html = '''
        <html><body>
        <a href="/hc/en-us/articles/999-Drop-99-01-01-26-Product-COAs">
            Drop #99 (01/01/26) Product COAs
        </a>
        </body></html>
        '''
        drops = collector._extract_drops_from_html(html)
        assert len(drops) == 1
        assert drops[0]['drop_number'] == 99

    def test_deduplicates_urls(self, collector):
        html = '''
        <html><body>
        <ul class="article-list">
            <li><a class="article-list-link" href="/hc/en-us/articles/1-Drop-1-COAs">Drop #1 COAs</a></li>
            <li><a class="article-list-link" href="/hc/en-us/articles/1-Drop-1-COAs">Drop #1 COAs</a></li>
        </ul>
        </body></html>
        '''
        drops = collector._extract_drops_from_html(html)
        assert len(drops) == 1


class TestScrapeDropList:

    def test_finds_coa_articles(self, collector, drop_list_html):
        with patch.object(collector, '_get_page_source', return_value=drop_list_html):
            drops = collector.scrape_drop_list()
            assert len(drops) == 2

    def test_extracts_drop_numbers(self, collector, drop_list_html):
        with patch.object(collector, '_get_page_source', return_value=drop_list_html):
            drops = collector.scrape_drop_list()
            assert {d['drop_number'] for d in drops} == {64, 65}

    def test_extracts_drop_dates(self, collector, drop_list_html):
        with patch.object(collector, '_get_page_source', return_value=drop_list_html):
            drops = collector.scrape_drop_list()
            assert {d['drop_date'] for d in drops} == {'11/17/25', '12/15/25'}

    def test_absolute_urls(self, collector, drop_list_html):
        with patch.object(collector, '_get_page_source', return_value=drop_list_html):
            for d in collector.scrape_drop_list():
                assert d['url'].startswith('https://')

    def test_excludes_non_coa(self, collector, drop_list_html):
        with patch.object(collector, '_get_page_source', return_value=drop_list_html):
            drops = collector.scrape_drop_list()
            assert not any('General' in d['title'] for d in drops)

    def test_empty_page(self, collector):
        with patch.object(collector, '_get_page_source', return_value='<html></html>'):
            collector.driver = MagicMock()  # For scroll fallback.
            collector.driver.page_source = '<html></html>'
            assert len(collector.scrape_drop_list()) == 0

    def test_pagination(self, collector, paginated_drop_list_html):
        page2_html = '''
        <html><body>
        <ul class="article-list">
            <li class="article-list-item">
                <a href="/hc/en-us/articles/222-Drop-2-02-01-24-Product-COAs"
                   class="article-list-link">Drop #2 (02/01/24) Product COAs</a>
            </li>
        </ul>
        </body></html>
        '''
        call_count = [0]
        def mock_get_page(url, **kwargs):
            call_count[0] += 1
            return paginated_drop_list_html if call_count[0] <= 1 else page2_html

        with patch.object(collector, '_get_page_source', side_effect=mock_get_page):
            drops = collector.scrape_drop_list()
            assert len(drops) == 2
            assert {d['drop_number'] for d in drops} == {1, 2}

    def test_has_title_field(self, collector, drop_list_html):
        with patch.object(collector, '_get_page_source', return_value=drop_list_html):
            for d in collector.scrape_drop_list():
                assert 'title' in d and len(d['title']) > 0


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Scrape Drop Page                                           ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestScrapeDropPage:

    def _make_drop(self, num=65, date='12/15/25'):
        return {
            'url': 'https://example.com/article',
            'title': f'Drop #{num}',
            'drop_number': num,
            'drop_date': date,
        }

    def test_finds_attachments(self, collector, drop_page_html):
        with patch.object(collector, '_get_page_source', return_value=drop_page_html):
            assert len(collector.scrape_drop_page(self._make_drop())) == 3

    def test_extracts_ids(self, collector, drop_page_html):
        with patch.object(collector, '_get_page_source', return_value=drop_page_html):
            ids = {a['attachment_id'] for a in collector.scrape_drop_page(self._make_drop())}
            assert '44281628412955' in ids
            assert '44281628422811' in ids
            assert '44281628434203' in ids

    def test_extracts_product_names(self, collector, drop_page_html):
        with patch.object(collector, '_get_page_source', return_value=drop_page_html):
            names = [a['product_name'] for a in collector.scrape_drop_page(self._make_drop())]
            assert 'Lemon Heads #4 + Z Live Rosin Vape 1g' in names
            assert 'Z Pie #9 Persy Flower 14g' in names

    def test_strips_pdf_ext(self, collector, drop_page_html):
        with patch.object(collector, '_get_page_source', return_value=drop_page_html):
            for a in collector.scrape_drop_page(self._make_drop()):
                assert not a['product_name'].endswith('.pdf')

    def test_absolute_urls(self, collector, drop_page_html):
        with patch.object(collector, '_get_page_source', return_value=drop_page_html):
            for a in collector.scrape_drop_page(self._make_drop()):
                assert a['attachment_url'].startswith('https://')

    def test_inherits_drop_metadata(self, collector, drop_page_html):
        with patch.object(collector, '_get_page_source', return_value=drop_page_html):
            for a in collector.scrape_drop_page(self._make_drop(65, '12/15/25')):
                assert a['drop_number'] == 65
                assert a['drop_date'] == '12/15/25'

    def test_empty_page(self, collector, empty_drop_page_html):
        with patch.object(collector, '_get_page_source', return_value=empty_drop_page_html):
            assert len(collector.scrape_drop_page(self._make_drop())) == 0

    def test_has_drop_title(self, collector, drop_page_html):
        with patch.object(collector, '_get_page_source', return_value=drop_page_html):
            for a in collector.scrape_drop_page(self._make_drop()):
                assert 'drop_title' in a

    def test_fallback_without_li_wrapper(self, collector):
        """Attachments without li.attachment-item should still be found."""
        html = '''
        <html><body>
        <a href="/hc/en-us/article_attachments/99999" target="_blank">
            Test Product 3.5g.pdf
        </a>
        </body></html>
        '''
        with patch.object(collector, '_get_page_source', return_value=html):
            attachments = collector.scrape_drop_page(self._make_drop())
            assert len(attachments) == 1
            assert attachments[0]['attachment_id'] == '99999'
            assert attachments[0]['product_name'] == 'Test Product 3.5g'


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Download Logic                                             ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestDownloadCoa:

    def test_successful(self, collector, sample_pdf_bytes):
        resp = MagicMock(status_code=200, content=sample_pdf_bytes)
        collector.session.get = MagicMock(return_value=resp)
        result = collector._download_coa('http://a', '123')
        assert result is not None and Path(result).exists()

    def test_rejects_html(self, collector, html_bytes):
        resp = MagicMock(status_code=200, content=html_bytes)
        collector.session.get = MagicMock(return_value=resp)
        assert collector._download_coa('http://a', '123') is None

    def test_rejects_small(self, collector, small_pdf_bytes):
        resp = MagicMock(status_code=200, content=small_pdf_bytes)
        collector.session.get = MagicMock(return_value=resp)
        assert collector._download_coa('http://a', '123') is None

    def test_handles_404(self, collector):
        resp = MagicMock(status_code=404)
        collector.session.get = MagicMock(return_value=resp)
        assert collector._download_coa('http://a', '123', max_retries=0) is None

    def test_handles_network_error(self, collector):
        collector.session.get = MagicMock(side_effect=requests.RequestException('fail'))
        assert collector._download_coa('http://a', '123', max_retries=0) is None

    def test_filename_is_attachment_id(self, collector, sample_pdf_bytes):
        resp = MagicMock(status_code=200, content=sample_pdf_bytes)
        collector.session.get = MagicMock(return_value=resp)
        result = collector._download_coa('http://a', '99999')
        assert result.endswith('99999.pdf')

    def test_retries_on_failure(self, collector, sample_pdf_bytes):
        fail_resp = MagicMock(status_code=500)
        ok_resp = MagicMock(status_code=200, content=sample_pdf_bytes)
        collector.session.get = MagicMock(side_effect=[fail_resp, ok_resp])
        result = collector._download_coa('http://a', '123', max_retries=1)
        assert result is not None


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Download New COAs (batch)                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestDownloadNewCoas:

    def test_skips_existing(self, collector):
        df = pd.DataFrame([
            {'attachment_id': '111', 'attachment_url': 'http://a'},
            {'attachment_id': '222', 'attachment_url': 'http://b'},
        ])
        stats = collector.download_new_coas(df, existing_ids={'111', '222'})
        assert stats['already_exists'] == 2 and stats['downloaded'] == 0

    def test_skips_empty_id(self, collector):
        df = pd.DataFrame([{'attachment_id': '', 'attachment_url': ''}])
        stats = collector.download_new_coas(df, existing_ids=set())
        assert stats['skipped_no_id'] == 1

    def test_downloads_new(self, collector, sample_pdf_bytes):
        resp = MagicMock(status_code=200, content=sample_pdf_bytes)
        collector.session.get = MagicMock(return_value=resp)
        df = pd.DataFrame([
            {'attachment_id': '99999', 'attachment_url': 'http://a', 'product_name': 'Test'},
        ])
        stats = collector.download_new_coas(df, existing_ids=set())
        assert stats['downloaded'] == 1

    def test_empty_dataframe(self, collector):
        stats = collector.download_new_coas(pd.DataFrame())
        assert stats['downloaded'] == 0

    def test_mixed_new_and_existing(self, collector, sample_pdf_bytes):
        resp = MagicMock(status_code=200, content=sample_pdf_bytes)
        collector.session.get = MagicMock(return_value=resp)
        df = pd.DataFrame([
            {'attachment_id': '111', 'attachment_url': 'http://a', 'product_name': 'Old'},
            {'attachment_id': '999', 'attachment_url': 'http://b', 'product_name': 'New'},
        ])
        stats = collector.download_new_coas(df, existing_ids={'111'})
        assert stats['already_exists'] == 1
        assert stats['downloaded'] == 1


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: LabResult Conversion                                       ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestConvertToLabResults:

    def test_produces_results(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        manifest = collector.catalog_existing(compute_hashes=False)
        results = collector._convert_to_lab_results(manifest)
        assert len(results) == len(manifest)

    def test_has_id(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        manifest = collector.catalog_existing(compute_hashes=False)
        for r in collector._convert_to_lab_results(manifest):
            assert 'id' in r and len(r['id']) == 16

    def test_state_fl(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        manifest = collector.catalog_existing(compute_hashes=False)
        for r in collector._convert_to_lab_results(manifest):
            assert r['state'] == 'fl'

    def test_source_flowery(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        manifest = collector.catalog_existing(compute_hashes=False)
        for r in collector._convert_to_lab_results(manifest):
            assert r['source'] == 'flowery'

    def test_producer_metadata(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        manifest = collector.catalog_existing(compute_hashes=False)
        for r in collector._convert_to_lab_results(manifest):
            assert r.get('producer') == 'The Flowery'
            assert r.get('producer_license_number') == 'MMTC-2019-0020'

    def test_coa_url_for_numeric(self, collector, tmp_dirs):
        pdf_dir, _ = tmp_dirs
        collector.pdf_dir = pdf_dir
        (pdf_dir / '12345.pdf').write_bytes(b'%PDF-1.4' + os.urandom(20000))
        manifest = collector.catalog_existing()
        results = collector._convert_to_lab_results(manifest)
        assert 'article_attachments/12345' in results[0].get('coa_url', '')

    def test_unique_ids(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        manifest = collector.catalog_existing(compute_hashes=False)
        ids = [r['id'] for r in collector._convert_to_lab_results(manifest)]
        assert len(ids) == len(set(ids))

    def test_empty_manifest(self, collector):
        results = collector._convert_to_lab_results(pd.DataFrame())
        assert len(results) == 0

    def test_nan_attachment_id_no_nan_in_url(self, collector, tmp_dirs):
        """Old entries with NaN attachment_id must not produce 'nan' in coa_url."""
        pdf_dir, _ = tmp_dirs
        collector.pdf_dir = pdf_dir
        (pdf_dir / 'OldFile.pdf').write_bytes(b'%PDF-1.4' + os.urandom(20000))
        manifest = collector.catalog_existing()
        # Simulate pandas reading empty attachment_id as NaN.
        manifest['attachment_id'] = float('nan')
        results = collector._convert_to_lab_results(manifest)
        coa_url = results[0].get('coa_url') or ''
        assert 'nan' not in coa_url.lower(), f'coa_url contains nan: {coa_url}'

    def test_nan_product_name_not_string_nan(self, collector, tmp_dirs):
        """NaN product_name must become None, not string 'nan'."""
        pdf_dir, _ = tmp_dirs
        collector.pdf_dir = pdf_dir
        (pdf_dir / 'test.pdf').write_bytes(b'%PDF-1.4' + os.urandom(20000))
        manifest = collector.catalog_existing()
        manifest['product_name'] = float('nan')
        results = collector._convert_to_lab_results(manifest)
        pn = results[0].get('product_name')
        assert pn is None or pn == '', f'product_name is {repr(pn)}'

    def test_date_collected_not_cataloging_timestamp(self, collector, populated_pdf_dir):
        """date_collected should be None — it means when sample was collected for testing."""
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        manifest = collector.catalog_existing(compute_hashes=False)
        results = collector._convert_to_lab_results(manifest)
        for r in results:
            assert r.get('date_collected') is None, (
                f'date_collected should be None, got {r["date_collected"]}'
            )

    def test_data_refreshed_date_set(self, collector, populated_pdf_dir):
        """data_refreshed_date should be the current timestamp."""
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        manifest = collector.catalog_existing(compute_hashes=False)
        results = collector._convert_to_lab_results(manifest)
        for r in results:
            drd = r.get('data_refreshed_date')
            assert drd is not None and drd != '', (
                'data_refreshed_date should be set'
            )

    def test_sample_hash_uses_file_hash(self, collector, tmp_dirs):
        """sample_hash should use the manifest's file_hash for uniqueness."""
        pdf_dir, _ = tmp_dirs
        collector.pdf_dir = pdf_dir
        content_a = b'%PDF-1.4' + os.urandom(20000)
        content_b = b'%PDF-1.4' + os.urandom(20000)
        (pdf_dir / 'a.pdf').write_bytes(content_a)
        (pdf_dir / 'b.pdf').write_bytes(content_b)
        manifest = collector.catalog_existing(compute_hashes=True)
        results = collector._convert_to_lab_results(manifest)
        hashes = [r['sample_hash'] for r in results]
        assert len(set(hashes)) == 2, f'sample_hash should be unique per file: {hashes}'
        # Should match the SHA-256 content hashes from the manifest.
        manifest_hashes = set(manifest['file_hash'].tolist())
        assert set(hashes) == manifest_hashes

    def test_no_nan_in_lab_results_url(self, collector, tmp_dirs):
        """lab_results_url must not contain 'nan'."""
        pdf_dir, _ = tmp_dirs
        collector.pdf_dir = pdf_dir
        (pdf_dir / 'OldFile.pdf').write_bytes(b'%PDF-1.4' + os.urandom(20000))
        manifest = collector.catalog_existing()
        manifest['attachment_id'] = float('nan')
        results = collector._convert_to_lab_results(manifest)
        lru = results[0].get('lab_results_url') or ''
        assert 'nan' not in lru.lower(), f'lab_results_url contains nan: {lru}'


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Manifest Enrichment                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestEnrichManifest:

    def test_enriches_product_names(self, collector):
        manifest = pd.DataFrame([{
            'file_name': '12345.pdf', 'sample_id': '12345',
            'attachment_id': '12345', 'product_name': '', 'drop_number': '',
        }])
        discovered = pd.DataFrame([{
            'attachment_id': '12345', 'product_name': 'Blue Dream 3.5g',
            'drop_number': 65, 'drop_date': '12/15/25',
        }])
        enriched = collector._enrich_manifest(manifest, discovered)
        assert enriched.iloc[0]['product_name'] == 'Blue Dream 3.5g'

    def test_no_overwrite(self, collector):
        manifest = pd.DataFrame([{
            'file_name': '12345.pdf', 'sample_id': '12345',
            'attachment_id': '12345', 'product_name': 'Existing', 'drop_number': '',
        }])
        discovered = pd.DataFrame([{
            'attachment_id': '12345', 'product_name': 'New', 'drop_number': 65,
        }])
        enriched = collector._enrich_manifest(manifest, discovered)
        assert enriched.iloc[0]['product_name'] == 'Existing'

    def test_no_match(self, collector):
        manifest = pd.DataFrame([{
            'file_name': 'x.pdf', 'sample_id': 'x', 'attachment_id': '',
            'product_name': '',
        }])
        discovered = pd.DataFrame([{'attachment_id': '99', 'product_name': 'T'}])
        enriched = collector._enrich_manifest(manifest, discovered)
        assert enriched.iloc[0]['product_name'] == ''

    def test_missing_columns(self, collector):
        manifest = pd.DataFrame([{'file_name': 'test.pdf'}])
        discovered = pd.DataFrame([{'something': 'else'}])
        result = collector._enrich_manifest(manifest, discovered)
        assert len(result) == 1


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: get_results Orchestration                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestGetResults:

    def test_catalog_only(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        results = collector.get_results(catalog_only=True)
        assert len(results) == len(list(pdf_dir.glob('*.pdf')))

    def test_catalog_only_saves_csv(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        collector.get_results(catalog_only=True, save_results=True)
        assert (collector.datasets_dir / 'fl-results-flowery-latest.csv').exists()

    def test_catalog_only_no_selenium(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        collector.get_results(catalog_only=True)
        assert collector.driver is None

    def test_empty_archive(self, collector):
        results = collector.get_results(catalog_only=True)
        assert len(results) == 0

    def test_no_save(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        collector.get_results(catalog_only=True, save_results=False)
        assert not (collector.datasets_dir / 'fl-results-flowery-latest.csv').exists()


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Archive Stats                                              ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestArchiveStats:

    def test_empty(self, collector):
        stats = collector.archive_stats()
        assert stats['total_pdfs'] == 0
        assert stats['total_size_bytes'] == 0
        assert stats['manifest_exists'] is False

    def test_populated(self, collector, populated_pdf_dir):
        pdf_dir, files = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        collector.catalog_existing()
        stats = collector.archive_stats()
        assert stats['total_pdfs'] == len(files)
        assert stats['total_size_bytes'] > 0
        assert stats['manifest_exists'] is True
        assert stats['manifest_entries'] == len(files)

    def test_size_gb_type(self, collector, populated_pdf_dir):
        pdf_dir, _ = populated_pdf_dir
        collector.pdf_dir = pdf_dir
        assert isinstance(collector.archive_stats()['total_size_gb'], float)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Edge Cases                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestEdgeCases:

    def test_nonexistent_pdf_dir(self, tmp_path):
        c = FloweryCollector(
            pdf_dir=str(tmp_path / 'nonexistent' / 'pdfs'),
            data_dir=str(tmp_path),
        )
        assert c.pdf_dir.exists()

    def test_corrupt_manifest(self, collector, tmp_dirs):
        pdf_dir, _ = tmp_dirs
        collector.pdf_dir = pdf_dir
        collector.manifest_path.write_text('not,valid,csv\n"unclosed')
        (pdf_dir / 'test.pdf').write_bytes(b'%PDF-1.4' + os.urandom(20000))
        manifest = collector.catalog_existing()
        assert len(manifest) >= 1

    def test_non_pdf_files_ignored(self, collector, tmp_dirs):
        pdf_dir, _ = tmp_dirs
        collector.pdf_dir = pdf_dir
        (pdf_dir / 'notes.txt').write_text('not a pdf')
        (pdf_dir / 'image.png').write_bytes(b'\x89PNG')
        (pdf_dir / 'real.pdf').write_bytes(b'%PDF-1.4' + os.urandom(20000))
        assert len(collector.catalog_existing()) == 1

    def test_special_chars_in_filename(self, collector, tmp_dirs):
        pdf_dir, _ = tmp_dirs
        collector.pdf_dir = pdf_dir
        (pdf_dir / 'GG4 + GG4 Persy 2.5g [Close Friends].pdf').write_bytes(
            b'%PDF-1.4' + os.urandom(20000)
        )
        assert len(collector.catalog_existing()) == 1

    def test_quit_driver_when_none(self, collector):
        collector.driver = None
        collector._quit_driver()

    def test_transfer_cookies_no_driver(self, collector):
        collector.driver = None
        collector._transfer_cookies()

    def test_download_empty_df(self, collector):
        stats = collector.download_new_coas(pd.DataFrame())
        assert stats['downloaded'] == 0

    def test_scrape_all_drops_empty(self, collector):
        """scrape_all_drops with no drops found."""
        with patch.object(collector, '_get_page_source', return_value='<html></html>'):
            collector.driver = MagicMock()
            collector.driver.page_source = '<html></html>'
            drops = collector.scrape_drop_list()
            assert len(drops) == 0

    def test_respectful_pause(self, collector):
        """Pause should not raise."""
        import time
        start = time.time()
        collector._respectful_pause(multiplier=0.1)
        elapsed = time.time() - start
        assert elapsed < 2.0  # Should be fast with pause=0.01

    def test_large_batch_dedup(self, collector, tmp_dirs):
        """Many files with same content should dedup to 1."""
        pdf_dir, _ = tmp_dirs
        collector.pdf_dir = pdf_dir
        content = b'%PDF-1.4' + b'\x00' * 20000
        for i in range(20):
            (pdf_dir / f'dup_{i:03d}.pdf').write_bytes(content)
        manifest = collector.catalog_existing()
        assert len(manifest) == 1


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test: Integration (requires Selenium + network)                  ║
# ╚══════════════════════════════════════════════════════════════════╝

@pytest.mark.integration
class TestIntegration:

    def test_scrape_drop_list_live(self, tmp_path):
        c = FloweryCollector(pdf_dir=str(tmp_path / 'pdfs'), data_dir=str(tmp_path))
        c._init_selenium(headless=True)
        try:
            drops = c.scrape_drop_list()
            assert len(drops) > 0
            for d in drops:
                assert d['url']
                assert 'coa' in d['title'].lower()
        finally:
            c._quit_driver()

    def test_scrape_drop_page_live(self, tmp_path):
        c = FloweryCollector(pdf_dir=str(tmp_path / 'pdfs'), data_dir=str(tmp_path))
        c._init_selenium(headless=True)
        try:
            drops = c.scrape_drop_list()
            if drops:
                attachments = c.scrape_drop_page(drops[0])
                assert len(attachments) > 0
        finally:
            c._quit_driver()

    def test_download_single_coa_live(self, tmp_path):
        c = FloweryCollector(pdf_dir=str(tmp_path / 'pdfs'), data_dir=str(tmp_path))
        c._init_selenium(headless=True)
        try:
            drops = c.scrape_drop_list()
            if drops:
                attachments = c.scrape_drop_page(drops[0])
                c._transfer_cookies()
                if attachments:
                    result = c._download_coa(
                        attachments[0]['attachment_url'],
                        attachments[0]['attachment_id'],
                    )
                    assert result is not None
                    assert os.path.exists(result)
        finally:
            c._quit_driver()