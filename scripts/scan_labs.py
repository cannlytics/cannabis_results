"""
Scan COA Corpus — Lab Identification Census
=============================================
Quickly scans a directory of COA PDFs to identify which lab/LIMS
produced each one, without actually parsing. Useful for:
  - Estimating algorithm coverage before a full parse run
  - Finding Confident Cannabis COAs in a corpus
  - Understanding the lab distribution in a state's data

Usage:
    cd cannabis_results/scripts
    python scan_lab_census.py --state ca --max-scan 500
    python scan_lab_census.py --state ca --max-scan 5000 --save
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import pdfplumber

# ── Configuration ─────────────────────────────────────────────────

STATE_NAMES = {
    'ak': 'alaska', 'az': 'arizona', 'ca': 'california', 'co': 'colorado',
    'ct': 'connecticut', 'fl': 'florida', 'hi': 'hawaii', 'ma': 'massachusetts',
    'md': 'maryland', 'mi': 'michigan', 'mo': 'missouri', 'ms': 'mississippi',
    'nj': 'new-jersey', 'nv': 'nevada', 'ny': 'new-york', 'oh': 'ohio',
    'or': 'oregon', 'ri': 'rhode-island', 'ut': 'utah', 'vt': 'vermont',
    'wa': 'washington',
}

DEFAULT_DATA_DIR = Path(os.environ.get('CANNLYTICS_DATA_DIR', 'D:/data'))

# Lab fingerprints — add more as needed.
LAB_FINGERPRINTS = {
    'confidentcannabis': {
        'urls': ['confidentcannabis.com', 'confidentlims.com'],
        'text': ['Confident Cannabis', 'Confident LIMS'],
    },
    'tagleaf': {
        'urls': ['lims.tagleaf.com', 'tagleaf.com'],
        'text': ['TagLeaf', 'lims.tagleaf'],
    },
    'kaycha': {
        'urls': ['kaychalabs.com', 'yourcoa.com'],
        'text': ['Kaycha Labs', 'Kaycha Laboratory'],
    },
    'sclabs': {
        'urls': ['client.sclabs.com', 'sclabs.com'],
        'text': ['SC Labs', 'SC Laboratories'],
    },
    'anresco': {
        'urls': ['anresco.com'],
        'text': ['Anresco'],
    },
    'greenleaflab': {
        'urls': ['greenleaflab.org'],
        'text': ['Green Leaf Lab'],
    },
    'sonoma': {
        'urls': ['sonomalabworks.com'],
        'text': ['Sonoma Lab Works'],
    },
    'terplife': {
        'urls': ['terplifelabs.com'],
        'text': ['TerpLife Labs', 'TerpLife'],
    },
    'mcrlabs': {
        'urls': ['mcrlabs.com', 'reports.mcrlabs.com'],
        'text': ['MCR Labs'],
    },
}


def identify_lab_fast(pdf_path: str) -> str:
    """Fast lab identification from page 1+2 text only."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return 'empty_pdf'
            texts = []
            for i in range(min(2, len(pdf.pages))):
                t = pdf.pages[i].extract_text()
                if t:
                    texts.append(t)
            if not texts:
                return 'image_only'
            combined = '\n'.join(texts)
            combined_lower = combined.lower()

            # Check URLs first (highest confidence).
            for lab_key, fp in LAB_FINGERPRINTS.items():
                for url in fp.get('urls', []):
                    if url in combined:
                        return lab_key

            # Check text patterns.
            for lab_key, fp in LAB_FINGERPRINTS.items():
                for pattern in fp.get('text', []):
                    if pattern.lower() in combined_lower:
                        return lab_key

            return 'unrecognized'
    except Exception as e:
        return f'error:{type(e).__name__}'


def main():
    parser = argparse.ArgumentParser(
        description='Scan COA corpus and identify lab/LIMS distribution',
    )
    parser.add_argument('--state', '-s', type=str, required=True)
    parser.add_argument('--max-scan', '-n', type=int, default=500)
    parser.add_argument('--data-dir', type=str, default=None)
    parser.add_argument('--save', action='store_true',
                        help='Save per-file results to .cache/lab-census-{state}.jsonl')
    parser.add_argument('--random', action='store_true',
                        help='Random sample instead of first N')
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else DEFAULT_DATA_DIR
    state_name = STATE_NAMES.get(args.state.lower(), args.state.lower())
    pdf_dir = data_dir / state_name / 'results' / 'pdfs'

    if not pdf_dir.exists():
        print(f'ERROR: PDF directory not found: {pdf_dir}')
        sys.exit(1)

    # Discover PDFs.
    pdf_files = sorted(
        str(p) for p in pdf_dir.rglob('*.pdf')
        if p.stat().st_size >= 21000
    )
    total_in_corpus = len(pdf_files)

    if args.random:
        import random
        random.seed(42)
        sample = random.sample(pdf_files, min(args.max_scan, len(pdf_files)))
    else:
        sample = pdf_files[:args.max_scan]

    print(f'\n🔬 Lab Census — {args.state.upper()}')
    print(f'   Corpus: {total_in_corpus:,} PDFs in {pdf_dir}')
    print(f'   Scanning: {len(sample):,} PDFs')
    print(f'   Mode: {"random sample" if args.random else "first N by hash"}')
    print()

    counts = Counter()
    file_results = []
    start = time.time()

    for i, pdf_path in enumerate(sample):
        lab = identify_lab_fast(pdf_path)
        counts[lab] += 1
        file_results.append({
            'file': os.path.basename(pdf_path),
            'lab': lab,
        })

        if (i + 1) % 100 == 0:
            elapsed = time.time() - start
            rate = (i + 1) / elapsed
            print(f'  [{i + 1:,}/{len(sample):,}] {rate:.0f} PDFs/sec  |  '
                  f'{dict(counts.most_common(3))}')

    elapsed = time.time() - start

    # Results.
    print()
    print('=' * 60)
    print(f'📋 LAB CENSUS RESULTS — {args.state.upper()}')
    print('=' * 60)
    print(f'  Scanned: {len(sample):,} / {total_in_corpus:,} PDFs')
    print(f'  Time: {elapsed:.1f}s ({len(sample)/elapsed:.0f} PDFs/sec)')
    print()

    print('  Lab Distribution:')
    for lab, count in counts.most_common():
        pct = 100 * count / len(sample)
        estimated_total = int(pct / 100 * total_in_corpus)
        bar = '█' * int(pct / 2)
        print(f'    {lab:25s}  {count:5,}  ({pct:5.1f}%)  ~{estimated_total:,} est.  {bar}')

    # Algorithm coverage.
    algo_labs = {'confidentcannabis', 'tagleaf'}
    algo_count = sum(counts[l] for l in algo_labs)
    algo_pct = 100 * algo_count / len(sample) if sample else 0
    print()
    print(f'  Algorithm coverage (CC + TagLeaf): {algo_count}/{len(sample)} ({algo_pct:.1f}%)')
    print(f'  Estimated in full corpus: ~{int(algo_pct/100*total_in_corpus):,} parseable for free')

    if args.save:
        cache_dir = Path('.cache')
        cache_dir.mkdir(exist_ok=True)
        out_path = cache_dir / f'lab-census-{args.state.lower()}.jsonl'
        with open(out_path, 'w') as f:
            for fr in file_results:
                f.write(json.dumps(fr) + '\n')
        print(f'\n  Saved per-file results to: {out_path}')

    print('=' * 60)


if __name__ == '__main__':
    main()