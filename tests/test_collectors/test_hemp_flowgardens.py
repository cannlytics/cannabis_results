"""
Test Suite | Hemp | Flow Gardens
Copyright (c) 2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2/24/2026
Updated: 2/24/2026
License: CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>

Description:
    Comprehensive unit and integration tests for the Flow Gardens
    hemp COA collector (``get_results_hemp_flowgardens.py``).

    Unit tests run without network access or Selenium.
    Integration tests require network access.

Usage:
    ```bash
    # Run all unit tests
    python algorithms/test_hemp_flowgardens.py

    # Run integration tests (network required)
    python algorithms/test_hemp_flowgardens.py --integration

    # Run with verbose output
    python algorithms/test_hemp_flowgardens.py -v
    ```
"""
# Standard imports:
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

# External imports:
import pandas as pd

# Module under test:
from get_results_hemp_flowgardens import (
    FlowGardensCollector,
    COA_PAGE_URL,
    FLOWGARDENS_PRODUCER,
    SECTION_PRODUCT_TYPES,
    DEFAULT_HEADERS,
    COA_EXTENSIONS,
    PDF_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MIN_PDF_SIZE,
    MIN_IMAGE_SIZE,
    _generate_result_id,
    _compute_file_hash,
    _extract_product_name_from_url,
    _extract_filename_from_url,
    _get_file_extension,
    _is_coa_url,
    _sanitize_filename,
    _is_valid_file,
    _parse_section_heading,
)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test Fixtures                                                    ║
# ╚══════════════════════════════════════════════════════════════════╝

# Sample HTML fragment matching the real Flow Gardens COA page structure.
SAMPLE_COA_HTML = """
<html>
<body>
<main>
<h2>Certificate of Analysis</h2>
<p>View and download our laboratory-tested certificates of analysis.</p>

<a href="https://cdn.shopify.com/s/files/1/0931/4012/4017/files/41_Kings_COA__63796.1751033130.1280.1280.jpg?v=1753866760">
    <h3>41 Kings</h3>
</a>
<a href="https://cdn.shopify.com/s/files/1/0931/4012/4017/files/Alien_Grape.pdf?v=1761586188">
    <h3>Alien Grape</h3>
</a>
<a href="https://cdn.shopify.com/s/files/1/0931/4012/4017/files/Animal_Face.png?v=1757439611">
    <h3>Animal Face</h3>
</a>
<a href="https://cdn.shopify.com/s/files/1/0931/4012/4017/files/Black_Ice.pdf?v=1757704071">
    <h3>Black Ice</h3>
</a>
<a href="/collections/flower">
    <h3>View Flower</h3>
</a>

<h2>Pre-Rolls</h2>
<a href="https://cdn.shopify.com/s/files/1/0931/4012/4017/files/Butterberry_COA.pdf?v=1757011711">
    <h3>Butterberry</h3>
</a>

<h2>Concentrates</h2>
<a href="https://cdn.shopify.com/s/files/1/0931/4012/4017/files/Black_Garlic.pdf?v=1756233529">
    <h3>Black Garlic Live Rosin</h3>
</a>

<h2>Edibles</h2>
<a href="https://cdn.shopify.com/s/files/1/0931/4012/4017/files/Gummy_1-1_Celestial_COA.pdf?v=1757010613">
    <h3>THC:CBD Gummies 1:1</h3>
</a>

</main>
</body>
</html>
"""

# Sample COA URLs from the real page.
SAMPLE_PDF_URL = (
    'https://cdn.shopify.com/s/files/1/0931/4012/4017/files/'
    'Black_Ice.pdf?v=1757704071'
)
SAMPLE_IMAGE_URL = (
    'https://cdn.shopify.com/s/files/1/0931/4012/4017/files/'
    '41_Kings_COA__63796.1751033130.1280.1280.jpg?v=1753866760'
)
SAMPLE_PNG_URL = (
    'https://cdn.shopify.com/s/files/1/0931/4012/4017/files/'
    'Animal_Face.png?v=1757439611'
)

# Fake PDF content (valid).
FAKE_PDF_CONTENT = b'%PDF-1.4' + b'\x00' * (MIN_PDF_SIZE + 100)

# Fake JPEG content (valid).
FAKE_JPEG_CONTENT = b'\xff\xd8\xff\xe0' + b'\x00' * (MIN_IMAGE_SIZE + 100)

