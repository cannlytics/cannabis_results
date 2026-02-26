# Cannabis Results

[![License: CC BY 4.0](https://img.shields.io/badge/License-CC_BY_4.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)
[![HuggingFace](https://img.shields.io/badge/🤗-HuggingFace-yellow.svg)](https://huggingface.co/datasets/cannlytics/cannabis_results)
[![GitHub](https://img.shields.io/badge/GitHub-Repository-black.svg)](https://github.com/cannlytics/cannabis_results)

A comprehensive, open-source collection of cannabis Certificates of Analysis (COAs) and raw lab results for consumer information, research, and regulatory transparency.

## 🎯 Purpose

Provide transparent access to cannabis laboratory test data so consumers can verify they are consuming clean, quality cannabis, and empower researchers, regulators, businesses, and consumers with accurate cannabis analytics.

## 🔗 Links

- **Code**: [GitHub Repository](https://github.com/cannlytics/cannabis_results)
- **Data**: [HuggingFace Dataset](https://huggingface.co/datasets/cannlytics/cannabis_results)

## 📊 Data Overview

| Metric | Value |
|--------|-------|
| Total Results | 995,000+ |
| States Covered | 18 |
| Data Sources | 28+ |
| Live Web Scrapers | 14 (3 refactored) |
| Static PRR Processors | 14 |
| Update Frequency | Ongoing |

### Coverage by State

| State | Records | Sources | Status |
|-------|---------|---------|--------|
| AK | 5,000+ | PRR | 📁 Static |
| AZ | 74,000+ | Curaleaf, Flow, Sticky Saguaro, High Grade, AZ Organix | ⚪ Needs verification |
| CA | 50,000+ | Flower Co, SC Labs, Raw Garden, Glass House | 🟢 Flower Co active; others degraded/broken |
| CT | 15,000+ | State Open Data Portal | ⚪ Needs verification |
| FL | 25,000+ | Flowery, Jungle Boys, Kaycha, TerpLife | ⚪ Needs verification |
| HI | 1,000+ | PRR | 📁 Static |
| MA | 30,000+ | MCR Labs, PRR | 📁 Static + ⚪ MCR needs verification |
| MD | 5,000+ | PRR (MMCC) | 📁 Static |
| MI | 100,000+ | PRR | 📁 Static |
| MS | 1,000+ | PRR | 📁 Static |
| NJ | 10,000+ | PRR | 📁 Static |
| NV | 80,000+ | PRR | 📁 Static |
| NY | 5,000+ | Cannabis Realm, Jetty, MyCOA, Hudson | ⚪ Needs verification |
| OH | 5,000+ | PRR | 📁 Static |
| OR | 200,000+ | PRR | 📁 Static |
| RI | 5,000+ | PRR | 📁 Static |
| UT | 5,000+ | PRR (COA PDFs) | 📁 Static |
| WA | 300,000+ | PRR (LCB) | 📁 Static |
| *Multi* | Various | Reddit COA posts | ⚪ Needs verification |

### Algorithm Status

Each collection algorithm has one of the following statuses:

- 🟢 **Operational** — Tested and working. Actively collecting data.
- 🟡 **Degraded** — Partially working. Source has changed; some data still collectible.
- 🔴 **Broken** — Source removed or fundamentally changed. Needs repair or retirement.
- 🔵 **Refactored** — Modernized to the new standardized pipeline (`results_base.COACollector`, `config.results_schema`).
- ⚪ **Legacy** — Functional but uses the old `cannlytics` package architecture. Queued for refactoring.
- 📁 **Static** — Processes files from public records requests. No live collection; works when data files are present.

> **⚠️ Urgency Note (Feb 2026):** COA sources are drying up. Glass House Farms removed their COAs entirely. Raw Garden shifted to ephemeral digital COAs. Companies can and do change their data-sharing policies without notice. *Collect with urgency — what's open today may be gone tomorrow.*

#### Web Scrapers (Live Collection)

| Status | Algorithm | State | Source | Method | Pipeline | Notes |
|--------|-----------|-------|--------|--------|----------|-------|
| 🟢🔵 | `get_results_ca_flower_co.py` | CA | [Flower Company](https://flowercompany.com/) | Selenium | New | Operational. Refactored. May be our only active CA source. |
| 🟡🔵 | `get_results_ca_rawgarden.py` | CA | [Raw Garden](https://rawgarden.farm/lab-results/) | requests | New | Most COAs now link to lab's digital LIMS instead of PDF. Some PDFs remain. Refactored. |
| 🔴🔵 | `get_results_ca_glass_house.py` | CA | [Glass House Farms](https://glasshousefarms.org/strains/) | requests | New | COAs appear to have been removed from website. Refactored but source is deprecated. |
| ⚪ | `get_results_ca_sclabs.py` | CA | [SC Labs](https://client.sclabs.com/verify/) | requests | Legacy | Brute-force URL enumeration by date/sample ID. Slow but comprehensive. Needs verification. |
| ⚪ | `get_results_az.py` | AZ | High Grade, Sticky Saguaro, AZ Organix, Flow Distribution, Curaleaf | Selenium | Legacy | 5 collectors in one file. Needs verification and per-source status check. |
| ⚪ | `get_results_ct.py` | CT | [CT Open Data Portal](https://data.ct.gov/) | API | Legacy | State government API. Generally stable. |
| ⚪ | `get_results_fl_flowery.py` | FL | [The Flowery](https://support.theflowery.co) | Selenium | Legacy | Needs verification. |
| ⚪ | `get_results_fl_jungleboys.py` | FL | [Jungle Boys FL](https://jungleboysflorida.com) | Selenium | Legacy | Needs verification. |
| ⚪ | `get_results_fl_kaycha.py` | FL | [Kaycha Labs (YourCOA)](https://yourcoa.com) | Selenium | Legacy | Lab LIMS portal. Needs verification. |
| ⚪ | `get_results_fl_terplife.py` | FL | [TerpLife Labs](https://www.terplifelabs.com) | Selenium | Legacy | Search-based enumeration. Needs verification. |
| ⚪ | `get_results_ma_mcrlabs.py` | MA | [MCR Labs](https://reports.mcrlabs.com) | cannlytics | Legacy | Uses `cannlytics` internal parsing algorithm. |
| ⚪ | `get_results_ny.py` | NY | Jetty Extracts, MyCOA, Hudson Cannabis | Selenium | Legacy | 3 collectors in one file. Needs verification. |
| ⚪ | `get_results_ny_cannabis_realm.py` | NY | [Cannabis Realm NY](https://cannabisrealmny.com/) | Selenium | Legacy | Needs verification. |
| ⚪ | `get_results_reddit.py` | Multi | Reddit COA posts | Selenium | Legacy | Parses COA images from cannabis subreddits. Multi-state. |

#### Public Records Processors (Static Files)

| Status | Algorithm | State | Source | Notes |
|--------|-----------|-------|--------|-------|
| 📁 | `get_results_ak.py` | AK | PRR (Alaska) | Processes Metrc export data from PRR. |
| 📁 | `get_results_hi.py` | HI | PRR (Hawaii) | Extracts samples from custom CSV format. |
| 📁 | `get_results_ma.py` | MA | PRR (Massachusetts) | Reads THC/THCA/yeast-mold CSV files from PRR. |
| 📁 | `get_results_md.py` | MD | PRR (Maryland MMCC) | Processes Maryland Medical Cannabis Commission data. |
| 📁 | `get_results_mi.py` | MI | PRR (Michigan) | Processes Michigan PRR data with analysis. |
| 📁 | `get_results_ms.py` | MS | PRR (Mississippi) | Processes Mississippi PRR data with analysis. |
| 📁 | `get_results_nj.py` | NJ | PRR (New Jersey) | Processes New Jersey PRR data with analysis. |
| 📁 | `get_results_nv.py` | NV | PRR (Nevada) | Curates Nevada PRR lab result data. |
| 📁 | `get_results_oh.py` | OH | PRR (Ohio) | Processes Ohio PRR data with analysis. |
| 📁 | `get_results_or.py` | OR | PRR (Oregon) | Curates Oregon PRR data (credit: Jamie Toth). |
| 📁 | `get_results_ri.py` | RI | PRR (Rhode Island) | Curates Rhode Island PRR data. |
| 📁 | `get_results_ut.py` | UT | PRR (Utah) | Parses Utah COA PDFs from PRR ZIP archives. |
| 📁 | `get_results_wa.py` | WA | PRR (Washington LCB) | Processes Washington LCB PRR data. |
| 📁 | `get_results_wa_inventory.py` | WA | PRR (Washington LCB) | Processes Washington inventory/traceability data from [Box](https://lcb.app.box.com/s/plb3dr2fvsuvgixb38g10tbwqos73biz). |

#### Orchestrator

| Status | Algorithm | Notes |
|--------|-----------|-------|
| ⚪ | `get_results.py` | Main entry point. Imports and runs collectors by state. Needs update to new pipeline. |

#### Refactoring Progress

| Milestone | Count | Details |
|-----------|-------|---------|
| ✅ Refactored (new pipeline) | 3 | `ca_flower_co`, `ca_glass_house`, `ca_rawgarden` |
| ⏳ Legacy (queued) | 11 | AZ, CT, FL×4, MA (MCR), NY×2, SC Labs, Reddit |
| 📁 Static (lower priority) | 14 | PRR processors — stable, refactor as needed |
| **Total algorithms** | **28** | Plus `get_results.py` orchestrator |

## 🚀 Quick Start

### Using HuggingFace Datasets

```python
from datasets import load_dataset

# Load all results
dataset = load_dataset("cannlytics/cannabis_results")

# Load specific state
ca_results = load_dataset(
    "cannlytics/cannabis_results",
    data_files="data/ca/ca-results-latest.csv"
)
```

### Using Pandas

```python
import pandas as pd

# Direct download from HuggingFace
url = "https://huggingface.co/datasets/cannlytics/cannabis_results/resolve/main/data/all/all-results-latest.csv"
df = pd.read_csv(url)
print(f"Loaded {len(df):,} results")
```

## 📁 Repository Structure

```
cannabis_results/
├── algorithms/           # Data collection scripts
│   ├── get_results.py   # Main orchestrator
│   ├── get_results_az.py
│   ├── get_results_ca_flower_co.py
│   ├── get_results_ca_glass_house.py
│   ├── get_results_ca_rawgarden.py
│   └── ...              # 28 collection algorithms total
├── config/               # Configuration and schema
│   ├── results_config.py # Paths, source config, product types
│   └── results_schema.py # LabResult dataclass, normalization
├── results_base.py       # COACollector base class (new pipeline)
├── tests/                # Test suite
│   ├── conftest.py      # Shared fixtures
│   └── test_collectors/
│       ├── test_ca_flower_co.py
│       ├── test_ca_glass_house.py
│       └── test_ca_rawgarden.py
├── data/                 # Final datasets (HuggingFace LFS)
│   ├── all/
│   │   └── all-results-latest.csv
│   ├── ca/
│   │   └── ca-results-latest.csv
│   └── ...
├── analysis/             # Analysis scripts (analyze_results_*.py)
├── raw/                  # Raw public records data
├── LICENSE
├── readme.md
└── requirements.txt
```

## 🔧 Development

### Prerequisites

- Python 3.9+
- Git LFS (for HuggingFace data)
- Ghostscript (for PDF parsing)

### Installation

```bash
# Clone the repository
git clone https://github.com/cannlytics/cannabis_results.git
cd cannabis_results

# Install dependencies
pip install -r requirements.txt

# Install Ghostscript (Windows)
# Download from: https://ghostscript.com/releases/gsdnld.html
# Add to PATH: C:\Program Files\gs\gsX.XX.X\bin
```

### Running Collectors

```python
from algorithms.get_results_ca_flower_co import FlowerCompanyCollector

collector = FlowerCompanyCollector(
    data_dir='D:/data/california/results',
    pdf_dir='D:/data/california/results/pdfs/flower-company',
)
results = collector.get_results()
```

## 📋 Data Schema

Each result contains standardized fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Unique identifier |
| `product_name` | str | Product name |
| `product_type` | str | flower, concentrate, vape, etc. |
| `producer` | str | Producer/cultivator name |
| `batch_number` | str | Batch identifier |
| `date_tested` | date | Test date |
| `total_thc` | float | Total THC (%) |
| `total_cbd` | float | Total CBD (%) |
| `total_terpenes` | float | Total terpenes (%) |
| `pesticides_status` | str | pass/fail/nt |
| `heavy_metals_status` | str | pass/fail/nt |
| `microbials_status` | str | pass/fail/nt |
| `coa_url` | str | Link to COA |
| `state` | str | State code |
| `source` | str | Data source |

See `config/results_schema.py` for the complete schema definition.

<!-- FIXME: I think we need to fix and re-unify our current data schema with our historic schema below.

| Field | Example| Description |
|-------|--------|-------------|
| `analyses` | ["cannabinoids"] | A list of analyses performed on a given sample. |
| `{analysis}_method` | "HPLC" | The method used for each analysis. |
| `{analysis}_status` | "pass" | The pass, fail, or N/A status for pass / fail analyses.   |
| `coa_urls` | [{"url": "", "filename": ""}] | A list of certificate of analysis (CoA) URLs. |
| `date_collected` | 2022-04-20T04:20 | An ISO-formatted time when the sample was collected. |
| `date_tested` | 2022-04-20T16:20 | An ISO-formatted time when the sample was tested. |
| `date_received` | 2022-04-20T12:20 | An ISO-formatted time when the sample was received. |
| `distributor` | "Your Favorite Dispo" | The name of the product distributor, if applicable. |
| `distributor_address` | "Under the Bridge, SF, CA 55555" | The distributor address, if applicable. |
| `distributor_street` | "Under the Bridge" | The distributor street, if applicable. |
| `distributor_city` | "SF" | The distributor city, if applicable. |
| `distributor_state` | "CA" | The distributor state, if applicable. |
| `distributor_zipcode` | "55555" | The distributor zip code, if applicable. |
| `distributor_license_number` | "L2Stat" | The distributor license number, if applicable. |
| `images` | [{"url": "", "filename": ""}] | A list of image URLs for the sample. |
| `lab_results_url` | "https://cannlytics.com/results" | A URL to the sample results online. |
| `producer` | "Grow Tent" | The producer of the sampled product. |
| `producer_address` | "3rd & Army, SF, CA 55555" | The producer's address. |
| `producer_street` | "3rd & Army" | The producer's street. |
| `producer_city` | "SF" | The producer's city. |
| `producer_state` | "CA" | The producer's state. |
| `producer_zipcode` | "55555" | The producer's zipcode. |
| `producer_license_number` | "L2Calc" | The producer's license number. |
| `product_name` | "Blue Rhino Pre-Roll" | The name of the product. |
| `lab_id` | "Sample-0001" | A lab-specific ID for the sample. |
| `product_type` | "flower" | The type of product. |
| `batch_number` | "Order-0001" | A batch number for the sample or product. |
| `metrc_ids` | ["1A4060300002199000003445"] | A list of relevant Metrc IDs. |
| `metrc_lab_id` | "1A4060300002199000003445" | The Metrc ID associated with the lab sample. |
| `metrc_source_id` | "1A4060300002199000003445" | The Metrc ID associated with the sampled product. |
| `product_size` | 2000 | The size of the product in milligrams. |
| `serving_size` | 1000 | An estimated serving size in milligrams. |
| `servings_per_package` | 2 | The number of servings per package. |
| `sample_weight` | 1 | The weight of the product sample in grams. |
| `results` | [{...},...] | A list of results, see below for result-specific fields. |
| `status` | "pass" | The overall pass / fail status for all contaminant screening analyses. |
| `total_cannabinoids` | 14.20 | The analytical total of all cannabinoids measured. |
| `total_thc` | 14.00 | The analytical total of THC and THCA. |
| `total_cbd` | 0.20 | The analytical total of CBD and CBDA. |
| `total_terpenes` | 0.42 | The sum of all terpenes measured. |
| `results_hash` | "{sha256-hash}" | An HMAC of the sample's `results` JSON signed with Cannlytics' public key, `"cannlytics.eth"`. |
| `sample_id` | "{sha256-hash}" | A generated ID to uniquely identify the `producer`, `product_name`, and `results`. |
| `sample_hash` | "{sha256-hash}" | An HMAC of the entire sample JSON signed with Cannlytics' public key, `"cannlytics.eth"`. |
| `strain_name` | "Blue Rhino" | A strain name, if specified. Otherwise, can be attempted to be parsed from the `product_name`. |

Each result can contain the following fields.

| Field | Example| Description |
|-------|--------|-------------|
| `analysis` | "pesticides" | The analysis used to obtain the result. |
| `key` | "pyrethrins" | A standardized key for the result analyte. |
| `name` | "Pyrethrins" | The lab's internal name for the result analyte |
| `value` | 0.42 | The value of the result. |
| `mg_g` | 0.00000042 | The value of the result in milligrams per gram. |
| `units` | "ug/g" | The units for the result `value`, `limit`, `lod`, and `loq`. |
| `limit` | 0.5 | A pass / fail threshold for contaminant screening analyses. |
| `lod` | 0.01 | The limit of detection for the result analyte. Values below the `lod` are typically reported as `ND`. |
| `loq` | 0.1 | The limit of quantification for the result analyte. Values above the `lod` but below the `loq` are typically reported as `<LOQ`. |
| `status` | "pass" | The pass / fail status for contaminant screening analyses. |

 -->

## 🤝 Contributing

We welcome contributions! See our [Contributing Guide](CONTRIBUTING.md) for details.

### Adding a New Data Source

1. Create a collector in `algorithms/get_results_{state}_{source}.py`
2. Inherit from `results_base.COACollector`
3. Implement the `get_results()` method
4. Use `config.results_schema.LabResult` for output standardization
5. Add source configuration to `config/results_config.py`
6. Create a test file in `tests/test_collectors/test_{state}_{source}.py`
7. Submit a pull request

## 📬 Contact

- **Email**: dev@cannlytics.com
- **GitHub**: [@cannlytics](https://github.com/cannlytics)

## 📄 License

[Creative Commons Attribution 4.0 International (CC BY 4.0)](LICENSE)

You are free to:
- **Share** — copy and redistribute the data in any medium or format
- **Adapt** — remix, transform, and build upon the data for any purpose, even commercially

Under the following terms:
- **Attribution** — You must give appropriate credit to Cannlytics, provide a link to the license, and indicate if changes were made.

**Suggested citation:**
```
Cannlytics. (2026). Cannabis Results Dataset. 
https://huggingface.co/datasets/cannlytics/cannabis_results
```

Data is sourced from public records and publicly available sources.

---

© 2024-2026 Cannlytics — Simple Cannabis Analytics