#!/usr/bin/env python3
# ============================================
# 合成 nabu 的 vendor_boot.img（header v3）
# ============================================
# 把新编的 dtb 换进原厂 vendor_boot，其余段（vendor_ramdisk、各 addr、
# cmdline）全部沿用原厂，用于 fastboot flash vendor_boot_X。
#
# 为什么需要它
# ------------
# nabu 是 boot header v3：dtb **不在** boot 分区里，而在 vendor_boot。
# 所以只刷 boot.img 只换了内核，dtb 还是旧的。要让新编 dtb（含补回的
# RTIC FDT）生效，必须同时刷 vendor_boot。
#
# vendor_boot v3 布局（page_size 对齐）:
#   [header 2112B] [vendor_ramdisk] [dtb]
# header 里带 kernel_addr / ramdisk_addr / tags_addr / dtb_addr —— 这些
# 是 boot v3 所缺的，bootloader 从这里取。本脚本原样保留，不重算。
#
# 用法:
#   python3 make_vendor_boot_v3.py --dtb out/dtb \
#       --ref-vendor-boot <原厂vendor_boot_a.img> -o vendor_boot.img
#
# 退出码: 0 成功 / 1 输入不合法 / 2 参数错误
# ============================================

import argparse
import os
import struct
import sys

VENDOR_MAGIC = b"VNDRBOOT"
FDT_MAGIC = b"\xd0\x0d\xfe\xed"
V3_HEADER_SIZE = 2112


