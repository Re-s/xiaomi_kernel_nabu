#!/bin/bash

# 颜色定义
yellow='\033[0;33m'
white='\033[0m'
red='\033[0;31m'
green='\033[0;32m'
cyan='\033[0;36m'

# 输出带颜色的消息函数
color_echo() {
    local color=$1
    shift
    echo -e "${color}$*${white}"
}

# 确保脚本在出错时退出
set -e

# 动态定位脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || {
    color_echo "$red" "无法切换到脚本所在目录: $SCRIPT_DIR"
    exit 1
}
color_echo "$green" "工作目录: $SCRIPT_DIR"

# 参数处理
# KERNEL_NAME / KERNEL_VERSION / KERNEL_SUFFIX 支持环境变量覆盖，
# 便于 CI（workflow_dispatch 输入）与本地临时改名，无需改动本文件 ——
# 这样跟上游同步时不会在这里产生冲突。
#
# 最终内核名 = <内核版本>-<KERNEL_NAME>-<KERNEL_SUFFIX>-<日期>
#   例: 4.14.336-wakmemlody-nabu-hyperos1-260824
# KERNEL_SUFFIX 置空则退化为 <内核版本>-<KERNEL_NAME>-<日期>。
TARGET_DEVICE="nabu"
KERNEL_NAME="${KERNEL_NAME:-wakmemlody}"
KERNEL_VERSION="${KERNEL_VERSION:-v1.0}"
KERNEL_SUFFIX="${KERNEL_SUFFIX:-nabu-hyperos1}"
NO_CLEAN=false
MAKE_FLAGS=""
NUM_JOBS=$(nproc --all)

# 解析目标设备
if [ $# -lt 1 ] || [[ "$1" == --* ]]; then
    TARGET_DEVICE="nabu"
    color_echo "$yellow" "未指定设备，使用默认设备: $TARGET_DEVICE"
else
    TARGET_DEVICE="$1"
    shift || true
fi

# 处理选项参数
while [ $# -gt 0 ]; do
    case "$1" in
        -j)                 
            if [[ "$2" =~ ^[0-9]+$ ]]; then
                NUM_JOBS="$2"
                shift 2
            else
                color_echo "$red" "错误: -j 参数后面必须跟数字"
                exit 1
            fi
            ;;
        --noclean)
            NO_CLEAN=true
            shift
            ;;
        --)
            shift
            MAKE_FLAGS="$*"
            break
            ;;
        *)
            color_echo "$yellow" "忽略未知选项: $1"
            shift
            ;;
    esac
done

# 唯一构建目录
BUILD_DIR="../Releases_${TARGET_DEVICE}_${KERNEL_NAME}"
color_echo "$green" "使用独立构建目录: $BUILD_DIR"

# 修改产物路径
MAKE_ARGS="O=$BUILD_DIR"

# 编译信息
MAKE_ARGS+=" KBUILD_BUILD_HOST=Kuugo"
MAKE_ARGS+=" KBUILD_BUILD_USER=kuugo"

# 修改编译参数设置
MAKE_ARGS+=" ARCH=arm64"
MAKE_ARGS+=" SUBARCH=arm64"

# LLVM toolchain（系统 clang）
MAKE_ARGS+=" CC=clang"
MAKE_ARGS+=" LD=ld.lld"
MAKE_ARGS+=" NM=llvm-nm"
MAKE_ARGS+=" OBJDUMP=llvm-objdump"
MAKE_ARGS+=" STRIP=llvm-strip"

# 交叉编译工具链（系统 GNU binutils）
MAKE_ARGS+=" CROSS_COMPILE=aarch64-linux-gnu-"

# /proc/config.gz 与 Image 内嵌 IKCFG 段的数据源。
# 默认输出本次构建真实的 .config，可直接用于验证配置改动；
# 置 IKCONFIG_STOCK_MASQUERADE=1 则内嵌原厂 nabu-stock_defconfig 伪装原厂内核。
if [[ "${IKCONFIG_STOCK_MASQUERADE:-0}" =~ ^(1|y|yes)$ ]]; then
    MAKE_ARGS+=" IKCONFIG_STOCK_MASQUERADE=1"
    IKCONFIG_MODE="伪装原厂（nabu-stock_defconfig）"
else
    IKCONFIG_MODE="真实 .config"
fi

