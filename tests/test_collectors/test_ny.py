"""
Cannabis Results | New York Collector Tests
Copyright (c) 2024-2026 Cannlytics

Description:
    Comprehensive tests for all New York COA collection algorithms.

    Run unit tests (no network):
        pytest tests/test_collectors/test_ny.py -v -m "not integration"

    Run integration tests (requires network + Selenium):
        pytest tests/test_collectors/test_ny.py -v -m integration

    Run all tests:
        pytest tests/test_collectors/test_ny.py -v

    Run specific source tests:
        pytest tests/test_collectors/test_ny.py -v -k "CannabisRealm"
        pytest tests/test_collectors/test_ny.py -v -k "JettyExtracts"
        pytest tests/test_collectors/test_ny.py -v -k "Mycoa"
        pytest tests/test_collectors/test_ny.py -v -k "HudsonCannabis"
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

# Add output directory to path for imports.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Cannabis Realm imports ──────────────────────────────────────
from get_results_ny_cannabis_realm import (
    CannabisRealmCollector,
    ProgressTracker as RealmProgressTracker,
    _generate_result_id as realm_generate_id,
    _hash_url as realm_hash_url,
    _is_valid_pdf as realm_is_valid_pdf,
    _is_valid_pdf_file as realm_is_valid_pdf_file,
    _extract_percentage,
    _extract_weight_grams,
    _price_to_float,
    _sanitize_filename,
    _is_coa_link,
    BASE_URL as REALM_BASE_URL,
    CANNABIS_REALM_PRODUCER,
    STRAIN_TYPE_MAP,
    ALL_CATEGORIES,
    CORE_CATEGORIES,
)

# ── Jetty Extracts imports ─────────────────────────────────────
from get_results_ny_jetty_extracts import (
    JettyExtractsCollector,
    _generate_result_id as jetty_generate_id,
    _hash_url as jetty_hash_url,
    _is_valid_pdf as jetty_is_valid_pdf,
    _sanitize_filename as jetty_sanitize,
    COA_PAGE_URL as JETTY_COA_URL,
    JETTY_PRODUCER,
    GDRIVE_FOLDER_RE,
)

# ── MyCOA imports ──────────────────────────────────────────────
from get_results_ny_mycoa import (
    MycoaCollector,
    _generate_result_id as mycoa_generate_id,
    _hash_url as mycoa_hash_url,
    _is_valid_pdf as mycoa_is_valid_pdf,
    _dropbox_to_direct_url,
    _extract_filename_from_dropbox,
    MYCOA_URL,
    MYCOA_PRODUCER,
    DROPBOX_RE,
)

# ── Hudson Cannabis imports ────────────────────────────────────
from get_results_ny_hudson_cannabis import (
    HudsonCannabisCollector,
    _generate_result_id as hudson_generate_id,
    _hash_url as hudson_hash_url,
    _is_valid_pdf as hudson_is_valid_pdf,
    _extract_gdrive_file_id,
    _gdrive_direct_url,
    download_google_drive_file,
    COA_PAGE_URL as HUDSON_COA_URL,
    HUDSON_PRODUCER,
    GDRIVE_FILE_RE,
)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Fixtures                                                         ║
# ╚══════════════════════════════════════════════════════════════════╝

FAKE_PDF_HEADER = b'%PDF-1.4'
FAKE_PDF_DATA = FAKE_PDF_HEADER + b'\x00' * 10300  # >10KB


@pytest.fixture
def temp_dir():
    """Provide a temporary directory for test data."""
    with tempfile.TemporaryDirectory() as td:
        yield td


@pytest.fixture
def fake_pdf_data():
    """Provide valid fake PDF data."""
    return FAKE_PDF_DATA


@pytest.fixture
def realm_collector(temp_dir):
    """Provide a Cannabis Realm collector instance."""
    return CannabisRealmCollector(
        pdf_dir=os.path.join(temp_dir, 'pdfs', 'cannabis-realm'),
        data_dir=os.path.join(temp_dir, 'data'),
    )


@pytest.fixture
def jetty_collector(temp_dir):
    """Provide a Jetty Extracts collector instance."""
    return JettyExtractsCollector(
        pdf_dir=os.path.join(temp_dir, 'pdfs', 'jetty-extracts'),
        data_dir=os.path.join(temp_dir, 'data'),
    )


@pytest.fixture
def mycoa_collector(temp_dir):
    """Provide a MyCOA collector instance."""
    return MycoaCollector(
        pdf_dir=os.path.join(temp_dir, 'pdfs', 'my-coa'),
        data_dir=os.path.join(temp_dir, 'data'),
    )


@pytest.fixture
def hudson_collector(temp_dir):
    """Provide a Hudson Cannabis collector instance."""
    return HudsonCannabisCollector(
        pdf_dir=os.path.join(temp_dir, 'pdfs', 'hudson-cannabis'),
        data_dir=os.path.join(temp_dir, 'data'),
    )


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Cannabis Realm Tests                                             ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestRealmHelpers:
    """Tests for Cannabis Realm helper functions."""

    def test_generate_result_id_deterministic(self):
        assert realm_generate_id('a') == realm_generate_id('a')

    def test_generate_result_id_length(self):
        assert len(realm_generate_id('test')) == 16

    def test_generate_result_id_unique(self):
        assert realm_generate_id('a') != realm_generate_id('b')

    def test_hash_url_deterministic(self):
        assert realm_hash_url('x') == realm_hash_url('x')

    def test_hash_url_length(self):
        assert len(realm_hash_url('test')) == 12

    def test_is_valid_pdf_valid(self):
        assert realm_is_valid_pdf(FAKE_PDF_DATA)

    def test_is_valid_pdf_html(self):
        assert not realm_is_valid_pdf(b'<html>')

    def test_is_valid_pdf_too_small(self):
        assert not realm_is_valid_pdf(b'%PDF-short')

    def test_is_valid_pdf_empty(self):
        assert not realm_is_valid_pdf(b'')


class TestExtractPercentage:
    """Tests for _extract_percentage."""

    def test_normal(self):
        assert _extract_percentage('24.5%') == 24.5

    def test_zero(self):
        assert _extract_percentage('0%') == 0.0

    def test_decimal(self):
        assert _extract_percentage('0.1%') == 0.1

    def test_none_input(self):
        assert _extract_percentage(None) is None

    def test_invalid(self):
        assert _extract_percentage('abc') is None

    def test_no_percent_sign(self):
        assert _extract_percentage('24.5') == 24.5


class TestExtractWeightGrams:
    """Tests for _extract_weight_grams."""

    def test_grams(self):
        assert _extract_weight_grams('3.5g') == 3.5

    def test_one_gram(self):
        assert _extract_weight_grams('1g') == 1.0

    def test_ounce(self):
        assert abs(_extract_weight_grams('1oz') - 28.35) < 0.01

    def test_fraction_oz(self):
        result = _extract_weight_grams('1/8oz')
        assert result is not None
        assert abs(result - 3.54375) < 0.01

    def test_none_input(self):
        assert _extract_weight_grams(None) is None

    def test_invalid(self):
        assert _extract_weight_grams('abc') is None

    def test_empty(self):
        assert _extract_weight_grams('') is None


class TestPriceToFloat:
    """Tests for _price_to_float."""

    def test_normal(self):
        assert _price_to_float('$45.00') == 45.0

    def test_comma(self):
        assert _price_to_float('$1,299') == 1299.0

    def test_no_dollar(self):
        assert _price_to_float('45') == 45.0

    def test_none(self):
        assert _price_to_float(None) is None

    def test_invalid(self):
        assert _price_to_float('free') is None


class TestSanitizeFilename:
    """Tests for _sanitize_filename."""

    def test_spaces(self):
        assert _sanitize_filename('Hello World!') == 'Hello-World'

    def test_empty(self):
        assert _sanitize_filename('') == 'unknown'

    def test_long_name(self):
        name = 'A' * 200
        assert len(_sanitize_filename(name)) <= 100

    def test_special_chars(self):
        result = _sanitize_filename('file@#$name')
        assert '@' not in result
        assert '#' not in result


class TestIsCOALink:
    """Tests for _is_coa_link."""

    def test_pdf_link(self):
        assert _is_coa_link('https://example.com/report.pdf', '')

    def test_coa_keyword_in_href(self):
        assert _is_coa_link('https://example.com/coa-data', '')

    def test_lab_keyword_in_text(self):
        assert _is_coa_link('https://example.com/x', 'Lab Results')

    def test_not_coa(self):
        assert not _is_coa_link('https://example.com/shop', 'Buy')

    def test_empty_href(self):
        assert not _is_coa_link('', 'some text')


class TestRealmProgressTracker:
    """Tests for Cannabis Realm ProgressTracker."""

    def test_init_empty(self, temp_dir):
        pt = RealmProgressTracker(
            os.path.join(temp_dir, 'progress.json'),
        )
        assert len(pt.scraped_categories) == 0
        assert len(pt.scraped_product_urls) == 0
        assert len(pt.discovered_coas) == 0

    def test_record_category(self, temp_dir):
        pt = RealmProgressTracker(
            os.path.join(temp_dir, 'progress.json'),
        )
        pt.record_category('/menu/categories/flower')
        assert '/menu/categories/flower' in pt.scraped_categories

    def test_record_product(self, temp_dir):
        pt = RealmProgressTracker(
            os.path.join(temp_dir, 'progress.json'),
        )
        pt.record_product_visited('http://example.com/product/1')
        assert 'http://example.com/product/1' in pt.scraped_product_urls

    def test_record_coa(self, temp_dir):
        pt = RealmProgressTracker(
            os.path.join(temp_dir, 'progress.json'),
        )
        pt.record_coa_discovered('http://prod', 'http://coa.pdf')
        assert pt.discovered_coas['http://prod'] == 'http://coa.pdf'
        assert pt.state['total_coas_found'] == 1

    def test_record_download(self, temp_dir):
        pt = RealmProgressTracker(
            os.path.join(temp_dir, 'progress.json'),
        )
        pt.record_download('http://coa.pdf')
        assert 'http://coa.pdf' in pt.downloaded

    def test_save_and_reload(self, temp_dir):
        path = os.path.join(temp_dir, 'progress.json')
        pt = RealmProgressTracker(path)
        pt.record_category('/menu/categories/flower')
        pt.record_coa_discovered('p1', 'c1')
        pt.save()

        pt2 = RealmProgressTracker(path)
        assert '/menu/categories/flower' in pt2.scraped_categories
        assert pt2.discovered_coas['p1'] == 'c1'

    def test_no_duplicate_categories(self, temp_dir):
        pt = RealmProgressTracker(
            os.path.join(temp_dir, 'progress.json'),
        )
        pt.record_category('/menu/categories/flower')
        pt.record_category('/menu/categories/flower')
        assert len(pt.state['scraped_categories']) == 1


class TestRealmConstants:
    """Tests for Cannabis Realm constants."""

    def test_base_url(self):
        assert REALM_BASE_URL == 'https://cannabisrealmny.com'

    def test_producer_metadata(self):
        assert CANNABIS_REALM_PRODUCER['producer'] == 'Cannabis Realm'
        assert CANNABIS_REALM_PRODUCER['producer_state'] == 'ny'

    def test_strain_type_map(self):
        assert 'HYBRID' in STRAIN_TYPE_MAP
        assert 'INDICA' in STRAIN_TYPE_MAP
        assert 'SATIVA' in STRAIN_TYPE_MAP
        assert STRAIN_TYPE_MAP['INDICA']['indica_percentage'] == 1.0

    def test_categories(self):
        assert len(CORE_CATEGORIES) > 0
        assert len(ALL_CATEGORIES) >= len(CORE_CATEGORIES)
        assert all(
            c.startswith('/menu/categories/')
            for c in ALL_CATEGORIES
        )


class TestRealmCollectorInit:
    """Tests for Cannabis Realm collector initialization."""

    def test_init_creates_dirs(self, realm_collector):
        assert realm_collector.pdf_dir.exists()
        assert realm_collector.datasets_dir.exists()

    def test_init_paths(self, realm_collector):
        assert 'cannabis-realm' in str(realm_collector.pdf_dir)
        assert realm_collector.manifest_path.name == (
            'cannabis-realm-manifest.csv'
        )

    def test_context_manager(self, temp_dir):
        with CannabisRealmCollector(
            pdf_dir=os.path.join(temp_dir, 'pdfs'),
            data_dir=os.path.join(temp_dir, 'data'),
        ) as c:
            assert c is not None
            assert c.pdf_dir.exists()


class TestRealmCatalog:
    """Tests for Cannabis Realm catalog phase."""

    def test_catalog_empty(self, realm_collector):
        manifest = realm_collector.catalog_existing()
        assert len(manifest) == 0

    def test_catalog_with_valid_pdf(self, realm_collector, fake_pdf_data):
        pdf_path = realm_collector.pdf_dir / 'test.pdf'
        with open(str(pdf_path), 'wb') as f:
            f.write(fake_pdf_data)

        manifest = realm_collector.catalog_existing(save=False)
        assert len(manifest) == 1
        assert manifest.iloc[0]['file_name'] == 'test.pdf'

    def test_catalog_invalid_pdf(self, realm_collector):
        pdf_path = realm_collector.pdf_dir / 'bad.pdf'
        with open(str(pdf_path), 'wb') as f:
            f.write(b'<html>not a pdf</html>')

        manifest = realm_collector.catalog_existing(save=False)
        assert len(manifest) == 0

    def test_catalog_deduplication(self, realm_collector, fake_pdf_data):
        for name in ['a.pdf', 'b.pdf']:
            with open(str(realm_collector.pdf_dir / name), 'wb') as f:
                f.write(fake_pdf_data)

        manifest = realm_collector.catalog_existing(save=False)
        assert len(manifest) == 1  # Same content → deduplicated

    def test_catalog_saves_to_disk(self, realm_collector, fake_pdf_data):
        pdf_path = realm_collector.pdf_dir / 'test.pdf'
        with open(str(pdf_path), 'wb') as f:
            f.write(fake_pdf_data)

        realm_collector.catalog_existing(save=True)
        assert realm_collector.manifest_path.exists()


class TestRealmConvert:
    """Tests for Cannabis Realm convert phase."""

    def test_convert_empty(self, realm_collector):
        results = realm_collector._convert_to_lab_results(
            pd.DataFrame(),
        )
        assert len(results) == 0

    def test_convert_single(self, realm_collector):
        manifest = pd.DataFrame([{
            'file_name': 'test.pdf',
            'file_hash': 'abc123',
            'date_cataloged': '2026-01-01',
        }])
        results = realm_collector._convert_to_lab_results(manifest)
        assert len(results) == 1
        assert results[0]['source'] == 'cannabis_realm'
        assert results[0]['state'] == 'ny'
        assert 'result_id' in results[0]

    def test_convert_with_products(self, realm_collector):
        manifest = pd.DataFrame([{
            'file_name': 'abc123def456.pdf',
            'file_hash': 'hash1',
            'date_cataloged': '2026-01-01',
        }])
        products = pd.DataFrame([{
            'product_id': 'abc123def456',
            'product_name': 'Blue Dream',
            'brand': 'Great Cannabis Co',
            'category': 'flower',
            'strain_type': 'HYBRID',
            'total_thc': 24.5,
            'coa_url': 'https://example.com/coa.pdf',
        }])
        results = realm_collector._convert_to_lab_results(
            manifest, products,
        )
        assert len(results) == 1
        assert results[0]['product_name'] == 'Blue Dream'
        assert results[0]['producer'] == 'Great Cannabis Co'

    def test_convert_catalog_only_mode(
            self, realm_collector, fake_pdf_data,
    ):
        pdf_path = realm_collector.pdf_dir / 'test.pdf'
        with open(str(pdf_path), 'wb') as f:
            f.write(fake_pdf_data)

        results = realm_collector.get_results(catalog_only=True)
        assert len(results) == 1
        assert 'result_id' in results.columns


class TestRealmStats:
    """Tests for Cannabis Realm archive stats."""

    def test_stats_empty(self, realm_collector):
        stats = realm_collector.archive_stats()
        assert stats['total_pdfs'] == 0
        assert stats['source'] == 'cannabis_realm'

    def test_stats_with_pdfs(self, realm_collector, fake_pdf_data):
        for name in ['a.pdf', 'b.pdf']:
            with open(
                str(realm_collector.pdf_dir / name), 'wb',
            ) as f:
                f.write(fake_pdf_data)

        stats = realm_collector.archive_stats()
        assert stats['total_pdfs'] == 2


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Jetty Extracts Tests                                             ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestJettyConstants:
    """Tests for Jetty Extracts constants."""

    def test_coa_url(self):
        assert 'jettyextracts.com' in JETTY_COA_URL

    def test_producer(self):
        assert JETTY_PRODUCER['producer'] == 'Jetty Extracts'
        assert JETTY_PRODUCER['producer_state'] == 'ny'

    def test_gdrive_folder_regex(self):
        url = 'https://drive.google.com/drive/folders/abc123'
        assert GDRIVE_FOLDER_RE.search(url)
        assert not GDRIVE_FOLDER_RE.search('https://example.com')


class TestJettyCollectorInit:
    """Tests for Jetty Extracts collector initialization."""

    def test_init_creates_dirs(self, jetty_collector):
        assert jetty_collector.pdf_dir.exists()
        assert jetty_collector.datasets_dir.exists()

    def test_init_paths(self, jetty_collector):
        assert 'jetty-extracts' in str(jetty_collector.pdf_dir)

    def test_context_manager(self, temp_dir):
        with JettyExtractsCollector(
            pdf_dir=os.path.join(temp_dir, 'pdfs'),
            data_dir=os.path.join(temp_dir, 'data'),
        ) as c:
            assert c is not None


class TestJettyCatalog:
    """Tests for Jetty Extracts catalog phase."""

    def test_catalog_empty(self, jetty_collector):
        manifest = jetty_collector.catalog_existing()
        assert len(manifest) == 0

    def test_catalog_with_pdf(self, jetty_collector, fake_pdf_data):
        pdf_path = jetty_collector.pdf_dir / 'test.pdf'
        with open(str(pdf_path), 'wb') as f:
            f.write(fake_pdf_data)
        manifest = jetty_collector.catalog_existing(save=False)
        assert len(manifest) == 1

    def test_catalog_nested_pdfs(self, jetty_collector, fake_pdf_data):
        """Jetty uses rglob for nested folder downloads."""
        subdir = jetty_collector.pdf_dir / 'subfolder'
        subdir.mkdir()
        with open(str(subdir / 'nested.pdf'), 'wb') as f:
            f.write(fake_pdf_data)
        manifest = jetty_collector.catalog_existing(save=False)
        assert len(manifest) == 1


class TestJettyDiscover:
    """Tests for Jetty Extracts discover phase."""

    def test_discover_missing_datafile(self, jetty_collector):
        urls = jetty_collector.discover_folder_urls()
        assert len(urls) == 0

    def test_discover_from_datafile(self, jetty_collector):
        # Create a mock datafile.
        df = pd.DataFrame({
            'product': ['Product A', 'Product B'],
            'folder_url': [
                'https://drive.google.com/drive/folders/abc123',
                'https://drive.google.com/drive/folders/def456',
            ],
        })
        df.to_csv(str(jetty_collector.datafile_path), index=False)

        urls = jetty_collector.discover_folder_urls()
        assert len(urls) == 2


class TestJettyConvert:
    """Tests for Jetty Extracts convert phase."""

    def test_convert_empty(self, jetty_collector):
        results = jetty_collector._convert_to_lab_results(
            pd.DataFrame(),
        )
        assert len(results) == 0

    def test_convert_single(self, jetty_collector):
        manifest = pd.DataFrame([{
            'file_name': 'test-product.pdf',
            'file_hash': 'abc123',
            'date_cataloged': '2026-01-01',
        }])
        results = jetty_collector._convert_to_lab_results(manifest)
        assert len(results) == 1
        assert results[0]['source'] == 'jetty_extracts'
        assert results[0]['producer'] == 'Jetty Extracts'


class TestJettyStats:
    """Tests for Jetty Extracts archive stats."""

    def test_stats_empty(self, jetty_collector):
        stats = jetty_collector.archive_stats()
        assert stats['total_pdfs'] == 0
        assert stats['source'] == 'jetty_extracts'


# ╔══════════════════════════════════════════════════════════════════╗
# ║ MyCOA Tests                                                      ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestMycoaHelpers:
    """Tests for MyCOA helper functions."""

    def test_dropbox_dl0_to_dl1(self):
        url = 'https://www.dropbox.com/s/abc/file.pdf?dl=0'
        assert _dropbox_to_direct_url(url).endswith('dl=1')

    def test_dropbox_already_dl1(self):
        url = 'https://www.dropbox.com/s/abc/file.pdf?dl=1'
        assert _dropbox_to_direct_url(url) == url

    def test_dropbox_no_dl_param(self):
        url = 'https://www.dropbox.com/s/abc/file.pdf'
        result = _dropbox_to_direct_url(url)
        assert 'dl=1' in result

    def test_extract_filename_normal(self):
        url = 'https://www.dropbox.com/s/abc/Report.pdf?dl=0'
        assert _extract_filename_from_dropbox(url) == 'Report.pdf'

    def test_extract_filename_encoded(self):
        url = 'https://www.dropbox.com/s/abc/My%20Report.pdf?dl=0'
        name = _extract_filename_from_dropbox(url)
        assert 'Report' in name

    def test_extract_filename_fallback(self):
        url = 'https://www.dropbox.com/s/abc'
        name = _extract_filename_from_dropbox(url)
        assert name.endswith('.pdf')

    def test_dropbox_regex(self):
        assert DROPBOX_RE.search(
            'https://www.dropbox.com/s/abc123/file.pdf',
        )
        assert not DROPBOX_RE.search('https://example.com')


class TestMycoaConstants:
    """Tests for MyCOA constants."""

    def test_url(self):
        assert 'mycoa.info' in MYCOA_URL

    def test_producer(self):
        assert MYCOA_PRODUCER['producer'] == 'MFNY'
        assert MYCOA_PRODUCER['producer_state'] == 'ny'


class TestMycoaCollectorInit:
    """Tests for MyCOA collector initialization."""

    def test_init_creates_dirs(self, mycoa_collector):
        assert mycoa_collector.pdf_dir.exists()
        assert mycoa_collector.datasets_dir.exists()

    def test_init_paths(self, mycoa_collector):
        assert 'my-coa' in str(mycoa_collector.pdf_dir)

    def test_context_manager(self, temp_dir):
        with MycoaCollector(
            pdf_dir=os.path.join(temp_dir, 'pdfs'),
            data_dir=os.path.join(temp_dir, 'data'),
        ) as c:
            assert c is not None


class TestMycoaCatalog:
    """Tests for MyCOA catalog phase."""

    def test_catalog_empty(self, mycoa_collector):
        manifest = mycoa_collector.catalog_existing()
        assert len(manifest) == 0

    def test_catalog_with_pdf(self, mycoa_collector, fake_pdf_data):
        pdf_path = mycoa_collector.pdf_dir / 'test.pdf'
        with open(str(pdf_path), 'wb') as f:
            f.write(fake_pdf_data)
        manifest = mycoa_collector.catalog_existing(save=False)
        assert len(manifest) == 1


class TestMycoaConvert:
    """Tests for MyCOA convert phase."""

    def test_convert_empty(self, mycoa_collector):
        results = mycoa_collector._convert_to_lab_results(
            pd.DataFrame(),
        )
        assert len(results) == 0

    def test_convert_single(self, mycoa_collector):
        manifest = pd.DataFrame([{
            'file_name': 'mfny-batch.pdf',
            'file_hash': 'xyz789',
            'date_cataloged': '2026-01-01',
        }])
        results = mycoa_collector._convert_to_lab_results(manifest)
        assert len(results) == 1
        assert results[0]['source'] == 'mycoa'
        assert results[0]['producer'] == 'MFNY'


class TestMycoaStats:
    """Tests for MyCOA archive stats."""

    def test_stats_empty(self, mycoa_collector):
        stats = mycoa_collector.archive_stats()
        assert stats['total_pdfs'] == 0
        assert stats['source'] == 'mycoa'


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Hudson Cannabis Tests                                            ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestHudsonHelpers:
    """Tests for Hudson Cannabis helper functions."""

    def test_extract_file_id(self):
        url = 'https://drive.google.com/file/d/1AbCdEf/view'
        assert _extract_gdrive_file_id(url) == '1AbCdEf'

    def test_extract_file_id_none(self):
        assert _extract_gdrive_file_id('https://example.com') is None

    def test_gdrive_direct_url(self):
        direct = _gdrive_direct_url('abc123')
        assert 'abc123' in direct
        assert 'export=download' in direct

    def test_gdrive_file_regex(self):
        assert GDRIVE_FILE_RE.search(
            'https://drive.google.com/file/d/abc123/view',
        )
        assert not GDRIVE_FILE_RE.search(
            'https://drive.google.com/drive/folders/abc',
        )


class TestHudsonConstants:
    """Tests for Hudson Cannabis constants."""

    def test_coa_url(self):
        assert 'hudsoncannabis.co' in HUDSON_COA_URL

    def test_producer(self):
        assert HUDSON_PRODUCER['producer'] == 'Hudson Cannabis'
        assert HUDSON_PRODUCER['producer_state'] == 'ny'


class TestHudsonCollectorInit:
    """Tests for Hudson Cannabis collector initialization."""

    def test_init_creates_dirs(self, hudson_collector):
        assert hudson_collector.pdf_dir.exists()
        assert hudson_collector.datasets_dir.exists()

    def test_init_paths(self, hudson_collector):
        assert 'hudson-cannabis' in str(hudson_collector.pdf_dir)

    def test_context_manager(self, temp_dir):
        with HudsonCannabisCollector(
            pdf_dir=os.path.join(temp_dir, 'pdfs'),
            data_dir=os.path.join(temp_dir, 'data'),
        ) as c:
            assert c is not None


class TestHudsonCatalog:
    """Tests for Hudson Cannabis catalog phase."""

    def test_catalog_empty(self, hudson_collector):
        manifest = hudson_collector.catalog_existing()
        assert len(manifest) == 0

    def test_catalog_with_pdf(self, hudson_collector, fake_pdf_data):
        pdf_path = hudson_collector.pdf_dir / 'test.pdf'
        with open(str(pdf_path), 'wb') as f:
            f.write(fake_pdf_data)
        manifest = hudson_collector.catalog_existing(save=False)
        assert len(manifest) == 1


class TestHudsonConvert:
    """Tests for Hudson Cannabis convert phase."""

    def test_convert_empty(self, hudson_collector):
        results = hudson_collector._convert_to_lab_results(
            pd.DataFrame(),
        )
        assert len(results) == 0

    def test_convert_single(self, hudson_collector):
        manifest = pd.DataFrame([{
            'file_name': '1AbCdEfGhIjKlMnOpQrStUvWxYz.pdf',
            'file_hash': 'xyz789',
            'date_cataloged': '2026-01-01',
        }])
        results = hudson_collector._convert_to_lab_results(manifest)
        assert len(results) == 1
        assert results[0]['source'] == 'hudson_cannabis'
        assert results[0]['producer'] == 'Hudson Cannabis'
        assert 'drive.google.com' in results[0]['lab_results_url']


class TestHudsonStats:
    """Tests for Hudson Cannabis archive stats."""

    def test_stats_empty(self, hudson_collector):
        stats = hudson_collector.archive_stats()
        assert stats['total_pdfs'] == 0
        assert stats['source'] == 'hudson_cannabis'


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Cross-Module Consistency Tests                                   ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestCrossModuleConsistency:
    """Tests ensuring consistency across all NY collectors."""

    ALL_COLLECTORS = [
        CannabisRealmCollector,
        JettyExtractsCollector,
        MycoaCollector,
        HudsonCannabisCollector,
    ]

    def test_all_have_context_manager(self, temp_dir):
        for cls in self.ALL_COLLECTORS:
            c = cls(
                pdf_dir=os.path.join(temp_dir, 'pdfs', cls.__name__),
                data_dir=os.path.join(temp_dir, 'data'),
            )
            assert hasattr(c, '__enter__')
            assert hasattr(c, '__exit__')

    def test_all_have_get_results(self, temp_dir):
        for cls in self.ALL_COLLECTORS:
            c = cls(
                pdf_dir=os.path.join(temp_dir, 'pdfs', cls.__name__),
                data_dir=os.path.join(temp_dir, 'data'),
            )
            assert hasattr(c, 'get_results')
            assert callable(c.get_results)

    def test_all_have_catalog_existing(self, temp_dir):
        for cls in self.ALL_COLLECTORS:
            c = cls(
                pdf_dir=os.path.join(temp_dir, 'pdfs', cls.__name__),
                data_dir=os.path.join(temp_dir, 'data'),
            )
            assert hasattr(c, 'catalog_existing')

    def test_all_have_archive_stats(self, temp_dir):
        for cls in self.ALL_COLLECTORS:
            c = cls(
                pdf_dir=os.path.join(temp_dir, 'pdfs', cls.__name__),
                data_dir=os.path.join(temp_dir, 'data'),
            )
            stats = c.archive_stats()
            assert 'source' in stats
            assert 'total_pdfs' in stats
            assert stats['state'] == 'ny'

    def test_result_id_consistency(self):
        """All modules produce same ID for same input."""
        test_input = 'test-consistency'
        ids = [
            realm_generate_id(test_input),
            jetty_generate_id(test_input),
            mycoa_generate_id(test_input),
            hudson_generate_id(test_input),
        ]
        assert len(set(ids)) == 1

    def test_hash_url_consistency(self):
        """All modules produce same hash for same URL."""
        test_url = 'https://example.com/coa.pdf'
        hashes = [
            realm_hash_url(test_url),
            jetty_hash_url(test_url),
            mycoa_hash_url(test_url),
            hudson_hash_url(test_url),
        ]
        assert len(set(hashes)) == 1

    def test_pdf_validation_consistency(self):
        """All modules agree on PDF validity."""
        valid = FAKE_PDF_DATA
        invalid = b'<html>not a pdf</html>'
        assert all([
            realm_is_valid_pdf(valid),
            jetty_is_valid_pdf(valid),
            mycoa_is_valid_pdf(valid),
            hudson_is_valid_pdf(valid),
        ])
        assert not any([
            realm_is_valid_pdf(invalid),
            jetty_is_valid_pdf(invalid),
            mycoa_is_valid_pdf(invalid),
            hudson_is_valid_pdf(invalid),
        ])

    def test_all_required_dirs_exist(self, temp_dir):
        for cls in self.ALL_COLLECTORS:
            c = cls(
                pdf_dir=os.path.join(temp_dir, 'pdfs', cls.__name__),
                data_dir=os.path.join(temp_dir, 'data'),
            )
            assert c.pdf_dir.exists()
            assert c.datasets_dir.exists()
