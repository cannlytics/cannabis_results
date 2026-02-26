"""
Cannabis Results Schema
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2/1/2026
Updated: 2/25/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Standardized field definitions for all lab results, including
    comprehensive analyte key definitions for all standard analyses.

    Field Groups:
        - Identifiers: Unique keys and hashes
        - Product Information: Name, type, strain, sizing
        - Producer / Distributor / Lab Information + location
        - Dates: All relevant timestamps
        - Analyses: List of analyses and per-analysis method/status
        - Cannabinoids, Terpenes, Contaminants
        - Traceability, COA Information, Classification
        - Additional Results: Full analyte results array
        - Metadata: Source tracking and timestamps

    Analyte Definitions:
        - CANNABINOID_KEYS: Standard cannabinoid analyte keys
        - TERPENE_KEYS: Standard terpene analyte keys
        - HEAVY_METAL_KEYS: Standard heavy metal analyte keys
        - MICROBIAL_KEYS: Standard microbial analyte keys
        - PESTICIDE_KEYS: Standard pesticide analyte keys
        - RESIDUAL_SOLVENT_KEYS: Standard residual solvent analyte keys
        - MOISTURE_KEYS: Standard moisture and water activity keys
        - FOREIGN_MATTER_KEYS: Standard foreign matter keys
        - ANALYSIS_CONFIGS: Master config mapping analyses to keys and keywords
"""
# Standard imports:
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, List, Dict, Any
import hashlib
import json


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Standard Analyte Definitions                                     ║
# ╚══════════════════════════════════════════════════════════════════╝

CANNABINOID_KEYS = [
    # '9r_delta_10_thc',
    # '9s_delta_10_thc',
    'cbc',
    'cbd',
    'cbda',
    'cbdv',
    'cbdva',
    'cbg',
    'cbga',
    'cbl',
    'cbn',
    'cbna',
    'delta_8_thc',
    'delta_9_thc',
    'thca',
    'thcv',
    'thcva',
]

TERPENE_KEYS = [
    'alpha_bisabolol',
    'alpha_cedrene',
    'alpha_humulene',
    'alpha_phellandrene',
    'alpha_pinene',
    'alpha_terpinene',
    'alpha_terpineol',
    'beta_caryophyllene',
    'beta_eudesmol',
    'beta_myrcene',
    'beta_pinene',
    'borneol',
    'camphene',
    'camphor',
    'caryophyllene_oxide',
    'cedrol',
    'cedrene',
    'cis_nerolidol',
    'citronellol',
    'd_limonene',
    'delta_3_carene',
    'eucalyptol',
    'fenchol',
    'fenchone',
    'gamma_terpinene',
    'gamma_terpineol',
    'geraniol',
    'geranyl_acetate',
    'guaiol',
    'isoborneol',
    'isopulegol',
    'linalool',
    'menthol',
    'nerol',
    'ocimene',
    'p_cymene',
    'phytol',
    'pulegone',
    'sabinene',
    'sabinene_hydrate',
    'terpinolene',
    'trans_beta_farnesene',
    'trans_nerolidol',
    'valencene',
]

HEAVY_METAL_KEYS = [
    'antimony',
    'arsenic',
    'cadmium',
    'chromium',
    'copper',
    'lead',
    'mercury',
    'nickel',
]

MICROBIAL_KEYS = [
    'aspergillus_flavus',
    'aspergillus_fumigatus',
    'aspergillus_niger',
    'aspergillus_terreus',
    'e_coli',
    'ochratoxin_a',
    'salmonella',
    'total_aerobic_bacteria',
    'total_aflatoxins',
    'total_coliforms',
    'total_yeast_and_mold',
]

