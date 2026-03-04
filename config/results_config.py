"""
Cannabis Results Configuration
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2/1/2026
Updated: 3/3/2026
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:
    Centralized configuration for all result collection and processing.
    This module provides path management, state configurations, and
    API settings following the cannabis_licenses repository pattern.
"""
# Standard imports:
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, List
import os


@dataclass
class PathConfig:
    """Path configuration for the results pipeline.
    
    Centralizes all file system paths to ensure consistency across
    all collection algorithms and processing pipelines.
    
    Attributes:
        base_dir: Root directory for all results data.
        cache_dir: Directory for caching downloaded URLs and parsed data.
        log_dir: Directory for log files.
    """
    base_dir: Path = field(default_factory=lambda: Path(os.environ.get('CANNLYTICS_DATA_DIR', 'D:/data')))
    cache_dir: Path = field(default_factory=lambda: Path(os.environ.get('CANNLYTICS_CACHE_DIR', 'D:/data/.cache')))
    log_dir: Path = field(default_factory=lambda: Path(os.environ.get('CANNLYTICS_LOG_DIR', 'D:/data/.logs')))

    @property
    def data_dir(self) -> Path:
        """Root directory for all data files."""
        return self.base_dir

    @property
    def build_dir(self) -> Path:
        """Directory for intermediate build artifacts."""
        return Path(os.environ.get('CANNLYTICS_BUILD_DIR', str(self.base_dir / '.build')))

    @property
    def output_dir(self) -> Path:
        """Directory for final pipeline output files."""
        return Path(os.environ.get('CANNLYTICS_OUTPUT_DIR', str(self.base_dir / '.output')))

    @property
    def documents_dir(self) -> Path:
        """Directory for generated documents (data dictionary, etc.)."""
        return self.base_dir / 'documents' / 'build'

    @property
    def package_dir(self) -> Path:
        """Directory for delivery-ready ZIP packages."""
        return Path(os.environ.get(
            'CANNLYTICS_PACKAGE_DIR',
            str(Path(__file__).resolve().parent.parent / 'package'),
        ))

    def state_dir(self, state: str) -> Path:
        """Get the data directory for a specific state.
        
        Args:
            state: Two-letter state abbreviation (e.g., 'ca', 'ny').
            
        Returns:
            Path to the state's data directory.
        """
        state_names = {v.code: v.name for v in STATES.values()}
        state_name = state_names.get(state.lower(), state).lower().replace(' ', '-')
        return self.data_dir / state_name / 'results'

    def pdf_dir(self, state: str, source: str = '') -> Path:
        """Get the PDF storage directory for a state/source.
        
        Args:
            state: Two-letter state abbreviation.
            source: Optional source identifier (e.g., 'flower-company').
            
        Returns:
            Path to the PDF directory.
        """
        base = self.state_dir(state) / 'pdfs'
        return base / source if source else base

    def datasets_dir(self, state: str) -> Path:
        """Get the datasets directory for a state.
        
        Args:
            state: Two-letter state abbreviation.
            
        Returns:
            Path to the datasets directory.
        """
        return self.state_dir(state) / 'datasets'

    def cache_path(self, name: str) -> Path:
        """Get the cache file path for a named cache.
        
        Args:
            name: Cache identifier (e.g., 'results-ca-flower-company').
            
        Returns:
            Path to the cache JSONL file.
        """
        return self.cache_dir / f'{name}.jsonl'

    def ensure_dirs(self, state: str, source: str = '') -> None:
        """Create all necessary directories for a state/source.
        
        Args:
            state: Two-letter state abbreviation.
            source: Optional source identifier.
        """
        self.state_dir(state).mkdir(parents=True, exist_ok=True)
        self.pdf_dir(state, source).mkdir(parents=True, exist_ok=True)
        self.datasets_dir(state).mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.build_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)


@dataclass
class StateConfig:
    """Configuration for a state's data collection.
    
    Attributes:
        code: Two-letter state code (e.g., 'ca', 'ny').
        name: Full state name (e.g., 'California').
        sources: List of available data sources.
        active: Whether collection is currently active.
        prr_available: Whether public records request data is available.
        notes: Special notes or considerations.
    """
    code: str
    name: str
    sources: List[str] = field(default_factory=list)
    active: bool = True
    prr_available: bool = False
    notes: str = ''


