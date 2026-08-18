import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "addons"
    / "hair_tool_unreal_bridge"
    / "layerblend_contract.py"
)
SPEC = importlib.util.spec_from_file_location("umb_layerblend_contract", MODULE_PATH)
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


class FakeMaterial(dict):
    def __init__(self, name, **properties):
        super().__init__(properties)
        self.name = name
        self.library = types.SimpleNamespace(filepath="")


def report_item(*, structured=True):
    item = {
        "manifest_material": "M_LayerBlend_Test",
        "asset_class": "MaterialInstanceConstant",
        "asset_path": "/Game/Material/AssetSurface/MI/LayerBlend/MI_LayerBlend_Test",
        "parent": "/Game/Material/AssetSurface/Master/M_LayerBlend",
        "preserved_after": {
            "scalars": {"Height": 1.25, "Height_Strengh": 0.1},
            "static_switches": {"VertexColor_HeightBlend": True},
            "raw_scalar_parameter_values": (
                '[{parameter_info: {name: "Height_Strengh", '
                "association: LayerParameter, index: 0}, parameter_value: 0.375000}]"
            ),
        },
        "height_preview_after": {
            "displacement_scaling": {"magnitude": 8.0, "center": 0.0}
        },
    }
    if structured:
        item["direct_scalar_overrides_after"] = [
            {
                "parameter": "Height_Strengh",
                "association": "LayerParameter",
                "index": 0,
                "value": 0.5,
            }
        ]
    return item


class TestLayerBlendContract(unittest.TestCase):
    def test_explicit_tiling_contract_and_latest_report_are_preferred(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "unreal_tiling_audit_old.json"
            new = root / "unreal_tiling_verify_new.json"
            failed = root / "unreal_tiling_audit_failed.json"
            old.write_text(json.dumps({"material_instances": []}), encoding="utf-8")
            new.write_text(json.dumps({"material_instances": []}), encoding="utf-8")
            failed.write_text(
                json.dumps({"status": "host_failure", "errors": ["offline"]}),
                encoding="utf-8",
            )
            old.touch()
            new.touch()
            failed.touch()
            material = FakeMaterial(
                "Custom_Display_Name",
                tiling_master="M_LayerBlend",
                tiling_report_directory=str(root),
            )
            self.assertTrue(contract.is_layerblend_material(material))
            self.assertEqual(contract.report_directory_for_material(material), root)
            self.assertEqual(contract.latest_report(root), new)

    def test_exact_unreal_height_values_use_layer_override(self):
        values = contract.height_preview_values(report_item())
        self.assertEqual(values["magnitude_cm"], 8.0)
        self.assertEqual(values["center"], 0.0)
        self.assertEqual(values["master_height"], 1.25)
        self.assertEqual(values["height_strength"], 0.5)
        self.assertTrue(values["use_vertex_color"])
        self.assertEqual(values["scaling_source"], "unreal_report")

    def test_legacy_report_fallback_reads_layer_association_not_global_value(self):
        item = report_item(structured=False)
        item.pop("height_preview_after")
        values = contract.height_preview_values(item)
        self.assertEqual(values["height_strength"], 0.375)
        self.assertEqual(values["magnitude_cm"], 8.0)
        self.assertEqual(values["center"], 0.0)
        self.assertEqual(values["scaling_source"], "compatibility_default")

    def test_contract_declares_preview_only_and_unchanged_export_geometry(self):
        data = contract.build_contract(
            object_name="Wall",
            report_path=Path("report.json"),
            scene_scale_length=1.0,
            materials=[{"material": "M_LayerBlend_Test"}],
        )
        self.assertTrue(data["preview_only"])
        self.assertTrue(data["export_geometry_unchanged"])
        self.assertIn("Center", data["formula"])
        self.assertEqual(contract.loads_contract(contract.dumps_contract(data)), data)


if __name__ == "__main__":
    unittest.main()
