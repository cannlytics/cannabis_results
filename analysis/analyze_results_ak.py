"""
Get Cannabis Results | Alaska
Copyright (c) 2024 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 10/17/2024
Updated: 10/17/2024
License: CC-BY 4.0 <https://huggingface.co/datasets/cannlytics/cannabis_tests/blob/main/LICENSE>

Description:

    Analyze all public Alaska lab result data.

Data Sources:
    
    - Public records request from the State of Alaska.

"""
# Standard imports:
import os

# External imports:
from matplotlib import pyplot as plt
import matplotlib.dates as mdates
import matplotlib.cm as cm
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd
import seaborn as sns


#------------------------------------------------------------------------------
# Setup.
#------------------------------------------------------------------------------

# Define where figures are saved.
assets_dir = r"C:\Users\keega\Documents\cannlytics\cannabis-data-science\season-5\181-ak-results-analysis\presentation\images\figures"

# Setup plotting style.
plt.style.use('seaborn-v0_8-whitegrid')
plt.rcParams.update({
    'font.family': 'Times New Roman',
    'font.size': 24,
    'axes.edgecolor': 'gray',
    'axes.linewidth': 0.8,
    'grid.color': 'lightgray',
    'grid.linestyle': '--',
    'grid.linewidth': 0.5,
    # 'legend.fontsize': 14,
    # 'legend.title_fontsize': 16,
})


#------------------------------------------------------------------------------
# Read and process the data.
#------------------------------------------------------------------------------

# Define where the data lives.
data_dir = 'D://data/alaska/results/datasets'

# Read all of the results in the data directory.
results = []
for file_name in os.listdir(data_dir):
    file_path = os.path.join(data_dir, file_name)
    if file_name.endswith('.xlsx'):
        df = pd.read_excel(file_path)
        results.append(df)
results = pd.concat(results, ignore_index=True)
total_results = len(results)
print('Total number of results:', total_results)

# Augment producer ID.
results['producer_id'] = results['label'].apply(lambda x: x[12:15])

# Standardize timeseries variables.
results['date_tested'] = pd.to_datetime(results['date_tested'])
results['month'] = results['date_tested'].dt.to_period('M').dt.to_timestamp()


#------------------------------------------------------------------------------
# Analysis: Alaska number of tests over time.
#------------------------------------------------------------------------------

# Visualize the number of products tested over time.
results['month'] = pd.to_datetime(results['month'])
all_months = pd.date_range(start=results['month'].min(), end=results['month'].max(), freq='MS')
monthly_test_counts = results.groupby('month').size().reindex(all_months, fill_value=0).reset_index(name='count')
monthly_test_counts.columns = ['month', 'count']
fig, ax = plt.subplots(figsize=(17.5, 7.5))
ax.bar(
    monthly_test_counts['month'].dt.strftime('%Y-%m'),
    monthly_test_counts['count'],
    color='skyblue',
    edgecolor='white',
    width=0.8
)
ax.set_title('Number of Products Tested by Month in Alaska', pad=10)
ax.set_xlabel('')
ax.set_ylabel('Number of Products')
ax.tick_params(axis='x', rotation=45)
ax.grid(True, linestyle='--', alpha=0.7, axis='y')
ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{int(x):,}'))
ax.xaxis.set_major_locator(mdates.AutoDateLocator())
plt.ylim(0.01)
plt.tight_layout()
plt.savefig(f'{assets_dir}/ak-number-of-products-tested-by-month.png', dpi=96, bbox_inches='tight', transparent=False, facecolor='white')
plt.show()


#------------------------------------------------------------------------------
# Analysis: Alaska producers over time.
#------------------------------------------------------------------------------

