import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("firmware_build", ROOT / "scripts/build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.devices = json.loads((ROOT / "config/devices.json").read_text(encoding="utf-8"))

    def test_machine_selection_only_sets_official_target_and_profile(self):
        r5s = build.resolve_settings({"DEVICE": "NanoPi R5S"}, self.devices)
        r4s = build.resolve_settings({"DEVICE": "NanoPi R4S"}, self.devices)
        self.assertEqual(r5s["profile"], "friendlyarm_nanopi-r5s")
        self.assertEqual(r4s["profile"], "friendlyarm_nanopi-r4s")
        self.assertNotIn("kmod-r8125", build.selected_packages(r5s))
        self.assertNotIn("kmod-r8125", build.selected_packages(r4s))
        self.assertIsNone(r5s["rootfs_size"])

    def test_custom_device_preserves_official_partition_size(self):
        settings = build.resolve_settings({"DEVICE": "其他机型", "CUSTOM_TARGET": "mediatek/filogic",
                                           "CUSTOM_PROFILE": "example_router", "ADD_ARGON": "false"}, self.devices)
        self.assertIsNone(settings["rootfs_size"])
        self.assertNotIn("argon", settings["selected_features"])

    def test_all_sixteen_feature_combinations_add_only_selected_groups(self):
        features = list(build.FEATURES)
        for mask in range(16):
            env = {"ADD_" + name.upper(): str(bool(mask & (1 << index))).lower()
                   for index, name in enumerate(features)}
            settings = build.resolve_settings(env, self.devices)
            packages = build.selected_packages(settings)
            with self.subTest(mask=mask):
                for index, name in enumerate(features):
                    primary = {"openclash": "luci-app-openclash", "ddns_go": "ddns-go",
                               "upnp": "miniupnpd-nftables", "argon": "luci-theme-argon"}[name]
                    self.assertEqual(primary in packages, bool(mask & (1 << index)))
                self.assertEqual("-dnsmasq" in packages, "openclash" in settings["selected_features"])
                self.assertEqual(len(packages), len(set(packages)))
                self.assertTrue(build.required_packages(settings).issubset(set(packages)))
                self.assertNotIn("autocore", packages)
                self.assertNotIn("default-settings-chn", packages)
                if mask == 0:
                    self.assertEqual(packages, [])

    def test_invalid_inputs_cannot_enter_make_arguments_or_download_paths(self):
        for env in ({"VERSION": "../../evil"}, {"VERSION": "23.05.6"},
                    {"DEVICE": "其他机型", "CUSTOM_TARGET": "../rockchip/armv8", "CUSTOM_PROFILE": "r5s"},
                    {"DEVICE": "其他机型", "CUSTOM_TARGET": "rockchip/armv8", "CUSTOM_PROFILE": "$(touch x)"},
                    {"ROOTFS_SIZE": "0"}, {"ROOTFS_SIZE": "512\nFILES=x"}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                build.resolve_settings(env, self.devices)

    def test_wrong_version_target_and_profile_are_rejected(self):
        settings = build.resolve_settings({}, self.devices)
        metadata = {"version_number": "25.12.2", "target": "rockchip/armv8",
                    "profiles": {"friendlyarm_nanopi-r5s": {"device_packages": ["kmod-r8125"]}}}
        self.assertEqual(build.check_metadata(settings, metadata)["device_packages"], ["kmod-r8125"])
        for key, value in (("version_number", "24.10.6"), ("target", "x86/64"), ("profiles", {})):
            invalid = dict(metadata, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                build.check_metadata(settings, invalid)

    def test_correct_imagebuilder_and_checksum_are_required(self):
        settings = build.resolve_settings({}, self.devices)
        name = "immortalwrt-imagebuilder-25.12.2-rockchip-armv8.Linux-x86_64.tar.zst"
        html = '<a href="%s">builder</a><a href="immortalwrt-sdk.tar.zst">sdk</a>' % name
        self.assertEqual(build.find_archive(html, settings), name)
        with self.assertRaises(ValueError):
            build.find_archive('<a href="immortalwrt-sdk.tar.zst">sdk</a>', settings)
        digest = hashlib.sha256(b"archive fixture").hexdigest()
        self.assertEqual(build.find_checksum(digest + " *" + name, name), digest)
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / name
            archive.write_bytes(b"archive fixture")
            build.verify_archive(archive, digest)
            with self.assertRaises(ValueError):
                build.verify_archive(archive, "0" * 64)

    def test_final_manifest_must_contain_plugins_translations_and_image(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            required = build.required_packages(build.resolve_settings({}, self.devices))
            (output / "test.manifest").write_text("\n".join(p + " - 1.0" for p in required), encoding="utf-8")
            (output / "test.img.gz").write_bytes(b"fixture")
            build.verify_manifest(output, required)
            (output / "test.manifest").write_text("luci-app-openclash - 1.0", encoding="utf-8")
            with self.assertRaises(ValueError):
                build.verify_manifest(output, required)

    def test_local_build_flow_only_includes_firstboot_script_when_argon_selected(self):
        for enabled in (True, False):
            with self.subTest(argon=enabled), tempfile.TemporaryDirectory() as directory:
                temporary_root = Path(directory)
                import shutil
                shutil.copytree(ROOT / "config", temporary_root / "config")
                shutil.copytree(ROOT / "files", temporary_root / "files")
                archive_name = "immortalwrt-imagebuilder-25.12.2-rockchip-armv8.Linux-x86_64.tar.zst"
                payload = b"official download fixture"
                digest = hashlib.sha256(payload).hexdigest()
                metadata = {"version_number": "25.12.2", "target": "rockchip/armv8",
                            "default_packages": ["base-files", "firewall4", "dnsmasq"],
                            "profiles": {"friendlyarm_nanopi-r5s": {"device_packages": ["kmod-r8125"]}}}

                def fake_fetch(url, destination=None):
                    if destination is not None:
                        destination.write_bytes(payload)
                    elif url.endswith("profiles.json"):
                        return json.dumps(metadata)
                    elif url.endswith("sha256sums"):
                        return digest + " *" + archive_name
                    else:
                        return '<a href="%s">ImageBuilder</a>' % archive_name

                def fake_run(command, cwd, log):
                    if command[:2] == ["make", "image"]:
                        arguments = dict(item.split("=", 1) for item in command[2:])
                        self.assertNotIn("ROOTFS_PARTSIZE", arguments)
                        packages = arguments["PACKAGES"].split()
                        self.assertEqual("luci-theme-argon" in packages, enabled)
                        output = Path(arguments["BIN_DIR"])
                        names = [p for p in packages if not p.startswith("-")]
                        (output / "firmware.manifest").write_text("\n".join(p + " - 1.0" for p in names), encoding="utf-8")
                        (output / "firmware.img.gz").write_bytes(b"image fixture")

                env = {"DEVICE": "NanoPi R5S", "VERSION": "25.12.2",
                       "ADD_ARGON": str(enabled).lower(), "ADD_OPENCLASH": "false",
                       "ADD_DDNS_GO": "false", "ADD_UPNP": "false"}
                with mock.patch.object(build, "ROOT", temporary_root), \
                     mock.patch.object(build, "fetch", side_effect=fake_fetch), \
                     mock.patch.object(build, "run", side_effect=fake_run), \
                     mock.patch.dict(build.os.environ, env, clear=True), \
                     mock.patch("builtins.print"):
                    build.main()
                output = temporary_root / "build/output"
                self.assertEqual((output / "firstboot-script.txt").exists(), enabled)
                self.assertEqual((temporary_root / "build/files/etc/uci-defaults/zzz-argon-chinese").exists(), enabled)
                plan = json.loads((output / "build-plan.json").read_text(encoding="utf-8"))
                self.assertIn("kmod-r8125", plan["official_default_packages"])
                self.assertNotIn("kmod-r8125", plan["requested_packages"])
                self.assertIn("dnsmasq", plan["expected_packages_before_dependency_resolution"])


if __name__ == "__main__":
    unittest.main()
