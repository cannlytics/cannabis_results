"""
Cannabis Results | Selenium Driver Utilities
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2/2/2026
Updated: 2/2/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Reusable Selenium WebDriver initialization with automatic ChromeDriver
    version management. This module handles the common ChromeDriver version
    mismatch issues by using webdriver-manager.
    
    IMPORTANT: This module explicitly uses webdriver-manager to download
    the correct ChromeDriver version, bypassing any outdated drivers
    that may be in the system PATH.

Usage:
    ```python
    from config.driver_utils import initialize_driver
    
    driver = initialize_driver(headless=True)
    driver.get('https://example.com')
    # ... use driver ...
    driver.quit()
    ```

Installation:
    pip install selenium webdriver-manager
"""
# Standard imports:
import os
from typing import Optional

# Selenium imports:
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    webdriver = None
    ChromeOptions = None
    ChromeService = None

# webdriver_manager for automatic driver management.
try:
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.core.os_manager import ChromeType
    WEBDRIVER_MANAGER_AVAILABLE = True
except ImportError:
    WEBDRIVER_MANAGER_AVAILABLE = False
    ChromeDriverManager = None
    ChromeType = None


def initialize_driver(
        headless: bool = True,
        window_size: str = '1920,1200',
        download_dir: Optional[str] = None,
        verbose: bool = False,
):
    """Initialize a Selenium Chrome driver with automatic version management.
    
    This function tries multiple methods to initialize a WebDriver:
    1. webdriver-manager (preferred) - downloads correct ChromeDriver version
    2. Selenium's built-in manager (Selenium 4.6+) - automatic driver management
    3. Edge fallback - uses Microsoft Edge if Chrome fails
    
    IMPORTANT: This function explicitly uses webdriver-manager to bypass
    any outdated ChromeDriver versions that may exist in the system PATH.
    
    Args:
        headless: Whether to run the browser in headless mode.
        window_size: Browser window size as 'width,height'.
        download_dir: Directory for automatic downloads (PDF handling).
        verbose: Whether to print initialization progress.
        
    Returns:
        A configured Chrome (or Edge) WebDriver instance.
        
    Raises:
        ImportError: If Selenium is not installed.
        RuntimeError: If no WebDriver could be initialized.
        
    Example:
        >>> driver = initialize_driver(headless=True)
        >>> driver.get('https://example.com')
        >>> print(driver.title)
        >>> driver.quit()
    """
    if not SELENIUM_AVAILABLE:
        raise ImportError(
            "Selenium is not installed. Install with: pip install selenium webdriver-manager"
        )
    
    # Configure Chrome options
    options = ChromeOptions()
    options.add_argument(f'--window-size={window_size}')
    if headless:
        options.add_argument('--headless=new')  # Modern headless mode for Chrome 109+
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    
    # Suppress logging
    options.add_argument('--log-level=3')
    options.add_experimental_option('excludeSwitches', ['enable-logging'])
    
    # Configure download preferences
    prefs = {
        'download.prompt_for_download': False,
        'download.directory_upgrade': True,
        'plugins.always_open_pdf_externally': True,
        'safebrowsing.enabled': True,
    }
    if download_dir:
        prefs['download.default_directory'] = download_dir
    options.add_experimental_option('prefs', prefs)
    
    # Exclude automation flags
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)
    
    errors_encountered = []
    
    # Method 1: Use webdriver_manager (preferred - downloads correct version)
    if WEBDRIVER_MANAGER_AVAILABLE:
        try:
            if verbose:
                print('  [1/3] Initializing Chrome via webdriver-manager...')
            
            # Force webdriver-manager to download the correct driver
            # This bypasses any outdated drivers in PATH
            driver_path = ChromeDriverManager().install()
            
            if verbose:
                print(f'       ChromeDriver path: {driver_path}')
            
            service = ChromeService(executable_path=driver_path)
            driver = webdriver.Chrome(service=service, options=options)
            driver.implicitly_wait(10)
            
            if verbose:
                print('       ✓ Chrome driver initialized successfully via webdriver-manager')
            return driver
            
        except Exception as e:
            error_msg = f'webdriver-manager failed: {e}'
            errors_encountered.append(error_msg)
            if verbose:
                print(f'       ✗ {error_msg}')
    else:
        errors_encountered.append('webdriver-manager not installed')
        if verbose:
            print('  [1/3] webdriver-manager not available, skipping...')
    
    # Method 2: Use Selenium's built-in driver management (Selenium 4.6+)
    try:
        if verbose:
            print('  [2/3] Trying Selenium built-in driver management...')
        
        # Selenium 4.6+ can auto-download drivers via Selenium Manager
        # Create service without specifying executable_path to trigger auto-download
        service = ChromeService()
        driver = webdriver.Chrome(service=service, options=options)
        driver.implicitly_wait(10)
        
        if verbose:
            print('       ✓ Chrome driver initialized via Selenium Manager')
        return driver
        
    except Exception as e:
        error_msg = f'Selenium built-in management failed: {e}'
        errors_encountered.append(error_msg)
        if verbose:
            print(f'       ✗ {error_msg}')
    
    # Method 3: Fallback to Edge
    try:
        if verbose:
            print('  [3/3] Falling back to Microsoft Edge...')
        
        from selenium.webdriver.edge.options import Options as EdgeOptions
        from selenium.webdriver.edge.service import Service as EdgeService
        
        edge_options = EdgeOptions()
        if headless:
            edge_options.add_argument('--headless=new')
        edge_options.add_argument(f'--window-size={window_size}')
        edge_options.add_argument('--disable-gpu')
        edge_options.add_argument('--no-sandbox')
        
        # Try with webdriver-manager for Edge
        if WEBDRIVER_MANAGER_AVAILABLE:
            try:
                from webdriver_manager.microsoft import EdgeChromiumDriverManager
                edge_driver_path = EdgeChromiumDriverManager().install()
                edge_service = EdgeService(executable_path=edge_driver_path)
                driver = webdriver.Edge(service=edge_service, options=edge_options)
            except Exception:
                # Fall back to default Edge service
                driver = webdriver.Edge(options=edge_options)
        else:
            driver = webdriver.Edge(options=edge_options)
        
        driver.implicitly_wait(10)
        
        if verbose:
            print('       ✓ Edge driver initialized successfully')
        return driver
        
    except Exception as e:
        error_msg = f'Edge fallback failed: {e}'
        errors_encountered.append(error_msg)
        if verbose:
            print(f'       ✗ {error_msg}')
    
    # All methods failed
    error_details = '\n  - '.join([''] + errors_encountered)
    raise RuntimeError(
        f'Could not initialize any WebDriver. Errors encountered:{error_details}\n\n'
        'Troubleshooting:\n'
        '1. Ensure Chrome or Edge browser is installed\n'
        '2. Install webdriver-manager: pip install webdriver-manager\n'
        '3. Remove outdated chromedriver from PATH (check C:\\Python39\\Scripts\\)\n'
        '4. Check network connectivity for driver downloads'
    )


