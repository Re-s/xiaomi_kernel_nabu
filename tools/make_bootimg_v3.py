#!/usr/bin/env python3
# ============================================
# 合成 nabu 的 boot.img（header v3）
# ============================================
# 与设备原厂格式完全一致的 boot 分区镜像，用于 fastboot flash boot_X。
#
# 为什么是 v3 而不是自包含的 v0/v2
# --------------------------------
# nabu 原厂 boot 分区就是 v3：头里只有 kernel_size / ramdisk_size /
# os_version / header_size / cmdline，**没有** kernel_addr、ramdisk_addr、
# tags_addr，也没有 dtb —— 这些全部由 vendor_boot 分区提供，bootloader
# 会把两个分区一起读。
#
# 先前尝试过合成自包含镜像给 `fastboot boot` 用，两次都黑屏，原因：
#   1. v0 + 把 dtb 拼在 Image 尾部 —— arm64 **没有** CONFIG_ARM64_APPENDED_DTB
#      （那是 32 位 ARM 的机制），拼上去的字节永远不会被读，内核拿不到设备树。
#      defconfig 里的 CONFIG_BUILD_ARM64_APPENDED_DTB_IMAGE 只是构建时产出
#      Image.gz-dtb 这个文件名的开关，与运行时无关。
#   2. v2 + 照抄 vendor_boot 的 addr 字段 —— v2 的 addr 语义带独立 base，
#      而 vendor_boot 里那些值（tags_addr=0x100 等）是相对偏移，直接搬过去
#      内核被加载到错误物理地址。
# 结论：不要自造非标准结构，照 v3 原样合成，走设备原生路径。
#
# 用法:
#   python3 make_bootimg_v3.py --kernel Image --ref-boot <原厂boot_a.img> -o boot.img
#
#   --ref-boot 提供 ramdisk、os_version、cmdline，保证除内核外与原厂一致。
#   也可用 --ramdisk 显式指定 ramdisk。
#
# 刷入（写非活动槽，可一键切回）:
#   fastboot flash boot_b boot.img
#   fastboot --set-active=b && fastboot reboot
#   # 不行就 fastboot --set-active=a
#
# 退出码: 0 成功 / 1 输入不合法 / 2 参数错误
# ============================================

import argparse
import os
import struct
import sys

BOOT_MAGIC = b"ANDROID!"
V3_HEADER_SIZE = 1580
V3_PAGE_SIZE = 4096          # v3 固定 4096，头里不再存该字段
V3_CMDLINE_SIZE = 1536


