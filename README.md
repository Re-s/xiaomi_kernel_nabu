# xiaomi_kernel_nabu · DroidSpaces

小米平板 5（nabu / SM8150 / Snapdragon 860）内核，面向 [DroidSpaces](https://github.com/AndroidDroidSpaces) 容器方案，
fork 自 [Kuugo2002/xiaomi_kernel_nabu](https://github.com/Kuugo2002/xiaomi_kernel_nabu) 的 `HyperOS1-ksu-next`。

**实机验证通过** —— HyperOS 1 / Android 13 / 内核 `4.14.336` / KernelSU-Next。

| 项目 | 状态 |
| --- | --- |
| 目标设备 | Xiaomi Pad 5（nabu），boot header **v3** |
| 容器能力 | user/pid/net 等 namespace、cgroup、overlayfs、squashfs、binfmt_misc、UNIX_DIAG、PROC_CHILDREN |
| 验证方式 | 每项配置附运行时证据（见 [`DROIDSPACES.md`](DROIDSPACES.md) 第一节矩阵） |
| 构建产物 | AnyKernel3 zip、`boot.img` + `vendor_boot.img`、`Image` / `dtb` / `dtbo.img`、`build.config` |
| CI 门禁 | RTIC FDT 存在性、FDT 顺序、Kconfig 采纳断言、Image 内嵌配置一致性 |

## ⚠️ 必读：HyperOS 上 `/proc/config.gz` 必须伪装

实测 HyperOS 会校验 `/proc/config.gz` 的内容：刷入输出**真实配置**的内核后，
每次开机弹「**设备内部出现问题**」对话框；开启原厂伪装后消失。

因此 CI 的 `ikconfig_stock_masquerade` 输入**默认已开启**（输出原厂
`nabu-stock_defconfig` 伪装）。细节、机制边界与两种模式的门禁行为见
[`DROIDSPACES.md` 第七节](DROIDSPACES.md)。验证任何配置改动只看 CI 产物
`build.config`，不要相信设备上的 `/proc/config.gz`。

## 使用

### 构建

GitHub Actions 手动触发（Actions → Build kernel → Run workflow）：

| 输入 | 默认 | 说明 |
| --- | --- | --- |
| `ikconfig_stock_masquerade` | **true** | HyperOS 必开，见上节 |
| `build_boot_images` | true | 从 Release [`stock-ref`](../../releases/tag/stock-ref) 拉参考件合成双镜像 |
| `ksu_variant` | KernelSU-Next | 选 `none` 可关闭 KSU |
| `extra_config` | 空 | 追加 defconfig 项（逗号分隔） |

本地构建：

```sh
IKCONFIG_STOCK_MASQUERADE=1 ./build.sh nabu -j"$(nproc)"
```

### 刷入

nabu 是 header v3：**dtb 在 vendor_boot，两个镜像必须一起刷**，只刷
boot.img 不更新 dtb。

```sh
adb reboot bootloader
fastboot flash boot_a        boot.img
fastboot flash vendor_boot_a vendor_boot.img
fastboot reboot
```

推荐刷非活动槽留退路（详见 [`DROIDSPACES.md` 第五节](DROIDSPACES.md)）。
AnyKernel3 zip 在管理器 App 内刷入曾有无法进系统的案例，优先用 fastboot 双镜像路径。

### 原厂参考件

CI 所需的 `boot-ref.img`（18.9 MiB）/ `vendor_boot-ref.img`（8 KiB）在
Release [`stock-ref`](../../releases/tag/stock-ref)，由
[`tools/make_stock_ref.py`](tools/make_stock_ref.py) 从原厂分区裁出
（合成结果与全量镜像逐字节相同）。ROM 大版本升级后需重新裁剪上传。

## 文档索引

完整说明在 **[DROIDSPACES.md](DROIDSPACES.md)**：

1. [实际开启的内核配置](DROIDSPACES.md#一实际开启的内核配置) —— 分类清单 + 实机验证矩阵
2. [RTIC FDT](DROIDSPACES.md#二rtic-fdt启动必需不是可选加固) —— 缺失必变砖
3. [FDT 顺序](DROIDSPACES.md#三fdt-顺序决定能否启动) —— 按 idx 取树，顺序错开不了机
4. [合成 boot/vendor_boot](DROIDSPACES.md#四合成-bootimg--vendor_bootimg)
5. [刷入与槽位策略](DROIDSPACES.md#五刷入用非活动槽可一键回退)
6. [构建](DROIDSPACES.md#六构建)
7. [/proc/config.gz 与原厂伪装](DROIDSPACES.md#七procconfiggz-与原厂伪装hyperos-必读)

## 致谢

- [Kuugo2002/xiaomi_kernel_nabu](https://github.com/Kuugo2002/xiaomi_kernel_nabu) 及其上游
- [ravindu644](https://github.com/ravindu644) 的 DroidSpaces 内核配置块
- osm0sis 的 AnyKernel3；Sultan Alsawaf 的原版 ikconfig 补丁（本分支将其开关化）
