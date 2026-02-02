"""
Analyze Results | Mississippi
Copyright (c) 2024 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 10/3/2024
Updated: 10/17/2024
License: CC-BY 4.0 <https://huggingface.co/datasets/cannlytics/cannabis_tests/blob/main/LICENSE>

Description:

    Analyze all public Mississippi lab result data.

Data Sources:
    
    - Public records request from the State of Mississippi.

"""
# External imports:
from matplotlib import pyplot as plt
import matplotlib.cm as cm
import numpy as np
import pandas as pd
import scipy.stats as stats
import seaborn as sns
from statsmodels.formula.api import ols
from statsmodels.stats.multicomp import pairwise_tukeyhsd


#------------------------------------------------------------------------------
# Setup.
#------------------------------------------------------------------------------

# Define where figures are saved.
assets_dir = r"C:\Users\keega\Documents\cannlytics\cannabis-data-science\season-5\181-ak-results-analysis\presentation\images\figures"

# Setup plotting style.
plt.style.use('seaborn-v0_8-whitegrid')
plt.rcParams.update({
    'font.family': 'Times New Roman',
    'font.size': 18,
    'axes.edgecolor': 'gray',
    'axes.linewidth': 0.8,
    'grid.color': 'lightgray',
    'grid.linestyle': '--',
    'grid.linewidth': 0.5,
    'legend.fontsize': 14,
    'legend.title_fontsize': 16,
})


#------------------------------------------------------------------------------
# Read the data.
#------------------------------------------------------------------------------

# Read the tests.
datafile = r"D:\data\mississippi\Mississippi-20240912T203419Z-001\Mississippi\050724 Test Results Request_Produced to Requestor (1).xlsx"
tests = pd.read_excel(datafile)
print('Number of tests:', len(tests))

# Standardize column names.
columns = {
    'Testing Facility UID': 'lab_id',
    'ResultRecordedDateTime': 'date_tested',
    'Package ID': 'product_id',
    'Quantity': 'value',
    'Name': 'test_name',
    'Note': 'notes',
}
tests.rename(columns=columns, inplace=True)

# Get the product types.
test_names = list(tests['test_name'].unique())
product_types = [x.split(')')[-1].strip() for x in test_names]
product_types = list(set(product_types))
print('Product types:', product_types)

def extract_product_type(test_name, product_types):
    """Extract the product type from the test name."""
    for product_type in product_types:
        if test_name.endswith(product_type):
            return product_type
    return None

# Assign `product_type` to tests
tests['product_type'] = tests['test_name'].apply(
    extract_product_type,
    product_types=product_types
)

def clean_test_names(test_name):
    """Clean test names."""
    return test_name.split('(')[0].strip()

# Remove extraneous test details.
tests['test_name'] = tests['test_name'].apply(clean_test_names)

# Pivot the data by label.
results = tests.pivot_table(
    index=['product_id', 'lab_id', 'product_type'],
    columns='test_name',
    values='value',
    aggfunc='first'
).reset_index()
print('Number of products:', len(results))

# Get final `date_tested` for each package.
dates = []
for product_id in results['product_id']:
    dates.append(tests[tests['product_id'] == product_id]['date_tested'].max())
results['date_tested'] = dates

# Optional: Compile all notes for each package.
notes = []
for product_id in results['product_id']:
    notes.append(', '.join(tests[tests['product_id'] == product_id]['notes'].dropna().unique()))
results['notes'] = notes

# Save the data.
date = results['date_tested'].max().strftime('%Y-%m-%d')
outfile = f'D:/data/mississippi/ms-results-{date}.xlsx'
results.to_excel(outfile, index=False)


#------------------------------------------------------------------------------
# Analysis: Product types.
#------------------------------------------------------------------------------

