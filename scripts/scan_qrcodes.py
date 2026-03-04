"""
Scan QR Codes | COA QR Code URL Extraction Pipeline
Copyright (c) 2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 2/26/2026
Updated: 2/26/2026
License: MIT License <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:
    Standalone, idempotent script that scans COA PDF front pages for
    QR codes and extracts URLs (``coa_url``) keyed by ``pdf_hash``.

    This script is designed to run independently and concurrently
    alongside other pipeline scripts. It produces a cache of
    ``{pdf_hash: coa_url}`` mappings that can be merged into the
    main results dataset during the ``process_results.py`` phase.

    Architecture:
        Python handles: PDF discovery, page-to-image extraction,
                        QR region detection (OpenCV, optional),
                        caching (Bogart), hash computation, orchestration
        Rust handles:   QR code decoding via ``qrustie`` binary
                        (memory-safe, no interpreter crashes)

    Why Rust for QR decoding?
        Certain COA PDFs (notably from NY labs) contain QR codes that
        cause Python QR decoders to crash catastrophically — killing
        the interpreter with no recoverable error. Rust's memory
        safety guarantees produce clean errors instead of segfaults.
        The Rust decoder also applies 4 preprocessing strategies ×
        2 decoder engines = 8 decode attempts per image for maximum
        detection rate.

    Detection Strategy (two-phase):
        Phase 1: Full-page decode — send the entire page image to
                 the decoder (Rust or Python). Works for most COAs.
        Phase 2: Region-crop decode — if Phase 1 fails and OpenCV
                 is available, use contour detection to find and crop
                 square-ish regions likely to be QR codes. Resize each
                 to a standard 512px width and decode individually.
                 This handles COAs where the QR code is small.

    Pipeline Position:
        This runs ALONGSIDE other scripts (not in sequence):
        get_results_{state}.py → ┬→ parse_coas.py      → process_results.py
                                 └→ scan_qrcodes.py ──┘

    Cache Structure:
        Cache file: .cache/qr-scan-{state}.jsonl
        Key: pdf_hash (SHA-256 of PDF content)
        Value: {
            'pdf_hash': str,       # SHA-256 hash
            'coa_url': str|None,   # Decoded URL or None
            'qr_data': str|None,   # Raw QR content (may not be URL)
            'decoder': str,        # Which engine decoded it
            'strategy': str,       # Which preprocessing worked
            'scanned_at': str,     # ISO timestamp
        }

Usage:
    # Scan QR codes for a specific state
    python scan_qrcodes.py --state ny

    # Scan with a specific source filter
    python scan_qrcodes.py --state fl --source flowery

    # Limit number of scans (for testing)
    python scan_qrcodes.py --state ca --max-scans 50

    # View cache statistics
    python scan_qrcodes.py --state ny --cache-stats

    # Dry run (show what would be scanned)
    python scan_qrcodes.py --state ny --dry-run

    # Specify custom qrustie binary path
    python scan_qrcodes.py --state ny --qrustie-path ./qrustie/target/release/qrustie

    # Export found URLs to CSV
    python scan_qrcodes.py --state ny --export
"""
# Standard imports:
import argparse
import hashlib
import json
import logging
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

# External imports:
import pandas as pd
import pdfplumber

# Optional imports (enhance detection when available).
try:
    import cv2
    import numpy as np
    OPENCV_AVAILABLE = True
except ImportError:
    OPENCV_AVAILABLE = False

# Internal imports:
from cannlytics.data.cache import Bogart
from cannlytics.logs import initialize_logs
from cannlytics.utils.utils import hash_file

# Suppress pdfminer noise.
logging.getLogger('pdfminer').setLevel(logging.ERROR)

# Resolve repo root for config imports (scripts/ -> repo root).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Configuration                                                    ║
# ╚══════════════════════════════════════════════════════════════════╝

# Default paths: prefer centralized config, fall back to env / hardcoded.
try:
    from config.results_config import PATHS as _PATHS
    DEFAULT_DATA_DIR = _PATHS.data_dir
    DEFAULT_CACHE_DIR = _PATHS.cache_dir
    DEFAULT_LOG_DIR = _PATHS.log_dir
except ImportError:
    DEFAULT_DATA_DIR = Path(os.environ.get(
        'CANNLYTICS_DATA_DIR', 'D:/data',
    ))
    DEFAULT_CACHE_DIR = Path(os.environ.get(
        'CANNLYTICS_CACHE_DIR', 'D:/data/.cache',
    ))
    DEFAULT_LOG_DIR = Path(os.environ.get(
        'CANNLYTICS_LOG_DIR', 'D:/data/.logs',
    ))

