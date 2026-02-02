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
| States Covered | 14+ |
| Data Sources | 26+ |
| Update Frequency | Weekly |

### Coverage by State

| State | Records | Sources | Status |
|-------|---------|---------|--------|
| AZ | 74,000+ | Curaleaf, Flow, Sticky Saguaro | ✅ Active |
| CA | 50,000+ | Flower Co, SC Labs, Glass House | ✅ Active |
| CT | 15,000+ | State Portal | ✅ Active |
| FL | 25,000+ | Flowery, Jungle Boys, Kaycha | ✅ Active |
| MA | 30,000+ | MCR Labs, PRR | ✅ Active |
| MI | 100,000+ | State Portal | ✅ Active |
| NV | 80,000+ | State Portal | ✅ Active |
| NY | 5,000+ | Cannabis Realm, PRR | ✅ Active |
| OR | 200,000+ | PRR | ✅ Active |
| WA | 300,000+ | State Portal | ✅ Active |
| *Others* | Various | PRR, Web | ✅ Active |

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
│   ├── base.py          # Base collector class
│   ├── get_results_az.py
│   ├── get_results_ca_flower_co.py
│   └── ...
├── config/               # Configuration and schema
│   ├── results_config.py
│   └── results_schema.py
├── data/                 # Final datasets (HuggingFace LFS)
│   ├── all/
│   │   └── all-results-latest.csv
│   ├── ca/
│   │   └── ca-results-latest.csv
│   └── ...
├── raw/                  # Raw public records data
├── analysis/             # Analysis scripts
├── .gitignore
├── .gitattributes
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

## 🤝 Contributing

We welcome contributions! See our [Contributing Guide](CONTRIBUTING.md) for details.

### Adding a New Data Source

1. Create a collector in `algorithms/get_results_{state}_{source}.py`
2. Inherit from `COACollector` base class
3. Implement the `get_results()` method
4. Add source configuration to `config/results_config.py`
5. Submit a pull request

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
