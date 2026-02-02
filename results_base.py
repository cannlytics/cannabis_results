"""
Cannabis Results | COA Collector Base Class
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2/1/2026
Updated: 2/1/2026
License: <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:
    Enhanced base class for all COA collection algorithms.
    Provides standardized logging, caching, rate limiting,
    and error handling for consistent collector behavior.
"""
# Standard imports:
from abc import ABC, abstractmethod
from datetime import datetime
import hashlib
import logging
import os
from pathlib import Path
import random
import time
from typing import Optional, List, Dict, Any, Callable

# External imports:
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Note: Selenium is imported conditionally to support non-web collectors
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    webdriver = None

# Internal imports - use relative import if within package
try:
    from config.results_config import PATHS, API_CONFIG, get_collector_config
except ImportError:
    # Fallback for standalone use
    PATHS = None
    API_CONFIG = {
        'rate_limit_delay': 3.33,
        'max_retries': 3,
        'timeout': 30,
        'exponential_backoff_base': 2,
        'max_backoff_delay': 60,
    }
    get_collector_config = None


class COACollector(ABC):
    """Base class for all COA collectors.
    
    Provides common functionality for:
    - File system management (directories, paths)
    - Logging with file and console output
    - Caching to avoid re-downloading
    - Rate limiting with exponential backoff
    - Selenium WebDriver management
    - PDF downloading with retry logic
    - Results saving in standardized format
    
    Subclasses must implement the `get_results()` method.
    
    Example usage:
        ```python
        class MyCollector(COACollector):
            def get_results(self, **kwargs) -> pd.DataFrame:
                # Implementation
                pass
        
        collector = MyCollector(state='ca', source='my_source')
        results = collector.get_results()
        ```
    """
    
    def __init__(
            self,
            state: str,
            source: str,
            data_dir: Optional[str] = None,
            pdf_dir: Optional[str] = None,
            cache_path: Optional[str] = None,
            log_dir: Optional[str] = None,
            log_name: Optional[str] = None,
            pause_time: Optional[float] = None,
            max_retries: Optional[int] = None,
            verbose: bool = True,
        ):
        """Initialize the collector.
        
        Args:
            state: Two-letter state code (e.g., 'ca', 'ny').
            source: Data source identifier (e.g., 'flower_company').
            data_dir: Override for data directory path.
            pdf_dir: Override for PDF storage directory.
            cache_path: Override for cache file path.
            log_dir: Override for log directory.
            log_name: Override for log file name.
            pause_time: Seconds to pause between requests.
            max_retries: Maximum retry attempts for failed requests.
            verbose: Whether to enable verbose logging.
        """
        self.state = state.lower()
        self.source = source
        self.verbose = verbose
        
        # Configure paths
        if PATHS:
            self.data_dir = Path(data_dir) if data_dir else PATHS.state_dir(state)
            self.pdf_dir = Path(pdf_dir) if pdf_dir else PATHS.pdf_dir(state, source)
            self.cache_path = cache_path or str(PATHS.cache_path(f'results-{state}-{source}'))
            self._log_dir = Path(log_dir) if log_dir else PATHS.log_dir
        else:
            # Fallback for standalone use
            self.data_dir = Path(data_dir or f'./data/{state}')
            self.pdf_dir = Path(pdf_dir or f'./data/{state}/pdfs/{source}')
            self.cache_path = cache_path or f'./.cache/results-{state}-{source}.jsonl'
            self._log_dir = Path(log_dir or './.logs')
        
        self.datasets_dir = self.data_dir / 'datasets'
        
        # Configure timing
        self.pause_time = pause_time or API_CONFIG.get('rate_limit_delay', 3.33)
        self.max_retries = max_retries or API_CONFIG.get('max_retries', 3)
        self._backoff_base = API_CONFIG.get('exponential_backoff_base', 2)
        self._max_backoff = API_CONFIG.get('max_backoff_delay', 60)
        
        # Ensure directories exist
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.datasets_dir.mkdir(parents=True, exist_ok=True)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup logging
        self.log_name = log_name or f'get_results_{state}_{source}'
        self.logger = self._setup_logging()
        
        # Initialize cache
        self.cache = self._init_cache()
        
        # Selenium driver (lazy initialization)
        self.driver: Optional[webdriver.Chrome] = None
        
        # HTTP session with retry logic
        self._session = self._create_session()
        
        # Statistics tracking
        self._stats = {
            'started_at': datetime.now(),
            'downloads': 0,
            'cached': 0,
            'errors': 0,
            'retries': 0,
        }
    
    @abstractmethod
    def get_results(self, **kwargs) -> pd.DataFrame:
        """Collect results from the data source.
        
        Must be implemented by subclasses.
        
        Returns:
            DataFrame containing collected results.
        """
        pass
    
    # === Logging ===
    
    def _setup_logging(self) -> logging.Logger:
        """Configure logging for the collector.
        
        Creates both file and console handlers with timestamp formatting.
        
        Returns:
            Configured logger instance.
        """
        logger = logging.getLogger(self.log_name)
        logger.setLevel(logging.INFO)
        
        # Avoid duplicate handlers
        if logger.handlers:
            return logger
        
        # File handler with date
        date_str = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
        log_file = self._log_dir / f'{self.log_name.replace("_", "-")}-{date_str}.log'
        fh = logging.FileHandler(log_file, encoding='utf-8')
        fh.setLevel(logging.INFO)
        
        # Console handler
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO if self.verbose else logging.WARNING)
        
        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s | %(name)s | %(levelname)s | %(message)s',
            datefmt='%Y-%m-%dT%H:%M:%S'
        )
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)
        
        logger.addHandler(fh)
        logger.addHandler(ch)
        
        return logger
    
    # === Caching ===
    
    def _init_cache(self):
        """Initialize the cache for tracking downloads.
        
        Returns:
            Cache instance (Bogart if available, else dict-based fallback).
        """
        try:
            from cannlytics.data.cache import Bogart
            cache_dir = Path(self.cache_path).parent
            cache_dir.mkdir(parents=True, exist_ok=True)
            return Bogart(self.cache_path)
        except ImportError:
            self.logger.warning("Bogart cache not available, using in-memory dict")
            return _DictCache(self.cache_path)
    
    def is_cached(self, url: str) -> bool:
        """Check if a URL has been cached.
        
        Args:
            url: URL to check.
            
        Returns:
            True if URL is in cache.
        """
        url_hash = self._hash_url(url)
        return bool(self.cache.get(url_hash))
    
    def _hash_url(self, url: str) -> str:
        """Generate a hash for a URL.
        
        Args:
            url: URL to hash.
            
        Returns:
            MD5 hash of the URL.
        """
        return hashlib.md5(url.encode()).hexdigest()
    
    # === HTTP Requests ===
    
    def _create_session(self) -> requests.Session:
        """Create an HTTP session with retry logic.
        
        Returns:
            Configured requests Session.
        """
        session = requests.Session()
        retry_strategy = Retry(
            total=self.max_retries,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"],
            backoff_factor=1
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session
    
    def download_file(
            self,
            url: str,
            destination: str,
            headers: Optional[Dict] = None,
            skip_cached: bool = True,
        ) -> bool:
        """Download a file with retry logic.
        
        Args:
            url: URL to download from.
            destination: Local path to save file.
            headers: Optional HTTP headers.
            skip_cached: Whether to skip if already cached.
            
        Returns:
            True if download succeeded, False otherwise.
        """
        url_hash = self._hash_url(url)
        
        # Check cache
        if skip_cached and self.cache.get(url_hash):
            self.logger.info(f'Skipped (cached): {url}')
            self._stats['cached'] += 1
            return True
        
        # Download with retries
        for attempt in range(self.max_retries):
            try:
                response = self._session.get(
                    url,
                    headers=headers,
                    timeout=API_CONFIG.get('timeout', 30),
                    stream=True
                )
                response.raise_for_status()
                
                # Save file
                with open(destination, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                # Update cache
                self.cache.set(url_hash, {
                    'url': url,
                    'file': destination,
                    'downloaded_at': datetime.now().isoformat(),
                })
                
                self.logger.info(f'Downloaded: {destination}')
                self._stats['downloads'] += 1
                return True
                
            except requests.RequestException as e:
                self._stats['retries'] += 1
                delay = min(
                    self._backoff_base ** attempt + random.uniform(0, 1),
                    self._max_backoff
                )
                self.logger.warning(
                    f'Download failed (attempt {attempt + 1}/{self.max_retries}): {e}. '
                    f'Retrying in {delay:.1f}s...'
                )
                time.sleep(delay)
        
        self.logger.error(f'Failed to download after {self.max_retries} attempts: {url}')
        self._stats['errors'] += 1
        return False
    
    # === Rate Limiting ===
    
    def rate_limit(self, multiplier: float = 1.0, jitter: bool = True):
        """Apply rate limiting delay.
        
        Args:
            multiplier: Factor to multiply base pause time.
            jitter: Whether to add random jitter (reduces detection).
        """
        delay = self.pause_time * multiplier
        if jitter:
            delay += random.uniform(0, self.pause_time * 0.2)
        time.sleep(delay)
    
    # === Selenium ===
    
    def _init_selenium(
            self,
            headless: bool = True,
            download_dir: Optional[str] = None,
            **kwargs
        ) -> None:
        """Initialize Selenium WebDriver.
        
        Args:
            headless: Whether to run browser in headless mode.
            download_dir: Directory for automatic downloads.
            **kwargs: Additional options to pass to Chrome.
        """
        if not SELENIUM_AVAILABLE:
            raise ImportError("Selenium is not installed. Install with: pip install selenium")
        
        download_dir = download_dir or str(self.pdf_dir)
        
        options = Options()
        if headless:
            options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        
        # Configure downloads
        prefs = {
            'download.default_directory': download_dir,
            'download.prompt_for_download': False,
            'download.directory_upgrade': True,
            'plugins.always_open_pdf_externally': True,
        }
        options.add_experimental_option('prefs', prefs)
        
        self.driver = webdriver.Chrome(options=options)
        self.driver.implicitly_wait(10)
        self.logger.info(f'Selenium driver initialized (headless={headless})')
    
    def _quit_driver(self) -> None:
        """Safely quit the Selenium driver."""
        if self.driver:
            try:
                self.driver.quit()
                self.logger.info('Selenium driver closed')
            except Exception as e:
                self.logger.warning(f'Error closing driver: {e}')
            finally:
                self.driver = None
    
    def wait_for_element(
            self,
            by: str,
            value: str,
            timeout: int = 10,
            condition: str = 'presence'
        ):
        """Wait for an element to be available.
        
        Args:
            by: Locator type (e.g., 'css selector', 'id').
            value: Locator value.
            timeout: Maximum seconds to wait.
            condition: 'presence' or 'clickable'.
            
        Returns:
            The located element.
        """
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.common.by import By
        
        by_map = {
            'css selector': By.CSS_SELECTOR,
            'id': By.ID,
            'xpath': By.XPATH,
            'class name': By.CLASS_NAME,
            'tag name': By.TAG_NAME,
        }
        locator = (by_map.get(by, by), value)
        
        wait = WebDriverWait(self.driver, timeout)
        if condition == 'clickable':
            return wait.until(EC.element_to_be_clickable(locator))
        return wait.until(EC.presence_of_element_located(locator))
    
    # === Results Saving ===
    
    def save_results(
            self,
            df: pd.DataFrame,
            prefix: Optional[str] = None,
            include_date: bool = True,
            save_latest: bool = True,
        ) -> str:
        """Save results to CSV file(s).
        
        Args:
            df: DataFrame to save.
            prefix: File name prefix.
            include_date: Whether to include date in filename.
            save_latest: Whether to also save as 'latest' version.
            
        Returns:
            Path to the saved file.
        """
        prefix = prefix or f'results-{self.state}-{self.source}'
        date_str = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
        
        # Save dated version
        if include_date:
            dated_file = self.datasets_dir / f'{prefix}-{date_str}.csv'
            df.to_csv(dated_file, index=False)
            self.logger.info(f'Saved: {dated_file}')
        
        # Save latest version
        if save_latest:
            latest_file = self.datasets_dir / f'{prefix}-latest.csv'
            df.to_csv(latest_file, index=False)
            self.logger.info(f'Saved: {latest_file}')
        
        return str(dated_file if include_date else latest_file)
    
    # Alias for backward compatibility
    _save_results = save_results
    
    # === Statistics ===
    
    def get_stats(self) -> Dict[str, Any]:
        """Get collection statistics.
        
        Returns:
            Dictionary with download/cache/error counts.
        """
        self._stats['duration_seconds'] = (
            datetime.now() - self._stats['started_at']
        ).total_seconds()
        return self._stats
    
    def log_stats(self) -> None:
        """Log collection statistics summary."""
        stats = self.get_stats()
        self.logger.info(
            f"Collection complete: {stats['downloads']} downloads, "
            f"{stats['cached']} cached, {stats['errors']} errors, "
            f"{stats['retries']} retries in {stats['duration_seconds']:.1f}s"
        )
    
    # === Context Manager ===
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup resources."""
        self._quit_driver()
        self.log_stats()
        return False


class _DictCache:
    """Simple dictionary-based cache fallback when Bogart is not available."""
    
    def __init__(self, path: str):
        self.path = path
        self.data: Dict[str, Any] = {}
        self._load()
    
    def _load(self):
        """Load cache from file if exists."""
        import json
        try:
            if os.path.exists(self.path):
                with open(self.path, 'r') as f:
                    for line in f:
                        entry = json.loads(line)
                        if 'key' in entry:
                            self.data[entry['key']] = entry.get('value')
        except Exception:
            pass
    
    def get(self, key: str) -> Any:
        """Get a value from cache."""
        return self.data.get(key)
    
    def set(self, key: str, value: Any) -> None:
        """Set a value in cache."""
        import json
        self.data[key] = value
        # Append to file
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, 'a') as f:
                f.write(json.dumps({'key': key, 'value': value}) + '\n')
        except Exception:
            pass
    
    def hash_url(self, url: str) -> str:
        """Hash a URL."""
        return hashlib.md5(url.encode()).hexdigest()


# === Test ===
if __name__ == '__main__':
    # Test the base class structure
    print("COACollector base class loaded successfully")
    print(f"Selenium available: {SELENIUM_AVAILABLE}")
    print(f"Default pause time: {API_CONFIG.get('rate_limit_delay', 3.33)}")
