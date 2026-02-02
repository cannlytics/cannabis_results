"""
Upload Producers
Copyright (c) 2024 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 12/17/2024
Updated: 12/17/2024
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>
"""
# Standard imports:
import json
import math
import os
import re
from time import sleep

# External imports:
from cannlytics.ai.embeddings import get_embedding
from cannlytics.ai.gen import text_to_color_ai
from cannlytics.data import create_hash
from cannlytics.data.cache import Bogart
from cannlytics.firebase import (
    initialize_firebase,
    update_document,
    upload_file,
)
from cannlytics.utils import kebab_case
from cannlytics.utils.constants import states
from datasets import load_dataset
from dotenv import dotenv_values
from openai import OpenAI
import pandas as pd



# === Tests ===
if __name__ == '__main__':

    # Read results data.
    cache_path = os.path.join(cache_dir, f'coas.jsonl')
    results = read_cache(cache_path, desired_fields)
    print('Total number of results:', len(results))

    # TODO: Find unique licenses in parsed results.


    # TODO: Cross-reference licenses with the database.


    # TODO: Get data for unidentified licenses.
