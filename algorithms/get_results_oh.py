"""
Get Results | Ohio
Copyright (c) 2024 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 10/3/2024
Updated: 10/3/2024
License: CC-BY 4.0 <https://huggingface.co/datasets/cannlytics/cannabis_tests/blob/main/LICENSE>

Description:

    Collect all public Ohio lab result data.

Data Sources:
    
    - Public records request from the State of Ohio.

"""
# External imports:
from matplotlib import pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import seaborn as sns

#------------------------------------------------------------------------------
# Setup.
#------------------------------------------------------------------------------

# Define where figures are saved.
assets_dir = r"C:\Users\keega\Documents\cannlytics\cannabis-data-science\season-4\179-reviews\presentation\images\figures"

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
# Read and process the data.
#------------------------------------------------------------------------------

# Read the data.
datafile = r"D:\data\ohio\Ohio-20240912T203604Z-001\Ohio\Metrc-Ohio-DatabaseQuery - 2024-06-20T123647.470.xlsx"
tests = pd.read_excel(datafile)
print('Number of tests:', len(tests))

# Standardize columns.
columns = {
    'Date': 'date_tested',
    'Tag': 'product_id',
    'Analyte': 'test_name',
    'Result': 'value',
    'Disposition': 'status',
    'Notes': 'notes',
}
tests = tests.rename(columns=columns)


def clean_test_names(test_name):
    """Clean test names."""
    return test_name.split('(')[0].strip()


# Remove extraneous test details.
tests['test_name'] = tests['test_name'].apply(clean_test_names)

# Pivot the data by label.
results = tests.pivot_table(
    index=['product_id', 'date_tested'],
    columns='test_name',
    values='value',
    aggfunc='first'
).reset_index()
print('Number of products:', len(results))

def get_date_tested(product_id):
    """Get the date tested for a product."""
    return tests[tests['product_id'] == product_id]['date_tested'].max()

# Augment producer ID.
results['producer_id'] = results['product_id'].apply(lambda x: x[12:15])

# Save the data.
date = results['date_tested'].max()
outfile = f'D:/data/ohio/oh-results-{date}.xlsx'
results.to_excel(outfile, index=False)
print(f'Saved results to: {outfile}')


#------------------------------------------------------------------------------
# Analysis: Ohio number of tests over time.
#------------------------------------------------------------------------------

# Visualize the number of products tested over time.
sample = results.copy()
sample['date_tested'] = pd.to_datetime(sample['date_tested'])
sample['month'] = sample['date_tested'].dt.to_period('M').dt.to_timestamp()
monthly_test_counts = sample.groupby('month').size().reset_index(name='count')
fig, ax = plt.subplots(figsize=(15.5, 5))
ax.bar(
    monthly_test_counts['month'].dt.strftime('%Y-%m'),
    monthly_test_counts['count'],
    color='skyblue',
    edgecolor='white',
    width=0.8
)
ax.set_title('Number of Medical Flower Products Tested by Month in Ohio', pad=10)
ax.set_xlabel('')
ax.set_ylabel('Number of Products')
ax.tick_params(axis='x', rotation=45)
ax.grid(True, linestyle='--', alpha=0.7, axis='y')
ax.xaxis.set_major_locator(mdates.AutoDateLocator())
plt.ylim(0.01)
plt.tight_layout()
plt.savefig(f'{assets_dir}/ohio-number-of-products-tested-by-month.png', dpi=96, bbox_inches='tight', transparent=False, facecolor='white')
plt.show()


#------------------------------------------------------------------------------
# Analysis: Ohio medical producers over time.
#------------------------------------------------------------------------------

# Standardize timeseries.
sample = results.copy()
sample['date_tested'] = pd.to_datetime(sample['date_tested'])
sample['month'] = sample['date_tested'].dt.to_period('M').dt.to_timestamp()

