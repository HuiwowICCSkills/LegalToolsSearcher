"""ICC Legal Tools retrieval core for the standalone LegalToolsSearcher skill.

The public Legal Tools site exposes three useful anonymous routes:

1. ``GET /api/ltddocs/search`` searches indexed metadata and document text.
2. ``GET /api/ltddocs/findOne`` resolves an official document number
   (``externalId``) to one catalogue record.
3. ``GET /doc/<slug>/pdf/`` serves the document PDF.

Search results are catalogue records plus index highlights; they are not full
text.  ``fetch`` is the separate operation that downloads and extracts a PDF.
No GDUFS session, browser, API key, or cookie is used.
"""

from __future__ import annotations

import hashlib
import html as html_module
import importlib.metadata
import json
import platform
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SKILL_NAME = 'LegalToolsSearcher'
SCHEMA_VERSION = 1

SEARCH_URL = 'https://legal-tools.org/api/ltddocs/search'
FIND_ONE_URL = 'https://legal-tools.org/api/ltddocs/findOne'
DOCUMENT_URL = 'https://www.legal-tools.org/doc/{slug}/'
PDF_URL = 'https://www.legal-tools.org/doc/{slug}/pdf/'

UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/155.0.0.0 Safari/537.36'
)

DEFAULT_MAX_BYTES = 250 * 1024 * 1024
JSON_MAX_BYTES = 40 * 1024 * 1024
TAG_RE = re.compile(r'<[^>]+>')
SLUG_RE = re.compile(r'^[A-Za-z0-9_-]{2,64}$')
BARE_SLUG_RE = re.compile(r'^[A-Za-z0-9]{2,64}$')

ORGANISATIONS = {
    'icc': 'International Criminal Court (ICC)',
    'icty': 'International Criminal Tribunal for the former Yugoslavia (ICTY)',
    'ictr': 'International Criminal Tribunal for Rwanda (ICTR)',
    'irmct': 'International Residual Mechanism for Criminal Tribunals (IRMCT)',
    'icrc': 'International Committee of the Red Cross (ICRC)',
    'toaep': 'Torkel Opsahl Academic EPublisher (TOAEP)',
}


class RetrievalError(RuntimeError):
    """A retrieval operation failed and should be reported, not hidden."""


class NotFound(RetrievalError):
    """The requested Legal Tools record does not exist."""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def clean(value: Any) -> str:
    if value is None:
        return ''
    text = html_module.unescape(str(value))
    text = TAG_RE.sub('', text)
    return re.sub(r'\s+', ' ', text).strip()


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def normalize_legal_tools_url(value: Any) -> str:
    """Return HTTPS for legal-tools.org links and leave other links intact."""
    url = str(value or '').strip()
    if not url:
        return ''
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or '').lower()
    if host == 'legal-tools.org' or host.endswith('.legal-tools.org'):
        return urllib.parse.urlunsplit(
            ('https', parsed.netloc, parsed.path, parsed.query, parsed.fragment)
        )
    return url


def _url_with_params(
    url: str, params: dict[str, Any] | None = None
) -> str:
    if not params:
        return url
    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    separator = '&' if '?' in url else '?'
    return url + separator + query


class Response:
    """Minimal response record shared by the live client and tests."""

    __slots__ = ('status', 'headers', 'body', 'url')

    def __init__(
        self,
        status: int,
        headers: dict[str, str],
        body: bytes,
        url: str,
    ):
        self.status = status
        self.headers = {str(k).lower(): str(v) for k, v in headers.items()}
        self.body = body
        self.url = url


