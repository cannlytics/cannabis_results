"""

Data Sources:

    - [Robust MO](https://drive.google.com/drive/folders/1a53ciGSZQZgl0EXma5dzHBkQcKOdc1EN)

"""
# Internal imports:
from collections import defaultdict
import os
import re

# External imports:

from matplotlib import pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter, MaxNLocator
from matplotlib.colors import LinearSegmentedColormap
import matplotlib.lines as mlines
from nltk.util import ngrams
from nltk.corpus import stopwords
import nltk
import numpy as np
import pandas as pd
import seaborn as sns


#-----------------------------------------------------------------------
# Setup.
#-----------------------------------------------------------------------

# Setup plotting style.
plt.style.use('seaborn-v0_8-whitegrid')
plt.rcParams.update({
    'font.family': 'Times New Roman',
    'font.size': 32,
})

# Define where figures are saved.
assets_dir = r"C:\Users\keega\Documents\cannlytics\cannabis-data-science\season-4\173-cannabis-culture\presentation\images\figures"


#-----------------------------------------------------------------------
# Data: Read the results.
#-----------------------------------------------------------------------

# Read the results.
datafile = 'D://data/missouri/MissouriMedical/mo-robust-coa-data-2024-08-22-00-12-34.xlsx'
mo_results = pd.read_excel(datafile)

# Read CA results..
datafile = 'D:/data/cannabis_results/data/ca/ca-results-latest.xlsx'
ca_results = pd.read_excel(datafile)
print('Read %i CA results.' % len(ca_results))

# Read FL results.
datafile = 'D:/data/cannabis_results/data/fl/fl-results-latest.xlsx'
fl_results = pd.read_excel(datafile)
print('Read %i FL results.' % len(fl_results))

# Read MA results.
datafile = 'D:/data/cannabis_results/data/ma/ma-results-latest.xlsx'
ma_results = pd.read_excel(datafile)
ma_results['strain_name'] = ''
print('Read %i MA results.' % len(ma_results))

# Read NV results.
datafile = 'D:/data/cannabis_results/data/nv/nv-results-latest.xlsx'
nv_results = pd.read_excel(datafile)
nv_results['strain_name'] = ''
print('Read %i NV results.' % len(nv_results))


#-----------------------------------------------------------------------
# Data: Identifying common strain names.
#-----------------------------------------------------------------------

# Download NLTK stopwords if not already available
nltk.download('stopwords')

def generate_ngrams(text, n):
    """Generate n-grams from the given text."""
    if pd.isna(text):
        return []
    words = re.findall(r'\b\w+\b', text.lower())
    return list(ngrams(words, n))

def process_ngrams(data, columns, n_range=(1, 3), state='mo'):
    """Process n-grams for given columns in the data."""
    stop_words = set(stopwords.words('english'))
    ngram_states = defaultdict(set)
    for column in columns:
        if column not in data.columns:
            continue
        for text in data[column].dropna():
            for n in range(n_range[0], n_range[1] + 1):
                for ng in generate_ngrams(text, n):
                    if not any(word in stop_words for word in ng):
                        ngram_states[' '.join(ng)].add(state)
    return ngram_states

def find_cross_state_ngrams(mo_ngrams, other_states_data, columns, n_range=(1, 3)):
    """Analyze n-grams that appear in MO and at least one other state."""
    results = []
    for state, data in other_states_data.items():
        state_ngrams = process_ngrams(data, columns, n_range, state)
        for ngram, mo_presence in mo_ngrams.items():
            if state in state_ngrams[ngram]:
                mo_ngrams[ngram].add(state)
    for ngram, states in mo_ngrams.items():
        if len(states) > 1:  # Appears in MO and at least one other state
            results.append({
                'n_gram': ngram,
                'length': len(ngram.split()),
                'state_count': len(states),
                'states': ', '.join(sorted(states))
            })
    try:
        return pd.DataFrame(results).sort_values('state_count', ascending=False)
    except:
        return pd.DataFrame()


# Find n-grams for MO.
columns_to_analyze = ['product_name', 'strain_name']
mo_ngrams = process_ngrams(mo_results, columns_to_analyze)

