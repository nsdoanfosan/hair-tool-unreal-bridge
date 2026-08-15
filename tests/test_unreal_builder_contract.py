import ast
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "addons" / "hair_tool_unreal_bridge" / "schema.py"
BUILDER_PATH = ROOT / "unreal" / "build_haircards_master.py"


def load_schema():
    spec = importlib.util.spec_from_file_location("htue_schema", SCHEMA_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestUnrealBuilderContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = load_schema()
        cls.source = BUILDER_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def _literal_parameter_names(self, function_name):
        names = set()
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != function_name:
                continue
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                if isinstance(node.args[1].value, str):
                    names.add(node.args[1].value)
        return names

    def test_every_blender_vector_parameter_exists_in_unreal_master(self):
        self.assertTrue(
            set(self.schema.VECTOR_FIELDS.values()).issubset(
                self._literal_parameter_names("vector")
            )
        )

    def test_every_blender_scalar_parameter_exists_in_unreal_master(self):
        self.assertTrue(
            set(self.schema.SCALAR_FIELDS.values()).issubset(
                self._literal_parameter_names("scalar")
            )
        )

    def test_blend_and_payload_contract_are_explicit(self):
        for input_name in ("base", "blend", "t", "blendType"):
            self.assertIn(f'function_call, "{input_name}"', self.source)
        self.assertIn(
            'BLEND_FUNCTION = "/Game/CC_Shaders/HairShader/Source/Functions/blendFunc"',
            self.source,
        )
        self.assertIn("SYSTEM_COLOR_UV_INDEX = 1", self.source)
        self.assertIn("RFAOS_UV_TAG = 6.0", self.source)
        self.assertIn("65535.0", self.source)
        self.assertIn("256.0", self.source)

    def test_contract_v3_uses_direct_system_color_rgb(self):
        self.assertIn("sync_parameters = set", self.source)
        self.assertIn("param not in sync_parameters", self.source)
        self.assertIn("system_color_rgb", self.source)
        self.assertNotIn('vector(material, "System Color 01"', self.source)
        self.assertNotIn('vector(material, "System Color 02"', self.source)
        self.assertNotIn('scalar(material, "System Mask Contrast"', self.source)

    def test_material_instance_groups_mirror_the_blender_panel(self):
        expected_groups = (
            "01 | HTUE SYNC - Textures",
            "02 | HTUE SYNC - Base",
            "03 | HTUE SYNC - System Color",
            "04 | HTUE SYNC - Root",
            "05 | HTUE SYNC - Tip",
            "06 | HTUE SYNC - ID",
            "07 | HTUE SYNC - Depth",
            "08 | HTUE SYNC - AO & Roughness",
            "90 | UNREAL ONLY - UV",
            "91 | UNREAL ONLY - Surface & Flow",
            "92 | UNREAL ONLY - Opacity",
        )
        for group in expected_groups:
            self.assertIn(group, self.source)

    def test_unreal_color_stack_matches_blender_layer_order(self):
        order = (
            "system_color_result = blend_stage",
            "root_result = blend_stage",
            "hair_tool_color = blend_stage",
            "id_result = blend_stage",
            "depth_result = blend_stage",
            "final_color = blend_stage",
        )
        positions = [self.source.index(token) for token in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(
            "root_saturate = lerp(material, one, root_saturate, root_range_enabled",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