class HttpClient:
    """One direct HTTP request per call; no cookies, retries, or browser."""

    def __init__(self, timeout: int = 60):
        self.timeout = timeout

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
        max_bytes: int = JSON_MAX_BYTES,
    ) -> Response:
        request_url = _url_with_params(url, params)
        request = urllib.request.Request(
            request_url,
            headers={'User-Agent': UA, **(headers or {})},
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout or self.timeout
            ) as response:
                declared = response.headers.get('Content-Length')
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise RetrievalError(
                        f'Response exceeds the {max_bytes} byte limit: {request_url}'
                    )
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise RetrievalError(
                        f'Response exceeds the {max_bytes} byte limit: {request_url}'
                    )
                return Response(
                    response.status,
                    dict(response.headers),
                    body,
                    response.geturl(),
                )
        except urllib.error.HTTPError as exc:
            message = f'HTTP {exc.code}: {request_url}'
            if exc.code == 404:
                raise NotFound(message) from exc
            raise RetrievalError(message) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RetrievalError(
                f'Connection failed: {request_url}: {exc}'
            ) from exc

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
        max_bytes: int = JSON_MAX_BYTES,
    ) -> Response:
        return self.request(
            'GET',
            url,
            params=params,
            headers=headers,
            timeout=timeout,
            max_bytes=max_bytes,
        )

    def head(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
        max_bytes: int = JSON_MAX_BYTES,
    ) -> Response:
        return self.request(
            'HEAD',
            url,
            headers=headers,
            timeout=timeout,
            max_bytes=max_bytes,
        )


def resolve_organisation(value: str) -> str:
    value = value.strip()
    return ORGANISATIONS.get(value.lower(), value)


