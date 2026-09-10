"""Prepare/apply one hash-pinned Hair Tool 4.6.1 source fix; never touch preferences."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

VERSION = (4, 6, 1)
RELATIVE_SOURCE = Path("hair_baking") / "hair_nodes_tree.py"
ORIGINAL_SHA256 = "bca9201a3d002b7b0e94c8ef2028b8baad2bfe964ac240273c888d54b4bd527f"
PATCHED_SHA256 = "6d5371872bb89a10638c3913874fdc881ca8ff69ba8e1e294b100629b059b6b7"
REPLACEMENT = Path(__file__).with_name("sort_hair_mods.pyfrag")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_version(addon_dir):
    tree = ast.parse((addon_dir / "__init__.py").read_bytes())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "bl_info"
            for target in node.targets
        ):
            return tuple(ast.literal_eval(node.value)["version"])
    raise ValueError("Hair Tool bl_info.version was not found")


def build_candidate(original):
    """Replace only the audited method, preserving the vendor file's newlines."""
    if sha256(original) != ORIGINAL_SHA256:
        raise ValueError("Source hash differs from the audited Hair Tool 4.6.1 file")
    tree = ast.parse(original)
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "HairSystemTree")
    method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "sort_hair_mods")
    newline = b"\r\n" if b"\r\n" in original else b"\n"
    replacement = REPLACEMENT.read_bytes().replace(b"\r\n", b"\n").rstrip(b"\n").replace(b"\n", newline) + newline
    lines = original.splitlines(keepends=True)
    result = b"".join(lines[:method.lineno - 1]) + replacement + b"".join(lines[method.end_lineno:])
    compile(result, str(RELATIVE_SOURCE), "exec")
    return result


def prepare(addon_dir, candidate_dir, *, relative_source=RELATIVE_SOURCE,
            patched_hash=PATCHED_SHA256, builder=build_candidate):
    addon_dir = Path(addon_dir).resolve()
    candidate_dir = Path(candidate_dir).resolve()
    if candidate_dir == addon_dir or addon_dir in candidate_dir.parents:
        raise ValueError("Candidate directory must be outside the installed add-on")
    if read_version(addon_dir) != VERSION:
        raise ValueError("This patch supports only the audited Hair Tool 4.6.1 version")
    source = addon_dir / relative_source
    original = source.read_bytes()
    source_hash = sha256(original)
    if source_hash == patched_hash:
        candidate = original
    else:
        candidate = builder(original)
    if sha256(candidate) != patched_hash:
        raise ValueError("Candidate hash differs from the reviewed patch")
    target = candidate_dir / relative_source
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(candidate)
    receipt = {"status": "prepared", "version": VERSION, "source": str(source), "source_sha256": source_hash, "candidate": str(target), "candidate_sha256": sha256(target.read_bytes())}
    (candidate_dir / (relative_source.stem + "_patch_receipt.json")).write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def apply(addon_dir, candidate_dir, *, relative_source=RELATIVE_SOURCE,
          original_hash=ORIGINAL_SHA256, patched_hash=PATCHED_SHA256):
    addon_dir = Path(addon_dir).resolve()
    source = addon_dir / relative_source
    candidate = Path(candidate_dir).resolve() / relative_source
    if read_version(addon_dir) != VERSION:
        raise ValueError("This patch supports only the audited Hair Tool 4.6.1 version")
    candidate_bytes = candidate.read_bytes()
    if sha256(candidate_bytes) != patched_hash:
        raise ValueError("Candidate hash differs from the reviewed patch")
    original = source.read_bytes()
    source_hash = sha256(original)
    if source_hash == patched_hash:
        return {"status": "already-applied", "source": str(source), "sha256": source_hash}
    if source_hash != original_hash:
        raise ValueError("Installed file changed; refusing to overwrite an unaudited source")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = source.with_name(source.name + ".pre-htue-fix-" + stamp)
    with backup.open("xb") as stream:
        stream.write(original)
    if sha256(backup.read_bytes()) != source_hash:
        raise ValueError("Backup hash verification failed; installed source was not changed")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=source.parent, prefix=".sort-patch-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(candidate_bytes)
        # Recheck the installed bytes immediately before the atomic replacement.
        if sha256(source.read_bytes()) != source_hash:
            raise ValueError("Installed source changed during patch preparation")
        os.replace(temporary, source)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    result_hash = sha256(source.read_bytes())
    if result_hash != patched_hash:
        raise ValueError("Installed file verification failed; restore the verified backup")
    return {"status": "applied", "source": str(source), "backup": str(backup), "backup_sha256": source_hash, "sha256": result_hash}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "apply"))
    parser.add_argument("--addon-dir", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    arguments = parser.parse_args()
    operation = prepare if arguments.mode == "prepare" else apply
    print(json.dumps(operation(arguments.addon_dir, arguments.candidate_dir), indent=2))


if __name__ == "__main__":
    main()