# Find n-grams present in other states.
other_states_data = {
    'ca': ca_results,
    'ma': ma_results,
    'fl': fl_results,
    'nv': nv_results
}
cross_state_ngram_results = find_cross_state_ngrams(mo_ngrams, other_states_data, columns_to_analyze)

# Display results
print('Number of n-grams in MO and another other state:', len(cross_state_ngram_results))

# Restrict to known n-grams.
known_strains = [
    'aether',
    'alien fruit',
    'apple fritter',
    'apple pie',
    'apples bananas',
    'banana mac',
    'blueberry runtz',
    'cali raisin',
    'cali raisins',
    'california raisins',
    'cereal milk',
    'chanel',
    'cherry blossom',
    'chili verde',
    'dome',
    'dying dreaming',
    'finnish frost',
    'fruit salad',
    'gmo',
    'governmint oasis',
    'grape pie',
    'grapes cream',
    'gush mintz',
    'gushmints',
    'gushmintz',
    'heavy duty fruity',
    'kush mintz',
    'lemon cherry gelato',
    'mac',
    'mendo crumble',
    'permanent marker',
    'pineapple breeze',
    'platinum gorilla',
    'point break',
    'rainbow belts',
    'raspberry parfait',
    'runtz',
    'runtz muffins',
    'san fernando valley',
    'sangria',
    'sfv',
    'silver dragon',
    'silver dragon',
    'slurty3',
    'space center',
    'sun kissed',
    'tahiti sunrise',
    'triangle kush'
]

# Table of the top n-grams.
matching_ngrams = cross_state_ngram_results[cross_state_ngram_results['n_gram'].isin(known_strains)]
top_ngrams = matching_ngrams.head(9).copy()
top_ngrams.sort_values('n_gram', ascending=True, inplace=True)
top_ngrams = top_ngrams[['n_gram', 'states']]
top_ngrams.columns = ['Strain Name', 'States']
table = top_ngrams.to_latex(index=False)
print(table)


#-----------------------------------------------------------------------
# Data: Get results for common strain names.
#-----------------------------------------------------------------------

def find_longest_matching_strain(text, known_strains):
    """
    Find the longest known strain name that matches the given text.
    """
    matching_strains = [strain for strain in known_strains if strain.lower() in text.lower()]
    return max(matching_strains, key=len) if matching_strains else None

def standardize_strain_name(strain_name, strain_name_variations):
    """
    Standardize the strain name based on known variations.
    """
    return strain_name_variations.get(strain_name.lower(), strain_name)

def filter_and_standardize_results(data, known_strains, strain_name_variations, columns=['product_name', 'strain_name']):
    """
    Filter the dataframe to include only rows where product_name or strain_name
    contain one of the known strain names, and add a standardized strain name column.
    """
    pattern = '|'.join(map(re.escape, known_strains))
    masks = [data[col].str.contains(pattern, case=False, na=False) for col in columns]
    final_mask = pd.concat(masks, axis=1).any(axis=1)
    filtered_data = data[final_mask].copy()
    filtered_data['standard_strain_name'] = filtered_data.apply(
        lambda row: find_longest_matching_strain(
            ' '.join(str(row[col]) for col in columns if pd.notna(row[col])),
            known_strains
        ),
        axis=1
    )
    filtered_data['standard_strain_name'] = filtered_data['standard_strain_name'].apply(
        lambda x: standardize_strain_name(x, strain_name_variations) if pd.notna(x) else x
    )
    return filtered_data

# Define known alternative spellings.
strain_name_variations = {
    'gushmints': 'gush mintz',
    'gushmintz': 'gush mintz',
    'sfv': 'san fernando valley',
    'cali raisins': 'california raisins',
    'cali raisin': 'california raisins',
}

# Filter results for each state
filtered_ca_results = filter_and_standardize_results(ca_results, known_strains, strain_name_variations)
filtered_fl_results = filter_and_standardize_results(fl_results, known_strains, strain_name_variations)
filtered_ma_results = filter_and_standardize_results(ma_results, known_strains, strain_name_variations)
filtered_nv_results = filter_and_standardize_results(nv_results, known_strains, strain_name_variations)
filtered_mo_results = filter_and_standardize_results(mo_results, known_strains, strain_name_variations)

