"""
Analyze COAs
Copyright (c) 2024-2025 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 12/15/2024
Updated: 2/17/2025
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:

"""
# Standard imports:
import os

# External imports:
import pandas as pd

# Internal imports:
from cannlytics.data.cache import Bogart
from cannlytics.firebase import initialize_firebase, update_document
from dotenv import dotenv_values


#-----------------------------------------------------------------------
# Setup.
#-----------------------------------------------------------------------

# Initialize the COA cache.
cache_dir = 'D://data/.cache'
coas_cache = Bogart(os.path.join(cache_dir, f'coas.jsonl'))

# Initialize Firebase.
config = dotenv_values('../.env')
credentials = config['GOOGLE_APPLICATION_CREDENTIALS']
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = credentials
db = initialize_firebase()


#-----------------------------------------------------------------------
# Calculate aggregate statistics.
#-----------------------------------------------------------------------

# Read the COA stats cache.
stats_cache = Bogart(os.path.join(cache_dir, 'coa-stats.jsonl'))

# Read in all of the cached data.
results = coas_cache.to_df()
print('Total number of uploaded COAs:', len(results))

# Fill missing `producer_license_number` with `license_number`.
results['producer_license_number'] = results['producer_license_number'].fillna(results['license_number'])
results.drop(columns=['license_number'], inplace=True)

# Calculate totals.
stats = {}
stats['total_results'] = len(results)
stats['total_results_by_state'] = results['state'].value_counts().to_dict()
stats['total_results_by_year'] = results['year'].value_counts().to_dict()
stats['total_results_by_type'] = results['standard_product_type'].value_counts().to_dict()

# Calculate totals by producer, lab, product and strain.
stats['total_producers'] = results['producer_license_number'].nunique()
stats['total_labs'] = results['lab'].nunique()
stats['total_products'] = results['product_name'].nunique()
stats['total_strains'] = results['standard_strain_name'].nunique()

# Upload the aggregate statistics to Firestore.
ref = 'stats/coas'
slug = ref.replace('/', '-')
stats['updated_at'] = pd.Timestamp.now().isoformat()
update_document(ref, stats, database=db)
stats_cache.set(slug, stats)
print(ref, stats)


#-----------------------------------------------------------------------
# Calculate state statistics.
# Collection: `stats/coas/total-coas-by-state/{state}`
#-----------------------------------------------------------------------

# Calculate state statistics
collection = 'stats/coas/total-coas-by-state/%s'
states = results['state'].unique()
for state in states:

    # Calculate state-level statistics.
    state_results = results[results['state'] == state]
    state_stats = {
        'state': state,
        'total_results': len(state_results),
        'total_results_by_year': state_results['year'].value_counts().to_dict(),
        'total_results_by_type': state_results['standard_product_type'].value_counts().to_dict(),
        'total_producers': state_results['producer_license_number'].nunique(),
        'total_labs': state_results['lab'].nunique(),
        'total_products': state_results['product_name'].nunique(),
        'total_strains': state_results['standard_strain_name'].nunique(),
        
        # Calculate average tests per producer
        'avg_tests_per_producer': len(state_results) / state_results['producer_license_number'].nunique(),
        
        # Calculate top labs by volume
        'top_labs': state_results['lab'].value_counts().head(5).to_dict(),
        
        # Calculate top product types
        'top_product_types': state_results['standard_product_type'].value_counts().head(5).to_dict(),
        
        # Calculate monthly test volumes for the past year
        'monthly_volumes': state_results[
            state_results['year'] == state_results['year'].max()
        ]['month'].value_counts().sort_index().to_dict(),
        
        # Calculate producer market concentration (percentage of tests by top 10 producers)
        'top_producer_concentration': (
            state_results['producer_license_number'].value_counts().head(10).sum() 
            / len(state_results) * 100
        )
    }
    
    # Check if the state statistics have changed.
    ref = collection % state
    slug = ref.replace('/', '-')
    current_stats = stats_cache.get(slug)
    if current_stats:
        current_stats.pop('updated_at', None)
        if current_stats == state_stats:
            continue
    
    # Update cache and database.
    state_stats['updated_at'] = pd.Timestamp.now().isoformat()
    update_document(ref, state_stats, database=db)
    stats_cache.set(slug, state_stats)
    print('Uploaded stats:', ref)