# === State Configurations ===
# Comprehensive list of states with available data sources.
STATES: Dict[str, StateConfig] = {
    # States with functioning collectors
    'ak': StateConfig('ak', 'Alaska', ['prr'], prr_available=True,
                      notes='PRR data only'),
    'az': StateConfig('az', 'Arizona', 
                      ['curaleaf', 'flow_distribution', 'sticky_saguaro', 'high_grade'],
                      notes='High Grade currently broken; Curaleaf has cross-state data'),
    'ca': StateConfig('ca', 'California',
                      ['flower_company', 'glass_house', 'raw_garden', 'sclabs'],
                      notes='Flower Company best performer; IP ban risk'),
    'co': StateConfig('co', 'Colorado', ['prr'], prr_available=True),
    'ct': StateConfig('ct', 'Connecticut', ['ne_labs', 'registry'],
                      notes='NE Labs parsing; needs merge with analyze script'),
    'fl': StateConfig('fl', 'Florida',
                      ['kaycha', 'terplife', 'flowery', 'jungleboys'],
                      notes='TerpLife has Google Drive alternative'),
    'hi': StateConfig('hi', 'Hawaii', ['prr'], prr_available=True),
    'ma': StateConfig('ma', 'Massachusetts', ['mcrlabs', 'prr'], prr_available=True,
                      notes='MCR Labs needs major refactor'),
    'md': StateConfig('md', 'Maryland', ['prr'], prr_available=True),
    'mi': StateConfig('mi', 'Michigan', ['prr'], prr_available=True),
    'mo': StateConfig('mo', 'Missouri', [], active=False,
                      notes='No active collectors'),
    'ms': StateConfig('ms', 'Mississippi', ['prr'], prr_available=True),
    'nj': StateConfig('nj', 'New Jersey', ['prr'], prr_available=True),
    'nv': StateConfig('nv', 'Nevada', ['prr'], prr_available=True,
                      notes='153K+ records'),
    'ny': StateConfig('ny', 'New York',
                      ['cannabis_realm', 'my_coa', 'hudson_cannabis', 'jetty_extracts'],
                      notes='Cannabis Realm best performer; 6-12 month PRR advantage'),
    'oh': StateConfig('oh', 'Ohio', ['prr'], prr_available=True),
    'or': StateConfig('or', 'Oregon', ['prr'], prr_available=True,
                      notes='196K+ records'),
    'ri': StateConfig('ri', 'Rhode Island', ['prr'], prr_available=True),
    'ut': StateConfig('ut', 'Utah', ['prr'], prr_available=True,
                      notes='Limited data'),
    'vt': StateConfig('vt', 'Vermont',
                      ['3js_vt', 'garcias', 'gram_central', 'float_on', 'bushy_beard'],
                      active=False, notes='NEW - 3,185+ COAs discovered'),
    'wa': StateConfig('wa', 'Washington', ['prr', 'inventory'], prr_available=True,
                      notes='202K+ records; needs unification'),
}


# === API Configuration ===
API_CONFIG = {
    'rate_limit_delay': 3.33,      # Seconds between requests (default)
    'max_retries': 3,              # Maximum retry attempts
    'timeout': 30,                 # Request timeout in seconds
    'batch_size': 100,             # Records per batch upload
    'exponential_backoff_base': 2, # Base for exponential backoff
    'max_backoff_delay': 60,       # Maximum backoff delay in seconds
}


# === Processing Configuration ===
PROCESSING_CONFIG = {
    'parse_method': 'ai',              # 'ai' or 'custom'
    'ai_model': 'gpt-4o-mini',         # Model for AI parsing
    'embedding_model': 'text-embedding-3-small',
    'max_pdf_size_mb': 50,             # Skip PDFs larger than this
    'enable_ocr': True,                # Enable OCR for image-based PDFs
}