def align_up(value, alignment):
    return ((value + alignment - 1) // alignment) * alignment


def parse_vendor_boot(path):
    with open(path, "rb") as fh:
        buf = fh.read()
    if buf[:8] != VENDOR_MAGIC:
        raise ValueError(f"{path} 不是 vendor_boot 镜像（magic={buf[:8]!r}）")
    hv = struct.unpack("<I", buf[8:12])[0]
    if hv != 3:
        raise ValueError(f"{path} 是 header v{hv}，本脚本只处理 v3")

    info = {
        "header_version": hv,
        "page_size": struct.unpack("<I", buf[12:16])[0],
        "kernel_addr": struct.unpack("<I", buf[16:20])[0],
        "ramdisk_addr": struct.unpack("<I", buf[20:24])[0],
        "vendor_ramdisk_size": struct.unpack("<I", buf[24:28])[0],
        "cmdline": buf[28:28 + 2048].split(b"\0")[0],
        "tags_addr": struct.unpack("<I", buf[2076:2080])[0],
        "name": buf[2080:2096],
        "header_size": struct.unpack("<I", buf[2096:2100])[0],
        "dtb_size": struct.unpack("<I", buf[2100:2104])[0],
        "dtb_addr": struct.unpack("<Q", buf[2104:2112])[0],
        "raw_header": buf[:V3_HEADER_SIZE],
    }
    pg = info["page_size"]
    off = align_up(info["header_size"], pg)
    info["vendor_ramdisk"] = buf[off:off + info["vendor_ramdisk_size"]]
    off += align_up(info["vendor_ramdisk_size"], pg)
    info["dtb"] = buf[off:off + info["dtb_size"]]
    return info


def build_vendor_boot_v3(ref, dtb, vendor_ramdisk=None):
    """基于原厂 header 换掉 dtb。header 只改 dtb_size，其余原样保留。"""
    if vendor_ramdisk is None:
        vendor_ramdisk = ref["vendor_ramdisk"]

    hdr = bytearray(ref["raw_header"])
    struct.pack_into("<I", hdr, 24, len(vendor_ramdisk))
    struct.pack_into("<I", hdr, 2100, len(dtb))
    # kernel_addr / ramdisk_addr / tags_addr / dtb_addr / cmdline / name
    # 全部沿用原厂，不做任何改动

    pg = ref["page_size"]

    def pad(data):
        return data + b"\0" * (align_up(len(data), pg) - len(data))

    return bytes(pad(bytes(hdr)) + pad(vendor_ramdisk) + pad(dtb))


def scan_fdts(buf):
    out = []
    off = 0
    while off + 8 <= len(buf) and buf[off:off + 4] == FDT_MAGIC:
        ts = struct.unpack(">I", buf[off + 4:off + 8])[0]
        if not 0 < ts <= len(buf) - off:
            break
        out.append((off, ts))
        off += ts
    return out, off


def main():
    ap = argparse.ArgumentParser(
        description="合成 nabu vendor_boot.img (header v3)")
    ap.add_argument("--dtb", required=True, help="新编 dtb（须含 RTIC FDT）")
    ap.add_argument("--ref-vendor-boot", required=True,
                    help="原厂 vendor_boot 分区镜像")
    ap.add_argument("--vendor-ramdisk",
                    help="显式指定 vendor_ramdisk，默认沿用原厂")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()

    for p in (args.dtb, args.ref_vendor_boot):
        if not os.path.isfile(p):
            print(f"错误: 找不到 {p}")
            return 2

    try:
        ref = parse_vendor_boot(args.ref_vendor_boot)
    except ValueError as exc:
        print(f"::error::{exc}")
        return 1

    print(f"[参考] {args.ref_vendor_boot}")
    print(f"  page_size      = {ref['page_size']}")
    print(f"  kernel_addr    = 0x{ref['kernel_addr']:x}")
    print(f"  ramdisk_addr   = 0x{ref['ramdisk_addr']:x}")
    print(f"  tags_addr      = 0x{ref['tags_addr']:x}")
    print(f"  dtb_addr       = 0x{ref['dtb_addr']:x}")
    print(f"  vendor_ramdisk = {ref['vendor_ramdisk_size']} 字节")
    print(f"  dtb            = {ref['dtb_size']} 字节")

    with open(args.dtb, "rb") as fh:
        dtb = fh.read()
    if dtb[:4] != FDT_MAGIC:
        print(f"::error::{args.dtb} 不以 FDT magic 开头")
        return 1

    fdts, consumed = scan_fdts(dtb)
    print(f"\n[dtb] {args.dtb} {len(dtb)} 字节，{len(fdts)} 个 FDT")
    if consumed != len(dtb):
        print(f"::error::FDT 覆盖不连续：解析 {consumed} / 共 {len(dtb)} 字节")
        return 1
    rtic = [t for _, t in fdts if t < 1000]
    for i, (o, t) in enumerate(fdts):
        tag = "  <- RTIC" if t < 1000 else ""
        print(f"  #{i} off=0x{o:<8x} size={t}{tag}")
    if not rtic:
        print("::error::dtb 里没有 RTIC FDT（应有一个约 173 字节的小 FDT）")
        print("  缺它设备无法启动 —— 先跑 fix_rtic_dtb.py")
        return 1

    vendor_ramdisk = None
    if args.vendor_ramdisk:
        with open(args.vendor_ramdisk, "rb") as fh:
            vendor_ramdisk = fh.read()
        print(f"\n[vendor_ramdisk] {args.vendor_ramdisk} "
              f"{len(vendor_ramdisk)} 字节（显式指定）")
    else:
        print(f"\n[vendor_ramdisk] 沿用原厂 "
              f"{ref['vendor_ramdisk_size']} 字节")

    img = build_vendor_boot_v3(ref, dtb, vendor_ramdisk)

    with open(args.output, "wb") as fh:
        fh.write(img)

    chk = parse_vendor_boot(args.output)
    ok = (chk["dtb"] == dtb
          and chk["vendor_ramdisk"] == (vendor_ramdisk
                                       or ref["vendor_ramdisk"])
          and chk["kernel_addr"] == ref["kernel_addr"]
          and chk["ramdisk_addr"] == ref["ramdisk_addr"]
          and chk["tags_addr"] == ref["tags_addr"]
          and chk["dtb_addr"] == ref["dtb_addr"]
          and chk["cmdline"] == ref["cmdline"])
    print(f"\n[输出] {args.output} {len(img)} 字节 (header v3)")
    print(f"  回读复验: {'通过' if ok else '失败'}")
    if not ok:
        print("::error::回读复验不一致")
        return 1

    # 精简参考件（tools/make_stock_ref.py 裁出）已把 dtb 段去掉、dtb_size 置 0，
    # 此时算差值没有意义，会显示成 "+全部字节" 而误导人。
    if ref["dtb_size"]:
        delta = len(dtb) - ref["dtb_size"]
        print(f"  dtb 相比原厂 {delta:+d} 字节")
    else:
        print(f"  dtb {len(dtb)} 字节（参考件不含 dtb 段，无法比差值）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
