"""
Cannabis Results | Schema Tests
Copyright (c) 2024-2026 Cannlytics

Description:
    Tests for results_schema.py including LabResult dataclass,
    validation rules, and normalization functions.
    
    Run:
        pytest tests/test_schema.py -v
"""
# Standard imports:
from datetime import datetime
import json
import sys
from pathlib import Path

# External imports:
import pytest

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Internal imports:
from config.results_schema import (
    LabResult,
    ResultDetail,
    VALIDATION_RULES,
    validate_result,
    normalize_status,
    normalize_product_type,
    normalize_analyte_key,
    ANALYTE_KEYS,
)


class TestLabResult:
    """Tests for the LabResult dataclass."""
    
    def test_create_minimal(self):
        """Test creating LabResult with minimal data."""
        result = LabResult(state='ca')
        
        assert result.state == 'ca'
        assert result.id is not None  # Auto-generated
        assert len(result.id) == 16
        assert result.sample_hash is not None
    
    def test_create_full(self, sample_lab_result):
        """Test creating LabResult with full data."""
        result = LabResult(**sample_lab_result)
        
        assert result.product_name == 'Blue Dream'
        assert result.total_thc == 24.5
        assert result.state == 'ca'
    
    def test_id_generation_deterministic(self):
        """Test that same inputs produce same ID."""
        result1 = LabResult(
            product_name='Blue Dream',
            producer='Test Farm',
            batch_number='BATCH-001',
            date_tested=datetime(2026, 1, 15),
        )
        result2 = LabResult(
            product_name='Blue Dream',
            producer='Test Farm',
            batch_number='BATCH-001',
            date_tested=datetime(2026, 1, 15),
        )
        
        # IDs should match for identical data
        assert result1.id == result2.id
    
    def test_id_generation_unique(self):
        """Test that different inputs produce different IDs."""
        result1 = LabResult(product_name='Blue Dream', producer='Farm A')
        result2 = LabResult(product_name='OG Kush', producer='Farm A')
        result3 = LabResult(product_name='Blue Dream', producer='Farm B')
        
        # All should be different
        assert result1.id != result2.id
        assert result1.id != result3.id
        assert result2.id != result3.id
    
    def test_hash_generation(self):
        """Test sample hash generation."""
        result = LabResult(
            product_name='Blue Dream',
            producer='Test Farm',
            total_thc=24.5,
        )
        
        assert result.sample_hash is not None
        assert len(result.sample_hash) == 64  # SHA-256 hex
    
    def test_to_dict(self):
        """Test conversion to dictionary."""
        result = LabResult(
            product_name='Blue Dream',
            state='ca',
            total_thc=24.5,
            date_tested=datetime(2026, 1, 15, 10, 30, 0),
        )
        
        data = result.to_dict()
        
        assert isinstance(data, dict)
        assert data['product_name'] == 'Blue Dream'
        assert data['state'] == 'ca'
        assert data['total_thc'] == 24.5
        # Datetime should be ISO string
        assert data['date_tested'] == '2026-01-15T10:30:00'
    
    def test_from_dict(self, sample_lab_result):
        """Test creating LabResult from dictionary."""
        result = LabResult.from_dict(sample_lab_result)
        
        assert result.product_name == sample_lab_result['product_name']
        assert result.total_thc == sample_lab_result['total_thc']
        assert result.state == sample_lab_result['state']
    
    def test_from_dict_with_datetime_string(self):
        """Test parsing datetime strings in from_dict."""
        data = {
            'product_name': 'Test',
            'state': 'ca',
            'date_tested': '2026-01-15T10:30:00',
        }
        
        result = LabResult.from_dict(data)
        
        assert isinstance(result.date_tested, datetime)
        assert result.date_tested.year == 2026
        assert result.date_tested.month == 1
        assert result.date_tested.day == 15
    
    def test_list_fields_default_empty(self):
        """Test that list fields default to empty lists."""
        result = LabResult(state='ca')
        
        assert result.metrc_ids == []
        assert result.traceability_ids == []
        assert result.coa_urls == []
        assert result.results == []
        assert result.images == []


