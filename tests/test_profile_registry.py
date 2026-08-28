import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "addons"
    / "hair_tool_unreal_bridge"
    / "profile_registry.py"
)
SPEC = importlib.util.spec_from_file_location("htue_profile_registry", MODULE_PATH)
profile_registry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile_registry)


class TestProfileRegistry(unittest.TestCase):
    def test_roundtrip_uses_stable_schema_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            values = {"base_color": [0.1, 0.2, 0.3, 1.0], "root_mix": 0.5}
            data = profile_registry.empty_registry()
            record = profile_registry.profile_record("profile-a", "Hair", 1, values)
            data["profiles"]["profile-a"] = record
            with profile_registry.registry_lock(path):
                profile_registry.save_registry(path, data)

            loaded = profile_registry.load_registry(path)
            self.assertEqual(loaded, data)
            self.assertEqual(record["content_hash"], profile_registry.values_hash(values))
            self.assertEqual(
                record["content_hash"],
                profile_registry.values_hash(json.loads(json.dumps(values))),
            )

    def test_stale_child_merges_only_its_unchanged_remote_fields(self):
        base = {"base_color": [0.1, 0.1, 0.1, 1.0], "root_mix": 0.5}
        remote = {"base_color": [0.2, 0.2, 0.2, 1.0], "root_mix": 0.5}
        local = {"base_color": [0.1, 0.1, 0.1, 1.0], "root_mix": 0.8}
        merged = profile_registry.merge_changed_values(
            remote,
            base,
            local,
            {"root_mix"},
        )
        self.assertEqual(merged["base_color"], remote["base_color"])
        self.assertEqual(merged["root_mix"], 0.8)

    def test_same_field_concurrent_edit_is_rejected(self):
        base = {"root_mix": 0.5}
        remote = {"root_mix": 0.6}
        local = {"root_mix": 0.8}
        with self.assertRaises(profile_registry.ProfileConflictError) as raised:
            profile_registry.merge_changed_values(
                remote,
                base,
                local,
                {"root_mix"},
            )
        self.assertEqual(raised.exception.fields, ("root_mix",))

    def test_live_lock_rejects_a_second_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            with profile_registry.registry_lock(path):
                with self.assertRaises(profile_registry.RegistryLockError):
                    with profile_registry.registry_lock(path):
                        pass


if __name__ == "__main__":
    unittest.main()
