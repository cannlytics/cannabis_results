"""
Get Results | New York (Coordinator)
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 6/24/2024
Updated: 2/28/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Orchestrate all New York cannabis COA collection algorithms.
    Coordinates four independent data source collectors:

    1. Cannabis Realm (primary) — Dispensary menu scraping
    2. Jetty Extracts — Google Drive folder downloads
    3. MyCOA / MFNY — Dropbox link downloads
    4. Hudson Cannabis — Google Drive file downloads

    Each source is an independent module that can run standalone
    or be orchestrated through this coordinator.

Data Sources:
    - [Cannabis Realm NY](https://cannabisrealmny.com/)
    - [Jetty Extracts](https://jettyextracts.com/coa-new-york/)
    - [MyCOA / MFNY](https://www.mycoa.info/)
    - [Hudson Cannabis](https://www.hudsoncannabis.co/coas)

Usage:
    ```python
    from algorithms.get_results_ny import run_ny_collection

    results = run_ny_collection()
    ```

Command Line:
    ```bash
    # Run all NY sources
    python algorithms/get_results_ny.py

    # Run specific source
    python algorithms/get_results_ny.py --source cannabis-realm
    python algorithms/get_results_ny.py --source jetty-extracts
    python algorithms/get_results_ny.py --source mycoa
    python algorithms/get_results_ny.py --source hudson-cannabis

    # Catalog-only mode (all sources)
    python algorithms/get_results_ny.py --catalog-only

    # Summary of local archives
    python algorithms/get_results_ny.py --summary

    # Run unit tests for all modules
    python algorithms/get_results_ny.py --test
    ```
"""
# Standard imports:
from datetime import datetime
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

# External imports:
import pandas as pd

# Source module imports.
try:
    from get_results_ny_cannabis_realm import (
        CannabisRealmCollector,
        _run_tests as _test_cannabis_realm,
    )
except ImportError:
    CannabisRealmCollector = None
    _test_cannabis_realm = None

try:
    from get_results_ny_jetty_extracts import (
        JettyExtractsCollector,
        _run_tests as _test_jetty_extracts,
    )
except ImportError:
    JettyExtractsCollector = None
    _test_jetty_extracts = None

try:
    from get_results_ny_mycoa import (
        MycoaCollector,
        _run_tests as _test_mycoa,
    )
except ImportError:
    MycoaCollector = None
    _test_mycoa = None

try:
    from get_results_ny_hudson_cannabis import (
        HudsonCannabisCollector,
        _run_tests as _test_hudson_cannabis,
    )
except ImportError:
    HudsonCannabisCollector = None
    _test_hudson_cannabis = None


# Module-level logger.
logger = logging.getLogger('get_results_ny')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Source Registry                                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

SOURCE_REGISTRY = {
    'cannabis-realm': {
        'collector_class': CannabisRealmCollector,
        'test_fn': _test_cannabis_realm,
        'pdf_subdir': 'cannabis-realm',
        'description': 'Cannabis Realm NY dispensary menu',
        'priority': 1,
    },
    'jetty-extracts': {
        'collector_class': JettyExtractsCollector,
        'test_fn': _test_jetty_extracts,
        'pdf_subdir': 'jetty-extracts',
        'description': 'Jetty Extracts Google Drive COAs',
        'priority': 2,
    },
    'mycoa': {
        'collector_class': MycoaCollector,
        'test_fn': _test_mycoa,
        'pdf_subdir': 'my-coa',
        'description': 'MyCOA / MFNY Dropbox COAs',
        'priority': 3,
    },
    'hudson-cannabis': {
        'collector_class': HudsonCannabisCollector,
        'test_fn': _test_hudson_cannabis,
        'pdf_subdir': 'hudson-cannabis',
        'description': 'Hudson Cannabis Google Drive COAs',
        'priority': 4,
    },
}


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Coordinator Functions                                            ║
# ╚══════════════════════════════════════════════════════════════════╝