# Fake PNG content (valid).
FAKE_PNG_CONTENT = b'\x89PNG\r\n\x1a\n' + b'\x00' * (MIN_IMAGE_SIZE + 100)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Unit Tests: Helper Functions                                     ║
# ╚══════════════════════════════════════════════════════════════════╝

class TestResults:
    """Simple test result tracker."""
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def ok(self, name: str, condition: bool, detail: str = ''):
        if condition:
            self.passed += 1
            print(f'  ✓ {name}')
        else:
            self.failed += 1
            msg = f'  ✗ {name}'
            if detail:
                msg += f' — {detail}'
            print(msg)
            self.errors.append(name)

    @property
    def success(self) -> bool:
        return self.failed == 0

    def summary(self):
        total = self.passed + self.failed
        print(f'\n  Results: {self.passed}/{total} passed, {self.failed} failed')
        if self.errors:
            print(f'  Failed tests: {", ".join(self.errors)}')


def test_generate_result_id(r: TestResults):
    """Test _generate_result_id."""
    print('\n--- _generate_result_id ---')

    # Deterministic.
    id1 = _generate_result_id('test.pdf')
    id2 = _generate_result_id('test.pdf')
    r.ok('Deterministic', id1 == id2, f'{id1} != {id2}')

    # Correct length.
    r.ok('Length is 16', len(id1) == 16, f'len={len(id1)}')

    # Different inputs → different IDs.
    id3 = _generate_result_id('other.pdf')
    r.ok('Different inputs differ', id1 != id3)

    # Empty string doesn't crash.
    id4 = _generate_result_id('')
    r.ok('Empty string OK', len(id4) == 16)

    # Hex characters only.
    import re
    r.ok('Hex chars only', bool(re.match(r'^[0-9a-f]+$', id1)))


def test_extract_product_name_from_url(r: TestResults):
    """Test _extract_product_name_from_url."""
    print('\n--- _extract_product_name_from_url ---')

    cases = [
        (SAMPLE_PDF_URL, 'Black Ice'),
        (SAMPLE_IMAGE_URL, '41 Kings'),
        (SAMPLE_PNG_URL, 'Animal Face'),
        (
            'https://cdn.shopify.com/s/files/1/0931/4012/4017/files/'
            'Gummy_1-1_Celestial_COA.pdf?v=1757010613',
            'Gummy 1-1 Celestial',
        ),
        (
            'https://cdn.shopify.com/s/files/1/0931/4012/4017/files/'
            'Candy_Shish_6.pdf?v=1764088117',
            'Candy Shish',
        ),
        (
            'https://cdn.shopify.com/s/files/1/0931/4012/4017/files/'
            'Apple_and_Banana.pdf?v=1756229364',
            'Apple and Banana',
        ),
        ('', ''),
        (None, ''),
    ]

    for url, expected in cases:
        result = _extract_product_name_from_url(url or '')
        label = (url or 'None')[-50:] if url else 'None'
        r.ok(
            f'{label} → "{result}"',
            result == expected,
            f'expected "{expected}"',
        )


def test_extract_filename_from_url(r: TestResults):
    """Test _extract_filename_from_url."""
    print('\n--- _extract_filename_from_url ---')

    cases = [
        (SAMPLE_PDF_URL, 'Black_Ice.pdf'),
        (SAMPLE_IMAGE_URL, '41_Kings_COA__63796.1751033130.1280.1280.jpg'),
        ('', ''),
    ]

    for url, expected in cases:
        result = _extract_filename_from_url(url)
        r.ok(f'→ "{result}"', result == expected, f'expected "{expected}"')


def test_get_file_extension(r: TestResults):
    """Test _get_file_extension."""
    print('\n--- _get_file_extension ---')

    cases = [
        (SAMPLE_PDF_URL, '.pdf'),
        (SAMPLE_IMAGE_URL, '.jpg'),
        (SAMPLE_PNG_URL, '.png'),
        ('https://example.com/page', ''),
        ('', ''),
    ]

    for url, expected in cases:
        result = _get_file_extension(url)
        r.ok(f'"{url[-30:]}" → "{result}"', result == expected, f'expected "{expected}"')


