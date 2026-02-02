"""
Get Results | New Jersey
Copyright (c) 2024 Cannlytics

Authors: Keegan Skeate <https://github.com/keeganskeate>
Created: 9/4/2024
Updated: 9/4/2024
License: MIT License <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>
"""
# Standard imports:
from datetime import datetime
import os
import re

# External imports:
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import StrMethodFormatter
from matplotlib import cm
import numpy as np
import pandas as pd
import seaborn as sns
from scipy import stats


#------------------------------------------------------------------------------
# Setup.
#------------------------------------------------------------------------------

# Define where to save figures.
assets_dir = r'C:\Users\keega\Documents\cannlytics\cannabis-data-science\season-4\175-state-markets\presentation\images\figures'

# Define where the data lives.
data_dir = 'D://data/new-jersey/public-records'

# Setup plotting style.
plt.style.use('seaborn-v0_8-whitegrid')
plt.rcParams.update({
    'font.family': 'Times New Roman',
    'font.size': 24,
})

# Define columns.
nj_result_columns = {
    'TestPerformedDate': 'date_tested',
    'METRCTag': 'label',
    'TestTypeName': 'test_type',
    'TestTypeNamePart': 'test_name',
    'TestPassed': 'status',
    'TestResultLevel': 'value',
    'TestComment': 'notes',
    'TestingFacilityId': 'lab_id',
}


#------------------------------------------------------------------------------
# Data: Read the results.
#------------------------------------------------------------------------------

def extract_product_type(x: str) -> str:
    """Extract product type from a test type name."""
    parts = re.split(r'\(|\)', x)
    parts = [part.strip() for part in parts if part.strip()]
    if len(parts) <= 1:
        return 'R&D Testing'
    if "R&D Testing" in parts[-1]:
        product_type = parts[-1].split("R&D Testing")[-1].strip()
    else:
        product_type = parts[-1].strip()
    if product_type == '':
        product_type = 'R&D Testing'
    return product_type

def extract_units(x: str) -> str:
    """Extract units from a test type name."""
    return x.split('(')[-1].split(')')[0].strip()

def identify_r_and_d(x: str) -> bool:
    """Identify if a test is R&D."""
    return "R&D" in x


# Read all of the results.
all_results = []
total_tests = 0
datafiles = os.listdir(data_dir)
for datafile in datafiles:

    # Read the data.
    filepath = os.path.join(data_dir, datafile)
    data = pd.read_csv(filepath)
    data.rename(columns=nj_result_columns, inplace=True)
    total_tests += len(data)

    # Get product type, units, and r_and_d status.
    data['product_type'] = data['test_type'].apply(extract_product_type)
    data['r_and_d'] = data['test_type'].apply(identify_r_and_d)

    # Pivot the data by label.
    results = data.pivot_table(
        index=['label', 'date_tested', 'lab_id', 'product_type'],
        columns='test_name',
        values='value',
        aggfunc='first'
    ).reset_index()

    # Assign status, notes, and R&D status.
    for index, row in results.iterrows():
        sample_results = data.loc[data['label'] == row['label']]
        results.loc[index, 'status'] = 'Pass' if sample_results['status'].all() else 'Fail'
        results.loc[index, 'r_and_d'] = sample_results['r_and_d'].any()
        # Optional: Also get notes.

    # Record the results.
    all_results.append(results)
    print(f'Processed {datafile} with {len(results)} samples.')

# Aggregate all of the results.
results = pd.concat(all_results, axis=0)
print(f'Aggregated results for {len(results)} products.')

# Optional: Identify units for each test type.

# Restrict the data by time.
results['date'] = pd.to_datetime(results['date_tested'], format='%Y-%m-%d')
results['month'] = results['date'].dt.to_period('M')
results = results[(results['date'] >= '2023-02-01') & (results['date'] < '2024-07-01')]

# Save the results.
# results.to_csv('D://data/new-jersey/nj-results-latest.csv', index=False)

# Exclude quality control tests.
qc_types = [
    'Additional',
    'R&D Testing',
    'Stability T1',
    'Stability T2',
    'Stability T3',
    'Stability T0',
]
sample = results[~results['product_type'].isin(qc_types)]

# Standardize product types.
product_type_mapping = {
    'Raw Plant Material': 'Flower',
    'Raw Plant Material & Concentrate/Extract': 'Mix',
    'Raw Plant Material & Conentrate/Extract': 'Mix',
    'Inhalable/Vape Concentrate': 'Vape',
    'Solvent Based Concentrate/Extract': 'Solvent-based Concentrate',
    'Non-Solvent Concentrate': 'Non-solvent Concentrate',
    'Infused Non-Edible': 'Infused',
    'Infused Edible': 'Edible',
    'Infused Products': 'Infused',
    'Edibles': 'Edible',
}
sample['product_type'] = sample['product_type'].map(product_type_mapping)

