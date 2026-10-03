# ImmortalWrt 自选软件固件

在 GitHub Actions 选择机型和稳定版版本号，分别勾选 **OpenClash、DDNS-Go、UPnP、Argon**。工作流只追加所选功能及其依赖，自带软件包、机型驱动和默认分区大小由官方 ImageBuilder 决定。

## 使用

1. 本功能已集成到仓库默认分支，工作流文件为 `.github/workflows/build-custom-software.yml`。
2. 打开 **Actions → 自选软件构建 ImmortalWrt → Run workflow**。
3. 选择 **NanoPi R5S / NanoPi R4S / x86-64 软路由**，或列表中的其他 Rockchip 机型，填写官网存在的版本号，例如 `25.12.2`。
4. 勾选需要的四项软件；不需要的取消勾选。根分区大小留空即可。
5. 点击运行，成功后在本次运行页面的 **Artifacts** 下载 `firmware-版本号-运行编号`。

默认勾选之前要求的四项。全部取消时，只使用官方默认软件包，不删除官方已经自带的应用。

公开或私人仓库均可使用，不需要另外配置 Secret。GitHub Actions 可用额度取决于账户套餐。固件与日志附件保留 14 天。

## 四个勾选项

| 选项 | 自动添加内容 |
| --- | --- |
| OpenClash | LuCI 插件、Ruby/YAML、dnsmasq-full、curl/证书、TUN、fw4 TProxy 等依赖 |
| DDNS-Go | 主程序、LuCI 设置页、简体中文包、证书依赖 |
| UPnP | nftables 后台、LuCI 设置页、简体中文包及依赖 |
| Argon | 主题、中文主题设置页、LuCI 中文包、wget/jsonfilter；首次启动自动选择 Argon 和简体中文 |

仅勾选 OpenClash 时，工作流才用 `dnsmasq-full` 和 `ip-full` 替换可能自带的 `dnsmasq` 和 `ip-tiny`，避免冲突。底层库依赖由官方包管理器自动补齐；不同机型的默认驱动由各自官方配置保留，不将 R5S 驱动加到其他机型。

OpenClash 插件包不含 Mihomo 运行内核；刷入后还需在 OpenClash 页面下载内核并导入订阅。

仅勾选 Argon 时才加入首次启动脚本 `files/etc/uci-defaults/zzz-argon-chinese`。保留旧配置升级时，旧配置可能覆盖主题和语言设置；可以到 **系统 → 系统 → 语言和界面** 手动选择。

## 其他机型与分区

选择“其他机型”，填写官网机型页面链接中的 `target` 和 `id`：例如 `target=rockchip%2Farmv8` 对应平台 `rockchip/armv8`，`id=friendlyarm_nanopi-r5s` 对应 profile `friendlyarm_nanopi-r5s`。

支持有官方 ImageBuilder、使用 firewall4/nftables 的 `24.10.x` 和 `25.12.x` 稳定版。版本、平台、profile 或所选软件包不存在时会报错，不会自动换版本或跳过插件。

“根分区大小”默认留空，保留官方大小。R5S/R4S/x86 镜像空间不够时可填写 `512`（MB）；其他机型需要依据闪存容量判断是否装得下，不应盲目扩大。

GitHub 原生下拉菜单是静态选项，无法在打开 Run workflow 时动态加载官网全部机型。扩展预设时，在 `config/devices.json` 添加机型，并同步更新工作流的 `device.options`；现有“其他机型”输入不需要修改文件。

## 修改追加软件

四项软件清单分别存放在 `config/packages/openclash.txt`、`ddns_go.txt`、`upnp.txt`、`argon.txt`。每个清单只在对应选项勾选时读取，重复依赖会去重。

`config/devices.json` 只映射机型到官方平台和 profile，不维护或覆盖自带软件包清单。

## 下载内容与检查

成功附件包含固件镜像、官方工具生成的 `sha256sums`、最终 `.manifest` 安装清单、`build-plan.json` 和日志。

工作流从官方 HTTPS 下载对应版本的 ImageBuilder，并核对官方 SHA-256；构建结束后检查所选插件、中文包和主题确实出现在最终安装清单中。失败时下载 `build-log` 附件定位原因。

本地检查命令：`python -m unittest discover -s custom-builder/tests -v`。完整固件构建需要 Linux 环境及官方下载源网络访问。本文件包交付时尚未在 GitHub Actions 跑完整构建，也未刷机验证。

参考：[ImmortalWrt 官方下载](https://downloads.immortalwrt.org/)、[官方 ImageBuilder 参数](https://github.com/immortalwrt/immortalwrt/blob/master/target/imagebuilder/files/Makefile)、[GitHub 手动运行工作流](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/manually-running-a-workflow)。
