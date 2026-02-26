"""
Parse COAs | AI-Powered Certificate of Analysis Parsing Engine
Copyright (c) 2024-2026 Cannlytics

Authors:
    Keegan Skeate <https://github.com/keeganskeate>
Created: 10/7/2024
Updated: 2/25/2026
License: MIT License <https://github.com/cannlytics/cannlytics/blob/main/LICENSE>

Description:
    Production-grade AI-powered COA parsing engine. This is the command
    station that:

    1. Discovers COA PDFs that need parsing across all states/sources
    2. Determines optimal parsing strategy per COA (PDF, image, text)
    3. Delegates to the best AI provider based on cost/quality/availability
    4. Extracts metadata and per-analysis results using multi-prompt approach
    5. Caches all parsed data with model/provider attribution
    6. Tracks costs, performance, and quality metrics
    7. Supports parallel execution and incremental resumption

    Parsing Strategy:
        - Simple COAs (1-2 pages, cannabinoids/terpenes only):
          Text extraction → single prompt
        - Standard COAs (2-5 pages, multiple analyses):
          PDF/image pages → metadata prompt + per-analysis prompts
        - Complex COAs (5+ pages, comprehensive testing):
          Targeted page extraction by keyword → per-analysis prompts

    AI Provider Priority:
        1. Anthropic Claude (highest quality, production-grade)
        2. OpenAI (reliable, good structured output)
        3. Google Gemini (free tier available)
        4. xAI Grok (low cost, good for bulk)

    Pipeline Position:
        This is the PARSING script in the results pipeline:
        get_results_{state}.py → parse_coas.py → process_results.py

Usage:
    # Parse COAs for a specific state
    python parse_coas.py --state ny --max-parses 100

    # Parse with a specific provider
    python parse_coas.py --state ca --provider anthropic

    # Parse all states, using free tokens first
    python parse_coas.py --all --budget 5.00

    # View cache statistics
    python parse_coas.py --state ny --cache-stats

    # Dry run (show what would be parsed)
    python parse_coas.py --state ny --dry-run
"""
# Standard imports:
import argparse
import base64
import gc
import io
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union

# External imports:
from dotenv import dotenv_values
import pandas as pd
import pdfplumber
from PIL import Image

# Internal imports:
from cannlytics.data.cache import Bogart
from cannlytics.logs import initialize_logs
from cannlytics.utils.utils import hash_file

# Suppress pdfminer noise.
import logging
import platform
logging.getLogger('pdfminer').setLevel(logging.ERROR)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Windows Long Path Support                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

IS_WINDOWS = platform.system() == 'Windows'
WIN_MAX_PATH = 259  # Effective limit (260 minus null terminator).


def _long_path(path: str) -> str:
    """Prepend the Windows extended-length path prefix if needed.

    Windows has a default MAX_PATH of 260 characters. Cannabis COA
    files from PRRs often have deeply nested directories and verbose
    filenames that exceed this limit. The ``\\\\?\\`` prefix enables
    paths up to 32,767 characters.

    On non-Windows systems this is a no-op.
    """
    if not IS_WINDOWS:
        return path
    path = str(path)
    if path.startswith('\\\\?\\'):
        return path
    # Convert forward slashes and make absolute.
    abs_path = os.path.abspath(path)
    if len(abs_path) > WIN_MAX_PATH:
        return f'\\\\?\\{abs_path}'
    return abs_path


def _safe_file_size(path: str, min_size: int = 21_000) -> bool:
    """Check if a file meets the minimum size, handling long paths."""
    try:
        return os.path.getsize(_long_path(path)) >= min_size
    except (OSError, FileNotFoundError):
        return False


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Configuration                                                    ║
# ╚══════════════════════════════════════════════════════════════════╝

# Import schema and config.
try:
    from config.results_schema import (
        ANALYSIS_CONFIGS,
        LabTestMetadata,
        LabAnalysis,
        LabTestResult,
        normalize_product_type,
    )
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config.results_schema import (
        ANALYSIS_CONFIGS,
        LabTestMetadata,
        LabAnalysis,
        LabTestResult,
        normalize_product_type,
    )


# ── AI Provider Pricing (per 1M tokens) ───────────────────────────

AI_PROVIDERS = {
    'anthropic': {
        'name': 'Anthropic Claude',
        'models': {
            'claude-sonnet-4-5-20250929': {
                'input': 3.00, 'output': 15.00,
                'supports_pdf': True, 'supports_images': True,
                'supports_structured_output': False,
                'max_output_tokens': 64_000,
            },
            'claude-haiku-4-5-20251001': {
                'input': 1.00, 'output': 5.00,
                'supports_pdf': True, 'supports_images': True,
                'supports_structured_output': False,
                'max_output_tokens': 64_000,
            },
        },
        'default_model': 'claude-haiku-4-5-20251001',
        'env_key': 'ANTHROPIC_API_KEY',
        'priority': 1,
        'free_tier': False,
    },
    'openai': {
        'name': 'OpenAI',
        'models': {
            'gpt-5-mini': {
                'input': 0.25, 'output': 2.00,
                'supports_pdf': True, 'supports_images': True,
                'supports_structured_output': True,
                'max_output_tokens': 16_384,
                'image_cost': 0.003825,
            },
            'gpt-5-nano': {
                'input': 0.05, 'output': 0.40,
                'supports_pdf': True, 'supports_images': True,
                'supports_structured_output': True,
                'max_output_tokens': 16_384,
                'image_cost': 0.001275,
            },
            'gpt-5': {
                'input': 1.25, 'output': 10.00,
                'supports_pdf': True, 'supports_images': True,
                'supports_structured_output': True,
                'max_output_tokens': 32_768,
                'image_cost': 0.003825,
            },
        },
        'default_model': 'gpt-5-mini',
        'env_key': 'OPENAI_API_KEY',
        'priority': 2,
        'free_tier': False,
    },
    'gemini': {
        'name': 'Google Gemini',
        'models': {
            'gemini-2.5-flash': {
                'input': 0.30, 'output': 2.50,
                'supports_pdf': True, 'supports_images': True,
                'supports_structured_output': True,
                'max_output_tokens': 65_536,
                'free_tier_input': 0.0, 'free_tier_output': 0.0,
            },
            'gemini-2.5-pro': {
                'input': 1.25, 'output': 10.00,
                'supports_pdf': True, 'supports_images': True,
                'supports_structured_output': True,
                'max_output_tokens': 65_536,
                'free_tier_input': 0.0, 'free_tier_output': 0.0,
            },
        },
        'default_model': 'gemini-2.5-flash',
        'env_key': 'GOOGLE_API_KEY',
        'priority': 3,
        'free_tier': True,
    },
    'xai': {
        'name': 'xAI Grok',
        'models': {
            'grok-4-1-fast-non-reasoning': {
                'input': 0.20, 'output': 0.50,
                'supports_pdf': False, 'supports_images': True,
                'supports_structured_output': True,
                'max_output_tokens': 16_384,
            },
            'grok-3-mini': {
                'input': 0.30, 'output': 0.50,
                'supports_pdf': False, 'supports_images': True,
                'supports_structured_output': True,
                'max_output_tokens': 16_384,
            },
        },
        'default_model': 'grok-4-1-fast-non-reasoning',
        'env_key': 'XAI_API_KEY',
        'priority': 4,
        'free_tier': False,
    },
}