class TestValidation:
    """Tests for validation functions."""
    
    def test_validate_valid_result(self):
        """Test validation of a valid result."""
        result = LabResult(
            state='ca',
            total_thc=24.5,
            total_cbd=0.5,
            pesticides_status='pass',
        )
        
        is_valid, errors = validate_result(result)
        
        assert is_valid is True
        assert len(errors) == 0
    
    def test_validate_missing_required(self):
        """Test validation catches missing required fields."""
        result = LabResult()  # Missing state
        
        # Manually set state to None to test validation
        result.state = None
        
        is_valid, errors = validate_result(result)
        
        assert is_valid is False
        assert any('state' in e.lower() for e in errors)
    
    def test_validate_thc_out_of_range(self):
        """Test validation catches THC out of range."""
        result = LabResult(
            state='ca',
            total_thc=150.0,  # Invalid: above max
        )
        
        is_valid, errors = validate_result(result)
        
        assert is_valid is False
        assert any('total_thc' in e for e in errors)
    
    def test_validate_negative_thc(self):
        """Test validation catches negative THC."""
        result = LabResult(
            state='ca',
            total_thc=-5.0,  # Invalid: negative
        )
        
        is_valid, errors = validate_result(result)
        
        assert is_valid is False
        assert any('total_thc' in e for e in errors)
    
    def test_validate_invalid_status(self):
        """Test validation catches invalid status values."""
        result = LabResult(
            state='ca',
            pesticides_status='invalid_status',
        )
        
        is_valid, errors = validate_result(result)
        
        assert is_valid is False
        assert any('pesticides_status' in e for e in errors)
    
    def test_validate_terpenes_range(self):
        """Test validation of terpene ranges."""
        # Valid terpenes
        result = LabResult(
            state='ca',
            total_terpenes=3.5,
            beta_myrcene=1.2,
        )
        is_valid, errors = validate_result(result)
        assert is_valid is True
        
        # Invalid terpenes
        result_invalid = LabResult(
            state='ca',
            total_terpenes=20.0,  # Above max 15%
        )
        is_valid, errors = validate_result(result_invalid)
        assert is_valid is False