def test_is_coa_url(r: TestResults):
    """Test _is_coa_url."""
    print('\n--- _is_coa_url ---')

    r.ok('PDF URL is COA', _is_coa_url(SAMPLE_PDF_URL))
    r.ok('JPG URL is COA', _is_coa_url(SAMPLE_IMAGE_URL))
    r.ok('PNG URL is COA', _is_coa_url(SAMPLE_PNG_URL))
    r.ok('Non-COA URL rejected', not _is_coa_url('https://example.com/page'))
    r.ok('Collection URL rejected', not _is_coa_url('/collections/flower'))
    r.ok('Empty rejected', not _is_coa_url(''))
    r.ok('None rejected', not _is_coa_url(None))


def test_sanitize_filename(r: TestResults):
    """Test _sanitize_filename."""
    print('\n--- _sanitize_filename ---')

    r.ok('Colon removed', ':' not in _sanitize_filename('Test: Name'))
    r.ok('Slash removed', '/' not in _sanitize_filename('A/B'))
    r.ok('Backslash removed', '\\' not in _sanitize_filename('A\\B'))
    r.ok('Question mark removed', '?' not in _sanitize_filename('What?'))
    r.ok('Normal name preserved', _sanitize_filename('Black_Ice.pdf') == 'Black_Ice.pdf')
    r.ok(
        'Max length respected',
        len(_sanitize_filename('x' * 300, max_length=100)) <= 100,
    )
    r.ok('Empty string OK', _sanitize_filename('') == '')


def test_is_valid_file(r: TestResults):
    """Test _is_valid_file."""
    print('\n--- _is_valid_file ---')

    # Valid PDF.
    r.ok('Valid PDF accepted', _is_valid_file(FAKE_PDF_CONTENT, SAMPLE_PDF_URL))

    # Too-small PDF.
    small_pdf = b'%PDF-1.4' + b'\x00' * 100
    r.ok('Small PDF rejected', not _is_valid_file(small_pdf, SAMPLE_PDF_URL))

    # HTML disguised as PDF.
    html_content = b'<html>' + b'\x00' * 20000
    r.ok('HTML rejected as PDF', not _is_valid_file(html_content, SAMPLE_PDF_URL))

    # Valid JPEG.
    r.ok('Valid JPEG accepted', _is_valid_file(FAKE_JPEG_CONTENT, SAMPLE_IMAGE_URL))

    # Valid PNG.
    r.ok('Valid PNG accepted', _is_valid_file(FAKE_PNG_CONTENT, SAMPLE_PNG_URL))

    # Too-small image.
    small_img = b'\xff\xd8\xff\xe0' + b'\x00' * 10
    r.ok('Small image rejected', not _is_valid_file(small_img, SAMPLE_IMAGE_URL))


def test_parse_section_heading(r: TestResults):
    """Test _parse_section_heading."""
    print('\n--- _parse_section_heading ---')

    cases = [
        ('Certificate of Analysis', 'flower'),
        ('Pre-Rolls', 'preroll'),
        ('Concentrates', 'concentrate'),
        ('Edibles', 'edible'),
        ('Flower', 'flower'),
        ('  Pre-Rolls  ', 'preroll'),
        ('Unknown Section', None),
        ('', None),
        (None, None),
    ]

    for text, expected in cases:
        result = _parse_section_heading(text)
        label = f'"{text}"' if text else str(text)
        r.ok(f'{label} → {result}', result == expected, f'expected {expected}')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Unit Tests: Constants & Configuration                            ║
# ╚══════════════════════════════════════════════════════════════════╝

def test_constants(r: TestResults):
    """Test module constants are properly defined."""
    print('\n--- Constants ---')

    r.ok('COA_PAGE_URL defined', COA_PAGE_URL.startswith('https://'))
    r.ok('Producer has name', FLOWGARDENS_PRODUCER['producer'] == 'Flow Gardens')
    r.ok('Producer state is TN', FLOWGARDENS_PRODUCER['producer_state'] == 'tn')
    r.ok('Headers have User-Agent', 'User-Agent' in DEFAULT_HEADERS)
    r.ok('PDF extensions include .pdf', '.pdf' in PDF_EXTENSIONS)
    r.ok('Image extensions include .jpg', '.jpg' in IMAGE_EXTENSIONS)
    r.ok('COA extensions combine both', '.pdf' in COA_EXTENSIONS and '.jpg' in COA_EXTENSIONS)
    r.ok('Section types map pre-rolls', SECTION_PRODUCT_TYPES.get('pre-rolls') == 'preroll')


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Unit Tests: FlowGardensCollector                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