#-----------------------------------------------------------------------
# Calculate producer statistics.
#-----------------------------------------------------------------------

# Calculate producer statistics.
collection = 'stats/coas/coas-by-producer/%s'
producers = results['producer_license_number'].unique()
for license_number in producers[:1]:

    # Calculate producer statistics.
    producer_results = results[results['producer_license_number'] == license_number]
    producer = producer_results.iloc[0]['producer']
    producer_state = producer_results.iloc[0]['state']
    producer_stats = {
        'producer': producer,
        'state': producer_state,
        'license_number': license_number,
        'total_results': len(producer_results),
        'total_results_by_year': producer_results['year'].value_counts().to_dict(),
        'total_results_by_type': producer_results['standard_product_type'].value_counts().to_dict(),
        'total_strains': producer_results['standard_strain_name'].nunique(),
        'total_products': producer_results['product_name'].nunique(),
        'total_labs': producer_results['lab'].nunique(),
    }

    # Check if the producer statistics have changed.
    ref = collection % license_number
    slug = ref.replace('/', '-')
    current_stats = stats_cache.get(slug)
    if current_stats:
        current_stats.pop('updated_at', None)
        if current_stats == producer_stats:
            continue

    # Update cache and database.
    producer_stats['updated_at'] = pd.Timestamp.now().isoformat()
    update_document(ref, producer_stats, database=db)
    stats_cache.set(slug, producer_stats)
    print('Uploaded stats:', ref)


#-----------------------------------------------------------------------
# Calculate lab statistics.
#-----------------------------------------------------------------------

# Calculate lab statistics
collection = 'stats/coas/coas-by-lab/%s'
labs = list(results['lab'].unique())
labs = [lab for lab in labs if lab]  # Remove any None/empty values
labs.sort()

for lab in labs:
    # Calculate lab-specific statistics
    lab_results = results[results['lab'] == lab]
    lab_stats = {
        'lab': lab,
        'states': list(lab_results['state'].unique()),  # Labs may operate in multiple states
        'total_results': len(lab_results),
        'total_results_by_year': lab_results['year'].value_counts().to_dict(),
        'total_results_by_type': lab_results['standard_product_type'].value_counts().to_dict(),
        'total_results_by_state': lab_results['state'].value_counts().to_dict(),
        
        # Calculate client base statistics
        'total_producers': lab_results['producer_license_number'].nunique(),
        'total_products': lab_results['product_name'].nunique(),
        'total_strains': lab_results['standard_strain_name'].nunique(),
        
        # Calculate testing volume trends
        'monthly_volumes': lab_results[
            lab_results['year'] == lab_results['year'].max()
        ]['month'].value_counts().sort_index().to_dict(),
        
        # Calculate client concentration metrics
        'top_producers': lab_results['producer_license_number'].value_counts().head(10).to_dict(),
        'producer_concentration': (
            lab_results['producer_license_number'].value_counts().head(10).sum() 
            / len(lab_results) * 100
        ),
        
        # Calculate product type specialization
        'product_type_distribution': (
            (lab_results['standard_product_type'].value_counts() 
             / len(lab_results) * 100).round(2).to_dict()
        ),
        
        # Calculate state distribution for multi-state labs
        'state_distribution': (
            (lab_results['state'].value_counts() 
             / len(lab_results) * 100).round(2).to_dict()
        ),
        
        # Calculate average tests per client
        'avg_tests_per_producer': len(lab_results) / lab_results['producer_license_number'].nunique(),
        
        # Calculate client retention metrics (producers who tested in consecutive years)
        'year_over_year_clients': {
            year: len(set(
                lab_results[lab_results['year'] == year]['producer_license_number']
            ).intersection(set(
                lab_results[lab_results['year'] == year-1]['producer_license_number']
            )))
            for year in sorted(lab_results['year'].unique())[1:]  # Skip first year
        }
    }
    
    # Calculate market share per state
    lab_stats['market_share_by_state'] = {}
    for state in lab_stats['states']:
        state_total = len(results[results['state'] == state])
        lab_state_total = len(lab_results[lab_results['state'] == state])
        lab_stats['market_share_by_state'][state] = round(
            (lab_state_total / state_total * 100), 2
        )
    
    # Check if the lab statistics have changed
    ref = collection % lab.lower().replace(' ', '-')
    slug = ref.replace('/', '-')
    current_stats = stats_cache.get(slug)
    if current_stats:
        current_stats.pop('updated_at', None)
        if current_stats == lab_stats:
            continue
    
    # Update cache and database
    lab_stats['updated_at'] = pd.Timestamp.now().isoformat()
    update_document(ref, lab_stats, database=db)
    stats_cache.set(slug, lab_stats)
    print('Uploaded stats:', ref)



