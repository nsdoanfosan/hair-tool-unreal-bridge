import importlib.util
from pathlib import Path
import types
import unittest


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "addons"
    / "hair_tool_unreal_bridge"
    / "schema.py"
)
SPEC = importlib.util.spec_from_file_location("htue_schema", SCHEMA_PATH)
schema = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(schema)


class FakeSettings(types.SimpleNamespace):
    pass


def settings():
    values = {
        "texture_root": str(schema.DEFAULT_TEXTURE_ROOT),
        "texture_set": "Hair_Long_01",
    }
    for field in schema.VECTOR_FIELDS:
        values[field] = (0.1, 0.2, 0.3, 1.0)
    for field in schema.SCALAR_FIELDS:
        values[field] = "OVERLAY" if field in schema.BLEND_FIELDS else 0.5
    return FakeSettings(**values)


class TestHairToolUnrealBridgeSchema(unittest.TestCase):
    def test_contract_uses_exact_hairtool_mi_path_and_pipeline_ownership(self):
        data = schema.build_contract("M_HT_Default_Material_01", settings())
        self.assertEqual(
            data["material_instance_path"],
            "/Game/Material/HairTool/MI/MI_HT_Default_Material_01",
        )
        self.assertFalse(data["create_if_missing"])
        self.assertTrue(data["manage_existing_material_instance"])
        self.assertEqual(data["material_instance_ownership"], "pipeline")

    def test_blend_modes_are_exported_with_shared_numeric_legend(self):
        data = schema.build_contract("M_HT_Default_Material_01", settings())
        scalars = data["hair_tool"]["scalar_parameters"]
        for parameter in (
            "Root Blend Mode",
            "Tip Blend Mode",
            "ID Blend Mode",
            "Depth Blend Mode",
            "System Blend Mode",
            "AO Blend Mode",
        ):
            self.assertEqual(scalars[parameter], 2.0)

    def test_system_color_is_per_vertex_rgb_not_alpha_selected_parameters(self):
        data = schema.build_contract("M_HT_Default_Material_01", settings())
        hair = data["hair_tool"]
        self.assertNotIn("System Color 01", hair["vector_parameters"])
        self.assertNotIn("System Color 02", hair["vector_parameters"])
        self.assertNotIn("System Mask Contrast", hair["scalar_parameters"])
        self.assertFalse(hair["vertex_uv_payload"]["system_color_alpha_used"])
        self.assertEqual(
            hair["vertex_uv_payload"]["system_color_source"],
            "evaluated SystemColor.RGB",
        )
        self.assertIn("SystemColor.RG", hair["vertex_uv_payload"]["UV1.RG"])
        self.assertIn("SystemColor.B", hair["vertex_uv_payload"]["UV3.G"])

    def test_vertex_and_texture_sources_are_both_declared(self):
        data = schema.build_contract("M_HT_Default_Material_01", settings())
        self.assertEqual(data["hair_tool"]["vertex_color"]["G"], "Factor / Root-Tip vertex source")
        self.assertIn("IRD Map.G", data["hair_tool"]["texture_channels"])
        self.assertIn("IRD Map.B", data["hair_tool"]["texture_channels"])
        self.assertIn("ORM Map.R", data["hair_tool"]["texture_channels"])
        self.assertEqual(
            {entry["param"] for entry in data["textures"]},
            {"Flow Map", "IRD Map", "ORM Map", "Opacity Map"},
        )

    def test_layer_order_and_ownership_are_explicit(self):
        hair = schema.build_contract("M_HT_Default_Material_01", settings())["hair_tool"]
        self.assertEqual(
            hair["layer_order"],
            ["Base", "System", "Root", "Tip", "ID", "Depth", "AO"],
        )
        self.assertEqual(
            hair["source_ownership"]["hair_tool"],
            "evaluated deformer attributes only",
        )
        self.assertEqual(
            hair["source_ownership"]["legacy_hair_shader_blending"],
            "ignored",
        )

    def test_contract_validates_and_roundtrips(self):
        original = schema.build_contract("M_HT_Default_Material_01", settings())
        encoded = schema.dumps_contract("M_HT_Default_Material_01", settings())
        decoded = schema.loads_contract(encoded)
        self.assertEqual(decoded, original)
        self.assertEqual(schema.validate_contract(decoded), [])


if __name__ == "__main__":
    unittest.main()
