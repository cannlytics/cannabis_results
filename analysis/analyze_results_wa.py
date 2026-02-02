"""
Analyze Cannabis Lab Results | Washington
Copyright (c) 2025 Cannlytics

Authors: Keegan Skeate <https://github.com/keeganskeate>
Created: 1/11/2025
Updated: 1/11/2025
License: MIT License <https://github.com/cannlytics/cannabis-data-science/blob/main/LICENSE>
"""
# Standard imports:
import ast
import os

# External imports:
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.colors as mcolors
import matplotlib.patheffects as path_effects
from matplotlib.ticker import FuncFormatter
from matplotlib.ticker import MultipleLocator
import pandas as pd
import numpy as np
import statsmodels.formula.api as smf
import seaborn as sns

# Internal imports:
from cannlytics.compounds import pesticides

#-----------------------------------------------------------------------
# Setup
#-----------------------------------------------------------------------

# Define where figures are saved.
assets_dir = r"C:\Users\keega\OneDrive\Cannlytics\research\lab-results-paper\paper\images\figures"

# Setup plotting style.
plt.style.use('seaborn-v0_8-whitegrid')
plt.rcParams.update({
    'font.family': 'Times New Roman',
    'font.size': 36,
})