PESTICIDE_KEYS = [
    'abamectin',
    'acephate',
    'acequinocyl',
    'acetamiprid',
    'aldicarb',
    'azadirachtin',
    'azoxystrobin',
    'bifenazate',
    'bifenthrin',
    'boscalid',
    'captan',
    'carbaryl',
    'carbofuran',
    'chlorantraniliprole',
    'chlordane',
    'chlorfenapyr',
    'chlormequat_chloride',
    'chlorpyrifos',
    'clofentezine',
    'coumaphos',
    'cyfluthrin',
    'cypermethrin',
    'daminozide',
    'diazinon',
    'dichlorvos',
    'dimethoate',
    'dimethomorph',
    'ethoprophos',
    'etofenprox',
    'etoxazole',
    'fenhexamid',
    'fenoxycarb',
    'fenpyroximate',
    'fipronil',
    'flonicamid',
    'fludioxonil',
    'hexythiazox',
    'imazalil',
    'imidacloprid',
    'indole_3_butyric_acid',
    'kresoxim_methyl',
    'malathion',
    'metalaxyl',
    'methiocarb',
    'methomyl',
    'methyl_parathion',
    'mevinphos',
    'mgk_264',
    'myclobutanil',
    'naled',
    'oxamyl',
    'paclobutrazol',
    'pentachloronitrobenzene',
    'permethrins',
    'phosmet',
    'piperonyl_butoxide',
    'prallethrin',
    'propiconazole',
    'propoxur',
    'pyrethrins',
    'pyridaben',
    'spinetoram',
    'spinosad',
    'spiromesifen',
    'spirotetramat',
    'spiroxamine',
    'tebuconazole',
    'thiacloprid',
    'thiamethoxam',
    'trifloxystrobin',
]

RESIDUAL_SOLVENT_KEYS = [
    '1_2_dichloroethane',
    '2_propanol',
    'acetone',
    'acetonitrile',
    'benzene',
    'chloroform',
    'dichloromethane',
    'dimethyl_sulfoxide',
    'ethanol',
    'ethyl_acetate',
    'ethyl_ether',
    'methanol',
    'n_heptane',
    'propane',
    'tetrafluoroethane',
    'toluene',
    'total_butanes',
    'total_hexanes',
    'total_pentanes',
    'total_xylenes',
    'trichloroethane',
]

MOISTURE_KEYS = [
    'moisture_content',
    'water_activity',
]

FOREIGN_MATTER_KEYS = [
    'foreign_matter',
    'mammal_excrement',
    'stems',
]

# Master analysis configuration: maps analysis names to their keys,
# search keywords (for locating relevant pages), and product-type filters.
ANALYSIS_CONFIGS = {
    'cannabinoids': {
        'keys': CANNABINOID_KEYS,
        'keywords': ['cannabinoid', 'potency', 'cannabinoids'],
        'product_types': None,  # All product types
    },
    'terpenes': {
        'keys': TERPENE_KEYS,
        'keywords': ['terpene', 'terpenoid', 'terpenes'],
        'product_types': None,
    },
    'pesticides': {
        'keys': PESTICIDE_KEYS,
        'keywords': ['pesticide', 'pyrethrin', 'pesticides'],
        'product_types': None,
    },
    'heavy_metals': {
        'keys': HEAVY_METAL_KEYS,
        'keywords': ['heavy metal', 'metals'],
        'product_types': None,
    },
    'microbials': {
        'keys': MICROBIAL_KEYS,
        'keywords': ['microbial', 'mycotoxin', 'aspergillus', 'microbiological'],
        'product_types': None,
    },
    'residual_solvents': {
        'keys': RESIDUAL_SOLVENT_KEYS,
        'keywords': ['residual solvent', 'solvents'],
        'product_types': ['concentrate', 'vape', 'edible', 'tincture'],
    },
    'moisture_foreign_matter': {
        'keys': MOISTURE_KEYS + FOREIGN_MATTER_KEYS,
        'keywords': ['moisture', 'water activity', 'foreign matter', 'foreign material'],
        'product_types': ['flower', 'preroll', 'infused'],
    },
}


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Analyte Key Normalization                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

ANALYTE_KEYS = {
    # Cannabinoids
    'thc': 'delta_9_thc',
    'd9thc': 'delta_9_thc',
    'delta_9_thc': 'delta_9_thc',
    'delta9thc': 'delta_9_thc',
    'd8thc': 'delta_8_thc',
    'delta_8_thc': 'delta_8_thc',
    'thca_a': 'thca',
    'thc_a': 'thca',
    'cbd_a': 'cbda',
    'cannabidiol': 'cbd',
    'cannabigerol': 'cbg',

    # Terpenes
    'myrcene': 'beta_myrcene',
    'b_myrcene': 'beta_myrcene',
    'beta_myrcene': 'beta_myrcene',
    'limonene': 'd_limonene',
    'd_limonene': 'd_limonene',
    'caryophyllene': 'beta_caryophyllene',
    'b_caryophyllene': 'beta_caryophyllene',
    'beta_caryophyllene': 'beta_caryophyllene',
    'a_pinene': 'alpha_pinene',
    'alpha_pinene': 'alpha_pinene',
    'b_pinene': 'beta_pinene',
    'beta_pinene': 'beta_pinene',
    'a_humulene': 'alpha_humulene',
    'humulene': 'alpha_humulene',
    'alpha_humulene': 'alpha_humulene',
    'a_bisabolol': 'alpha_bisabolol',
    'alpha_bisabolol': 'alpha_bisabolol',
}


