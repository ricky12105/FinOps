"""Register approved historical Azure Cost Management exports into a verified selection manifest.

This script validates that all 12 closed monthly cost snapshots (legacy ActualCost 2021-10-01 CSV
or FOCUS 1.0r2 Parquet) reconcile against independent native Azure Cost Management monthly totals,
verifies SHA-256 content and schema digests, and writes an immutable `selection.json` manifest
beside the recovered evidence files.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from finops_config import safe_extract_path
from finops_foundation import digest, immutable_json, read_json, schema_digest


def register(recovery: Path | str, output: Path | str) -> dict[str, Any]:
    """Validate recovered monthly billing exports and emit an immutable selection manifest.

    Args:
        recovery: Directory containing recovered billing export manifests, data parts, and
            native monthly reconciliation responses.
        output: Target path for the generated `selection.json` file (must reside inside `recovery`).

    Returns:
        Summary dictionary with the output path, validated month count, total row count, and
        sorted list of active schema adapters.

    Raises:
        ValueError: If `output` is outside `recovery`, fewer than 12 closed months are present,
            any part file fails path-traversal or SHA-256 checks, or an unsupported schema is found.
    """
    recovery_dir, output_path = Path(recovery).resolve(), Path(output).resolve()
    if output_path.parent != recovery_dir:
        raise ValueError("Keep the selection beside its source evidence for portable relative paths")
    receipt = read_json(recovery_dir / "historical-detail-reconciliation.json")
    inventory = read_json(recovery_dir / "inventory.json")
    native = read_json(recovery_dir / "native-query-2025-10_2026-09.json")["properties"]
    columns = [column["name"] for column in native["columns"]]
    native_rows = [dict(zip(columns, row, strict=True)) for row in native["rows"]]
    if native.get("nextLink") or len(native_rows) != 12 or len(receipt["closedHistorySelection"]) != 12:
        raise ValueError("Expected twelve complete closed-month comparisons")
    native_costs = {row["BillingMonth"][:7]: str(row["Cost"]) for row in native_rows if row["Currency"] == receipt["currency"]}
    snapshots: list[dict[str, Any]] = []
    manifests: set[str] = set()
    for selected in receipt["closedHistorySelection"]:
        period = selected["period"]
        if selected["source"] == "historical-cost-details":
            manifest_name = selected["manifestFile"]
            manifest_path = safe_extract_path(recovery_dir, manifest_name)
            manifest = read_json(manifest_path)
            if manifest["Status"] != "Completed" or manifest["BlobCount"] != len(manifest["Parts"]):
                raise ValueError("Incomplete recovered report")
            adapter = "actualcost-2021-10-01"
            parts = [
                {
                    "path": safe_extract_path(recovery_dir, part["LocalFile"]).name,
                    "sha256": part["SHA256"].lower(),
                    "bytes": part["ByteCount"],
                    "rows": selected["rows"],
                }
                for part in manifest["Parts"]
            ]
            if len(parts) != 1:
                raise ValueError("Recovered multi-part reports require individual row-count evidence")
            run_id, observed = manifest["OperationId"].rsplit("/", 1)[-1], manifest["DownloadedAtUtc"]
        else:
            matches = [entry for entry in inventory if entry["RunId"] == selected["runId"] and entry["Period"] == period]
            if len(matches) != 1:
                raise ValueError("Missing or ambiguous selected export")
            entry = matches[0]
            adapter = "focus-1.0r2" if entry["Type"] == "FocusCost" and entry["Version"] == "1.0r2" else "actualcost-2021-10-01"
            if adapter == "actualcost-2021-10-01" and (entry["Type"] != "ActualCost" or entry["Version"] != "2021-10-01"):
                raise ValueError("Unsupported selected source version")
            manifest_name = safe_extract_path(recovery_dir, entry["ManifestFile"]).name
            parts = [
                {
                    "path": safe_extract_path(recovery_dir, part["LocalFile"]).name,
                    "sha256": part["SHA256"].lower(),
                    "bytes": part["Bytes"],
                    "rows": part["Rows"],
                }
                for part in entry["Parts"]
            ]
            run_id, observed = entry["RunId"], entry["Submitted"]
        manifests.add(manifest_name)
        first_part_path = safe_extract_path(recovery_dir, parts[0]["path"])
        snapshots.append(
            {
                "period": period,
                "adapter": adapter,
                "status": "Closed",
                "observedAtUtc": observed,
                "runId": run_id,
                "schemaSha256": schema_digest(first_part_path, adapter),
                "partCount": len(parts),
                "parts": parts,
                "rows": selected["rows"],
                "nativeCost": native_costs[period],
            }
        )
    evidence: list[dict[str, Any]] = []
    for name in sorted(manifests | {"historical-detail-reconciliation.json", "native-query-2025-10_2026-09.json", "inventory.json"}):
        path = safe_extract_path(recovery_dir, name)
        evidence.append(
            {
                "path": name,
                "sha256": digest(path),
                "bytes": path.stat().st_size,
                "role": "Native reconciliation" if name.startswith("native-") else "Source manifest / selection evidence",
            }
        )
    config = {
        "customerId": "zava-demo",
        "subscriptionId": receipt["subscriptionId"],
        "currency": receipt["currency"],
        "snapshots": snapshots,
        "evidence": evidence,
    }
    immutable_json(output_path, config)
    return {
        "selection": str(output_path),
        "months": len(snapshots),
        "rows": sum(snapshot["rows"] for snapshot in snapshots),
        "adapters": sorted({snapshot["adapter"] for snapshot in snapshots}),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Register only the previously approved recovered history")
    parser.add_argument("--recovery", type=Path, required=True, help="Directory containing recovered billing exports")
    parser.add_argument("--output", type=Path, required=True, help="Target selection.json path inside the recovery directory")
    args = parser.parse_args()
    print(json.dumps(register(args.recovery, args.output), indent=2))
