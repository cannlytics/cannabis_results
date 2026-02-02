
# Read MA lab results.
import pandas as pd


datafiles = [
    r"D:\data\public-records\Massachusetts\TestingTHC-THCA-YeastMold-Apr-Dec2021-FINAL.csv",
    r"D:\data\public-records\Massachusetts\TestingTHC-THCA-YeastMold-2022-FINAL.csv",
    r"D:\data\public-records\Massachusetts\TestingTHC-THCA-YeastMold-2023-Jan-June-FINAL.csv",
    r"D:\data\public-records\Massachusetts\TestingTHC-THCA-YeastMold-2023-Jul-Sep-FINAL.csv",
]
ma_results = pd.concat([pd.read_csv(datafile) for datafile in datafiles])

# Coalesce similarly named columns.
ma_results['lab'] = ma_results['TestingLabId'].combine_first(ma_results['TestingLab'])
ma_results['strain_name'] = ma_results['StrainName'].combine_first(ma_results['Strain'])
ma_results = ma_results.drop(columns=[
    'TestingLabId',
    'TestingLab',
    'StrainName',
    'Strain',
])

# Rename certain columns.
ma_results = ma_results.rename(columns={
    'ProductCategory': 'product_type',
    'PackageLabel': 'label',
    'TestType': 'test_type',
    'TestResult': 'test_result',
    'TestPerformedDate': 'date_tested',
})

# Standardize state.
state = 'MA'
ma_results['lab_state'] = state
ma_results['producer_state'] = state

# Add a date column.
ma_results['date'] = pd.to_datetime(ma_results['date_tested'])
ma_results['week'] = ma_results['date'].dt.to_period('W').astype(str)
ma_results['month'] = ma_results['date'].dt.to_period('M').astype(str)
ma_results = ma_results.sort_values('date')

# Creating a pivot table
pivot_df = ma_results.pivot_table(
    index=['label', 'date_tested', 'lab'],
    columns='test_type',
    values='test_result',
    aggfunc='first',
).reset_index()
pivot_df.columns.name = None  
pivot_df.rename({
    'THC (%) Raw Plant Material': 'delta_9_thc',
    'THCA (%) Raw Plant Material': 'thca',
    'Total THC (%) Raw Plant Material': 'total_thc',
    'Total Yeast and Mold (CFU/g) Raw Plant Material': 'yeast_and_mold'
}, axis=1, inplace=True)
pivot_df['date'] = pd.to_datetime(pivot_df['date_tested'])
pivot_df['week'] = pivot_df['date'].dt.to_period('W').astype(str)
pivot_df['month'] = pivot_df['date'].dt.to_period('M').astype(str)
print('Number of public MA lab results:', len(pivot_df))

# Save the data.
outfile = 'D://data/cannabis_results/data/ma/ma-results-latest.xlsx'
outfile_csv = 'D://data/cannabis_results/data/ma/ma-results-latest.csv'
pivot_df.to_excel(outfile, index=False)
pivot_df.to_csv(outfile_csv, index=False)
print('Saved Excel:', outfile)
print('Saved CSV:', outfile_csv)