# 检查设备配置是否存在
if [[ ! -f "$SCRIPT_DIR/arch/arm64/configs/${TARGET_DEVICE}_defconfig" ]]; then
    color_echo "$red" "错误: 未找到目标设备 [$TARGET_DEVICE] 的配置"
    color_echo "$yellow" "可用设备配置:"
    ls "$SCRIPT_DIR/arch/arm64/configs/"*_defconfig | sed "s/.*\///; s/_defconfig//" | xargs printf "  %s\n"
    exit 1
fi

# 显示环境信息
color_echo "$cyan" "=============================================="
color_echo "$green" "构建配置信息:"
color_echo "$cyan" "=============================================="
color_echo "$yellow" "目标设备:    $TARGET_DEVICE"
color_echo "$yellow" "内核名称:    $KERNEL_NAME"
color_echo "$yellow" "内核版本:    $KERNEL_VERSION"
color_echo "$yellow" "编译线程数:  $NUM_JOBS"
color_echo "$yellow" "KernelSU:    禁用"
color_echo "$yellow" "config.gz:   $IKCONFIG_MODE"
color_echo "$yellow" "清理:        $($NO_CLEAN && echo "跳过" || echo "执行")"
color_echo "$cyan" "=============================================="

color_echo "$green" "[clang 版本信息]:"
clang --version

# 清理工作区
if ! $NO_CLEAN; then
    color_echo "$yellow" "清理工作区..."
    rm -rf "$BUILD_DIR"
else
    color_echo "$yellow" "跳过清理步骤..."
fi

# KernelSU-Next 源码清理与同步
color_echo "$green" "正在检查并清理旧的 KernelSU-Next 源码..."

# 移除源码根目录下的 KernelSU 文件夹
if [ -d "KernelSU-Next" ] && ! $NO_CLEAN; then
    color_echo "$yellow" "移除旧的 KernelSU 目录..."
    rm -rf KernelSU-Next
fi

# 移除 drivers/kernelsu 文件夹
if [ -d "drivers/kernelsu" ] && ! $NO_CLEAN; then
    color_echo "$yellow" "移除旧的 drivers/kernelsu 目录..."
    rm -rf drivers/kernelsu
fi

# 拉取并安装指定的 KernelSU 版本
color_echo "$green" "正在下载并配置 KernelSU-Next"
curl -LSs "https://raw.githubusercontent.com/KernelSU-Next/KernelSU-Next/next/kernel/setup.sh" | bash -s legacy

# 拼本地版本串。CONFIG_LOCALVERSION 会直接追加在内核版本号后面，
# 所以必须以 - 开头。日期用 UTC，与 CI 保持一致。
if [[ -n "$KERNEL_SUFFIX" ]]; then
    LOCAL_VERSION_DATE="-${KERNEL_NAME}-${KERNEL_SUFFIX}-$(date +%y%m%d)"
else
    LOCAL_VERSION_DATE="-${KERNEL_NAME}-$(date +%y%m%d)"
fi
color_echo "$green" "内核名将为: $(make -s kernelversion 2>/dev/null || echo 4.14.x)${LOCAL_VERSION_DATE}"
touch .scmversion

# 配置内核
color_echo "$green" "配置 ${TARGET_DEVICE}_defconfig..."
make $MAKE_ARGS "${TARGET_DEVICE}_defconfig"

# 设置本地版本
./scripts/config --file "$BUILD_DIR/.config" --set-str CONFIG_LOCALVERSION "$LOCAL_VERSION_DATE"

# 记录开始时间
START_TIME=$(date +%s)

# 编译内核
color_echo "$green" "开始编译内核 (使用 $NUM_JOBS 个线程)..."
make $MAKE_ARGS -j$NUM_JOBS $MAKE_FLAGS

# 检查编译结果
IMAGE_PATH="$BUILD_DIR/arch/arm64/boot/Image"
if [[ ! -f "$IMAGE_PATH" ]]; then
    color_echo "$red" "错误: 未找到内核镜像 [$IMAGE_PATH]，编译失败"
    exit 1
fi

# 计算编译时间
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
MINUTES=$((DURATION / 60))
SECONDS=$((DURATION % 60))

color_echo "$green" "编译成功! 耗时: ${MINUTES}分${SECONDS}秒"

DTB_PATH="$BUILD_DIR/arch/arm64/boot/dtb"