# Default paths (can be overridden by environment or CLI).
DEFAULT_DATA_DIR = Path(os.environ.get('CANNLYTICS_DATA_DIR', 'D:/data'))
DEFAULT_CACHE_DIR = Path(os.environ.get('CANNLYTICS_CACHE_DIR', 'D:/data/.cache'))
DEFAULT_LOG_DIR = Path(os.environ.get('CANNLYTICS_LOG_DIR', 'D:/data/.logs'))

# State name mapping.
STATE_NAMES = {
    'ak': 'alaska', 'az': 'arizona', 'ca': 'california', 'co': 'colorado',
    'ct': 'connecticut', 'fl': 'florida', 'hi': 'hawaii', 'ma': 'massachusetts',
    'md': 'maryland', 'mi': 'michigan', 'mo': 'missouri', 'ms': 'mississippi',
    'nj': 'new-jersey', 'nv': 'nevada', 'ny': 'new-york', 'oh': 'ohio',
    'or': 'oregon', 'ri': 'rhode-island', 'ut': 'utah', 'vt': 'vermont',
    'wa': 'washington',
}


# ╔══════════════════════════════════════════════════════════════════╗
# ║ AI Provider Clients                                              ║
# ╚══════════════════════════════════════════════════════════════════╝

class CostTracker:
    """Track cumulative costs across all providers and models."""

    def __init__(self):
        self.records: List[Dict] = []
        self.total_cost: float = 0.0

    def record(
            self,
            provider: str,
            model: str,
            input_tokens: int,
            output_tokens: int,
            cost: float,
            analysis: str = '',
            pdf_hash: str = '',
        ):
        """Record a single API call's cost."""
        entry = {
            'timestamp': datetime.now().isoformat(),
            'provider': provider,
            'model': model,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'cost': cost,
            'analysis': analysis,
            'pdf_hash': pdf_hash,
        }
        self.records.append(entry)
        self.total_cost += cost

    def summary(self) -> Dict:
        """Get a summary of all costs."""
        by_provider = {}
        by_model = {}
        for r in self.records:
            prov = r['provider']
            mod = r['model']
            by_provider[prov] = by_provider.get(prov, 0.0) + r['cost']
            by_model[mod] = by_model.get(mod, 0.0) + r['cost']
        return {
            'total_cost': round(self.total_cost, 6),
            'total_calls': len(self.records),
            'by_provider': {k: round(v, 6) for k, v in by_provider.items()},
            'by_model': {k: round(v, 6) for k, v in by_model.items()},
        }

    def __str__(self) -> str:
        s = self.summary()
        return (
            f"Total: ${s['total_cost']:.4f} "
            f"({s['total_calls']} calls) | "
            f"By provider: {s['by_provider']}"
        )