# Combine all filtered results into a single dataframe
all_filtered_results = pd.concat([
    filtered_ca_results.assign(state='CA'),
    filtered_fl_results.assign(state='FL'),
    filtered_ma_results.assign(state='MA'),
    filtered_nv_results.assign(state='NV'),
    filtered_mo_results.assign(state='MO'),
])
table_data = all_filtered_results['state'].value_counts()
table_data = table_data.reset_index()
table_data.columns = ['State', 'Results of common strains']
table = table_data.to_latex(index=False)
print(table)

def format_with_commas(x, p):
    return f'{x:,.0f}'

# Visualize the number of results per state by common strains.
plt.figure(figsize=(15, 9.5))
table_data = table_data.sort_values('Results of common strains', ascending=True)
bars = plt.barh(table_data['State'], table_data['Results of common strains'], alpha=0.7)
plt.title('Number of Results for Common Strains by State', pad=20)
plt.xlabel('')
plt.ylabel('')
for i, v in enumerate(table_data['Results of common strains']):
    plt.text(v + 50, i, f'{v:,.0f}', va='center')
state_colors = {
    'CA': '#0057B7', 'FL': '#FF8C00', 'MA': '#800080', 'NV': '#8B4513', 'MO': 'teal'
}
for bar, state in zip(bars, table_data['State']):
    bar.set_color(state_colors[state])
plt.gca().xaxis.set_major_formatter(FuncFormatter(format_with_commas))
plt.tight_layout()
outfile = os.path.join(assets_dir, 'strains-by-state.png')
plt.savefig(outfile, dpi=96, bbox_inches='tight', transparent=False, facecolor='white')
plt.show()


#-----------------------------------------------------------------------
# Data: Standardize cannabinoid and terpene data.
#-----------------------------------------------------------------------

from cannlytics.compounds import terpenes
from cannlytics.data.coas import standardize_results

# Define compounds of interest.
cannabinoids = [
    'cbda',
    'thca',
    'delta_9_thc',
    'cbc',
    'cbd',
    'cbdv',
    'cbg',
    'cbga',
    'cbn',
    'thcv',
    'delta_8_thc',
]
major_cannabinoids = [
    'thca',
    'cbga',
    'thcv',
]
major_terpenes = [
    'alpha_humulene',
    'beta_caryophyllene',
    'beta_myrcene',
    'beta_pinene',
    'camphene',
    'd_limonene',
    'linalool',
    'terpinolene',
]
all_terpenes = list(terpenes.keys())

# Standardize all of the results.
compounds = cannabinoids + all_terpenes
ca_subsample = filtered_ca_results.loc[filtered_ca_results['lab'] != 'SC Labs'].copy()
standard_ca_results = standardize_results(ca_subsample, compounds)
standard_fl_results = standardize_results(filtered_fl_results, compounds)
standard_ma_results = standardize_results(filtered_ma_results, compounds)
standard_nv_results = filtered_nv_results.copy()
standard_mo_results = standardize_results(filtered_mo_results, compounds)
state_results = {
    'CA': standard_ca_results,
    'FL': standard_fl_results,
    'MA': standard_ma_results,
    'MO': standard_mo_results,
    'NV': standard_nv_results,

}

#-----------------------------------------------------------------------
# Analysis: Missouri strains.
#-----------------------------------------------------------------------

# Look at the top strains observed in MO.
standard_mo_results['standard_strain_name'].value_counts()


#-----------------------------------------------------------------------
# Analysis: Strain names.
#-----------------------------------------------------------------------

# Define top strain names.
top_strains = [
    'apple fritter',
    'cereal milk',
    'gmo',
    'grape pie',
    'lemon cherry gelato',
    # 'mac',
    # 'rainbow belts',
    'runtz',
    # 'sfv'
]

# Define flower types.
flower_types = [
    'flower',
    'Flower',
    'Plant (Flower - Cured)',
    'Plant (Bulk Flower)',
    'Flower Inhalable',
    'Shake/Trim',
    'Marijuana Flowers/Buds',
    'Small/Popcorn Buds',
    'Whole Wet Plant',
    'Shake/Trim (by strain)',
    'Marijuana Flowers/Buds-Packaged',
]