# State name mapping (matches parse_coas.py).
STATE_NAMES = {
    'ak': 'alaska', 'az': 'arizona', 'ca': 'california',
    'co': 'colorado', 'ct': 'connecticut', 'fl': 'florida',
    'hi': 'hawaii', 'ma': 'massachusetts', 'md': 'maryland',
    'mi': 'michigan', 'mo': 'missouri', 'ms': 'mississippi',
    'nj': 'new-jersey', 'nv': 'nevada', 'ny': 'new-york',
    'oh': 'ohio', 'or': 'oregon', 'ri': 'rhode-island',
    'ut': 'utah', 'vt': 'vermont', 'wa': 'washington',
}

# Platform detection.
IS_WINDOWS = platform.system() == 'Windows'
WIN_MAX_PATH = 259

# Minimum PDF file size to process (skip tiny/corrupt files).
MIN_PDF_SIZE = 21_000  # ~21 KB

# Image resolution for PDF page rendering (DPI).
# 200 DPI balances quality with speed for QR detection.
RENDER_DPI = 200

# Maximum time (seconds) to wait for qrustie to decode a single image.
QRUSTIE_TIMEOUT = 30

# Default qrustie binary paths to search.
QRUSTIE_SEARCH_PATHS = [
    # Relative to this script.
    './qrustie/target/release/qrustie',
    './qrustie/target/debug/qrustie',
    '../qrustie/target/release/qrustie',
    # System PATH.
    'qrustie',
]
if IS_WINDOWS:
    QRUSTIE_SEARCH_PATHS = [
        p + '.exe' if not p.endswith('.exe') else p
        for p in QRUSTIE_SEARCH_PATHS
    ] + QRUSTIE_SEARCH_PATHS


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Windows Long Path Support                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

def _long_path(path: str) -> str:
    """Prepend the Windows extended-length path prefix if needed."""
    if not IS_WINDOWS:
        return path
    path = str(path)
    if path.startswith('\\\\?\\'):
        return path
    abs_path = os.path.abspath(path)
    if len(abs_path) > WIN_MAX_PATH:
        return f'\\\\?\\{abs_path}'
    return abs_path


def _safe_file_size(path: str, min_size: int = MIN_PDF_SIZE) -> bool:
    """Check if a file meets the minimum size, handling long paths."""
    try:
        return os.path.getsize(_long_path(path)) >= min_size
    except (OSError, FileNotFoundError):
        return False


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Utility Functions                                                ║
# ╚══════════════════════════════════════════════════════════════════╝

