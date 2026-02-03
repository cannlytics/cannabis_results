"""
Cannabis Results | Configuration Tests
Copyright (c) 2024-2026 Cannlytics

Description:
    Tests for results_config.py including PathConfig, StateConfig,
    and configuration constants.
    
    Run:
        pytest tests/test_config.py -v
"""
# Standard imports:
from pathlib import Path
import sys

# External imports:
import pytest

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Internal imports:
from config.results_config import (
    PathConfig,
    StateConfig,
    STATES,
    API_CONFIG,
    PROCESSING_CONFIG,
    SOURCE_CONFIG,
    PRODUCT_TYPES,
    DATA_QUALITY,
    PATHS,
    get_collector_config,
)


class TestPathConfig:
    """Tests for PathConfig class."""
    
    def test_default_paths(self):
        """Test default path configuration."""
        config = PathConfig()
        
        assert config.base_dir is not None
        assert config.cache_dir is not None
        assert config.log_dir is not None
    
    def test_custom_paths(self, temp_dir):
        """Test custom path configuration."""
        config = PathConfig(
            base_dir=temp_dir / 'data',
            cache_dir=temp_dir / 'cache',
            log_dir=temp_dir / 'logs',
        )
        
        assert config.base_dir == temp_dir / 'data'
        assert config.cache_dir == temp_dir / 'cache'
        assert config.log_dir == temp_dir / 'logs'
    
    def test_state_dir(self):
        """Test state directory generation."""
        config = PathConfig()
        
        ca_dir = config.state_dir('ca')
        
        assert 'california' in str(ca_dir).lower()
        assert 'results' in str(ca_dir)
    
    def test_state_dir_case_insensitive(self):
        """Test that state code is case-insensitive."""
        config = PathConfig()
        
        ca_lower = config.state_dir('ca')
        ca_upper = config.state_dir('CA')
        
        assert ca_lower == ca_upper
    
    def test_pdf_dir(self):
        """Test PDF directory generation."""
        config = PathConfig()
        
        pdf_dir = config.pdf_dir('ca', 'flower-company')
        
        assert 'pdfs' in str(pdf_dir)
        assert 'flower-company' in str(pdf_dir)
    
    def test_pdf_dir_without_source(self):
        """Test PDF directory without source."""
        config = PathConfig()
        
        pdf_dir = config.pdf_dir('ca')
        
        assert 'pdfs' in str(pdf_dir)
    
    def test_datasets_dir(self):
        """Test datasets directory generation."""
        config = PathConfig()
        
        datasets_dir = config.datasets_dir('ca')
        
        assert 'datasets' in str(datasets_dir)
    
    def test_cache_path(self):
        """Test cache path generation."""
        config = PathConfig()
        
        cache_path = config.cache_path('results-ca-flower-company')
        
        assert 'results-ca-flower-company' in str(cache_path)
        assert str(cache_path).endswith('.jsonl')
    
    def test_ensure_dirs(self, temp_dir):
        """Test directory creation."""
        config = PathConfig(
            base_dir=temp_dir / 'data',
            cache_dir=temp_dir / 'cache',
            log_dir=temp_dir / 'logs',
        )
        
        config.ensure_dirs('ca', 'test-source')
        
        assert config.state_dir('ca').exists()
        assert config.pdf_dir('ca', 'test-source').exists()
        assert config.datasets_dir('ca').exists()
        assert config.cache_dir.exists()
        assert config.log_dir.exists()


class TestStateConfig:
    """Tests for StateConfig class."""
    
    def test_create_state_config(self):
        """Test creating a StateConfig."""
        config = StateConfig(
            code='ca',
            name='California',
            sources=['flower_company', 'sclabs'],
            active=True,
            prr_available=False,
            notes='Test state',
        )
        
        assert config.code == 'ca'
        assert config.name == 'California'
        assert len(config.sources) == 2
        assert config.active is True
    
    def test_state_config_defaults(self):
        """Test StateConfig default values."""
        config = StateConfig(code='ny', name='New York')
        
        assert config.sources == []
        assert config.active is True
        assert config.prr_available is False
        assert config.notes == ''


class TestStatesConfiguration:
    """Tests for STATES configuration."""
    
    def test_states_exist(self):
        """Test that expected states are configured."""
        expected_states = ['ak', 'az', 'ca', 'co', 'ct', 'fl', 'hi', 'ma', 'md', 'mi', 
                         'mo', 'ms', 'nj', 'nv', 'ny', 'oh', 'or', 'ri', 'ut', 'vt', 'wa']
        
        for state in expected_states:
            assert state in STATES, f"Missing state configuration: {state}"
    
    def test_california_config(self):
        """Test California state configuration."""
        ca = STATES['ca']
        
        assert ca.code == 'ca'
        assert ca.name == 'California'
        assert 'flower_company' in ca.sources
        assert ca.active is True
    
    def test_all_states_have_required_fields(self):
        """Test that all states have required fields."""
        for code, config in STATES.items():
            assert config.code == code, f"State {code} has mismatched code"
            assert config.name, f"State {code} missing name"
            assert isinstance(config.sources, list), f"State {code} sources not a list"
            assert isinstance(config.active, bool), f"State {code} active not boolean"
    
    def test_active_states_have_sources(self):
        """Test that active states have at least one source."""
        for code, config in STATES.items():
            if config.active:
                has_source = len(config.sources) > 0 or config.prr_available
                assert has_source, f"Active state {code} has no sources"


