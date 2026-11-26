#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
build=$(mktemp -d "$root/out/build.XXXXXXXX")
printf 'Work directory: %s\n' "$build"
python=${PYTHON:-python3}
jobs=${BUILD_JOBS:-8}
mkdir -p "$build/tmp" "$build/kernel"
export TMPDIR="$build/tmp" LC_ALL=C TZ=UTC
export KBUILD_BUILD_USER=aosstudio KBUILD_BUILD_HOST=builder
export KBUILD_BUILD_TIMESTAMP='2026-11-21 23:59:59 UTC'
export KBUILD_BUILD_VERSION=1

"$python" scripts/inputs.py --stage "$build/source"
source="$build/source/common"
virtual="$build/source/virtual-device"
out="$build/kernel"
patch --batch --forward --fuzz=0 -p1 -d "$source" \
    < patches/gendwarfksyms-skip-files-with-no-exports.patch

kmake=(make --no-print-directory -C "$source" O="$out" ARCH=x86_64 LLVM=-21 LOCALVERSION=-aosstudio)
"${kmake[@]}" rustavailable
"${kmake[@]}" gki_defconfig
"$source/scripts/kconfig/merge_config.sh" -m -O "$out" "$out/.config" \
    "$virtual/virtual_device_core.fragment" "$virtual/virtual_device.fragment"
"${kmake[@]}" olddefconfig
for option in RUST=y DEBUG_INFO_BTF=y DEBUG_INFO_BTF_MODULES=y ANDROID_BINDER_IPC_RUST=m MODVERSIONS=y; do
    grep -Fx "CONFIG_$option" "$out/.config"
done
grep -Fx '# CONFIG_TLS is not set' "$out/.config"

"${kmake[@]}" -j "$jobs" bzImage modules

extensions="$build/extensions"
mkdir -p "$extensions/goldfish"
cp -a "$virtual/vkms" "$extensions/"
cp -a "$build/source/virtio-media/driver" "$extensions/virtio-media"
cp -a "$virtual/uapi" "$extensions/goldfish/"
cp "$virtual"/goldfish_drivers/{defconfig_test.h,goldfish_address_space.c,goldfish_pipe.c,goldfish_sync.c} \
    "$virtual"/v4l2loopback/{v4l2loopback.c,v4l2loopback.h,v4l2loopback_formats.h} "$extensions/goldfish/"
cp "$root/builders/virtio-media.Kbuild" "$extensions/virtio-media/Kbuild"
cp "$root/builders/goldfish.Kbuild" "$extensions/goldfish/Kbuild"
"${kmake[@]}" -j "$jobs" M="$extensions/virtio-media" modules
"${kmake[@]}" -j "$jobs" M="$extensions/vkms" CONFIG_DRM_VKMS=m modules
"${kmake[@]}" -j "$jobs" M="$extensions/goldfish" modules

"$python" scripts/package.py "$build"