# Define colors for product types.
product_type_colors = {
    'Infused Edible': '#FF6F61',  # Soft Coral Red
    'Infused Non-Edible': '#b9a281',  # Warm Amber Orange
    'Solvent Based Concentrate/Extract': '#42A5F5',  # Sky Blue
    'Raw Plant Material': '#66BB6A',  # Fresh Leaf Green
    'Non-Solvent Concentrate': '#FFD54F',  # Soft Sunflower Yellow
    'Non-Solvent Concentrate/Extract': '#AB47BC',  # Lavender Purple
}

# Visualize the proportion of product types.
counts = results['product_type'].value_counts()
colors = [product_type_colors[product] for product in counts.index]
plt.figure(figsize=(10, 10))
plt.pie(
    counts,
    labels=counts.index,
    autopct='%1.1f%%',
    startangle=140,
    colors=colors,
)
plt.axis('equal')
plt.savefig(f'{assets_dir}/ms-product-types.png', dpi=96, bbox_inches='tight', transparent=False, facecolor='white')
plt.show()

# Visualize the proportion of product types by month.
sample = results.copy()
sample['date_tested'] = pd.to_datetime(sample['date_tested'])
sample['month'] = sample['date_tested'].dt.to_period('M').dt.to_timestamp()
sample['month_str'] = sample['month'].dt.strftime('%Y-%m')
monthly_counts = sample.groupby(['month', 'product_type']).size().reset_index(name='count')
total_counts = monthly_counts.groupby('month')['count'].sum().reset_index(name='total_count')
monthly_counts = monthly_counts.merge(total_counts, on='month')
monthly_counts['proportion'] = monthly_counts['count'] / monthly_counts['total_count']
data_pivot = monthly_counts.pivot(index='month', columns='product_type', values='proportion').fillna(0)
product_types_order = [
    'Raw Plant Material',
    'Non-Solvent Concentrate',
    'Non-Solvent Concentrate/Extract',
    'Solvent Based Concentrate/Extract',
    'Infused Edible',
    'Infused Non-Edible'
]
product_types_present = [pt for pt in product_types_order if pt in data_pivot.columns]
data_pivot = data_pivot[product_types_present]
data_pivot = data_pivot.sort_index()
data_pivot.index = data_pivot.index.strftime('%Y-%m')
fig, ax = plt.subplots(figsize=(15.5, 5))
bottom = np.zeros(len(data_pivot))
for product_type in product_types_present:
    values = data_pivot[product_type].values
    ax.bar(
        data_pivot.index,
        values,
        bottom=bottom,
        color=product_type_colors[product_type],
        label=product_type,
        edgecolor='white',
        width=0.8
    )
    bottom += values
ax.set_title('Proportion of Product Types Tested by Month in Mississippi', pad=10)
ax.set_xlabel('')
ax.set_ylabel('')
ax.tick_params(axis='x', rotation=45)
ax.set_ylim(0, 1)
ax.yaxis.set_major_formatter(plt.FuncFormatter('{0:.0%}'.format))
ax.legend(
    title='Product Type',
    title_fontsize=18,
    fontsize=16,
    loc='upper left',
    bbox_to_anchor=(1.02, 1),
    borderaxespad=0
)
plt.tight_layout(rect=[0, 0, 0.85, 1])
plt.savefig(f'{assets_dir}/ms-product-types-by-month.png', dpi=96, bbox_inches='tight', transparent=False, facecolor='white')
plt.show()


#------------------------------------------------------------------------------
# Analysis: Labs.
#------------------------------------------------------------------------------

# Current event.
# Source: https://www.mississippifreepress.org/nightmare-scenario-msdh-places-hold-on-rapid-analytics-tested-medical-cannabis-products/
testing_pause = pd.to_datetime('2023-12-21').strftime('%Y-%m')