# Visualize the number of results per state by top strains.
top_strain_results = all_filtered_results.loc[
    all_filtered_results['standard_strain_name'].isin(top_ngrams['Strain Name'])
]
table_data = top_strain_results['standard_strain_name'].value_counts()
table_data = table_data.reset_index()
table_data.sort_values('standard_strain_name', ascending=True, inplace=True)
table_data.columns = ['Strain Name', 'Number of results']
table = table_data.to_latex(index=False)
plt.figure(figsize=(15, 9.5))
table_data.sort_values('Number of results', ascending=True, inplace=True)
bars = plt.barh(table_data['Strain Name'], table_data['Number of results'], alpha=0.7)
plt.title('Number of Results by Strain Name', pad=20)
plt.xlabel('')
plt.ylabel('')
for i, v in enumerate(table_data['Number of results']):
    plt.text(v + 5, i, f'{v:,.0f}', va='center')
num_strains = len(table_data)
colors = plt.cm.viridis(np.linspace(0, 1, num_strains))
for bar, color in zip(bars, colors):
    bar.set_color(color)
plt.gca().xaxis.set_major_formatter(FuncFormatter(format_with_commas))
plt.tight_layout()
outfile = os.path.join(assets_dir, 'number-of-strain-results.png')
plt.savefig(outfile, dpi=96, bbox_inches='tight', transparent=False, facecolor='white')
plt.show()


# Optional: Visualize MO results against the distribution of results.
# strain = 'lemon cherry gelato'
# compound = 'd_limonene'
# plt.figure(figsize=(12, 6))
# all_values = []
# for state, df in state_results.items():
#     if state == 'MO':
#         continue
#     df = df[df['product_type'].isin(flower_types)]
#     state_data = df[df['standard_strain_name'] == strain][compound]
#     all_values.extend(state_data)
#     plt.hist(
#         state_data,
#         alpha=0.3,
#         label=state,
#         density=True
#     )
# all_values = [value for value in all_values if isinstance(value, (int, float)) and pd.notna(value)]
# mean_value = np.mean(all_values)
# plt.axvline(mean_value, color='r', linestyle='--', label='Other States Mean')
# mo_data = standard_mo_results[standard_mo_results['standard_strain_name'] == strain][compound]
# mo_value = mo_data.mean()
# plt.axvline(mo_value, color='g', linestyle='-', label='MO')
# plt.annotate(f'MO: {mo_value:.2f}', xy=(mo_value, plt.ylim()[1]), 
#                 xytext=(5, 5), textcoords='offset points', 
#                 ha='left', va='bottom', color='g')
# plt.xlim(0)
# plt.title(f'Distribution of `{compound}` in {strain.title()}', fontsize=16)
# plt.xlabel('Concentration')
# plt.ylabel('Density')
# plt.legend()
# plt.tight_layout()
# plt.show()

# === Visualize terpene ratios ===

# Define a color map for states
state_colors = {
    'CA': '#0057B7',  # Blue inspired by California's coastal skies
    'FL': '#FF8C00',  # Bright orange, reminiscent of Florida’s sunsets and oranges
    'MA': '#800080',  # Dark purple, offering a strong and elegant contrast
    'NV': '#8B4513',  # Dark gold, symbolizing Nevada's mining heritage with a richer tone
    'MO': 'teal'   # Pleasant green, representing Missouri’s lush landscapes
}

