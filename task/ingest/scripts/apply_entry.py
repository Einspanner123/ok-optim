"""The sole formal hub write entry; applies the exact staged files under the run workdir."""
import argparse
import sys

from _entry import apply
from _state import IngestError, _stdio_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.confirm:
        print("needs_human: apply 需要用户授权并传入 --confirm", file=sys.stderr)
        return 2
    try:
        _stdio_json(apply(), args.json)
        return 0
    except (IngestError, ValueError) as exc:
        print(f"needs_human: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