class TestAPIConfig:
    """Tests for API_CONFIG constants."""
    
    def test_api_config_exists(self):
        """Test that API_CONFIG is defined."""
        assert API_CONFIG is not None
        assert isinstance(API_CONFIG, dict)
    
    def test_rate_limit_defined(self):
        """Test rate limit configuration."""
        assert 'rate_limit_delay' in API_CONFIG
        assert API_CONFIG['rate_limit_delay'] > 0
    
    def test_retry_config_defined(self):
        """Test retry configuration."""
        assert 'max_retries' in API_CONFIG
        assert API_CONFIG['max_retries'] >= 1
        
        assert 'exponential_backoff_base' in API_CONFIG
        assert API_CONFIG['exponential_backoff_base'] > 1
    
    def test_timeout_defined(self):
        """Test timeout configuration."""
        assert 'timeout' in API_CONFIG
        assert API_CONFIG['timeout'] > 0


class TestSourceConfig:
    """Tests for SOURCE_CONFIG constants."""
    
    def test_source_config_exists(self):
        """Test that SOURCE_CONFIG is defined."""
        assert SOURCE_CONFIG is not None
        assert isinstance(SOURCE_CONFIG, dict)
    
    def test_flower_company_config(self):
        """Test Flower Company source configuration."""
        assert 'flower_company' in SOURCE_CONFIG
        
        fc = SOURCE_CONFIG['flower_company']
        assert 'base_url' in fc
        assert 'pause_time' in fc
        assert fc['base_url'].startswith('http')
    
    def test_all_sources_have_base_url(self):
        """Test that web-based sources have base URLs."""
        web_sources = ['flower_company', 'glass_house', 'raw_garden', 'curaleaf']
        
        for source in web_sources:
            if source in SOURCE_CONFIG:
                config = SOURCE_CONFIG[source]
                # Either base_url or alternative method
                has_url = 'base_url' in config or 'folder_id' in config
                assert has_url, f"Source {source} missing base_url"


class TestProductTypes:
    """Tests for PRODUCT_TYPES configuration."""
    
    def test_product_types_exist(self):
        """Test that PRODUCT_TYPES is defined."""
        assert PRODUCT_TYPES is not None
        assert isinstance(PRODUCT_TYPES, dict)
    
    def test_required_types_exist(self):
        """Test that required product types are defined."""
        required_types = ['flower', 'preroll', 'concentrate', 'vape', 'edible', 'tincture', 'topical']
        
        for ptype in required_types:
            assert ptype in PRODUCT_TYPES, f"Missing product type: {ptype}"
    
    def test_types_have_variations(self):
        """Test that product types have variation lists."""
        for ptype, variations in PRODUCT_TYPES.items():
            assert isinstance(variations, list), f"{ptype} variations not a list"
            assert len(variations) > 0, f"{ptype} has no variations"


class TestDataQuality:
    """Tests for DATA_QUALITY configuration."""
    
    def test_data_quality_exists(self):
        """Test that DATA_QUALITY is defined."""
        assert DATA_QUALITY is not None
        assert isinstance(DATA_QUALITY, dict)
    
    def test_completeness_threshold(self):
        """Test completeness threshold."""
        assert 'min_completeness' in DATA_QUALITY
        assert 0 <= DATA_QUALITY['min_completeness'] <= 1
    
    def test_accuracy_threshold(self):
        """Test accuracy threshold."""
        assert 'min_accuracy' in DATA_QUALITY
        assert 0 <= DATA_QUALITY['min_accuracy'] <= 1


class TestGetCollectorConfig:
    """Tests for get_collector_config function."""
    
    def test_get_collector_config_returns_dict(self):
        """Test that function returns a dictionary."""
        config = get_collector_config('ca', 'flower_company')
        
        assert isinstance(config, dict)
    
    def test_get_collector_config_has_state(self):
        """Test that config includes state info."""
        config = get_collector_config('ca', 'flower_company')
        
        assert 'state' in config
    
    def test_get_collector_config_has_source(self):
        """Test that config includes source info."""
        config = get_collector_config('ca', 'flower_company')
        
        assert 'source' in config
    
    def test_get_collector_config_has_paths(self):
        """Test that config includes path info."""
        config = get_collector_config('ca', 'flower_company')
        
        assert 'paths' in config
        assert 'data_dir' in config['paths']
        assert 'pdf_dir' in config['paths']
        assert 'cache_path' in config['paths']
    
    def test_get_collector_config_has_pause_time(self):
        """Test that config includes pause time."""
        config = get_collector_config('ca', 'flower_company')
        
        assert 'pause_time' in config
        assert config['pause_time'] > 0
    
    def test_get_collector_config_unknown_source(self):
        """Test config for unknown source."""
        config = get_collector_config('ca', 'unknown_source')
        
        # Should still return valid config with defaults
        assert isinstance(config, dict)
        assert 'pause_time' in config


class TestGlobalPaths:
    """Tests for global PATHS instance."""
    
    def test_paths_instance_exists(self):
        """Test that PATHS is instantiated."""
        assert PATHS is not None
        assert isinstance(PATHS, PathConfig)
    
    def test_paths_data_dir_exists(self):
        """Test that PATHS has data_dir property."""
        assert hasattr(PATHS, 'data_dir')


# === Run tests directly ===
if __name__ == '__main__':
    pytest.main([__file__, '-v'])