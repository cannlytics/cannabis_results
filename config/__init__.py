"""
Cannabis Results | Configuration Package
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2/2/2026
Updated: 2/2/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Centralized configuration, schema, and utilities for the
    cannabis_results repository.
    
    This package provides:
    - Path management (results_config.py)
    - Data schema definitions (results_schema.py)
    - Selenium driver utilities (driver_utils.py)

Usage:
    ```python
    from config import PATHS, STATES, LabResult
    from config.driver_utils import initialize_driver
    ```
"""

# Configuration exports
from .results_config import (
    PathConfig,
    StateConfig,
    PATHS,
    STATES,
    API_CONFIG,
    PROCESSING_CONFIG,
    SOURCE_CONFIG,
    PRODUCT_TYPES,
    DATA_QUALITY,
    get_collector_config,
)

# Schema exports
from .results_schema import (
    LabResult,
    ResultDetail,
    VALIDATION_RULES,
    ANALYTE_KEYS,
    validate_result,
    normalize_status,
    normalize_product_type,
    normalize_analyte_key,
)

# Driver utilities
from .driver_utils import (
    initialize_driver,
    initialize_driver_with_retry,
    get_driver_info,
)

__all__ = [
    # Config
    'PathConfig',
    'StateConfig',
    'PATHS',
    'STATES',
    'API_CONFIG',
    'PROCESSING_CONFIG',
    'SOURCE_CONFIG',
    'PRODUCT_TYPES',
    'DATA_QUALITY',
    'get_collector_config',
    # Schema
    'LabResult',
    'ResultDetail',
    'VALIDATION_RULES',
    'ANALYTE_KEYS',
    'validate_result',
    'normalize_status',
    'normalize_product_type',
    'normalize_analyte_key',
    # Driver
    'initialize_driver',
    'initialize_driver_with_retry',
    'get_driver_info',
]

__version__ = '1.0.0'
