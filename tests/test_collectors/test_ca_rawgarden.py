"""
Cannabis Results | CA Raw Garden Collector Tests
Copyright (c) 2024-2026 Cannlytics

Description:
    Tests for the California Raw Garden collector.

    Run unit tests (no network):
        pytest tests/test_collectors/test_ca_rawgarden.py -v -m "not integration"

    Run integration tests (requires network):
        pytest tests/test_collectors/test_ca_rawgarden.py -v -m integration

    Run all tests:
        pytest tests/test_collectors/test_ca_rawgarden.py -v
"""
# Standard imports:
from datetime import datetime
from pathlib import Path
import sys

# External imports:
import pytest

# Ensure project root is in path.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Internal imports:
from algorithms.get_results_ca_rawgarden import (
    RawGardenCollector,
    BASE_URL,
    RAW_GARDEN,
    SUBTYPE_TO_PRODUCT_TYPE,
    DEFAULT_HEADERS,
    _kebab_case,
)


# =========================================================================
# Fixtures
# =========================================================================

@pytest.fixture
def raw_garden_collector(temp_data_dir, temp_pdf_dir, temp_cache_path, temp_log_dir):
    """Pre-configured RawGardenCollector for testing."""
    return RawGardenCollector(
        data_dir=str(temp_data_dir),
        pdf_dir=str(temp_pdf_dir),
        cache_path=temp_cache_path,
        log_dir=str(temp_log_dir),
        verbose=False,
    )


@pytest.fixture
def sample_raw_product():
    """Sample raw product as produced by the scraping pipeline."""
    return {
        'product_name': 'Wave Rider',
        'product_subtype': 'Refined Live Resin Cartridges',
        'date_retail': '2024-07-28T00:00:00',
        'coa_pdf': '220000985-Wave-Rider-Live-Resin.pdf',
        'coa_url': 'https://rawgarden.farm/wp-content/uploads/2025/03/220000985-Wave-Rider-Live-Resin.pdf',
    }


# =========================================================================
# Constants Tests
# =========================================================================

class TestRawGardenConstants:
    """Tests for module-level constants."""

    def test_base_url(self):
        """Test base URL is correctly defined."""
        assert BASE_URL == 'https://rawgarden.farm/lab-results/'
        assert BASE_URL.startswith('https://')

    def test_producer_metadata_complete(self):
        """Test that producer metadata has all required fields."""
        required_fields = [
            'business_dba_name',
            'business_legal_name',
            'business_website',
            'producer_license_number',
            'distributor_license_number',
            'producer_latitude',
            'producer_longitude',
            'producer_street_address',
            'producer_city',
            'producer_county',
            'producer_state',
        ]
        for field in required_fields:
            assert field in RAW_GARDEN, f"Missing producer field: {field}"

    def test_producer_license_number(self):
        """Test that the manufacturing license number is correct."""
        assert RAW_GARDEN['producer_license_number'] == 'CDPH-10003156'

    def test_distributor_license_number(self):
        """Test that the distribution license number is correct."""
        assert RAW_GARDEN['distributor_license_number'] == 'C11-0000496-LIC'

    def test_producer_state(self):
        """Test that the producer state is California."""
        assert RAW_GARDEN['producer_state'] == 'CA'

    def test_producer_county(self):
        """Test that the producer county is Santa Barbara."""
        assert RAW_GARDEN['producer_county'] == 'Santa Barbara'

    def test_producer_coordinates_valid(self):
        """Test that producer coordinates are valid for Lompoc, CA."""
        lat = RAW_GARDEN['producer_latitude']
        lon = RAW_GARDEN['producer_longitude']
        # Lompoc, CA is roughly 34.6°N, 120.5°W.
        assert 34.0 <= lat <= 35.0, f"Latitude out of range: {lat}"
        assert -121.0 <= lon <= -120.0, f"Longitude out of range: {lon}"

    def test_default_headers_has_user_agent(self):
        """Test that default headers include a User-Agent."""
        assert 'User-Agent' in DEFAULT_HEADERS
        assert 'Mozilla' in DEFAULT_HEADERS['User-Agent']

    def test_subtype_mapping_has_key_categories(self):
        """Test that subtype mapping covers key Raw Garden products."""
        key_subtypes = [
            'refined live resin cartridges',
            'refined live resin',
            'flower',
            'pre-rolls',
        ]
        for subtype in key_subtypes:
            assert subtype in SUBTYPE_TO_PRODUCT_TYPE, (
                f"Missing subtype mapping: {subtype}"
            )

    def test_subtype_mapping_values_valid(self):
        """Test that all mapped product types are valid."""
        valid_types = {'flower', 'vape', 'concentrate', 'preroll', 'edible'}
        for subtype, product_type in SUBTYPE_TO_PRODUCT_TYPE.items():
            assert product_type in valid_types, (
                f"Invalid product type '{product_type}' for subtype '{subtype}'"
            )