DTBO_PATH="$BUILD_DIR/arch/arm64/boot/dtbo.img"

ANY_KERNEL_DIR="$SCRIPT_DIR/anykernel"

cp "$IMAGE_PATH" "$ANY_KERNEL_DIR"
cp "$DTBO_PATH" "$ANY_KERNEL_DIR"
if [[ -f "$DTB_PATH" ]]; then
    # ------------------------------------------------------------------
    # 补回 RTIC FDT
    #
    # 原厂 vendor_boot 的 dtb 尾部有一个 173 字节、含 qcom,rtic-id 与
    # MP_DATA 的 FDT。arch/arm64/boot/Makefile 里 rtic_mp.dtb 只在
    # RTIC_MPGEN 有定义时才加入 DTB_OBJS（那需要高通闭源 MPGen 工具），
    # 因此常规编译产出的 dtb **必然缺少它**。
    #
    # 实测：缺这个 FDT 的 dtb 刷入后设备无法正常启动；用原厂 RTIC FDT
    # 补齐后启动正常。所以这一步不是可选项。
    #
    # fix_rtic_dtb.py 是幂等的：dtb 已含 RTIC 时返回 3 并跳过。
    # ------------------------------------------------------------------
    RTIC_FDT="$SCRIPT_DIR/stock/rtic.fdt"
    RTIC_FIXER="$SCRIPT_DIR/fix_rtic_dtb.py"
    if [[ -f "$RTIC_FDT" && -f "$RTIC_FIXER" ]]; then
        color_echo "$green" "补回 RTIC FDT 到 dtb..."
        set +e
        python3 "$RTIC_FIXER" "$RTIC_FDT" "$DTB_PATH" "$DTB_PATH.fixed"
        rtic_rc=$?
        set -e
        case $rtic_rc in
            0)
                mv "$DTB_PATH.fixed" "$DTB_PATH"
                color_echo "$green" "RTIC FDT 已追加"
                ;;
            3)
                rm -f "$DTB_PATH.fixed"
                color_echo "$yellow" "dtb 已含 RTIC FDT，跳过"
                ;;
            *)
                color_echo "$red" "错误: RTIC 修补失败 (rc=$rtic_rc)"
                exit 1
                ;;
        esac
    else
        color_echo "$red" "错误: 缺少 $RTIC_FDT 或 $RTIC_FIXER"
        color_echo "$red" "      没有 RTIC FDT 的 dtb 会导致设备无法启动"
        exit 1
    fi

    # ------------------------------------------------------------------
    # 按原厂顺序重排 FDT —— 必须做，否则设备无法启动
    #
    # nabu 的 bootloader **按索引**选设备树：cmdline 里
    # androidboot.dtb_idx=1，取拼接序列的第 2 个 FDT。原厂顺序与
    # dts/qcom/Makefile 的 nabu-sm8150-overlay.dtbo-base 声明一致：
    #   sm8150 / sm8150-v2 / sm8150p / sm8150p-v2 / rtic
    #
    # 而 arch/arm64/boot/Makefile 用
    #   DTB_OBJS := $(shell find $(obj)/dts/ -name \*.dtb)
    # find 的顺序取决于文件系统目录项排列，**不稳定**。实测某次构建
    # 得到 SM8150P v1 / SM8150P v2 / SM8150 v2 / SM8150 v1，于是 idx=1
    # 落到 SM8150P v2 —— 芯片型号都不对，刷入后开不了机。
    #
    # 症状：AK3 包（会写 dtb）开不了机，只刷 boot.img（不碰 dtb）正常。
    # ------------------------------------------------------------------
    color_echo "$green" "按原厂顺序重排 dtb 内的 FDT..."
    python3 "$SCRIPT_DIR/tools/reorder_dtb.py" "$DTB_PATH" "$DTB_PATH.ordered"
    mv "$DTB_PATH.ordered" "$DTB_PATH"

    # 硬门禁：顺序不对就不该出包
    python3 "$SCRIPT_DIR/tools/reorder_dtb.py" --check "$DTB_PATH"

    cp "$DTB_PATH" "$ANY_KERNEL_DIR"
else
    color_echo "$red" "错误: 未检测到 DTB 文件 [$DTB_PATH]"
    color_echo "$red" "      nabu 需要 dtb，检查 defconfig 是否启用"
    color_echo "$red" "      CONFIG_MACH_XIAOMI_SM8150 与 CONFIG_BUILD_ARM64_DT_OVERLAY"
    exit 1
