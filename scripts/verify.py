"""Verify the board package and boot a small isolated Goldfish module fixture."""

from gzip import GzipFile
import json
import os
from pathlib import Path
from shutil import copyfile, which
from struct import iter_unpack
import subprocess
import tarfile
from tempfile import TemporaryDirectory

from inputs import LOCK_FILE, ROOT, Lock, read_lock
from package import OUT, package_name, sha256

EARLY_MODULES = (
    "virtio_dma_buf.ko",
    "virtio-rng.ko",
    "virtio_blk.ko",
    "virtio_console.ko",
    "virtio_pci_modern_dev.ko",
    "virtio_pci_legacy_dev.ko",
    "virtio_pci.ko",
    "vmw_vsock_virtio_transport.ko",
)
DEVICE_MODULES = (
    "virtio-media.ko",
    "vkms.ko",
    "goldfish_address_space.ko",
    "goldfish_pipe.ko",
    "goldfish_sync.ko",
    "v4l2loopback.ko",
)


def module_name(name: str) -> str:
    return Path(name).stem.replace("-", "_")


def verify_modules(tree: Path, board: Path, lock: Lock, release: str) -> None:
    image = board / f"kernel-{lock['board_version']}"
    with image.open("rb") as stream:
        stream.seek(0x202)
        assert stream.read(4) == b"HdrS", "Expected an x86 bzImage boot header"
    installed = list(board.glob("*/*.ko"))
    actual = {
        category: sorted(path.name for path in board.joinpath(category).glob("*.ko"))
        for category in lock["modules"]
    }
    assert actual == lock["modules"]
    providers = {module_name(module.name) for module in installed}
    builtin = (tree / "metadata/modules.builtin").read_text().splitlines()
    providers.update(module_name(name) for name in builtin)
    for module in installed:
        with module.open("rb") as stream:
            header = stream.read(20)
        assert header.startswith(b"\x7fELF\x02\x01") and header[18:] == b"\x3e\x00", (
            module
        )
        release_info = subprocess.check_output(
            ["modinfo", "-F", "vermagic", module], text=True
        ).split()
        assert release_info[0] == release, module
        depends = subprocess.check_output(
            ["modinfo", "-F", "depends", module], text=True
        ).strip()
        missing = {module_name(item) for item in depends.split(",") if item} - providers
        if missing:
            raise ValueError(
                f"{module.name} requires unavailable modules: {sorted(missing)}"
            )


def verify_rust_versions(tree: Path, module: Path, work: Path) -> None:
    crcs = work / "rust-version-crcs"
    names = work / "rust-version-names"
    subprocess.run(
        [
            "objcopy",
            "--dump-section",
            f"__version_ext_crcs={crcs}",
            "--dump-section",
            f"__version_ext_names={names}",
            module,
            work / "rust-section-copy.ko",
        ],
        check=True,
    )
    expected = {
        columns[1]: int(columns[0], 16)
        for line in (tree / "metadata/Module.symvers").read_text().splitlines()
        if (columns := line.split())
    }
    selected = names.read_bytes().rstrip(b"\x00").split(b"\x00")
    for name, (crc,) in zip(
        selected, iter_unpack("<I", crcs.read_bytes()), strict=True
    ):
        symbol = name.decode("ascii")
        assert expected.get(symbol) == crc, symbol
    assert selected
    print(f"Rust Binder extended symbol versions verified: {len(selected)}", flush=True)


