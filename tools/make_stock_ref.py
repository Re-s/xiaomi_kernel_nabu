#!/usr/bin/env python3
"""把原厂 boot / vendor_boot 分区镜像裁成"参考件"，去掉合成时用不到的段。

合成新镜像时，原厂镜像只被用来提供 **除内核/dtb 之外** 的部分：

- boot.img        需要 ramdisk、os_version、cmdline —— 原厂内核会被新内核替换
- vendor_boot.img 需要 header（各 addr / cmdline / name）与 vendor_ramdisk
                  —— 原厂 dtb 会被新编 dtb 替换

所以把这些无用段裁掉，体积能大幅缩小（nabu 实测）：

    boot.img         51798016 B (49.4 MiB) → 19808256 B (18.9 MiB)
    vendor_boot.img   2007040 B ( 1.9 MiB) →     8192 B ( 8.0 KiB)

裁出来的文件仍是**合法的 v3 镜像**（只是被裁段的 size 字段置 0），
可直接喂给 make_bootimg_v3.py --ref-boot / make_vendor_boot_v3.py
--ref-vendor-boot，无需改动那两个脚本。

用法：
    make_stock_ref.py boot        <原厂boot_a.img>        -o stock/boot-ref.img
    make_stock_ref.py vendor_boot <原厂vendor_boot_a.img> -o stock/vendor_boot-ref.img

从设备取原厂镜像（需 root）：
    adb shell su -c 'dd if=/dev/block/by-name/boot_a of=/data/local/tmp/boot_a.img'
    adb pull /data/local/tmp/boot_a.img
注意 dd 出来的是**整个分区**（boot 128 MiB / vendor_boot 96 MiB），
本脚本会按 header 自动算出实际镜像范围，不必事先截断。
"""

import argparse
import os
import struct
import sys

BOOT_MAGIC = b"ANDROID!"
VENDOR_MAGIC = b"VNDRBOOT"
V3_BOOT_HEADER_SIZE = 1580
V3_VENDOR_HEADER_SIZE = 2112


def align_up(x, a):
    return (x + a - 1) // a * a


def trim_boot(buf):
    """boot v3: [hdr 1580B][kernel][ramdisk] → 保留 hdr+ramdisk，kernel_size 置 0。

    布局固定 pagesize 4096。裁后 ramdisk 紧跟 header（因为 kernel 段长度为 0）。
    """
    if buf[:8] != BOOT_MAGIC:
        raise ValueError(f"不是 boot 镜像（magic={buf[:8]!r}）")
    hv = struct.unpack("<I", buf[40:44])[0]
    if hv != 3:
        raise ValueError(f"boot header v{hv}，本脚本只处理 v3")

    kernel_size = struct.unpack("<I", buf[8:12])[0]
    ramdisk_size = struct.unpack("<I", buf[12:16])[0]
    pg = 4096

    off = align_up(V3_BOOT_HEADER_SIZE, pg) + align_up(kernel_size, pg)
    ramdisk = buf[off:off + ramdisk_size]
    if len(ramdisk) != ramdisk_size:
        raise ValueError(
            f"ramdisk 截断：期望 {ramdisk_size} B，实际只读到 {len(ramdisk)} B"
        )

    hdr = bytearray(buf[:V3_BOOT_HEADER_SIZE])
    struct.pack_into("<I", hdr, 8, 0)  # kernel_size = 0

    def pad(data):
        return data + b"\0" * (align_up(len(data), pg) - len(data))

    out = pad(bytes(hdr)) + pad(ramdisk)
    return out, {
        "kernel_size": kernel_size,
        "ramdisk_size": ramdisk_size,
        "os_version": struct.unpack("<I", buf[16:20])[0],
        "cmdline": buf[44:44 + 1536].split(b"\0")[0],
    }


def trim_vendor_boot(buf):
    """vendor_boot v3: [hdr 2112B][vendor_ramdisk][dtb] → 保留前两段，dtb_size 置 0。"""
    if buf[:8] != VENDOR_MAGIC:
        raise ValueError(f"不是 vendor_boot 镜像（magic={buf[:8]!r}）")
    hv = struct.unpack("<I", buf[8:12])[0]
    if hv != 3:
        raise ValueError(f"vendor_boot header v{hv}，本脚本只处理 v3")

    pg = struct.unpack("<I", buf[12:16])[0]
    vrs = struct.unpack("<I", buf[24:28])[0]
    header_size = struct.unpack("<I", buf[2096:2100])[0]
    dtb_size = struct.unpack("<I", buf[2100:2104])[0]

    off = align_up(header_size, pg)
    vramdisk = buf[off:off + vrs]
    if len(vramdisk) != vrs:
        raise ValueError(
            f"vendor_ramdisk 截断：期望 {vrs} B，实际只读到 {len(vramdisk)} B"
        )

    hdr = bytearray(buf[:V3_VENDOR_HEADER_SIZE])
    struct.pack_into("<I", hdr, 2100, 0)  # dtb_size = 0

    def pad(data):
        return data + b"\0" * (align_up(len(data), pg) - len(data))

    out = pad(bytes(hdr)) + pad(vramdisk)
    return out, {
        "page_size": pg,
        "vendor_ramdisk_size": vrs,
        "dtb_size": dtb_size,
        "cmdline": buf[28:28 + 2048].split(b"\0")[0],
    }


def main():
    ap = argparse.ArgumentParser(
        description="把原厂 boot/vendor_boot 裁成精简参考件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("kind", choices=("boot", "vendor_boot"), help="镜像类型")
    ap.add_argument("image", help="原厂分区镜像（可以是整分区 dd 出来的）")
    ap.add_argument("-o", "--output", required=True, help="输出的参考件路径")
    args = ap.parse_args()

    if not os.path.isfile(args.image):
        print(f"错误: 找不到 {args.image}", file=sys.stderr)
        return 2

    with open(args.image, "rb") as fh:
        buf = fh.read()

    try:
        out, info = (trim_boot if args.kind == "boot" else trim_vendor_boot)(buf)
    except ValueError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "wb") as fh:
        fh.write(out)

    print(f"[输入] {args.image} {len(buf)} B")
    for k, v in info.items():
        if isinstance(v, bytes):
            v = v.decode(errors="replace") or "(空)"
        print(f"  {k:20s} = {v}")
    saved = len(buf) - len(out)
    print(f"[输出] {args.output} {len(out)} B "
          f"（省下 {saved} B，{saved / len(buf) * 100:.1f}%）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
