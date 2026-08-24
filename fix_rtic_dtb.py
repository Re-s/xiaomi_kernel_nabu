#!/usr/bin/env python3
# ============================================
# RTIC DTB Fixer for Xiaomi Pad 5 (nabu)
# 小米平板5 dtb RTIC 节点修复工具
# ============================================
# 版本: 1.0.0
#
# 用途:
#   部分第三方内核包在重新编译 dtb 时只保留了平台 FDT，
#   丢失了原厂 dtb 尾部的 qcom,rtic-id / MP_DATA 节点
#   （高通 RTIC 运行时完整性检查的度量数据）。
#   本工具从原厂 vendor_boot.img 提取该 FDT，追加回目标 dtb。
#
# 背景:
#   nabu 使用 boot header v3 布局，dtb 存放在 vendor_boot 分区内，
#   而非 boot 分区。boot v3 头没有 dtb_size 字段。
#
# 用法:
#   python3 fix_rtic_dtb.py <RTIC来源> <待修dtb> <输出dtb>
#
#   <RTIC来源> 支持两种形式，按内容自动识别：
#     1. 原厂 vendor_boot.img（VNDRBOOT 魔数）—— 提取 dtb 段后定位 RTIC FDT
#     2. 裸 RTIC FDT 文件（d00dfeed 魔数，173 字节）—— 直接使用
#   形式 2 便于提交进 git 并在 CI 中使用（vendor_boot.img 有 100MB，
#   而 RTIC FDT 只有 173 字节）。
#
# 附加模式:
#   --extract <vendor_boot.img> <输出.fdt>
#     从 vendor_boot.img 中抽出 RTIC FDT 存为独立文件，用于生成上述形式 2。
#
# 示例:
#   # 1. 解包第三方 AnyKernel3 zip
#   unzip -d work third_party_kernel.zip
#   # 2. 修复 dtb（任选一种 RTIC 来源）
#   python3 fix_rtic_dtb.py stock/vendor_boot.img work/dtb work/dtb.fixed
#   python3 fix_rtic_dtb.py stock/rtic.fdt        work/dtb work/dtb.fixed
#   # 3. 替换并重新打包
#   mv work/dtb.fixed work/dtb
#   (cd work && zip -r9 ../fixed_kernel.zip .)
#
#   # 一次性生成可提交的 rtic.fdt
#   python3 fix_rtic_dtb.py --extract stock/vendor_boot.img stock/rtic.fdt
# ============================================

import hashlib
import os
import struct
import sys

FDT_MAGIC = b"\xd0\x0d\xfe\xed"
VENDOR_BOOT_MAGIC = b"VNDRBOOT"
RTIC_MARKER = b"qcom,rtic-id"

# vendor_boot header v3/v4 字段偏移
VB_OFF_HEADER_SIZE = 2096
VB_OFF_DTB_SIZE = 2100


def scan_fdts(buf):
    """
    严格扫描拼接的 FDT 序列。

    dtb 是多个 FDT 顺序拼接的裸格式（无索引表），因此按每个 FDT
    头声明的 totalsize 推进，并校验头部字段合法性，避免把内核数据
    里偶然出现的 d00dfeed 误判为 FDT 起点。
    """
    found = []
    i = 0
    while i < len(buf) - 40:
        if buf[i:i + 4] == FDT_MAGIC:
            totalsize, off_struct, off_strings, off_rsvmap, version, last_comp = \
                struct.unpack(">6I", buf[i + 4:i + 28])
            size_strings, size_struct = struct.unpack(">2I", buf[i + 32:i + 40])
            valid = (
                0 < totalsize <= len(buf) - i
                and version in (16, 17)
                and last_comp == 16
                and off_struct + size_struct <= totalsize
                and off_strings + size_strings <= totalsize
            )
            if valid:
                found.append({
                    "offset": i,
                    "size": totalsize,
                    "version": version,
                    "data": buf[i:i + totalsize],
                })
                i += totalsize
                continue
        i += 1
    return found