def normalize_analyte_key(key: str) -> str:
    """Normalize an analyte key to standard format."""
    key_lower = str(key).lower().strip().replace(' ', '_').replace('-', '_')
    return ANALYTE_KEYS.get(key_lower, key_lower)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Pydantic Models for AI Structured Output                         ║
# ╚══════════════════════════════════════════════════════════════════╝

try:
    from pydantic import BaseModel as PydanticBaseModel

    class LabTestMetadata(PydanticBaseModel):
        """Pydantic model for AI-extracted COA metadata."""
        product_name: str = ''
        strain_name: str = ''
        product_type: str = ''
        date_tested: str = ''
        date_received: str = ''
        date_collected: str = ''
        batch_number: str = ''
        batch_size: float = 0.0
        lab: str = ''
        lab_license_number: str = ''
        lab_address: str = ''
        lab_city: str = ''
        lab_state: str = ''
        lab_zipcode: str = ''
        producer: str = ''
        producer_street: str = ''
        producer_city: str = ''
        producer_state: str = ''
        producer_zipcode: str = ''
        producer_license_number: str = ''
        distributor: str = ''
        distributor_license_number: str = ''
        sample_id: str = ''
        sample_weight: float = 0.0
        total_cannabinoids: float = 0.0
        total_cbd: float = 0.0
        total_thc: float = 0.0
        total_terpenes: float = 0.0
        status: str = ''
        analyses: list[str] = []

    class LabTestResult(PydanticBaseModel):
        """A single analyte measurement."""
        key: str
        name: str
        value: float = 0.0
        units: str = ''
        limit: float = 0.0
        lod: float = 0.0
        loq: float = 0.0
        status: str = ''

    class LabAnalysis(PydanticBaseModel):
        """Results for a specific analysis type."""
        analysis: str
        results: list[LabTestResult] = []

except ImportError:
    LabTestMetadata = None
    LabTestResult = None
    LabAnalysis = None


# ╔══════════════════════════════════════════════════════════════════╗
# ║ LabResult Dataclass (unchanged interface)                        ║
# ╚══════════════════════════════════════════════════════════════════╝

