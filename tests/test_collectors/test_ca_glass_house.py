"""
Cannabis Results | CA Glass House Farms Collector Tests
Copyright (c) 2024-2026 Cannlytics

Description:
    Tests for the California Glass House Farms collector.

    Run unit tests (no network):
        pytest tests/test_collectors/test_ca_glass_house.py -v -m "not integration"

    Run integration tests (requires network):
        pytest tests/test_collectors/test_ca_glass_house.py -v -m integration

    Run all tests:
        pytest tests/test_collectors/test_ca_glass_house.py -v
"""
# Standard imports:
from datetime import datetime
from pathlib import Path
import sys

# External imports:
import pytest
from bs4 import BeautifulSoup

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Internal imports:
from algorithms.get_results_ca_glass_house import (
    GlassHouseFarmsCollector,
    BASE_URL,
    GLASS_HOUSE_FARMS,
    STRAIN_TYPES,
)


# =========================================================================
# Constants Tests
# =========================================================================

class TestGlassHouseConstants:
    """Tests for module-level constants."""

    def test_base_url(self):
        """Test base URL is correctly defined."""
        assert BASE_URL == 'https://glasshousefarms.org/strains/'
        assert BASE_URL.startswith('https://')

    def test_producer_metadata_complete(self):
        """Test that producer metadata has all required fields."""
        required_fields = [
            'business_dba_name',
            'business_website',
            'producer_license_number',
            'producer_latitude',
            'producer_longitude',
            'producer_street_address',
            'producer_city',
            'producer_county',
            'producer_state',
        ]
        for field in required_fields:
            assert field in GLASS_HOUSE_FARMS, f"Missing producer field: {field}"

    def test_producer_license_number(self):
        """Test that the license number is correctly formatted."""
        license_num = GLASS_HOUSE_FARMS['producer_license_number']
        assert license_num == 'CCL18-0000512'
        assert license_num.startswith('CCL')

    def test_producer_state(self):
        """Test that the producer state is California."""
        assert GLASS_HOUSE_FARMS['producer_state'] == 'CA'

    def test_producer_coordinates_valid(self):
        """Test that producer coordinates are valid lat/lon."""
        lat = GLASS_HOUSE_FARMS['producer_latitude']
        lon = GLASS_HOUSE_FARMS['producer_longitude']

        # Carpinteria, CA is roughly 34.4°N, 119.5°W
        assert 34.0 <= lat <= 35.0, f"Latitude out of range: {lat}"
        assert -120.0 <= lon <= -119.0, f"Longitude out of range: {lon}"

    def test_strain_types_complete(self):
        """Test that all expected strain types are defined."""
        expected = ['sativa', 'sativaDominant', 'hybrid', 'indica', 'indicaDominant', 'cbd']
        for strain_type in expected:
            assert strain_type in STRAIN_TYPES, f"Missing strain type: {strain_type}"

    def test_strain_types_have_required_fields(self):
        """Test that each strain type has classification and percentages."""
        for name, info in STRAIN_TYPES.items():
            assert 'classification' in info, f"{name} missing 'classification'"
            assert 'sativa_percentage' in info, f"{name} missing 'sativa_percentage'"
            assert 'indica_percentage' in info, f"{name} missing 'indica_percentage'"

    def test_strain_type_percentages_valid(self):
        """Test that indica/sativa percentages are valid (0-1, sum <= 1)."""
        for name, info in STRAIN_TYPES.items():
            indica = info['indica_percentage']
            sativa = info['sativa_percentage']
            assert 0 <= indica <= 1, f"{name}: indica={indica} out of range"
            assert 0 <= sativa <= 1, f"{name}: sativa={sativa} out of range"
            assert indica + sativa <= 1.01, f"{name}: percentages sum > 1"

    def test_strain_type_extremes(self):
        """Test specific strain type values."""
        assert STRAIN_TYPES['sativa']['sativa_percentage'] == 1.0
        assert STRAIN_TYPES['sativa']['indica_percentage'] == 0.0
        assert STRAIN_TYPES['indica']['sativa_percentage'] == 0.0
        assert STRAIN_TYPES['indica']['indica_percentage'] == 1.0
        assert STRAIN_TYPES['hybrid']['sativa_percentage'] == 0.5
        assert STRAIN_TYPES['hybrid']['indica_percentage'] == 0.5


