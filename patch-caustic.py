#!/usr/bin/env python3
import subprocess
import logging
import sys
import re
import glob
import hashlib

from pathlib import Path
from apk_patch_pipeline import setup_logging, run

log = logging.getLogger("patch-caustic")

def read_manifest():
    manifest = {}

    for line in open("Manifest-apk.txt").readlines():
        line = line.strip()
        if line == "":
            continue

        m = re.match(r'^([a-z0-9A-Z]+):\s+(.*)$', line)
        if not m:
            log.error("Invalid manifest: parse error")
            return
        manifest[m.group(1).lower()] = m.group(2)

    REQUIRED = ["url", "name", "size", "sha256"]

    for r in REQUIRED:
        if r not in manifest:
            log.error("Invalid manifest: missing key %s", r)
            return

    return manifest

def load_patches():
    patches = glob.glob("patches/*.patch")

    # sort in numeric order
    patches = list(map(lambda x: Path(x), patches))
    patches = sorted(patches, key=lambda x: int(x.name.split("-")[0]))

    log.info("Loaded %d patches", len(patches))

    return patches

def apply_patch(patch_path, work_dir):
    patch_path = patch_path.resolve()
    res = subprocess.run(["patch", "--no-backup-if-mismatch", "-p1", "--fuzz=0", "-i", str(patch_path)], cwd=str(work_dir))

    if not res or res.returncode != 0:
        log.error("Failed to patch!")
        return False

    return True

def main():
    setup_logging(enable_colors=sys.stdout.isatty())
    log.info("Caustic Patcher")

    manifest = read_manifest()
    patches = load_patches()

    file_path = Path(manifest["name"])
    new_file_path = Path(manifest["name"].split(".")[0] + "_patched.apk")

    if not file_path.exists():
        log.error("Missing file %s. Please download", file_path)
        return

    log.info("File: %s", file_path)
    st = file_path.stat()
    if st.st_size != int(manifest["size"]):
        log.error("Invalid file size")
        return

    log.info("Size: %d", st.st_size)

    with open(file_path, 'rb') as fp:
        got = hashlib.sha256(fp.read()).hexdigest()

        if got != manifest["sha256"]:
            log.error("Invalid hash")
            return

        log.info("Sha256: %s", got)

    log.info("Ready to begin patching...")

    work_dir = Path("./work")
    new_package_name = "com.apkpatcher.caustic"

    binpath = "./apk_patch_pipeline.py"
    log.info("==> CHECK")
    run([binpath, "check"])

    log.info("==> DECODE")
    run([binpath, "decode", str(file_path), "--out", str(work_dir)])

    log.info("==> RENAME")
    run([binpath, "rename", str(work_dir), "--new-package", new_package_name])

    log.info("==> PATCH")
    for patch in patches:
        log.info("Applying patch %s", patch)
        if not apply_patch(patch, work_dir):
            return

    log.info("==> REBUILD")
    run([binpath, "rebuild", str(work_dir), "--out", str(new_file_path) + ".tmp"])

    log.info("==> FINISH")
    run([binpath, "finish",
         str(new_file_path) + ".tmp",
         "--work-dir", str(work_dir),
         "--final-name", str(new_file_path)
    ])

    return 0
if __name__ == "__main__":
    res = main()
    if res is None:
        res = 1
    sys.exit(res)
