"""Pure-data contract helpers for the M_LayerBlend height preview."""

from __future__ import annotations

import json
import re
from pathlib import Path


CONTRACT_SCHEMA = "umb.layerblend-height-preview.v1"
CONTRACT_VERSION = 1
OBJECT_CONTRACT_PROPERTY = "umb_layerblend_height_preview_json"
TILING_MASTER = "M_LayerBlend"
HEIGHT_NODE_NAME = "UEUN_Height"
DEFAULT_MAGNITUDE_CM = 8.0
DEFAULT_CENTER = 0.0

_RAW_SCALAR_PATTERN = re.compile(
    r'name:\s*"(?P<name>[^"]+)"\s*,\s*'
    r'association:\s*(?P<association>[A-Za-z_]+)\s*,\s*'
    r'index:\s*(?P<index>-?\d+)\s*}\s*,\s*'
    r'parameter_value:\s*(?P<value>[-+0-9.eE]+)'
)


def is_layerblend_material(material):
    """Accept the explicit tiling contract first, then the legacy name."""
    if material is None:
        return False
    try:
        if str(material.get("tiling_master") or "") == TILING_MASTER:
            return True
    except AttributeError:
        pass
    return str(getattr(material, "name", "")).startswith("M_LayerBlend_")


def report_directory_for_material(material) -> Path | None:
    explicit = str(material.get("tiling_report_directory") or "")
    if explicit:
        return Path(explicit)
    library = getattr(material, "library", None)
    filepath = str(getattr(library, "filepath", "") or "")
    if filepath:
        return Path(filepath).resolve().parent / "reports" / "unreal"
    return None


def latest_report(report_directory: Path) -> Path | None:
    if not report_directory.is_dir():
        return None
    candidates = []
    for path in report_directory.glob("unreal_tiling_*.json"):
        if path.name.endswith(".spec.json") or path.name.endswith(".transaction.json"):
            continue
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            continue
        candidates.append((modified, path.name, path))
    # Usually only the newest file needs to be opened. Failed host reports are
    # skipped until the last complete Unreal snapshot is found.
    for _modified, _name, path in sorted(candidates, reverse=True):
        # A failed host-side audit also leaves a JSON report for diagnostics.
        # It must not eclipse the last usable Unreal snapshot in Blender.
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(data.get("material_instances"), list):
            continue
        return path
    return None


def load_report(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("material_instances"), list):
        raise ValueError(f"Not a Tiling Material Batch Unreal report: {path}")
    return data


def instance_item(report: dict, material_name: str) -> dict | None:
    matches = [
        item
        for item in report.get("material_instances") or []
        if item.get("manifest_material") == material_name
        and item.get("asset_class") == "MaterialInstanceConstant"
    ]
    if not matches:
        return None
    matches.sort(
        key=lambda item: (
            "/MI/LayerBlend/" not in str(item.get("asset_path") or ""),
            str(item.get("asset_path") or "").casefold(),
        )
    )
    return matches[0]


def _snapshot(item: dict) -> dict:
    return item.get("preserved_after") or item.get("preserved_before") or {}


def _structured_scalar_rows(item: dict) -> list[dict]:
    rows = item.get("direct_scalar_overrides_after")
    if rows is None:
        rows = item.get("direct_scalar_overrides_before")
    return list(rows or [])


def _raw_scalar_rows(snapshot: dict) -> list[dict]:
    text = str(snapshot.get("raw_scalar_parameter_values") or "")
    return [
        {
            "parameter": match.group("name"),
            "association": match.group("association"),
            "index": int(match.group("index")),
            "value": float(match.group("value")),
        }
        for match in _RAW_SCALAR_PATTERN.finditer(text)
    ]


def scalar_rows(item: dict) -> list[dict]:
    rows = _structured_scalar_rows(item)
    return rows if rows else _raw_scalar_rows(_snapshot(item))


def _height_strength(item: dict, snapshot: dict) -> float:
    rows = scalar_rows(item)
    layer_rows = [
        row
        for row in rows
        if str(row.get("parameter") or row.get("name") or "") == "Height_Strengh"
        and "layerparameter" in re.sub(
            r"[^a-z]", "", str(row.get("association") or "").casefold()
        )
        and int(row.get("index", -1)) == 0
    ]
    if layer_rows:
        return float(layer_rows[-1]["value"])
    return float((snapshot.get("scalars") or {}).get("Height_Strengh", 1.0))


def height_preview_values(item: dict) -> dict:
    snapshot = _snapshot(item)
    synced = item.get("height_preview_after") or item.get("height_preview_before") or {}
    scaling = synced.get("displacement_scaling") or {}
    has_synced_scaling = "magnitude" in scaling and "center" in scaling
    return {
        "unreal_material_instance": str(item.get("asset_path") or ""),
        "unreal_parent": str(item.get("parent") or snapshot.get("parent") or ""),
        "magnitude_cm": float(scaling.get("magnitude", DEFAULT_MAGNITUDE_CM)),
        "center": float(scaling.get("center", DEFAULT_CENTER)),
        "master_height": float((snapshot.get("scalars") or {}).get("Height", 1.0)),
        "height_strength": _height_strength(item, snapshot),
        "use_vertex_color": bool(
            (snapshot.get("static_switches") or {}).get(
                "VertexColor_HeightBlend", True
            )
        ),
        "scaling_source": "unreal_report" if has_synced_scaling else "compatibility_default",
    }


def build_contract(
    *,
    object_name: str,
    report_path: Path,
    scene_scale_length: float,
    materials: list[dict],
) -> dict:
    scale_length = max(float(scene_scale_length), 1.0e-9)
    return {
        "schema": CONTRACT_SCHEMA,
        "version": CONTRACT_VERSION,
        "object": object_name,
        "source_report": str(report_path),
        "scene_scale_length": scale_length,
        "preview_only": True,
        "export_geometry_unchanged": True,
        "formula": (
            "offset_bu = ((HeightTexture.R * Height_Strengh * Height * "
            "VertexColor.R) - Center) * MagnitudeCm / (100 * SceneScaleLength)"
        ),
        "materials": materials,
    }


def dumps_contract(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def loads_contract(value) -> dict | None:
    if not value:
        return None
    data = json.loads(str(value))
    if data.get("schema") != CONTRACT_SCHEMA:
        raise ValueError(f"Unsupported Unreal Material Bridge schema: {data.get('schema')!r}")
    if int(data.get("version", 0)) != CONTRACT_VERSION:
        raise ValueError(f"Unsupported Unreal Material Bridge version: {data.get('version')!r}")
    return data
