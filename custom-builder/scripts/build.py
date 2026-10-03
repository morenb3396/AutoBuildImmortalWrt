#!/usr/bin/env python3
"""Build customized firmware with the matching official ImmortalWrt ImageBuilder."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = "https://downloads.immortalwrt.org"
FEATURES = {
    "openclash": {"luci-app-openclash", "dnsmasq-full", "ip-full", "kmod-nft-tproxy", "kmod-tun"},
    "ddns_go": {"luci-app-ddns-go", "ddns-go", "luci-i18n-ddns-go-zh-cn"},
    "upnp": {"luci-app-upnp", "luci-i18n-upnp-zh-cn", "miniupnpd-nftables"},
    "argon": {"luci-theme-argon", "luci-app-argon-config", "luci-i18n-argon-config-zh-cn", "luci-i18n-base-zh-cn"},
}


def read_packages(path):
    tokens = []
    for line in path.read_text(encoding="utf-8").splitlines():
        tokens.extend(line.split("#", 1)[0].split())
    if any(not re.fullmatch(r"-?[a-z0-9][a-z0-9+_.-]*", p) for p in tokens):
        raise ValueError("软件包名称不合法，请检查 %s。" % path)
    return list(dict.fromkeys(tokens))


def resolve_settings(env, devices):
    version = env.get("VERSION", "25.12.2").strip()
    if not re.fullmatch(r"(?:24\.10|25\.12)\.\d+", version):
        raise ValueError("请填写 24.10.x 或 25.12.x 稳定版版本号；不支持旧版 fw3 或 snapshot。")
    device = env.get("DEVICE", "NanoPi R5S").strip()
    if device == "其他机型":
        settings = {"target": env.get("CUSTOM_TARGET", "").strip(),
                    "profile": env.get("CUSTOM_PROFILE", "").strip()}
    elif device in devices:
        settings = dict(devices[device])
    else:
        raise ValueError("未知机型，请选择已有预设或“其他机型”。")
    settings["rootfs_size"] = None
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9_-]*", settings["target"]):
        raise ValueError("平台格式应为 target/subtarget，例如 rockchip/armv8。")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.+-]*", settings["profile"]):
        raise ValueError("请填写合法的机型 profile ID，可从官网机型链接中的 id 查看。")
    size = env.get("ROOTFS_SIZE", "").strip()
    if size:
        if not size.isascii() or not size.isdigit() or not 128 <= int(size) <= 4096:
            raise ValueError("根分区大小应为 128–4096 之间的整数 MB，或留空。")
        settings["rootfs_size"] = int(size)
    selected = []
    for feature in FEATURES:
        flag = env.get("ADD_" + feature.upper(), "true").lower()
        if flag not in ("true", "false"):
            raise ValueError("ADD_%s 只能为 true 或 false。" % feature.upper())
        if flag == "true":
            selected.append(feature)
    settings.update(version=version, device=device, selected_features=selected)
    return settings


def selected_packages(settings):
    packages = []
    for feature in settings["selected_features"]:
        packages.extend(read_packages(ROOT / "config/packages" / (feature + ".txt")))
    return list(dict.fromkeys(packages))


def required_packages(settings):
    required = set()
    for feature in settings["selected_features"]:
        required.update(FEATURES[feature])
    return required


def check_metadata(settings, metadata):
    if metadata.get("target") != settings["target"]:
        raise ValueError("官方机型列表的平台与请求不符，已停止构建。")
    if metadata.get("version_number") != settings["version"]:
        raise ValueError("官方机型列表的固件版本与请求不符，已停止构建。")
    profile = metadata.get("profiles", {}).get(settings["profile"])
    if profile is None:
        raise ValueError("该版本不含 profile %s；请核对版本与机型。" % settings["profile"])
    return profile


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.names = set()

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            self.names.add(PurePosixPath(unquote(urlsplit(href).path)).name)


def find_archive(html, settings):
    links = Links()
    links.feed(html)
    prefix = "immortalwrt-imagebuilder-%s-%s.Linux-x86_64.tar." % (
        settings["version"], settings["target"].replace("/", "-"))
    for extension in ("zst", "xz"):
        if prefix + extension in links.names:
            return prefix + extension
    raise ValueError("官网没有该版本/平台的 ImageBuilder，无法生成此固件。")


def find_checksum(text, filename):
    for line in text.splitlines():
        fields = line.split(maxsplit=1)
        if len(fields) == 2 and fields[1].lstrip("*") == filename:
            if re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
                return fields[0].lower()
    raise ValueError("官方 sha256sums 中没有此 ImageBuilder，已停止下载。")


def fetch(url, destination=None):
    """Use HTTPS validation and retry transient network failures."""
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "ImmortalWrt-Custom-Builder/1.0"})
            with urlopen(request, timeout=90) as response:
                if destination is None:
                    return response.read().decode("utf-8")
                with destination.open("wb") as output:
                    shutil.copyfileobj(response, output)
                return None
        except (OSError, ValueError) as error:
            if attempt == 2:
                raise RuntimeError("无法读取官网文件 %s：%s" % (url, error)) from error
            print("网络请求失败，稍后重试：%s" % url, flush=True)
            time.sleep(5 * (attempt + 1))


def verify_archive(path, expected):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError("ImageBuilder SHA-256 校验失败，已停止构建。")


def run(command, cwd, log):
    print("运行：%s" % " ".join(command), flush=True)
    with log.open("a", encoding="utf-8") as output:
        process = subprocess.Popen(command, cwd=str(cwd), stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", errors="replace")
        for line in process.stdout:
            print(line, end="", flush=True)
            output.write(line)
        returncode = process.wait()
    if returncode:
        raise RuntimeError("构建命令失败（退出码 %s），详情见 build.log。" % returncode)


def verify_manifest(output, required):
    manifests = list(output.glob("*.manifest"))
    if not manifests:
        raise ValueError("未找到最终安装清单，无法确认预装软件包。")
    for manifest in manifests:
        installed = {line.split()[0] for line in manifest.read_text(
            encoding="utf-8").splitlines() if line.strip()}
        missing = required - installed
        if missing:
            raise ValueError("固件缺少必需软件包：%s" % ", ".join(sorted(missing)))
    if not any(p.name.endswith((".bin", ".img.gz", ".img", ".itb", ".ubi", ".trx"))
               for p in output.iterdir()):
        raise ValueError("没有生成可识别的固件镜像。")


def main():
    devices = json.loads((ROOT / "config/devices.json").read_text(encoding="utf-8"))
    settings = resolve_settings(os.environ, devices)
    work = ROOT / "build"
    work.mkdir(exist_ok=True)
    log = work / "build.log"
    log.write_text("", encoding="utf-8")
    base = "%s/releases/%s/targets/%s/" % (
        DOWNLOADS, settings["version"], settings["target"])
    print("机型：%s；版本：%s；平台：%s；profile：%s" % (
        settings["device"], settings["version"], settings["target"], settings["profile"]), flush=True)

    metadata_text = fetch(base + "profiles.json")
    (work / "official-profiles.json").write_text(metadata_text, encoding="utf-8")
    metadata = json.loads(metadata_text)
    profile = check_metadata(settings, metadata)
    packages = selected_packages(settings)
    removed = {p[1:] for p in packages if p.startswith("-")}
    defaults = list(dict.fromkeys(metadata.get("default_packages", []) + profile.get("device_packages", [])))
    effective = [p for p in dict.fromkeys(defaults + packages)
                 if not p.startswith("-") and p not in removed]
    settings.update(requested_packages=packages, official_default_packages=defaults,
                    expected_packages_before_dependency_resolution=effective,
                    official_download_url=base)
    archive_name = find_archive(fetch(base), settings)
    checksum = find_checksum(fetch(base + "sha256sums"), archive_name)
    settings.update(imagebuilder=archive_name, imagebuilder_sha256=checksum)
    (work / "build-plan.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "selected-packages.txt").write_text(" ".join(packages) + "\n", encoding="utf-8")

    archive = work / archive_name
    print("下载并校验官方 ImageBuilder：%s" % archive_name, flush=True)
    fetch(base + archive_name, archive)
    verify_archive(archive, checksum)
    builder = work / "imagebuilder"
    builder.mkdir()  # Do not reuse a previous build directory.
    run(["tar", "-xf", str(archive), "-C", str(builder), "--strip-components=1"], ROOT, log)
    overlay = work / "files"
    if "argon" in settings["selected_features"]:
        shutil.copytree(ROOT / "files", overlay)
        (overlay / "etc/uci-defaults/zzz-argon-chinese").chmod(0o755)
    else:
        overlay.mkdir()
    output = work / "output"
    output.mkdir()
    run(["make", "info"], builder, log)
    command = ["make", "image", "PROFILE=" + settings["profile"],
               "PACKAGES=" + " ".join(packages), "FILES=" + str(overlay),
               "BIN_DIR=" + str(output),
               "EXTRA_IMAGE_NAME=" + ("-".join(settings["selected_features"]) or "default-packages")]
    if settings["rootfs_size"] is not None:
        command.append("ROOTFS_PARTSIZE=" + str(settings["rootfs_size"]))
    run(command, builder, log)
    verify_manifest(output, required_packages(settings))
    for name in ("build-plan.json", "selected-packages.txt", "official-profiles.json", "build.log"):
        shutil.copy2(work / name, output / name)
    if "argon" in settings["selected_features"]:
        shutil.copy2(overlay / "etc/uci-defaults/zzz-argon-chinese", output / "firstboot-script.txt")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## 固件已生成\n\n- 机型：%s\n- 版本：%s\n- 平台：%s\n"
                         "- profile：%s\n- 所选功能：%s\n- 已核对最终软件包清单。\n\n"
                         "下载本次运行的 firmware 附件即可获取镜像和 sha256sums。\n" % (
                             settings["device"], settings["version"], settings["target"], settings["profile"],
                             ", ".join(settings["selected_features"]) or "无（官方默认包）"))
            if "openclash" in settings["selected_features"]:
                handle.write("\nOpenClash 的 Mihomo 内核与订阅仍需在刷入后添加。\n")
    print("构建完成，固件和最终安装清单位于 build/output。", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print("错误：%s" % error, file=sys.stderr, flush=True)
        directory = ROOT / "build"
        if directory.exists():
            with (directory / "build.log").open("a", encoding="utf-8") as log:
                log.write("\n错误：%s\n" % error)
        sys.exit(1)
