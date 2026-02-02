"""
Get Cannabis Results | Alaska
Copyright (c) 2024 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 7/10/2024
Updated: 12/8/2024
License: CC-BY 4.0 <https://huggingface.co/datasets/cannlytics/cannabis_tests/blob/main/LICENSE>

Data Source:
    - Public records request
"""
# Standard imports:
import os

# External imports:
from cannlytics.logs import initialize_logs
from cannlytics.data.coas import CoADoc, standardize_results
from cannlytics.utils.utils import snake_case
import pandas as pd

# Initialize logging.
logger = initialize_logs(
    name='get_results_ak.py',
    prefix='get-results-ak'
)

# Define Sample and result column mappings.
sample_columns = {
    'PackageLabel': 'label',
    'LabFacilityName': 'lab',
    'ProductName': 'product_name',
    'ProductCategoryName': 'product_type',
    'TestPerformedDate': 'date_tested',
    'OverallPassed': 'status',
    'PackageId': 'package_id',
    'LabTestResultId': 'sample_id',
    'TestingFacilityId': 'lab_id',
    'LabFacilityLicenseNumber': 'lab_license_number',
    'SourcePackageId': 'source_package_id',
    'SourcePackageLabel': 'source_package_label',
    'IsRevoked': 'revoked',
}
result_columns = {
    'TestTypeName': 'name',
    'TestPassed': 'status',
    'TestResultLevel': 'value',
}


def process_file(parser, file_path, unique_id='PackageLabel'):
    """Process each file and transform the data."""
    chunks = pd.read_csv(
        file_path,
        chunksize=100000,
        low_memory=False,
        usecols=lambda x: x in sample_columns or x in result_columns
    )
    samples = {}
    for chunk in chunks:
        for _, row in chunk.iterrows():
            unique_value = row[unique_id]
            if unique_value not in samples:
                sample = {sample_columns[key]: row[key] for key in sample_columns if key in row}
                sample['results'] = []
                samples[unique_value] = sample
            result = {result_columns[key]: row[key] for key in result_columns if key in row}
            name = snake_case(result['name'].split('(')[0].strip())
            result['key'] = parser.analytes.get(name, name)
            samples[unique_value]['results'].append(result)
    return pd.DataFrame(samples.values())

# === Test ===
# [✓] Tested: 2024-12-08 by Keegan Skeate <keegan@cannlytics>
if __name__ == '__main__':

    # Define where the data lives.
    data_dir = r'D:\data\public-records\Alaska\AK Lab Result Data 2016-2024\AK Lab Result Data 2016-2024'
    output_dir = r'D:\data\alaska\results\results'
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Find all of the datafiles.
    # Note: There is a misspelling of 'TestResult' in some file names.
    test_datafiles = []
    for root, dirs, files in os.walk(data_dir):
        for file in files:
            if 'TestResult' in file or 'TestResutl' in file:
                test_datafile = os.path.join(root, file)
                test_datafiles.append(test_datafile)

    # Process each file and save the results by year
    parser = CoADoc()
    id_columns = ['product_name', 'product_type', 'date_tested']
    compounds = ['total_thc', 'thca', 'delta_9_thc']
    for datafile in reversed(test_datafiles):
        logger.info(f'Processing file: {datafile}')
        samples = process_file(parser, datafile)
        samples.drop_duplicates(subset=id_columns, inplace=True)
        logger.info(f'Found {len(samples)} samples.')

        # Process 'date_tested' and extract year.
        samples['date_tested'] = pd.to_datetime(samples['date_tested'], errors='coerce')
        samples = samples.dropna(subset=['date_tested'])
        samples['year'] = samples['date_tested'].dt.year

        # Add `total_thc` column.
        samples = standardize_results(samples, compounds=compounds)

        # Save the results by year.
        for year, yearly_samples in samples.groupby('year'):
            sample_datafile = os.path.join(output_dir, f'ak-lab-results-{year}.xlsx')
            
            # Update existing file.
            if os.path.exists(sample_datafile):
                existing_data = pd.read_excel(sample_datafile)
                combined_data = pd.concat([existing_data, yearly_samples], ignore_index=True)
                combined_data.drop_duplicates(subset=id_columns, inplace=True)
                combined_data.sort_values('date_tested', ascending=False, inplace=True)
                combined_data.to_excel(sample_datafile, index=False)
                logger.info(f'Updated {len(yearly_samples)} samples for year {year}')
            
            # Create new file.
            else:
                yearly_samples.sort_values('date_tested', ascending=False, inplace=True)
                yearly_samples.to_excel(sample_datafile, index=False)
                logger.info(f'Saved {len(yearly_samples)} samples for year {year}')

    # Complete collection.
    logger.info('✓ Collected results for AK.')