# =========================================================================
# Kebab Case Tests
# =========================================================================

class TestKebabCase:
    """Tests for the _kebab_case utility function."""

    def test_spaces_to_hyphens(self):
        """Test that spaces become hyphens."""
        assert _kebab_case('Refined Live Resin') == 'refined-live-resin'

    def test_mixed_case_lowered(self):
        """Test that mixed case is lowered."""
        assert _kebab_case('RG Diamonds') == 'rg-diamonds'

    def test_special_characters_replaced(self):
        """Test that special characters become hyphens."""
        assert _kebab_case('Pre-Rolls') == 'pre-rolls'

    def test_whitespace_trimmed(self):
        """Test that leading/trailing whitespace is trimmed."""
        assert _kebab_case('  Flower  ') == 'flower'

    def test_multiple_special_chars_collapsed(self):
        """Test that multiple special chars collapse to single hyphen."""
        assert _kebab_case('foo   bar') == 'foo-bar'

    def test_no_trailing_hyphens(self):
        """Test that result has no trailing hyphens."""
        result = _kebab_case('test!')
        assert not result.endswith('-')
        assert not result.startswith('-')


# =========================================================================
# Collector Initialization Tests
# =========================================================================

class TestRawGardenCollectorInit:
    """Tests for collector initialization."""

    def test_init_default(self, temp_data_dir, temp_pdf_dir, temp_cache_path, temp_log_dir):
        """Test default initialization."""
        collector = RawGardenCollector(
            data_dir=str(temp_data_dir),
            pdf_dir=str(temp_pdf_dir),
            cache_path=temp_cache_path,
            log_dir=str(temp_log_dir),
            verbose=False,
        )
        assert collector.state == 'ca'
        assert collector.source == 'raw_garden'
        assert collector.pause_time == 1.0

    def test_init_custom_pause(self, temp_data_dir, temp_pdf_dir, temp_cache_path, temp_log_dir):
        """Test initialization with custom pause time."""
        collector = RawGardenCollector(
            data_dir=str(temp_data_dir),
            pdf_dir=str(temp_pdf_dir),
            cache_path=temp_cache_path,
            log_dir=str(temp_log_dir),
            pause_time=5.0,
            verbose=False,
        )
        assert collector.pause_time == 5.0

    def test_init_creates_directories(self, tmp_path):
        """Test that initialization creates necessary directories."""
        data_dir = tmp_path / 'test_data'
        pdf_dir = tmp_path / 'test_pdfs'
        log_dir = tmp_path / 'test_logs'

        RawGardenCollector(
            data_dir=str(data_dir),
            pdf_dir=str(pdf_dir),
            log_dir=str(log_dir),
            cache_path=str(tmp_path / 'cache.jsonl'),
            verbose=False,
        )

        assert data_dir.exists()
        assert pdf_dir.exists()
        assert log_dir.exists()

    def test_context_manager(self, temp_data_dir, temp_pdf_dir, temp_cache_path, temp_log_dir):
        """Test collector as context manager."""
        with RawGardenCollector(
            data_dir=str(temp_data_dir),
            pdf_dir=str(temp_pdf_dir),
            cache_path=temp_cache_path,
            log_dir=str(temp_log_dir),
            verbose=False,
        ) as collector:
            assert collector is not None
            assert collector.state == 'ca'
            assert collector.source == 'raw_garden'


