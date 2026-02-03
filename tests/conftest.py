"""
Cannabis Results | Pytest Configuration
Copyright (c) 2024-2026 Cannlytics

Description:
    Shared fixtures and configuration for pytest.
    
    IMPORTANT: This file MUST be located at tests/conftest.py
    for pytest to discover the fixtures.
    
    Fixtures defined here are automatically available to all tests
    in the tests/ directory and its subdirectories.
"""
# Standard imports:
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile

# External imports:
import pytest

# Ensure the project root is in the path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# === Configuration ===

def pytest_configure(config):
    """Configure pytest markers."""
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests (require network)"
    )
    config.addinivalue_line(
        "markers", "slow: marks tests as slow running"
    )
    config.addinivalue_line(
        "markers", "selenium: marks tests that require Selenium"
    )


# === Fixtures: Temporary Directories ===

@pytest.fixture
def temp_dir(tmp_path):
    """Provide a temporary directory that is cleaned up after the test.
    
    Uses pytest's built-in tmp_path fixture for cross-platform compatibility.
    """
    return tmp_path


@pytest.fixture
def temp_data_dir(tmp_path):
    """Provide a temporary data directory structure."""
    data_dir = tmp_path / 'data' / 'california' / 'results'
    data_dir.mkdir(parents=True)
    return data_dir


@pytest.fixture
def temp_pdf_dir(tmp_path):
    """Provide a temporary PDF directory."""
    pdf_dir = tmp_path / 'pdfs'
    pdf_dir.mkdir(parents=True)
    return pdf_dir


@pytest.fixture
def temp_cache_path(tmp_path):
    """Provide a temporary cache file path."""
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir(parents=True, exist_ok=True)
    return str(cache_dir / 'cache.jsonl')


@pytest.fixture
def temp_log_dir(tmp_path):
    """Provide a temporary log directory."""
    log_dir = tmp_path / 'logs'
    log_dir.mkdir(parents=True)
    return log_dir


# === Fixtures: Sample Data ===

@pytest.fixture
def sample_product():
    """Sample product data as returned by Flower Company scraper."""
    return {
        'product_name': 'Blue Dream',
        'producer': 'Test Farm',
        'category': 'fire-flower',
        'product_url': 'https://flowercompany.com/product/test-blue-dream',
        'total_thc': 24.5,
        'total_thc_units': 'percent',
        'price': 50.0,
        'discount_price': 45.0,
        'discount': 5.0,
        'amount': 3.5,
        'classification': 'Hybrid',
        'indica_percentage': 0.5,
        'sativa_percentage': 0.5,
    }


@pytest.fixture
def sample_augmented_product(sample_product):
    """Sample augmented product data with all details."""
    product = sample_product.copy()
    product.update({
        'product_id': 'abc123def456789a',
        'product_type': 'Flower',
        'product_subtype': 'Indoor',
        'product_description': 'A classic sativa-dominant hybrid.',
        'product_contents': 'Cannabis Flower',
        'predicted_effects': 'Relaxed, Happy, Euphoric',
        'predicted_aromas': ['Blueberry', 'Sweet', 'Berry'],
        'lineage': 'Blueberry x Haze',
        'lab_results_url': 'https://flowercompany.com/product/test.pdf',
        'image_url': 'https://flowercompany.com/images/test.jpg',
        'distributor': 'Test Distributor',
        'distributor_license_number': 'C11-0000001-LIC',
        'total_cbd': 0.5,
    })
    return product


@pytest.fixture
def sample_lab_result():
    """Sample LabResult data dictionary."""
    return {
        'id': 'abc123def456789a',
        'sample_id': 'SAMPLE-001',
        'product_name': 'Blue Dream',
        'product_type': 'flower',
        'strain_name': 'Blue Dream',
        'producer': 'Test Farm',
        'producer_license_number': 'CCL20-0000001',
        'lab': 'SC Labs',
        'lab_license_number': 'C8-0000001-LIC',
        'date_tested': '2026-01-15',
        'total_thc': 24.5,
        'total_cbd': 0.5,
        'total_terpenes': 2.3,
        'beta_myrcene': 0.8,
        'd_limonene': 0.5,
        'beta_caryophyllene': 0.4,
        'pesticides_status': 'pass',
        'heavy_metals_status': 'pass',
        'microbials_status': 'pass',
        'overall_status': 'pass',
        'state': 'ca',
        'source': 'flower_company',
        'coa_url': 'https://example.com/coa.pdf',
    }


