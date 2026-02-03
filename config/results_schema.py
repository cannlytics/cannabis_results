"""
Cannabis Results Schema
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2/1/2026
Updated: 2/2/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Standardized field definitions for all lab results.
    This schema ensures consistency across all states and sources,
    enabling reliable data aggregation and analysis.
"""
# Standard imports:
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, List, Dict, Any
import hashlib
import json


@dataclass
class LabResult:
    """Standard schema for a cannabis lab result record.
    
    This dataclass defines the canonical schema for all lab results
    in the Cannlytics repository. All collectors should map their
    data to this schema for consistency.
    
    Attributes are organized into logical groups:
    - Identifiers: Unique keys and hashes
    - Product Information: Name, type, strain
    - Producer Information: Cultivator/manufacturer details
    - Lab Information: Testing laboratory details
    - Dates: All relevant timestamps
    - Cannabinoids: THC, CBD, and other cannabinoid values
    - Terpenes: Terpene profile values
    - Contaminants: Safety test results
    - Traceability: Metrc and other tracking IDs
    - COA Information: Links to certificates
    - Metadata: Source tracking and timestamps
    """
    
    # === Identifiers ===
    id: Optional[str] = None              # Unique record ID (auto-generated if None)
    sample_id: Optional[str] = None       # Sample identifier from lab/source
    sample_hash: Optional[str] = None     # Hash for deduplication
    results_hash: Optional[str] = None    # Hash of results for validation
    
    # === Product Information ===
    product_name: Optional[str] = None
    product_type: Optional[str] = None    # Standardized type (flower, vape, etc.)
    product_subtype: Optional[str] = None # More specific type
    strain_name: Optional[str] = None
    batch_number: Optional[str] = None
    batch_size: Optional[float] = None    # Size in grams
    product_size: Optional[float] = None  # Individual unit size in mg
    serving_size: Optional[float] = None  # Serving size in mg
    servings_per_package: Optional[int] = None
    
    # === Producer Information ===
    producer: Optional[str] = None
    producer_license_number: Optional[str] = None
    producer_address: Optional[str] = None
    producer_street: Optional[str] = None
    producer_city: Optional[str] = None
    producer_state: Optional[str] = None
    producer_zipcode: Optional[str] = None
    
    # === Distributor Information ===
    distributor: Optional[str] = None
    distributor_license_number: Optional[str] = None
    distributor_address: Optional[str] = None
    
    # === Lab Information ===
    lab: Optional[str] = None
    lab_license_number: Optional[str] = None
    lab_address: Optional[str] = None
    lab_state: Optional[str] = None
    lab_id: Optional[str] = None          # Lab's internal sample ID
    
    # === Dates ===
    date_tested: Optional[datetime] = None
    date_collected: Optional[datetime] = None
    date_received: Optional[datetime] = None
    date_produced: Optional[datetime] = None
    date_packaged: Optional[datetime] = None
    date_expires: Optional[datetime] = None
    
    # === Cannabinoids (percent) ===
    delta_9_thc: Optional[float] = None
    delta_8_thc: Optional[float] = None
    thca: Optional[float] = None
    total_thc: Optional[float] = None
    cbd: Optional[float] = None
    cbda: Optional[float] = None
    total_cbd: Optional[float] = None
    cbg: Optional[float] = None
    cbga: Optional[float] = None
    cbn: Optional[float] = None
    cbc: Optional[float] = None
    cbdv: Optional[float] = None
    thcv: Optional[float] = None
    total_cannabinoids: Optional[float] = None
    
    # === Terpenes (percent) ===
    beta_myrcene: Optional[float] = None
    d_limonene: Optional[float] = None
    beta_caryophyllene: Optional[float] = None
    alpha_pinene: Optional[float] = None
    beta_pinene: Optional[float] = None
    linalool: Optional[float] = None
    alpha_humulene: Optional[float] = None
    terpinolene: Optional[float] = None
    ocimene: Optional[float] = None
    alpha_bisabolol: Optional[float] = None
    camphene: Optional[float] = None
    geraniol: Optional[float] = None
    nerolidol: Optional[float] = None
    guaiol: Optional[float] = None
    caryophyllene_oxide: Optional[float] = None
    total_terpenes: Optional[float] = None
    
    # === Contaminants ===
    pesticides_status: Optional[str] = None
    heavy_metals_status: Optional[str] = None
    microbials_status: Optional[str] = None
    mycotoxins_status: Optional[str] = None
    residual_solvents_status: Optional[str] = None
    foreign_matter_status: Optional[str] = None
    moisture_content: Optional[float] = None
    water_activity: Optional[float] = None
    overall_status: Optional[str] = None  # pass/fail/N/A
    
    # === Traceability ===
    metrc_ids: List[str] = field(default_factory=list)
    metrc_lab_id: Optional[str] = None
    metrc_source_id: Optional[str] = None
    traceability_ids: List[Dict] = field(default_factory=list)
    
    # === COA Information ===
    coa_url: Optional[str] = None
    coa_urls: List[Dict] = field(default_factory=list)  # [{url, filename}]
    coa_pdf: Optional[str] = None         # Local path to PDF
    lab_results_url: Optional[str] = None
    
    # === Classification ===
    indica_percentage: Optional[float] = None
    sativa_percentage: Optional[float] = None
    classification: Optional[str] = None  # Indica, Sativa, Hybrid
    
    # === Additional Results ===
    results: List[Dict] = field(default_factory=list)  # Full results array
    images: List[Dict] = field(default_factory=list)   # [{url, filename}]
    
    # === Metadata ===
    state: Optional[str] = None           # Two-letter state code
    source: Optional[str] = None          # Data source identifier
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    data_refreshed_date: Optional[datetime] = None
    
    def __post_init__(self):
        """Generate IDs and hashes if not provided."""
        if not self.id:
            self.id = self._generate_id()
        if not self.sample_hash:
            self.sample_hash = self._generate_hash()
    
    def _generate_id(self) -> str:
        """Generate a unique ID based on key fields."""
        key_data = f"{self.product_name or ''}{self.producer or ''}{self.batch_number or ''}{self.date_tested or ''}"
        return hashlib.sha256(key_data.encode()).hexdigest()[:16]
    
    def _generate_hash(self) -> str:
        """Generate a hash of the entire record for deduplication."""
        # Use key identifying fields
        hash_data = {
            'product_name': self.product_name,
            'producer': self.producer,
            'batch_number': self.batch_number,
            'total_thc': self.total_thc,
            'date_tested': str(self.date_tested) if self.date_tested else None,
        }
        return hashlib.sha256(json.dumps(hash_data, sort_keys=True).encode()).hexdigest()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with datetime serialization."""
        data = asdict(self)
        # Convert datetime objects to ISO strings
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LabResult':
        """Create a LabResult from a dictionary."""
        # Convert ISO strings back to datetime
        date_fields = ['date_tested', 'date_collected', 'date_received', 
                       'date_produced', 'date_packaged', 'date_expires',
                       'created_at', 'updated_at', 'data_refreshed_date']
        for field_name in date_fields:
            if field_name in data and isinstance(data[field_name], str):
                try:
                    data[field_name] = datetime.fromisoformat(data[field_name])
                except (ValueError, TypeError):
                    data[field_name] = None
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# === Validation Rules ===
VALIDATION_RULES = {
    # Cannabinoid ranges (percent)
    'delta_9_thc': {'min': 0, 'max': 40, 'type': float},
    'delta_8_thc': {'min': 0, 'max': 40, 'type': float},
    'thca': {'min': 0, 'max': 40, 'type': float},
    'total_thc': {'min': 0, 'max': 45, 'type': float},
    'cbd': {'min': 0, 'max': 30, 'type': float},
    'cbda': {'min': 0, 'max': 30, 'type': float},
    'total_cbd': {'min': 0, 'max': 35, 'type': float},
    'total_cannabinoids': {'min': 0, 'max': 100, 'type': float},
    
    # Terpene ranges (percent)
    'total_terpenes': {'min': 0, 'max': 15, 'type': float},
    'beta_myrcene': {'min': 0, 'max': 5, 'type': float},
    'd_limonene': {'min': 0, 'max': 5, 'type': float},
    'beta_caryophyllene': {'min': 0, 'max': 5, 'type': float},
    'linalool': {'min': 0, 'max': 3, 'type': float},
    
    # Moisture/water activity
    'moisture_content': {'min': 0, 'max': 20, 'type': float},
    'water_activity': {'min': 0, 'max': 1, 'type': float},
    
    # Classification percentages
    'indica_percentage': {'min': 0, 'max': 1, 'type': float},
    'sativa_percentage': {'min': 0, 'max': 1, 'type': float},
    
    # Status fields
    'pesticides_status': {'values': ['pass', 'fail', 'nt', 'n/a', None]},
    'heavy_metals_status': {'values': ['pass', 'fail', 'nt', 'n/a', None]},
    'microbials_status': {'values': ['pass', 'fail', 'nt', 'n/a', None]},
    'mycotoxins_status': {'values': ['pass', 'fail', 'nt', 'n/a', None]},
    'residual_solvents_status': {'values': ['pass', 'fail', 'nt', 'n/a', None]},
    'overall_status': {'values': ['pass', 'fail', 'nt', 'n/a', None]},
    
    # Required fields
    'required': ['state'],
}


def validate_result(result: LabResult) -> tuple:
    """Validate a single result against rules.
    
    Args:
        result: LabResult instance to validate.
        
    Returns:
        Tuple of (is_valid: bool, errors: list of error messages).
    """
    errors = []
    data = result.to_dict()
    
    # Check required fields
    for field_name in VALIDATION_RULES.get('required', []):
        if not data.get(field_name):
            errors.append(f'Missing required field: {field_name}')
    
    # Check numeric ranges
    for field_name, rules in VALIDATION_RULES.items():
        if field_name == 'required':
            continue
        value = data.get(field_name)
        if value is None:
            continue
        
        if 'min' in rules and value < rules['min']:
            errors.append(f'{field_name} below minimum: {value} < {rules["min"]}')
        if 'max' in rules and value > rules['max']:
            errors.append(f'{field_name} above maximum: {value} > {rules["max"]}')
        if 'values' in rules and value not in rules['values']:
            errors.append(f'{field_name} invalid value: {value}')
    
    return len(errors) == 0, errors


def normalize_status(status: Any) -> Optional[str]:
    """Normalize a status value to standard format.
    
    Args:
        status: Raw status value from source.
        
    Returns:
        Normalized status string or None.
    """
    if status is None:
        return None
    
    status_str = str(status).lower().strip()
    
    # Map common variations
    pass_values = ['pass', 'passed', 'passing', 'p', 'compliant', 'yes', 'true', '1']
    fail_values = ['fail', 'failed', 'failing', 'f', 'non-compliant', 'no', 'false', '0']
    nt_values = ['nt', 'not tested', 'n/t', 'not applicable', 'n/a', 'na', '-', '']
    
    if status_str in pass_values:
        return 'pass'
    elif status_str in fail_values:
        return 'fail'
    elif status_str in nt_values:
        return 'nt'
    
    return status_str


def normalize_product_type(product_type: Any) -> Optional[str]:
    """Normalize a product type to standard categories.
    
    Args:
        product_type: Raw product type from source.
        
    Returns:
        Normalized product type or original if not mapped.
    """
    if product_type is None:
        return None
    
    # Import here to avoid circular imports
    try:
        from config.results_config import PRODUCT_TYPES
    except ImportError:
        # Fallback mapping if config not available
        PRODUCT_TYPES = {
            'flower': ['flower', 'bud', 'buds', 'cannabis flower', 'dried flower', 'trim', 'shake'],
            'preroll': ['preroll', 'pre-roll', 'joint', 'blunt', 'prerolls', 'pre-rolls', 'infused preroll'],
            'concentrate': ['concentrate', 'extract', 'wax', 'shatter', 'rosin', 'live resin', 
                           'budder', 'badder', 'sauce', 'diamonds', 'sugar', 'crumble'],
            'vape': ['vape', 'cartridge', 'cart', 'vaporizer', 'pod', 'disposable', 'aio'],
            'edible': ['edible', 'gummy', 'chocolate', 'beverage', 'candy', 'baked goods', 
                      'capsule', 'tablet'],
            'tincture': ['tincture', 'oil', 'drops', 'sublingual', 'rso'],
            'topical': ['topical', 'cream', 'lotion', 'balm', 'salve', 'transdermal'],
        }
    
    pt_lower = str(product_type).lower().strip()
    
    for standard_type, variations in PRODUCT_TYPES.items():
        if pt_lower in [v.lower() for v in variations]:
            return standard_type
    
    # Return original if no match
    return product_type


# === Result Detail Schema ===
@dataclass
class ResultDetail:
    """Schema for individual test result within a COA.
    
    Represents a single analyte measurement from a lab test.
    """
    analysis: str                          # Analysis type (cannabinoids, pesticides, etc.)
    key: str                               # Standardized analyte key
    name: Optional[str] = None             # Lab's display name
    value: Optional[float] = None          # Measured value
    mg_g: Optional[float] = None           # Value in mg/g
    units: Optional[str] = None            # Units for value/limit
    limit: Optional[float] = None          # Pass/fail threshold
    lod: Optional[float] = None            # Limit of detection
    loq: Optional[float] = None            # Limit of quantification
    status: Optional[str] = None           # pass/fail/nt
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


# === Standard Analyte Keys ===
# Mapping of common variations to standard keys
# NOTE: The normalize_analyte_key function converts hyphens to underscores BEFORE lookup,
# so we include BOTH hyphen and underscore versions for proper matching.
ANALYTE_KEYS = {
    # Cannabinoids - hyphen versions
    'thc': 'delta_9_thc',
    'd9thc': 'delta_9_thc',
    'delta-9-thc': 'delta_9_thc',
    'delta9thc': 'delta_9_thc',
    'd8thc': 'delta_8_thc',
    'delta-8-thc': 'delta_8_thc',
    'thca-a': 'thca',
    'thc-a': 'thca',
    'cbda': 'cbda',
    'cbd-a': 'cbda',
    'cannabidiol': 'cbd',
    'cannabigerol': 'cbg',
    
    # Cannabinoids - underscore versions (after hyphen replacement)
    'delta_9_thc': 'delta_9_thc',
    'delta_8_thc': 'delta_8_thc',
    'thca_a': 'thca',
    'thc_a': 'thca',
    'cbd_a': 'cbda',
    
    # Terpenes - hyphen versions (original input)
    'myrcene': 'beta_myrcene',
    'b-myrcene': 'beta_myrcene',
    'limonene': 'd_limonene',
    'd-limonene': 'd_limonene',
    'caryophyllene': 'beta_caryophyllene',
    'b-caryophyllene': 'beta_caryophyllene',
    'a-pinene': 'alpha_pinene',
    'b-pinene': 'beta_pinene',
    'a-humulene': 'alpha_humulene',
    'humulene': 'alpha_humulene',
    
    # Terpenes - underscore versions (after hyphen replacement)
    'b_myrcene': 'beta_myrcene',
    'd_limonene': 'd_limonene',
    'b_caryophyllene': 'beta_caryophyllene',
    'a_pinene': 'alpha_pinene',
    'b_pinene': 'beta_pinene',
    'a_humulene': 'alpha_humulene',
    
    # Additional common variations
    'beta_myrcene': 'beta_myrcene',
    'beta_caryophyllene': 'beta_caryophyllene',
    'alpha_pinene': 'alpha_pinene',
    'alpha_humulene': 'alpha_humulene',
}


def normalize_analyte_key(key: str) -> str:
    """Normalize an analyte key to standard format.
    
    Converts spaces and hyphens to underscores, lowercases,
    then looks up in ANALYTE_KEYS mapping.
    
    Args:
        key: Raw analyte key from source.
        
    Returns:
        Standardized analyte key.
        
    Examples:
        >>> normalize_analyte_key('THC')
        'delta_9_thc'
        >>> normalize_analyte_key('b-myrcene')
        'beta_myrcene'
        >>> normalize_analyte_key('B-Myrcene')
        'beta_myrcene'
    """
    key_lower = str(key).lower().strip().replace(' ', '_').replace('-', '_')
    return ANALYTE_KEYS.get(key_lower, key_lower)


# === Test ===
if __name__ == '__main__':
    # Test LabResult creation
    result = LabResult(
        product_name='Blue Dream',
        producer='Test Farm',
        state='ca',
        total_thc=24.5,
        total_cbd=0.5,
        pesticides_status='pass',
    )
    
    print(f"Generated ID: {result.id}")
    print(f"Generated hash: {result.sample_hash}")
    
    # Test validation
    is_valid, errors = validate_result(result)
    print(f"Valid: {is_valid}, Errors: {errors}")
    
    # Test with invalid data
    invalid_result = LabResult(
        product_name='Bad Data',
        total_thc=150,  # Invalid: above max
        state=None,     # Invalid: required field missing
    )
    is_valid, errors = validate_result(invalid_result)
    print(f"Valid: {is_valid}, Errors: {errors}")
    
    # Test normalization
    print(f"Status normalize: {normalize_status('PASSED')}")
    print(f"Product type normalize: {normalize_product_type('Pre-Roll')}")
    
    # Test analyte key normalization
    test_keys = ['THC', 'b-myrcene', 'B-Myrcene', 'd-limonene', 'myrcene']
    for key in test_keys:
        print(f"Analyte normalize: '{key}' -> '{normalize_analyte_key(key)}'")