def test_collector_init(r: TestResults):
    """Test FlowGardensCollector initialization."""
    print('\n--- FlowGardensCollector.__init__ ---')

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = FlowGardensCollector(
            coa_dir=os.path.join(tmpdir, 'coas'),
            data_dir=tmpdir,
            verbose=False,
        )

        r.ok('coa_dir set', collector.coa_dir.exists())
        r.ok('datasets_dir created', collector.datasets_dir.exists())
        r.ok('manifest_path set', 'flowgardens-manifest.csv' in str(collector.manifest_path))
        r.ok('session created', collector.session is not None)
        r.ok('pause set', collector.pause > 0)


def test_collector_context_manager(r: TestResults):
    """Test FlowGardensCollector context manager."""
    print('\n--- FlowGardensCollector context manager ---')

    with tempfile.TemporaryDirectory() as tmpdir:
        with FlowGardensCollector(
            coa_dir=os.path.join(tmpdir, 'coas'),
            data_dir=tmpdir,
            verbose=False,
        ) as collector:
            r.ok('__enter__ returns self', collector is not None)
        # Session should be closed after __exit__.
        r.ok('__exit__ completes', True)


def test_catalog_existing_empty(r: TestResults):
    """Test catalog_existing with an empty directory."""
    print('\n--- catalog_existing (empty) ---')

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = FlowGardensCollector(
            coa_dir=os.path.join(tmpdir, 'coas'),
            data_dir=tmpdir,
            verbose=False,
        )
        manifest = collector.catalog_existing()
        r.ok('Returns DataFrame', isinstance(manifest, pd.DataFrame))
        r.ok('Empty for empty dir', len(manifest) == 0)


def test_catalog_existing_with_files(r: TestResults):
    """Test catalog_existing with sample files on disk."""
    print('\n--- catalog_existing (with files) ---')

    with tempfile.TemporaryDirectory() as tmpdir:
        coa_dir = os.path.join(tmpdir, 'coas')
        os.makedirs(coa_dir)

        # Write sample files.
        pdf_path = os.path.join(coa_dir, 'Test_Product.pdf')
        with open(pdf_path, 'wb') as f:
            f.write(FAKE_PDF_CONTENT)

        img_path = os.path.join(coa_dir, 'Test_Image.jpg')
        with open(img_path, 'wb') as f:
            f.write(FAKE_JPEG_CONTENT)

        # Non-COA file should be ignored.
        txt_path = os.path.join(coa_dir, 'notes.txt')
        with open(txt_path, 'w') as f:
            f.write('not a COA')

        collector = FlowGardensCollector(
            coa_dir=coa_dir,
            data_dir=tmpdir,
            verbose=False,
        )
        manifest = collector.catalog_existing()

        r.ok('Found 2 files', len(manifest) == 2, f'found {len(manifest)}')
        r.ok('Has file_name col', 'file_name' in manifest.columns)
        r.ok('Has sha256 col', 'sha256' in manifest.columns)
        r.ok('Has file_type col', 'file_type' in manifest.columns)
        r.ok('Has product_name col', 'product_name' in manifest.columns)

        # Check file types.
        types = set(manifest['file_type'].tolist())
        r.ok('Has pdf type', 'pdf' in types)
        r.ok('Has image type', 'image' in types)

        # Check hashes are populated.
        r.ok('Hashes populated', all(len(h) == 64 for h in manifest['sha256']))

        # Check manifest was saved.
        r.ok('Manifest CSV created', collector.manifest_path.exists())

        # .txt file should NOT be in manifest.
        fnames = manifest['file_name'].tolist()
        r.ok('txt file excluded', 'notes.txt' not in fnames)


