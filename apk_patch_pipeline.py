#!/usr/bin/env python3
import argparse
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("apk_patch_pipeline")

COLOR_RED_INTENSE = "\033[1;31m"
COLOR_RED = "\033[31m"
COLOR_WHITE_INTENSE = "\033[1;37m"
COLOR_WHITE = "\033[37m"
COLOR_YELLOW_INTENSE = "\033[1;33m"
COLOR_YELLOW = "\033[33m"
COLOR_DEFAULT = "\033[0m"

COLOR_MAP = {
    logging.INFO: COLOR_WHITE_INTENSE,
    logging.ERROR: COLOR_RED_INTENSE,
    logging.WARNING: COLOR_YELLOW_INTENSE,
    logging.CRITICAL: COLOR_RED_INTENSE,
}

LEVEL_NAME = {
    logging.INFO: "INFO",
    logging.ERROR: "ERROR",
    logging.WARNING: "WARN",
    logging.CRITICAL: "CRIT",
}


def setup_logging(debug=False, enable_colors=False, show_package=False):
    if debug:
        level = logging.DEBUG
    else:
        level = logging.INFO

    if show_package:
        fmt = "[%(levelname)s] %(name)s: %(message)s"
    else:
        fmt = "[%(levelname)s] %(message)s"

    root = logging.getLogger()
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter(fmt)
    handler.setFormatter(formatter)

    root.addHandler(handler)

    for k, v in LEVEL_NAME.items():
        if enable_colors:
            logging.addLevelName(k, COLOR_MAP[k] + v + COLOR_DEFAULT)
        else:
            logging.addLevelName(k, v)

# ---------------------------------------------------------------------------
# Tool detection
# ---------------------------------------------------------------------------

REQUIRED_TOOLS = {
    "apktool": "Decodes/rebuilds APK resources + smali. Install: https://apktool.org",
    "zipalign": "Aligns zip entries for mmap-friendly native libs. Ships with Android SDK build-tools.",
    "apksigner": "Signs APKs (v2/v3 scheme). Ships with Android SDK build-tools.",
    "keytool": "Generates signing keystores. Ships with the JDK.",
}

def which(tool: str) -> str | None:
    return shutil.which(tool)


