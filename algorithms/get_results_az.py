"""
Get Results | Arizona (Coordinator)
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 8/24/2024
Updated: 2/28/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Coordinate the collection of Arizona cannabis lab result COA PDFs
    from multiple data sources.

    This module serves as the orchestration layer for all Arizona-
    specific COA collection algorithms. Each source is implemented
    as an independent collector module following the standardized
    four-phase pipeline pattern.

    Arizona Sources:
        ┌─────────────────────────────────────────────────────────────┐
        │ Source               │ Module                    │ Status   │
        ├─────────────────────────────────────────────────────────────┤
        │ Sticky Saguaro       │ get_results_az_sticky_... │ Active   │
        │ Flow Distribution    │ get_results_az_flow_d...  │ Active   │
        │ Curaleaf*            │ get_results_curaleaf      │ Active   │
        │ High Grade           │ (deprecated)              │ Inactive │
        │ Arizona Organix      │ (deprecated)              │ Inactive │
        └─────────────────────────────────────────────────────────────┘

        * Curaleaf is a multi-state operator. Its collector is in
          `get_results_curaleaf.py` (not AZ-specific). COAs from
          Curaleaf span multiple states and are filtered downstream.

    Historical Yield:
        57,785 COA PDFs collected across all AZ sources to date.

    Architecture Decision:
        Each source was originally aggregated into a single file
        due to their relatively simple collection patterns. However,
        following the established FL pattern (MÜV, Kaycha, Flowery,
        etc.), each source is now its own module for:
          - Independent debugging and maintenance
          - Isolated failure domains (one source breaking doesn't
            affect others)
          - Clearer test organization
          - Easier onboarding of new contributors

Usage:
    ```python
    from algorithms.get_results_az import collect_all_az

    results = collect_all_az()
    ```

Command Line:
    ```bash
    # Run all active AZ sources
    python algorithms/get_results_az.py

    # Run a specific source
    python algorithms/get_results_az.py --source sticky-saguaro
    python algorithms/get_results_az.py --source flow-distribution

    # Catalog only (no network)
    python algorithms/get_results_az.py --catalog-only

    # Run unit tests for all AZ modules
    python algorithms/get_results_az.py --test
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


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Constants                                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

# Active AZ sources (in order of reliability/priority).
ACTIVE_SOURCES = [
    'sticky-saguaro',
    'flow-distribution',
]

# Multi-state sources that include AZ data.
MULTI_STATE_SOURCES = [
    'curaleaf',
]

# Deprecated sources (preserved for historical reference).
DEPRECATED_SOURCES = [
    'high-grade',
    'arizona-organix',
]

# Default data directory.
DEFAULT_DATA_DIR = 'D:/data/arizona/results'

# Module-level logger.
logger = logging.getLogger('get_results_az')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Source Registry                                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

def _get_collector(source: str, data_dir: str = DEFAULT_DATA_DIR):
    """Get the collector instance for a given source.

    Args:
        source: Source identifier (e.g., 'sticky-saguaro').
        data_dir: Base data directory.

    Returns:
        Collector instance or None if unavailable.
    """
    if source == 'sticky-saguaro':
        try:
            from algorithms.get_results_az_sticky_saguaro import (
                StickySaguaroCollector,
            )
            return StickySaguaroCollector(
                pdf_dir=os.path.join(
                    data_dir, 'pdfs', 'sticky-saguaro',
                ),
                data_dir=data_dir,
            )
        except ImportError:
            # Try relative import for standalone use.
            try:
                from get_results_az_sticky_saguaro import (
                    StickySaguaroCollector,
                )
                return StickySaguaroCollector(
                    pdf_dir=os.path.join(
                        data_dir, 'pdfs', 'sticky-saguaro',
                    ),
                    data_dir=data_dir,
                )
            except ImportError:
                logger.error(
                    'StickySaguaroCollector not available'
                )
                return None

    elif source == 'flow-distribution':
        try:
            from algorithms.get_results_az_flow_distribution import (
                FlowDistributionCollector,
            )
            return FlowDistributionCollector(
                pdf_dir=os.path.join(
                    data_dir, 'pdfs', 'flow-distribution',
                ),
                data_dir=data_dir,
            )
        except ImportError:
            try:
                from get_results_az_flow_distribution import (
                    FlowDistributionCollector,
                )
                return FlowDistributionCollector(
                    pdf_dir=os.path.join(
                        data_dir, 'pdfs', 'flow-distribution',
                    ),
                    data_dir=data_dir,
                )
            except ImportError:
                logger.error(
                    'FlowDistributionCollector not available'
                )
                return None

    elif source == 'curaleaf':
        try:
            from algorithms.get_results_curaleaf import (
                CuraleafCollector,
            )
            return CuraleafCollector(
                pdf_dir=os.path.join(
                    data_dir, 'pdfs', 'curaleaf',
                ),
                data_dir=data_dir,
            )
        except ImportError:
            try:
                from get_results_curaleaf import CuraleafCollector
                return CuraleafCollector(
                    pdf_dir=os.path.join(
                        data_dir, 'pdfs', 'curaleaf',
                    ),
                    data_dir=data_dir,
                )
            except ImportError:
                logger.error('CuraleafCollector not available')
                return None

    else:
        logger.error(f'Unknown source: {source}')
        return None


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Collection Functions                                             ║
# ╚══════════════════════════════════════════════════════════════════╝

def collect_source(
        source: str,
        data_dir: str = DEFAULT_DATA_DIR,
        catalog_only: bool = False,
        headless: bool = True,
        **kwargs,
    ) -> pd.DataFrame:
    """Collect COAs from a single source.

    Args:
        source: Source identifier.
        data_dir: Base data directory.
        catalog_only: If True, only build manifests.
        headless: Run Selenium in headless mode.
        **kwargs: Additional arguments for the collector.

    Returns:
        DataFrame with collected results.
    """
    logger.info(f'Collecting from source: {source}')

    collector = _get_collector(source, data_dir)
    if collector is None:
        logger.error(f'Failed to initialize collector: {source}')
        return pd.DataFrame()

    try:
        with collector:
            results = collector.get_results(
                catalog_only=catalog_only,
                headless=headless,
                **kwargs,
            )
        logger.info(
            f'Source {source}: {len(results)} results collected'
        )
        return results
    except Exception as exc:
        logger.error(f'Error collecting from {source}: {exc}')
        return pd.DataFrame()


def collect_all_az(
        data_dir: str = DEFAULT_DATA_DIR,
        include_multi_state: bool = False,
        catalog_only: bool = False,
        headless: bool = True,
        sources: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
    """Collect COAs from all active Arizona sources.

    Args:
        data_dir: Base data directory.
        include_multi_state: Include multi-state sources (Curaleaf).
        catalog_only: If True, only build manifests.
        headless: Run Selenium in headless mode.
        sources: Override list of sources to collect.

    Returns:
        Dictionary mapping source name → results DataFrame.
    """
    if sources is None:
        sources = list(ACTIVE_SOURCES)
        if include_multi_state:
            sources.extend(MULTI_STATE_SOURCES)

    all_results = {}
    for source in sources:
        results = collect_source(
            source,
            data_dir=data_dir,
            catalog_only=catalog_only,
            headless=headless,
        )
        all_results[source] = results

    # Summary.
    total = sum(len(r) for r in all_results.values())
    logger.info(
        f'Arizona collection complete: '
        f'{total} total results from {len(all_results)} sources'
    )

    return all_results


def get_az_summary(data_dir: str = DEFAULT_DATA_DIR) -> Dict:
    """Get a summary of all AZ data collections.

    Args:
        data_dir: Base data directory.

    Returns:
        Dictionary with per-source statistics.
    """
    summary = {}
    for source in ACTIVE_SOURCES + MULTI_STATE_SOURCES:
        collector = _get_collector(source, data_dir)
        if collector and hasattr(collector, 'archive_stats'):
            try:
                summary[source] = collector.archive_stats()
            except Exception:
                summary[source] = {'error': 'stats unavailable'}
        else:
            summary[source] = {'status': 'not available'}
    return summary


# ╔══════════════════════════════════════════════════════════════════╗
# ║ CLI Entry Point                                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

if __name__ == '__main__':
    import argparse

    # Configure logging.
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S',
    )

    parser = argparse.ArgumentParser(
        description='Collect Arizona cannabis COA PDFs.',
    )
    parser.add_argument(
        '--data-dir',
        default=DEFAULT_DATA_DIR,
        help='Base data directory.',
    )
    parser.add_argument(
        '--source',
        choices=ACTIVE_SOURCES + MULTI_STATE_SOURCES,
        default=None,
        help='Run a specific source only.',
    )
    parser.add_argument(
        '--include-multi-state',
        action='store_true',
        help='Include multi-state sources (Curaleaf).',
    )
    parser.add_argument(
        '--catalog-only',
        action='store_true',
        help='Only catalog existing PDFs.',
    )
    parser.add_argument(
        '--no-headless',
        action='store_true',
        help='Show browser windows.',
    )
    parser.add_argument(
        '--summary',
        action='store_true',
        help='Print archive summary and exit.',
    )
    parser.add_argument(
        '--test',
        action='store_true',
        help='Run unit tests for all AZ modules.',
    )
    args = parser.parse_args()

    if args.test:
        # Run all module unit tests.
        print('Running unit tests for all AZ modules...')
        print()

        print('=== Sticky Saguaro ===')
        try:
            from get_results_az_sticky_saguaro import run_unit_tests
            run_unit_tests()
        except ImportError:
            print('  (module not found)')
        print()

        print('=== Flow Distribution ===')
        try:
            from get_results_az_flow_distribution import (
                run_unit_tests as run_flow_tests,
            )
            run_flow_tests()
        except ImportError:
            print('  (module not found)')
        print()

        print('=== Curaleaf ===')
        try:
            from get_results_curaleaf import (
                run_unit_tests as run_curaleaf_tests,
            )
            run_curaleaf_tests()
        except ImportError:
            print('  (module not found)')
        print()

        print('All AZ module tests complete.')
        exit(0)

    if args.summary:
        summary = get_az_summary(args.data_dir)
        for source, stats in summary.items():
            print(f'\n{source}:')
            for k, v in stats.items():
                print(f'  {k}: {v}')
        exit(0)

    if args.source:
        results = collect_source(
            args.source,
            data_dir=args.data_dir,
            catalog_only=args.catalog_only,
            headless=not args.no_headless,
        )
        print(f'{args.source}: {len(results)} results')
    else:
        all_results = collect_all_az(
            data_dir=args.data_dir,
            include_multi_state=args.include_multi_state,
            catalog_only=args.catalog_only,
            headless=not args.no_headless,
        )
        for source, results in all_results.items():
            print(f'{source}: {len(results)} results')