class AIClient:
    """Unified AI client supporting multiple providers."""

    def __init__(
            self,
            provider: str = 'openai',
            model: Optional[str] = None,
            config: Optional[Dict] = None,
            logger: Optional[logging.Logger] = None,
        ):
        self.provider = provider
        self.provider_config = AI_PROVIDERS[provider]
        self.model = model or self.provider_config['default_model']
        self.model_config = self.provider_config['models'][self.model]
        self.config = config or dotenv_values('../.env')
        self.logger = logger or logging.getLogger(__name__)
        self.client = None
        self._exhausted = False
        self._init_client()

    def _init_client(self):
        """Initialize the provider-specific client."""
        api_key = self.config.get(
            self.provider_config['env_key'],
            os.environ.get(self.provider_config['env_key'], ''),
        )
        if not api_key:
            self.logger.warning(
                f'{self.provider}: No API key found for '
                f'{self.provider_config["env_key"]}'
            )
            self._exhausted = True
            return

        if self.provider == 'openai':
            from openai import OpenAI
            self.client = OpenAI(api_key=api_key)

        elif self.provider == 'anthropic':
            from anthropic import Anthropic
            self.client = Anthropic(api_key=api_key)

        elif self.provider == 'gemini':
            from google import genai
            self.client = genai.Client(api_key=api_key)

        elif self.provider == 'xai':
            from openai import OpenAI
            self.client = OpenAI(
                api_key=api_key,
                base_url='https://api.x.ai/v1',
            )

    @property
    def is_available(self) -> bool:
        return self.client is not None and not self._exhausted

    @property
    def supports_pdf(self) -> bool:
        return self.model_config.get('supports_pdf', False)

    @property
    def supports_structured_output(self) -> bool:
        return self.model_config.get('supports_structured_output', False)

    def calculate_cost(
            self,
            input_tokens: int,
            output_tokens: int,
            num_images: int = 0,
        ) -> float:
        """Calculate the cost of a single API call."""
        in_rate = self.model_config['input'] / 1_000_000
        out_rate = self.model_config['output'] / 1_000_000
        token_cost = input_tokens * in_rate + output_tokens * out_rate
        image_cost = num_images * self.model_config.get('image_cost', 0.0)
        return token_cost + image_cost

    def parse_metadata(
            self,
            pdf_path: str,
            page_images: Optional[List[str]] = None,
            page_text: Optional[str] = None,
        ) -> Tuple[Optional[Dict], float, int, int]:
        """Parse metadata from a COA.

        Returns: (parsed_data, cost, input_tokens, output_tokens)
        """
        system_prompt = METADATA_SYSTEM_PROMPT
        user_prompt = METADATA_USER_PROMPT

        return self._call_ai(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            pdf_path=pdf_path,
            page_images=page_images,
            page_text=page_text,
            response_schema=LabTestMetadata,
            mode='metadata',
        )

    def parse_analysis(
            self,
            analysis_name: str,
            analyte_keys: List[str],
            pdf_path: str,
            page_images: Optional[List[str]] = None,
            page_text: Optional[str] = None,
        ) -> Tuple[Optional[Dict], float, int, int]:
        """Parse a specific analysis from a COA.

        Returns: (parsed_data, cost, input_tokens, output_tokens)
        """
        system_prompt = ANALYSIS_SYSTEM_PROMPT
        user_prompt = ANALYSIS_USER_PROMPT % (
            analysis_name,
            '\n'.join(analyte_keys),
        )

        return self._call_ai(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            pdf_path=pdf_path,
            page_images=page_images,
            page_text=page_text,
            response_schema=LabAnalysis,
            mode='analysis',
        )

    def _call_ai(
            self,
            system_prompt: str,
            user_prompt: str,
            pdf_path: Optional[str] = None,
            page_images: Optional[List[str]] = None,
            page_text: Optional[str] = None,
            response_schema: Any = None,
            mode: str = 'metadata',
        ) -> Tuple[Optional[Dict], float, int, int]:
        """Unified AI call across all providers.

        Strategy hierarchy:
        1. PDF direct (if provider supports it and we have a PDF)
        2. Images (if we have page images)
        3. Text (fallback)

        Returns: (parsed_data, cost, input_tokens, output_tokens)
        """
        if not self.is_available:
            return None, 0.0, 0, 0

        try:
            if self.provider == 'openai' or self.provider == 'xai':
                return self._call_openai(
                    system_prompt, user_prompt, pdf_path,
                    page_images, page_text, response_schema, mode,
                )
            elif self.provider == 'anthropic':
                return self._call_anthropic(
                    system_prompt, user_prompt, pdf_path,
                    page_images, page_text, mode,
                )
            elif self.provider == 'gemini':
                return self._call_gemini(
                    system_prompt, user_prompt, pdf_path,
                    page_images, page_text, response_schema, mode,
                )
        except Exception as e:
            error_str = str(e).lower()
            if '429' in error_str or 'rate' in error_str or 'quota' in error_str:
                self.logger.warning(f'{self.provider}: Rate limited / exhausted.')
                self._exhausted = True
            else:
                self.logger.error(f'{self.provider} error: {e}')
            return None, 0.0, 0, 0

    # ── OpenAI / xAI Implementation ─────────────────────────────

    def _call_openai(
            self,
            system_prompt, user_prompt, pdf_path,
            page_images, page_text, response_schema, mode,
        ) -> Tuple[Optional[Dict], float, int, int]:
        """Call OpenAI or xAI (OpenAI-compatible API).

        Two API paths:
          - PDF available (OpenAI only): Uses the Responses API
            (`client.responses.create`) with `input_file`, which
            natively extracts text + page images from PDFs.
          - Images/text: Uses the Chat Completions API
            (`client.beta.chat.completions.parse`) with `image_url`.

        The Responses API is preferred for PDFs because it preserves
        document structure, layout, and visual elements. xAI does
        not support the Responses API, so it always uses images.
        """

        # ── Path A: PDF via Responses API (OpenAI only) ─────────
        if pdf_path and self.provider == 'openai':
            return self._call_openai_responses_api(
                system_prompt, user_prompt, pdf_path,
                response_schema, mode,
            )

        # ── Path B: Images/text via Chat Completions ────────────
        user_content = [{'type': 'text', 'text': user_prompt}]

        # Safety net: If pdf_path but no images (xAI path),
        # convert PDF to images.
        _temp_dir = None
        if pdf_path and not page_images:
            import tempfile as _tf
            _temp_dir = _tf.mkdtemp()
            page_images = get_pdf_pages_as_images(
                pdf_path,
                page_indexes=list(range(5)),
                output_dir=_temp_dir,
            )
            if not page_images:
                page_text = extract_pdf_text(pdf_path)

        if page_images:
            for img_path in page_images:
                b64 = _encode_image(img_path)
                user_content.append({
                    'type': 'image_url',
                    'image_url': {'url': f'data:image/jpeg;base64,{b64}', 'detail': 'high'},
                })
        elif page_text:
            user_content[0]['text'] = f'{user_prompt}\n\nCOA Text:\n{page_text}'

        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_content},
        ]

        kwargs = {
            'model': self.model,
            'messages': messages,
        }
        if self.supports_structured_output and response_schema:
            completion = self.client.beta.chat.completions.parse(
                **kwargs,
                response_format=response_schema,
                reasoning_effort='high',
            )
            msg = completion.choices[0].message
            if getattr(msg, 'refusal', None):
                self.logger.warning(f'Model refused: {msg.refusal}')
                return None, 0.0, 0, 0
            try:
                parsed = msg.parsed.model_dump()
            except Exception:
                return None, 0.0, 0, 0
        else:
            kwargs['max_tokens'] = self.model_config.get('max_output_tokens', 16_384)
            completion = self.client.chat.completions.create(**kwargs)
            content = completion.choices[0].message.content
            parsed = _extract_json(content)
            if parsed is None:
                return None, 0.0, 0, 0

        usage = getattr(completion, 'usage', None)
        in_tok = getattr(usage, 'prompt_tokens', 0) if usage else 0
        out_tok = getattr(usage, 'completion_tokens', 0) if usage else 0
        num_images = len(page_images) if page_images else 0
        cost = self.calculate_cost(in_tok, out_tok, num_images)

        if _temp_dir:
            import shutil
            shutil.rmtree(_temp_dir, ignore_errors=True)

        return parsed, cost, in_tok, out_tok

    def _call_openai_responses_api(
            self,
            system_prompt: str,
            user_prompt: str,
            pdf_path: str,
            response_schema: Any,
            mode: str,
        ) -> Tuple[Optional[Dict], float, int, int]:
        """Call OpenAI's Responses API with native PDF input.

        The Responses API (`client.responses.create`) accepts PDFs
        via `input_file`. For vision-capable models, it extracts
        both text and page images and sends both to the model.

        Structured output is achieved via `text.format` with a
        JSON schema derived from the Pydantic model.
        """
        # Read and base64-encode the PDF.
        with open(_long_path(pdf_path), 'rb') as f:
            pdf_b64 = base64.b64encode(f.read()).decode('utf-8')

        # Build input content (user message with PDF + prompt).
        pdf_filename = os.path.basename(pdf_path)
        input_content = [
            {
                'type': 'input_text',
                'text': user_prompt,
            },
            {
                'type': 'input_file',
                'filename': pdf_filename,
                'file_data': f'data:application/pdf;base64,{pdf_b64}',
            },
        ]

        # Build the API call kwargs.
        # The Responses API uses `instructions` for system-level prompting.
        kwargs = {
            'model': self.model,
            'instructions': system_prompt,
            'input': [{'role': 'user', 'content': input_content}],
        }

        # Add structured output via JSON schema if we have a Pydantic model.
        if response_schema and hasattr(response_schema, 'model_json_schema'):
            schema_name = response_schema.__name__.lower()
            schema = response_schema.model_json_schema()
            _make_schema_strict(schema)
            kwargs['text'] = {
                'format': {
                    'type': 'json_schema',
                    'name': schema_name,
                    'schema': schema,
                },
            }

        # Call the Responses API.
        response = self.client.responses.create(**kwargs)

        # Parse the response.
        output_text = response.output_text
        parsed = _extract_json(output_text)
        if parsed is None:
            self.logger.warning(
                f'OpenAI Responses API: failed to parse JSON '
                f'from response ({len(output_text)} chars).'
            )
            return None, 0.0, 0, 0

        # Extract usage (Responses API uses input_tokens/output_tokens).
        usage = getattr(response, 'usage', None)
        in_tok = getattr(usage, 'input_tokens', 0) if usage else 0
        out_tok = getattr(usage, 'output_tokens', 0) if usage else 0
        cost = self.calculate_cost(in_tok, out_tok)

        return parsed, cost, in_tok, out_tok

    # ── Anthropic Implementation ────────────────────────────────

    def _call_anthropic(
            self,
            system_prompt, user_prompt, pdf_path,
            page_images, page_text, mode,
        ) -> Tuple[Optional[Dict], float, int, int]:
        """Call Anthropic Claude with PDF or image support."""
        content = []

        # Strategy 1: PDF direct (Anthropic supports native PDF).
        if pdf_path and self.supports_pdf:
            with open(_long_path(pdf_path), 'rb') as f:
                pdf_b64 = base64.b64encode(f.read()).decode('utf-8')
            content.append({
                'type': 'document',
                'source': {
                    'type': 'base64',
                    'media_type': 'application/pdf',
                    'data': pdf_b64,
                },
            })

        # Strategy 2: Images.
        elif page_images:
            for img_path in page_images:
                b64 = _encode_image(img_path)
                content.append({
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': 'image/jpeg',
                        'data': b64,
                    },
                })

        # Strategy 3: Text only.
        elif page_text:
            user_prompt = f'{user_prompt}\n\nCOA Text:\n{page_text}'

        # Append the user prompt.
        content.append({'type': 'text', 'text': user_prompt})

        # Build the response format instruction.
        if mode == 'metadata':
            json_schema = _metadata_json_hint()
        else:
            json_schema = _analysis_json_hint()

        enhanced_system = (
            f'{system_prompt}\n\n'
            f'IMPORTANT: Respond ONLY with valid JSON matching this schema:\n'
            f'{json_schema}'
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.model_config.get('max_output_tokens', 16_384),
            system=enhanced_system,
            messages=[{'role': 'user', 'content': content}],
        )

        # Parse response.
        text = ''.join(
            block.text for block in response.content
            if hasattr(block, 'text')
        )
        parsed = _extract_json(text)
        if parsed is None:
            return None, 0.0, 0, 0

        # Calculate cost.
        in_tok = response.usage.input_tokens
        out_tok = response.usage.output_tokens
        cost = self.calculate_cost(in_tok, out_tok)

        return parsed, cost, in_tok, out_tok

    # ── Gemini Implementation ───────────────────────────────────

    def _call_gemini(
            self,
            system_prompt, user_prompt, pdf_path,
            page_images, page_text, response_schema, mode,
        ) -> Tuple[Optional[Dict], float, int, int]:
        """Call Google Gemini with PDF or image support."""
        from google.genai import types

        contents = []

        # Strategy 1: PDF direct.
        if pdf_path and self.supports_pdf:
            with open(_long_path(pdf_path), 'rb') as f:
                pdf_bytes = f.read()
            contents.append(types.Part.from_bytes(
                data=pdf_bytes,
                mime_type='application/pdf',
            ))

        # Strategy 2: Images.
        elif page_images:
            for img_path in page_images:
                with open(img_path, 'rb') as f:
                    img_bytes = f.read()
                contents.append(types.Part.from_bytes(
                    data=img_bytes,
                    mime_type='image/jpeg',
                ))

        # Strategy 3: Text.
        elif page_text:
            user_prompt = f'{user_prompt}\n\nCOA Text:\n{page_text}'

        contents.append(user_prompt)

        # Configure generation.
        gen_config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type='application/json',
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=gen_config,
        )

        # Parse response.
        parsed = _extract_json(response.text)
        if parsed is None:
            return None, 0.0, 0, 0

        # Calculate cost.
        usage = response.usage_metadata
        in_tok = getattr(usage, 'prompt_token_count', 0)
        out_tok = getattr(usage, 'candidates_token_count', 0)
        cost = self.calculate_cost(in_tok, out_tok)

        return parsed, cost, in_tok, out_tok


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Prompts                                                          ║
# ╚══════════════════════════════════════════════════════════════════╝