def test_scrape_coa_page_mock(r: TestResults):
    """Test scrape_coa_page with mocked HTTP response."""
    print('\n--- scrape_coa_page (mocked) ---')

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = FlowGardensCollector(
            coa_dir=os.path.join(tmpdir, 'coas'),
            data_dir=tmpdir,
            verbose=False,
        )

        # Mock the HTTP response.
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_COA_HTML
        mock_response.raise_for_status = MagicMock()

        with patch.object(collector.session, 'get', return_value=mock_response):
            discovered = collector.scrape_coa_page()

        r.ok('Returns DataFrame', isinstance(discovered, pd.DataFrame))
        r.ok(
            'Found 7 COA links',
            len(discovered) == 7,
            f'found {len(discovered)}',
        )

        # Check required columns.
        for col in ['product_name', 'product_type', 'coa_url', 'file_name', 'section']:
            r.ok(f'Has {col} column', col in discovered.columns)

        # Check product types by section.
        if len(discovered) > 0:
            flower_rows = discovered[discovered['product_type'] == 'flower']
            preroll_rows = discovered[discovered['product_type'] == 'preroll']
            concentrate_rows = discovered[discovered['product_type'] == 'concentrate']
            edible_rows = discovered[discovered['product_type'] == 'edible']

            r.ok('Flower section has 4', len(flower_rows) == 4, f'got {len(flower_rows)}')
            r.ok('Pre-Rolls section has 1', len(preroll_rows) == 1, f'got {len(preroll_rows)}')
            r.ok('Concentrates section has 1', len(concentrate_rows) == 1, f'got {len(concentrate_rows)}')
            r.ok('Edibles section has 1', len(edible_rows) == 1, f'got {len(edible_rows)}')

        # Check non-COA link was filtered.
        urls = discovered['coa_url'].tolist()
        r.ok(
            'Non-COA link filtered',
            not any('/collections/' in u for u in urls),
        )

        # Check product names.
        names = discovered['product_name'].tolist()
        r.ok('"41 Kings" found', '41 Kings' in names)
        r.ok('"Alien Grape" found', 'Alien Grape' in names)
        r.ok('"Butterberry" found', 'Butterberry' in names)


def test_download_new_coas_mock(r: TestResults):
    """Test download_new_coas with mocked HTTP responses."""
    print('\n--- download_new_coas (mocked) ---')

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = FlowGardensCollector(
            coa_dir=os.path.join(tmpdir, 'coas'),
            data_dir=tmpdir,
            pause=0.01,  # Fast for tests.
            verbose=False,
        )

        # Create mock discovered DataFrame.
        discovered = pd.DataFrame([
            {
                'product_name': 'Black Ice',
                'product_type': 'flower',
                'coa_url': SAMPLE_PDF_URL,
                'file_name': 'Black_Ice.pdf',
                'file_extension': '.pdf',
                'section': 'flower',
            },
            {
                'product_name': '41 Kings',
                'product_type': 'flower',
                'coa_url': SAMPLE_IMAGE_URL,
                'file_name': '41_Kings_COA__63796.1751033130.1280.1280.jpg',
                'file_extension': '.jpg',
                'section': 'flower',
            },
        ])

        # Mock HTTP response returning valid content.
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = FAKE_PDF_CONTENT
        mock_resp.raise_for_status = MagicMock()

        with patch.object(collector.session, 'get', return_value=mock_resp):
            downloaded = collector.download_new_coas(discovered, existing_files=set())

        r.ok('Downloaded 2 files', downloaded == 2, f'downloaded {downloaded}')

        # Verify files were written.
        files = os.listdir(str(collector.coa_dir))
        r.ok('Files on disk', len(files) == 2, f'{len(files)} files')

        # Test skipping existing files.
        with patch.object(collector.session, 'get', return_value=mock_resp):
            downloaded2 = collector.download_new_coas(
                discovered,
                existing_files={'Black_Ice.pdf'},
            )
        r.ok('Skipped existing', downloaded2 == 1, f'downloaded {downloaded2}')


def test_download_handles_invalid_content(r: TestResults):
    """Test that download_new_coas rejects invalid file content."""
    print('\n--- download_new_coas (invalid content) ---')

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = FlowGardensCollector(
            coa_dir=os.path.join(tmpdir, 'coas'),
            data_dir=tmpdir,
            pause=0.01,
            verbose=False,
        )

        discovered = pd.DataFrame([{
            'product_name': 'Bad File',
            'product_type': 'flower',
            'coa_url': SAMPLE_PDF_URL,
            'file_name': 'Bad_File.pdf',
            'file_extension': '.pdf',
            'section': 'flower',
        }])

        # Return HTML instead of PDF.
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'<html><body>Error</body></html>'
        mock_resp.raise_for_status = MagicMock()

        with patch.object(collector.session, 'get', return_value=mock_resp):
            downloaded = collector.download_new_coas(discovered, set())

        r.ok('Rejected invalid content', downloaded == 0)
        files = os.listdir(str(collector.coa_dir))
        r.ok('No files written', len(files) == 0)


