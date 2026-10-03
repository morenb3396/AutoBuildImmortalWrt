import json
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.load((ROOT / ".github/workflows/build-custom-software.yml").read_text(
            encoding="utf-8"), Loader=yaml.BaseLoader)

    def test_dropdown_matches_device_profiles(self):
        devices = json.loads((ROOT / "custom-builder/config/devices.json").read_text(encoding="utf-8"))
        choices = self.workflow["on"]["workflow_dispatch"]["inputs"]["device"]["options"]
        self.assertEqual(set(choices), set(devices) | {"其他机型"})

    def test_checkboxes_reach_builder_and_use_correct_artifact_paths(self):
        inputs = self.workflow["on"]["workflow_dispatch"]["inputs"]
        steps = self.workflow["jobs"]["build"]["steps"]
        build_step = next(s for s in steps if s.get("run", "").startswith("python3 custom-builder/scripts/build.py"))
        for feature, variable in (("openclash", "ADD_OPENCLASH"), ("ddns_go", "ADD_DDNS_GO"),
                                  ("upnp", "ADD_UPNP"), ("argon", "ADD_ARGON")):
            self.assertEqual(inputs[feature]["type"], "boolean")
            self.assertEqual(build_step["env"][variable], "${{ inputs.%s }}" % feature)
        upload_paths = [s["with"]["path"] for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertTrue(all(p.startswith("custom-builder/build/") for p in upload_paths))
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(len(inputs), 9)


if __name__ == "__main__":
    unittest.main()