# Count the number of lab tests by lab over time.
sample = results.copy()
sample['date_tested'] = pd.to_datetime(sample['date_tested'])
sample['month'] = sample['date_tested'].dt.to_period('M').dt.to_timestamp()
monthly_lab_counts = sample.groupby(['month', 'lab_id']).size().reset_index(name='count')
data_pivot = monthly_lab_counts.pivot(index='month', columns='lab_id', values='count').fillna(0)
data_pivot = data_pivot.sort_index()
data_pivot.index = data_pivot.index.strftime('%Y-%m')
lab_ids = data_pivot.columns.tolist()
color_map = cm.get_cmap('tab20', len(lab_ids))
lab_colors = {lab: color_map(i) for i, lab in enumerate(lab_ids)}

# Visualize the number of lab tests by lab over time.
fig, ax = plt.subplots(figsize=(15.5, 5))
bottom = np.zeros(len(data_pivot))
for lab in lab_ids:
    values = data_pivot[lab].values
    ax.bar(
        data_pivot.index,
        values,
        bottom=bottom,
        color=lab_colors[lab],
        label=lab,
        edgecolor='white',
        width=0.8
    )
    bottom += values
ax.set_title('Number of Products Tested by Lab by Month in Mississippi', pad=10)
ax.set_xlabel('')
ax.set_ylabel('Number of Products')
ax.tick_params(axis='x', rotation=45)
plt.vlines(testing_pause, 0, 1000, color='red', linestyle='--', label='Administrative Hold')
ax.legend(
    title='Lab',
    title_fontsize=18,
    fontsize=16,
    loc='upper left',
    bbox_to_anchor=(1.02, 1),
    borderaxespad=0
)
plt.ylim(0.01)
plt.tight_layout(rect=[0, 0, 0.85, 1])
plt.savefig(f'{assets_dir}/ms-results-by-lab-by-month.png', dpi=96, bbox_inches='tight', transparent=False, facecolor='white')
plt.show()


#------------------------------------------------------------------------------
# Analysis: Total THC over time.
#------------------------------------------------------------------------------

# Calculate wet `total_thc` for flower.
flower = results[results['product_type'] == 'Raw Plant Material']
flower['wet_total_thc'] = flower['Total THC'] * (1 - flower['Moisture Content'] / 100)

# Process Total THC over time by lab.
flower['date_tested'] = pd.to_datetime(flower['date_tested'])
flower['month'] = flower['date_tested'].dt.to_period('M').dt.to_timestamp()
flower = flower[
    (flower['Total THC'] > 0)
]

# Visualization of Total THC over time by lab.
testing_pause = pd.to_datetime('2023-12-21')
fig, ax = plt.subplots(figsize=(15.5, 5))
sns.scatterplot(
    data=flower,
    x='date_tested',
    y='Total THC',
    hue='lab_id',
    palette=lab_colors,
    alpha=0.6,
    edgecolor=None,
    s=50,
    ax=ax
)
monthly_avg_thc = flower.groupby(['month', 'lab_id'])['Total THC'].mean().reset_index()
sns.lineplot(
    data=monthly_avg_thc,
    x='month',
    y='Total THC',
    hue='lab_id',
    palette=lab_colors,
    linewidth=2.5,
    ax=ax,
    legend=False
)
ax.vlines(testing_pause, 0, 50, color='red', linestyle='--', label='Administrative Hold')
ax.set_title('Dry Total THC (%) in Medical Flower by Lab in Mississippi', pad=10)
ax.set_xlabel('')
ax.set_ylabel('Total THC (%)', labelpad=10)
ax.tick_params(axis='x', rotation=45)
ax.set_ylim(0.001, 50)
ax.legend(
    title='Lab',
    title_fontsize=18,
    fontsize=16,
    loc='upper left',
    bbox_to_anchor=(1.02, 1),
    borderaxespad=0
)
plt.xlim(flower['date_tested'].min(), flower['date_tested'].max())
plt.tight_layout(rect=[0, 0, 0.85, 1])
plt.savefig(f'{assets_dir}/ms-total-thc-over-time-by-lab.png', dpi=96, bbox_inches='tight', transparent=False, facecolor='white')
plt.show()