# =========================================================================
# Collector Initialization Tests
# =========================================================================

class TestGlassHouseCollectorInit:
    """Tests for collector initialization."""

    def test_init_default(self, temp_data_dir, temp_pdf_dir, temp_cache_path, temp_log_dir):
        """Test default initialization."""
        collector = GlassHouseFarmsCollector(
            data_dir=str(temp_data_dir),
            pdf_dir=str(temp_pdf_dir),
            cache_path=temp_cache_path,
            log_dir=str(temp_log_dir),
            verbose=False,
        )

        assert collector.state == 'ca'
        assert collector.source == 'glass_house'
        assert collector.pause_time == 3.0  # Source-specific default

    def test_init_custom_pause(self, temp_data_dir, temp_pdf_dir, temp_cache_path, temp_log_dir):
        """Test initialization with custom pause time."""
        collector = GlassHouseFarmsCollector(
            data_dir=str(temp_data_dir),
            pdf_dir=str(temp_pdf_dir),
            cache_path=temp_cache_path,
            log_dir=str(temp_log_dir),
            pause_time=10.0,
            verbose=False,
        )

        assert collector.pause_time == 10.0

    def test_init_creates_directories(self, tmp_path):
        """Test that initialization creates necessary directories."""
        data_dir = tmp_path / 'test_data'
        pdf_dir = tmp_path / 'test_pdfs'
        log_dir = tmp_path / 'test_logs'

        collector = GlassHouseFarmsCollector(
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
        with GlassHouseFarmsCollector(
            data_dir=str(temp_data_dir),
            pdf_dir=str(temp_pdf_dir),
            cache_path=temp_cache_path,
            log_dir=str(temp_log_dir),
            verbose=False,
        ) as collector:
            assert collector is not None
            assert collector.state == 'ca'
            assert collector.source == 'glass_house'


# =========================================================================
# Static Helper Tests
# =========================================================================

class TestGenerateResultId:
    """Tests for _generate_result_id static method."""

    def test_id_length(self):
        """Test that generated ID is 16 characters."""
        result_id = GlassHouseFarmsCollector._generate_result_id(
            'Blue Dream', 'https://example.com/coa.pdf'
        )
        assert len(result_id) == 16

    def test_id_is_hex_string(self):
        """Test that ID is a valid hex string."""
        result_id = GlassHouseFarmsCollector._generate_result_id(
            'Blue Dream', 'https://example.com/coa.pdf'
        )
        int(result_id, 16)  # Raises ValueError if not valid hex

    def test_id_deterministic(self):
        """Test that same inputs produce same ID."""
        id1 = GlassHouseFarmsCollector._generate_result_id('Blue Dream', 'https://example.com/a.pdf')
        id2 = GlassHouseFarmsCollector._generate_result_id('Blue Dream', 'https://example.com/a.pdf')
        assert id1 == id2

    def test_id_unique_by_strain(self):
        """Test that different strains produce different IDs."""
        id1 = GlassHouseFarmsCollector._generate_result_id('Blue Dream', 'https://example.com/a.pdf')
        id2 = GlassHouseFarmsCollector._generate_result_id('OG Kush', 'https://example.com/a.pdf')
        assert id1 != id2

    def test_id_unique_by_coa_url(self):
        """Test that different COA URLs produce different IDs."""
        id1 = GlassHouseFarmsCollector._generate_result_id('Blue Dream', 'https://example.com/a.pdf')
        id2 = GlassHouseFarmsCollector._generate_result_id('Blue Dream', 'https://example.com/b.pdf')
        assert id1 != id2

    def test_id_handles_none(self):
        """Test ID generation with None values."""
        result_id = GlassHouseFarmsCollector._generate_result_id(None, None)
        assert len(result_id) == 16
        assert isinstance(result_id, str)

    def test_id_handles_empty_strings(self):
        """Test ID generation with empty strings."""
        result_id = GlassHouseFarmsCollector._generate_result_id('', '')
        assert len(result_id) == 16


class TestExtractStrainType:
    """Tests for _extract_strain_type static method."""

    def _make_wave(self, classes: str) -> object:
        """Create a mock BeautifulSoup wave element.

        Args:
            classes: Space-separated CSS class string.

        Returns:
            BeautifulSoup Tag element.
        """
        html = f'<div class="{classes}"></div>'
        soup = BeautifulSoup(html, 'html.parser')
        return soup.find('div')

    def test_sativa(self):
        """Test sativa classification."""
        wave = self._make_wave('wave sativa')
        result = GlassHouseFarmsCollector._extract_strain_type(wave)
        assert result['classification'] == 'Sativa'
        assert result['sativa_percentage'] == 1.0
        assert result['indica_percentage'] == 0.0

    def test_indica(self):
        """Test indica classification."""
        wave = self._make_wave('wave indica')
        result = GlassHouseFarmsCollector._extract_strain_type(wave)
        assert result['classification'] == 'Indica'
        assert result['sativa_percentage'] == 0.0
        assert result['indica_percentage'] == 1.0

    def test_hybrid(self):
        """Test hybrid classification."""
        wave = self._make_wave('wave hybrid')
        result = GlassHouseFarmsCollector._extract_strain_type(wave)
        assert result['classification'] == 'Hybrid'
        assert result['sativa_percentage'] == 0.5
        assert result['indica_percentage'] == 0.5

    def test_sativa_dominant(self):
        """Test sativa-dominant classification."""
        wave = self._make_wave('wave sativaDominant')
        result = GlassHouseFarmsCollector._extract_strain_type(wave)
        assert result['classification'] == 'S-Hybrid'
        assert result['sativa_percentage'] == 0.75
        assert result['indica_percentage'] == 0.25

    def test_indica_dominant(self):
        """Test indica-dominant classification."""
        wave = self._make_wave('wave indicaDominant')
        result = GlassHouseFarmsCollector._extract_strain_type(wave)
        assert result['classification'] == 'I-Hybrid'
        assert result['sativa_percentage'] == 0.25
        assert result['indica_percentage'] == 0.75

    def test_cbd(self):
        """Test CBD classification."""
        wave = self._make_wave('wave cbd')
        result = GlassHouseFarmsCollector._extract_strain_type(wave)
        assert result['classification'] == 'CBD'
        assert result['sativa_percentage'] == 0.0
        assert result['indica_percentage'] == 0.0

    def test_unknown_class(self):
        """Test unknown wave class returns None values."""
        wave = self._make_wave('wave unknown')
        result = GlassHouseFarmsCollector._extract_strain_type(wave)
        assert result['classification'] is None
        assert result['indica_percentage'] is None
        assert result['sativa_percentage'] is None

    def test_none_input(self):
        """Test None wave element returns None values."""
        result = GlassHouseFarmsCollector._extract_strain_type(None)
        assert result['classification'] is None
        assert result['indica_percentage'] is None
        assert result['sativa_percentage'] is None

    def test_wave_only_class(self):
        """Test wave element with only 'wave' class (no type)."""
        wave = self._make_wave('wave')
        result = GlassHouseFarmsCollector._extract_strain_type(wave)
        assert result['classification'] is None

    def test_multiple_classes(self):
        """Test wave element with multiple extra classes."""
        wave = self._make_wave('wave sativa extra-class another')
        result = GlassHouseFarmsCollector._extract_strain_type(wave)
        assert result['classification'] == 'Sativa'


# =========================================================================
# Data Conversion Tests
# =========================================================================

class TestConvertToLabResults:
    """Tests for _convert_to_lab_results method."""

    @pytest.fixture
    def glass_house_collector(self, temp_data_dir, temp_pdf_dir, temp_cache_path, temp_log_dir):
        """Pre-configured GlassHouseFarmsCollector for testing."""
        return GlassHouseFarmsCollector(
            data_dir=str(temp_data_dir),
            pdf_dir=str(temp_pdf_dir),
            cache_path=temp_cache_path,
            log_dir=str(temp_log_dir),
            verbose=False,
        )

    @pytest.fixture
    def sample_raw_result(self):
        """Sample raw result as produced by the collection pipeline."""
        return {
            'strain_name': 'Blue Dream',
            'strain_type': 'Sativa Dominant',
            'strain_url': 'https://glasshousefarms.org/strains/blue-dream/',
            'strain_id': 'blue-dream',
            'image_url': 'https://glasshousefarms.org/images/blue-dream.jpg',
            'classification': 'S-Hybrid',
            'indica_percentage': 0.25,
            'sativa_percentage': 0.75,
            'lineage': ['Blueberry', 'Haze'],
            'coa_url': 'https://glasshousefarms.org/coas/blue-dream-batch-1.pdf',
            'lab_result_id': 'blue-dream-batch-1',
        }

    def test_convert_single_result(self, glass_house_collector, sample_raw_result):
        """Test converting a single raw result to LabResult."""
        results = glass_house_collector._convert_to_lab_results([sample_raw_result])

        assert len(results) == 1
        result = results[0]

        assert result['state'] == 'ca'
        assert result['source'] == 'glass_house'
        assert result['product_name'] == 'Blue Dream'
        assert result['strain_name'] == 'Blue Dream'

    def test_convert_includes_producer_metadata(self, glass_house_collector, sample_raw_result):
        """Test that producer metadata is included."""
        results = glass_house_collector._convert_to_lab_results([sample_raw_result])
        result = results[0]

        assert result['producer'] == 'Glass House Farms'
        assert result['producer_license_number'] == 'CCL18-0000512'
        assert result['producer_state'] == 'CA'

    def test_convert_includes_classification(self, glass_house_collector, sample_raw_result):
        """Test that classification data is included."""
        results = glass_house_collector._convert_to_lab_results([sample_raw_result])
        result = results[0]

        assert result['classification'] == 'S-Hybrid'
        assert result['indica_percentage'] == 0.25
        assert result['sativa_percentage'] == 0.75

    def test_convert_includes_coa_url(self, glass_house_collector, sample_raw_result):
        """Test that COA URL is included."""
        results = glass_house_collector._convert_to_lab_results([sample_raw_result])
        result = results[0]

        expected_url = 'https://glasshousefarms.org/coas/blue-dream-batch-1.pdf'
        assert result['coa_url'] == expected_url

    def test_convert_includes_timestamps(self, glass_house_collector, sample_raw_result):
        """Test that timestamps are included."""
        results = glass_house_collector._convert_to_lab_results([sample_raw_result])
        result = results[0]

        assert 'created_at' in result
        assert 'updated_at' in result

    def test_convert_product_type_is_flower(self, glass_house_collector, sample_raw_result):
        """Test that product type is always 'flower' for Glass House."""
        results = glass_house_collector._convert_to_lab_results([sample_raw_result])
        result = results[0]

        assert result['product_type'] == 'flower'

    def test_convert_includes_images(self, glass_house_collector, sample_raw_result):
        """Test that image data is included when available."""
        results = glass_house_collector._convert_to_lab_results([sample_raw_result])
        result = results[0]

        assert len(result['images']) == 1
        assert result['images'][0]['url'] == sample_raw_result['image_url']

    def test_convert_no_image(self, glass_house_collector, sample_raw_result):
        """Test conversion when no image is available."""
        sample_raw_result['image_url'] = None
        results = glass_house_collector._convert_to_lab_results([sample_raw_result])
        result = results[0]

        assert result['images'] == []

    def test_convert_multiple_results(self, glass_house_collector, sample_raw_result):
        """Test converting multiple results."""
        result2 = sample_raw_result.copy()
        result2['strain_name'] = 'OG Kush'
        result2['lab_result_id'] = 'og-kush-batch-1'

        results = glass_house_collector._convert_to_lab_results(
            [sample_raw_result, result2]
        )

        assert len(results) == 2
        assert results[0]['product_name'] == 'Blue Dream'
        assert results[1]['product_name'] == 'OG Kush'

    def test_convert_empty_list(self, glass_house_collector):
        """Test converting an empty list."""
        results = glass_house_collector._convert_to_lab_results([])
        assert results == []

    def test_convert_null_coa_url(self, glass_house_collector, sample_raw_result):
        """Test conversion when COA URL is None (strain without COAs)."""
        sample_raw_result['coa_url'] = None
        results = glass_house_collector._convert_to_lab_results([sample_raw_result])
        result = results[0]

        assert result['coa_url'] is None
        assert result['product_name'] == 'Blue Dream'


# =========================================================================
# Strain Expansion Logic Tests
# =========================================================================

class TestStrainExpansion:
    """Tests for the strain × COA expansion logic in get_results."""

    def test_strain_with_multiple_coas_expands(self):
        """Test that a strain with multiple COAs produces multiple results."""
        strain = {
            'strain_name': 'Blue Dream',
            'coa_urls': [
                'https://example.com/coa1.pdf',
                'https://example.com/coa2.pdf',
                'https://example.com/coa3.pdf',
            ],
        }

        raw_results = []
        for coa_url in strain['coa_urls']:
            lab_result_id = coa_url.split('/')[-1].split('.')[0]
            result = {**strain, 'coa_url': coa_url, 'lab_result_id': lab_result_id}
            raw_results.append(result)

        assert len(raw_results) == 3
        assert raw_results[0]['lab_result_id'] == 'coa1'
        assert raw_results[1]['lab_result_id'] == 'coa2'
        assert raw_results[2]['lab_result_id'] == 'coa3'

    def test_strain_without_coas_still_recorded(self):
        """Test that strains without COAs still produce a record."""
        strain = {
            'strain_name': 'Mystery Strain',
            'coa_urls': [],
        }

        raw_results = []
        if not strain['coa_urls']:
            result = {**strain, 'coa_url': None, 'lab_result_id': 'generated-id'}
            raw_results.append(result)

        assert len(raw_results) == 1
        assert raw_results[0]['coa_url'] is None


# =========================================================================
# Integration Tests
# =========================================================================

@pytest.mark.integration
class TestGlassHouseIntegration:
    """Integration tests requiring network access to glasshousefarms.org."""

    @pytest.fixture
    def glass_house_collector(self, temp_data_dir, temp_pdf_dir, temp_cache_path, temp_log_dir):
        """Pre-configured collector for integration tests."""
        collector = GlassHouseFarmsCollector(
            data_dir=str(temp_data_dir),
            pdf_dir=str(temp_pdf_dir),
            cache_path=temp_cache_path,
            log_dir=str(temp_log_dir),
            verbose=True,
        )
        yield collector

    @pytest.mark.slow
    def test_get_strains(self, glass_house_collector):
        """Test fetching strains from the live site."""
        strains = glass_house_collector._get_strains()

        assert len(strains) > 0, "No strains found — site may have changed"

        # Validate first strain structure.
        first = strains[0]
        assert 'strain_name' in first
        assert 'strain_url' in first
        assert 'strain_id' in first
        assert first['strain_name'], "Strain name should not be empty"

    @pytest.mark.slow
    def test_strain_has_classification(self, glass_house_collector):
        """Test that strains have classification data."""
        strains = glass_house_collector._get_strains()

        # At least some strains should have classification.
        classified = [s for s in strains if s.get('classification') is not None]
        assert len(classified) > 0, "No strains have classification data"

    @pytest.mark.slow
    def test_get_strain_details(self, glass_house_collector):
        """Test fetching details for one strain."""
        strains = glass_house_collector._get_strains()

        # Find a strain with a URL.
        strain_with_url = next(
            (s for s in strains if s.get('strain_url')),
            None
        )
        assert strain_with_url is not None, "No strains have URLs"

        detailed = glass_house_collector._get_strain_details(strain_with_url.copy())

        # Should have lineage and coa_urls fields.
        assert 'lineage' in detailed
        assert 'coa_urls' in detailed
        assert isinstance(detailed['lineage'], list)
        assert isinstance(detailed['coa_urls'], list)

    @pytest.mark.slow
    def test_strain_detail_no_url(self, glass_house_collector):
        """Test that strain without URL returns empty lineage/COAs."""
        strain = {'strain_name': 'Test', 'strain_url': None}
        result = glass_house_collector._get_strain_details(strain)

        assert result['lineage'] == []
        assert result['coa_urls'] == []

    @pytest.mark.slow
    def test_full_collection_smoke(self, glass_house_collector):
        """Smoke test: run full collection without downloading PDFs.

        This tests the entire pipeline except PDF downloads.
        """
        results = glass_house_collector.get_results(
            download_pdfs=False,
            save_intermediate=False,
        )

        assert len(results) > 0, "No results collected"
        assert 'state' in results.columns
        assert 'source' in results.columns
        assert results['state'].iloc[0] == 'ca'
        assert results['source'].iloc[0] == 'glass_house'


# =========================================================================
# Inline Module Tests
# =========================================================================

class TestUnitTestsFromMain:
    """Run the unit tests defined in the module's __main__ block."""

    def test_run_unit_tests(self):
        """Test that the module's inline unit tests pass."""
        from algorithms.get_results_ca_glass_house import run_unit_tests

        success = run_unit_tests()
        assert success is True


# === Run tests directly ===
if __name__ == '__main__':
    pytest.main([__file__, '-v', '-m', 'not integration'])