METADATA_SYSTEM_PROMPT = """You are an expert cannabis Certificate of Analysis (COA) parser. Extract structured metadata from the provided COA document. Return data as JSON matching the LabTestMetadata schema.

Fields to extract:
| Field | Type | Example | Description |
|-------|------|---------|-------------|
| product_name | str | "Blue Dream Preroll (1g)" | Product name |
| strain_name | str | "Blue Dream" | Cannabis strain name |
| product_type | str | "flower" | Product type (flower, concentrate, edible, preroll, vape, tincture, topical) |
| date_tested | str | "2024-01-23" | Test date (ISO format YYYY-MM-DD) |
| date_received | str | "2024-01-22" | Received date (ISO format) |
| date_collected | str | "" | Collection date (ISO format) |
| batch_number | str | "BN123" | Batch/lot number |
| batch_size | float | 1000.0 | Batch size in grams |
| lab | str | "ABC Labs" | Testing lab name |
| lab_license_number | str | "LAB001" | Lab license number |
| lab_address | str | "123 Main St" | Lab address |
| lab_city | str | "Portland" | Lab city |
| lab_state | str | "OR" | Lab state (2-letter code) |
| lab_zipcode | str | "97201" | Lab ZIP code |
| producer | str | "ABC Farms" | Producer/cultivator name |
| producer_street | str | "789 Farm Rd" | Producer street |
| producer_city | str | "Bend" | Producer city |
| producer_state | str | "OR" | Producer state |
| producer_zipcode | str | "97701" | Producer ZIP |
| producer_license_number | str | "PROD001" | Producer license |
| distributor | str | "" | Distributor name if listed |
| distributor_license_number | str | "" | Distributor license |
| sample_id | str | "ABC123" | Lab sample ID |
| sample_weight | float | 1.0 | Sample weight in grams |
| total_cannabinoids | float | 20.0 | Total cannabinoids % |
| total_cbd | float | 0.5 | Total CBD % |
| total_thc | float | 18.0 | Total THC % |
| total_terpenes | float | 2.0 | Total terpenes % |
| status | str | "pass" | Overall pass/fail status |
| analyses | list | ["cannabinoids", "terpenes"] | List of analyses performed |

Rules:
- Return 0.0 for numeric fields not found, "" for string fields not found.
- Dates must be ISO format (YYYY-MM-DD). Convert any date format found.
- For product_type, use lowercase standardized names.
- For analyses, list all analysis types mentioned on the COA."""

