"""Fetch the pinned kernel source trees and materialize a clean build input."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import subprocess
from typing import TypedDict, cast

ROOT = Path(__file__).resolve().parents[1]
LOCK_FILE = ROOT / "locks/inputs.json"
CACHE = ROOT / "out/inputs"
OBJECT_ID = re.compile(r"[a-f0-9]{40}")
GIT_ENV = os.environ | {
    "GIT_CONFIG_COUNT": "0",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
}


class Source(TypedDict):
    url: str
    commit: str
    tree: str
    license: str


class Lock(TypedDict):
    version: str
    target: str
    board_version: str
    sources: dict[str, Source]
    modules: dict[str, list[str]]


def read_lock() -> Lock:
    return cast(Lock, json.loads(LOCK_FILE.read_bytes()))


def git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "-C",
            path,
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.https.allow=always",
            "-c",
            "http.followRedirects=false",
            *args,
        ],
        env=GIT_ENV,
        text=True,
        stdout=subprocess.PIPE,
        check=check,
    )


def fetch_source(name: str, source: Source) -> None:
    if Path(name).name != name or name in (".", ".."):
        raise ValueError(f"Invalid source name: {name}")
    commit, tree, url = source["commit"], source["tree"], source["url"]
    if not url.startswith("https://") or not all(
        OBJECT_ID.fullmatch(value) for value in (commit, tree)
    ):
        raise ValueError(f"Expected HTTPS and full Git object IDs for {name}")
    cached = CACHE / name
    if not cached.is_dir():
        cached.mkdir(parents=True)
        git(cached, "init", "--bare", "--quiet")
    rewrite = git(
        cached,
        "config",
        "--get-regexp",
        r"^url\..*\.(insteadof|pushinsteadof)$",
        check=False,
    )
    if rewrite.returncode != 1:
        raise ValueError(f"Unexpected Git URL rewrite settings in {cached}")
    probe = git(
        cached, "rev-parse", "--verify", "--quiet", f"{commit}^{{tree}}", check=False
    )
    if probe.returncode:
        print(f"Fetching {name} at {commit}", flush=True)
        git(
            cached,
            "-c",
            "fetch.fsckObjects=true",
            "fetch",
            "--quiet",
            "--no-tags",
            "--no-write-fetch-head",
            "--depth=1",
            url,
            commit,
        )
        probe = git(cached, "rev-parse", "--verify", f"{commit}^{{tree}}")
    if probe.stdout.strip() != tree:
        raise ValueError(f"Git tree mismatch for {name} at {commit}")


def materialize(source_dir: Path, name: str, source: Source) -> None:
    target = source_dir / name
    target.mkdir()
    with subprocess.Popen(
        ["git", "-C", CACHE / name, "archive", "--format=tar", source["commit"]],
        env=GIT_ENV,
        stdout=subprocess.PIPE,
    ) as archive:
        assert archive.stdout is not None
        subprocess.run(
            ["tar", "--extract", "--directory", target, "--no-same-owner"],
            stdin=archive.stdout,
            check=True,
        )
        if archive.wait():
            raise ValueError(f"Could not unpack {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path)
    args = parser.parse_args()
    sources = read_lock()["sources"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for job in [
            pool.submit(fetch_source, name, spec) for name, spec in sources.items()
        ]:
            job.result()
    if args.stage:
        args.stage.mkdir(parents=True)
        for name, spec in sources.items():
            materialize(args.stage, name, spec)


if __name__ == "__main__":
    main()