# Visualization of the number of producers over time.
producer_counts = sample.groupby('month')['producer_id'].nunique().reset_index()
plt.figure(figsize=(12.5, 5))
plt.plot(producer_counts['month'], producer_counts['producer_id'], marker='o')
plt.title('Monthly Number of Medical Cannabis Producers in Ohio', pad=10)
plt.xlabel('')
plt.ylabel('Number of Producers')
plt.xticks(rotation=45)
plt.grid(True, linestyle='--', alpha=0.7)
plt.ylim(0.01)
plt.tight_layout()
plt.savefig(f'{assets_dir}/ohio-number-of-producers-over-time.png', dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()

# Visualization of the marker share of producer over time.
monthly_producer_tests = sample.groupby(['month', 'producer_id'])['product_id'].count().unstack(fill_value=0)
market_share = monthly_producer_tests.div(monthly_producer_tests.sum(axis=1), axis=0)
top_producers = market_share.sum().sort_values(ascending=False).head(10).index
market_share_top = market_share[top_producers]
market_share_top['Others'] = 1 - market_share_top.sum(axis=1)
column_order = ['Others'] + top_producers.tolist()
colors = sns.color_palette("tab20", n_colors=len(column_order))
fig, ax = plt.subplots(figsize=(15.5, 5))
ax.stackplot(
    market_share_top.index,
    [market_share_top[col] for col in column_order],
    labels=column_order,
    colors=colors,
    alpha=0.8
)
ax.set_title('Monthly Market Share of the Top 10 Producers in Ohio', pad=10)
ax.set_xlabel('')
ax.set_ylabel('Market Share')
plt.xticks(rotation=45)
plt.grid(True, linestyle='--', alpha=0.7)
handles, labels = ax.get_legend_handles_labels()
legend_labels = [f"{label} ({market_share_top[label].iloc[-1]:.1%})" for label in column_order]
plt.legend(reversed(handles), reversed(legend_labels), title='Producer ID (Latest Share)', loc='center left', bbox_to_anchor=(1, 0.5))
plt.tight_layout(rect=[0, 0, 0.85, 1])
plt.savefig(f'{assets_dir}/ohio-producer-market-share.png', dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()


#------------------------------------------------------------------------------
# Analysis: Ohio medical total THC concentrations over time.
#------------------------------------------------------------------------------

# Visualize total THC over time.
sample = results.copy()
sample['total_thc'] = sample['THC'] + sample['THCA'] * 0.877
sample = sample[sample['total_thc'] > 0]
sample['date_tested'] = pd.to_datetime(sample['date_tested'])
sample['month'] = sample['date_tested'].dt.to_period('M').dt.to_timestamp()
sample = sample[(sample['total_thc'] >= 0) & (sample['total_thc'] <= 40)]
plt.figure(figsize=(15.5, 5))
sns.scatterplot(
    data=sample,
    x='date_tested',
    y='total_thc',
    color='paleturquoise',
    alpha=0.5,
    s=50
)
monthly_avg_thc = sample.groupby('month')['total_thc'].mean().reset_index()
sns.lineplot(
    data=monthly_avg_thc,
    x='month',
    y='total_thc',
    color='lightseagreen',
    linewidth=2.5,
    label='Average Total THC'
)
plt.title('Total THC (%) of Medical Flower in Ohio', pad=10)
plt.xlabel('')
plt.ylabel('Total THC (%)')
plt.xticks(rotation=45)
plt.legend()
plt.xlim(
    pd.to_datetime('2019-01-01').to_period('M').to_timestamp(),
    pd.to_datetime('2023-07-01').to_period('M').to_timestamp()
)
plt.tight_layout()
plt.savefig(f'{assets_dir}/ohio-total-thc-over-time.png', dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()

# Table of average THC by year.
sample['year'] = sample['date_tested'].dt.year
avg_thc_by_year = sample.groupby('year')['total_thc'].mean().reset_index()
avg_thc_by_year.columns = ['Year', 'Average Total THC (%)']
avg_thc_by_year = avg_thc_by_year.round(1)
table_avg_thc_by_year_latex = avg_thc_by_year.to_latex(
    index=False,
    header=True,
    caption='Average Total THC by Year in Ohio',
    column_format='lr',
    float_format="%.1f",  # Ensures 1 decimal place
    escape=False
)
print(table_avg_thc_by_year_latex)

# Visualize average total THC by producer over time.
monthly_avg_by_producer = sample.groupby(['month', 'producer_id'])['total_thc'].mean().reset_index()
avg_thc_pivot = monthly_avg_by_producer.pivot(index='month', columns='producer_id', values='total_thc')
plt.figure(figsize=(15.5, 5))
for producer in avg_thc_pivot.columns:
    plt.plot(
        avg_thc_pivot.index,
        avg_thc_pivot[producer],
        linewidth=1.5,
        alpha=0.5
    )
sns.lineplot(
    data=monthly_avg_thc,
    x='month',
    y='total_thc',
    color='royalblue',
    linewidth=2.5,
    label='Average Total THC'
)
plt.title('Average Total THC by Producer Over Time in Ohio', pad=10)
plt.xlabel('')
plt.ylabel('Average Total THC (%)')
plt.xticks(rotation=45)
plt.ylim(0.01)
plt.xlim(
    pd.to_datetime('2019-01-01').to_period('M').to_timestamp(),
    pd.to_datetime('2023-07-01').to_period('M').to_timestamp()
)
plt.tight_layout(rect=[0, 0, 0.85, 1])
plt.savefig(f'{assets_dir}/ohio-avg-total-thc-by-producer-over-time.png', dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()

# Scatter plot of producer market share by to producer avg. total THC.
producer_counts = sample.groupby('producer_id').size()
total_products = sample.shape[0]
producer_market_share = (producer_counts / total_products) * 100  # Convert to percentage.
avg_total_thc_by_producer = sample.groupby('producer_id')['total_thc'].mean()
producer_stats = pd.DataFrame({
    'producer_id': producer_counts.index,
    'market_share': producer_market_share.values,
    'avg_total_thc': avg_total_thc_by_producer.values
})
plt.figure(figsize=(12.5, 7))
scatter = plt.scatter(
    producer_stats['market_share'],
    producer_stats['avg_total_thc'],
    s=180,
    alpha=0.7,
    c=producer_stats['market_share'],
    cmap='viridis_r',
)
plt.hlines(
    y=producer_stats['avg_total_thc'].mean(),
    xmin=0,
    xmax=13,
    color='lightseagreen',
    linestyle='--',
    linewidth=2.5,
    label='Unconditional Avg. Total THC'
)
plt.legend()
plt.xlim(0, 13)
plt.title('Producer Average Total THC to Market Share in Ohio', pad=10)
plt.xlabel('Market Share (%)')
plt.ylabel('Avg. Total THC (%)')
plt.grid(True, linestyle='--', alpha=0.7)
plt.tight_layout()
plt.savefig(f'{assets_dir}/ohio-producer-market-share-to-avg-thc.png', dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()


#------------------------------------------------------------------------------
# Analysis: Aggregate statistics.
#------------------------------------------------------------------------------

# Output summary statistics to LaTeX.
results['date_tested'] = pd.to_datetime(results['date_tested'])
summary = pd.DataFrame({
    'Start Date': [results['date_tested'].min().strftime('%Y-%m-%d')],
    'End Date': [results['date_tested'].max().strftime('%Y-%m-%d')],
    'Total Tests': [f'{len(tests):,}'],
    'Total Products': [f'{len(results):,}'],
})
output = summary.T
table = output.to_latex(index=True, header=False)
table = table.replace('\n\\midrule', '')
print(table)