def align_up(value, alignment):
    return ((value + alignment - 1) // alignment) * alignment


def parse_v3(path):
    with open(path, "rb") as fh:
        buf = fh.read()
    if buf[:8] != BOOT_MAGIC:
        raise ValueError(f"{path} 不是 boot 镜像（magic={buf[:8]!r}）")
    hv = struct.unpack("<I", buf[40:44])[0]
    if hv != 3:
        raise ValueError(f"{path} 是 header v{hv}，本脚本只处理 v3")
    ks = struct.unpack("<I", buf[8:12])[0]
    rs = struct.unpack("<I", buf[12:16])[0]
    pg = V3_PAGE_SIZE
    ro = pg + align_up(ks, pg)
    return {
        "kernel_size": ks,
        "ramdisk_size": rs,
        "os_version": struct.unpack("<I", buf[16:20])[0],
        "header_size": struct.unpack("<I", buf[20:24])[0],
        "cmdline": buf[44:44 + V3_CMDLINE_SIZE].split(b"\0")[0],
        "kernel": buf[pg:pg + ks],
        "ramdisk": buf[ro:ro + rs],
    }


def build_v3(kernel, ramdisk, os_version, cmdline):
    if len(cmdline) > V3_CMDLINE_SIZE - 1:
        raise ValueError(f"cmdline 超长（{len(cmdline)}）")

    hdr = bytearray(V3_HEADER_SIZE)
    hdr[0:8] = BOOT_MAGIC
    struct.pack_into("<I", hdr, 8, len(kernel))
    struct.pack_into("<I", hdr, 12, len(ramdisk))
    struct.pack_into("<I", hdr, 16, os_version)
    struct.pack_into("<I", hdr, 20, V3_HEADER_SIZE)
    # reserved[4] @24..40 留零
    struct.pack_into("<I", hdr, 40, 3)          # header_version
    hdr[44:44 + V3_CMDLINE_SIZE] = cmdline[:V3_CMDLINE_SIZE - 1].ljust(
        V3_CMDLINE_SIZE, b"\0")

    pg = V3_PAGE_SIZE

    def pad(data):
        return data + b"\0" * (align_up(len(data), pg) - len(data))

    return bytes(pad(bytes(hdr)) + pad(kernel) + pad(ramdisk))


def main():
    ap = argparse.ArgumentParser(description="合成 nabu boot.img (header v3)")
    ap.add_argument("--kernel", required=True, help="新编内核 Image")
    ap.add_argument("--ref-boot", required=True,
                    help="原厂 boot 分区镜像，提供 ramdisk/os_version/cmdline")
    ap.add_argument("--ramdisk", help="显式指定 ramdisk，覆盖 --ref-boot 的")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()

    for p in (args.kernel, args.ref_boot):
        if not os.path.isfile(p):
            print(f"错误: 找不到 {p}")
            return 2

    try:
        ref = parse_v3(args.ref_boot)
    except ValueError as exc:
        print(f"::error::{exc}")
        return 1

    print(f"[参考] {args.ref_boot}")
    osv = ref["os_version"]
    print(f"  os_version   = {(osv >> 25) & 0x7f}.{(osv >> 18) & 0x7f}."
          f"{(osv >> 11) & 0x7f}  patch={2000 + ((osv >> 4) & 0x7f)}-"
          f"{osv & 0xf:02d}")
    print(f"  kernel_size  = {ref['kernel_size']}")
    print(f"  ramdisk_size = {ref['ramdisk_size']}")
    print(f"  cmdline      = {ref['cmdline'].decode() or '(空)'}")

    with open(args.kernel, "rb") as fh:
        kernel = fh.read()
    if kernel[0x38:0x3c] != b"ARM\x64":
        print(f"::error::{args.kernel} 不是 arm64 Image"
              f"（0x38 处应为 ARM\\x64，实为 {kernel[0x38:0x3c]!r}）")
        return 1
    print(f"\n[内核] {args.kernel} {len(kernel)} 字节 (arm64 magic OK)")
    delta = len(kernel) - ref["kernel_size"]
    print(f"  相比原厂 {delta:+d} 字节")

    if args.ramdisk:
        with open(args.ramdisk, "rb") as fh:
            ramdisk = fh.read()
        print(f"\n[ramdisk] {args.ramdisk} {len(ramdisk)} 字节（显式指定）")
    else:
        ramdisk = ref["ramdisk"]
        print(f"\n[ramdisk] 沿用 {args.ref_boot} 的 {len(ramdisk)} 字节")

    try:
        img = build_v3(kernel, ramdisk, ref["os_version"], ref["cmdline"])
    except ValueError as exc:
        print(f"::error::{exc}")
        return 1

    with open(args.output, "wb") as fh:
        fh.write(img)

    # 回读复验
    chk = parse_v3(args.output)
    ok = (chk["kernel"] == kernel and chk["ramdisk"] == ramdisk
          and chk["os_version"] == ref["os_version"]
          and chk["cmdline"] == ref["cmdline"])
    print(f"\n[输出] {args.output} {len(img)} 字节 (header v3)")
    print(f"  回读复验: {'通过' if ok else '失败'}")
    if not ok:
        print("::error::回读复验不一致")
        return 1

    print("\n刷入非活动槽（可一键切回，boot_a 不动）:")
    print("  adb reboot bootloader")
    print(f"  fastboot flash boot_b {args.output}")
    print("  fastboot --set-active=b && fastboot reboot")
    print("  # 有问题就切回: fastboot --set-active=a")
    print("\n注意 dtb 在 vendor_boot 分区，本镜像只换内核。")
    print("若 dtb 也要更新，需同时刷 vendor_boot_b。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