def check_tools() -> bool:
    log.info("== Checking required tools ==")
    all_ok = True
    for tool, desc in REQUIRED_TOOLS.items():
        path = which(tool)
        if path:
            log.info(f"[OK] {tool:<12} FOUND ({path})")
        else:
            log.error(f"[XX] {tool:<12} MISSING -- {desc}")
            all_ok = False

    if all_ok:
        log.info("All required tools present.")
    else:
        log.error("Missing required tools -- install them before continuing.")
    return all_ok


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    log.info(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        log.error(f"Command failed with exit code {result.returncode}")
        sys.exit(result.returncode)
    return result


def require_tools(*tools: str):
    missing = [t for t in tools if not which(t)]
    if missing:
        log.error(f"Missing required tool(s): {', '.join(missing)}. Run 'check' for details.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Decode (smali is disassembly, not decompiled source -- apktool "decode")
# ---------------------------------------------------------------------------

def decode(apk_path: str, out_dir: str):
    require_tools("apktool")
    out = Path(out_dir)
    if out.exists():
        log.error(f"Output directory {out_dir} already exists. Remove it or choose another --out.")
        sys.exit(1)
    run(["apktool", "d", apk_path, "-o", out_dir])
    log.info(f"Decoded to {out_dir}/")
    log.info("Smali sources:  find under smali*/ directories (disassembly, not decompiled source)")
    log.info("Native libs:    lib/<abi>/*.so")
    log.info("Manifest:       AndroidManifest.xml")
    log.info(">>> Manually patch smali and/or native libraries now, then run 'rename' and 'rebuild'. <<<")


# ---------------------------------------------------------------------------
# Rename package (for coexistence with the original app)
# ---------------------------------------------------------------------------

def rename_package(work_dir: str, new_package: str):
    manifest_path = Path(work_dir) / "AndroidManifest.xml"
    if not manifest_path.exists():
        log.error(f"{manifest_path} not found.")
        sys.exit(1)

    text = manifest_path.read_text(encoding="utf-8")

    m = re.search(r'package="([^"]+)"', text)
    if not m:
        log.error("Could not find package attribute in manifest.")
        sys.exit(1)
    old_package = m.group(1)

    text = text.replace(f'package="{old_package}"', f'package="{new_package}"', 1)

    # Rewrite provider authorities that reference the old package to avoid
    # ContentProvider authority collisions with the original app.
    def fix_authority(match: re.Match) -> str:
        authorities = match.group(1)
        new_auths = ",".join(
            a.replace(old_package, new_package) if old_package in a else a
            for a in authorities.split(",")
        )
        return f'android:authorities="{new_auths}"'

    text, n = re.subn(r'android:authorities="([^"]+)"', fix_authority, text)

    manifest_path.write_text(text, encoding="utf-8")

    log.info(f"Renamed package: {old_package} -> {new_package}")
    log.info(f"Rewrote {n} provider authorities referencing the old package.")
    log.warning("If the app has deep links / intent-filter hosts tied to the old package, "
                "or hardcoded package-name checks in code (smali string constants, "
                "signature/package verification), you'll need to grep for those separately:")
    log.warning(f'  grep -rl "{old_package}" {work_dir}/smali*')


# ---------------------------------------------------------------------------
# Rebuild
# ---------------------------------------------------------------------------

def rebuild(work_dir: str, out_apk: str):
    require_tools("apktool")
    run(["apktool", "b", work_dir, "-o", out_apk])
    log.info(f"Rebuilt unsigned APK: {out_apk}")


# ---------------------------------------------------------------------------
# Zipalign
# ---------------------------------------------------------------------------

def zipalign(in_apk: str, out_apk: str):
    require_tools("zipalign")
    run(["zipalign", "-v", "-p", "4", in_apk, out_apk])
    log.info(f"Aligned APK: {out_apk}")


# ---------------------------------------------------------------------------
# Keystore + Sign
# ---------------------------------------------------------------------------

def ensure_keystore(keystore: str, alias: str, storepass: str, keypass: str):
    if Path(keystore).exists():
        log.info(f"Keystore {keystore} already exists, reusing it.")
        return
    require_tools("keytool")
    log.info(f"Generating new keystore: {keystore}")
    run([
        "keytool", "-genkey", "-v",
        "-keystore", keystore,
        "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
        "-alias", alias,
        "-storepass", storepass,
        "-keypass", keypass,
        "-dname", "CN=Patched App, OU=Personal, O=Personal, L=Unknown, ST=Unknown, C=US",
    ])


def sign(in_apk: str, out_apk: str, keystore: str, alias: str, storepass: str, keypass: str):
    require_tools("apksigner")
    run([
        "apksigner", "sign",
        "--ks", keystore,
        "--ks-key-alias", alias,
        "--ks-pass", f"pass:{storepass}",
        "--key-pass", f"pass:{keypass}",
        "--out", out_apk,
        in_apk,
    ])
    log.info(f"Signed APK: {out_apk}")


def verify(apk_path: str):
    require_tools("apksigner", "zipalign")
    log.info("== apksigner verify ==")
    run(["apksigner", "verify", "-v", apk_path])
    log.info("== zipalign -c (alignment check) ==")
    run(["zipalign", "-c", "-v", "4", apk_path])
    log.info(f"{apk_path} is aligned and signed correctly.")


# ---------------------------------------------------------------------------
# Convenience: run several phases together
# ---------------------------------------------------------------------------

def finish(work_dir: str, unsigned_apk: str, keystore: str, alias: str, storepass: str, keypass: str,
           final_name: str | None):
    work_dir = Path(work_dir)
    base = Path(unsigned_apk).stem

    aligned = str(work_dir / "{base}_aligned.apk")
    signed = final_name or f"{base}_signed.apk"

    zipalign(unsigned_apk, aligned)
    ensure_keystore(keystore, alias, storepass, keypass)
    sign(aligned, signed, keystore, alias, storepass, keypass)
    verify(signed)
    log.info(f"Done. Install with: adb install {signed}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="APK patch/rebuild/sign pipeline.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="Detect required and optional tools.")

    p = sub.add_parser("decode", help="apktool decode to smali (disassembly) + resources.")
    p.add_argument("apk")
    p.add_argument("--out", required=True)

    p = sub.add_parser("rename", help="Change applicationId + provider authorities for coexistence.")
    p.add_argument("work_dir")
    p.add_argument("--new-package", required=True)

    p = sub.add_parser("rebuild", help="apktool rebuild to an unsigned APK.")
    p.add_argument("work_dir")
    p.add_argument("--out", required=True)

    p = sub.add_parser("zipalign", help="Align an unsigned APK.")
    p.add_argument("apk_in")
    p.add_argument("apk_out")

    p = sub.add_parser("sign", help="Sign an aligned APK (generates keystore if missing).")
    p.add_argument("apk_in")
    p.add_argument("apk_out")
    p.add_argument("--keystore", default="patchkey.jks")
    p.add_argument("--alias", default="patchkey")
    p.add_argument("--storepass", default="android123")
    p.add_argument("--keypass", default="android123")

    p = sub.add_parser("verify", help="Verify signature + alignment of a final APK.")
    p.add_argument("apk")

    p = sub.add_parser("finish", help="zipalign + sign + verify an unsigned APK in one step.")
    p.add_argument("unsigned_apk")
    p.add_argument("--work-dir", required=True)
    p.add_argument("--keystore", default="patchkey.jks")
    p.add_argument("--alias", default="patchkey")
    p.add_argument("--storepass", default="android123")
    p.add_argument("--keypass", default="android123")
    p.add_argument("--final-name", default=None)

    args = parser.parse_args()

    setup_logging(enable_colors=sys.stdout.isatty())

    if args.command == "check":
        ok = check_tools()
        sys.exit(0 if ok else 1)
    elif args.command == "decode":
        decode(args.apk, args.out)
    elif args.command == "rename":
        rename_package(args.work_dir, args.new_package)
    elif args.command == "rebuild":
        rebuild(args.work_dir, args.out)
    elif args.command == "zipalign":
        zipalign(args.apk_in, args.apk_out)
    elif args.command == "sign":
        ensure_keystore(args.keystore, args.alias, args.storepass, args.keypass)
        sign(args.apk_in, args.apk_out, args.keystore, args.alias, args.storepass, args.keypass)
    elif args.command == "verify":
        verify(args.apk)
    elif args.command == "finish":
        finish(args.work_dir, args.unsigned_apk, args.keystore, args.alias, args.storepass, args.keypass,
               args.final_name)

if __name__ == "__main__":
    main()
