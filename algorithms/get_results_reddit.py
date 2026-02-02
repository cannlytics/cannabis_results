"""
Get Results | Reddit
Copyright (c) 2024 Cannlytics

Authors: Keegan Skeate <https://github.com/keeganskeate>
Created: 8/24/2024
Updated: 9/6/2025
License: MIT License <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>
"""
# Standard imports:
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
import shutil
from time import sleep
import logging

# External imports:
from bs4 import BeautifulSoup
from cannlytics.data.cache import Bogart
from cannlytics.data.coas import CoADoc
from cannlytics.data.web import initialize_selenium, download_file_with_selenium
from cannlytics.utils.utils import remove_duplicate_files
from dotenv import dotenv_values
import pandas as pd
import praw
import requests
import tempfile
import zxing

# DEV:
os.chdir(r'C:\Users\keega\Documents\cannlytics\cannlytics')

# TODO: Use internal logging.
from cannlytics.logs import initialize_logs

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Define the minimum file size for a successful download.
MIN_FILE_SIZE = 75 * 1024

# Define the subreddits.
STATE_SUBREDDIT = {
    # 'az': {'state': 'az', 'state_name': 'arizona', 'subreddit': 'ZonaEnts'},
    # 'fl': {'state': 'fl', 'state_name': 'florida', 'subreddit': 'FLMedicalTrees'},
    # 'il': {'state': 'il', 'state_name': 'illinois', 'subreddit': 'ILTrees'},
    # 'me': {'state': 'me', 'state_name': 'maine', 'subreddit': 'mainetrees'},
    # 'md': {'state': 'md', 'state_name': 'maryland', 'subreddit': 'MDEnts'},
    # 'mi': {'state': 'mi', 'state_name': 'michigan', 'subreddit': 'Michigents'},
    # 'mn': {'state': 'mn', 'state_name': 'minnesota', 'subreddit': 'MNtrees'},
    # 'mo': {'state': 'mo', 'state_name': 'missouri', 'subreddit': 'MissouriMedical'},
    # 'ms': {'state': 'ms', 'state_name': 'mississippi', 'subreddit': 'MSmedicalcannabis'},
    # 'nj': {'state': 'nj', 'state_name': 'new-jersey', 'subreddit': 'NewJerseyMarijuana'},
    # 'nv': {'state': 'nv', 'state_name': 'nevada', 'subreddit': 'vegastrees'},
    'ny': {'state': 'ny', 'state_name': 'new-york', 'subreddit': 'NYSCannabis'},
    # 'ok': {'state': 'ok', 'state_name': 'oklahoma', 'subreddit': 'OKmarijuana'},
    # 'pa': {'state': 'pa', 'state_name': 'pennsylvania', 'subreddit': 'PaMedicalMarijuana'},
    # 'canada': {'state': 'canada', 'state_name': 'canada', 'subreddit': 'CanadianCannabisLPs'},
}


#-----------------------------------------------------------------------
# Helper functions.
#-----------------------------------------------------------------------

