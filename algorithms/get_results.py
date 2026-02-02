"""
Get Results
Copyright (c) 2024 Cannlytics

Author: Keegan Skeate <https://github.com/keeganskeate>
Created: 12/8/2024
Updated: 12/10/2024
License: MIT

Description:

    Collect cannabis lab result COAs from multiple data sources.

"""
# Standard imports:
import os

# External imports:
import pandas as pd

# Internal imports:
from cannlytics.data.collectors import COACollector

# Collectors:
from get_results_az import (
    HighGradeCollector,
    StickySaguaroCollector,
    ArizonaOrganixCollector,
    FlowDistributionCollector,
    CuraleafCollector,
)
from get_results_ca_flower_co import FlowerCompanyCollector
from get_results_ca_glass_house import GlassHouseFarmsCollector
from get_results_ca_rawgarden import RawGardenCollector
from get_results_fl_flowery import FloweryCollector
from get_results_fl_jungleboys import JungleBoysCollector
from get_results_fl_kaycha import KaychaLabsCollector
from get_results_fl_terplife import TerpLifeLabsCollector
from get_results_ny import (
    JettyExtractsCollector,
    MyCOACollector,
    HudsonCannabisCollector,
)


def get_results(
        id: str,
        data_dir: str,
        cache_dir: str,
        collectors: dict[str, type[COACollector]],
    ) -> dict[str, pd.DataFrame]:
    """Collect cannabis lab result COAs from multiple sources."""
    results = {}
    for source, collector_class in collectors.items():
        cache_path = os.path.join(cache_dir, f'results-{id}-{source}.jsonl')
        pdf_dir = os.path.join(data_dir, 'pdfs', source)
        log_name = f'get_results_{id}_{source}'
        collector = collector_class(
            data_dir,
            cache_path=cache_path,
            pdf_dir=pdf_dir,
            log_name=log_name,
        )
        results[source] = collector.get_results()
    return results


# === Tests ===
# [✓] Tested: 2024-12-10 by Keegan Skeate <keegan@cannlytics>
if __name__ == '__main__':

    # Get Arizona results.
    results = get_results(
        id='az',
        data_dir='D://data/arizona',
        cache_path='D://data/.cache/results-az.jsonl',
        collectors = {
            'high-grade': HighGradeCollector,
            'sticky-saguaro': StickySaguaroCollector,
            'arizona-organix': ArizonaOrganixCollector,
            'flow-distribution': FlowDistributionCollector,
            'curaleaf': CuraleafCollector,
        }
    )

    # Get California results.
    results = get_results(
        id='ca',
        data_dir='D://data/california/results',
        cache_dir='D://data/.cache',
        collectors = {
            'flower-company': FlowerCompanyCollector,
            'glass-house-farms': GlassHouseFarmsCollector,
            'rawgarden': RawGardenCollector,
            # TODO:
            # - SC Labs
        }
    )

    # Get Florida results.
    results = get_results(
        id='fl',
        data_dir='D://data/florida/results',
        cache_dir='D://data/.cache',
        collectors = {
            'flowery': FloweryCollector,
            'jungleboys': JungleBoysCollector,
            'terplife': TerpLifeLabsCollector,
            'kaycha': KaychaLabsCollector,
        }
    )

    # Get New York results.
    results = get_results(
        id='ny',
        data_dir='D://data/new-york/results',
        cache_dir='D://data/.cache',
        collectors = {
            'jetty-extracts': JettyExtractsCollector,
            'my-coa': MyCOACollector,
            'hudson-cannabis': HudsonCannabisCollector
        }
    )