METADATA_USER_PROMPT = (
    'Extract the metadata from this Certificate of Analysis (COA). '
    'Return valid JSON matching the LabTestMetadata schema.'
)

ANALYSIS_SYSTEM_PROMPT = """You are an expert cannabis Certificate of Analysis (COA) parser. Extract lab test results for a SPECIFIC analysis type from the provided COA page(s). Return data as JSON with fields:

- "analysis": The analysis type name (string)
- "results": A list of result objects, each with:
  | Field | Type | Example | Description |
  |-------|------|---------|-------------|
  | key | str | "delta_9_thc" | Standardized analyte key (snake_case) |
  | name | str | "Δ9-THC" | Lab's displayed name |
  | value | float | 20.00 | Measured value |
  | units | str | "percent" | Units: "percent", "mg/g", "ug/g", "ppm", "ppb", "cfu/g", "aW" |
  | limit | float | 0.0 | Action limit (0.0 if not shown) |
  | lod | float | 0.0 | Limit of detection |
  | loq | float | 0.0 | Limit of quantification |
  | status | str | "pass" | "pass", "fail", or "" |

Rules:
- Extract ONLY results for the specified analysis.
- Use the standardized key format (snake_case).
- If a value shows "ND" (not detected), use 0.0.
- If a value shows "<LOQ", use the LOQ value minus 0.01.
- Include ALL analytes shown, even if they are not in the standard list."""

ANALYSIS_USER_PROMPT = (
    'Extract ONLY the %s results from this COA page(s). '
    'Standard analyte keys for this analysis:\n\n%s\n\n'
    'Return valid JSON with "analysis" and "results" fields.'
)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ PDF Utilities                                                    ║
# ╚══════════════════════════════════════════════════════════════════╝

def _encode_image(
        image_path: str,
        max_width: int = 2000,
        max_height: int = 2000,
    ) -> str:
    """Encode an image as a base64 string."""
    with open(image_path, 'rb') as f:
        return base64.b64encode(f.read()).decode('utf-8')