def initialize_driver_with_retry(
        headless: bool = True,
        max_retries: int = 3,
        verbose: bool = False,
        **kwargs,
):
    """Initialize a WebDriver with retry logic.
    
    Args:
        headless: Whether to run headless.
        max_retries: Maximum number of initialization attempts.
        verbose: Whether to print progress.
        **kwargs: Additional arguments passed to initialize_driver.
        
    Returns:
        A configured WebDriver instance.
        
    Raises:
        RuntimeError: If initialization fails after all retries.
    """
    import time
    
    last_error = None
    for attempt in range(max_retries):
        try:
            if verbose:
                print(f'WebDriver initialization attempt {attempt + 1}/{max_retries}')
            return initialize_driver(headless=headless, verbose=verbose, **kwargs)
        except Exception as e:
            last_error = e
            if verbose:
                print(f'  Attempt {attempt + 1} failed: {e}')
            if attempt < max_retries - 1:
                time.sleep(2)  # Wait before retry
    
    raise RuntimeError(f'Failed to initialize WebDriver after {max_retries} attempts: {last_error}')


def get_driver_info(driver) -> dict:
    """Get information about the current WebDriver.
    
    Args:
        driver: WebDriver instance.
        
    Returns:
        Dictionary with driver information.
    """
    if driver is None:
        return {'status': 'not_initialized'}
    
    try:
        caps = driver.capabilities
        return {
            'status': 'active',
            'browser_name': caps.get('browserName', 'unknown'),
            'browser_version': caps.get('browserVersion', 'unknown'),
            'platform': caps.get('platformName', 'unknown'),
            'chrome_driver_version': caps.get('chrome', {}).get('chromedriverVersion', 'unknown'),
        }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