def initramfs(board: Path, work: Path) -> Path:
    busybox = which(os.environ.get("GUEST_BUSYBOX", "busybox"))
    if busybox is None:
        raise ValueError("Install busybox-static for the guest boot check")
    fixture = work / "root"
    for directory in ("bin", "early-modules", "binderfs", "dev", "proc", "sys"):
        (fixture / directory).mkdir(parents=True)
    copyfile(busybox, fixture / "bin/busybox").chmod(0o755)
    copyfile(ROOT / "tests/init", fixture / "init").chmod(0o755)
    copyfile(board / "gki_modules/rust_binder.ko", fixture / "rust_binder.ko")
    for name in DEVICE_MODULES:
        copyfile(board / "goldfish_modules" / name, fixture / name)
    (fixture / "early-modules/load-order").write_text(
        "".join(name + "\n" for name in EARLY_MODULES)
    )
    modules = {path.name: path for path in board.glob("*/*.ko")}
    for name in EARLY_MODULES:
        copyfile(modules[name], fixture / "early-modules" / name)
    archive = work / "initramfs.cpio.gz"
    members = sorted(
        path.relative_to(fixture).as_posix() for path in fixture.rglob("*")
    )
    with GzipFile(archive, "wb", mtime=0) as stream:
        result = subprocess.run(
            ["cpio", "--quiet", "--null", "-o", "--format=newc", "--owner=0:0"],
            cwd=fixture,
            input=b"".join(os.fsencode(name) + b"\x00" for name in members),
            check=True,
            stdout=subprocess.PIPE,
        )
        stream.write(result.stdout)
    return archive


def boot(board: Path, image: Path, work: Path, release: str) -> None:
    ramdisk = initramfs(board, work)
    disk = work / "virtio.raw"
    with disk.open("wb") as stream:
        stream.write(b"AOSSTUDIO_DEVICE_PROBE")
        stream.truncate(1024 * 1024)
    acceleration = "kvm" if os.access("/dev/kvm", os.R_OK | os.W_OK) else "tcg"
    command = [
        os.environ.get("GUEST_QEMU", "qemu-system-x86_64"),
        "-machine",
        "q35",
        "-accel",
        acceleration,
        "-cpu",
        "host" if acceleration == "kvm" else "max",
        "-m",
        "1024",
        "-smp",
        "2",
        "-display",
        "none",
        "-serial",
        "stdio",
        "-monitor",
        "none",
        "-nic",
        "none",
        "-no-reboot",
        "-kernel",
        str(image),
        "-initrd",
        str(ramdisk),
        "-append",
        "8250.nr_uarts=1 console=ttyS0,115200n8 loglevel=6 rdinit=/init panic=1 binder.impl=rust",
        "-drive",
        f"file={disk},format=raw,if=virtio,readonly=on",
    ]
    log = OUT / "boot.log"
    with log.open("wb") as stream:
        subprocess.run(
            command, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=120
        )
    console = log.read_text(errors="replace")
    for marker in (
        f"BINDERFS_OK {release}",
        "RUST_BINDER_ACTIVE_OK",
        "VIRTIO_BOOT_MODULES_OK",
        "VIRTIO_MEDIA_MODULE_OK",
        "VKMS_DRM_DEVICE_OK",
        "GOLDFISH_DRIVER_REGISTRATION_OK",
        "V4L2_LOOPBACK_DEVICE_OK",
    ):
        if marker not in console:
            raise ValueError(f"Missing boot result {marker}. See {log}")
    print(f"Guest boot and module loading verified with {acceleration}. See {log}")


def main() -> None:
    lock = read_lock()
    with TemporaryDirectory(prefix="verify.", dir=OUT) as temporary:
        work = Path(temporary)
        tree = work / "extract"
        tree.mkdir()
        with tarfile.open(OUT / "dist" / package_name(lock)) as archive:
            archive.extractall(tree, filter="data")
        manifest = json.loads((tree / "manifest.json").read_bytes())
        if (tree / "metadata/inputs.json").read_bytes() != LOCK_FILE.read_bytes():
            raise ValueError("Package has a different source and module lock")
        actual = {
            file.relative_to(tree).as_posix(): sha256(file)
            for file in tree.rglob("*")
            if file.is_file() and file != tree / "manifest.json"
        }
        if actual != manifest["files"]:
            raise ValueError("Package files do not match the manifest")
        board = tree / "prebuilts/qemu-kernel" / lock["target"] / lock["board_version"]
        release = manifest["kernel_release"]
        verify_modules(tree, board, lock, release)
        verify_rust_versions(tree, board / "gki_modules/rust_binder.ko", work)
        boot(board, board / f"kernel-{lock['board_version']}", work, release)


if __name__ == "__main__":
    main()