# Visualization of the number of producers over time.
sample = results.copy()
sample['date_tested'] = pd.to_datetime(sample['date_tested'])
sample['month'] = sample['date_tested'].dt.to_period('M').dt.to_timestamp()
producer_counts = sample.groupby('month')['producer_id'].nunique().reset_index()
plt.figure(figsize=(17.5, 7.5))
plt.plot(producer_counts['month'], producer_counts['producer_id'], marker='o')
plt.title('Monthly Number of Cannabis Producers in Alaska', pad=15)
plt.xlabel('')
plt.ylabel('Number of Producers')
plt.xticks(rotation=45)
plt.grid(True, linestyle='--', alpha=0.7)
plt.ylim(0.01)
plt.tight_layout()
plt.savefig(f'{assets_dir}/ak-number-of-producers-over-time.png', dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()

# Visualization of the marker share of producer over time.
monthly_producer_tests = sample.groupby(['month', 'producer_id'])['label'].count().unstack(fill_value=0)
market_share = monthly_producer_tests.div(monthly_producer_tests.sum(axis=1), axis=0)
top_producers = market_share.sum().sort_values(ascending=False).head(10).index
market_share_top = market_share[top_producers]
market_share_top['Others'] = 1 - market_share_top.sum(axis=1)
column_order = ['Others'] + top_producers.tolist()
colors = sns.color_palette("tab20", n_colors=len(column_order))
fig, ax = plt.subplots(figsize=(17.5, 7.5))
ax.stackplot(
    market_share_top.index,
    [market_share_top[col] for col in column_order],
    labels=column_order,
    colors=colors,
    alpha=0.8
)
ax.set_title('Monthly Market Share of the Top 10 Producers in Alaska', pad=10)
ax.set_xlabel('')
ax.set_ylabel('Market Share')
ax.xaxis.set_major_formatter(plt.FixedFormatter(market_share_top.index.strftime('%Y-%m')))
plt.xticks(rotation=45)
plt.grid(True, linestyle='--', alpha=0.7)
handles, labels = ax.get_legend_handles_labels()
legend_labels = [f"{label} ({market_share_top[label].iloc[-1]:.1%})" for label in column_order]
plt.legend(reversed(handles), reversed(legend_labels), title='Producer ID (Latest Share)', loc='center left', bbox_to_anchor=(1, 0.5))
plt.tight_layout(rect=[0, 0, 0.85, 1])
plt.savefig(f'{assets_dir}/ak-producer-market-share.png', dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()


#------------------------------------------------------------------------------
# Analysis: Product types.
#------------------------------------------------------------------------------

# Define colors for product types.
product_type_colors = {
    'Edible': '#FF6F61',  # Soft Coral Red
    'Immature Bud': '#b9a281',  # Warm Amber Orange
    'Leaf/Trim': '#42A5F5',  # Sky Blue
    'Bud/Flower': '#66BB6A',  # Fresh Leaf Green
    'Sample Bud/Flower': '#FFD54F',  # Soft Sunflower Yellow
    'Concentrate': '#AB47BC',  # Lavender Purple
    'Other': '#B0BEC5',  # Light Grey for "Other"
}

# Visualize the proportion of product types.
counts = results['product_type'].value_counts()
threshold = 1750
other_count = counts[counts < threshold].sum()
counts = counts[counts >= threshold]
counts['Other'] = other_count
colors = [product_type_colors.get(product, product_type_colors['Other']) for product in counts.index]
plt.figure(figsize=(12, 12))
plt.pie(
    counts,
    labels=counts.index,
    autopct='%1.1f%%',
    startangle=140,
    colors=colors,
)
plt.axis('equal')
plt.savefig(f'{assets_dir}/ak-product-types.png', dpi=96, bbox_inches='tight', transparent=False, facecolor='white')
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
    'Bud/Flower',
    'Sample Bud/Flower',
    'Immature Bud',
    'Concentrate',
    'Edible',
    'Other'
]
product_types_present = [pt for pt in product_types_order if pt in data_pivot.columns]
data_pivot = data_pivot[product_types_present]
data_pivot = data_pivot.sort_index()
data_pivot.index = data_pivot.index.strftime('%Y-%m')

fig, ax = plt.subplots(figsize=(21.5, 7.5))
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
ax.set_title('Proportion of Product Types Tested by Month in Alaska', pad=10)
ax.set_xlabel('')
ax.set_ylabel('')
ax.tick_params(axis='x', rotation=45)
ax.set_ylim(0.01, 1)
plt.xlim('2016-10', '2024-05')
ax.yaxis.set_major_formatter(plt.FuncFormatter('{0:.0%}'.format))
xticks = data_pivot.index[::6]
ax.set_xticks(xticks)
ax.legend(
    title='Product Type',
    title_fontsize=24,
    fontsize=21,
    loc='upper left',
    bbox_to_anchor=(1.02, 1),
    borderaxespad=0
)
plt.tight_layout(rect=[0, 0, 0.85, 1])
plt.savefig(f'{assets_dir}/ak-product-types-by-month.png', dpi=96, bbox_inches='tight', transparent=False, facecolor='white')
plt.show()


#------------------------------------------------------------------------------
# Analysis: Total THC over time by lab.
#------------------------------------------------------------------------------

from cannlytics.data.coas import standardize_results

# Restrict to flower.
flower = results.loc[
    (results['product_type'] == 'Bud/Flower') |
    (results['product_type'] == 'Sample Bud/Flower')
]

# Calculate total THC.
flower['dry_total_thc'] = flower['thca'].mul(0.877) + flower['delta_9_thc']

# Process Total THC over time by lab.
flower['date_tested'] = pd.to_datetime(flower['date_tested'])
flower['month'] = flower['date_tested'].dt.to_period('M').dt.to_timestamp()
flower = flower[
    (flower['dry_total_thc'] > 0)
]

# Consistent lab color palette.
labs = list(flower['lab'].unique())
color_map = cm.get_cmap('tab20', len(labs))
lab_colors = {lab: color_map(i) for i, lab in enumerate(labs)}

# Visualization of Total THC over time by lab.
fig, ax = plt.subplots(figsize=(17.5, 7.5))
monthly_avg_thc = flower.groupby(['month', 'lab'])['dry_total_thc'].mean().reset_index()
sns.lineplot(
    data=monthly_avg_thc,
    x='month',
    y='dry_total_thc',
    hue='lab',
    palette=lab_colors,
    linewidth=2.5,
    ax=ax,
    legend=True
)
ax.set_title('Dry Total THC (%) in Flower by Lab in Alaska', pad=10)
ax.set_xlabel('')
ax.set_ylabel('Total THC (%)', labelpad=10)
ax.tick_params(axis='x', rotation=45)
ax.set_ylim(0.001, 30)
ax.legend(
    title='Lab',
    title_fontsize=18,
    fontsize=16,
    loc='lower right',
    borderaxespad=0
)
plt.xlim(flower['date_tested'].min(), flower['date_tested'].max())
plt.tight_layout()
plt.savefig(f'{assets_dir}/ak-total-thc-over-time-by-lab.png', dpi=96, bbox_inches='tight', transparent=False, facecolor='white')
plt.show()

# Table of average THC by lab.
avg_thc_by_lab = flower.groupby('lab')['dry_total_thc'].mean().reset_index()
avg_thc_by_lab.columns = ['Lab', 'Avg. Total THC (%)']
avg_thc_by_lab['Avg. Total THC (%)'] = avg_thc_by_lab['Avg. Total THC (%)'].map('{:.2f}'.format)
table_thc_by_lab_latex = avg_thc_by_lab.to_latex(
    index=False,
    header=True,
    caption='Average Total THC by Lab in Flower Samples',
    column_format='lr',
    escape=True
)
print(table_thc_by_lab_latex)

# # Calculate the difference of THC by lab.
# # Note: Restrict to operating labs.
# two_labs = flower[flower['lab'].isin(['Land & Seas Laboratory', 'CANNTEST, LLC'])]
# land_seas_thc = two_labs[two_labs['lab'] == 'Land & Seas Laboratory']['dry_total_thc']
# canntest_thc = two_labs[two_labs['lab'] == 'CANNTEST, LLC']['dry_total_thc']
# stat, p = stats.ttest_ind(land_seas_thc, canntest_thc, equal_var=False)
# print(f"T-test results between 'Land & Seas Laboratory' and 'CANNTEST, LLC':")
# print(f"Statistic={stat:.3f}, p-value={p:.3f}")
# if p < 0.05:
#     print("Significant difference in Total THC between the two labs.")
# else:
#     print("No significant difference in Total THC between the two labs.")

# Visualize the distribution of THC by lab.
flower['short_name'] = flower['lab'].apply(lambda x: x[:11] + '...')
fig, ax = plt.subplots(figsize=(12.5, 7.5))
sns.boxplot(
    x='short_name',
    y='dry_total_thc',
    data=flower,
    hue='lab',
    palette=lab_colors,
    legend=False,
)
plt.title('Dry Total THC (%) by Lab In Alaska', pad=10)
plt.xlabel('')
plt.ylabel('Total THC (%)', labelpad=10)
plt.ylim(0.01, 50)
plt.xticks(rotation=45)
plt.tight_layout()
plt.savefig(f'{assets_dir}/ak-total-thc-by-lab-boxplot.png', dpi=96, bbox_inches='tight', transparent=False, facecolor='white')
plt.show()


#------------------------------------------------------------------------------
# Analysis: Aggregate statistics.
#------------------------------------------------------------------------------

# Output summary statistics to LaTeX.
results['date_tested'] = pd.to_datetime(results['date_tested'])
summary = pd.DataFrame({
    'Start Date': [results['date_tested'].min().strftime('%Y-%m-%d')],
    'End Date': [results['date_tested'].max().strftime('%Y-%m-%d')],
    'Products Tested': [f'{total_results:,}'],
})
output = summary.T
table = output.to_latex(index=True, header=False)
table = table.replace('\n\\midrule', '')
print(table)