# Table of average THC by lab.
avg_thc_by_lab = flower.groupby('lab_id')['Total THC'].mean().reset_index()
avg_thc_by_lab.columns = ['Lab ID', 'Avg. Total THC (%)']
avg_thc_by_lab['Avg. Total THC (%)'] = avg_thc_by_lab['Avg. Total THC (%)'].map('{:.2f}'.format)
table_thc_by_lab_latex = avg_thc_by_lab.to_latex(
    index=False,
    header=True,
    caption='Average Total THC of Flower Products by Lab',
    column_format='lr',
    escape=False
)
print(table_thc_by_lab_latex)

# Perform a 3-way difference-of-means tests.
model = ols('Q("Total THC") ~ C(lab_id)', data=flower).fit()
tukey = pairwise_tukeyhsd(
    endog=flower['Total THC'],
    groups=flower['lab_id'],
    alpha=0.05
)
print(tukey)

# Visualize the distribution of THC by lab.
fig, ax = plt.subplots(figsize=(7.5, 5.5))
sns.boxplot(
    x='lab_id',
    y='Total THC',
    data=flower,
    hue='lab_id',
    palette=lab_colors,
)
plt.title('Dry Total THC (%) by Lab in Mississippi', pad=10)
plt.xlabel('')
plt.ylabel('Total THC (%)')
plt.ylim(0.01)
plt.tight_layout()
plt.savefig(f'{assets_dir}/ms-total-thc-by-lab-boxplot.png', dpi=96, bbox_inches='tight', transparent=False, facecolor='white')
plt.show()

# Visualization of Total THC (dry), wet `total_thc`, and `Moisture Content`
# of flower over time.
# News Source: https://www.mississippifreepress.org/nightmare-scenario-msdh-places-hold-on-rapid-analytics-tested-medical-cannabis-products/
# Bulletin: https://www.mmcp.ms.gov/sites/default/files/QuarantineStatementsforWebsite.pdf
testing_pause = pd.to_datetime('2023-12-21') 
sample = flower[
    (flower['Total THC'] > 0) & (flower['Total THC'] <= 50) &
    (flower['wet_total_thc'] > 0) & (flower['wet_total_thc'] <= 50) &
    (flower['Moisture Content'] > 0) & (flower['Moisture Content'] <= 25)
]
monthly_avg = sample.groupby('month').agg({
    'Total THC': 'mean',
    'wet_total_thc': 'mean',
    'Moisture Content': 'mean'
}).reset_index()

# Visualization of Total THC (dry), wet `total_thc`, and `Moisture Content`
fig, axs = plt.subplots(3, 1, figsize=(15.5, 15), sharex=True)
sns.scatterplot(
    data=sample,
    x='date_tested',
    y='Total THC',
    color='#1f77b4',  # Blue
    alpha=0.4,
    s=50,
    label='Total THC (Dry)',
    ax=axs[0]
)
sns.lineplot(
    data=monthly_avg,
    x='month',
    y='Total THC',
    color='#1f77b4',
    linewidth=2.5,
    label='Avg Total THC (Dry)',
    ax=axs[0]
)
axs[0].set_ylabel('Total THC (%)')
axs[0].set_title('Dry Total THC (%) in Medical Flower in Mississippi', pad=10)
axs[0].axvline(testing_pause, color='red', linestyle='--', linewidth=2, label='Administrative Hold')
axs[0].legend()
sns.scatterplot(
    data=sample,
    x='date_tested',
    y='wet_total_thc',
    color='#ff7f0e',  # Orange
    alpha=0.4,
    s=50,
    label='Total THC (Wet)',
    ax=axs[1]
)
sns.lineplot(
    data=monthly_avg,
    x='month',
    y='wet_total_thc',
    color='#ff7f0e',
    linewidth=2.5,
    label='Avg Total THC (Wet)',
    ax=axs[1]
)
axs[1].set_ylabel('Wet Total THC (%)')
axs[1].set_title('Wet Total THC (%) in Medical Flower in Mississippi', pad=10)
axs[1].axvline(testing_pause, color='red', linestyle='--', linewidth=2, label='Administrative Hold')
axs[1].legend()
sns.scatterplot(
    data=sample,
    x='date_tested',
    y='Moisture Content',
    color='#2ca02c',  # Green
    alpha=0.4,
    s=50,
    label='Moisture Content (%)',
    ax=axs[2]
)
sns.lineplot(
    data=monthly_avg,
    x='month',
    y='Moisture Content',
    color='#2ca02c',
    linewidth=2.5,
    label='Avg Moisture Content (%)',
    ax=axs[2]
)
axs[2].set_ylabel('Moisture Content (%)')
axs[2].set_title('Moisture Content (%) in Medical Flower in Mississippi', pad=10)
axs[2].set_xlabel('')
axs[2].tick_params(axis='x', rotation=45)
axs[2].axvline(testing_pause, color='red', linestyle='--', linewidth=2, label='Administrative Hold')
axs[2].legend()
plt.xlim(sample['date_tested'].min(), sample['date_tested'].max())
plt.tight_layout()
plt.savefig(f'{assets_dir}/ms-total-thc-and-moisture-over-time.png', dpi=96, bbox_inches='tight', transparent=False, facecolor='white')
plt.show()