def _extract_json(text: str) -> Optional[Dict]:
    """Extract JSON from an AI response, handling markdown code fences."""
    if not text:
        return None
    text = text.strip()
    # Remove markdown code fences.
    if text.startswith('```'):
        lines = text.split('\n')
        lines = [l for l in lines if not l.strip().startswith('```')]
        text = '\n'.join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON within the text.
        start = text.find('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    return None


def _make_schema_strict(schema: Dict) -> None:
    """Recursively make a JSON schema compatible with OpenAI structured output.

    OpenAI's Responses API structured output requires every object
    in the JSON schema to:
      1. Set ``additionalProperties`` to ``false``
      2. List ALL property keys in ``required``

    Pydantic's ``model_json_schema()`` omits both of these for
    Optional fields. This function mutates the schema in-place.
    """
    if not isinstance(schema, dict):
        return

    # Process $defs (Pydantic puts nested model schemas here).
    for defn in schema.get('$defs', {}).values():
        _make_schema_strict(defn)

    # If this is an object type, enforce strict constraints.
    if schema.get('type') == 'object':
        schema['additionalProperties'] = False
        # All properties must be required.
        if 'properties' in schema:
            schema['required'] = list(schema['properties'].keys())

    # Recurse into properties.
    for prop in schema.get('properties', {}).values():
        _make_schema_strict(prop)

    # Recurse into array items.
    if 'items' in schema:
        _make_schema_strict(schema['items'])

    # Handle anyOf / oneOf / allOf (used by Optional fields).
    for key in ('anyOf', 'oneOf', 'allOf'):
        for variant in schema.get(key, []):
            _make_schema_strict(variant)


def _metadata_json_hint() -> str:
    """Return a JSON schema hint for metadata extraction."""
    return json.dumps({
        'product_name': 'str', 'strain_name': 'str', 'product_type': 'str',
        'date_tested': 'YYYY-MM-DD', 'date_received': 'YYYY-MM-DD',
        'date_collected': 'YYYY-MM-DD',
        'batch_number': 'str', 'batch_size': 0.0,
        'lab': 'str', 'lab_license_number': 'str',
        'lab_address': 'str', 'lab_city': 'str', 'lab_state': 'str', 'lab_zipcode': 'str',
        'producer': 'str', 'producer_street': 'str', 'producer_city': 'str',
        'producer_state': 'str', 'producer_zipcode': 'str',
        'producer_license_number': 'str',
        'distributor': 'str', 'distributor_license_number': 'str',
        'sample_id': 'str', 'sample_weight': 0.0,
        'total_cannabinoids': 0.0, 'total_cbd': 0.0, 'total_thc': 0.0,
        'total_terpenes': 0.0, 'status': 'str',
        'analyses': ['cannabinoids', 'terpenes'],
    }, indent=2)


def _analysis_json_hint() -> str:
    """Return a JSON schema hint for analysis extraction."""
    return json.dumps({
        'analysis': 'str',
        'results': [
            {'key': 'str', 'name': 'str', 'value': 0.0, 'units': 'str',
             'limit': 0.0, 'lod': 0.0, 'loq': 0.0, 'status': 'str'}
        ],
    }, indent=2)


def get_pdf_info(pdf_path: str) -> Dict:
    """Get basic info about a PDF (page count, text presence)."""
    try:
        with pdfplumber.open(_long_path(pdf_path)) as pdf:
            num_pages = len(pdf.pages)
            first_page_text = (pdf.pages[0].extract_text() or '') if pdf.pages else ''
            has_text = len(first_page_text) > 50
            # Detect analyses mentioned.
            all_text = ''
            for page in pdf.pages[:5]:  # Check first 5 pages max.
                t = page.extract_text() or ''
                all_text += ' ' + t.lower()
            detected_analyses = []
            for analysis, config in ANALYSIS_CONFIGS.items():
                for keyword in config['keywords']:
                    if keyword.lower() in all_text:
                        detected_analyses.append(analysis)
                        break
            return {
                'num_pages': num_pages,
                'has_text': has_text,
                'first_page_text': first_page_text[:500],
                'detected_analyses': detected_analyses,
            }
    except Exception as e:
        return {'num_pages': 0, 'has_text': False, 'error': str(e)}


def get_pdf_pages_as_images(
        pdf_path: str,
        page_indexes: Union[int, List[int], str] = 0,
        keywords: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        resolution: int = 300,
    ) -> List[str]:
    """Convert PDF pages to JPEG images."""
    if output_dir is None:
        output_dir = tempfile.mkdtemp()
    os.makedirs(output_dir, exist_ok=True)

    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    image_paths = []

    try:
        with pdfplumber.open(_long_path(pdf_path)) as pdf:
            total_pages = len(pdf.pages)

            # Determine which pages to process.
            if isinstance(page_indexes, int):
                pages = [min(page_indexes, total_pages - 1)]
            elif isinstance(page_indexes, list):
                pages = [p for p in page_indexes if p < total_pages]
            elif page_indexes == 'all':
                pages = list(range(total_pages))
            elif page_indexes == 'keywords' and keywords:
                pages = [0]  # Always include first page.
                for i in range(1, total_pages):
                    text = (pdf.pages[i].extract_text() or '').lower()
                    if any(kw.lower() in text for kw in keywords):
                        pages.append(i)
                pages = sorted(set(pages))
            else:
                pages = [0]

            for idx in pages:
                out_path = os.path.join(
                    output_dir,
                    f'{pdf_name}_p{idx + 1:03d}.jpeg',
                )
                pdf.pages[idx].to_image(resolution=resolution).save(out_path)
                image_paths.append(out_path)

    except Exception as e:
        logging.getLogger(__name__).error(f'PDF image conversion failed: {e}')

    return image_paths


def extract_pdf_text(
        pdf_path: str,
        keywords: Optional[List[str]] = None,
    ) -> str:
    """Extract text from PDF pages matching keywords."""
    try:
        with pdfplumber.open(_long_path(pdf_path)) as pdf:
            if not pdf.pages:
                return ''
            if not keywords:
                return pdf.pages[0].extract_text() or ''
            texts = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    text_lower = text.lower()
                    if any(kw.lower() in text_lower for kw in keywords):
                        texts.append(text)
            return '\n\n'.join(texts) if texts else (pdf.pages[0].extract_text() or '')
    except Exception:
        return ''


# ╔══════════════════════════════════════════════════════════════════╗
# ║ Core Parsing Engine                                              ║
# ╚══════════════════════════════════════════════════════════════════╝

class COAParser:
    """Production-grade COA parsing engine.

    Orchestrates multi-provider AI parsing with intelligent
    strategy selection, caching, and cost tracking.
    """

    def __init__(
            self,
            state: str,
            provider: str = 'openai',
            model: Optional[str] = None,
            data_dir: Optional[Path] = None,
            cache_dir: Optional[Path] = None,
            log_dir: Optional[Path] = None,
            budget: Optional[float] = None,
            max_parses: Optional[int] = None,
            logger: Optional[logging.Logger] = None,
        ):
        self.state = state.lower()
        self.state_name = STATE_NAMES.get(self.state, self.state)
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.log_dir = log_dir or DEFAULT_LOG_DIR
        self.budget = budget
        self.max_parses = max_parses

        # Ensure directories exist.
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Initialize logger.
        self.logger = logger or initialize_logs(
            f'parse_coas_{self.state}',
            prefix=f'parse-coas-{self.state}',
            log_dir=str(self.log_dir),
        )

        # Initialize AI client.
        config = dotenv_values('../.env')
        self.ai_client = AIClient(
            provider=provider,
            model=model,
            config=config,
            logger=self.logger,
        )

        # Initialize cost tracker.
        self.cost_tracker = CostTracker()

        # Initialize caches.
        model_tag = (model or AI_PROVIDERS[provider]['default_model']).replace('.', '_')
        self.metadata_cache = Bogart(
            str(self.cache_dir / f'results-{self.state}-metadata-{model_tag}.jsonl')
        )
        self.analysis_caches = {}
        for analysis_name in ANALYSIS_CONFIGS:
            self.analysis_caches[analysis_name] = Bogart(
                str(self.cache_dir / f'results-{self.state}-{analysis_name}-{model_tag}.jsonl')
            )

    @property
    def pdf_dir(self) -> Path:
        """Get the PDF directory for this state."""
        return self.data_dir / self.state_name / 'results' / 'pdfs'

    def discover_pdfs(self, source: str = '') -> pd.DataFrame:
        """Discover all COA PDFs for this state/source."""
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
                f'Skipped {skipped_long} PDFs that could not be '
                f'accessed (likely long path issues).'
            )

        pdf_files.sort()
        df = pd.DataFrame({'file_path': pdf_files})
        if df.empty:
            return df

        df['pdf_hash'] = df['file_path'].apply(
            lambda x: hash_file(_long_path(x), size=65536)
        )
        df.drop_duplicates(subset=['pdf_hash'], inplace=True)
        self.logger.info(f'Discovered {len(df):,} unique PDFs in {search_dir}')
        return df

    def parse_all(
            self,
            source: str = '',
            sample_size: Optional[int] = None,
            random_state: int = 42,
            analyses: Optional[List[str]] = None,
        ) -> Dict:
        """Parse all discovered COA PDFs.

        Args:
            source: Optional source filter (e.g., 'prr', 'flowery').
            sample_size: If set, randomly sample this many PDFs.
            random_state: Random seed for reproducibility.
            analyses: List of analyses to parse. None = all detected.

        Returns:
            Summary statistics dict.
        """
        # Discover PDFs.
        pdfs = self.discover_pdfs(source)
        if pdfs.empty:
            self.logger.info('No PDFs found.')
            return {'parsed': 0, 'skipped': 0, 'errors': 0}

        # Optionally sample.
        if sample_size and sample_size < len(pdfs):
            pdfs = pdfs.sample(n=sample_size, random_state=random_state)
            self.logger.info(f'Sampled {len(pdfs):,} PDFs.')

        # Apply max_parses limit.
        if self.max_parses:
            pdfs = pdfs.head(self.max_parses)

        total = len(pdfs)
        parsed_count = 0
        skipped_count = 0
        error_count = 0

        self.logger.info(f'Starting parse of {total:,} COAs...')

        for i, (_, row) in enumerate(pdfs.iterrows()):
            # Budget check.
            if self.budget and self.cost_tracker.total_cost >= self.budget:
                self.logger.info(
                    f'Budget exhausted (${self.cost_tracker.total_cost:.4f} '
                    f'>= ${self.budget:.2f}). Stopping.'
                )
                break

            # Provider exhaustion check.
            if not self.ai_client.is_available:
                self.logger.warning('AI provider exhausted. Stopping.')
                break

            pdf_hash = row['pdf_hash']
            file_path = row['file_path']
            self.logger.info(f'--- [{i + 1}/{total}] {os.path.basename(file_path)} ---')

            try:
                result = self._parse_single_coa(
                    pdf_hash=pdf_hash,
                    file_path=file_path,
                    analyses=analyses,
                )
                if result == 'skipped':
                    skipped_count += 1
                else:
                    parsed_count += 1
            except Exception as e:
                self.logger.error(f'Error parsing {pdf_hash}: {e}')
                error_count += 1

        # Summary.
        summary = {
            'state': self.state,
            'total_pdfs': total,
            'parsed': parsed_count,
            'skipped': skipped_count,
            'errors': error_count,
            'costs': self.cost_tracker.summary(),
        }
        self.logger.info(
            f'Parse complete. Parsed: {parsed_count}, '
            f'Skipped: {skipped_count}, Errors: {error_count}. '
            f'Cost: ${self.cost_tracker.total_cost:.4f}'
        )
        return summary

    def _parse_single_coa(
            self,
            pdf_hash: str,
            file_path: str,
            analyses: Optional[List[str]] = None,
        ) -> str:
        """Parse a single COA PDF.

        Returns 'parsed' or 'skipped'.
        """
        # ── Step 1: Parse metadata ──────────────────────────────

        if self.metadata_cache.get(pdf_hash):
            self.logger.info(f'Metadata cached: {pdf_hash[:12]}...')
            metadata = self.metadata_cache.get(pdf_hash)
        else:
            self.logger.info('Parsing metadata...')
            start = time.time()

            with tempfile.TemporaryDirectory() as tmpdir:
                # Determine strategy.
                pdf_info = get_pdf_info(file_path)

                if self.ai_client.supports_pdf:
                    # Send PDF directly (Anthropic, Gemini).
                    self.logger.info(
                        f'Strategy: native PDF ({pdf_info.get("num_pages", "?")} pages)'
                    )
                    parsed, cost, in_tok, out_tok = self.ai_client.parse_metadata(
                        pdf_path=file_path,
                    )
                else:
                    # Convert to page images (OpenAI, xAI).
                    num_pages = pdf_info.get('num_pages', 1)
                    page_idx = [0, 1] if num_pages > 1 else [0]
                    images = get_pdf_pages_as_images(
                        file_path, page_indexes=page_idx,
                        output_dir=tmpdir,
                    )
                    text = extract_pdf_text(file_path)
                    self.logger.info(
                        f'Strategy: {len(images)} page images'
                        f'{" + text" if not images else ""}'
                    )
                    parsed, cost, in_tok, out_tok = self.ai_client.parse_metadata(
                        pdf_path=None,
                        page_images=images if images else None,
                        page_text=text if not images else None,
                    )

            if parsed is None:
                self.logger.warning(f'Metadata parse failed: {pdf_hash[:12]}...')
                return 'skipped'

            elapsed = time.time() - start
            metadata = {
                'pdf_hash': pdf_hash,
                'parsing_model': self.ai_client.model,
                'parsing_provider': self.ai_client.provider,
                'parsing_time': round(elapsed, 2),
                'parsing_cost': round(cost, 6),
                **parsed,
            }
            self.metadata_cache.set(pdf_hash, metadata)
            self.cost_tracker.record(
                self.ai_client.provider, self.ai_client.model,
                in_tok, out_tok, cost, 'metadata', pdf_hash,
            )
            self.logger.info(
                f'Metadata parsed: ${cost:.4f}, {round(elapsed)}s'
            )

        # ── Step 2: Determine which analyses to parse ───────────

        product_type = normalize_product_type(
            metadata.get('product_type', '')
        )
        detected = metadata.get('analyses', [])
        pdf_info = get_pdf_info(file_path)
        detected_from_pdf = pdf_info.get('detected_analyses', [])

        # Combine detected analyses from metadata and PDF scan.
        all_detected = set(detected_from_pdf)
        for a in (detected or []):
            a_lower = a.lower().replace(' ', '_')
            for config_name in ANALYSIS_CONFIGS:
                if config_name in a_lower or a_lower in config_name:
                    all_detected.add(config_name)

        # Always include cannabinoids and terpenes.
        all_detected.add('cannabinoids')
        all_detected.add('terpenes')

        # Filter by product type restrictions.
        target_analyses = []
        for analysis_name in ANALYSIS_CONFIGS:
            if analyses and analysis_name not in analyses:
                continue
            config = ANALYSIS_CONFIGS[analysis_name]
            allowed_types = config.get('product_types')
            if allowed_types and product_type not in allowed_types:
                continue
            if analysis_name in all_detected or not allowed_types:
                target_analyses.append(analysis_name)

        self.logger.info(
            f'Product type: {product_type}. '
            f'Target analyses: {target_analyses}'
        )

        # ── Step 3: Parse each analysis ─────────────────────────

        for analysis_name in target_analyses:
            cache = self.analysis_caches[analysis_name]

            # Check cache.
            if cache.get(pdf_hash):
                self.logger.info(f'{analysis_name}: cached')
                continue

            # Budget check.
            if self.budget and self.cost_tracker.total_cost >= self.budget:
                self.logger.info('Budget exhausted mid-COA.')
                break

            config = ANALYSIS_CONFIGS[analysis_name]
            keywords = config['keywords']
            analyte_keys = config['keys']

            self.logger.info(f'Parsing {analysis_name}...')
            start = time.time()

            with tempfile.TemporaryDirectory() as tmpdir:
                if self.ai_client.supports_pdf:
                    # For PDF-capable providers (Anthropic, Gemini).
                    self.logger.info(f'{analysis_name}: strategy=native PDF')
                    parsed, cost, in_tok, out_tok = self.ai_client.parse_analysis(
                        analysis_name=analysis_name,
                        analyte_keys=analyte_keys,
                        pdf_path=file_path,
                    )
                else:
                    # Use keyword-targeted page images (OpenAI, xAI).
                    images = get_pdf_pages_as_images(
                        file_path,
                        page_indexes='keywords',
                        keywords=keywords,
                        output_dir=tmpdir,
                    )
                    text = extract_pdf_text(file_path, keywords=keywords) if not images else None
                    self.logger.info(
                        f'{analysis_name}: strategy='
                        f'{len(images)} keyword-targeted images'
                        f'{" + text" if text else ""}'
                    )
                    parsed, cost, in_tok, out_tok = self.ai_client.parse_analysis(
                        analysis_name=analysis_name,
                        analyte_keys=analyte_keys,
                        pdf_path=None,
                        page_images=images if images else None,
                        page_text=text,
                    )

            elapsed = time.time() - start

            if parsed is not None:
                cache_entry = {
                    'pdf_hash': pdf_hash,
                    'parsing_model': self.ai_client.model,
                    'parsing_provider': self.ai_client.provider,
                    'parsing_time': round(elapsed, 2),
                    'parsing_cost': round(cost, 6),
                    'results': parsed.get('results', []),
                }
                cache.set(pdf_hash, cache_entry)
                self.cost_tracker.record(
                    self.ai_client.provider, self.ai_client.model,
                    in_tok, out_tok, cost, analysis_name, pdf_hash,
                )
                n_results = len(parsed.get('results', []))
                self.logger.info(
                    f'{analysis_name}: {n_results} results, '
                    f'${cost:.4f}, {round(elapsed)}s'
                )
            else:
                self.logger.warning(f'{analysis_name}: parse failed')

        return 'parsed'

    def get_cache_stats(self) -> Dict:
        """Get statistics about the current cache state."""
        stats = {
            'state': self.state,
            'model': self.ai_client.model,
            'provider': self.ai_client.provider,
            'metadata': len(self.metadata_cache.to_df()),
        }
        for analysis_name, cache in self.analysis_caches.items():
            df = cache.to_df()
            stats[analysis_name] = len(df)
        return stats