def retry(func, max_retries=3, backoff=5):
    """Decorator for retrying functions with exponential backoff."""
    def wrapper(*args, **kwargs):
        for attempt in range(1, max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if attempt == max_retries:
                    logging.error(f'Failed after {max_retries} attempts: {e}')
                    raise
                sleep(backoff * attempt)
                logging.info(f'Retrying ({attempt}/{max_retries})...')
    return wrapper

@retry
def get_post_content(reddit, post_id):
    """Retrieve the post content with retry."""
    return reddit.submission(id=post_id)

@retry
def download_image(image_url, outfile):
    """Download an image with retry."""
    response = requests.get(image_url)
    if response.status_code == 200 and len(response.content) >= MIN_FILE_SIZE:
        with open(outfile, 'wb') as file:
            file.write(response.content)
        logging.info(f"Downloaded image: {outfile}")
    else:
        logging.warning(f"Failed to download valid image: {image_url}")

def extract_author(post):
    """Consolidated author extraction."""
    author = post.get('author')
    if author:
        return author
    author_element = post.find('a', {'slot': 'authorName'}) or post.find('a', class_='text-neutral-content-weak font-semibold')
    if author_element:
        author = author_element.text.strip()
        if author.startswith('u/'):
            author = author[2:]
        return author
    return None


#-----------------------------------------------------------------------
# Reddit functions.
#-----------------------------------------------------------------------

def initialize_reddit(config):
    """Initialize a Reddit client."""
    return praw.Reddit(
        client_id=config['REDDIT_CLIENT_ID'],
        client_secret=config['REDDIT_SECRET'],
        password=config['REDDIT_PASSWORD'],
        user_agent=config['REDDIT_USER_AGENT'],
        username=config['REDDIT_USERNAME'],
    )


def get_reddit_posts(
        driver,
        data,
        recorded_posts=None,
    ):
    """Get posts from Reddit page, handling both search results and homepage/feed views."""
    page_source = driver.page_source
    soup = BeautifulSoup(page_source, 'html.parser')
    if recorded_posts is None:
        recorded_posts = set()  # Use set for faster lookups
    
    # Method 1: Search format.
    search_posts = soup.find_all('search-telemetry-tracker', attrs={'data-testid': 'search-sdui-post'})
    for post in search_posts:
        tracking_context = post.get('data-faceplate-tracking-context')
        if not tracking_context:
            continue
        post_data = json.loads(tracking_context)
        post_info = post_data.get('post', {})
        post_id = post_info.get('id')
        if not post_id or post_id in recorded_posts:
            continue
        recorded_posts.add(post_id)
        title_element = post.find('a', attrs={'data-testid': 'post-title'}) or post.find('a', attrs={'data-testid': 'post-title-text'})
        title = title_element.text.strip() if title_element else post_info.get('title')
        url = title_element['href'] if title_element else None
        if not url:
            continue
        if not url.startswith('https'):
            url = 'https://www.reddit.com' + url
        created_timestamp = post_info.get('created_timestamp')
        if not created_timestamp:
            logging.warning(f'Missing timestamp for post {post_id}; skipping.')
            continue
        author = extract_author(post)
        subreddit_info = post_data.get('subreddit', {})
        data.append({
            'title': title,
            'post_url': url,
            'created_timestamp': created_timestamp,
            'author_id': post_info.get('author_id'),
            'author': author,
            'post_id': post_id,
            'number_comments': post_info.get('number_comments'),
            'subreddit_id': subreddit_info.get('id'),
            'subreddit_name': subreddit_info.get('name'),
        })
    
    # Method 2: Feed format.
    feed_posts = soup.find_all('shreddit-post')
    for post in feed_posts:
        post_id = post.get('id')
        if not post_id or post_id in recorded_posts:
            continue
        recorded_posts.add(post_id)
        title = post.get('post-title')
        post_url = post.get('permalink')
        content_href = post.get('content-href')
        url = content_href if content_href else post_url
        if not url:
            continue
        if not url.startswith('https'):
            url = 'https://www.reddit.com' + url
        created_timestamp = post.get('created-timestamp')
        if not created_timestamp:
            logging.warning(f'Missing timestamp for post {post_id}; skipping.')
            continue
        author = extract_author(post)
        data.append({
            'title': title,
            'post_url': url,
            'created_timestamp': created_timestamp,
            'author_id': post.get('author-id'),
            'author': author,
            'post_id': post_id,
            'number_comments': post.get('comment-count'),
            'subreddit_id': post.get('subreddit-id'),
            'subreddit_name': post.get('subreddit-prefixed-name'),
        })

    logging.info(f'Number of posts collected: {len(data)}')
    return data, recorded_posts


def collect_all_posts(
        driver,
        subreddit,
        sort_by='new',
        query=None,
        max_posts=5000,
        scroll_wait=2,
):
    """Automate scrolling and collecting posts."""
    if query:
        url = f"https://www.reddit.com/r/{subreddit}/search/?q={query}&sort={sort_by}"
    else:
        url = f"https://www.reddit.com/r/{subreddit}/{sort_by}/?feedViewType=compactView"
    driver.get(url)
    # FIXME: Handle robot check
    sleep(60)
    data = []
    recorded_posts = set()
    last_height = driver.execute_script("return document.body.scrollHeight")
    while len(data) < max_posts:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        sleep(scroll_wait)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
        data, recorded_posts = get_reddit_posts(driver, data, recorded_posts)
    return data


def get_post_images(submission):
    """Get the images for a Reddit post."""
    images = []
    try:
        if 'imgur.com' in submission.url or submission.url.endswith(('.jpg', '.jpeg', '.png', '.gif')):
            images.append(submission.url)
        if hasattr(submission, 'is_gallery') and submission.is_gallery:
            image_dict = submission.media_metadata
            if image_dict is not None:
                for image_item in image_dict.values():
                    largest_image = image_item.get('s')
                    if largest_image:
                        url = largest_image.get('u')
                        if url:
                            images.append(url)
            else:
                logging.warning(f"Gallery post {submission.id} has no media_metadata available")
    except Exception as e:
        logging.error(f"Error extracting images from post {submission.id}: {e}")
    return images


def download_post_images(post_id, images, images_directory):
    """Download the images for a Reddit post in parallel."""
    def download_single(i, image_url):
        file_extension = os.path.splitext(image_url)[-1].split('?')[0] or '.jpg'
        filename = f"{post_id}_image_{i}{file_extension}"
        outfile = os.path.join(images_directory, filename)
        if os.path.exists(outfile):
            return
        download_image(image_url, outfile)

    with ThreadPoolExecutor(max_workers=5) as executor:
        executor.map(download_single, range(1, len(images) + 1), images)


def get_post_comments(submission):
    """Retrieve the post comments."""
    comments = []
    submission.comments.replace_more(limit=None)
    for comment in submission.comments.list():
        comments.append({
            'comment_id': comment.id,
            'comment_author': comment.author.name if comment.author else None,
            'comment_body': comment.body,
            'comment_created_utc': datetime.utcfromtimestamp(comment.created_utc).strftime('%Y-%m-%d %H:%M:%S')
        })
    return comments


def get_reddit_post_data(
        data,
        collected_post_ids=None,
        config=None,
        env_file='.env',
        images_directory=None,
    ):
    """Get the data for each post."""
    if config is None:
        config = dotenv_values(env_file)
    reddit = initialize_reddit(config)
    if collected_post_ids is None:
        collected_post_ids = set()
    all_posts = []
    for n, post_data in enumerate(data, start=1):
        post_id = post_data['post_id'].split('_')[-1]
        if post_id in collected_post_ids:
            logging.info(f'Post already collected: {post_id}')
            continue
        logging.info(f'{n}. Getting data for post: {post_id}')
        submission = get_post_content(reddit, post_id)
        post_content = submission.selftext
        author = submission.author.name if submission.author else None
        if not post_data.get('author'):
            post_data['author'] = author
        images = get_post_images(submission)
        download_post_images(post_id, images, images_directory)
        comments = get_post_comments(submission)
        post_data['post_content'] = post_content
        post_data['upvotes'] = submission.ups
        post_data['downvotes'] = submission.downs
        post_data['images'] = images
        post_data['comments'] = comments
        all_posts.append(post_data)
        sleep(1)  # Minimal sleep; PRAW handles rate limits
    return all_posts


#-----------------------------------------------------------------------
# Data handling.
#-----------------------------------------------------------------------

def read_all_posts(data_dir):
    """Read already collected posts."""
    post_datafiles = [os.path.join(data_dir, x) for x in os.listdir(data_dir) if 'collected-posts' in x and x.endswith('.xlsx')]
    if not post_datafiles:
        return pd.DataFrame()
    collected_posts = pd.concat([pd.read_excel(x) for x in post_datafiles])
    collected_posts.drop_duplicates(subset=['post_id', 'post_url'], inplace=True)
    return collected_posts


def save_post_data(posts, data_dir, namespace, file_type='xlsx'):
    """Save the post data."""
    if not posts:
        logging.info('No posts to save.')
        return
    df = pd.DataFrame(posts)
    timestamp = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
    ext = '.csv' if file_type == 'csv' else '.xlsx'
    datafile = os.path.join(data_dir, f'{namespace}-{timestamp}{ext}')
    if file_type == 'csv':
        df.to_csv(datafile, index=False)
    else:
        df.to_excel(datafile, index=False)
    logging.info(f'Saved post data: {datafile}')


def get_image_cache_urls(image_cache_path: str):
    """Get all of the unique URLs from an image cache."""
    image_cache = Bogart(image_cache_path)
    image_urls = image_cache.to_df()
    image_urls = image_urls.loc[~image_urls['coa_url'].isna()]
    image_urls = image_urls.drop_duplicates(subset=['coa_url'])
    image_urls = image_urls.loc[image_urls['coa_url'].str.startswith('http')]
    return image_urls


def read_all_results(data_dir):
    """Read already collected results."""
    coa_datafiles = [os.path.join(data_dir, x) for x in os.listdir(data_dir) if 'coa-data' in x]
    if not coa_datafiles:
        return pd.DataFrame()
    all_results = pd.concat([pd.read_excel(x) for x in coa_datafiles])
    all_results.drop_duplicates(subset=['sample_id', 'results_hash'], inplace=True)
    return all_results


#-----------------------------------------------------------------------
# COA parsing functions.
#-----------------------------------------------------------------------

def scan_image_urls(
        images_directory: str, 
        image_cache_path: str,
        reverse: bool = False,
    ):
    """Scan images for COA URLs."""

    # Initialize cache and scanner.
    image_cache = Bogart(image_cache_path)
    scanner = zxing.BarCodeReader()

    # Find all images in the directory.
    image_files = os.listdir(images_directory)
    image_files = [os.path.join(images_directory, x) for x in image_files]
    if reverse:
        image_files = list(reversed(image_files))
    print('Scanning %i images in directory: %s' % (len(image_files), images_directory))

    # Scan all images for COA URLs.
    for image_file in image_files:

        # Create a hash of the image file.
        image_hash = image_cache.hash_file(image_file)
        if image_cache.get(image_hash):
            continue

        # Scan the image for a COA URL.
        print('Scanning:', image_file)
        coa_url = None
        post_id = os.path.basename(image_file).split('_')[0]
        try:
            bar_code = scanner.decode(image_file)
            if not bar_code:
                bar_code = scanner.decode(image_file, try_harder=True)
        except:
            print('Failed to scan image:', image_file)
            continue
        if bar_code:
            coa_url = bar_code.parsed
            print(f"COA URL found for post {post_id}: {coa_url}")
        
        # Record the image hash and any COA URL.
        image_cache.set(image_hash, {'coa_url': coa_url, 'post_id': post_id})
    
    # Return any image URLs.
    return get_image_cache_urls(image_cache_path)


def download_coa(
        url: str,
        pdf_dir: str,
        filename: str,
        use_cache: bool = True,
    ):
    outfile = os.path.join(pdf_dir, filename)
    if os.path.exists(outfile) and use_cache:
        print('Existing file:', outfile)
        return
    try:
        response = requests.get(url, allow_redirects=True)
        if response.status_code == 200:
            if len(response.content) < MIN_FILE_SIZE:
                print('File size is small, retrying with Selenium:', url)
                download_file_with_selenium(response.url, download_dir=pdf_dir, filename=filename)
            else:
                with open(outfile, 'wb') as pdf:
                    pdf.write(response.content)
                print('Downloaded:', outfile)
        else:
            print('Failed to download, retrying with Selenium:', url)
            download_file_with_selenium(url, download_dir=pdf_dir, filename=filename)
    except Exception as e:
        print(f"Error downloading {url}: {str(e)}")
        print('Retrying with Selenium standard method.')
        try:
            download_file_with_selenium(url, download_dir=pdf_dir, filename=filename)
        except:
            print('Retrying with Selenium targeting `a` tag.')
            download_file_with_selenium(
                url,
                download_dir=pdf_dir,
                method='a',
                tag_name='a',
                filename=filename,
            )


#-----------------------------------------------------------------------
# Main execution.
#-----------------------------------------------------------------------

def get_results_reddit(base_dir='./data'):
    """Main function to get results from Reddit."""
    driver = initialize_selenium(headless=False)
    config = dotenv_values('.env')
    query = None  # Set to a query string for search, e.g., 'results'; None for /new/
    for state, info in STATE_SUBREDDIT.items():
        subreddit = info['subreddit']
        state_name = info['state_name']
        data_dir = os.path.join(base_dir, state_name, subreddit)
        images_directory = os.path.join(data_dir, 'images')
        pdf_dir = os.path.join(base_dir, state_name, 'results/pdfs', subreddit)
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(images_directory, exist_ok=True)
        os.makedirs(pdf_dir, exist_ok=True)
        logging.info(f'Processing subreddit: {subreddit}')
        data = collect_all_posts(driver, subreddit, query=query)
        save_post_data(data, data_dir, namespace=f'{state}-reddit-raw-posts')
        collected_posts = read_all_posts(data_dir)
        try:
            collected_post_ids = set(collected_posts['post_id'].str.split('_').str[-1].tolist())
        except KeyError:
            collected_post_ids = []
        logging.info(f'Total already collected posts: {len(collected_post_ids)}')
        all_posts = get_reddit_post_data(
            data,
            collected_post_ids=collected_post_ids,
            config=config,
            images_directory=images_directory,
        )
        save_post_data(all_posts, data_dir, namespace=f'{state}-reddit-collected-posts')
    driver.quit()


# === Test ===
if __name__ == '__main__':

    # Get results from Reddit.
    get_results_reddit(base_dir='D://data')

    # TODO: Refactor the following into re-usable functions.

    # Scan images for URLs for each state.
    for state, values in STATE_SUBREDDIT.items():
        subreddit = values['subreddit']
        state_name = values['state_name']
        data_dir = f'D://data/{state_name}/{subreddit}'
        images_directory = f'D://data/{state_name}/{subreddit}/images'
        image_cache_path = os.path.join(data_dir, f'{state}-image-urls.jsonl')
        image_urls = scan_image_urls(images_directory, image_cache_path, reverse=True)
        print(f'{state} | Number of unique COA URLs:', len(image_urls))

    # Downloaded all of the COA PDFs.
    for state, values in STATE_SUBREDDIT.items():

        # Read the image URLs.
        subreddit = values['subreddit']
        state_name = values['state_name']
        data_dir = f'D://data/{state_name}/{subreddit}'
        images_directory = f'D://data/{state_name}/{subreddit}/images'
        pdf_dir = f'D://data/{state_name}/results/pdfs/{subreddit}'
        image_cache_path = os.path.join(data_dir, f'{state}-image-urls.jsonl')
        try:
            image_urls = get_image_cache_urls(image_cache_path)
        except:
            print(f'{state} | No image URLs found.')
            continue
        print(f'{state} | Number of unique COA URLs:', len(image_urls))

        # Download all PDFs.
        for index, row in image_urls.iterrows():
            post_id = row['post_id']
            url = row['coa_url']
            try:

                # Download a confident cannabis COA.
                if 'confidentcannabis.com' in url:
                    filename = f"{post_id}-coa-{index}.pdf"
                    sample_id = url.rstrip('/').split('/')[-1]
                    pdf_url = f'https://orders.confidentcannabis.com/report/public/pdf/{sample_id}'
                    download_file_with_selenium(
                        pdf_url,
                        download_dir=pdf_dir,
                        method='confident_cannabis',
                        filename=filename,
                    )
                    print(f"Downloaded COA for post {post_id}: {pdf_url}")

                # Download regular PDFs.
                if url.startswith('http'):
                    filename = f"{post_id}-coa-{index}.pdf"
                    download_coa(url, pdf_dir, filename)
                    print(f"Downloaded COA for post {post_id}: {url}")
                else:
                    print('Invalid URL:', url)
            
            except Exception as e:
                print(f"Error downloading {url}: {str(e)}")
                continue

        # Remove any duplicate PDFs.
        remove_duplicate_files(pdf_dir, verbose=True)

    # Parse all of the COA PDFs.
    parser = CoADoc()
    temp_path = tempfile.mkdtemp()
    all_failed = []
    for state, values in STATE_SUBREDDIT.items():

        # Get all PDF files.
        state_name = values['state_name']
        subreddit = values['subreddit']
        pdf_dir = f'D://data/{state_name}/results/pdfs/{subreddit}'
        pdf_files = os.listdir(pdf_dir)
        pdf_files = [os.path.join(pdf_dir, x) for x in pdf_files]

        # Parse the COA PDFs.
        failed = []
        data_dir = f'D://data/{state_name}/{subreddit}'
        coa_cache_path = os.path.join(data_dir, f'{state}-results-reddit.jsonl')
        coa_cache = Bogart(coa_cache_path)
        print(f'Parsing {len(pdf_files)} COA PDFs for {state}...')
        for pdf_file in pdf_files:

            # Use cached data if available.
            pdf_hash = coa_cache.hash_file(pdf_file)
            if coa_cache.get(pdf_hash):
                print('Already parsed: %s' % pdf_file)
                continue

            # Parse the PDF.
            try:
                coa_data = parser.parse_pdf(
                    pdf_file,
                    temp_path=temp_path,
                    verbose=True,
                    # use_qr_code=False,
                )
            except Exception as e:
                print('Failed to parse: %s' % pdf_file)
                print(str(e))
                failed.append(pdf_file)
                continue

            # Cache any parsed data.
            if coa_data:
                print('Parsed: %s' % pdf_file)
                if isinstance(coa_data, list):
                    obs = coa_data[0]
                elif isinstance(coa_data, dict):
                    obs = coa_data
                obs['coa_pdf'] = os.path.basename(pdf_file)
                # FIXME: Ensure data has a post_id.
                # obs['post_id'] = post_id
                coa_cache.set(pdf_hash, obs)
            else:
                print('Found no data: %s' % pdf_file)

        # Keep track of the failed files.
        all_failed.extend(failed)

    # Remove the temporary directory.
    try:
        shutil.rmtree(temp_path)
    except:
        pass

    # Calculate the parsing accuracy.
    total_files = len(pdf_files)
    failed_files = len(all_failed)
    parsed_files = total_files - failed_files
    accuracy = parsed_files / total_files
    print('Total files:', total_files)
    print('Failed files:', failed_files)
    print('Parsed files:', parsed_files)
    print('Accuracy:', accuracy)

    # Parse all of the COA URLs.
    parser = CoADoc()
    temp_path = tempfile.mkdtemp()
    all_failed_urls = []
    for state, values in STATE_SUBREDDIT.items():

        # Identify the state and subreddit.
        subreddit = values['subreddit']
        state_name = values['state_name']
        data_dir = f'D://data/{state_name}/{subreddit}'

        # Read the image URLs.
        images_directory = f'D://data/{state_name}/{subreddit}/images'
        image_cache_path = os.path.join(data_dir, f'{state}-image-urls.jsonl')
        try:
            image_urls = get_image_cache_urls(image_cache_path)
        except:
            print(f'{state} | No image URLs found.')
            # continue
        print(f'{state} | Number of unique COA URLs:', len(image_urls))

        # Parse all of the URLs.
        coa_cache_path = os.path.join(data_dir, f'{state}-results-reddit.jsonl')
        coa_cache = Bogart(coa_cache_path)
        print('Number of already parsed results:', len(coa_cache.cache))
        for index, row in image_urls.iterrows():
            post_id = row['post_id']
            url = row['coa_url']

            # Use cached data if available.
            url_hash = coa_cache.hash_url(url)
            if coa_cache.get(url_hash):
                print('Already parsed: %s' % url)
                continue

            # Parse the URL.
            try:
                coa_data = parser.parse_url(url, verbose=True)
            except Exception as e:
                print('Failed to parse: %s' % url)
                print(str(e))
                all_failed_urls.append(url)
                continue

            # Cache any parsed data.
            if coa_data:
                print('Parsed: %s' % url)
                if isinstance(coa_data, list):
                    obs = coa_data[0]
                elif isinstance(coa_data, dict):
                    obs = coa_data
                obs['coa_url'] = url
                obs['post_id'] = post_id
                coa_cache.set(url_hash, obs)
            else:
                print('Found no data: %s' % url)

    # Read all parsed COAs.
    all_results = []
    for state, values in STATE_SUBREDDIT.items():

        # Read all COAs for a given state.
        subreddit = values['subreddit']
        state_name = values['state_name']
        data_dir = f'D://data/{state_name}/{subreddit}'
        coa_cache_path = os.path.join(data_dir, f'{state}-results-reddit.jsonl')
        coa_cache = Bogart(coa_cache_path)
        results = coa_cache.to_df()
        all_results.append(results)
        print(f'{state} | Number of parsed results:', len(results))

    # Aggregate all results.
    all_results = pd.concat(all_results)
    all_results.drop_duplicates(subset=['sample_id', 'results_hash'], inplace=True)
    all_results.reset_index(drop=True, inplace=True)
    print('Total number of unique results:', len(all_results))