def run_ny_collection(
        data_dir: str = '',
        source: Optional[str] = None,
        catalog_only: bool = False,
        headless: bool = True,
        verbose: bool = True,
    ) -> pd.DataFrame:
    """Run New York COA collection across all or specified sources.

    Args:
        data_dir: Base data directory for NY results.
        source: Specific source to run (None = all).
        catalog_only: Only catalog existing PDFs.
        headless: Run browsers headlessly.
        verbose: Enable verbose logging.

    Returns:
        Combined DataFrame of all LabResult records.
    """
    if not data_dir:
        data_dir = 'D:/data/new-york/results'

    # Configure logging.
    log = logging.getLogger('get_results_ny')
    if not log.handlers:
        handler = logging.StreamHandler()
        fmt = (
            '%(asctime)s | %(name)s | %(levelname)s | %(message)s'
        )
        handler.setFormatter(
            logging.Formatter(fmt, datefmt='%Y-%m-%dT%H:%M:%S'),
        )
        log.addHandler(handler)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)

    # Determine which sources to run.
    if source:
        if source not in SOURCE_REGISTRY:
            log.error(
                'Unknown source: %s. Available: %s',
                source, ', '.join(SOURCE_REGISTRY.keys()),
            )
            return pd.DataFrame()
        sources_to_run = {source: SOURCE_REGISTRY[source]}
    else:
        sources_to_run = SOURCE_REGISTRY

    all_results = []

    for name, config in sorted(
        sources_to_run.items(),
        key=lambda x: x[1]['priority'],
    ):
        collector_class = config['collector_class']
        if collector_class is None:
            log.warning(
                'Skipping %s (module not importable)', name,
            )
            continue

        log.info('=' * 60)
        log.info('Running source: %s', name)
        log.info('=' * 60)

        pdf_dir = os.path.join(
            data_dir, 'pdfs', config['pdf_subdir'],
        )

        try:
            with collector_class(
                pdf_dir=pdf_dir,
                data_dir=data_dir,
                verbose=verbose,
            ) as collector:
                results = collector.get_results(
                    catalog_only=catalog_only,
                    headless=headless,
                )
                if len(results) > 0:
                    all_results.append(results)
                    log.info(
                        '%s: %d results', name, len(results),
                    )
                else:
                    log.info('%s: 0 results', name)

        except Exception as e:
            log.error(
                'Error running %s: %s', name, str(e),
            )
            continue

    # Combine all results.
    if all_results:
        combined = pd.concat(all_results, ignore_index=True)
    else:
        combined = pd.DataFrame()

    log.info('=' * 60)
    log.info(
        'NY collection complete: %d total results', len(combined),
    )
    log.info('=' * 60)

    # Save combined results.
    if len(combined) > 0:
        datasets_dir = os.path.join(data_dir, 'datasets')
        os.makedirs(datasets_dir, exist_ok=True)
        output_path = os.path.join(
            datasets_dir, 'ny-all-results.csv',
        )
        combined.to_csv(output_path, index=False)
        log.info('Combined: %d records → %s', len(combined), output_path)

    return combined


def get_ny_archive_summary(
        data_dir: str = '',
    ) -> Dict:
    """Get archive statistics across all NY sources.

    Args:
        data_dir: Base data directory.

    Returns:
        Dictionary with per-source and aggregate stats.
    """
    if not data_dir:
        data_dir = 'D:/data/new-york/results'

    summary = {'sources': {}, 'totals': {
        'total_pdfs': 0, 'total_size_mb': 0.0,
    }}

    for name, config in SOURCE_REGISTRY.items():
        collector_class = config['collector_class']
        if collector_class is None:
            continue

        pdf_dir = os.path.join(
            data_dir, 'pdfs', config['pdf_subdir'],
        )
        try:
            collector = collector_class(
                pdf_dir=pdf_dir, data_dir=data_dir,
            )
            stats = collector.archive_stats()
            summary['sources'][name] = stats
            summary['totals']['total_pdfs'] += stats.get(
                'total_pdfs', 0,
            )
            summary['totals']['total_size_mb'] += stats.get(
                'total_size_mb', 0.0,
            )
        except Exception:
            summary['sources'][name] = {'error': 'unavailable'}

    summary['totals']['total_size_mb'] = round(
        summary['totals']['total_size_mb'], 2,
    )
    return summary


# ╔══════════════════════════════════════════════════════════════════╗
# ║ CLI Entry Point                                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='New York Cannabis COA Collection Coordinator',
    )
    parser.add_argument(
        '--source',
        choices=list(SOURCE_REGISTRY.keys()),
        default=None,
        help='Run specific source (default: all)',
    )
    parser.add_argument(
        '--data-dir', default='',
        help='Base data directory',
    )
    parser.add_argument(
        '--catalog-only', action='store_true',
        help='Only catalog existing PDFs',
    )
    parser.add_argument(
        '--headless', action='store_true', default=True,
        help='Run browsers headlessly',
    )
    parser.add_argument(
        '--no-headless', action='store_true',
        help='Show browser windows',
    )
    parser.add_argument(
        '--summary', action='store_true',
        help='Show archive summary statistics',
    )
    parser.add_argument(
        '--test', action='store_true',
        help='Run unit tests for all modules',
    )
    args = parser.parse_args()

    if args.test:
        print('Running unit tests for all NY modules...\n')
        test_fns = [
            ('Cannabis Realm', _test_cannabis_realm),
            ('Jetty Extracts', _test_jetty_extracts),
            ('MyCOA', _test_mycoa),
            ('Hudson Cannabis', _test_hudson_cannabis),
        ]
        for name, fn in test_fns:
            if fn is not None:
                print(f'=== {name} ===')
                fn()
                print()
            else:
                print(f'=== {name} === (skipped: not importable)')
        print('All NY module tests complete.')
        raise SystemExit(0)

    if args.summary:
        summary = get_ny_archive_summary(
            data_dir=args.data_dir,
        )
        print('\n📊 New York Archive Summary')
        print('=' * 50)
        for name, stats in summary['sources'].items():
            if 'error' in stats:
                print(f'  {name}: {stats["error"]}')
            else:
                print(
                    f'  {name}: {stats.get("total_pdfs", 0)} PDFs '
                    f'({stats.get("total_size_mb", 0)} MB)',
                )
        print('-' * 50)
        print(
            f'  TOTAL: {summary["totals"]["total_pdfs"]} PDFs '
            f'({summary["totals"]["total_size_mb"]} MB)',
        )
        raise SystemExit(0)

    headless = args.headless and not args.no_headless

    results = run_ny_collection(
        data_dir=args.data_dir,
        source=args.source,
        catalog_only=args.catalog_only,
        headless=headless,
    )
    print(f'\nTotal results: {len(results)}')