def extract_stock_dtb(vendor_boot_path):
    """从 vendor_boot.img 中提取 dtb 段。"""
    with open(vendor_boot_path, "rb") as fh:
        vb = fh.read()

    if vb[:8] != VENDOR_BOOT_MAGIC:
        raise ValueError(f"{vendor_boot_path}: 不是 vendor_boot 镜像 "
                         f"(魔数 {vb[:8]!r}，应为 {VENDOR_BOOT_MAGIC!r})")

    header_version, page_size, _kaddr, _raddr, vendor_ramdisk_size = \
        struct.unpack("<5I", vb[8:28])
    header_size, dtb_size = struct.unpack(
        "<2I", vb[VB_OFF_HEADER_SIZE:VB_OFF_HEADER_SIZE + 8])

    if header_version < 3:
        raise ValueError(f"vendor_boot header v{header_version} 不受支持（需要 v3/v4）")
    if dtb_size == 0:
        raise ValueError("vendor_boot 中 dtb_size 为 0，无 dtb 可提取")

    def page_align(n):
        return (n + page_size - 1) // page_size * page_size

    dtb_offset = page_align(page_align(header_size) + vendor_ramdisk_size)

    print(f"[原厂] {os.path.basename(vendor_boot_path)}")
    print(f"       header_version={header_version} page_size={page_size}")
    print(f"       vendor_ramdisk_size={vendor_ramdisk_size}")
    print(f"       dtb @0x{dtb_offset:x} size={dtb_size}")

    return vb[dtb_offset:dtb_offset + dtb_size]


def load_rtic_fdt(source_path):
    """
    从 RTIC 来源取出那一个 RTIC FDT，返回 dict(size/data)。

    两种来源按魔数自动识别：
      - VNDRBOOT：原厂 vendor_boot.img，提取 dtb 段后扫描定位
      - d00dfeed：裸 RTIC FDT 文件，直接校验后使用（CI 场景，173 字节可入 git）
    """
    with open(source_path, "rb") as fh:
        head = fh.read(8)

    if head[:4] == FDT_MAGIC:
        with open(source_path, "rb") as fh:
            blob = fh.read()
        fdts = scan_fdts(blob)
        if len(fdts) != 1 or fdts[0]["size"] != len(blob):
            raise ValueError(
                f"{source_path}: 不是单个完整 FDT 文件"
                f"（扫出 {len(fdts)} 个，文件 {len(blob)} 字节）")
        if RTIC_MARKER not in blob:
            raise ValueError(f"{source_path}: FDT 中没有 {RTIC_MARKER.decode()} 属性")
        print(f"[原厂] {os.path.basename(source_path)}（裸 RTIC FDT）")
        print(f"       size={len(blob)} md5={hashlib.md5(blob).hexdigest()}")
        return fdts[0]

    if head == VENDOR_BOOT_MAGIC:
        stock_dtb = extract_stock_dtb(source_path)
        stock_fdts = scan_fdts(stock_dtb)
        report("原厂dtb", stock_fdts, len(stock_dtb))
        rtic = [f for f in stock_fdts if RTIC_MARKER in f["data"]]
        if len(rtic) != 1:
            raise ValueError(
                f"原厂 dtb 中找到 {len(rtic)} 个 RTIC FDT，预期恰好 1 个")
        return rtic[0]

    raise ValueError(
        f"{source_path}: 无法识别的 RTIC 来源"
        f"（魔数 {head[:8]!r}，应为 {VENDOR_BOOT_MAGIC!r} 或 {FDT_MAGIC!r}）")