# Clean column names by stripping white space.
sample.columns = sample.columns.str.strip()

# Reset index to ensure it's unique
sample = sample.reset_index(drop=True)

# Consistent lab colors.
all_labs = results['lab_id'].unique()
n_colors = len(all_labs)
color_palette = plt.cm.get_cmap('tab20')
lab_colors = {lab: color_palette(i/n_colors) for i, lab in enumerate(all_labs)}


#------------------------------------------------------------------------------
# Analysis: Number of tests over time.
#------------------------------------------------------------------------------

# Plot the number of tests over time.
test_counts = results.groupby('month').size().reset_index(name='count')
test_counts['month'] = test_counts['month'].dt.to_timestamp()
avg_tests_per_month = test_counts['count'].mean()
plt.figure(figsize=(15, 8))
plt.plot(test_counts['month'], test_counts['count'], marker='o')
plt.title('Monthly Number of Cannabis Products Tested in New Jersey', pad=20)
plt.xlabel('')
plt.ylabel('Products Tested')
plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
plt.gcf().autofmt_xdate()
plt.grid(True, linestyle='--', alpha=0.7)
for i, row in test_counts.iterrows():
    text = row['count']
    plt.annotate(f'{text:,}', (row['month'], row['count']), 
                 xytext=(0, 5), textcoords='offset points', ha='center')
