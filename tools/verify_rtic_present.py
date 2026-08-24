#!/usr/bin/env python3
"""校验 dtb 内含且仅含一个 RTIC FDT。

nabu 的 arch/arm64/boot/Makefile 只在 RTIC_MPGEN 有定义时才把
rtic_mp.dtb 加进 DTB_OBJS，而那需要高通闭源 MPGen 工具。因此常规
编译产出的 dtb 必然缺少原厂那个 173 字节的 RTIC FDT —— 实测缺它
设备无法启动，所以这里做硬校验。

用法: verify_rtic_present.py <dtb路径>
退出码: 0 通过 / 1 校验失败 / 2 参数错误
"""
import struct
import sys

FDT_MAGIC = b"\xd0\x0d\xfe\xed"
RTIC_MAX_SIZE = 1000       # RTIC FDT 约 173 字节，平台 FDT 都在 49 万以上


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    path = sys.argv[1]
    with open(path, "rb") as fh:
        buf = fh.read()

    off, sizes = 0, []
    while off + 8 <= len(buf) and buf[off:off + 4] == FDT_MAGIC:
        totalsize = struct.unpack(">I", buf[off + 4:off + 8])[0]
        if not 0 < totalsize <= len(buf) - off:
            print(f"::error::FDT #{len(sizes)} totalsize 非法: {totalsize}")
            return 1
        sizes.append(totalsize)
        off += totalsize

    if off != len(buf):
        print(f"::error::{path} FDT 覆盖不连续: 解析 {off} / 共 {len(buf)} 字节")
        return 1

    rtic = [s for s in sizes if s < RTIC_MAX_SIZE]
    platform = [s for s in sizes if s >= RTIC_MAX_SIZE]
    print(f"{path}: {len(sizes)} 个 FDT，共 {len(buf)} 字节")
    print(f"  平台 FDT: {platform}")
    print(f"  RTIC FDT: {rtic}")

    if len(rtic) != 1:
        print(f"::error::期望恰好 1 个 RTIC FDT，实际 {len(rtic)} 个")
        print("  缺少它设备无法启动，检查 build.sh 的 RTIC 修补步骤")
        return 1
    if len(platform) != 4:
        print(f"::error::期望 4 个平台 FDT，实际 {len(platform)} 个")
        return 1

    print(f"[OK] RTIC FDT 就位（{rtic[0]} 字节）+ 4 个平台 FDT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
