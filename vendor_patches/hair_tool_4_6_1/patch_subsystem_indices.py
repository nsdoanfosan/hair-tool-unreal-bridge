"""Read/write the full Hair Tool subsystem index instead of its final digit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import patch_sort_hair_mods as core

RELATIVE_SOURCE = Path("hair_tool_props.py")
ORIGINAL_SHA256 = "cea0944fa4a4d3c414e51df87cb3344ce2e5fce7317617b63cff23d084565f63"
PATCHED_SHA256 = "019ec623ec483b8c39e4d027053fde2c95edef8b0f1d3c26d7629eb90056fa59"
ORIGINAL_LINE = b"mod_idx = int(self.path_from_id()[-2])"
REPLACEMENT_LINE = b'mod_idx = int(self.path_from_id().rsplit("[", 1)[1].split("]", 1)[0])'


def build_candidate(original):
    if core.sha256(original) != ORIGINAL_SHA256:
        raise ValueError("Source hash differs from the audited Hair Tool 4.6.1 properties file")
    if original.count(ORIGINAL_LINE) != 2:
        raise ValueError("Expected exactly the audited get_node_val/set_node_val index lines")
    candidate = original.replace(ORIGINAL_LINE, REPLACEMENT_LINE)
    compile(candidate, str(RELATIVE_SOURCE), "exec")
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "apply"))
    parser.add_argument("--addon-dir", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    args = parser.parse_args()
    common = {"relative_source": RELATIVE_SOURCE, "patched_hash": PATCHED_SHA256}
    if args.mode == "prepare":
        receipt = core.prepare(args.addon_dir, args.candidate_dir, builder=build_candidate, **common)
    else:
        receipt = core.apply(args.addon_dir, args.candidate_dir, original_hash=ORIGINAL_SHA256, **common)
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