#-----------------------------------------------------------------------
# Calculate strain statistics.
#-----------------------------------------------------------------------

# Calculate strain statistics
collection = 'stats/coas/coas-by-strain/%s'
strains = list(results['standard_strain_name'].unique())
strains = [strain for strain in strains if strain]  # Remove any None/empty values
strains.sort()
for strain in strains:

    # Calculate strain-specific statistics
    strain_results = results[results['standard_strain_name'] == strain]
    strain_stats = {
        # Metadata
        'strain_name': strain,

        # Totals
        'total_results': len(strain_results),
        'total_results_by_year': strain_results['year'].value_counts().to_dict(),
        'total_results_by_state': strain_results['state'].value_counts().to_dict(),
        'total_producers': strain_results['producer_license_number'].nunique(),
        'total_labs': strain_results['lab'].nunique(),
        'total_products': strain_results['product_name'].nunique(),
        
        # Geographic metrics
        'state_distribution': (
            (strain_results['state'].value_counts() 
             / len(strain_results) * 100).round(2).to_dict()
        ),
        
        # Producer metrics
        'top_producers': strain_results['producer_license_number'].value_counts().head(10).to_dict(),
        'producer_concentration': (
            strain_results['producer_license_number'].value_counts().head(10).sum() 
            / len(strain_results) * 100
        ).round(2),
        
        # Product type metrics
        'product_types': strain_results['standard_product_type'].value_counts().to_dict(),
        'product_type_distribution': (
            (strain_results['standard_product_type'].value_counts() 
             / len(strain_results) * 100).round(2).to_dict()
        ),
        
        # Timeseries metrics
        'monthly_volumes': strain_results[
            strain_results['year'] == strain_results['year'].max()
        ]['month'].value_counts().sort_index().to_dict(),
        'year_over_year_growth': {
            str(year): (
                len(strain_results[strain_results['year'] == year]) /
                len(strain_results[strain_results['year'] == year-1]) * 100 - 100
                if len(strain_results[strain_results['year'] == year-1]) > 0 else None
            )
            for year in sorted(strain_results['year'].unique())[1:]
        },
        
        # Calculate state-level market share
        'market_share_by_state': {}
    }
    
    # Calculate market share for each state where the strain appears
    for state in strain_stats['total_results_by_state'].keys():
        state_total_strains = len(results[
            (results['state'] == state) & 
            (results['standard_strain_name'].notna())
        ])
        strain_state_total = strain_stats['total_results_by_state'].get(state, 0)
        strain_stats['market_share_by_state'][state] = round(
            (strain_state_total / state_total_strains * 100), 2
        )
    
    # Calculate producer adoption trends
    yearly_producers = {
        year: strain_results[
            strain_results['year'] == year
        ]['producer_license_number'].nunique()
        for year in sorted(strain_results['year'].unique())
    }
    strain_stats['yearly_producer_adoption'] = yearly_producers
    
    # Calculate new vs returning producers each year
    strain_stats['producer_retention'] = {}
    previous_year_producers = set()
    for year in sorted(strain_results['year'].unique()):
        current_year_producers = set(
            strain_results[
                strain_results['year'] == year
            ]['producer_license_number'].unique()
        )
        strain_stats['producer_retention'][str(year)] = {
            'total_producers': len(current_year_producers),
            'new_producers': len(current_year_producers - previous_year_producers),
            'returning_producers': len(current_year_producers & previous_year_producers)
        }
        previous_year_producers = current_year_producers
    
    # Check if the strain statistics have changed
    ref = collection % strain.lower().replace(' ', '-')
    slug = ref.replace('/', '-')
    current_stats = stats_cache.get(slug)
    if current_stats:
        current_stats.pop('updated_at', None)
        if current_stats == strain_stats:
            continue
    
    # Update cache and database
    strain_stats['updated_at'] = pd.Timestamp.now().isoformat()
    update_document(ref, strain_stats, database=db)
    stats_cache.set(slug, strain_stats)
    print('Uploaded stats:', ref)