@dataclass
class LabResult:
    """Standard schema for a cannabis lab result record."""

    # ── Identifiers ──────────────────────────────────────────────
    id: Optional[str] = None
    sample_id: Optional[str] = None
    sample_hash: Optional[str] = None
    results_hash: Optional[str] = None

    # ── Product Information ──────────────────────────────────────
    product_name: Optional[str] = None
    product_type: Optional[str] = None
    product_subtype: Optional[str] = None
    strain_name: Optional[str] = None
    batch_number: Optional[str] = None
    batch_size: Optional[float] = None
    product_size: Optional[float] = None
    serving_size: Optional[float] = None
    servings_per_package: Optional[int] = None
    sample_weight: Optional[float] = None

    # ── Producer Information ─────────────────────────────────────
    producer: Optional[str] = None
    producer_license_number: Optional[str] = None
    producer_address: Optional[str] = None
    producer_street: Optional[str] = None
    producer_city: Optional[str] = None
    producer_county: Optional[str] = None
    producer_state: Optional[str] = None
    producer_zipcode: Optional[str] = None
    producer_latitude: Optional[float] = None
    producer_longitude: Optional[float] = None

    # ── Distributor Information ───────────────────────────────────
    distributor: Optional[str] = None
    distributor_license_number: Optional[str] = None
    distributor_address: Optional[str] = None
    distributor_street: Optional[str] = None
    distributor_city: Optional[str] = None
    distributor_county: Optional[str] = None
    distributor_state: Optional[str] = None
    distributor_zipcode: Optional[str] = None
    distributor_latitude: Optional[float] = None
    distributor_longitude: Optional[float] = None

    # ── Lab Information ──────────────────────────────────────────
    lab: Optional[str] = None
    lab_license_number: Optional[str] = None
    lab_id: Optional[str] = None
    lab_address: Optional[str] = None
    lab_street: Optional[str] = None
    lab_city: Optional[str] = None
    lab_county: Optional[str] = None
    lab_state: Optional[str] = None
    lab_zipcode: Optional[str] = None
    lab_latitude: Optional[float] = None
    lab_longitude: Optional[float] = None
    lab_phone: Optional[str] = None
    lab_website: Optional[str] = None

    # ── Dates ────────────────────────────────────────────────────
    date_tested: Optional[datetime] = None
    date_collected: Optional[datetime] = None
    date_received: Optional[datetime] = None
    date_produced: Optional[datetime] = None
    date_packaged: Optional[datetime] = None
    date_expires: Optional[datetime] = None

    # ── Analyses ─────────────────────────────────────────────────
    analyses: List[str] = field(default_factory=list)

    # ── Cannabinoids (percent) ───────────────────────────────────
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

    # ── Terpenes (percent) ───────────────────────────────────────
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

    # ── Contaminant Status ───────────────────────────────────────
    pesticides_status: Optional[str] = None
    heavy_metals_status: Optional[str] = None
    microbials_status: Optional[str] = None
    mycotoxins_status: Optional[str] = None
    residual_solvents_status: Optional[str] = None
    foreign_matter_status: Optional[str] = None
    moisture_content: Optional[float] = None
    water_activity: Optional[float] = None
    status: Optional[str] = None

    # ── Traceability ─────────────────────────────────────────────
    metrc_ids: List[str] = field(default_factory=list)
    metrc_lab_id: Optional[str] = None
    metrc_source_id: Optional[str] = None
    traceability_ids: List[Dict] = field(default_factory=list)

    # ── COA Information ──────────────────────────────────────────
    coa_url: Optional[str] = None
    coa_urls: List[Dict] = field(default_factory=list)
    coa_pdf: Optional[str] = None
    lab_results_url: Optional[str] = None

    # ── Classification ───────────────────────────────────────────
    indica_percentage: Optional[float] = None
    sativa_percentage: Optional[float] = None
    classification: Optional[str] = None

    # ── Additional Results ───────────────────────────────────────
    results: List[Dict] = field(default_factory=list)
    images: List[Dict] = field(default_factory=list)

    # ── Metadata ─────────────────────────────────────────────────
    state: Optional[str] = None
    source: Optional[str] = None
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
        key_data = (
            f"{self.product_name or ''}"
            f"{self.producer or ''}"
            f"{self.batch_number or ''}"
            f"{self.date_tested or ''}"
        )
        return hashlib.sha256(key_data.encode()).hexdigest()[:16]

    def _generate_hash(self) -> str:
        hash_data = {
            'product_name': self.product_name,
            'producer': self.producer,
            'batch_number': self.batch_number,
            'total_thc': self.total_thc,
            'date_tested': str(self.date_tested) if self.date_tested else None,
        }
        return hashlib.sha256(
            json.dumps(hash_data, sort_keys=True).encode()
        ).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LabResult':
        date_fields = [
            'date_tested', 'date_collected', 'date_received',
            'date_produced', 'date_packaged', 'date_expires',
            'created_at', 'updated_at', 'data_refreshed_date',
        ]
        for field_name in date_fields:
            if field_name in data and isinstance(data[field_name], str):
                try:
                    data[field_name] = datetime.fromisoformat(data[field_name])
                except (ValueError, TypeError):
                    data[field_name] = None
        return cls(**{
            k: v for k, v in data.items()
            if k in cls.__dataclass_fields__
        })


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Validation                                                       ║
# ╚══════════════════════════════════════════════════════════════════╝

VALIDATION_RULES = {
    'delta_9_thc': {'min': 0, 'max': 40, 'type': float},
    'delta_8_thc': {'min': 0, 'max': 40, 'type': float},
    'thca': {'min': 0, 'max': 40, 'type': float},
    'total_thc': {'min': 0, 'max': 100, 'type': float},
    'cbd': {'min': 0, 'max': 30, 'type': float},
    'cbda': {'min': 0, 'max': 30, 'type': float},
    'total_cbd': {'min': 0, 'max': 35, 'type': float},
    'total_cannabinoids': {'min': 0, 'max': 100, 'type': float},
    'total_terpenes': {'min': 0, 'max': 20, 'type': float},
    'moisture_content': {'min': 0, 'max': 20, 'type': float},
    'water_activity': {'min': 0, 'max': 1, 'type': float},
    'producer_latitude': {'min': -90, 'max': 90, 'type': float},
    'producer_longitude': {'min': -180, 'max': 180, 'type': float},
    'lab_latitude': {'min': -90, 'max': 90, 'type': float},
    'lab_longitude': {'min': -180, 'max': 180, 'type': float},
    'pesticides_status': {'values': ['pass', 'fail', 'nt', 'n/a', None]},
    'heavy_metals_status': {'values': ['pass', 'fail', 'nt', 'n/a', None]},
    'microbials_status': {'values': ['pass', 'fail', 'nt', 'n/a', None]},
    'residual_solvents_status': {'values': ['pass', 'fail', 'nt', 'n/a', None]},
    'status': {'values': ['pass', 'fail', 'nt', 'n/a', None]},
    'required': ['state'],
}


