"""Read-only ledger/hub reconciliation; structural checks belong to hubkit."""
import argparse
import sys
import _state as state
from hubkit import readers, validators


def scan(key: str | None = None) -> dict:
    rows, error = readers.load_models_csv(state.HUB)
    rep = validators.Report()
    validators.validate(state.HUB, None, rep)
    anomalies = [{"kind": "hub_contract", "key": e["model"], "detail": e["message"]} for e in rep.errors]
    if error:
        return {"anomalies": anomalies, "error": error}
    rows = [r for r in rows if "__fields__" not in r]
    ledger = state.latest_by_key(state.load_ledger())
    by_title = {row["paper_title"]: row for row in rows}
    for k, rec in ledger.items():
        row = by_title.get(rec["title"])
        if rec["verdict"] == "unavailable":
            anomalies.append({"kind": "unavailable", "key": k, "detail": rec.get("evidence", "")})
        elif rec["verdict"] != "none" and (not rec.get("applied") or row is None):
            anomalies.append({"kind": "not_applied", "key": k, "detail": rec["title"]})
        elif row is not None and rec.get("applied"):
            current = {**rec, "repo_url": row["repo_url"], "commit": row["commit_hash"]}
            if state.paper_hash(current) != rec["paper_hash"]:
                anomalies.append({"kind": "hash_mismatch", "key": k, "detail": rec["title"]})
    known = {rec["title"] for rec in ledger.values()}
    for row in rows:
        if row["paper_title"] not in known:
            anomalies.append({"kind": "missing_in_ledger", "key": row["model_name"],
                              "detail": "存量条目无台账，需人工判断"})
    if key:
        rec = ledger.get(key)
        model = by_title.get(rec["title"], {}).get("model_name") if rec else key
        anomalies = [a for a in anomalies if a["key"] in {key, model}]
    return {"scanned_hub_rows": len(rows), "ledger_keys": len(ledger),
            "anomaly_count": len(anomalies), "anomalies": anomalies,
            **({"key": key, "record": ledger.get(key)} if key else {})}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key", nargs="?")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = scan(args.key)
    state._stdio_json(result, args.json)
    return 3 if result["anomalies"] else 0


if __name__ == "__main__":
    sys.exit(main())
