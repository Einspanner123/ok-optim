"""The sole formal hub write entry; records successful application itself."""
import argparse
import sys
from _entry import apply
from _state import IngestError, _stdio_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.confirm:
        print("needs_human: apply 需要用户授权并传入 --confirm", file=sys.stderr)
        return 2
    try:
        _stdio_json(apply(args.slug), args.json)
        return 0
    except (IngestError, ValueError) as exc:
        print(f"needs_human: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