def validate_result(result: LabResult) -> tuple:
    """Validate a single result against rules."""
    errors = []
    data = result.to_dict()
    for field_name in VALIDATION_RULES.get('required', []):
        if not data.get(field_name):
            errors.append(f'Missing required field: {field_name}')
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
    """Normalize a status value to standard format."""
    if status is None:
        return None
    status_str = str(status).lower().strip()
    if status_str in ['pass', 'passed', 'passing', 'p', 'compliant', 'yes', 'true', '1']:
        return 'pass'
    elif status_str in ['fail', 'failed', 'failing', 'f', 'non-compliant', 'no', 'false', '0']:
        return 'fail'
    elif status_str in ['nt', 'not tested', 'n/t', 'not applicable', 'n/a', 'na', '-', '']:
        return 'nt'
    return status_str


def normalize_product_type(product_type: Any) -> Optional[str]:
    """Normalize a product type to standard categories."""
    if product_type is None:
        return None
    PRODUCT_TYPES = {
        'flower': ['flower', 'bud', 'buds', 'cannabis flower', 'dried flower',
                    'trim', 'shake', 'plant material', 'biomass'],
        'preroll': ['preroll', 'pre-roll', 'joint', 'blunt', 'prerolls', 'pre-rolls'],
        'infused': ['infused preroll', 'infused pre-roll', 'infused flower',
                     'enhanced preroll', 'moon rock', 'moonrock'],
        'concentrate': ['concentrate', 'extract', 'wax', 'shatter', 'rosin',
                        'live resin', 'budder', 'badder', 'sauce', 'diamonds',
                        'sugar', 'crumble', 'hash', 'kief', 'distillate', 'rso'],
        'vape': ['vape', 'cartridge', 'cart', 'vaporizer', 'pod', 'disposable', 'aio'],
        'edible': ['edible', 'gummy', 'chocolate', 'beverage', 'candy',
                   'baked goods', 'capsule', 'tablet', 'ingestible'],
        'tincture': ['tincture', 'oil', 'drops', 'sublingual'],
        'topical': ['topical', 'cream', 'lotion', 'balm', 'salve', 'transdermal'],
    }
    pt_lower = str(product_type).lower().strip()
    for standard_type, variations in PRODUCT_TYPES.items():
        if pt_lower in [v.lower() for v in variations]:
            return standard_type
    return product_type


# ╔══════════════════════════════════════════════════════════════════╗
# ║ ResultDetail                                                     ║
# ╚══════════════════════════════════════════════════════════════════╝

@dataclass
class ResultDetail:
    """Schema for individual test result within a COA."""
    analysis: str
    key: str
    name: Optional[str] = None
    value: Optional[float] = None
    mg_g: Optional[float] = None
    units: Optional[str] = None
    limit: Optional[float] = None
    lod: Optional[float] = None
    loq: Optional[float] = None
    status: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


if __name__ == '__main__':
    print(f'Cannabinoid keys: {len(CANNABINOID_KEYS)}')
    print(f'Terpene keys: {len(TERPENE_KEYS)}')
    print(f'Pesticide keys: {len(PESTICIDE_KEYS)}')
    print(f'Heavy metal keys: {len(HEAVY_METAL_KEYS)}')
    print(f'Microbial keys: {len(MICROBIAL_KEYS)}')
    print(f'Residual solvent keys: {len(RESIDUAL_SOLVENT_KEYS)}')
    print(f'Moisture keys: {len(MOISTURE_KEYS)}')
    print(f'Foreign matter keys: {len(FOREIGN_MATTER_KEYS)}')
    total = sum(len(v['keys']) for v in ANALYSIS_CONFIGS.values())
    print(f'Total unique analyte keys across all analyses: {total}')
    print('\n✓ Schema loaded successfully.')