def strain_scatterplot(strain, terpene1, terpene2):
    """Create a scatterplot of two terpenes for a given strain."""
    plt.figure(figsize=(21, 13))
    all_terpene1 = []
    all_terpene2 = []

    # Plot data for each state except MO
    sorted_states = sorted([state for state in state_results.keys() if state != 'MO'])
    reversed_states = sorted_states[::-1]
    for i, state in enumerate(reversed_states):
        df = state_results[state]
        df = df[df['product_type'].isin(flower_types)]
        state_data = df[df['standard_strain_name'] == strain]
        state_data = state_data[(state_data[terpene1] > 0) & (state_data[terpene2] > 0)]
        x = state_data[terpene1]
        y = state_data[terpene2]
        alpha = 0.25 if state == 'NV' else 0.7
        plt.scatter(
            x,
            y,
            alpha=alpha,
            label=state,
            s=160,
            color=state_colors[state],
            zorder=i  # Assign z-order based on reversed index
        )
        all_terpene1.extend(x)
        all_terpene2.extend(y)

    # Calculate and plot mean for other states
    mean_terpene1 = np.mean([v for v in all_terpene1 if pd.notna(v)])
    mean_terpene2 = np.mean([v for v in all_terpene2 if pd.notna(v)])
    plt.scatter(
        mean_terpene1,
        mean_terpene2,
        color='red',
        s=1250,
        marker='*', 
        label='Prior Mean',
        zorder=5
    )

    # Plot MO data
    mo_data = standard_mo_results[standard_mo_results['standard_strain_name'] == strain]
    mo_terpene1 = mo_data[terpene1]
    mo_terpene2 = mo_data[terpene2]
    plt.scatter(
        mo_terpene1,
        mo_terpene2,
        color=state_colors['MO'],
        s=400,
        alpha=1,
        label='MO', 
        zorder=4
    )

    # Style the figure.
    terpene1_title = terpene1.replace('_', '-').title()
    terpene2_title = terpene2.replace('_', '-').title()
    plt.xlim(0.001)
    plt.ylim(0)
    plt.title(f'{terpene1_title} vs {terpene2_title} in {strain.title()}', pad=20)
    plt.xlabel(terpene1_title)
    plt.ylabel(terpene2_title)
    handles, labels = plt.gca().get_legend_handles_labels()
    sorted_pairs = sorted(zip(labels, handles), key=lambda t: t[0])
    sorted_labels, sorted_handles = zip(*sorted_pairs)
    plt.legend(sorted_handles, sorted_labels, bbox_to_anchor=(1.25, 1), loc='upper right')
    plt.tight_layout()
    slug = strain.lower().replace(' ', '-')
    plt.savefig(os.path.join(assets_dir, f'{slug}-ratio.png'), dpi=96, transparent=False, facecolor='white')
    plt.show()

# # DEV: Test scatterplot.
strain = 'apple fritter'
terpene1, terpene2 = 'd_limonene', 'beta_pinene'
strain_scatterplot(strain, terpene1, terpene2)

# Create scatterplots for all top strains.
terpene1, terpene2 = 'd_limonene', 'beta_pinene'
for strain in top_strains:
    strain_scatterplot(strain, terpene1, terpene2)


#-----------------------------------------------------------------------
# Analysis: Aggregate statistics.
#-----------------------------------------------------------------------

# Define known aggregate statistics.
total_pdfs = 1282

# Calculate the total number of results.
total_results = len(mo_results)

# Calculate the percent parsed.
percent_parsed = total_results / total_pdfs

# Output summary statistics to LaTeX.
summary = pd.DataFrame({
    'Total COA PDFs': [f'{total_pdfs:,}'],
    'Parsed Results': [f'{total_results:,}'],
    'Percent Parsed': [f'{percent_parsed:.1%}'],
})
output = summary.T
table = output.to_latex(index=True, header=False)
table = table.replace('\n\\midrule', '')
print(table)

# TODO: Visualize the product types.


#-----------------------------------------------------------------------
# Analysis: Chemical diversity.
#-----------------------------------------------------------------------

def calculate_shannon_diversity(df, compounds):
    """Calculate Shannon Diversity Index."""
    diversities = []
    for _, row in df.iterrows():
        proportions = [pd.to_numeric(row[compound], errors='coerce') for compound in compounds if pd.to_numeric(row[compound], errors='coerce') > 0]
        proportions = np.array(proportions) / sum(proportions)
        shannon_index = -np.sum(proportions * np.log2(proportions))
        diversities.append(shannon_index)
    return diversities


# TODO: Visualize aggregate chemical diversity over time in MO.


# TODO: Visualize chemical diversity over time for strains in MO vs. other states.
# Line for average diversity of a given strain.
# Dots for diversity of a given strain result in MO.