def test_convert_to_lab_results(r: TestResults):
    """Test _convert_to_lab_results."""
    print('\n--- _convert_to_lab_results ---')

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = FlowGardensCollector(
            coa_dir=os.path.join(tmpdir, 'coas'),
            data_dir=tmpdir,
            verbose=False,
        )

        manifest = pd.DataFrame([
            {
                'file_name': 'Black_Ice.pdf',
                'file_path': '/tmp/Black_Ice.pdf',
                'file_type': 'pdf',
                'file_extension': '.pdf',
                'file_size_bytes': 50000,
                'sha256': 'abc123' * 10 + 'abcd',
                'product_name': 'Black Ice',
                'cataloged_at': datetime.now().isoformat(),
            },
            {
                'file_name': '41_Kings_COA__63796.jpg',
                'file_path': '/tmp/41_Kings_COA__63796.jpg',
                'file_type': 'image',
                'file_extension': '.jpg',
                'file_size_bytes': 30000,
                'sha256': 'def456' * 10 + 'defg',
                'product_name': '41 Kings',
                'cataloged_at': datetime.now().isoformat(),
            },
        ])

        discovered = pd.DataFrame([
            {
                'product_name': 'Black Ice',
                'product_type': 'flower',
                'coa_url': SAMPLE_PDF_URL,
                'file_name': 'Black_Ice.pdf',
                'section': 'flower',
            },
            {
                'product_name': '41 Kings',
                'product_type': 'flower',
                'coa_url': SAMPLE_IMAGE_URL,
                'file_name': '41_Kings_COA__63796.jpg',
                'section': 'flower',
            },
        ])

        results = collector._convert_to_lab_results(manifest, discovered)

        r.ok('Returns list', isinstance(results, list))
        r.ok('2 results', len(results) == 2, f'got {len(results)}')

        if results:
            rec = results[0]
            r.ok('Has result_id', 'result_id' in rec and len(rec['result_id']) == 16)
            r.ok('Has product_name', rec['product_name'] == 'Black Ice')
            r.ok('Has product_type', rec['product_type'] == 'flower')
            r.ok('Has producer', rec['producer'] == 'Flow Gardens')
            r.ok('Has state', rec['state'] == 'tn')
            r.ok('Has source', rec['source'] == 'flowgardens')
            r.ok('Has coa_url', rec['coa_url'] == SAMPLE_PDF_URL)
            r.ok('Has coa_file', rec['coa_file'] == 'Black_Ice.pdf')
            r.ok('Has coa_file_type', rec['coa_file_type'] == 'pdf')
            r.ok('Has file_hash', len(rec['file_hash']) > 0)

    # Test with empty manifest.
    with tempfile.TemporaryDirectory() as tmpdir:
        collector2 = FlowGardensCollector(
            coa_dir=os.path.join(tmpdir, 'coas'),
            data_dir=tmpdir,
            verbose=False,
        )
        empty_results = collector2._convert_to_lab_results(pd.DataFrame())
        r.ok('Empty manifest → empty list', len(empty_results) == 0)


def test_get_results_catalog_only(r: TestResults):
    """Test get_results in catalog-only mode."""
    print('\n--- get_results (catalog_only) ---')

    with tempfile.TemporaryDirectory() as tmpdir:
        coa_dir = os.path.join(tmpdir, 'coas')
        os.makedirs(coa_dir)

        # Place a sample PDF.
        with open(os.path.join(coa_dir, 'Test_Strain.pdf'), 'wb') as f:
            f.write(FAKE_PDF_CONTENT)

        collector = FlowGardensCollector(
            coa_dir=coa_dir,
            data_dir=tmpdir,
            verbose=False,
        )

        results = collector.get_results(catalog_only=True)
        r.ok('Returns DataFrame', isinstance(results, pd.DataFrame))
        r.ok('1 result', len(results) == 1, f'got {len(results)}')

        # Check CSV was saved.
        csv_path = collector.datasets_dir / 'hemp-results-flowgardens-latest.csv'
        r.ok('CSV saved', csv_path.exists())