# ╔══════════════════════════════════════════════════════════════════╗
# ║ CLI Entry Point                                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

def main():
    """Command-line interface for COA parsing."""
    parser = argparse.ArgumentParser(
        description='Cannlytics AI COA Parsing Engine',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Parse 100 NY COAs using OpenAI
  python parse_coas.py --state ny --provider openai --max-parses 100

  # Parse CA COAs with Anthropic, $5 budget
  python parse_coas.py --state ca --provider anthropic --budget 5.00

  # Parse using Gemini free tier
  python parse_coas.py --state fl --provider gemini

  # Show cache statistics
  python parse_coas.py --state ny --cache-stats

  # Dry run - show what would be parsed
  python parse_coas.py --state ny --dry-run

Provider Priority: anthropic > openai > gemini > xai
        """,
    )

    # Required arguments.
    parser.add_argument(
        '--state', '-s', type=str, required=True,
        help='State code (e.g., ny, ca, fl)',
    )

    # Provider options.
    parser.add_argument(
        '--provider', '-p', type=str, default='openai',
        choices=['anthropic', 'openai', 'gemini', 'xai'],
        help='AI provider (default: openai)',
    )
    parser.add_argument(
        '--model', '-m', type=str, default=None,
        help='Specific model name (default: provider default)',
    )

    # Scope options.
    parser.add_argument(
        '--source', type=str, default='',
        help='Filter to specific source (e.g., prr, flowery)',
    )
    parser.add_argument(
        '--max-parses', '-n', type=int, default=None,
        help='Maximum number of COAs to parse',
    )
    parser.add_argument(
        '--sample-size', type=int, default=None,
        help='Random sample size (for dev/testing)',
    )
    parser.add_argument(
        '--budget', '-b', type=float, default=None,
        help='Maximum budget in dollars',
    )
    parser.add_argument(
        '--analyses', type=str, nargs='+', default=None,
        help='Specific analyses to parse (e.g., cannabinoids terpenes)',
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

    # Utility commands.
    parser.add_argument(
        '--cache-stats', action='store_true',
        help='Show cache statistics and exit',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Show what would be parsed without parsing',
    )
    parser.add_argument(
        '--clear-cache', action='store_true',
        help='Delete all cache files for this state/model and exit',
    )

    args = parser.parse_args()

    # Initialize parser.
    coa_parser = COAParser(
        state=args.state,
        provider=args.provider,
        model=args.model,
        data_dir=Path(args.data_dir) if args.data_dir else None,
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        budget=args.budget,
        max_parses=args.max_parses,
    )

    # Handle utility commands.
    if args.cache_stats:
        stats = coa_parser.get_cache_stats()
        print('\n=== Cache Statistics ===')
        for k, v in stats.items():
            print(f'  {k}: {v:,}' if isinstance(v, int) else f'  {k}: {v}')
        return

    if args.dry_run:
        pdfs = coa_parser.discover_pdfs(args.source)
        print(f'\nDry Run: {len(pdfs):,} PDFs discovered')
        if not pdfs.empty:
            print(f'  Directory: {coa_parser.pdf_dir}')
            print(f'  Provider: {args.provider} ({coa_parser.ai_client.model})')
            if args.sample_size:
                print(f'  Sample size: {args.sample_size}')
            if args.max_parses:
                print(f'  Max parses: {args.max_parses}')
            if args.budget:
                print(f'  Budget: ${args.budget:.2f}')

            # Show what's already cached.
            stats = coa_parser.get_cache_stats()
            cached = stats.get('metadata', 0)
            remaining = len(pdfs) - cached
            print(f'  Already cached: {cached:,}')
            print(f'  Remaining: {remaining:,}')
        return

    if args.clear_cache:
        print(f'\nClearing cache for {args.state.upper()} / {coa_parser.ai_client.model}...')
        cache_pattern = f'results-{args.state}-*-{coa_parser.ai_client.model.replace(".", "_")}*'
        import glob
        cache_files = glob.glob(str(coa_parser.cache_dir / cache_pattern))
        if not cache_files:
            print('  No cache files found.')
        else:
            for cf in cache_files:
                os.remove(cf)
                print(f'  Deleted: {os.path.basename(cf)}')
            print(f'  Cleared {len(cache_files)} cache file(s).')
        return

    # Run parsing.
    print(f'\n🔬 Cannlytics AI COA Parser')
    print(f'   State: {args.state.upper()}')
    print(f'   Provider: {args.provider} ({coa_parser.ai_client.model})')
    if args.budget:
        print(f'   Budget: ${args.budget:.2f}')
    if args.max_parses:
        print(f'   Max parses: {args.max_parses:,}')
    print()

    summary = coa_parser.parse_all(
        source=args.source,
        sample_size=args.sample_size,
        analyses=args.analyses,
    )

    # Print final summary.
    print('\n' + '=' * 60)
    print('📋 PARSE SUMMARY')
    print('=' * 60)
    print(f'  State: {summary["state"].upper()}')
    print(f'  Total PDFs: {summary["total_pdfs"]:,}')
    print(f'  Parsed: {summary["parsed"]:,}')
    print(f'  Skipped (cached): {summary["skipped"]:,}')
    print(f'  Errors: {summary["errors"]:,}')
    costs = summary['costs']
    print(f'  Total cost: ${costs["total_cost"]:.4f}')
    print(f'  Total API calls: {costs["total_calls"]:,}')
    if costs['by_provider']:
        print(f'  By provider: {costs["by_provider"]}')
    print('=' * 60)


if __name__ == '__main__':
    main()