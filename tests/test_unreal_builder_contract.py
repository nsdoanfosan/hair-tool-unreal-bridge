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
        self.assertIn("RFAOS_UV_TAG = 4.0", self.source)
        self.assertIn("65535.0", self.source)
        self.assertIn("256.0", self.source)

    def test_contract_v2_can_override_legacy_system_color_preservation(self):
        self.assertIn("sync_parameters = set", self.source)
        self.assertIn("param not in sync_parameters", self.source)


if __name__ == "__main__":
    unittest.main()