def cleanup_old_chromedriver():
    """Find and report old ChromeDriver installations that may cause issues.
    
    Returns:
        List of paths to potentially problematic ChromeDriver installations.
    """
    import shutil
    
    problem_paths = []
    
    # Common locations for old chromedriver
    search_paths = [
        'C:\\Python39\\Scripts\\chromedriver.exe',
        'C:\\Python38\\Scripts\\chromedriver.exe',
        'C:\\Python37\\Scripts\\chromedriver.exe',
        os.path.expanduser('~\\chromedriver.exe'),
        os.path.expanduser('~\\Desktop\\chromedriver.exe'),
    ]
    
    # Also check PATH
    path_dirs = os.environ.get('PATH', '').split(os.pathsep)
    for path_dir in path_dirs:
        chromedriver_path = os.path.join(path_dir, 'chromedriver.exe')
        if chromedriver_path not in search_paths:
            search_paths.append(chromedriver_path)
    
    for path in search_paths:
        if os.path.exists(path):
            problem_paths.append(path)
    
    return problem_paths


# === Quick test ===
if __name__ == '__main__':
    print('='*60)
    print('Selenium Driver Utilities - Diagnostic Test')
    print('='*60)
    
    print(f'\nSelenium available: {SELENIUM_AVAILABLE}')
    print(f'webdriver-manager available: {WEBDRIVER_MANAGER_AVAILABLE}')
    
    # Check for old chromedriver installations
    print('\nChecking for old ChromeDriver installations...')
    old_drivers = cleanup_old_chromedriver()
    if old_drivers:
        print('  ⚠ Found potentially problematic ChromeDriver installations:')
        for path in old_drivers:
            print(f'    - {path}')
        print('  Consider removing these to avoid version conflicts.')
    else:
        print('  ✓ No problematic ChromeDriver installations found.')
    
    if not SELENIUM_AVAILABLE:
        print('\nERROR: Selenium not installed')
        print('Run: pip install selenium webdriver-manager')
        exit(1)
    
    print('\nTesting driver initialization...')
    try:
        driver = initialize_driver(headless=True, verbose=True)
        print(f'\nDriver info: {get_driver_info(driver)}')
        
        print('\nTesting navigation...')
        driver.get('https://www.google.com')
        print(f'Page title: {driver.title}')
        
        driver.quit()
        print('\n' + '='*60)
        print('SUCCESS! WebDriver is working correctly.')
        print('='*60)
    except Exception as e:
        print(f'\nERROR: {e}')
        print('\nTroubleshooting:')
        print('1. Remove old chromedriver: del C:\\Python39\\Scripts\\chromedriver.exe')
        print('2. Reinstall webdriver-manager: pip install --upgrade webdriver-manager')
        print('3. Clear webdriver-manager cache: rm -rf ~/.wdm')
        exit(1)