# =========================================================================
# Static Helper Tests
# =========================================================================

class TestGenerateResultId:
    """Tests for _generate_result_id static method."""

    def test_id_length(self):
        """Test that generated ID is 16 characters."""
        result_id = RawGardenCollector._generate_result_id(
            'Wave Rider', 'https://example.com/coa.pdf'
        )
        assert len(result_id) == 16

    def test_id_is_hex_string(self):
        """Test that ID is a valid hex string."""
        result_id = RawGardenCollector._generate_result_id(
            'Wave Rider', 'https://example.com/coa.pdf'
        )
        int(result_id, 16)  # Raises ValueError if invalid.

    def test_id_deterministic(self):
        """Test that same inputs produce same ID."""
        id1 = RawGardenCollector._generate_result_id('A', 'https://example.com/a.pdf')
        id2 = RawGardenCollector._generate_result_id('A', 'https://example.com/a.pdf')
        assert id1 == id2

    def test_id_unique_by_name(self):
        """Test that different names produce different IDs."""
        id1 = RawGardenCollector._generate_result_id('Product A', 'https://example.com/a.pdf')
        id2 = RawGardenCollector._generate_result_id('Product B', 'https://example.com/a.pdf')
        assert id1 != id2

    def test_id_unique_by_url(self):
        """Test that different URLs produce different IDs."""
        id1 = RawGardenCollector._generate_result_id('Product A', 'https://example.com/a.pdf')
        id2 = RawGardenCollector._generate_result_id('Product A', 'https://example.com/b.pdf')
        assert id1 != id2

    def test_id_handles_none(self):
        """Test ID generation with None values."""
        result_id = RawGardenCollector._generate_result_id(None, None)
        assert len(result_id) == 16
        assert isinstance(result_id, str)


class TestNormalizeSubtype:
    """Tests for _normalize_subtype static method."""

    def test_cartridges(self):
        """Test cartridge subtype normalization."""
        assert RawGardenCollector._normalize_subtype('Refined Live Resin Cartridges') == 'vape'

    def test_ready_to_use(self):
        """Test ready-to-use subtype normalization."""
        assert RawGardenCollector._normalize_subtype('Refined Live Resin Ready-To-Use') == 'vape'

    def test_concentrate(self):
        """Test concentrate subtype normalization."""
        assert RawGardenCollector._normalize_subtype('Refined Live Resin') == 'concentrate'

    def test_live_resin(self):
        """Test live resin subtype normalization."""
        assert RawGardenCollector._normalize_subtype('Live Resin') == 'concentrate'

    def test_diamonds(self):
        """Test diamonds subtype normalization."""
        assert RawGardenCollector._normalize_subtype('RG Diamonds') == 'concentrate'

    def test_flower(self):
        """Test flower subtype normalization."""
        assert RawGardenCollector._normalize_subtype('Flower') == 'flower'

    def test_prerolls(self):
        """Test pre-rolls subtype normalization."""
        assert RawGardenCollector._normalize_subtype('Pre-Rolls') == 'preroll'

    def test_gummies(self):
        """Test gummies subtype normalization."""
        assert RawGardenCollector._normalize_subtype('Gummies') == 'edible'

    def test_empty_string(self):
        """Test empty string returns None."""
        assert RawGardenCollector._normalize_subtype('') is None

    def test_none(self):
        """Test None returns None."""
        assert RawGardenCollector._normalize_subtype(None) is None

    def test_case_insensitive(self):
        """Test that normalization is case-insensitive."""
        assert RawGardenCollector._normalize_subtype('FLOWER') == 'flower'
        assert RawGardenCollector._normalize_subtype('pre-rolls') == 'preroll'