fi

# ------------------------------------------------------------------
# 可选：合成 boot.img / vendor_boot.img（fastboot flash 用）
#
# 需要原厂镜像作参考（提供 ramdisk、os_version、各 addr、cmdline）。
# 放到 stock/ 下即自动启用；缺失就跳过，只出 AK3 包。
#
# 优先用 tools/make_stock_ref.py 裁出的**精简参考件**（推荐，体积小得多；
# 实测 boot 49.4→18.9 MiB、vendor_boot 1.9 MiB→8 KiB，且合成结果与用全量
# 镜像逐字节相同 —— 因为原厂内核与原厂 dtb 本来就会被新产物替换掉）：
#
#   stock/boot-ref.img          精简 boot 参考件
#   stock/vendor_boot-ref.img   精简 vendor_boot 参考件
#
# 也接受直接放全量原厂分区镜像（向后兼容）：
#
#   stock/boot.img         原厂 boot 分区   （dd if=/dev/block/by-name/boot_a）
#   stock/vendor_boot.img  原厂 vendor_boot （dd if=.../vendor_boot_a）
#
# 注意 nabu 是 boot header v3：dtb 在 vendor_boot 而非 boot，所以
# **只刷 boot.img 不会更新 dtb**。要让新 dtb（含 RTIC）生效必须两个都刷。
# ------------------------------------------------------------------
pick_ref() {
    # 依次探测候选路径，返回第一个存在的
    local p
    for p in "$@"; do
        [[ -f "$p" ]] && { printf '%s' "$p"; return 0; }
    done
    return 1
}
REF_BOOT="$(pick_ref "$SCRIPT_DIR/stock/boot-ref.img" "$SCRIPT_DIR/stock/boot.img" || true)"
REF_VB="$(pick_ref "$SCRIPT_DIR/stock/vendor_boot-ref.img" "$SCRIPT_DIR/stock/vendor_boot.img" || true)"

# BUILD_BOOT_IMAGES=false 可显式跳过（CI 的 workflow_dispatch 输入）
if [[ "${BUILD_BOOT_IMAGES:-true}" != "true" ]]; then
    color_echo "$yellow" "BUILD_BOOT_IMAGES=false，跳过 boot 镜像合成"
elif [[ -n "$REF_BOOT" ]]; then
    color_echo "$green" "合成 boot.img（参考: ${REF_BOOT#$SCRIPT_DIR/}）..."
    python3 "$SCRIPT_DIR/tools/make_bootimg_v3.py" \
        --kernel "$IMAGE_PATH" --ref-boot "$REF_BOOT" \
        -o "$BUILD_DIR/boot.img"
else
    color_echo "$yellow" "提示: 无 stock/boot-ref.img 或 stock/boot.img，跳过 boot.img 合成"
fi

if [[ "${BUILD_BOOT_IMAGES:-true}" != "true" ]]; then
    :
elif [[ -n "$REF_VB" ]]; then
    color_echo "$green" "合成 vendor_boot.img（参考: ${REF_VB#$SCRIPT_DIR/}）..."
    python3 "$SCRIPT_DIR/tools/make_vendor_boot_v3.py" \
        --dtb "$DTB_PATH" --ref-vendor-boot "$REF_VB" \
        -o "$BUILD_DIR/vendor_boot.img"
else
    color_echo "$yellow" "提示: 无 stock/vendor_boot-ref.img 或 stock/vendor_boot.img，跳过 vendor_boot 合成"
fi

# 创建ZIP文件名
ZIP_NAME="${TARGET_DEVICE}_${KERNEL_NAME}-${KERNEL_VERSION}_KernelSU-Next_$(date +%y%m%d)$(date +%H%M).zip"

color_echo "$green" "创建刷机包: $ZIP_NAME"
(cd "$ANY_KERNEL_DIR" && zip -r9 "$ZIP_NAME" ./* -x .git .gitignore out/ ./*.zip)

mv "$ANY_KERNEL_DIR/$ZIP_NAME" "$BUILD_DIR/"

color_echo "$green" "完成! 刷机包已保存到: [$BUILD_DIR/$ZIP_NAME]"

color_echo "$green" "ALL DONE"
