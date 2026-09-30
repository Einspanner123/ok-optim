"""Read-only hub contract scan; structural checks belong to hubkit.

Stateless: no ledger to reconcile — anomalies are hubkit validator findings.
"""

import argparse
import sys

import _state as state

from hubkit import readers, validators


def scan(model: str | None = None) -> dict:
    rows, error = readers.load_models_csv(state.HUB)
    rep = validators.Report()
    validators.validate(state.HUB, model, rep)
    anomalies = [
        {"kind": "hub_contract", "key": e["model"], "detail": e["message"]} for e in rep.errors
    ]
    if error:
        return {"anomalies": anomalies, "error": error}
    rows = [r for r in rows if "__fields__" not in r]
    return {
        "scanned_hub_rows": len(rows),
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
        **({"model": model} if model else {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", nargs="?", help="只扫描指定模型")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = scan(args.model)
    state._stdio_json(result, args.json)
    return 3 if result["anomalies"] else 0


if __name__ == "__main__":
    sys.exit(main())
