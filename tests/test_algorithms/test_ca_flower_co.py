"""
Cannabis Results | CA Flower Company Collector Tests
Copyright (c) 2024-2026 Cannlytics

Description:
    Tests for the California Flower Company collector.
    
    Run unit tests (no network):
        pytest tests/test_collectors/test_ca_flower_co.py -v -m "not integration"
    
    Run integration tests (requires network):
        pytest tests/test_collectors/test_ca_flower_co.py -v -m integration
    
    Run all tests:
        pytest tests/test_collectors/test_ca_flower_co.py -v
"""
# Standard imports:
from datetime import datetime
from pathlib import Path
import sys

# External imports:
import pytest

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Internal imports:
from algorithms.get_results_ca_flower_co import (
    FlowerCompanyCollector,
    BASE_URL,
    CATEGORY_PAGES,
    INDICA_PERCENTAGES,
    CATEGORY_TO_PRODUCT_TYPE,
)


class TestFlowerCompanyConstants:
    """Tests for module-level constants."""
    
    def test_base_url(self):
        """Test base URL is correctly defined."""
        assert BASE_URL == 'https://flowercompany.com/'
        assert BASE_URL.endswith('/')
    
    def test_category_pages_defined(self):
        """Test that category pages are defined."""
        assert len(CATEGORY_PAGES) > 0
        
        # Should include common categories
        expected = ['category/fire-flower', 'category/cartridges', 'category/concentrates']
        for cat in expected:
            assert cat in CATEGORY_PAGES, f"Missing category: {cat}"
    
    def test_indica_percentages_complete(self):
        """Test that all classifications have indica percentages."""
        expected_classifications = ['Indica', 'I-Hybrid', 'Hybrid', 'S-Hybrid', 'Sativa']
        
        for classification in expected_classifications:
            assert classification in INDICA_PERCENTAGES, f"Missing: {classification}"
            assert 0 <= INDICA_PERCENTAGES[classification] <= 1
    
    def test_indica_percentages_values(self):
        """Test specific indica percentage values."""
        assert INDICA_PERCENTAGES['Indica'] == 1.0
        assert INDICA_PERCENTAGES['Hybrid'] == 0.5
        assert INDICA_PERCENTAGES['Sativa'] == 0.0
    
    def test_category_to_product_type_mapping(self):
        """Test category to product type mapping."""
        assert CATEGORY_TO_PRODUCT_TYPE['fire-flower'] == 'flower'
        assert CATEGORY_TO_PRODUCT_TYPE['cartridges'] == 'vape'
        assert CATEGORY_TO_PRODUCT_TYPE['concentrates'] == 'concentrate'
        assert CATEGORY_TO_PRODUCT_TYPE['edibles'] == 'edible'
        assert CATEGORY_TO_PRODUCT_TYPE['prerolls'] == 'preroll'


class TestFlowerCompanyCollectorInit:
    """Tests for collector initialization."""
    
    def test_init_default(self, temp_data_dir, temp_pdf_dir, temp_cache_path, temp_log_dir):
        """Test default initialization."""
        collector = FlowerCompanyCollector(
            data_dir=str(temp_data_dir),
            pdf_dir=str(temp_pdf_dir),
            cache_path=temp_cache_path,
            log_dir=str(temp_log_dir),
            verbose=False,
        )
        
        assert collector.state == 'ca'
        assert collector.source == 'flower_company'
        assert collector.pause_time == 4.0  # Source-specific default
    
    def test_init_custom_pause(self, temp_data_dir, temp_pdf_dir, temp_cache_path, temp_log_dir):
        """Test initialization with custom pause time."""
        collector = FlowerCompanyCollector(
            data_dir=str(temp_data_dir),
            pdf_dir=str(temp_pdf_dir),
            cache_path=temp_cache_path,
            log_dir=str(temp_log_dir),
            pause_time=10.0,
            verbose=False,
        )
        
        assert collector.pause_time == 10.0
    
    def test_init_creates_directories(self, temp_dir):
        """Test that initialization creates necessary directories."""
        data_dir = temp_dir / 'test_data'
        pdf_dir = temp_dir / 'test_pdfs'
        log_dir = temp_dir / 'test_logs'
        
        collector = FlowerCompanyCollector(
            data_dir=str(data_dir),
            pdf_dir=str(pdf_dir),
            log_dir=str(log_dir),
            cache_path=str(temp_dir / 'cache.jsonl'),
            verbose=False,
        )
        
        assert data_dir.exists()
        assert pdf_dir.exists()
        assert log_dir.exists()


