"""Candidate metadata and unapplied ledger conclusions. Declared CLI entry."""
import argparse
import json
import sys

from _state import (IngestError, _stdio_json, append_ledger, load_candidate_raw,
                    progress, update_candidate)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    set_parser = sub.add_parser("set")
    set_parser.add_argument("slug")
    set_parser.add_argument("--payload", required=True)
    set_parser.add_argument("--json", action="store_true")
    status = sub.add_parser("status")
    status.add_argument("slug", nargs="?")
    status.add_argument("--json", action="store_true")
    record = sub.add_parser("record")
    record.add_argument("slug")
    record.add_argument("--verdict", required=True,
                        choices=["official", "author_maintained", "likely", "none", "unavailable"])
    record.add_argument("--evidence", required=True)
    record.add_argument("--repo-url")
    record.add_argument("--commit")
    record.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "status":
            from _entry import status_snapshot
            result = status_snapshot(args.slug)
            _stdio_json(result, args.json)
            return 0
        if args.action == "set":
            cand = update_candidate(args.slug, json.loads(args.payload))
            result = {"slug": args.slug, "fields": sorted(cand)}
            progress(args.slug, "pending", result)
        else:
            cand = load_candidate_raw(args.slug)
            if args.repo_url is not None:
                cand["repo_url"] = args.repo_url
            if args.commit is not None:
                cand["commit_hash"] = args.commit
            result = append_ledger(cand, args.verdict, args.evidence, applied=False)
            progress(args.slug, "none" if args.verdict == "none" else "pending", result)
        _stdio_json(result, args.json)
        return 0
    except (IngestError, ValueError) as exc:
        print(f"needs_human: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
