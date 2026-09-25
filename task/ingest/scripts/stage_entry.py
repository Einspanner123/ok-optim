"""Render and validate one candidate without changing the formal hub."""
import argparse
import sys
from _entry import stage
from _state import IngestError, _stdio_json
from hubkit.render import ContractError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = stage(args.slug)
        _stdio_json(result, args.json)
        return 0 if result["ok"] else 3
    except (IngestError, ContractError, ValueError) as exc:
        print(f"needs_human: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