plt.axhline(avg_tests_per_month, color='lightblue', linestyle='--', label=f'Average: {avg_tests_per_month:.0f}')
plt.ylim(0, 4800)
plt.tight_layout()
outfile = os.path.join(assets_dir, 'nj-results-timeseries.png')
plt.savefig(outfile, dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()


#------------------------------------------------------------------------------
# Analysis: Producers.
#------------------------------------------------------------------------------

# Assign producer ID.
results['producer_id'] = results['label'].apply(lambda x: x[12:15])

# Visualize the number of producers operating each month.
producer_counts = results.groupby(results['date'].dt.to_period('M'))['producer_id'].nunique().reset_index()
producer_counts['date'] = producer_counts['date'].dt.to_timestamp()
plt.figure(figsize=(15, 8.5))
plt.plot(producer_counts['date'], producer_counts['producer_id'], marker='o')
plt.title('Monthly Number of Producers in New Jersey', pad=20)
plt.xlabel('')
plt.ylabel('Producers')
plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
plt.gcf().autofmt_xdate()
plt.grid(True, linestyle='--', alpha=0.7)
for i, row in producer_counts.iterrows():
    plt.annotate(str(row['producer_id']), (row['date'], row['producer_id']), 
                 xytext=(0, 5), textcoords='offset points', ha='center')
plt.ylim(0, 55)
plt.tight_layout()
outfile = os.path.join(assets_dir, 'nj-producers-timeseries.png')
plt.savefig(outfile, dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()

# Visualize the number of tests per producer per month.
monthly_metrics = results.groupby(results['date'].dt.to_period('M')).agg({
    'producer_id': 'nunique',
    'label': 'count'
}).reset_index()
monthly_metrics['avg_tests_per_producer'] = monthly_metrics['label'] / monthly_metrics['producer_id']
monthly_metrics['date'] = monthly_metrics['date'].dt.to_timestamp()
avg_num_tests_per_producer = monthly_metrics['label'].sum() / monthly_metrics['producer_id'].sum()
plt.figure(figsize=(15, 8.5))
plt.plot(monthly_metrics['date'], monthly_metrics['avg_tests_per_producer'], marker='o')
plt.title('Monthly Average Number of Products Tested per Producer in New Jersey', pad=20)
plt.xlabel('')
plt.ylabel('Average Products Tested per Producer')
plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
plt.gcf().autofmt_xdate()
plt.grid(True, linestyle='--', alpha=0.7)
for i, row in monthly_metrics.iterrows():
    plt.annotate(f"{row['avg_tests_per_producer']:.0f}", (row['date'], row['avg_tests_per_producer']), 
                 xytext=(0, 5), textcoords='offset points', ha='center')
plt.axhline(avg_num_tests_per_producer, color='lightblue', linestyle='--', label=f'Average: {avg_num_tests_per_producer:.0f}')
plt.ylim(0, 210)
plt.tight_layout()
outfile = os.path.join(assets_dir, 'nj-tests-per-producer-timeseries.png')
plt.savefig(outfile, dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()

# Visualize monthly market share of producers.
monthly_producer_tests = results.groupby([results['date'].dt.to_period('M'), 'producer_id'])['label'].count().unstack(fill_value=0)
market_share = monthly_producer_tests.div(monthly_producer_tests.sum(axis=1), axis=0)
top_producers = market_share.sum().sort_values(ascending=False).head(10).index
market_share_top = market_share[top_producers]
market_share_top['Others'] = 1 - market_share_top.sum(axis=1)
column_order = market_share_top.sum().sort_values().index.tolist()
column_order.remove('Others')
column_order = ['Others'] + column_order
colors = sns.color_palette("tab20", n_colors=len(column_order))
fig, ax = plt.subplots(figsize=(17.5, 8.5))
ax.stackplot(
    market_share_top.index.to_timestamp(),
    [market_share_top[col] for col in column_order],
    labels=column_order,
    colors=colors,
    alpha=0.8
)
plt.title('Monthly Market Share of Top 10 Producers in New Jersey', pad=20)
plt.xlabel('')
plt.ylabel('Market Share')
ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
ax.xaxis.set_major_locator(mdates.AutoDateLocator())
fig.autofmt_xdate()
plt.grid(True, linestyle='--', alpha=0.7)
handles, labels = ax.get_legend_handles_labels()
legend_labels = [f"{label} ({market_share_top[label].iloc[-1]:.1%})" for label in column_order]
plt.legend(reversed(handles), reversed(legend_labels), title='Producer ID (Latest Share)', loc='center left', bbox_to_anchor=(1, 0.5))
plt.tight_layout()
outfile = os.path.join(assets_dir, 'nj-producer-market-share.png')
plt.savefig(outfile, dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()


#------------------------------------------------------------------------------
# Analysis: Labs.
#------------------------------------------------------------------------------

# Visualize the number of labs operating each month.
lab_counts = results.groupby(results['date'].dt.to_period('M'))['lab_id'].nunique().reset_index()
lab_counts['date'] = lab_counts['date'].dt.to_timestamp()
plt.figure(figsize=(15, 8.5))
plt.plot(lab_counts['date'], lab_counts['lab_id'], marker='o')
plt.title('Monthly Number of Labs in New Jersey', pad=20)
plt.xlabel('')
plt.ylabel('Labs')
plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
plt.gcf().autofmt_xdate()
plt.grid(True, linestyle='--', alpha=0.7)
for i, row in lab_counts.iterrows():
    plt.annotate(str(row['lab_id']), (row['date'], row['lab_id']), 
                 xytext=(0, 5), textcoords='offset points', ha='center')
plt.ylim(0, max(lab_counts['lab_id']) * 1.1)  # Set y-axis limit to 110% of max value
plt.tight_layout()
outfile = os.path.join(assets_dir, 'nj-labs-timeseries.png')
plt.savefig(outfile, dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()

# Visualize the number of tests per lab per month.
monthly_lab_metrics = results.groupby(results['date'].dt.to_period('M')).agg({
    'lab_id': 'nunique',
    'label': 'count'
}).reset_index()
monthly_lab_metrics['avg_tests_per_lab'] = monthly_lab_metrics['label'] / monthly_lab_metrics['lab_id']
monthly_lab_metrics['date'] = monthly_lab_metrics['date'].dt.to_timestamp()
avg_num_tests_per_lab = monthly_lab_metrics['label'].sum() / monthly_lab_metrics['lab_id'].sum()
plt.figure(figsize=(15, 8.5))
plt.plot(monthly_lab_metrics['date'], monthly_lab_metrics['avg_tests_per_lab'], marker='o')
plt.title('Monthly Average Number of Tests per Lab in New Jersey', pad=20)
plt.xlabel('')
plt.ylabel('Average Tests per Lab')
plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
plt.gcf().autofmt_xdate()
plt.grid(True, linestyle='--', alpha=0.7)
for i, row in monthly_lab_metrics.iterrows():
    plt.annotate(f"{row['avg_tests_per_lab']:.0f}", (row['date'], row['avg_tests_per_lab']), 
                 xytext=(0, 5), textcoords='offset points', ha='center')
plt.axhline(avg_num_tests_per_lab, color='lightblue', linestyle='--', label=f'Average: {avg_num_tests_per_lab:.0f}')
plt.ylim(0, max(monthly_lab_metrics['avg_tests_per_lab']) * 1.1)  # Set y-axis limit to 110% of max value
plt.legend()
plt.tight_layout()
outfile = os.path.join(assets_dir, 'nj-tests-per-lab-timeseries.png')
plt.savefig(outfile, dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()

# Visualize monthly market share of labs.
monthly_lab_tests = results.groupby([results['date'].dt.to_period('M'), 'lab_id'])['label'].count().unstack(fill_value=0)
lab_market_share = monthly_lab_tests.div(monthly_lab_tests.sum(axis=1), axis=0)
top_labs = lab_market_share.sum().sort_values(ascending=False).head(10).index
lab_market_share_top = lab_market_share[top_labs]
column_order = lab_market_share_top.sum().sort_values(ascending=False).index.tolist()
colors = sns.color_palette("tab20", n_colors=len(column_order))
fig, ax = plt.subplots(figsize=(17.5, 8.5))
ax.stackplot(
    lab_market_share_top.index.to_timestamp(),
    [lab_market_share_top[col] for col in column_order],
    labels=column_order,
    colors=[lab_colors[lab] for lab in column_order],
    alpha=0.8
)
plt.title('Monthly Market Share of Top 10 Labs in New Jersey', pad=20)
plt.xlabel('')
plt.ylabel('Market Share')
ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
ax.xaxis.set_major_locator(mdates.AutoDateLocator())
fig.autofmt_xdate()
plt.grid(True, linestyle='--', alpha=0.7)
handles, labels = ax.get_legend_handles_labels()
legend_labels = [f"{label} ({lab_market_share_top[label].iloc[-1]:.1%})" for label in column_order]
plt.legend(handles[::-1], legend_labels[::-1], title='Lab ID (Latest Share)', loc='center left', bbox_to_anchor=(1, 0.5))
plt.tight_layout()
outfile = os.path.join(assets_dir, 'nj-lab-market-share.png')
plt.savefig(outfile, dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()


#------------------------------------------------------------------------------
# Analysis: Tests per capita, dispensary, etc.
#------------------------------------------------------------------------------

# # Calculate the average number of tests per 100,000 per month.
# population = 9_262_000
# monthly_tests_per_capita = test_counts.copy()
# monthly_tests_per_capita['tests_per_capita'] = monthly_tests_per_capita['count'] / population * 100_000
# avg_tests_per_capita = monthly_tests_per_capita['tests_per_capita'].mean()
# plt.figure(figsize=(15, 8.5))
# plt.plot(monthly_tests_per_capita['month'], monthly_tests_per_capita['tests_per_capita'], marker='o')
# plt.title('Monthly Number of Cannabis Products Tested per Capita in New Jersey')
# plt.xlabel('')
# plt.ylabel('Products Tested per 100,000')
# plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
# plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
# plt.gcf().autofmt_xdate()
# plt.grid(True, linestyle='--', alpha=0.7)
# for i, row in monthly_tests_per_capita.iterrows():
#     plt.annotate(f"{row['tests_per_capita']:.0f}", (row['month'], row['tests_per_capita']), 
#                  xytext=(0, 5), textcoords='offset points', ha='center')
# plt.axhline(avg_tests_per_capita, color='lightblue', linestyle='--', label=f'Average: {avg_tests_per_capita:.0f}')
# plt.ylim(0, 100)
# plt.tight_layout()
# # outfile = os.path.join(assets_dir, 'nj-tests-per-capita-timeseries.png')
# # plt.savefig(outfile, dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
# plt.show()

# Visualize the number of tests per lab.


#------------------------------------------------------------------------------
# Analysis: Product types.
#------------------------------------------------------------------------------

# Group by month and product type, count the number of tests
monthly_product_tests = sample.groupby([sample['date'].dt.to_period('M'), 'product_type'])['label'].count().unstack(fill_value=0)
product_type_percentage = monthly_product_tests.div(monthly_product_tests.sum(axis=1), axis=0) * 100
top_product_types = product_type_percentage.sum().sort_values(ascending=False).head(10).index
fig, ax = plt.subplots(figsize=(19.5, 8.5))
ax.stackplot(
    product_type_percentage.index.to_timestamp(),
    [product_type_percentage[col] for col in top_product_types],
    labels=top_product_types,
    alpha=0.8
)
plt.title('Monthly Distribution of Product Types Tested in New Jersey', pad=10)
plt.xlabel('')
plt.ylabel('Percentage of Tests')
ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
ax.xaxis.set_major_locator(mdates.AutoDateLocator())
fig.autofmt_xdate()
plt.grid(True, linestyle='--', alpha=0.7)
handles, labels = ax.get_legend_handles_labels()
latest_percentages = product_type_percentage[top_product_types].iloc[-1]
legend_labels = [f"{label} ({latest_percentages[label]:.1f}%)" for label in top_product_types]
plt.legend(handles[::-1], legend_labels[::-1], title='Product Type (Latest %)', loc='center left', bbox_to_anchor=(1, 0.5))
plt.tight_layout()
outfile = os.path.join(assets_dir, 'nj-product-types.png')
plt.savefig(outfile, dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()


#------------------------------------------------------------------------------
# Analysis: Cannabinoids.
#------------------------------------------------------------------------------

def plot_cannabinoid_scatter(
        df,
        x_cannabinoid,
        y_cannabinoid,
        xmax=100,
        ymax=100,
        save=True,
        outfile=None,
    ):
    """Create a scatter plot for a pair of cannabinoids."""
    if '_' in x_cannabinoid:
        x_label = x_cannabinoid.replace('_', ' ').title()
    else:
        x_label = x_cannabinoid
    if '_' in y_cannabinoid:
        y_label = y_cannabinoid.replace('_', ' ').title()
    else:
        y_label = y_cannabinoid
    plt.figure(figsize=(15, 8.5))
    sns.scatterplot(
        data=df,
        x=x_cannabinoid,
        y=y_cannabinoid,
        hue='product_type',
        alpha=0.6
    )
    plt.title(f'{y_label} to {x_label} by Product Type in NJ', pad=20)
    plt.xlabel(f'{x_label} (%)')
    plt.ylabel(f'{y_label} (%)')
    plt.legend(title='Product Type', bbox_to_anchor=(1.05, 1), loc='upper left')
    plt.xlim(0, xmax)
    plt.ylim(0, ymax)
    plt.tight_layout()
    if save:
        if outfile is None:
            outfile = os.path.join(assets_dir, f'nj-{y_cannabinoid.lower().replace(" ", "-")}-to-{x_cannabinoid.lower().replace(" ", "-")}.png')
        plt.savefig(outfile, dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
    plt.show()


# Define cannabinoids.
cannabinoids = [
    'CBD',
    'CBDA',
    'CBG',
    'CBN',
    'THC',
    'THCA',
    'Total CBD',
    'Total THC'
]

# Visualize THC to THCA
types = [
    'Flower',
    'Mix',
    'Vape',
    'Solvent-based Concentrate',
    'Non-solvent Concentrate'
]
subsample = sample.loc[sample['product_type'].isin(types)]
plot_cannabinoid_scatter(subsample, 'THCA', 'THC')


# Calculate flower stats.
flower = sample[sample['product_type'] == 'Flower']
flower['date'] = pd.to_datetime(flower['date_tested'], format='%Y-%m-%d')
flower['month'] = flower['date'].dt.to_period('M')
flower['month'] = flower['month'].dt.to_timestamp()
monthly_flower_tests = flower.groupby([flower['month']])['THCA'].mean().reset_index()

# Visualize average THCA in flower by month by lab.
monthly_lab_flower_tests = flower.groupby(['month', 'lab_id'])['THCA'].mean().reset_index()
monthly_lab_flower_tests['month'] = monthly_lab_flower_tests['month'].dt.to_timestamp()
labs = flower['lab_id'].unique()
plt.figure(figsize=(17.5, 8.5))
for lab in labs:
    lab_data = monthly_lab_flower_tests[monthly_lab_flower_tests['lab_id'] == lab]
    plt.plot(
        lab_data['month'],
        lab_data['THCA'],
        marker='o',
        linestyle='-',
        alpha=0.7,
        label=f'Lab {lab}',
        color=lab_colors[lab]
    )
plt.plot(monthly_flower_tests['month'], monthly_flower_tests['THCA'], marker='o', linestyle='-', linewidth=3, color='black', label='Overall Average')
plt.title('Monthly Average THCA in Flower by Lab in New Jersey', pad=10)
plt.xlabel('')
plt.ylabel('THCA (%)')
plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
plt.gcf().autofmt_xdate()
plt.grid(True, linestyle='--', alpha=0.7)
plt.legend(title='Lab ID', bbox_to_anchor=(1.05, 1), loc='upper left')
plt.tight_layout()
outfile = os.path.join(assets_dir, 'nj-thca-flower-lab-timeseries.png')
plt.savefig(outfile, dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()


#------------------------------------------------------------------------------
# Analysis: Terpenes.
#------------------------------------------------------------------------------

# Define terpenes.
terpenes = [
    'Alpha-Bisabolol',
    'Alpha-Humulene',
    'Alpha-Pinene',
    'Alpha-Terpinene',
    'Beta-Caryophyllene',
    'Beta-Myrcene',
    'Beta-Pinene',
    'Caryophyllene Oxide',
    'Eucalyptol',
    'Limonene',
    'Linalool',
    'Nerolidol',
    'Other Terpenes'
]

# Plot total THC to total terpenes.
sample['total_terpenes'] = sample[terpenes].sum(axis=1)
flower = sample[sample['product_type'] == 'Flower']

# Visualize total THC to total terpenes.
plot_cannabinoid_scatter(
    sample,
    'total_terpenes',
    'Total THC',
    xmax=20,
    ymax=100,
)

# Visualize beta-pinene to limonene in all product types..
plot_cannabinoid_scatter(
    sample,
    'Limonene',
    'Beta-Pinene',
    xmax=6,
    ymax=1.15,
)

# Visualize beta-pinene to limonene in flower.
outfile = os.path.join(assets_dir, 'nj-limonene-to-beta-pinene-flower.png')
plot_cannabinoid_scatter(
    flower,
    'Limonene',
    'Beta-Pinene',
    xmax=1.75,
    ymax=0.4,
    outfile=outfile
)


#------------------------------------------------------------------------------
# Analysis: Heavy Metals.
#------------------------------------------------------------------------------

# Define heavy metals.
heavy_metals = [
    'Arsenic',
    'Cadmium',
    'Chromium',
    'Lead',
    'Mercury'
]


# Calculate detection rates for each heavy metal by product type
detection_rates = {}
for metal in heavy_metals:
    metal_data = pd.to_numeric(sample[metal], errors='coerce')
    detection_rates[metal] = sample.groupby('product_type').apply(lambda x: (pd.to_numeric(x[metal], errors='coerce') > 0).mean() * 100)
detection_rates_df = pd.DataFrame(detection_rates)
detection_rates_df = detection_rates_df.fillna(0)
detection_rates_df = detection_rates_df.drop('Mix')

# Create a heatmap of detection rates
plt.figure(figsize=(14, 10))
sns.heatmap(
    detection_rates_df, 
    annot=True, 
    fmt='.1f',
    cmap='BuPu', 
    vmin=0, 
    vmax=16, 
    cbar_kws={'label': 'Detection Rate (%)'},
    linewidths=0.5,
    annot_kws={"size": 21}
)

plt.title('Detection Rates for Heavy Metals by Product Type in NJ', pad=20)
plt.ylabel('')
plt.xlabel('')
plt.xticks(rotation=45, ha='right')
plt.tight_layout()
outfile = os.path.join(assets_dir, 'nj-heavy-metals-detection-rates-heatmap.png')
plt.savefig(outfile, dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()

# Calculate failure rates for each heavy metal by product type.
# Source: https://www.nj.gov/cannabis/documents/businesses/Business%20Resources/CRC%20Testing%20Guidance%209.28.22.pdf
metal_limits = {
    'Arsenic': 0.2,
    'Cadmium': 0.2,
    'Chromium': 0.6,
    'Lead': 0.5,
    'Mercury': 0.2,
}
failure_rates = {}
for metal in heavy_metals:
    metal_data = pd.to_numeric(sample[metal], errors='coerce')
    failure_rates[metal] = sample.groupby('product_type').apply(
        lambda x: (pd.to_numeric(x[metal], errors='coerce') > metal_limits[metal]).mean() * 100
    )
failure_rates_df = pd.DataFrame(failure_rates)
failure_rates_df = failure_rates_df.fillna(0)
if 'Mix' in failure_rates_df.index:
    failure_rates_df = failure_rates_df.drop('Mix')
    failure_rates_df = failure_rates_df.drop('Edible')

# Create a heatmap of failure rates.
plt.figure(figsize=(14, 10))
sns.heatmap(
    failure_rates_df, 
    annot=True, 
    fmt='.1f',
    cmap='Reds',
    vmin=0, 
    vmax=2, 
    cbar_kws={'label': 'Failure Rate (%)'},
    linewidths=0.5,
    annot_kws={"size": 21}
)
plt.title('Failure Rates for Heavy Metals by Product Type in NJ', pad=20)
plt.ylabel('')
plt.xlabel('')
plt.xticks(rotation=45, ha='right')
plt.tight_layout()
outfile = os.path.join(assets_dir, 'nj-heavy-metals-failure-rates-heatmap.png')
plt.savefig(outfile, dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()


# === Lead in flower analysis ===

# Filter for Flower samples and calculate detection rate for Lead by month and lab
flower = sample[sample['product_type'] == 'Flower']
flower_lead = flower[['month', 'lab_id', 'Lead']].copy()
flower_lead['month'] = flower_lead['month'].dt.to_timestamp()
flower_lead['Lead'] = pd.to_numeric(flower_lead['Lead'], errors='coerce')
flower_lead['detected'] = flower_lead['Lead'] > 0  # Change to detection instead of failure
monthly_lab_lead_detection = flower_lead.groupby(['month', 'lab_id'])['detected'].mean().reset_index()
monthly_lead_detection = flower_lead.groupby('month')['detected'].mean().reset_index()
labs = flower_lead['lab_id'].unique()
plt.figure(figsize=(15, 7.5))
for lab in labs:
    lab_data = monthly_lab_lead_detection[monthly_lab_lead_detection['lab_id'] == lab]
    plt.plot(
        lab_data['month'],
        lab_data['detected'] * 100,  # Convert to percentage
        marker='o',
        linestyle='-',
        alpha=0.7,
        label=f'Lab {lab}',
        color=lab_colors[lab]
    )
plt.plot(
    monthly_lead_detection['month'],
    monthly_lead_detection['detected'] * 100,
    marker='o',
    linestyle='-',
    linewidth=3,
    color='black',
    label='Overall Average'
)
plt.title('Monthly Detection Rate for Lead in Flower by Lab in New Jersey', pad=20)
plt.xlabel('')
plt.ylabel('Detection Rate (%)')
plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
plt.gcf().autofmt_xdate()
plt.grid(True, linestyle='--', alpha=0.7)
plt.legend(title='Lab ID', bbox_to_anchor=(1.05, 1), loc='upper left')
plt.tight_layout()
plt.ylim(0, 80)
outfile = os.path.join(assets_dir, 'nj-lead-flower-detection-lab-timeseries.png')
plt.savefig(outfile, dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()

# Timeseries of failure rates of Flower for Lead by month by lab.
flower = sample[sample['product_type'] == 'Flower']
flower_lead = flower[['month', 'lab_id', 'Lead',]].copy()
flower_lead['month'] = flower_lead['month'].dt.to_timestamp()
flower_lead['Lead'] = pd.to_numeric(flower_lead['Lead'], errors='coerce')
flower_lead['failed'] = flower_lead['Lead'] > metal_limits['Lead']
monthly_lab_lead_failure = flower_lead.groupby(['month', 'lab_id'])['failed'].mean().reset_index()
monthly_lead_failure = flower_lead.groupby('month')['failed'].mean().reset_index()
labs = flower_lead['lab_id'].unique()
plt.figure(figsize=(17.5, 8.5))
for lab in labs:
    lab_data = monthly_lab_lead_failure[monthly_lab_lead_failure['lab_id'] == lab]
    plt.plot(
        lab_data['month'],
        lab_data['failed'] * 100,  # Convert to percentage
        marker='o',
        linestyle='-',
        alpha=0.7,
        label=f'Lab {lab}',
        color=lab_colors[lab]
    )
plt.plot(
    monthly_lead_failure['month'],
    monthly_lead_failure['failed'] * 100,  # Convert to percentage
    marker='o',
    linestyle='-',
    linewidth=3,
    color='black',
    label='Overall Average'
)
plt.title('Monthly Failure Rate for Lead in Flower by Lab in New Jersey', pad=20)
plt.xlabel('')
plt.ylabel('Failure Rate (%)')
plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
plt.gcf().autofmt_xdate()
plt.grid(True, linestyle='--', alpha=0.7)
plt.legend(title='Lab ID', bbox_to_anchor=(1.05, 1), loc='upper left')
plt.tight_layout()
plt.ylim(0, 12)
outfile = os.path.join(assets_dir, 'nj-lead-flower-failure-lab-timeseries.png')
plt.savefig(outfile, dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()

# === Infused Chromium analysis ===

# Filter for Infused samples and calculate detection rate for Chromium by month and lab
infused_chromium = sample[(sample['product_type'] == 'Infused') & (sample['Chromium'].notna())].copy()
infused_chromium['month'] = infused_chromium['month'].dt.to_timestamp()
infused_chromium['Chromium'] = pd.to_numeric(infused_chromium['Chromium'], errors='coerce')
infused_chromium['detected'] = infused_chromium['Chromium'] > 0  # Change to detection instead of failure
monthly_lab_chromium_detection = infused_chromium.groupby(['month', 'lab_id'])['detected'].mean().reset_index()
monthly_chromium_detection = infused_chromium.groupby('month')['detected'].mean().reset_index()
labs = infused_chromium['lab_id'].unique()
plt.figure(figsize=(17.5, 8.5))
for lab in labs:
    lab_data = monthly_lab_chromium_detection[monthly_lab_chromium_detection['lab_id'] == lab]
    plt.plot(
        lab_data['month'],
        lab_data['detected'] * 100,  # Convert to percentage
        marker='o',
        linestyle='-',
        alpha=0.7,
        label=f'Lab {lab}',
        color=lab_colors[lab]
    )
plt.plot(
    monthly_chromium_detection['month'],
    monthly_chromium_detection['detected'] * 100,  # Convert to percentage
    marker='o',
    linestyle='-',
    linewidth=3,
    color='black',
    label='Overall Average'
)
plt.title('Monthly Detection Rate for Chromium in Infused Products by Lab in New Jersey', pad=20)
plt.xlabel('')
plt.ylabel('Detection Rate (%)')
plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
plt.gcf().autofmt_xdate()
plt.grid(True, linestyle='--', alpha=0.7)
plt.legend(title='Lab ID', bbox_to_anchor=(1.05, 1), loc='upper left')
plt.tight_layout()
plt.ylim(0, 100)  # Adjust this if you want to focus on a specific range
outfile = os.path.join(assets_dir, 'nj-chromium-infused-detection-lab-timeseries.png')
plt.savefig(outfile, dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()


# Timeseries of failure rates of Infused for Chromium by month by lab.
infused_chromium = sample[(sample['product_type'] == 'Infused') & (sample['Chromium'].notna())].copy()
infused_chromium['month'] = infused_chromium['month'].dt.to_timestamp()
infused_chromium['Chromium'] = pd.to_numeric(infused_chromium['Chromium'], errors='coerce')
infused_chromium['failed'] = infused_chromium['Chromium'] > metal_limits['Chromium']
monthly_lab_chromium_failure = infused_chromium.groupby(['month', 'lab_id'])['failed'].mean().reset_index()
monthly_chromium_failure = infused_chromium.groupby('month')['failed'].mean().reset_index()
labs = infused_chromium['lab_id'].unique()
plt.figure(figsize=(17.5, 8.5))
for lab in labs:
    lab_data = monthly_lab_chromium_failure[monthly_lab_chromium_failure['lab_id'] == lab]
    plt.plot(
        lab_data['month'],
        lab_data['failed'] * 100,  # Convert to percentage
        marker='o',
        linestyle='-',
        alpha=0.7,
        label=f'Lab {lab}',
        color=lab_colors[lab]
    )
plt.plot(
    monthly_chromium_failure['month'],
    monthly_chromium_failure['failed'] * 100,  # Convert to percentage
    marker='o',
    linestyle='-',
    linewidth=3,
    color='black',
    label='Overall Average'
)
plt.title('Monthly Failure Rate for Chromium in Infused Products by Lab in New Jersey', pad=20)
plt.xlabel('')
plt.ylabel('Failure Rate (%)')
plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
plt.gcf().autofmt_xdate()
plt.grid(True, linestyle='--', alpha=0.7)
plt.legend(title='Lab ID', bbox_to_anchor=(1.05, 1), loc='upper left')
plt.tight_layout()
plt.ylim(0, 30)
outfile = os.path.join(assets_dir, 'nj-chromium-infused-failure-lab-timeseries.png')
plt.savefig(outfile, dpi=96, bbox_inches='tight', facecolor='w', transparent=False)
plt.show()


#------------------------------------------------------------------------------
# Analysis: Microbes.
#------------------------------------------------------------------------------

# Define microbes.
microbes = [
    'E.coli',
    'L. monocytogenes',
    'Salmonella',
    'STEC',
    'Total Aerobic Microbial Count',
    'Total Coliforms',
    'Total Yeast and Mold',
    'Total Yeast and Mold Count'
]

# Future work.


#------------------------------------------------------------------------------
# Analysis: Pesticides.
#------------------------------------------------------------------------------

# Define pesticides.
pesticides = [
    'Abamectin',
    'Acetamiprid',
    'Aldicarb',
    'Azoxystrobin',
    'Bifenazate',
    'Bifenthrin',
    'Boscalid',
    'Carbaryl',
    'Carbofuran',
    'Chlorantraniliprole',
    'Chlorpyrifos',
    'Clofentezine',
    'Cyfluthrin',
    'DDVP/Dichlorvos',
    'Daminozide/Alar',
    'Diazinon',
    'Dimethoate',
    'Ethephon',
    'Etoxazole',
    'Fenpyroximate',
    'Fipronil',
    'Flonicamid',
    'Fludioxonil',
    'Hexythiazox',
    'Imazalil',
    'Imidacloprid',
    'Kresoxim-methyl',
    'Malathion',
    'Metalaxyl',
    'Methiocarb',
    'Methomyl',
    'Myclobutanil',
    'Naled',
    'Oxamyl',
    'Paclobutrazol',
    'Permethrins',
    'Phosmet',
    'Piperonyl Butoxide',
    'Propiconazole',
    'Pyrethrins',
    'Spinosad',
    'Spiromesifen',
    'Spirotetramat',
    'Thiacloprid',
    'Thiamethoxam',
    'Trifloxystrobin'
]

# Future work.


#------------------------------------------------------------------------------
# Analysis: Moisture Content.
#------------------------------------------------------------------------------

# Future work.


#------------------------------------------------------------------------------
# Analysis: Sales.
#------------------------------------------------------------------------------

# Get total sales.
# Source: https://www.nj.gov/governor/news/news/562024/approved/20240214b.shtml
total_sales_2023 = 673_907_755

# Calculate sales per tests in 2023.
# Note: Missing January data.
results_2023 = results[(results['date'] >= '2023-02-01') & (results['date'] < '2024-01-01')]
total_tests_2023 = len(results_2023)
sales_per_test_2023 = (total_sales_2023 * 11/12 ) / total_tests_2023
print(f'Sales per test in 2023: ${sales_per_test_2023:,.2f}')

# Output meta statistics to LaTeX in a table.
total_product_tests = len(results)
sample_size = len(sample)
first_date = sample['date'].min().strftime('%Y-%m-%d')
last_date = sample['date'].max().strftime('%Y-%m-%d')
tests_per_product = total_tests / total_product_tests
def format_number(num):
    return f"{num:,}"

# Create the LaTeX table content
latex_table = r"""
\begin{table}[h]
\centering
\begin{tabular}{lr}
\hline
Total tests & %s \\
Products tested & %s \\
Tests per product & %.0f \\
\hline
Sample size & %s \\
First date & %s \\
Last date & %s \\
\hline
\end{tabular}
\end{table}
""" % (
    format_number(total_tests),
    format_number(total_product_tests),
    tests_per_product,
    format_number(sample_size),
    first_date,
    last_date,
)
print(latex_table)
