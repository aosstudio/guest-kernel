"""Package the built kernel and the board's selected module inventory."""

import argparse
from gzip import GzipFile
from hashlib import file_digest
import json
from pathlib import Path
from shutil import copyfile, copytree
import tarfile
from tempfile import TemporaryDirectory

from inputs import LOCK_FILE, ROOT, Lock, read_lock

OUT = ROOT / "out"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def package_name(lock: Lock) -> str:
    return f"aosstudio-guest-kernel-{lock['version']}-{lock['target']}.tar.gz"


def tar_metadata(item: tarfile.TarInfo) -> tarfile.TarInfo:
    item.uid = item.gid = 0
    item.uname = item.gname = "root"
    item.mtime = 0
    return item


def package(build: Path, lock: Lock) -> Path:
    kernel = build / "kernel"
    release = (kernel / "include/config/kernel.release").read_text().strip()
    if not release.startswith(lock["version"] + "-"):
        raise ValueError(f"Unexpected kernel release: {release}")
    module_files: dict[str, Path] = {}
    for root in (kernel, build / "extensions"):
        for module in root.rglob("*.ko"):
            if module.name in module_files:
                raise ValueError(f"Ambiguous module name: {module.name}")
            module_files[module.name] = module

    dist = OUT / "dist"
    dist.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="package.", dir=OUT) as temporary:
        work = Path(temporary)
        tree = work / "tree"
        board = tree / "prebuilts/qemu-kernel" / lock["target"] / lock["board_version"]
        board.mkdir(parents=True)
        copyfile(
            kernel / "arch/x86/boot/bzImage", board / f"kernel-{lock['board_version']}"
        )
        for category, modules in lock["modules"].items():
            stage = board / category
            stage.mkdir()
            for name in modules:
                if name not in module_files:
                    raise ValueError(f"Missing board module: {name}")
                copyfile(module_files[name], stage / name)

        metadata = tree / "metadata"
        metadata.mkdir()
        for name in (".config", "Module.symvers", "modules.builtin"):
            copyfile(kernel / name, metadata / name)
        copyfile(LOCK_FILE, metadata / "inputs.json")
        copytree(ROOT / "patches", metadata / "patches")
        copytree(ROOT / "builders", metadata / "builders")

        licenses = tree / "licenses"
        licenses.mkdir()
        source = build / "source/common"
        for name in (
            "COPYING",
            "LICENSES/preferred/GPL-2.0",
            "LICENSES/exceptions/Linux-syscall-note",
        ):
            copyfile(source / name, licenses / Path(name).name)
        copyfile(
            build / "source/virtio-media/LICENSE", licenses / "virtio-media-LICENSE"
        )
        copyfile(ROOT / "LICENSE", licenses / "AOSStudio-LICENSE")
        record = {
            "kernel_release": release,
            "files": {
                path.relative_to(tree).as_posix(): sha256(path)
                for path in sorted(tree.rglob("*"))
                if path.is_file()
            },
        }
        (tree / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")

        output = dist / package_name(lock)
        pending = work / output.name
        with (
            pending.open("wb") as stream,
            GzipFile(filename="", mode="wb", fileobj=stream, mtime=0) as compressed,
            tarfile.open(fileobj=compressed, mode="w|") as archive,
        ):
            for path in sorted(tree.iterdir()):
                archive.add(path, arcname=path.name, filter=tar_metadata)
        pending.replace(output)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=Path)
    print(package(parser.parse_args().build, read_lock()))
