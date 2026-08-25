#!/usr/bin/env python3
"""校验内核 Image 内嵌的 IKCFG 配置与本次构建真实的 .config 是否一致。

背景：本仓库曾把 kernel/Makefile 里 config_data.gz 的依赖从 $(KCONFIG_CONFIG)
改成硬编码的 arch/arm64/configs/nabu-stock_defconfig（提交 26fb633ed1，源自
Pixel/marlin，用于躲避会读 /proc/config.gz 的 userspace 检查）。后果是
/proc/config.gz 与 Image 内嵌 IKCFG 段永远输出原厂假配置，与真实 .config 脱钩，
曾导致「CONFIG_PROC_CHILDREN 配了没生效」的误判（实际已生效，是 config.gz 在说谎）。

现在默认输出真实 .config。本脚本作为门禁，确保该契约不被无声破坏。

用法：
    verify_ikconfig.py <Image> <real .config>
        断言内嵌配置与 .config 一致（默认模式）
    verify_ikconfig.py --expect-masquerade <Image> <real .config>
        反向断言：内嵌配置与 .config 不同（IKCONFIG_STOCK_MASQUERADE=1 时用）

退出码：0 = 符合预期；1 = 不符合；2 = 无法取证（缺 IKCFG 段等）。
"""

import argparse
import gzip
import hashlib
import sys

MAGIC_START = b"IKCFG_ST"
MAGIC_END = b"IKCFG_ED"


def extract_ikconfig(image_path):
    """从内核 Image 中提取并解压 IKCFG 段，返回配置正文（str）。"""
    with open(image_path, "rb") as f:
        blob = f.read()

    start = blob.find(MAGIC_START)
    if start < 0:
        # 内核可能是 gzip 压缩的（Image.gz），先解一层再找
        gz_at = blob.find(b"\x1f\x8b\x08")
        if gz_at < 0:
            return None, "Image 内既无 IKCFG_ST 也无 gzip 魔数"
        try:
            blob = gzip.decompress(blob[gz_at:])
        except Exception as exc:  # noqa: BLE001 - 仅用于报告
            return None, f"解压内层 gzip 失败: {exc}"
        start = blob.find(MAGIC_START)
        if start < 0:
            return None, "解压后仍找不到 IKCFG_ST（CONFIG_IKCONFIG 可能未启用）"

    end = blob.find(MAGIC_END, start)
    if end < 0:
        return None, "找到 IKCFG_ST 但缺 IKCFG_ED"

    payload = blob[start + len(MAGIC_START):end]
    try:
        text = gzip.decompress(payload).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - 仅用于报告
        return None, f"解压 IKCFG 载荷失败: {exc}"
    return text, None


def config_pairs(text):
    """把配置正文解析成 {符号: 取值} —— 忽略注释与空行，'# CONFIG_X is not set' 记为 n。"""
    pairs = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# CONFIG_") and line.endswith(" is not set"):
            pairs[line[2:].split(" ", 1)[0]] = "n"
        elif line.startswith("CONFIG_") and "=" in line:
            key, _, val = line.partition("=")
            pairs[key] = val
    return pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="内核 Image 路径")
    parser.add_argument("real_config", help="本次构建真实的 .config 路径")
    parser.add_argument(
        "--expect-masquerade",
        action="store_true",
        help="反向断言：内嵌配置应与 .config 不同（伪装模式下使用）",
    )
    args = parser.parse_args()

    embedded, err = extract_ikconfig(args.image)
    if embedded is None:
        print(f"::error::无法从 {args.image} 提取 IKCFG: {err}")
        return 2

    with open(args.real_config, "r", encoding="utf-8", errors="replace") as f:
        real = f.read()

    emb_md5 = hashlib.md5(embedded.encode()).hexdigest()
    real_md5 = hashlib.md5(real.encode()).hexdigest()
    identical = embedded == real

    print(f"内嵌 IKCFG : {len(embedded.splitlines())} 行  md5 {emb_md5}")
    print(f"真实 .config: {len(real.splitlines())} 行  md5 {real_md5}")

    if args.expect_masquerade:
        if identical:
            print("::error::预期伪装模式（内嵌应为原厂配置），但内嵌配置与真实 .config 相同")
            return 1
        print("[OK] 伪装模式：内嵌配置与真实 .config 不同，符合预期")
        return 0

    if identical:
        print("[OK] 内嵌 IKCFG 与真实 .config 逐字节一致 —— /proc/config.gz 可信")
        return 0

    print("::error::内嵌 IKCFG 与真实 .config 不一致 —— /proc/config.gz 会说谎")

    emb_pairs = config_pairs(embedded)
    real_pairs = config_pairs(real)

    for key in ("CONFIG_LOCALVERSION",):
        if emb_pairs.get(key) != real_pairs.get(key):
            print(f"  {key}: 内嵌={emb_pairs.get(key)!r} 真实={real_pairs.get(key)!r}")

    diffs = [
        (k, emb_pairs.get(k), real_pairs[k])
        for k in sorted(real_pairs)
        if emb_pairs.get(k) != real_pairs[k]
    ]
    print(f"  取值不一致的符号: {len(diffs)}")
    for key, got, want in diffs[:20]:
        print(f"    {key}: 内嵌={got} 真实={want}")
    if len(diffs) > 20:
        print(f"    ...另有 {len(diffs) - 20} 项")

    print(
        "  提示：检查 kernel/Makefile 里 config_data.gz 的依赖是否被改成了"
        " 硬编码 defconfig（应为 $(KCONFIG_CONFIG)）"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