def do_extract(vendor_boot_path, output_path):
    """--extract：把 RTIC FDT 抽成独立文件，便于提交进 git 供 CI 使用。"""
    rtic = load_rtic_fdt(vendor_boot_path)
    with open(output_path, "wb") as fh:
        fh.write(rtic["data"])

    with open(output_path, "rb") as fh:
        back = fh.read()
    if back != rtic["data"]:
        print("错误: 写出内容与提取内容不一致")
        return 1

    print(f"\n[输出] {output_path}")
    print(f"       {len(back)} 字节 md5={hashlib.md5(back).hexdigest()}")
    print(f"       sha256={hashlib.sha256(back).hexdigest()}")
    print("\n[OK] RTIC FDT 已抽出，可提交至仓库供 CI 使用")
    return 0


def report(label, fdts, total_len):
    covered = sum(f["size"] for f in fdts)
    print(f"[{label}] {len(fdts)} 个 FDT，覆盖 {covered}/{total_len} 字节"
          f"{'' if covered == total_len else '  ← 有未覆盖残留'}")
    for idx, f in enumerate(fdts):
        tag = "  <- RTIC" if RTIC_MARKER in f["data"] else ""
        digest = hashlib.md5(f["data"]).hexdigest()[:16]
        print(f"       #{idx} off=0x{f['offset']:<8x} size={f['size']:<8} "
              f"md5={digest}{tag}")


USAGE = """用法:
  fix_rtic_dtb.py <RTIC来源> <待修dtb> <输出dtb>
  fix_rtic_dtb.py --extract <原厂vendor_boot.img> <输出.fdt>

<RTIC来源> 可以是原厂 vendor_boot.img，也可以是裸 RTIC FDT 文件
（由 --extract 生成，173 字节，便于提交进 git 供 CI 使用）。"""


def main():
    argv = sys.argv[1:]

    if argv and argv[0] == "--extract":
        if len(argv) != 3:
            print(USAGE)
            return 2
        vendor_boot_path, output_path = argv[1:3]
        if not os.path.isfile(vendor_boot_path):
            print(f"错误: 找不到文件 {vendor_boot_path}")
            return 2
        try:
            return do_extract(vendor_boot_path, output_path)
        except (ValueError, struct.error) as exc:
            print(f"错误: {exc}")
            return 1

    if len(argv) != 3:
        print(USAGE)
        return 2

    rtic_source_path, target_dtb_path, output_path = argv

    for p in (rtic_source_path, target_dtb_path):
        if not os.path.isfile(p):
            print(f"错误: 找不到文件 {p}")
            return 2

    # 1. 取出 RTIC FDT（来源可为 vendor_boot.img 或裸 FDT 文件）
    try:
        rtic = load_rtic_fdt(rtic_source_path)
    except (ValueError, struct.error) as exc:
        print(f"错误: {exc}")
        return 1
    print(f"\n[RTIC] size={rtic['size']} "
          f"md5={hashlib.md5(rtic['data']).hexdigest()}")

    # 2. 检查目标 dtb
    with open(target_dtb_path, "rb") as fh:
        target = fh.read()
    target_fdts = scan_fdts(target)
    print()
    report("待修dtb", target_fdts, len(target))

    if any(RTIC_MARKER in f["data"] for f in target_fdts):
        print("\n[跳过] 目标 dtb 已包含 RTIC FDT，无需修复")
        return 3

    # 3. 追加并复验
    fixed = target + rtic["data"]
    with open(output_path, "wb") as fh:
        fh.write(fixed)

    print(f"\n[输出] {output_path}")
    print(f"       {len(target)} + {rtic['size']} = {len(fixed)} 字节")

    with open(output_path, "rb") as fh:
        verify_fdts = scan_fdts(fh.read())
    print()
    report("复验", verify_fdts, len(fixed))

    if len(verify_fdts) != len(target_fdts) + 1:
        print("\n错误: FDT 数量未按预期增加 1")
        return 1
    if RTIC_MARKER not in verify_fdts[-1]["data"]:
        print("\n错误: 尾部 FDT 不是 RTIC")
        return 1
    if sum(f["size"] for f in verify_fdts) != len(fixed):
        print("\n错误: FDT 覆盖字节数与文件长度不符")
        return 1

    print("\n[OK] 结构校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