def test_archive_stats(r: TestResults):
    """Test archive_stats."""
    print('\n--- archive_stats ---')

    with tempfile.TemporaryDirectory() as tmpdir:
        coa_dir = os.path.join(tmpdir, 'coas')
        os.makedirs(coa_dir)

        # Write sample files.
        with open(os.path.join(coa_dir, 'A.pdf'), 'wb') as f:
            f.write(FAKE_PDF_CONTENT)
        with open(os.path.join(coa_dir, 'B.jpg'), 'wb') as f:
            f.write(FAKE_JPEG_CONTENT)
        with open(os.path.join(coa_dir, 'C.png'), 'wb') as f:
            f.write(FAKE_PNG_CONTENT)

        collector = FlowGardensCollector(
            coa_dir=coa_dir,
            data_dir=tmpdir,
            verbose=False,
        )
        stats = collector.archive_stats()

        r.ok('total_files is 3', stats['total_files'] == 3, f'got {stats["total_files"]}')
        r.ok('total_pdfs is 1', stats['total_pdfs'] == 1)
        r.ok('total_images is 2', stats['total_images'] == 2)
        r.ok('total_size_bytes > 0', stats['total_size_bytes'] > 0)
        r.ok('total_size_mb >= 0', stats['total_size_mb'] >= 0)


def test_compute_file_hash(r: TestResults):
    """Test _compute_file_hash."""
    print('\n--- _compute_file_hash ---')

    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b'test content')
        tmppath = f.name

    try:
        h1 = _compute_file_hash(tmppath)
        h2 = _compute_file_hash(tmppath)
        r.ok('Deterministic', h1 == h2)
        r.ok('Length is 64 (SHA-256)', len(h1) == 64)
        r.ok('Hex chars', all(c in '0123456789abcdef' for c in h1))
    finally:
        os.unlink(tmppath)


def test_download_empty_discovered(r: TestResults):
    """Test download_new_coas with empty DataFrame."""
    print('\n--- download_new_coas (empty) ---')

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = FlowGardensCollector(
            coa_dir=os.path.join(tmpdir, 'coas'),
            data_dir=tmpdir,
            verbose=False,
        )
        downloaded = collector.download_new_coas(pd.DataFrame(), set())
        r.ok('Returns 0 for empty', downloaded == 0)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Integration Tests (Network Required)                             ║
# ╚══════════════════════════════════════════════════════════════════╝

def test_integration_scrape_live(r: TestResults):
    """Integration test: scrape the live COA page."""
    print('\n--- INTEGRATION: scrape_coa_page (live) ---')

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = FlowGardensCollector(
            coa_dir=os.path.join(tmpdir, 'coas'),
            data_dir=tmpdir,
            verbose=False,
        )
        try:
            discovered = collector.scrape_coa_page()
            r.ok('Returns DataFrame', isinstance(discovered, pd.DataFrame))
            r.ok('Found >50 COAs', len(discovered) > 50, f'found {len(discovered)}')

            # Check sections are populated.
            if len(discovered) > 0:
                sections = discovered['section'].unique().tolist()
                types = discovered['product_type'].unique().tolist()
                r.ok('Multiple sections', len(sections) >= 2, f'sections: {sections}')
                r.ok('Has flower type', 'flower' in types)
                r.ok('Has concentrate type', 'concentrate' in types)

                # Check URLs are valid Shopify CDN URLs.
                cdn_urls = discovered['coa_url'].str.startswith('https://cdn.shopify.com/')
                r.ok('All CDN URLs', cdn_urls.all())

                # Check file extensions.
                exts = discovered['file_extension'].unique().tolist()
                r.ok('Has .pdf extension', '.pdf' in exts)
        except Exception as e:
            r.ok(f'Scrape failed: {e}', False)