class TestNormalization:
    """Tests for normalization functions."""
    
    def test_normalize_status_pass(self):
        """Test normalizing pass status variations."""
        pass_values = ['pass', 'PASS', 'Passed', 'passing', 'P', 'compliant', 'yes', 'true', '1']
        
        for value in pass_values:
            result = normalize_status(value)
            assert result == 'pass', f"Expected 'pass' for '{value}', got '{result}'"
    
    def test_normalize_status_fail(self):
        """Test normalizing fail status variations."""
        fail_values = ['fail', 'FAIL', 'Failed', 'failing', 'F', 'non-compliant', 'no', 'false', '0']
        
        for value in fail_values:
            result = normalize_status(value)
            assert result == 'fail', f"Expected 'fail' for '{value}', got '{result}'"
    
    def test_normalize_status_nt(self):
        """Test normalizing not tested status variations."""
        nt_values = ['nt', 'NT', 'not tested', 'N/T', 'n/a', 'NA', '-', '']
        
        for value in nt_values:
            result = normalize_status(value)
            assert result == 'nt', f"Expected 'nt' for '{value}', got '{result}'"
    
    def test_normalize_status_none(self):
        """Test normalizing None status."""
        assert normalize_status(None) is None
    
    def test_normalize_status_unknown(self):
        """Test normalizing unknown status."""
        result = normalize_status('pending review')
        assert result == 'pending review'
    
    def test_normalize_product_type(self):
        """Test normalizing product types."""
        # Note: This requires PRODUCT_TYPES from config
        try:
            from config.results_config import PRODUCT_TYPES
            
            # Test flower variations
            for variation in ['flower', 'bud', 'dried flower', 'trim']:
                result = normalize_product_type(variation)
                assert result == 'flower', f"Expected 'flower' for '{variation}', got '{result}'"
            
            # Test preroll variations
            for variation in ['preroll', 'pre-roll', 'joint']:
                result = normalize_product_type(variation)
                assert result == 'preroll', f"Expected 'preroll' for '{variation}', got '{result}'"
            
            # Test None
            assert normalize_product_type(None) is None
            
        except ImportError:
            pytest.skip("Config not available")
    
    def test_normalize_analyte_key(self):
        """Test normalizing analyte keys."""
        test_cases = [
            ('thc', 'delta_9_thc'),
            ('THC', 'delta_9_thc'),
            ('d9thc', 'delta_9_thc'),
            ('delta-9-thc', 'delta_9_thc'),
            ('myrcene', 'beta_myrcene'),
            ('b-myrcene', 'beta_myrcene'),
            ('limonene', 'd_limonene'),
            ('caryophyllene', 'beta_caryophyllene'),
        ]
        
        for input_key, expected in test_cases:
            result = normalize_analyte_key(input_key)
            assert result == expected, f"Expected '{expected}' for '{input_key}', got '{result}'"
    
    def test_normalize_analyte_key_unknown(self):
        """Test normalizing unknown analyte keys."""
        result = normalize_analyte_key('some_unknown_analyte')
        assert result == 'some_unknown_analyte'


class TestResultDetail:
    """Tests for ResultDetail dataclass."""
    
    def test_create_result_detail(self):
        """Test creating a ResultDetail."""
        detail = ResultDetail(
            analysis='cannabinoids',
            key='delta_9_thc',
            name='Δ9-THC',
            value=24.5,
            units='%',
            status='pass',
        )
        
        assert detail.analysis == 'cannabinoids'
        assert detail.key == 'delta_9_thc'
        assert detail.value == 24.5
    
    def test_result_detail_to_dict(self):
        """Test converting ResultDetail to dictionary."""
        detail = ResultDetail(
            analysis='pesticides',
            key='abamectin',
            value=0.0,
            limit=0.1,
            units='ppm',
            status='pass',
        )
        
        data = detail.to_dict()
        
        assert isinstance(data, dict)
        assert data['analysis'] == 'pesticides'
        assert data['key'] == 'abamectin'
        assert data['status'] == 'pass'


class TestValidationRules:
    """Tests for VALIDATION_RULES configuration."""
    
    def test_cannabinoid_rules_exist(self):
        """Test that cannabinoid validation rules are defined."""
        cannabinoid_fields = ['delta_9_thc', 'total_thc', 'cbd', 'total_cbd']
        
        for field in cannabinoid_fields:
            assert field in VALIDATION_RULES, f"Missing rule for {field}"
            assert 'min' in VALIDATION_RULES[field]
            assert 'max' in VALIDATION_RULES[field]
    
    def test_terpene_rules_exist(self):
        """Test that terpene validation rules are defined."""
        terpene_fields = ['total_terpenes', 'beta_myrcene', 'd_limonene']
        
        for field in terpene_fields:
            assert field in VALIDATION_RULES, f"Missing rule for {field}"
    
    def test_status_rules_have_valid_values(self):
        """Test that status validation rules include expected values."""
        status_fields = ['pesticides_status', 'heavy_metals_status', 'microbials_status']
        
        for field in status_fields:
            assert field in VALIDATION_RULES, f"Missing rule for {field}"
            assert 'values' in VALIDATION_RULES[field]
            assert 'pass' in VALIDATION_RULES[field]['values']
            assert 'fail' in VALIDATION_RULES[field]['values']


# === Run tests directly ===
if __name__ == '__main__':
    pytest.main([__file__, '-v'])