def _json_bytes(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def build_where(
    *,
    organisation: str = '',
    case_number: str = '',
    document_type: str = '',
    where_json: str = '',
) -> dict[str, Any]:
    """Build the API's exact-match ``where`` filter."""
    where: dict[str, Any] = {}
    if where_json:
        try:
            extra = json.loads(where_json)
        except ValueError as exc:
            raise ValueError('--where-json must be valid JSON') from exc
        if not isinstance(extra, dict):
            raise ValueError('--where-json must be a JSON object')
        where.update(extra)
    if organisation:
        where['organisation'] = resolve_organisation(organisation)
    if case_number:
        where['caseNumber'] = case_number.strip()
    if document_type:
        where['judicialDocumentType'] = document_type.strip()
    if where.get('deleted') is True:
        raise ValueError('Deleted records are not exposed by this skill')
    where['deleted'] = False
    return where


def _normalize_total(value: Any) -> tuple[int | None, Any]:
    if isinstance(value, int):
        return value, None
    if isinstance(value, str) and value.isdigit():
        return int(value), None
    if isinstance(value, dict):
        for key in ('value', 'count', 'total'):
            candidate = value.get(key)
            if isinstance(candidate, int):
                return candidate, value
            if isinstance(candidate, str) and candidate.isdigit():
                return int(candidate), value
    return None, value


def _highlight_text(record: dict[str, Any], limit: int = 5000) -> str:
    highlights = record.get('highlight') or {}
    if not isinstance(highlights, dict):
        return ''
    parts: list[str] = []
    preferred = (
        'docPages.text',
        'abstract',
        'title',
        'caseName_en',
        'externalId',
    )
    for field in preferred:
        values = highlights.get(field)
        if values is None:
            continue
        if not isinstance(values, list):
            values = [values]
        parts.extend(clean(item) for item in values if clean(item))
    if not parts:
        for values in highlights.values():
            if not isinstance(values, list):
                values = [values]
            parts.extend(clean(item) for item in values if clean(item))
    return ' '.join(parts)[:limit]


def _document_record(hit: dict[str, Any]) -> dict[str, Any]:
    slug = str(hit.get('slug') or '').strip().lower()
    document_url = normalize_legal_tools_url(
        hit.get('documentUrl') or (DOCUMENT_URL.format(slug=slug) if slug else '')
    )
    pdf_url = normalize_legal_tools_url(
        hit.get('pdfURL') or (PDF_URL.format(slug=slug) if slug else '')
    )
    return {
        'title': clean(hit.get('title')),
        'external_id': clean(hit.get('externalId')),
        'slug': slug,
        'url': document_url,
        'pdf_url': pdf_url,
        'original_pdf_url': normalize_legal_tools_url(hit.get('orignalPdfURL')),
        'case_name': clean(hit.get('caseName_en') or hit.get('caseName')),
        'case_number': clean(hit.get('caseNumber')),
        'date': clean(hit.get('dateCreated')),
        'organisation': clean(hit.get('organisation')),
        'related_organisation': clean(hit.get('relatedOrganisation')),
        'judicial_body': clean(hit.get('orgaisationJudicialBody')),
        'collection': clean(hit.get('collection')),
        'source_role': clean(hit.get('source')),
        'document_type': clean(
            hit.get('documentTypeEN')
            or hit.get('judicialDocumentType')
            or hit.get('contentType')
        ),
        'judicial_document_type': clean(hit.get('judicialDocumentType')),
        'content_type': clean(hit.get('contentType')),
        'confidentiality': clean(hit.get('confidentiality')),
        'court_type': clean(hit.get('courtType')),
        'importance': clean(hit.get('importance')),
        'number_of_pages': hit.get('numberOfPages'),
        'is_court_record': hit.get('isCourtRecord'),
        'is_public': (
            clean(hit.get('confidentiality')).lower() == 'public'
            if hit.get('confidentiality')
            else None
        ),
        'is_original_language': hit.get('isOriginalLanguage'),
        'is_authoritative_language': hit.get('isAuthoritativeLanguage'),
        'language_ids': hit.get('languageIds') or [],
        'download_permission_required': bool(
            hit.get('requireDownloadPermission')
        ),
        'abstract': clean(hit.get('abstract')),
        'document_origin': hit.get('documentOrigin') or {},
        'updated': clean(hit.get('updated')),
    }


def _search_result(record: dict[str, Any]) -> dict[str, Any]:
    hit = record.get('hit')
    if not isinstance(hit, dict):
        raise RetrievalError('Legal Tools search schema changed: hit missing')
    item = _document_record(hit)
    snippet = _highlight_text(record)
    if not snippet:
        snippet = item['abstract']
    return {
        **item,
        'snippet': snippet,
        'content_scope': (
            'search-index metadata and document-text highlight; not the full PDF'
        ),
        'result_type': 'retrieved_metadata',
    }


def _evidence(response: Response) -> dict[str, Any]:
    return {
        'request_url': response.url,
        'http_status': response.status,
        'content_type': response.headers.get('content-type', ''),
        'retrieved_at': now_utc(),
        'bytes': len(response.body),
        'sha256': sha256_bytes(response.body),
    }


def build_term(query: str, match: str, near_distance: int = 5) -> str:
    """Translate a friendly match mode into Legal Tools search syntax."""
    text = str(query or '').strip()
    if not text or match == 'raw':
        return text
    if match == 'phrase':
        return '"' + text.replace('"', '\\"') + '"'
    words = text.split()
    if len(words) < 2:
        return text
    if match == 'all':
        return ' AND '.join(words)
    if match == 'any':
        return ' OR '.join(words)
    if match == 'near':
        operator = f'NEAR/{near_distance}'
        return f' {operator} '.join(words)
    return text


def search(
    query: str = '',
    limit: int = 10,
    offset: int = 0,
    *,
    organisation: str = '',
    case_number: str = '',
    document_type: str = '',
    where_json: str = '',
    match: str = 'raw',
    near_distance: int = 5,
    http: HttpClient | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Search Legal Tools; an empty query is allowed with explicit filters."""
    if not 1 <= limit <= 50:
        raise ValueError('limit must be between 1 and 50')
    if offset < 0:
        raise ValueError('offset must be >= 0')
    if match not in ('raw', 'phrase', 'all', 'any', 'near'):
        raise ValueError("match must be raw, phrase, all, any or near")
    if near_distance < 1 or near_distance > 100:
        raise ValueError('near-distance must be 1..100')
    expression = build_term(query, match, near_distance)

    where = build_where(
        organisation=organisation,
        case_number=case_number,
        document_type=document_type,
        where_json=where_json,
    )
    filter_value = {'from': offset, 'limit': limit, 'where': where}
    client = http or HttpClient(timeout)
    params = {'term': expression, 'filter': _json_bytes(filter_value)}
    response = client.get(
        SEARCH_URL,
        params=params,
        timeout=timeout,
        max_bytes=JSON_MAX_BYTES,
    )
    try:
        payload = json.loads(response.body.decode('utf-8', 'replace'))
    except ValueError as exc:
        raise RetrievalError(
            'Legal Tools search schema changed: response is not JSON'
        ) from exc
    if not isinstance(payload.get('results'), list):
        raise RetrievalError(
            'Legal Tools search schema changed: results array missing'
        )

    results = [_search_result(record) for record in payload['results']]
    total, total_raw = _normalize_total(payload.get('total'))
    consumed = offset + len(results)
    if not results:
        next_offset = None
    elif total is not None:
        next_offset = consumed if consumed < total else None
    else:
        next_offset = consumed if len(results) == limit else None

    out: dict[str, Any] = {
        'skill': SKILL_NAME,
        'schema_version': SCHEMA_VERSION,
        'command': 'search',
        'status': 'ok',
        'retrieved_at': now_utc(),
        'query': query,
        'query_expression': expression,
        'match': match,
        'filters': where,
        'total': total,
        'results': results,
        'pagination': {
            'offset': offset,
            'limit': limit,
            'returned': len(results),
            'next_offset': next_offset,
        },
        'scope': (
            'Anonymous Legal Tools API term search over indexed metadata and '
            'document text. Multi-token and phrase behavior is controlled by '
            'the remote API and is not normalized by this skill.'
        ),
        'evidence': _evidence(response),
        'warnings': [],
    }
    if total_raw is not None:
        out['total_raw'] = total_raw
    return out


def _find_one(
    external_id: str,
    *,
    http: HttpClient | None = None,
    timeout: int = 60,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    external_id = external_id.strip()
    if not external_id:
        raise ValueError('official document number must not be empty')
    client = http or HttpClient(timeout)
    params = {'filter': _json_bytes({'where': {'externalId': external_id}})}
    try:
        response = client.get(
            FIND_ONE_URL,
            params=params,
            timeout=timeout,
            max_bytes=JSON_MAX_BYTES,
        )
    except NotFound:
        return None, {
            'request_url': _url_with_params(FIND_ONE_URL, params),
            'http_status': 404,
            'content_type': '',
            'retrieved_at': now_utc(),
            'bytes': 0,
            'sha256': '',
        }
    try:
        hit = json.loads(response.body.decode('utf-8', 'replace'))
    except ValueError as exc:
        raise RetrievalError(
            'Legal Tools findOne schema changed: response is not JSON'
        ) from exc
    if hit is None or hit == 'null' or hit == 'undefined':
        return None, _evidence(response)
    if not isinstance(hit, dict):
        raise RetrievalError(
            'Legal Tools findOne schema changed: expected one record'
        )
    return _document_record(hit), _evidence(response)


def document(
    external_id: str,
    *,
    http: HttpClient | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Resolve one official document number without downloading its PDF."""
    try:
        record, evidence = _find_one(
            external_id, http=http, timeout=timeout
        )
    except RetrievalError as exc:
        if 'HTTP 404' in str(exc):
            record, evidence = None, {}
        else:
            raise
    if record is None:
        return {
            'skill': SKILL_NAME,
            'schema_version': SCHEMA_VERSION,
            'command': 'document',
            'status': 'not-found',
            'retrieved_at': now_utc(),
            'external_id': external_id.strip(),
            'evidence': evidence,
            'warnings': [
                'The official document number was not found as an exact '
                'externalId. It does not prove that a differently numbered '
                'version or translation is absent.'
            ],
        }
    return {
        'skill': SKILL_NAME,
        'schema_version': SCHEMA_VERSION,
        'command': 'document',
        'status': 'ok',
        'retrieved_at': now_utc(),
        'external_id': record['external_id'],
        'document': record,
        'evidence': evidence,
        'warnings': [],
    }


def slug_from_locator(locator: str) -> str:
    locator = locator.strip()
    if not locator:
        raise ValueError('fetch locator must not be empty')
    parsed = urllib.parse.urlsplit(locator)
    if parsed.scheme or parsed.netloc:
        host = (parsed.hostname or '').lower()
        if host != 'legal-tools.org' and not host.endswith('.legal-tools.org'):
            raise RetrievalError(
                f'Not a Legal Tools URL: {locator}'
            )
        parts = [part for part in parsed.path.split('/') if part]
        if len(parts) < 2 or parts[0] != 'doc':
            raise RetrievalError(
                f'Legal Tools document URL must look like /doc/<slug>/: {locator}'
            )
        slug = parts[1]
    elif BARE_SLUG_RE.fullmatch(locator):
        slug = locator
    else:
        return ''
    if not SLUG_RE.fullmatch(slug):
        raise RetrievalError(f'Invalid Legal Tools document slug: {slug}')
    return slug.lower()


def pdf_pages(body: bytes) -> list[dict[str, Any]]:
    """Extract text page by page, preferring PyMuPDF over pypdf."""
    try:
        import pymupdf  # type: ignore

        with pymupdf.open(stream=body, filetype='pdf') as document:
            return [
                {'page': number, 'text': page.get_text('text')}
                for number, page in enumerate(document, 1)
            ]
    except ImportError:
        pass

    try:
        from pypdf import PdfReader

        reader = PdfReader(__import__('io').BytesIO(body))
        return [
            {'page': number, 'text': page.extract_text() or ''}
            for number, page in enumerate(reader.pages, 1)
        ]
    except ImportError as exc:
        raise RetrievalError(
            'PDF text extraction requires PyMuPDF or pypdf; run: '
            'python -m pip install -r requirements.txt'
        ) from exc
    except Exception as exc:
        raise RetrievalError(f'PDF extraction failed: {exc}') from exc


def _page_text(pages: list[dict[str, Any]]) -> str:
    return '\n\n'.join(
        f"@@PAGE {page['page']}@@\n{page.get('text', '')}"
        for page in pages
    )


def fetch(
    locator: str,
    *,
    http: HttpClient | None = None,
    timeout: int = 300,
    max_bytes: int = DEFAULT_MAX_BYTES,
    pdf_out: str | None = None,
    text_out: str | None = None,
) -> dict[str, Any]:
    """Download and extract one Legal Tools PDF.

    ``locator`` may be a ``/doc/<slug>/`` URL, a bare alphanumeric slug, or an
    official document number (``externalId``).
    """
    if max_bytes < 1:
        raise ValueError('max_bytes must be positive')
    client = http or HttpClient(timeout)
    evidence: list[dict[str, Any]] = []
    record: dict[str, Any] | None = None
    slug = slug_from_locator(locator)

    if not slug:
        record, lookup_evidence = _find_one(
            locator, http=client, timeout=timeout
        )
        if lookup_evidence:
            evidence.append(lookup_evidence)
        if record is None:
            return {
                'skill': SKILL_NAME,
                'schema_version': SCHEMA_VERSION,
                'command': 'fetch',
                'status': 'not-found',
                'retrieved_at': now_utc(),
                'locator': locator.strip(),
                'evidence': evidence,
                'warnings': [
                    'The official document number was not found as an exact '
                    'externalId.'
                ],
            }
        slug = record['slug']
        if not slug:
            raise RetrievalError(
                'Legal Tools record has no document slug; PDF URL unavailable'
            )
        pdf_url = record['pdf_url'] or PDF_URL.format(slug=slug)
    else:
        pdf_url = PDF_URL.format(slug=slug)

    response = client.get(
        pdf_url,
        timeout=timeout,
        max_bytes=max_bytes,
    )
    evidence.append(_evidence(response))
    if not response.body.startswith(b'%PDF'):
        preview = response.body[:200].decode('utf-8', 'replace')
        raise RetrievalError(
            'Legal Tools PDF route did not return a PDF: '
            f'{response.url}; content-type='
            f'{response.headers.get("content-type", "")}; body={preview!r}'
        )

    pages = pdf_pages(response.body)
    text = _page_text(pages)
    if pdf_out:
        pdf_path = Path(pdf_out)
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(response.body)
    if text_out:
        text_path = Path(text_out)
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text + '\n', encoding='utf-8')

    return {
        'skill': SKILL_NAME,
        'schema_version': SCHEMA_VERSION,
        'command': 'fetch',
        'status': 'ok',
        'retrieved_at': now_utc(),
        'locator': locator.strip(),
        'external_id': record['external_id'] if record else '',
        'slug': slug,
        'title': record['title'] if record else '',
        'document': record,
        'pdf': {
            'url': response.url,
            'content_type': response.headers.get('content-type', ''),
            'bytes': len(response.body),
            'sha256': sha256_bytes(response.body),
            'pages': len(pages),
            'needs_ocr': not any(page.get('text', '').strip() for page in pages),
            'pdf_out': str(Path(pdf_out).resolve()) if pdf_out else None,
        },
        'pages': pages,
        'text_characters': sum(
            len(page.get('text', '')) for page in pages
        ),
        'text_out': str(Path(text_out).resolve()) if text_out else None,
        'evidence': evidence,
        'warnings': [
            'PDF page text is the document text layer. Blank or garbled text '
            'means OCR is required; the PDF itself remains the authoritative '
            'retrieved artifact.'
        ],
    }


def doctor(
    *,
    probe: bool = True,
    http: HttpClient | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Check the Python stack and the two public Legal Tools routes."""
    checks: dict[str, Any] = {}
    checks['python'] = {
        'ok': sys.version_info >= (3, 10),
        'version': platform.python_version(),
        'required': True,
    }

    backend = ''
    backend_version = ''
    backend_error = ''
    try:
        import pymupdf  # type: ignore  # noqa: F401

        backend = 'pymupdf'
        try:
            backend_version = importlib.metadata.version('PyMuPDF')
        except importlib.metadata.PackageNotFoundError:
            backend_version = getattr(pymupdf, '__version__', '')
    except ImportError:
        try:
            import pypdf  # noqa: F401

            backend = 'pypdf'
            try:
                backend_version = importlib.metadata.version('pypdf')
            except importlib.metadata.PackageNotFoundError:
                backend_version = getattr(pypdf, '__version__', '')
        except ImportError as exc:
            backend_error = str(exc)
    checks['pdf_backend'] = {
        'ok': bool(backend),
        'backend': backend,
        'version': backend_version,
        'error': backend_error,
        'required': True,
    }

    search_status = 'skipped'
    pdf_status = 'skipped'
    search_ok = not probe
    pdf_ok = not probe
    if probe:
        client = http or HttpClient(timeout)
        try:
            payload = search(
                'Mudacumura confirmation of charges',
                1,
                0,
                http=client,
                timeout=timeout,
            )
            search_status = (
                f"HTTP 200; total={payload.get('total')}; "
                f"returned={len(payload.get('results') or [])}"
            )
            search_ok = True
            if payload.get('results'):
                probe_pdf = payload['results'][0]['pdf_url']
            else:
                probe_pdf = PDF_URL.format(slug='ecfae0')
        except RetrievalError as exc:
            search_status = str(exc)
            probe_pdf = PDF_URL.format(slug='ecfae0')
        if probe_pdf:
            try:
                response = client.head(
                    probe_pdf,
                    timeout=timeout,
                    max_bytes=JSON_MAX_BYTES,
                )
                pdf_status = (
                    f"HTTP {response.status}; "
                    f"content-type={response.headers.get('content-type', '')}"
                )
                pdf_ok = response.status == 200 and (
                    'pdf' in response.headers.get('content-type', '').lower()
                )
            except RetrievalError as exc:
                pdf_status = str(exc)
    checks['search_api'] = {'ok': search_ok, 'status': search_status}
    checks['pdf_route'] = {'ok': pdf_ok, 'status': pdf_status}

    ok = bool(
        checks['python']['ok']
        and checks['pdf_backend']['ok']
        and search_ok
        and pdf_ok
    )
    next_steps: list[str] = []
    if not checks['python']['ok']:
        next_steps.append('Install Python 3.10 or newer.')
    if not checks['pdf_backend']['ok']:
        next_steps.append('Run: python -m pip install -r requirements.txt')
    if probe and not search_ok:
        next_steps.append(
            'The Legal Tools search API probe failed; report the exact HTTP '
            'error rather than treating it as zero hits.'
        )
    if probe and not pdf_ok:
        next_steps.append(
            'The PDF route probe failed or returned a non-PDF response.'
        )
    return {
        'skill': SKILL_NAME,
        'schema_version': SCHEMA_VERSION,
        'command': 'doctor',
        'retrieved_at': now_utc(),
        'status': 'ok' if ok else 'degraded',
        'ok': ok,
        'checks': checks,
        'next_steps': next_steps,
    }