class TestParseDate:
    """Tests for _parse_date static method."""

    def test_iso_format(self):
        """Test parsing ISO date string."""
        result = RawGardenCollector._parse_date('2024-01-15')
        assert result == '2024-01-15T00:00:00'

    def test_natural_language_date(self):
        """Test parsing natural language date."""
        result = RawGardenCollector._parse_date('January 15, 2024')
        assert result == '2024-01-15T00:00:00'

    def test_us_date_format(self):
        """Test parsing US date format."""
        result = RawGardenCollector._parse_date('01/15/2024')
        assert result == '2024-01-15T00:00:00'

    def test_empty_string(self):
        """Test parsing empty string."""
        assert RawGardenCollector._parse_date('') is None

    def test_none(self):
        """Test parsing None."""
        assert RawGardenCollector._parse_date(None) is None

    def test_invalid_string(self):
        """Test parsing invalid date string."""
        assert RawGardenCollector._parse_date('not a date') is None

    def test_truncates_to_seconds(self):
        """Test that result is truncated to seconds (no microseconds)."""
        result = RawGardenCollector._parse_date('2024-01-15 13:45:30.123456')
        assert result == '2024-01-15T13:45:30'
        assert '.' not in result


# =========================================================================
# Data Conversion Tests
# =========================================================================

class TestConvertToLabResults:
    """Tests for _convert_to_lab_results method."""

    def test_convert_single_product(self, raw_garden_collector, sample_raw_product):
        """Test converting a single product to LabResult."""
        results = raw_garden_collector._convert_to_lab_results([sample_raw_product])

        assert len(results) == 1
        result = results[0]

        assert result['state'] == 'ca'
        assert result['source'] == 'raw_garden'
        assert result['product_name'] == 'Wave Rider'

    def test_convert_includes_producer_metadata(self, raw_garden_collector, sample_raw_product):
        """Test that producer metadata is included."""
        results = raw_garden_collector._convert_to_lab_results([sample_raw_product])
        result = results[0]

        assert result['producer'] == 'Raw Garden'
        assert result['producer_license_number'] == 'CDPH-10003156'
        assert result['producer_state'] == 'CA'
        assert result['producer_county'] == 'Santa Barbara'

    def test_convert_includes_distributor(self, raw_garden_collector, sample_raw_product):
        """Test that distributor info is included."""
        results = raw_garden_collector._convert_to_lab_results([sample_raw_product])
        result = results[0]

        assert result['distributor'] == 'Central Coast Agriculture, Inc.'
        assert result['distributor_license_number'] == 'C11-0000496-LIC'

    def test_convert_normalizes_product_type(self, raw_garden_collector, sample_raw_product):
        """Test that product type is normalized from subtype."""
        results = raw_garden_collector._convert_to_lab_results([sample_raw_product])
        result = results[0]

        # 'Refined Live Resin Cartridges' → 'vape'
        assert result['product_type'] == 'vape'

    def test_convert_preserves_subtype(self, raw_garden_collector, sample_raw_product):
        """Test that the original subtype is preserved."""
        results = raw_garden_collector._convert_to_lab_results([sample_raw_product])
        result = results[0]

        assert result['product_subtype'] == 'Refined Live Resin Cartridges'

    def test_convert_includes_coa_url(self, raw_garden_collector, sample_raw_product):
        """Test that COA URL is included."""
        results = raw_garden_collector._convert_to_lab_results([sample_raw_product])
        result = results[0]

        assert result['coa_url'] == sample_raw_product['coa_url']
        assert result['coa_url'].endswith('.pdf')

    def test_convert_includes_timestamps(self, raw_garden_collector, sample_raw_product):
        """Test that timestamps are included."""
        results = raw_garden_collector._convert_to_lab_results([sample_raw_product])
        result = results[0]

        assert 'created_at' in result
        assert 'updated_at' in result

    def test_convert_generates_unique_ids(self, raw_garden_collector, sample_raw_product):
        """Test that each result gets a unique ID."""
        product2 = sample_raw_product.copy()
        product2['product_name'] = 'Strawberry Cough'
        product2['coa_url'] = 'https://rawgarden.farm/another.pdf'

        results = raw_garden_collector._convert_to_lab_results(
            [sample_raw_product, product2]
        )

        assert results[0]['id'] != results[1]['id']

    def test_convert_empty_list(self, raw_garden_collector):
        """Test converting an empty list."""
        results = raw_garden_collector._convert_to_lab_results([])
        assert results == []

    def test_convert_includes_date_retail(self, raw_garden_collector, sample_raw_product):
        """Test that date_retail is included."""
        results = raw_garden_collector._convert_to_lab_results([sample_raw_product])
        result = results[0]

        assert result.get('date_retail') == '2024-07-28T00:00:00'