# Table of average dry THC, wet THC, and Moisture Content
# before and after the "Administrative Hold".
pre_hold_data = sample[sample['date_tested'] < testing_pause]
post_hold_data = sample[sample['date_tested'] >= testing_pause]
pre_hold_avg = pre_hold_data[['Total THC', 'wet_total_thc', 'Moisture Content']].mean().to_frame(name='Before Hold').T
post_hold_avg = post_hold_data[['Total THC', 'wet_total_thc', 'Moisture Content']].mean().to_frame(name='After Hold').T
avg_thc_moisture = pd.concat([pre_hold_avg, post_hold_avg])
avg_thc_moisture.columns = ['Dry Total THC (%)', 'Wet Total THC (%)', 'Moisture Content (%)']
avg_thc_moisture = avg_thc_moisture.round(1)
table_avg_latex = avg_thc_moisture.to_latex(
    index=True,
    header=True,
    caption='Average Dry Total THC, Wet Total THC, and Moisture Content Before and After Administrative Hold',
    column_format='lrrr',
    escape=False,
    float_format="%.1f",
)
print(table_avg_latex)


def perform_ttest(var, equal_var=True):
    stat, p = stats.ttest_ind(pre_hold_vars[var], post_hold_vars[var], equal_var=equal_var)
    print(f"T-test for {var}:")
    print(f"Statistic={stat:.3f}, p-value={p:.3f}")
    if p < 0.05:
        print(f"Significant difference in {var} before and after the hold.\n")
    else:
        print(f"No significant difference in {var} before and after the hold.\n")


# Perform t-tests for each variable.
# Decide whether to assume equal variances based on Levene's Test.
variables = ['Total THC', 'wet_total_thc', 'Moisture Content']
pre_hold_vars = {var: pre_hold_data[var].dropna() for var in variables}
post_hold_vars = {var: post_hold_data[var].dropna() for var in variables}
for var in variables:
    perform_ttest(var, equal_var=True)


#------------------------------------------------------------------------------
# Analysis: Aggregate statistics.
#------------------------------------------------------------------------------

# Output summary statistics to LaTeX.
summary = pd.DataFrame({
    'Start Date': [results['date_tested'].min().strftime('%Y-%m-%d')],
    'End Date': [results['date_tested'].max().strftime('%Y-%m-%d')],
    'Total Tests': [f'{len(tests):,}'],
    'Total Batches': [f'{len(results):,}'],
})
output = summary.T
table = output.to_latex(index=True, header=False)
table = table.replace('\n\\midrule', '')
print(table)
