# Build the Goldfish guest kernel

Build on Linux x86-64 with Python 3.14 and the Ubuntu 26.04 kernel toolchain. Install the host dependencies.

```sh
sudo apt-get update
sudo apt-get install --yes --no-install-recommends \
  bc bindgen binutils bison build-essential busybox-static clang-21 cpio \
  flex git kmod libclang1-21 libdw-dev libelf-dev libssl-dev lld-21 llvm-21 pahole \
  qemu-system-x86 rsync rust-src rustc zstd
```

Run the build and verification from the repository root.

```sh
make fetch
make build BUILD_JOBS=8
make verify
```

The source cache and build work directories go under disk-backed `out/`. The source lock pins commit and tree IDs. The build applies the recorded upstream fix, merges the Goldfish configuration fragments into `gki_defconfig`, and builds all selected modules against the resulting kernel and symbol table.

The archive is `out/dist/aosstudio-guest-kernel-6.12.96-x86_64.tar.gz`. Extract its `prebuilts/qemu-kernel/x86_64/6.12/` subtree into the system-image checkout at the matching path. It contains the kernel image and the GKI and Goldfish module groups. The package also contains the kernel configuration, symbol versions, original upstream URLs, commit IDs, build patch, and licenses. Recreate the corresponding sources from the locked trees and the included patch through the build commands above.

Verification checks that the packaged modules match the selected inventory and kernel release, satisfy module dependencies, and agree with the kernel's extended Rust Binder symbol versions. The QEMU fixture checks a read-only Virtio disk, Rust Binder, BinderFS, Virtio Media, VKMS, the Goldfish driver registrations, and V4L2 loopback. The boot uses KVM when `/dev/kvm` is accessible and QEMU's software accelerator otherwise. The console is written to `out/boot.log`.