# === Source-Specific Configuration ===
SOURCE_CONFIG = {
    # California
    'flower_company': {
        'base_url': 'https://flowercompany.com/',
        'pause_time': 4.0,
        'risk_level': 'medium',  # IP ban risk
        'run_frequency': 'weekly',
    },
    'glass_house': {
        'base_url': 'https://glasshousefarms.org/',
        'pause_time': 3.0,
        'needs_repair': True,
    },
    'raw_garden': {
        'base_url': 'https://rawgarden.farm/',
        'pause_time': 3.0,
        'estimated_coas': '5000-6500+',
    },
    
    # Arizona
    'curaleaf': {
        'base_url': 'https://coas.curaleaf.com/transparency/',
        'pause_time': 5.0,
        'multi_state': True,
        'notes': 'Contains cross-state data - filter by state',
    },
    'flow_distribution': {
        'base_url': 'https://flowdistribution.com/',
        'pause_time': 5.0,
        'needs_age_gate': True,
    },
    'sticky_saguaro': {
        'base_url': 'https://testing.stickysaguaro.com/',
        'pause_time': 3.0,
    },
    'high_grade': {
        'base_url': 'https://highgradeusa.com/testing/',
        'pause_time': 3.0,
        'needs_repair': True,
    },
    
    # New York
    'cannabis_realm': {
        'base_url': 'https://cannabisrealmny.com/',
        'pause_time': 10.0,
        'notes': 'Good performer; uses Alleaves API',
    },
    'my_coa': {
        'base_url': 'https://www.mycoa.info/',
        'pause_time': 5.0,
        'uses_dropbox': True,
    },
    'hudson_cannabis': {
        'base_url': 'https://www.hudsoncannabis.co/coas',
        'pause_time': 5.0,
        'uses_google_drive': True,
    },
    
    # Florida
    'kaycha': {
        'base_url': 'https://yourcoa.com/',
        'estimated_coas': '6000-8000+',
        'notes': 'Historical data; unclear if still uploading',
    },
    'terplife': {
        'base_url': 'https://terplife.com/',
        'estimated_coas': '14000+',
        'google_drive_available': True,
    },
    
    # Vermont (NEW)
    'vt_3js': {
        'folder_id': '1eJBq5VMfngke1iTXZ5zD0xTbj-SFx83z',
        'estimated_pdfs': 1342,
        'method': 'google_drive',
    },
    'vt_garcias': {
        'folder_id': '1nDUGiUP0xTd9o-n0K1V3da5HqJfEchPr',
        'estimated_pdfs': 1453,
        'method': 'google_drive',
    },
    'vt_gram_central': {
        'folder_id': '1MYlsmjplv3s8Q2ZB_fVWdI2pE8qPf3TO',
        'estimated_pdfs': 387,
        'method': 'google_drive',
    },
}


# === Product Type Standardization ===
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


# === Data Quality Thresholds ===
DATA_QUALITY = {
    'min_completeness': 0.90,        # 90% field population target
    'min_accuracy': 0.99,            # 99% accuracy target
    'max_duplicate_rate': 0.001,     # 0.1% max duplicate rate
    'max_data_age_days': 30,         # Maximum 30 days since last update
}


# === Instantiate default paths ===
PATHS = PathConfig()


def get_collector_config(state: str, source: str) -> dict:
    """Get configuration for a specific collector.
    
    Args:
        state: Two-letter state abbreviation.
        source: Source identifier.
        
    Returns:
        Dictionary with combined state, source, and API configuration.
    """
    state_config = STATES.get(state.lower())
    source_config = SOURCE_CONFIG.get(source, {})
    
    return {
        'state': state_config,
        'source': source_config,
        'api': API_CONFIG,
        'paths': {
            'data_dir': str(PATHS.state_dir(state)),
            'pdf_dir': str(PATHS.pdf_dir(state, source)),
            'cache_path': str(PATHS.cache_path(f'results-{state}-{source}')),
            'log_dir': str(PATHS.log_dir),
        },
        'pause_time': source_config.get('pause_time', API_CONFIG['rate_limit_delay']),
    }


# === Test ===
if __name__ == '__main__':
    # Test path generation
    print(f"California data dir: {PATHS.state_dir('ca')}")
    print(f"California PDF dir: {PATHS.pdf_dir('ca', 'flower-company')}")
    print(f"Cache path: {PATHS.cache_path('results-ca-flower-company')}")
    print(f"Build dir: {PATHS.build_dir}")
    print(f"Output dir: {PATHS.output_dir}")
    print(f"Documents dir: {PATHS.documents_dir}")
    print(f"Package dir: {PATHS.package_dir}")
    
    # Test state config
    ca_config = STATES['ca']
    print(f"\nCalifornia sources: {ca_config.sources}")
    
    # Test collector config
    config = get_collector_config('ca', 'flower_company')
    print(f"\nFlower Company config: {config}")