#-----------------------------------------------------------------------
# Calculate timeseries statistics.
#-----------------------------------------------------------------------

# Calculate timeseries statistics
print('Calculating timeseries statistics...')
monthly_collection = 'stats/coas/total-coas-by-month/%s'
results['year_month'] = results['year'].astype(str) + '-' + results['month'].astype(str).str.zfill(2)
months = sorted(results['year_month'].unique())
for year_month in months:

    # Calculate monthly statistics
    month_results = results[results['year_month'] == year_month]
    monthly_stats = {
        'year': int(year_month.split('-')[0]),
        'month': int(year_month.split('-')[1]),
        'year_month': year_month,
        
        # Basic volume metrics
        'total_results': len(month_results),
        'total_producers': month_results['producer_license_number'].nunique(),
        'total_labs': month_results['lab'].nunique(),
        'total_products': month_results['product_name'].nunique(),
        'total_strains': month_results['standard_strain_name'].nunique(),
        
        # State-level analysis
        'results_by_state': month_results['state'].value_counts().to_dict(),
        'producers_by_state': {
            state: month_results[month_results['state'] == state]['producer_license_number'].nunique()
            for state in month_results['state'].unique()
        },
        'labs_by_state': {
            state: month_results[month_results['state'] == state]['lab'].nunique()
            for state in month_results['state'].unique()
        },
        
        # Product analysis
        'results_by_type': month_results['standard_product_type'].value_counts().to_dict(),
        'product_type_distribution': (
            (month_results['standard_product_type'].value_counts() 
             / len(month_results) * 100).round(2).to_dict()
        ),
        
        # Market metrics
        'top_producers': month_results['producer_license_number'].value_counts().head(10).to_dict(),
        'top_labs': month_results['lab'].value_counts().head(10).to_dict(),
        'top_strains': month_results['standard_strain_name'].value_counts().head(10).to_dict(),
        
        # Calculate month-over-month changes
        'mom_changes': {}
    }
    
    # Calculate month-over-month changes
    if year_month != months[0]:  # Skip first month as it has no previous month
        previous_month = months[months.index(year_month) - 1]
        previous_results = results[results['year_month'] == previous_month]
        
        monthly_stats['mom_changes'] = {
            'total_results_change': (
                (len(month_results) - len(previous_results)) / len(previous_results) * 100
                if len(previous_results) > 0 else None
            ),
            'total_producers_change': (
                (monthly_stats['total_producers'] - previous_results['producer_license_number'].nunique())
                / previous_results['producer_license_number'].nunique() * 100
                if previous_results['producer_license_number'].nunique() > 0 else None
            ),
            'total_labs_change': (
                (monthly_stats['total_labs'] - previous_results['lab'].nunique())
                / previous_results['lab'].nunique() * 100
                if previous_results['lab'].nunique() > 0 else None
            )
        }
    
    # Check if monthly statistics have changed
    ref = monthly_collection % year_month
    slug = ref.replace('/', '-')
    current_stats = stats_cache.get(slug)
    if current_stats:
        current_stats.pop('updated_at', None)
        if current_stats == monthly_stats:
            continue
    
    # Update cache and database
    monthly_stats['updated_at'] = pd.Timestamp.now().isoformat()
    update_document(ref, monthly_stats, database=db)
    stats_cache.set(slug, monthly_stats)
    print('Uploaded monthly stats:', ref)

# Now calculate yearly statistics
yearly_collection = 'stats/coas/total-coas-by-year/%s'
years = sorted(results['year'].unique())

