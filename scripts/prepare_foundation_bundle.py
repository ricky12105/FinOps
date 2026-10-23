"""Package a verified local financial foundation version into an immutable ZIP bundle for Fabric.

Verifies the local candidate version, checks SHA-256 digests and byte counts of all referenced
source files and the `finops_foundation.py` runner, writes deterministic ZIP archive entries,
and emits a signed `bundle-<bundleId>.json` receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from finops_foundation import ADAPTER_VERSION, digest, encode, immutable_json, read_json, verified_version


def prepare(selection: Path | str, root: Path | str, output: Path | str) -> dict[str, Any]:
    """Build and verify a deterministic deployment bundle from a validated foundation candidate."""
    selection, root, output = Path(selection).resolve(), Path(root).resolve(), Path(output).resolve()
    config = read_json(selection)
    version = hashlib.sha256(encode({"adapterVersion": ADAPTER_VERSION, "selection": config})).hexdigest()
    verified_version(root, version)
    validation = read_json(root / "versions" / version / "validation.json")
    runner = Path(__file__).with_name("finops_foundation.py")
    files = {selection.name: selection, runner.name: runner}
    references = [part for snapshot in config["snapshots"] for part in snapshot["parts"]]
    references += config.get("evidence", [])
    for reference in references:
        name = reference["path"]
        if Path(name).name != name or name in {selection.name, runner.name, "bundle.json"}:
            raise ValueError("Bundle requires unambiguous flat source filenames")
        source = selection.parent / name
        if source.stat().st_size != reference["bytes"] or digest(source) != reference["sha256"].lower():
            raise ValueError("Bundle source hash or byte count mismatch")
        files[name] = source
    descriptor = {
        "selectionFile": selection.name,
        "expectedVersionId": version,
        "expectedRows": validation["rows"],
        "expectedMonths": len(config["snapshots"]),
        "expectedExactBilledCost": validation["exactBilledCost"],
        "coreSha256": digest(runner),
        "files": {name: digest(source) for name, source in sorted(files.items())},
    }
    bundle_id = hashlib.sha256(encode(descriptor)).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    bundle = output / f"bundle-{bundle_id}.zip"
    if not bundle.exists():
        with ZipFile(bundle, "x", compression=ZIP_DEFLATED) as archive:
            for name, payload in [(name, source.read_bytes()) for name, source in sorted(files.items())] + [("bundle.json", encode(descriptor))]:
                entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.compress_type = ZIP_DEFLATED
                archive.writestr(entry, payload)
    with ZipFile(bundle) as archive:
        if set(archive.namelist()) != set(files) | {"bundle.json"}:
            raise ValueError("Bundle member mismatch")
        if archive.read("bundle.json") != encode(descriptor):
            raise ValueError("Bundle descriptor mismatch")
        for name, expected in descriptor["files"].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError("Bundle member hash mismatch")
    receipt = {"bundleId": bundle_id, "bundleFile": bundle.name, "bundleSha256": digest(bundle), "bundleBytes": bundle.stat().st_size, **descriptor}
    immutable_json(output / f"bundle-{bundle_id}.json", receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Package a verified local candidate for isolated Fabric replay")
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    result = prepare(arguments.selection, arguments.root, arguments.output)
    print(json.dumps({name: value for name, value in result.items() if name != "files"}, indent=2))