# =========================================================================
# Download Organization Tests
# =========================================================================

class TestDownloadOrganization:
    """Tests for the download directory organization logic."""

    def test_subtype_to_directory_mapping(self):
        """Test that subtypes map to expected directory names."""
        assert _kebab_case('Refined Live Resin Cartridges') == 'refined-live-resin-cartridges'
        assert _kebab_case('RG Diamonds') == 'rg-diamonds'
        assert _kebab_case('Pre-Rolls') == 'pre-rolls'
        assert _kebab_case('Flower') == 'flower'


# =========================================================================
# Integration Tests
# =========================================================================

@pytest.mark.integration
class TestRawGardenIntegration:
    """Integration tests requiring network access to rawgarden.farm."""

    @pytest.fixture
    def integration_collector(self, temp_data_dir, temp_pdf_dir, temp_cache_path, temp_log_dir):
        """Pre-configured collector for integration tests."""
        return RawGardenCollector(
            data_dir=str(temp_data_dir),
            pdf_dir=str(temp_pdf_dir),
            cache_path=temp_cache_path,
            log_dir=str(temp_log_dir),
            verbose=True,
        )

    @pytest.mark.slow
    def test_get_products(self, integration_collector):
        """Test fetching products from the live site."""
        products = integration_collector._get_products()

        assert len(products) > 0, "No products found — site may have changed"

        first = products[0]
        assert 'product_name' in first
        assert 'coa_url' in first
        assert 'product_subtype' in first
        assert first['product_name'], "Product name should not be empty"

    @pytest.mark.slow
    def test_products_have_pdf_urls(self, integration_collector):
        """Test that all products have PDF URLs."""
        products = integration_collector._get_products()
        pdf_products = [p for p in products if p.get('coa_url', '').endswith('.pdf')]

        assert len(pdf_products) == len(products), (
            f"Not all products have PDF URLs: {len(pdf_products)}/{len(products)}"
        )

    @pytest.mark.slow
    def test_products_have_subtypes(self, integration_collector):
        """Test that products have subtype categories."""
        products = integration_collector._get_products()
        subtypes = set(p.get('product_subtype') for p in products if p.get('product_subtype'))

        assert len(subtypes) > 0, "No product subtypes found"

    @pytest.mark.slow
    def test_products_have_dates(self, integration_collector):
        """Test that products have retail dates."""
        products = integration_collector._get_products()
        with_dates = [p for p in products if p.get('date_retail')]

        # Most products should have dates.
        ratio = len(with_dates) / len(products) if products else 0
        assert ratio > 0.5, f"Only {ratio:.0%} of products have dates"

    @pytest.mark.slow
    def test_full_collection_smoke(self, integration_collector):
        """Smoke test: run full collection without downloading PDFs."""
        results = integration_collector.get_results(
            download_pdfs=False,
            save_intermediate=False,
        )

        assert len(results) > 0, "No results collected"
        assert 'state' in results.columns
        assert 'source' in results.columns
        assert results['state'].iloc[0] == 'ca'
        assert results['source'].iloc[0] == 'raw_garden'
        assert results['producer'].iloc[0] == 'Raw Garden'


# =========================================================================
# Inline Module Tests
# =========================================================================

class TestUnitTestsFromMain:
    """Run the unit tests defined in the module's __main__ block."""

    def test_run_unit_tests(self):
        """Test that the module's inline unit tests pass."""
        from algorithms.get_results_ca_rawgarden import run_unit_tests

        success = run_unit_tests()
        assert success is True


# === Run tests directly ===
if __name__ == '__main__':
    pytest.main([__file__, '-v', '-m', 'not integration'])