@pytest.fixture
def sample_lab_results_list(sample_lab_result):
    """List of sample lab results for testing batch operations."""
    results = []
    for i in range(5):
        result = sample_lab_result.copy()
        result['id'] = f'id{i:015d}'
        result['product_name'] = f'Product {i}'
        result['total_thc'] = 20.0 + i
        results.append(result)
    return results


# === Fixtures: Mock Objects ===

@pytest.fixture
def mock_cache():
    """Mock cache object for testing without file I/O."""
    class MockCache:
        def __init__(self):
            self.data = {}
        
        def get(self, key):
            return self.data.get(key)
        
        def set(self, key, value):
            self.data[key] = value
        
        def hash_url(self, url):
            import hashlib
            return hashlib.md5(url.encode()).hexdigest()
    
    return MockCache()


@pytest.fixture
def mock_logger():
    """Mock logger for testing without file output."""
    import logging
    
    logger = logging.getLogger('test_logger')
    logger.setLevel(logging.DEBUG)
    
    # Add null handler to suppress output
    if not logger.handlers:
        handler = logging.NullHandler()
        logger.addHandler(handler)
    
    return logger


# === Fixtures: Configuration ===

@pytest.fixture
def config_paths(tmp_path):
    """Override PATHS configuration for testing."""
    try:
        from config.results_config import PathConfig
        
        return PathConfig(
            base_dir=tmp_path / 'data',
            cache_dir=tmp_path / '.cache',
            log_dir=tmp_path / '.logs',
        )
    except ImportError:
        pytest.skip("Config not available")


# === Fixtures: Collector Instances ===

@pytest.fixture
def flower_company_collector(temp_data_dir, temp_pdf_dir, temp_cache_path, temp_log_dir):
    """Pre-configured FlowerCompanyCollector for testing."""
    try:
        from algorithms.get_results_ca_flower_co import FlowerCompanyCollector
    except ImportError:
        pytest.skip("FlowerCompanyCollector not available")
    
    collector = FlowerCompanyCollector(
        data_dir=str(temp_data_dir),
        pdf_dir=str(temp_pdf_dir),
        cache_path=temp_cache_path,
        log_dir=str(temp_log_dir),
        verbose=False,
    )
    
    yield collector
    
    # Cleanup
    if collector.driver:
        collector._quit_driver()


# === Helper Functions ===

def assert_valid_lab_result(result: dict) -> None:
    """Assert that a result dictionary has required fields.
    
    Args:
        result: Dictionary to validate.
        
    Raises:
        AssertionError: If validation fails.
    """
    # Required fields
    assert 'state' in result, "Missing 'state' field"
    assert result['state'] in ['ca', 'az', 'ct', 'fl', 'ma', 'mi', 'nv', 'ny', 'or', 'wa'], \
        f"Invalid state: {result['state']}"
    
    # Optional but expected fields
    expected_fields = [
        'id', 'product_name', 'product_type', 'producer',
        'total_thc', 'source', 'created_at',
    ]
    for field in expected_fields:
        if field not in result:
            print(f"Warning: Missing expected field '{field}'")
    
    # Validate numeric ranges if present
    if result.get('total_thc') is not None:
        assert 0 <= result['total_thc'] <= 100, \
            f"total_thc out of range: {result['total_thc']}"
    
    if result.get('total_cbd') is not None:
        assert 0 <= result['total_cbd'] <= 100, \
            f"total_cbd out of range: {result['total_cbd']}"


def load_test_fixture(name: str) -> dict:
    """Load a JSON test fixture.
    
    Args:
        name: Fixture name (without .json extension).
        
    Returns:
        Parsed JSON data.
    """
    fixture_path = Path(__file__).parent / 'fixtures' / f'{name}.json'
    if not fixture_path.exists():
        pytest.skip(f"Fixture {name}.json not found")
    with open(fixture_path) as f:
        return json.load(f)