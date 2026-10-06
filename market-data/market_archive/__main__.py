import argparse
import json
import sys

from .collector import run
from .demo import demo
from .verify import verify


def main(argv=None):
    parser = argparse.ArgumentParser(description='Market-data archive; demo and verify are fully offline.')
    commands = parser.add_subparsers(dest='command', required=True)
    example = commands.add_parser('demo', help='run deterministic synthetic fixtures through the real pipeline')
    example.add_argument('--output', required=True)
    audit = commands.add_parser('verify', help='read-only hash and SQLite evidence reconciliation')
    audit.add_argument('--root', required=True)
    collect = commands.add_parser('collect', help='explicit opt-in public GET collection for supplied market IDs')
    collect.add_argument('--manifest', required=True)
    collect.add_argument('--root', required=True)
    collect.add_argument('--live', action='store_true', help='acknowledge that this command makes public HTTP reads')
    collect.add_argument('--max-pages', type=int, default=2)
    collect.add_argument('--no-books', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'demo':
            report = demo(args.output)
            summary = {k: report[k] for k in ('source_label', 'executed_offline', 'exercise_passed', 'assertions', 'archive_verification')}
            status = 0 if report['exercise_passed'] else 2
        elif args.command == 'verify':
            summary = verify(args.root)
            status = 0 if summary['passed'] else 2
        else:
            if not args.live:
                parser.error('collect requires --live; demo is the offline entry point')
            with open(args.manifest, encoding='utf-8') as handle:
                manifest = json.load(handle)
            summary = run(args.root, manifest, max_pages=args.max_pages, include_books=not args.no_books)
            status = 0 if summary['collection_complete_without_errors'] else 2
        print(json.dumps(summary, indent=2, sort_keys=True))
        return status
    except (ValueError, OSError, RuntimeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
