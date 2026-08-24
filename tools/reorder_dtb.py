#!/usr/bin/env python3
# ============================================
# 按原厂顺序重排 dtb 内的 FDT 序列
# ============================================
# 为什么必须重排
# --------------
# nabu 的 bootloader **按索引**选设备树，不是按 qcom,msm-id 匹配：
# 设备 cmdline 里有 androidboot.dtb_idx=1，即取拼接序列中的第 2 个 FDT。
#
# 原厂 dtb 顺序（与 arch/arm64/boot/dts/qcom/Makefile 的
# nabu-sm8150-overlay.dtbo-base 声明顺序一致）：
#   #0 sm8150.dtb      SM8150 v1
#   #1 sm8150-v2.dtb   SM8150 v2   ← idx=1，设备实际用这个
#   #2 sm8150p.dtb     SM8150P v1
#   #3 sm8150p-v2.dtb  SM8150P v2
#   #4 rtic_mp.dtb     RTIC（173 字节）
#
# 而 arch/arm64/boot/Makefile 里：
#   DTB_OBJS := $(shell find $(obj)/dts/ -name \*.dtb)
# `find` 的返回顺序取决于目录项在文件系统里的物理排列，**不稳定**。
# 实测某次构建得到的顺序是 SM8150P v1 / SM8150P v2 / SM8150 v2 / SM8150 v1，
# 于是 idx=1 落到了 SM8150P v2 —— 芯片型号都不对（SM8150P 是另一款），
# 刷入后设备无法启动。
#
# 症状对照：AK3 包刷入（会写 dtb）开不了机，而只刷 boot.img（不碰 dtb，
# vendor_boot 里仍是原厂 dtb）可以正常启动 —— 差异恰好就在 dtb。
#
# 教训：不要以为 bootloader 会按 msm-id 自动选型。这台设备用的是索引，
# **顺序即一切**，FDT 集合一致不代表 dtb 等价。
#
# 用法:
#   python3 reorder_dtb.py <输入dtb> <输出dtb>
#   python3 reorder_dtb.py --check <dtb>        # 只检查顺序是否正确
#
# 退出码: 0 顺序正确/重排成功 / 1 顺序错误(--check)或输入不合法 / 2 参数错误
# ============================================

import re
import struct
import sys

FDT_MAGIC = b"\xd0\x0d\xfe\xed"

# 原厂顺序，取自 dts/qcom/Makefile 的 nabu-sm8150-overlay.dtbo-base
EXPECTED_ORDER = ["SM8150 v1", "SM8150 v2", "SM8150P v1", "SM8150P v2"]

MODEL_RE = re.compile(rb"Qualcomm Technologies, Inc\. (SM8150P? v[12]) SoC")
RTIC_MAX_SIZE = 1000


def scan(buf):
    """切分拼接的 FDT 序列，识别各自 model。"""
    items = []
    off = 0
    while off + 8 <= len(buf) and buf[off:off + 4] == FDT_MAGIC:
        totalsize = struct.unpack(">I", buf[off + 4:off + 8])[0]
        if not 0 < totalsize <= len(buf) - off:
            raise ValueError(f"FDT #{len(items)} totalsize 非法: {totalsize}")
        data = buf[off:off + totalsize]
        match = MODEL_RE.search(data)
        if match:
            model = match.group(1).decode()
        elif totalsize < RTIC_MAX_SIZE:
            model = "RTIC"
        else:
            model = "UNKNOWN"
        items.append({"model": model, "size": totalsize, "data": data})
        off += totalsize
    if off != len(buf):
        raise ValueError(f"FDT 覆盖不连续: 解析 {off} / 共 {len(buf)} 字节")
    return items


def describe(items):
    for i, it in enumerate(items):
        mark = "   <- idx=1, bootloader 选这个" if i == 1 else ""
        print(f"  #{i} {it['size']:<8} {it['model']}{mark}")


def main():
    args = [a for a in sys.argv[1:]]
    check_only = "--check" in args
    if check_only:
        args.remove("--check")
    if (check_only and len(args) != 1) or (not check_only and len(args) != 2):
        print(__doc__)
        return 2

    with open(args[0], "rb") as fh:
        buf = fh.read()
    try:
        items = scan(buf)
    except ValueError as exc:
        print(f"::error::{exc}")
        return 1

    print(f"{args[0]}: {len(items)} 个 FDT，共 {len(buf)} 字节")
    describe(items)

    platform = [it for it in items if it["model"] in EXPECTED_ORDER]
    rtic = [it for it in items if it["model"] == "RTIC"]
    unknown = [it for it in items if it["model"] == "UNKNOWN"]

    if unknown:
        print(f"::error::有 {len(unknown)} 个 FDT 无法识别 model")
        return 1
    if len(platform) != 4:
        print(f"::error::期望 4 个平台 FDT，实际 {len(platform)}")
        return 1
    if len(rtic) != 1:
        print(f"::error::期望 1 个 RTIC FDT，实际 {len(rtic)}")
        print("  缺 RTIC 设备无法启动，先跑 fix_rtic_dtb.py")
        return 1

    current = [it["model"] for it in platform]
    correct = current == EXPECTED_ORDER

    if check_only:
        if correct:
            print(f"\n[OK] 平台 FDT 顺序正确: {' / '.join(current)}")
            return 0
        print(f"\n::error::平台 FDT 顺序错误")
        print(f"  实际: {' / '.join(current)}")
        print(f"  应为: {' / '.join(EXPECTED_ORDER)}")
        print(f"  设备 androidboot.dtb_idx=1 会取到 "
              f"{current[1]}，而应是 {EXPECTED_ORDER[1]}")
        return 1

    by_model = {it["model"]: it for it in platform}
    ordered = [by_model[m] for m in EXPECTED_ORDER] + rtic
    out = b"".join(it["data"] for it in ordered)

    if len(out) != len(buf):
        print(f"::error::重排后大小变化 {len(buf)} -> {len(out)}")
        return 1

    with open(args[1], "wb") as fh:
        fh.write(out)

    print(f"\n重排后 -> {args[1]}")
    describe(scan(out))
    print(f"\n[OK] {'顺序本已正确，原样输出' if correct else '已按原厂顺序重排'}"
          f"（{len(out)} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
