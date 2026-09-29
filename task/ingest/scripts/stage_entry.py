"""Render and validate one entry without changing the formal hub.

Stateless: the entry payload is passed via --payload; materials are read from
the run workdir (paper/, repo/); staged files land in <workdir>/staged/.
"""
import argparse
import json
import sys

from _entry import stage
from _state import IngestError, _stdio_json
from hubkit.render import ContractError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True, help="条目字段 JSON（九个必需字段）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        payload = json.loads(args.payload)
        result = stage(payload)
        _stdio_json(result, args.json)
        return 0 if result["ok"] else 3
    except (IngestError, ContractError, ValueError, json.JSONDecodeError) as exc:
        print(f"needs_human: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
