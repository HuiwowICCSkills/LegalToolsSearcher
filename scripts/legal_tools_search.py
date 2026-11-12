#!/usr/bin/env python3
"""Command-line entry point for the standalone LegalToolsSearcher skill.

Commands:
  search    search the public Legal Tools catalogue
  document  resolve one official document number (externalId)
  fetch     download and extract one document PDF
  doctor    check dependencies and public route reachability

Every command prints a JSON report on stdout.  Search results are catalogue
metadata and index highlights; only ``fetch`` retrieves full document text.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import legal_tools_adapter as adapter  # noqa: E402 - path set above


def _emit(payload: dict[str, Any], out: str | None, summary: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + '\n', encoding='utf-8')
        summary = {'output': str(path.resolve()), **summary}
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='legal_tools_search.py',
        description='Standalone ICC Legal Tools catalogue and PDF retrieval.',
    )
    sub = parser.add_subparsers(dest='command', required=True)

    search = sub.add_parser(
        'search',
        help='Search Legal Tools indexed metadata and document text',
    )
    search.add_argument(
        'query',
        nargs='?',
        default='',
        help='Keyword query; omit with --all when using explicit filters',
    )
    search.add_argument(
        '--all',
        action='store_true',
        help='Allow a filter-only search with no keyword query',
    )
    search.add_argument(
        '--organisation',
        default='',
        help='Exact organisation label or alias: icc, icty, ictr, irmct, icrc, toaep',
    )
    search.add_argument(
        '--case-number',
        default='',
        help='Exact caseNumber filter, for example ICC-01/04-01/06',
    )
    search.add_argument(
        '--document-type',
        default='',
        help='Exact judicialDocumentType filter, for example judgement',
    )
    search.add_argument(
        '--where-json',
        default='',
        help='Additional JSON object merged into the API where filter',
    )
    search.add_argument(
        '--match',
        choices=('raw', 'phrase', 'all', 'any', 'near'),
        default='raw',
        help='Query matching mode: raw syntax, exact phrase, all words, any words, or NEAR',
    )
    search.add_argument(
        '--near-distance',
        type=int,
        default=5,
        help='Word distance for --match near (1..100, default 5)',
    )
    search.add_argument('--limit', type=int, default=10, help='1..50 (default 10)')
    search.add_argument('--offset', type=int, default=0, help='>= 0 (default 0)')
    search.add_argument('--timeout', type=int, default=60, help='1..1800 seconds')
    search.add_argument('--out', help='Write the JSON report to this file')

    document = sub.add_parser(
        'document',
        help='Resolve one official document number without downloading its PDF',
    )
    document.add_argument(
        'external_id',
        help='Official Legal Tools document number, for example ICC-01/04-01/12-1-Red',
    )
    document.add_argument('--timeout', type=int, default=60, help='1..1800 seconds')
    document.add_argument('--out', help='Write the JSON report to this file')

    fetch = sub.add_parser(
        'fetch',
        help='Download and extract one PDF by slug, Legal Tools URL, or externalId',
    )
    fetch.add_argument(
        'locator',
        help='Alphanumeric slug, /doc/<slug>/ URL, or official document number',
    )
    fetch.add_argument('--pdf-out', help='Also write the retrieved PDF here')
    fetch.add_argument('--text-out', help='Also write page-marked plain text here')
    fetch.add_argument(
        '--max-mb',
        type=int,
        default=250,
        help='Maximum PDF size in MiB (1..1000, default 250)',
    )
    fetch.add_argument('--timeout', type=int, default=300, help='1..1800 seconds')
    fetch.add_argument('--out', help='Write the JSON report to this file')

    doctor = sub.add_parser('doctor', help='Check dependencies and public routes')
    doctor.add_argument('--timeout', type=int, default=30, help='1..300 seconds')
    doctor.add_argument(
        '--no-probe',
        action='store_true',
        help='Skip the search API and PDF route probes',
    )
    doctor.add_argument('--out', help='Write the JSON report to this file')

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == 'doctor':
        if not 1 <= args.timeout <= 300:
            parser.error('Bounds: timeout 1..300 seconds')
        payload = adapter.doctor(probe=not args.no_probe, timeout=args.timeout)
        _emit(payload, args.out, {'ok': payload['ok'], 'status': payload['status']})
        return 0 if payload['ok'] else 1

    out = getattr(args, 'out', None)
    try:
        if args.command == 'search':
            if not 1 <= args.limit <= 50 or args.offset < 0:
                parser.error('Bounds: limit 1..50, offset >= 0')
            if not 1 <= args.timeout <= 1800:
                parser.error('Bounds: timeout 1..1800 seconds')
            filters_present = any(
                (
                    args.organisation,
                    args.case_number,
                    args.document_type,
                    args.where_json,
                )
            )
            if not args.query.strip() and not args.all:
                parser.error(
                    'Provide a query or use --all with an explicit filter'
                )
            if not args.query.strip() and not filters_present:
                parser.error(
                    'A filter-only search needs --organisation, --case-number, '
                    '--document-type, or --where-json'
                )
            payload = adapter.search(
                args.query,
                args.limit,
                args.offset,
                organisation=args.organisation,
                case_number=args.case_number,
                document_type=args.document_type,
                where_json=args.where_json,
                match=args.match,
                near_distance=args.near_distance,
                timeout=args.timeout,
            )
            _emit(
                payload,
                out,
                {
                    'status': payload.get('status'),
                    'total': payload.get('total'),
                    'results': len(payload.get('results') or []),
                    'next_offset': payload.get('pagination', {}).get('next_offset'),
                    'warnings': payload.get('warnings', []),
                },
            )
        elif args.command == 'document':
            if not 1 <= args.timeout <= 1800:
                parser.error('Bounds: timeout 1..1800 seconds')
            payload = adapter.document(args.external_id, timeout=args.timeout)
            _emit(
                payload,
                out,
                {
                    'status': payload.get('status'),
                    'external_id': payload.get('external_id'),
                    'warnings': payload.get('warnings', []),
                },
            )
        else:
            if not 1 <= args.timeout <= 1800:
                parser.error('Bounds: timeout 1..1800 seconds')
            if not 1 <= args.max_mb <= 1000:
                parser.error('Bounds: max-mb 1..1000')
            payload = adapter.fetch(
                args.locator,
                timeout=args.timeout,
                max_bytes=args.max_mb * 1024 * 1024,
                pdf_out=args.pdf_out,
                text_out=args.text_out,
            )
            _emit(
                payload,
                out,
                {
                    'status': payload.get('status'),
                    'external_id': payload.get('external_id'),
                    'slug': payload.get('slug'),
                    'pages': payload.get('pdf', {}).get('pages'),
                    'needs_ocr': payload.get('pdf', {}).get('needs_ocr'),
                    'pdf_out': payload.get('pdf', {}).get('pdf_out'),
                    'text_out': payload.get('text_out'),
                    'warnings': payload.get('warnings', []),
                },
            )
    except (adapter.RetrievalError, ValueError) as exc:
        payload = {
            'skill': adapter.SKILL_NAME,
            'schema_version': adapter.SCHEMA_VERSION,
            'command': args.command,
            'status': 'error',
            'retrieved_at': adapter.now_utc(),
            'error': str(exc),
        }
        _emit(payload, out, {'status': 'error', 'error': str(exc)})
        return 1
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