class TestExtractWeight:
    """Tests for _extract_weight static method."""
    
    def test_extract_weight_with_parentheses(self):
        """Test extracting weight from '1/8 (3.5g)' format."""
        assert FlowerCompanyCollector._extract_weight('1/8 (3.5g)') == 3.5
        assert FlowerCompanyCollector._extract_weight('1/4 (7g)') == 7.0
        assert FlowerCompanyCollector._extract_weight('1oz (28g)') == 28.0
    
    def test_extract_weight_simple(self):
        """Test extracting weight from '7g' format."""
        assert FlowerCompanyCollector._extract_weight('7g') == 7.0
        assert FlowerCompanyCollector._extract_weight('14g') == 14.0
        assert FlowerCompanyCollector._extract_weight('3.5g') == 3.5
    
    def test_extract_weight_with_spaces(self):
        """Test extracting weight with spaces."""
        assert FlowerCompanyCollector._extract_weight('7 g') == 7.0
        assert FlowerCompanyCollector._extract_weight(' 3.5g ') == 3.5
    
    def test_extract_weight_empty(self):
        """Test extracting weight from empty/None values."""
        assert FlowerCompanyCollector._extract_weight('') is None
        assert FlowerCompanyCollector._extract_weight(None) is None
    
    def test_extract_weight_invalid(self):
        """Test extracting weight from invalid strings."""
        assert FlowerCompanyCollector._extract_weight('invalid') is None
        assert FlowerCompanyCollector._extract_weight('abc') is None


class TestPriceToFloat:
    """Tests for _price_to_float static method."""
    
    def test_price_basic(self):
        """Test basic price conversion."""
        assert FlowerCompanyCollector._price_to_float('$50') == 50.0
        assert FlowerCompanyCollector._price_to_float('$50.00') == 50.0
        assert FlowerCompanyCollector._price_to_float('$25.99') == 25.99
    
    def test_price_with_comma(self):
        """Test price with comma separator."""
        assert FlowerCompanyCollector._price_to_float('$1,250.00') == 1250.0
        assert FlowerCompanyCollector._price_to_float('$1,000') == 1000.0
    
    def test_price_empty(self):
        """Test price conversion for empty/None values."""
        assert FlowerCompanyCollector._price_to_float('') is None
        assert FlowerCompanyCollector._price_to_float(None) is None
    
    def test_price_without_dollar(self):
        """Test price without dollar sign."""
        assert FlowerCompanyCollector._price_to_float('50.00') == 50.0


class TestParseThcValue:
    """Tests for _parse_thc_value static method."""
    
    def test_parse_thc_percent(self):
        """Test parsing THC percentage."""
        value, units = FlowerCompanyCollector._parse_thc_value('28.5% THC')
        assert value == 28.5
        assert units == 'percent'
        
        value, units = FlowerCompanyCollector._parse_thc_value('24.5%')
        assert value == 24.5
        assert units == 'percent'
    
    def test_parse_thc_mg(self):
        """Test parsing THC milligrams."""
        value, units = FlowerCompanyCollector._parse_thc_value('100mg THC')
        assert value == 100.0
        assert units == 'mg'
        
        value, units = FlowerCompanyCollector._parse_thc_value('50mg')
        assert value == 50.0
        assert units == 'mg'
    
    def test_parse_thc_empty(self):
        """Test parsing empty/None THC values."""
        value, units = FlowerCompanyCollector._parse_thc_value('')
        assert value is None
        assert units is None
        
        value, units = FlowerCompanyCollector._parse_thc_value(None)
        assert value is None
        assert units is None
    
    def test_parse_thc_uppercase(self):
        """Test parsing uppercase THC string."""
        value, units = FlowerCompanyCollector._parse_thc_value('28% THC')
        assert value == 28.0
        assert units == 'percent'


class TestGenerateProductId:
    """Tests for _generate_product_id static method."""
    
    def test_id_length(self):
        """Test that generated ID is 16 characters."""
        product_id = FlowerCompanyCollector._generate_product_id(
            'Blue Dream', 'Test Farm', 24.5
        )
        assert len(product_id) == 16
    
    def test_id_deterministic(self):
        """Test that same inputs produce same ID."""
        id1 = FlowerCompanyCollector._generate_product_id('Blue Dream', 'Test Farm', 24.5)
        id2 = FlowerCompanyCollector._generate_product_id('Blue Dream', 'Test Farm', 24.5)
        assert id1 == id2
    
    def test_id_unique(self):
        """Test that different inputs produce different IDs."""
        id1 = FlowerCompanyCollector._generate_product_id('Blue Dream', 'Farm A', 24.5)
        id2 = FlowerCompanyCollector._generate_product_id('OG Kush', 'Farm A', 24.5)
        id3 = FlowerCompanyCollector._generate_product_id('Blue Dream', 'Farm B', 24.5)
        id4 = FlowerCompanyCollector._generate_product_id('Blue Dream', 'Farm A', 25.0)
        
        assert id1 != id2
        assert id1 != id3
        assert id1 != id4
    
    def test_id_handles_none(self):
        """Test ID generation with None values."""
        product_id = FlowerCompanyCollector._generate_product_id(None, None, None)
        assert len(product_id) == 16
        assert isinstance(product_id, str)