for year in years:
    # Calculate yearly statistics
    year_results = results[results['year'] == year]
    yearly_stats = {
        'year': year,
        
        # Basic volume metrics
        'total_results': len(year_results),
        'total_producers': year_results['producer_license_number'].nunique(),
        'total_labs': year_results['lab'].nunique(),
        'total_products': year_results['product_name'].nunique(),
        'total_strains': year_results['standard_strain_name'].nunique(),
        
        # Monthly distribution
        'monthly_volumes': year_results['month'].value_counts().sort_index().to_dict(),
        
        # State-level analysis
        'results_by_state': year_results['state'].value_counts().to_dict(),
        'producers_by_state': {
            state: year_results[year_results['state'] == state]['producer_license_number'].nunique()
            for state in year_results['state'].unique()
        },
        'labs_by_state': {
            state: year_results[year_results['state'] == state]['lab'].nunique()
            for state in year_results['state'].unique()
        },
        
        # Product analysis
        'results_by_type': year_results['standard_product_type'].value_counts().to_dict(),
        'product_type_distribution': (
            (year_results['standard_product_type'].value_counts() 
             / len(year_results) * 100).round(2).to_dict()
        ),
        
        # Market metrics
        'top_producers': year_results['producer_license_number'].value_counts().head(10).to_dict(),
        'top_labs': year_results['lab'].value_counts().head(10).to_dict(),
        'top_strains': year_results['standard_strain_name'].value_counts().head(10).to_dict(),
        
        # Calculate year-over-year changes
        'yoy_changes': {}
    }
    
    # Calculate year-over-year changes
    if year != years[0]:  # Skip first year as it has no previous year
        previous_year = years[years.index(year) - 1]
        previous_results = results[results['year'] == previous_year]
        
        yearly_stats['yoy_changes'] = {
            'total_results_change': (
                (len(year_results) - len(previous_results)) / len(previous_results) * 100
                if len(previous_results) > 0 else None
            ),
            'total_producers_change': (
                (yearly_stats['total_producers'] - previous_results['producer_license_number'].nunique())
                / previous_results['producer_license_number'].nunique() * 100
                if previous_results['producer_license_number'].nunique() > 0 else None
            ),
            'total_labs_change': (
                (yearly_stats['total_labs'] - previous_results['lab'].nunique())
                / previous_results['lab'].nunique() * 100
                if previous_results['lab'].nunique() > 0 else None
            )
        }
    
    # Calculate quarterly metrics
    quarterly_volumes = {}
    for quarter in range(1, 5):
        quarter_months = [(quarter - 1) * 3 + i for i in range(1, 4)]
        quarter_results = year_results[year_results['month'].isin(quarter_months)]
        quarterly_volumes[f'Q{quarter}'] = {
            'total_results': len(quarter_results),
            'total_producers': quarter_results['producer_license_number'].nunique(),
            'total_labs': quarter_results['lab'].nunique(),
            'results_by_type': quarter_results['standard_product_type'].value_counts().to_dict()
        }
    yearly_stats['quarterly_volumes'] = quarterly_volumes
    
    # Check if yearly statistics have changed
    ref = yearly_collection % str(year)
    slug = ref.replace('/', '-')
    current_stats = stats_cache.get(slug)
    if current_stats:
        current_stats.pop('updated_at', None)
        if current_stats == yearly_stats:
            continue
    
    # Update cache and database
    yearly_stats['updated_at'] = pd.Timestamp.now().isoformat()
    update_document(ref, yearly_stats, database=db)
    stats_cache.set(slug, yearly_stats)
    print('Uploaded yearly stats:', ref)


#-----------------------------------------------------------------------
# Calculate chemical statistics.
#-----------------------------------------------------------------------

# Aggregate chemical statistics.
# Note: This allows for trending and distributions in the website.
# TODO: Calculate aggregate stats for:
variables = [
    'chemical_diversity',
    'cannabinoid_diversity',
    'terpene_diversity',
    'beta_pinene_d_limonene_ratio',
    'alpha_humulene_beta_caryophyllene_ratio',
    'camphene_d_limonene_ratio',
    'beta_myrcene_beta_pinene_ratio',
    'beta_caryophyllene_d_limonene_ratio',
    'dominant_cannabinoid',
    'dominant_terpene',
    'colorfulness',
    'purpleness',
]
# stats = calc_aggregate_results_stats(
#     data,
# )
