# Caustic 3 DAW Patching

Maintains a APK patchset for the [Caustic 3 DAW](https://web.archive.org/web/20231227040110/https://singlecellsoftware.com/caustic)

## Motivation

The original author (Rej, aka SingleCellSoftware) [isn't actively maintaining Caustic](https://singlecellsoftware.com/caustic3.html). There is a showstopper bug in the APK that will not let it restart after first install due to

```
java.lang.SecurityException: com.apkpatcher.caustic: One of RECEIVER_EXPORTED or RECEIVER_NOT_EXPORTED should be specified when a receiver isn't being registered exclusively for system broadcasts
```

This has been patched in [3-midi-broadcast-receiver-crash.patch](./patches/3-midi-broadcast-receiver-crash.patch).
Future patches can be made if needed using the patching pipeline in this repository.

## Usage

**Requirements:**

* Downloaded APK (see `Manifest-apk.txt` for URL)
* Linux machine with Python 3
* Android build tools in the `PATH` and `keytool`. Run `./apk_patch_pipeline.py check` to verify you have all the needed tools
* ADB enabled on a Android device

Run `./patch-caustic.py` then `adb install Caustic_3_patched.apk`.
Don't erase `patchkey.jks` once created, otherwise future `adb install` will fail with a mismatched key (you will need to uninstall the patched apk).
