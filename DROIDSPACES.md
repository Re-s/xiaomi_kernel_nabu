# DroidSpaces 分支说明

本分支 fork 自 [Kuugo2002/xiaomi_kernel_nabu](https://github.com/Kuugo2002/xiaomi_kernel_nabu)
的 `HyperOS1-ksu-next`，面向小米平板 5（nabu, SM8150 / Snapdragon 860）。

目标：在上游内核基础上补齐 DroidSpaces 容器所需的内核能力，并产出可直接
刷入的 `boot.img` / `vendor_boot.img`。

**实机验证通过** —— HyperOS 1 / Android 13，内核 `4.14.336`。

## 相对上游的改动

上游的构建体系（`build.sh`、python2 源码编译脚本、Clang A15 工具链、
AnyKernel3 模板）全部原样沿用，未做替换。改动集中在下面几处：

| 文件 | 改动 |
| --- | --- |
| `arch/arm64/configs/nabu_defconfig` | 追加 7 行容器配置 |
| `build.sh` | 补回 RTIC FDT；合成 boot/vendor_boot；dtb 缺失改为硬失败 |
| `fix_rtic_dtb.py` | 新增，RTIC FDT 修补器 |
| `stock/rtic.fdt` | 新增，173 字节原厂 RTIC FDT |
| `tools/make_bootimg_v3.py` | 新增，boot.img 生成器 |
| `tools/make_vendor_boot_v3.py` | 新增，vendor_boot.img 生成器 |
| `tools/verify_rtic_present.py` | 新增，RTIC 存在性硬校验 |

## 一、追加的内核配置

只加了上游 DroidSpaces 配置块未覆盖的三类：

```
CONFIG_UNIX_DIAG=y        # 容器内 ss / lsof 枚举 unix domain socket
CONFIG_SQUASHFS=y         # 挂载 squashfs rootfs 镜像 / AppImage
CONFIG_SQUASHFS_XATTR=y
CONFIG_SQUASHFS_ZLIB=y
CONFIG_SQUASHFS_XZ=y
CONFIG_SQUASHFS_ZSTD=y
CONFIG_BINFMT_MISC=y      # 注册 qemu-user / box64 跨架构执行
```

以下**不需要**再加，上游已具备：

- `CONFIG_BLK_DEV_LOOP` —— 已启用，且 `MIN_COUNT=16`
- `CONFIG_USER_NS` —— 上游 DroidSpaces 块自带（`nabu_defconfig:6120`，
  注释 `Fix for docker unsafe procfs error`），覆盖了 172 行的 not set
- namespace / cgroup / overlayfs / netfilter 等 —— 上游 DroidSpaces 块已齐备

追加在文件末尾即可覆盖前面的 `is not set` 声明，上游自身就用这个手法。

### 实机验证

```
$ ls /proc/self/ns/
cgroup ipc mnt net pid pid_for_children user uts      # user 出现了
$ unshare -U echo OK
OK
$ grep squashfs /proc/filesystems
        squashfs
$ ls -d /proc/sys/fs/binfmt_misc
/proc/sys/fs/binfmt_misc
```

刷入前该设备 `/proc/self/ns/` 无 `user`，`unshare -U` 返回
`Invalid argument`。

> 该内核未开 `CONFIG_IKCONFIG_PROC`，所以没有 `/proc/config.gz`。
> 用上面这些运行时证据判断，比读配置文本更可靠。

## 二、RTIC FDT：启动必需，不是可选加固

`arch/arm64/boot/Makefile`：

```make
ifdef RTIC_MPGEN
DTB_OBJS += rtic_mp.dtb
endif
```

`RTIC_MPGEN` 指向高通闭源 MPGen 工具，CI 与常规本地编译都没有，因此
`rtic_mp.dtb` 永远不参与 `DTB_OBJS` —— **编出的 dtb 必然缺少原厂那个
173 字节的 RTIC FDT**，而上游 `build.sh` 也没有补这一步。

实测：缺该 FDT 的 dtb 刷入后设备无法正常启动；补齐后启动正常。

`build.sh` 已在打包前自动修补，幂等（已含 RTIC 时跳过）。手动执行：

```sh
python3 fix_rtic_dtb.py stock/rtic.fdt <dtb> <输出>
# 退出码 0=已追加 1=输入不合法 2=参数错误 3=已含 RTIC，跳过
```

> 注意：实机 `/proc/device-tree` 里搜不到 `qcom,rtic-id`，`/proc/kallsyms`
> 里也没有 RTIC 驱动 —— 但这**不能**说明它不需要。该 FDT 很可能由
> bootloader/TZ 在校验阶段读取，读完并不合并进内核可见的 DT。拿内核视角
> 的观测去否证 bootloader 阶段的需求，是一次证据域越界。

## 三、FDT 顺序：决定能否启动

bootloader **按索引**选设备树。设备 cmdline 里有
`androidboot.dtb_idx=1`，即取拼接序列中的**第 2 个** FDT（0-based）。

原厂顺序（与 `dts/qcom/Makefile:7` 的
`nabu-sm8150-overlay.dtbo-base` 声明一致）：

```
#0 sm8150.dtb      SM8150 v1
#1 sm8150-v2.dtb   SM8150 v2    ← idx=1，设备实际用这个
#2 sm8150p.dtb     SM8150P v1
#3 sm8150p-v2.dtb  SM8150P v2
#4 rtic_mp.dtb     RTIC (173 B)
```

而 `arch/arm64/boot/Makefile` 用：

```make
DTB_OBJS := $(shell find $(obj)/dts/ -name \*.dtb)
```

`find` 的返回顺序取决于文件系统目录项的物理排列，**不稳定**。实测某次
构建得到 `SM8150P v1 / SM8150P v2 / SM8150 v2 / SM8150 v1`，于是 idx=1
落到 `SM8150P v2` —— SM8150**P** 是另一款芯片，刷入后无法启动。

> 症状对照：AnyKernel3 包刷入（会写 dtb）开不了机，而只刷 `boot.img`
> （header v3 不碰 vendor_boot，dtb 仍是原厂的）可以正常启动。

`build.sh` 已自动重排并做硬门禁。手动检查：

```sh
python3 tools/reorder_dtb.py --check <dtb>   # rc=0 正确 / rc=1 顺序错
python3 tools/reorder_dtb.py <输入> <输出>    # 重排
python3 tools/verify_rtic_present.py <dtb>   # RTIC 存在性（另一道，不可替代）
```

顺序正确时，编出的 dtb 与设备原厂**逐字节一致**
（md5 `feb7799e90987a952c756c2a946433c9`）。

> 两道门禁互不替代：顺序错的 dtb 其 RTIC 校验是**通过**的（RTIC 确实
> 在，只是平台 FDT 排错了）。
>
> 另外不要假设 bootloader 会按 `qcom,msm-id` 自动选型 —— 本项目一度
> 因此只做 FDT **集合**比对（集合 md5 一致即放行）而漏过了顺序问题。
> 集合相等不等于序列相等；下游按位置索引时，必须比对序列。

`dtbo.img` 与设备原厂逐字节一致（md5 `341280a8b5cbbdde81d79a31294bb9f9`），
**无需刷入**。

## 四、合成 boot.img / vendor_boot.img

把原厂分区镜像放在 `stock/` 下，`build.sh` 会自动合成；缺失则跳过，
只产出 AnyKernel3 包。

```sh
adb shell su -c 'dd if=/dev/block/by-name/boot_a of=/sdcard/boot.img'
adb shell su -c 'dd if=/dev/block/by-name/vendor_boot_a of=/sdcard/vendor_boot.img'
adb pull /sdcard/boot.img        stock/
adb pull /sdcard/vendor_boot.img stock/
```

手动执行：

```sh
python3 tools/make_bootimg_v3.py \
    --kernel out/arch/arm64/boot/Image \
    --ref-boot stock/boot.img -o boot.img

python3 tools/make_vendor_boot_v3.py \
    --dtb out/arch/arm64/boot/dtb \
    --ref-vendor-boot stock/vendor_boot.img -o vendor_boot.img
```

### 为什么必须是 header v3

nabu 原厂 boot 分区就是 v3，头里只有 `kernel_size` / `ramdisk_size` /
`os_version` / `header_size` / `cmdline`，**没有** `kernel_addr` /
`ramdisk_addr` / `tags_addr`，也没有 dtb —— 这些全部由 `vendor_boot`
提供，bootloader 把两个分区一起读。

布局（pagesize 4096）：

```
boot.img         [hdr 1580B][kernel][ramdisk]
vendor_boot.img  [hdr 2112B][vendor_ramdisk][dtb]
```

`vendor_boot` 头里的地址：`kernel=0x8000` `ramdisk=0x1000000`
`tags=0x100` `dtb=0x1f00000`。生成器原样保留，不重算。

### 往返验证：刷之前先做这一步

拿**原厂**内核/dtb 喂进生成器，输出应与原厂分区逐字节一致：

```sh
python3 tools/make_bootimg_v3.py --kernel <原厂内核> \
    --ref-boot stock/boot.img -o roundtrip.img
cmp roundtrip.img <截断后的原厂 boot.img>
```

预期：`boot` 51798016 字节、`vendor_boot` 2007040 字节。

> 从设备 dd 出来的是**整个分区**（boot 128MB、vendor_boot 96MB），尾部
> 是填充。比对前要按 header 算出实际镜像大小并截断。

## 五、刷入：用非活动槽，可一键回退

nabu 是 A/B 设备，只有 `boot_a`/`boot_b`，没有无后缀的 `boot`。

```sh
# 备份（一次就够）
adb shell su -c 'dd if=/dev/block/by-name/boot_a of=/sdcard/boot_a.bak'
adb pull /sdcard/boot_a.bak

adb reboot bootloader
fastboot flash boot_b        boot.img
fastboot flash vendor_boot_b vendor_boot.img
fastboot --set-active=b && fastboot reboot
```

出问题一条命令回退，`boot_a` 全程未被改动：

```sh
fastboot --set-active=a
```

**两个必须一起刷。** dtb 在 `vendor_boot` 里，只刷 `boot.img` 的话内核
换了但 dtb 还是旧的。

### `fastboot boot` 不可行

v3 镜像不自包含（dtb 和加载地址都在 vendor_boot），无法临时启动。
尝试过两种自造的自包含结构，均黑屏：

- **v0 + dtb 拼在 Image 尾部** —— arm64 **没有**
  `CONFIG_ARM64_APPENDED_DTB`，那是 32 位 ARM 的机制。defconfig 里的
  `CONFIG_BUILD_ARM64_APPENDED_DTB_IMAGE=y` 只是构建时产出
  `Image.gz-dtb` 这个文件名的开关，与运行时识别无关 —— 名字容易误导。
- **v2 + 照抄 vendor_boot 的 addr 字段** —— v2 的 addr 语义带独立 base，
  而 vendor_boot 里存的是相对偏移（`tags_addr=0x100` 作为绝对物理地址
  显然不成立），直接搬过去会让内核加载到错误地址。

结论：走设备原生的 v3 路径，用槽位切换代替临时启动。

## 六、构建

```sh
./build.sh nabu -j"$(nproc)"
```

或在 GitHub Actions 手动触发 `Build kernel`（`workflow_dispatch`）。
产物：AnyKernel3 zip、`Image`、`dtb`、`dtbo.img`，以及
`stock/` 参考镜像存在时的 `boot.img` / `vendor_boot.img`。

CI 会对 dtb 做 RTIC 存在性硬校验 —— 缺 RTIC 的包不该流出。