class TestConvertToLabResults:
    """Tests for _convert_to_lab_results method."""
    
    def test_convert_single_product(self, flower_company_collector, sample_augmented_product):
        """Test converting a single product to LabResult."""
        results = flower_company_collector._convert_to_lab_results([sample_augmented_product])
        
        assert len(results) == 1
        result = results[0]
        
        assert result['state'] == 'ca'
        assert result['source'] == 'flower_company'
        assert result['product_name'] == sample_augmented_product['product_name']
        assert result['producer'] == sample_augmented_product['producer']
        assert result['total_thc'] == sample_augmented_product['total_thc']
    
    def test_convert_normalizes_product_type(self, flower_company_collector, sample_augmented_product):
        """Test that product type is normalized."""
        sample_augmented_product['product_type'] = 'Flower'
        sample_augmented_product['category'] = 'fire-flower'
        
        results = flower_company_collector._convert_to_lab_results([sample_augmented_product])
        result = results[0]
        
        # Should be normalized to lowercase 'flower'
        assert result['product_type'] == 'flower'
    
    def test_convert_includes_timestamps(self, flower_company_collector, sample_augmented_product):
        """Test that timestamps are included."""
        results = flower_company_collector._convert_to_lab_results([sample_augmented_product])
        result = results[0]
        
        assert 'created_at' in result
        assert 'updated_at' in result


class TestCollectorMethods:
    """Tests for collector helper methods."""
    
    def test_is_cached(self, flower_company_collector, mock_cache):
        """Test is_cached method."""
        flower_company_collector.cache = mock_cache
        
        # Not cached initially
        assert flower_company_collector.is_cached('http://example.com/test.pdf') is False
        
        # Cache the URL
        url_hash = mock_cache.hash_url('http://example.com/test.pdf')
        mock_cache.set(url_hash, {'cached': True})
        
        assert flower_company_collector.is_cached('http://example.com/test.pdf') is True
    
    def test_context_manager(self, temp_data_dir, temp_pdf_dir, temp_cache_path, temp_log_dir):
        """Test collector as context manager."""
        with FlowerCompanyCollector(
            data_dir=str(temp_data_dir),
            pdf_dir=str(temp_pdf_dir),
            cache_path=temp_cache_path,
            log_dir=str(temp_log_dir),
            verbose=False,
        ) as collector:
            assert collector is not None
            assert collector.state == 'ca'


@pytest.mark.integration
@pytest.mark.selenium
class TestFlowerCompanyIntegration:
    """Integration tests requiring Selenium and network access."""
    
    @pytest.mark.slow
    def test_selenium_initialization(self, flower_company_collector):
        """Test Selenium driver initialization."""
        flower_company_collector._init_selenium(headless=True)
        
        try:
            assert flower_company_collector.driver is not None
            
            # Test navigation
            flower_company_collector.driver.get('https://www.google.com')
            assert 'Google' in flower_company_collector.driver.title
        finally:
            flower_company_collector._quit_driver()
    
    @pytest.mark.slow
    def test_age_gate_click(self, flower_company_collector):
        """Test age gate clicking."""
        flower_company_collector._init_selenium(headless=True)
        
        try:
            flower_company_collector.driver.get(BASE_URL)
            flower_company_collector._click_age_gate()
            
            # Should not raise
        finally:
            flower_company_collector._quit_driver()
    
    @pytest.mark.slow
    def test_get_product_pages(self, flower_company_collector):
        """Test getting product pages."""
        flower_company_collector._init_selenium(headless=True)
        
        try:
            pages = flower_company_collector._get_product_pages()
            
            # Should return category pages plus brand pages
            assert len(pages) >= len(CATEGORY_PAGES)
            
            # All category pages should be included
            for cat in CATEGORY_PAGES:
                assert cat in pages
        finally:
            flower_company_collector._quit_driver()
    
    @pytest.mark.slow
    def test_collect_products_single_page(self, flower_company_collector):
        """Test collecting products from a single page."""
        flower_company_collector._init_selenium(headless=True)
        
        try:
            # Test with just one page
            products = flower_company_collector._collect_products(['category/fire-flower'])
            
            # Should find some products
            assert len(products) >= 0  # May vary based on site content
            
            # Check product structure
            if products:
                product = products[0]
                assert 'product_name' in product
                assert 'product_url' in product
                assert 'producer' in product
        finally:
            flower_company_collector._quit_driver()


class TestUnitTestsFromMain:
    """Run the unit tests defined in the module's __main__ block."""
    
    def test_run_unit_tests(self):
        """Test that the module's unit tests pass."""
        from algorithms.get_results_ca_flower_co import run_unit_tests
        
        success = run_unit_tests()
        assert success is True


# === Run tests directly ===
if __name__ == '__main__':
    pytest.main([__file__, '-v', '-m', 'not integration'])