def find_qrustie_binary(explicit_path: Optional[str] = None) -> Optional[str]:
    """Locate the qrustie binary.

    Searches in order:
      1. Explicitly provided path (CLI argument)
      2. QRUSTIE_PATH environment variable
      3. Common relative paths (./qrustie/target/release/)
      4. System PATH

    On Windows, automatically tries appending .exe if the given
    path does not exist as-is.

    Args:
        explicit_path: User-provided path to the binary.

    Returns:
        Absolute path to the qrustie binary, or None if not found.
    """

    def _check_path(p: str) -> Optional[str]:
        """Check if a path points to an executable, trying .exe on Windows."""
        if not p:
            return None
        expanded = os.path.expanduser(p)
        # Try the path as given.
        if os.path.isfile(expanded):
            return os.path.abspath(expanded)
        # On Windows, try appending .exe if not already present.
        if IS_WINDOWS and not expanded.lower().endswith('.exe'):
            exe_path = expanded + '.exe'
            if os.path.isfile(exe_path):
                return os.path.abspath(exe_path)
        return None

    # Check explicit path first.
    if explicit_path:
        found = _check_path(explicit_path)
        if found:
            return found

    # Check environment variable.
    found = _check_path(os.environ.get('QRUSTIE_PATH', ''))
    if found:
        return found

    # Search common paths.
    for search_path in QRUSTIE_SEARCH_PATHS:
        found = _check_path(search_path)
        if found:
            return found

    # Try which/where on system PATH.
    cmd = 'where' if IS_WINDOWS else 'which'
    try:
        result = subprocess.run(
            [cmd, 'qrustie'],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip().split('\n')[0]
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    return None


def is_url(text: str) -> bool:
    """Check if a string looks like a URL.

    Args:
        text: String to check.

    Returns:
        True if the string parses as a valid HTTP(S) URL.
    """
    if not text:
        return False
    try:
        parsed = urlparse(text.strip())
        return parsed.scheme in ('http', 'https') and bool(parsed.netloc)
    except Exception:
        return False


def extract_page_image(
        pdf_path: str,
        page_index: int = 0,
        output_dir: Optional[str] = None,
        resolution: int = RENDER_DPI,
        logger: Optional[logging.Logger] = None,
    ) -> Optional[str]:
    """Extract a single page from a PDF as a JPEG image.

    Uses pdfplumber for conversion, which is already a dependency
    of the COA parsing pipeline.

    Args:
        pdf_path: Path to the PDF file.
        page_index: Page to extract (0-indexed, default first page).
        output_dir: Directory for the output image. Uses temp dir if None.
        resolution: Render resolution in DPI.
        logger: Optional logger for error diagnostics.

    Returns:
        Path to the JPEG image, or None on failure.
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix='qrustie_')
    os.makedirs(output_dir, exist_ok=True)

    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    image_path = os.path.join(output_dir, f'{pdf_name}_p{page_index}.jpeg')

    try:
        with pdfplumber.open(_long_path(pdf_path)) as pdf:
            if page_index >= len(pdf.pages):
                if logger:
                    logger.debug(
                        f'  Page {page_index} not in '
                        f'{os.path.basename(pdf_path)} '
                        f'({len(pdf.pages)} pages)'
                    )
                return None
            pdf.pages[page_index].to_image(
                resolution=resolution,
            ).save(image_path)
            return image_path
    except Exception as e:
        if logger:
            logger.debug(
                f'  Page extraction error for '
                f'{os.path.basename(pdf_path)}: {e}'
            )
        return None


def extract_qr_regions(
        image_path: str,
        output_dir: str,
        min_area: int = 1000,
        ar_range: Tuple[float, float] = (0.75, 1.35),
        resize_width: int = 512,
    ) -> List[str]:
    """Extract likely QR code regions from an image using contour detection.

    Uses OpenCV morphological operations to find square-ish, high-contrast
    regions in the image that are likely QR codes. Each region is cropped,
    resized to a standard size, and saved as a separate image file for
    decoding.

    This technique dramatically improves QR detection on full COA pages
    where the QR code is a small region of a dense document.

    Adapted from the original Cannlytics ``CoADoc.scan()`` method.

    Args:
        image_path: Path to the source image (full COA page).
        output_dir: Directory to save cropped region images.
        min_area: Minimum contour area to consider (pixels²).
        ar_range: Acceptable aspect ratio range (width/height) for
                  square-ish regions. QR codes are square (AR ≈ 1.0).
        resize_width: Width to resize cropped regions to (pixels).

    Returns:
        List of paths to cropped QR region images. Empty if OpenCV
        is not available or no regions are found.
    """
    if not OPENCV_AVAILABLE:
        return []

    try:
        image = cv2.imread(image_path)
        if image is None:
            return []

        # Grayscale → Gaussian blur → Otsu threshold.
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (9, 9), 0)
        thresh = cv2.threshold(
            blur, 0, 255,
            cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
        )[1]

        # Morphological close to connect nearby elements.
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        closed = cv2.morphologyEx(
            thresh, cv2.MORPH_CLOSE, kernel, iterations=2,
        )

        # Find contours.
        contours, _ = cv2.findContours(
            closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
        )

        regions = []
        ar_min, ar_max = ar_range

        for contour in contours:
            # Approximate the contour shape.
            peri = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.04 * peri, True)
            x, y, w, h = cv2.boundingRect(approx)
            area = cv2.contourArea(contour)
            ar = w / float(h) if h > 0 else 0

            # Filter for square-ish regions of sufficient size.
            # QR codes have 4 corners, aspect ratio ~1.0, and
            # meaningful area.
            if (len(approx) == 4
                    and area > min_area
                    and ar_min < ar < ar_max):

                # Crop the region from the original image.
                cropped = image[y:y + h, x:x + w]

                # Resize to standard width (preserving aspect ratio).
                h_resized = int(resize_width * (h / float(w)))
                resized = cv2.resize(
                    cropped,
                    (resize_width, h_resized),
                    interpolation=cv2.INTER_AREA,
                )

                # Save the cropped region.
                region_path = os.path.join(
                    output_dir,
                    f'qr_region_{len(regions)}_{x}_{y}.png',
                )
                cv2.imwrite(region_path, resized)
                regions.append(region_path)

        return regions

    except Exception:
        return []


def decode_qr_with_qrustie(
        image_path: str,
        qrustie_path: str,
        timeout: int = QRUSTIE_TIMEOUT,
    ) -> Dict:
    """Decode QR codes from an image using the qrustie Rust binary.

    Args:
        image_path: Path to the image file.
        qrustie_path: Path to the qrustie binary.
        timeout: Maximum seconds to wait for decoding.

    Returns:
        Dict with keys: found, data, decoder, strategy, error.
    """
    try:
        result = subprocess.run(
            [qrustie_path, '--input', image_path, '--first-only'],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.stdout.strip():
            return json.loads(result.stdout.strip())
        return {
            'found': False,
            'data': [],
            'decoder': '',
            'strategy': '',
            'error': result.stderr.strip() if result.stderr else '',
        }
    except subprocess.TimeoutExpired:
        return {
            'found': False,
            'data': [],
            'decoder': '',
            'strategy': '',
            'error': f'Timeout after {timeout}s',
        }
    except json.JSONDecodeError as e:
        return {
            'found': False,
            'data': [],
            'decoder': '',
            'strategy': '',
            'error': f'JSON parse error: {e}',
        }
    except Exception as e:
        return {
            'found': False,
            'data': [],
            'decoder': '',
            'strategy': '',
            'error': str(e),
        }


def decode_qr_with_python(image_path: str) -> Dict:
    """Fallback QR decoder using Python libraries.

    Used when qrustie is not available. Less reliable but
    provides basic functionality without Rust.

    Args:
        image_path: Path to the image file.

    Returns:
        Dict with keys: found, data, decoder, strategy, error.
    """
    # Try pyzbar first (most common Python QR decoder).
    try:
        from pyzbar import pyzbar
        from PIL import Image
        img = Image.open(image_path)
        decoded = pyzbar.decode(img)
        if decoded:
            data = [d.data.decode('utf-8') for d in decoded]
            return {
                'found': True,
                'data': data,
                'decoder': 'pyzbar',
                'strategy': 'direct',
                'error': '',
            }
    except ImportError:
        pass
    except Exception:
        pass

    # Try zxing-cpp as second fallback.
    try:
        import zxingcpp
        from PIL import Image
        img = Image.open(image_path)
        results = zxingcpp.read_barcodes(img)
        if results:
            data = [r.text for r in results if r.text]
            if data:
                return {
                    'found': True,
                    'data': data,
                    'decoder': 'zxingcpp',
                    'strategy': 'direct',
                    'error': '',
                }
    except ImportError:
        pass
    except Exception:
        pass

    return {
        'found': False,
        'data': [],
        'decoder': '',
        'strategy': '',
        'error': 'No Python QR decoder available (install pyzbar or zxingcpp)',
    }


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Core Scanner                                                      ║
# ╚══════════════════════════════════════════════════════════════════╝

class QRScanner:
    """COA QR code scanner with caching and PDF discovery.

    Scans COA PDF front pages for QR codes, extracts URLs,
    and caches results keyed by pdf_hash for later merging
    with the main results dataset.

    Attributes:
        state: Two-letter state code.
        data_dir: Root data directory.
        cache_dir: Cache directory for Bogart JSONL files.
        log_dir: Log directory.
        qrustie_path: Path to the qrustie Rust binary.
        use_python_fallback: Whether to fall back to Python decoders.
    """

    def __init__(
            self,
            state: str,
            data_dir: Optional[Path] = None,
            cache_dir: Optional[Path] = None,
            log_dir: Optional[Path] = None,
            qrustie_path: Optional[str] = None,
            use_python_fallback: bool = True,
            max_scans: Optional[int] = None,
            logger: Optional[logging.Logger] = None,
        ):
        self.state = state.lower()
        self.state_name = STATE_NAMES.get(self.state, self.state)
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.log_dir = log_dir or DEFAULT_LOG_DIR
        self.max_scans = max_scans
        self.use_python_fallback = use_python_fallback

        # Ensure directories exist.
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Initialize logger.
        self.logger = logger or initialize_logs(
            f'scan_qrcodes_{self.state}',
            prefix=f'qr-scan-{self.state}',
            log_dir=str(self.log_dir),
        )

        # Locate qrustie binary.
        self.qrustie_path = find_qrustie_binary(qrustie_path)
        if self.qrustie_path:
            self.logger.info(f'QRustie binary: {self.qrustie_path}')
        else:
            msg = 'QRustie binary not found'
            if use_python_fallback:
                self.logger.warning(f'{msg} - using Python fallback')
            else:
                self.logger.error(f'{msg} and Python fallback disabled')

        # Initialize cache.
        cache_name = f'qr-scan-{self.state}'
        self.cache = Bogart(
            str(self.cache_dir / f'{cache_name}.jsonl')
        )
        self.logger.info(
            f'Cache: {cache_name}.jsonl '
            f'({len(self.cache.to_df())} entries)'
        )

    @property
    def pdf_dir(self) -> Path:
        """Get the PDF directory for this state."""
        return self.data_dir / self.state_name / 'results' / 'pdfs'

    def discover_pdfs(self, source: str = '') -> pd.DataFrame:
        """Discover all COA PDFs for this state/source.

        Walks the PDF directory recursively, filters by minimum
        file size, computes SHA-256 hashes, and deduplicates.

        Args:
            source: Optional source subdirectory filter.

        Returns:
            DataFrame with columns: file_path, pdf_hash.
        """
        search_dir = self.pdf_dir / source if source else self.pdf_dir
        if not search_dir.exists():
            self.logger.warning(f'PDF directory not found: {search_dir}')
            return pd.DataFrame()

        pdf_files = []
        skipped_long = 0
        for root, _, files in os.walk(str(search_dir)):
            for f in files:
                if f.lower().endswith('.pdf'):
                    fp = os.path.join(root, f)
                    if _safe_file_size(fp):
                        pdf_files.append(fp)
                    elif IS_WINDOWS and len(fp) > WIN_MAX_PATH:
                        skipped_long += 1

        if skipped_long:
            self.logger.warning(
                f'Skipped {skipped_long} PDFs (likely long path issues)'
            )

        pdf_files.sort()
        df = pd.DataFrame({'file_path': pdf_files})
        if df.empty:
            return df

        df['pdf_hash'] = df['file_path'].apply(
            lambda x: hash_file(_long_path(x), size=65536)
        )
        df.drop_duplicates(subset=['pdf_hash'], inplace=True)
        self.logger.info(
            f'Discovered {len(df):,} unique PDFs in {search_dir}'
        )
        return df

    def scan_single_pdf(
            self,
            pdf_hash: str,
            file_path: str,
            temp_dir: str,
        ) -> Dict:
        """Scan a single PDF's front page for QR codes.

        Uses a two-phase approach for maximum detection rate:

        Phase 1: Send the full page image to the decoder. This works
                 for most COAs where the QR code is prominent.

        Phase 2 (fallback): If Phase 1 fails and OpenCV is available,
                 use contour detection to find square-ish regions in
                 the page that are likely QR codes. Crop each region,
                 resize to a standard size, and try decoding those
                 individually. This handles COAs where the QR code
                 is small relative to the full page.

        Args:
            pdf_hash: SHA-256 hash of the PDF file.
            file_path: Path to the PDF file.
            temp_dir: Directory for temporary image files.

        Returns:
            Cache entry dict with scan results.
        """
        # Extract first page as image.
        image_path = extract_page_image(
            file_path,
            page_index=0,
            output_dir=temp_dir,
            resolution=RENDER_DPI,
            logger=self.logger,
        )
        if not image_path:
            return {
                'pdf_hash': pdf_hash,
                'coa_url': None,
                'qr_data': None,
                'decoder': '',
                'strategy': '',
                'error': 'Failed to extract page image',
                'scanned_at': datetime.now().isoformat(),
            }

        # Determine the decoder function.
        def _decode(img_path: str) -> Dict:
            if self.qrustie_path:
                return decode_qr_with_qrustie(
                    img_path, self.qrustie_path,
                )
            elif self.use_python_fallback:
                return decode_qr_with_python(img_path)
            else:
                return {
                    'found': False, 'data': [],
                    'decoder': '', 'strategy': '',
                    'error': 'No decoder available',
                }

        decode_result = {'found': False, 'data': [], 'decoder': '',
                         'strategy': '', 'error': ''}
        region_paths = []

        try:
            # ── Phase 1: Full page image ──────────────────────────
            decode_result = _decode(image_path)

            # ── Phase 2: Cropped QR regions (fallback) ────────────
            # If full-page decode failed and OpenCV is available,
            # try finding and cropping the QR region(s) specifically.
            if not decode_result.get('found') and OPENCV_AVAILABLE:
                region_paths = extract_qr_regions(
                    image_path, output_dir=temp_dir,
                )
                for region_path in region_paths:
                    region_result = _decode(region_path)
                    if region_result.get('found'):
                        # Annotate with the cropping strategy.
                        region_result['strategy'] = (
                            'cv2_crop+'
                            + region_result.get('strategy', '')
                        )
                        decode_result = region_result
                        break

        finally:
            # Clean up temporary images.
            for cleanup_path in [image_path] + region_paths:
                try:
                    if cleanup_path and os.path.exists(cleanup_path):
                        os.remove(cleanup_path)
                except OSError:
                    pass

        # Extract URL from QR data.
        qr_data = None
        coa_url = None
        if decode_result.get('found') and decode_result.get('data'):
            qr_data = decode_result['data'][0]
            if is_url(qr_data):
                coa_url = qr_data.strip()

        return {
            'pdf_hash': pdf_hash,
            'coa_url': coa_url,
            'qr_data': qr_data,
            'decoder': decode_result.get('decoder', ''),
            'strategy': decode_result.get('strategy', ''),
            'error': decode_result.get('error', ''),
            'scanned_at': datetime.now().isoformat(),
        }

    def scan_all(
            self,
            source: str = '',
            sample_size: Optional[int] = None,
        ) -> Dict:
        """Scan all discovered COA PDFs for QR codes.

        Skips PDFs that are already cached (idempotent).
        Cleans up temporary files after each scan.

        Args:
            source: Optional source subdirectory filter.
            sample_size: If set, randomly sample this many PDFs.

        Returns:
            Summary statistics dict.
        """
        # Discover PDFs.
        pdfs = self.discover_pdfs(source)
        if pdfs.empty:
            self.logger.info('No PDFs found.')
            return self._summary(0, 0, 0, 0, 0)

        # Optionally sample.
        if sample_size and sample_size < len(pdfs):
            pdfs = pdfs.sample(n=sample_size, random_state=42)
            self.logger.info(f'Sampled {len(pdfs):,} PDFs')

        # Apply max_scans limit.
        if self.max_scans:
            pdfs = pdfs.head(self.max_scans)

        total = len(pdfs)
        scanned_count = 0
        skipped_count = 0
        found_count = 0
        error_count = 0

        self.logger.info(f'Starting QR scan of {total:,} COAs...')

        # Create a persistent temp directory for this run.
        with tempfile.TemporaryDirectory(prefix='qrustie_run_') as temp_dir:
            for i, (_, row) in enumerate(pdfs.iterrows()):
                pdf_hash = row['pdf_hash']
                file_path = row['file_path']
                basename = os.path.basename(file_path)

                # Check cache — skip if already scanned.
                if self.cache.get(pdf_hash):
                    skipped_count += 1
                    if (i + 1) % 500 == 0:
                        self.logger.info(
                            f'[{i + 1}/{total}] Skipped (cached): '
                            f'{skipped_count:,}'
                        )
                    continue

                # Scan this PDF.
                self.logger.info(
                    f'[{i + 1}/{total}] Scanning: {basename}'
                )
                result = self.scan_single_pdf(
                    pdf_hash, file_path, temp_dir,
                )

                # Cache the result (even if no QR found, to avoid re-scanning).
                self.cache.set(pdf_hash, result)
                scanned_count += 1

                if result.get('coa_url'):
                    found_count += 1
                    self.logger.info(
                        f'  [OK] URL: {result["coa_url"]}'
                    )
                elif result.get('qr_data'):
                    self.logger.info(
                        f'  [~] QR data (not URL): '
                        f'{result["qr_data"][:80]}...'
                    )
                elif result.get('error'):
                    error_count += 1
                    self.logger.debug(
                        f'  [X] Error: {result["error"]}'
                    )

                # Progress update every 100 scans.
                if scanned_count % 100 == 0:
                    self.logger.info(
                        f'  Progress: {scanned_count:,} scanned, '
                        f'{found_count:,} URLs found, '
                        f'{error_count:,} errors'
                    )

        return self._summary(
            total, scanned_count, skipped_count,
            found_count, error_count,
        )

    def _summary(
            self, total, scanned, skipped, found, errors,
        ) -> Dict:
        """Build a summary statistics dict."""
        return {
            'state': self.state,
            'total_pdfs': total,
            'scanned': scanned,
            'skipped_cached': skipped,
            'urls_found': found,
            'errors': errors,
            'cache_total': len(self.cache.to_df()),
        }

    def get_cache_stats(self) -> Dict:
        """Get statistics about the current cache state.

        Returns:
            Dict with cache statistics including URL counts,
            decoder distribution, and coverage.
        """
        df = self.cache.to_df()
        if df.empty:
            return {
                'state': self.state,
                'total_entries': 0,
                'urls_found': 0,
                'qr_found_no_url': 0,
                'no_qr_found': 0,
                'errors': 0,
            }

        has_url = df['coa_url'].notna() & (df['coa_url'] != '')
        has_qr = df['qr_data'].notna() & (df['qr_data'] != '')
        has_error = df['error'].notna() & (df['error'] != '')

        stats = {
            'state': self.state,
            'total_entries': len(df),
            'urls_found': int(has_url.sum()),
            'qr_found_no_url': int((has_qr & ~has_url).sum()),
            'no_qr_found': int((~has_qr & ~has_error).sum()),
            'errors': int(has_error.sum()),
        }

        # Decoder distribution.
        if 'decoder' in df.columns:
            decoders = df.loc[has_qr, 'decoder'].value_counts().to_dict()
            stats['decoders'] = decoders

        # Strategy distribution.
        if 'strategy' in df.columns:
            strategies = df.loc[has_qr, 'strategy'].value_counts().to_dict()
            stats['strategies'] = strategies

        return stats

    def export_urls(self, output_path: Optional[str] = None) -> str:
        """Export found URLs to CSV for merging.

        Args:
            output_path: Custom output path. Defaults to
                         {data_dir}/{state_name}/results/datasets/qr-urls-{state}.csv

        Returns:
            Path to the exported CSV file.
        """
        df = self.cache.to_df()
        if df.empty:
            self.logger.info('No cache data to export.')
            return ''

        # Filter to entries with URLs.
        has_url = df['coa_url'].notna() & (df['coa_url'] != '')
        url_df = df.loc[has_url, ['pdf_hash', 'coa_url', 'scanned_at']].copy()
        url_df = url_df.drop_duplicates(subset=['pdf_hash'])

        if url_df.empty:
            self.logger.info('No URLs found to export.')
            return ''

        # Sanitize URLs: strip whitespace, remove embedded newlines,
        # tabs, and carriage returns that can break CSV serialization.
        # Some QR payloads contain these characters.
        url_df['coa_url'] = (
            url_df['coa_url']
            .str.strip()
            .str.replace(r'[\r\n\t]', '', regex=True)
        )

        # Drop any rows where sanitization emptied the URL.
        url_df = url_df.loc[url_df['coa_url'] != '']

        # Determine output path.
        if not output_path:
            datasets_dir = (
                self.data_dir / self.state_name / 'results' / 'datasets'
            )
            datasets_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(
                datasets_dir / f'qr-urls-{self.state}.csv'
            )

        url_df.to_csv(output_path, index=False, escapechar='\\')
        self.logger.info(
            f'Exported {len(url_df):,} URLs to {output_path}'
        )
        return output_path


# ╔══════════════════════════════════════════════════════════════════╗
# ║ CLI Entry Point                                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

def main():
    """Command-line interface for QR code scanning."""
    parser = argparse.ArgumentParser(
        description='Cannlytics COA QR Code Scanner',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan NY COAs for QR codes
  python scan_qrcodes.py --state ny

  # Scan FL COAs from a specific source
  python scan_qrcodes.py --state fl --source flowery

  # Test with 50 scans
  python scan_qrcodes.py --state ny --max-scans 50

  # Show cache statistics
  python scan_qrcodes.py --state ny --cache-stats

  # Export found URLs to CSV
  python scan_qrcodes.py --state ny --export

  # Dry run
  python scan_qrcodes.py --state ny --dry-run
        """,
    )

    # Required arguments.
    parser.add_argument(
        '--state', '-s', type=str, required=True,
        help='State code (e.g., ny, ca, fl)',
    )

    # Scope options.
    parser.add_argument(
        '--source', type=str, default='',
        help='Filter to specific source subdirectory',
    )
    parser.add_argument(
        '--max-scans', '-n', type=int, default=None,
        help='Maximum number of PDFs to scan',
    )
    parser.add_argument(
        '--sample-size', type=int, default=None,
        help='Random sample size (for dev/testing)',
    )

    # Path options.
    parser.add_argument(
        '--data-dir', type=str, default=None,
        help=f'Data directory (default: {DEFAULT_DATA_DIR})',
    )
    parser.add_argument(
        '--cache-dir', type=str, default=None,
        help=f'Cache directory (default: {DEFAULT_CACHE_DIR})',
    )
    parser.add_argument(
        '--qrustie-path', type=str, default=None,
        help='Path to qrustie binary',
    )

    # Behavior options.
    parser.add_argument(
        '--no-rust', action='store_true',
        help='Skip Rust decoder, use Python fallback only',
    )
    parser.add_argument(
        '--no-fallback', action='store_true',
        help='Disable Python fallback (Rust only)',
    )

    # Utility commands.
    parser.add_argument(
        '--cache-stats', action='store_true',
        help='Show cache statistics and exit',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Show what would be scanned without scanning',
    )
    parser.add_argument(
        '--export', action='store_true',
        help='Export found URLs to CSV and exit',
    )
    parser.add_argument(
        '--clear-cache', action='store_true',
        help='Delete cache for this state and exit',
    )

    args = parser.parse_args()

    # Determine qrustie path.
    qrustie_override = None if args.no_rust else args.qrustie_path

    # Initialize scanner.
    scanner = QRScanner(
        state=args.state,
        data_dir=Path(args.data_dir) if args.data_dir else None,
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        qrustie_path=qrustie_override,
        use_python_fallback=not args.no_fallback,
        max_scans=args.max_scans,
    )

    # Handle utility commands.
    if args.cache_stats:
        stats = scanner.get_cache_stats()
        print('\n=== QR Scan Cache Statistics ===')
        for k, v in stats.items():
            if isinstance(v, dict):
                print(f'  {k}:')
                for dk, dv in v.items():
                    print(f'    {dk}: {dv}')
            elif isinstance(v, int):
                print(f'  {k}: {v:,}')
            else:
                print(f'  {k}: {v}')
        return

    if args.export:
        output = scanner.export_urls()
        if output:
            print(f'Exported to: {output}')
        else:
            print('No URLs to export.')
        return

    if args.clear_cache:
        cache_file = scanner.cache_dir / f'qr-scan-{args.state}.jsonl'
        if cache_file.exists():
            os.remove(str(cache_file))
            print(f'Cleared cache: {cache_file}')
        else:
            print('No cache file found.')
        return

    if args.dry_run:
        pdfs = scanner.discover_pdfs(args.source)
        print(f'\nDry Run: {len(pdfs):,} PDFs discovered')
        if not pdfs.empty:
            print(f'  Directory: {scanner.pdf_dir}')
            print(f'  Decoder: {"qrustie" if scanner.qrustie_path else "python fallback"}')
            if args.sample_size:
                print(f'  Sample size: {args.sample_size}')
            if args.max_scans:
                print(f'  Max scans: {args.max_scans}')

            # Show cache coverage.
            stats = scanner.get_cache_stats()
            cached = stats.get('total_entries', 0)
            print(f'  Already cached: {cached:,}')
            print(f'  Remaining: ~{max(0, len(pdfs) - cached):,}')
            print(f'  URLs found so far: {stats.get("urls_found", 0):,}')
        return

    # Run scanning.
    print(f'\n--- Cannlytics COA QR Scanner ---')
    print(f'   State: {args.state.upper()}')
    decoder = 'qrustie (Rust)' if scanner.qrustie_path else 'Python fallback'
    print(f'   Decoder: {decoder}')
    if args.max_scans:
        print(f'   Max scans: {args.max_scans:,}')
    print()

    summary = scanner.scan_all(
        source=args.source,
        sample_size=args.sample_size,
    )

    # Print final summary.
    print('\n' + '=' * 60)
    print('QR SCAN SUMMARY')
    print('=' * 60)
    print(f'  State: {summary["state"].upper()}')
    print(f'  Total PDFs: {summary["total_pdfs"]:,}')
    print(f'  Scanned: {summary["scanned"]:,}')
    print(f'  Skipped (cached): {summary["skipped_cached"]:,}')
    print(f'  URLs found: {summary["urls_found"]:,}')
    print(f'  Errors: {summary["errors"]:,}')
    print(f'  Total in cache: {summary["cache_total"]:,}')
    print('=' * 60)


if __name__ == '__main__':
    main()