def test_integration_download_sample(r: TestResults):
    """Integration test: download a few sample COAs."""
    print('\n--- INTEGRATION: download sample COAs ---')

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = FlowGardensCollector(
            coa_dir=os.path.join(tmpdir, 'coas'),
            data_dir=tmpdir,
            pause=1.0,
            verbose=False,
        )
        try:
            discovered = collector.scrape_coa_page()
            if len(discovered) == 0:
                r.ok('No COAs to download', False, 'scrape returned empty')
                return

            # Download first 2 PDFs and first image.
            pdfs = discovered[discovered['file_extension'] == '.pdf'].head(2)
            images = discovered[discovered['file_extension'].isin(['.jpg', '.png'])].head(1)
            sample = pd.concat([pdfs, images])

            downloaded = collector.download_new_coas(sample, set())
            r.ok(f'Downloaded {downloaded}/{len(sample)}', downloaded >= 1)

            # Check files exist on disk.
            files = os.listdir(str(collector.coa_dir))
            r.ok('Files on disk', len(files) >= 1, f'{len(files)} files')

            # Verify at least one file is valid.
            for fname in files:
                fpath = os.path.join(str(collector.coa_dir), fname)
                size = os.path.getsize(fpath)
                r.ok(f'{fname} size OK ({size:,} bytes)', size > 1000)
                break  # Just check first.

        except Exception as e:
            r.ok(f'Download test failed: {e}', False)


def test_integration_full_pipeline(r: TestResults):
    """Integration test: run get_results with scrape + download (limited)."""
    print('\n--- INTEGRATION: full pipeline (limited) ---')

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = FlowGardensCollector(
            coa_dir=os.path.join(tmpdir, 'coas'),
            data_dir=tmpdir,
            pause=1.0,
            verbose=False,
        )
        try:
            # Scrape but don't download (test pipeline flow).
            results = collector.get_results(
                scrape=True,
                download=False,
                save_results=True,
            )
            r.ok('Returns DataFrame', isinstance(results, pd.DataFrame))
            # Without download, results may be empty (no files on disk).
            r.ok('Pipeline completed', True)

            stats = collector.archive_stats()
            r.ok('Stats computed', isinstance(stats, dict))
            r.ok('Stats has total_files', 'total_files' in stats)

        except Exception as e:
            r.ok(f'Pipeline failed: {e}', False)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Test Runner                                                      ║
# ╚══════════════════════════════════════════════════════════════════╝

def run_unit_tests() -> bool:
    """Run all unit tests (no network required).

    Returns:
        True if all tests passed.
    """
    print('\n' + '=' * 64)
    print('UNIT TESTS: Flow Gardens Hemp COA Collector')
    print('=' * 64)

    r = TestResults()

    # Helper function tests.
    test_generate_result_id(r)
    test_extract_product_name_from_url(r)
    test_extract_filename_from_url(r)
    test_get_file_extension(r)
    test_is_coa_url(r)
    test_sanitize_filename(r)
    test_is_valid_file(r)
    test_parse_section_heading(r)
    test_compute_file_hash(r)
    test_constants(r)

    # Collector tests (mocked, no network).
    test_collector_init(r)
    test_collector_context_manager(r)
    test_catalog_existing_empty(r)
    test_catalog_existing_with_files(r)
    test_scrape_coa_page_mock(r)
    test_download_new_coas_mock(r)
    test_download_handles_invalid_content(r)
    test_download_empty_discovered(r)
    test_convert_to_lab_results(r)
    test_get_results_catalog_only(r)
    test_archive_stats(r)

    r.summary()
    print('=' * 64)
    return r.success


def run_integration_tests() -> bool:
    """Run integration tests (network required).

    Returns:
        True if all tests passed.
    """
    print('\n' + '=' * 64)
    print('INTEGRATION TESTS: Flow Gardens Hemp COA Collector')
    print('=' * 64)

    r = TestResults()

    test_integration_scrape_live(r)
    test_integration_download_sample(r)
    test_integration_full_pipeline(r)

    r.summary()
    print('=' * 64)
    return r.success


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Test Flow Gardens hemp COA collector.',
    )
    parser.add_argument(
        '--integration',
        action='store_true',
        help='Run integration tests (requires network).',
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Verbose output.',
    )
    args = parser.parse_args()

    # Always run unit tests.
    unit_ok = run_unit_tests()

    # Optionally run integration tests.
    integration_ok = True
    if args.integration:
        integration_ok = run_integration_tests()

    exit(0 if (unit_ok and integration_ok) else 1)