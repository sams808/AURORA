"""The real minimum macOS of a bundle: the highest LC_BUILD_VERSION / LC_VERSION_MIN_MACOSX of every Mach-O file in it.

Pure Python (no otool, no macholib), so it runs on any CI runner and in the tests:

    python packaging/macos_minos.py dist/AURORA.app                          # print the highest minos and who sets it
    python packaging/macos_minos.py dist/AURORA.app --check packaging/min_macos.txt
    python packaging/macos_minos.py dist/AURORA.app --arch arm64             # every Mach-O must carry this architecture

--check fails (exit 1) when a file needs a newer macOS than the declared minimum (min_macos.txt, which Info.plist's
LSMinimumSystemVersion repeats). Finder refuses such an app with error -10825 on the older systems, so the floor must
be measured, not assumed: PySide6's 6.12 wheels need macOS 26, its 6.11 wheels macOS 15.
"""
from __future__ import annotations

import argparse
import plistlib
import struct
import sys
from pathlib import Path

LC_REQ_DYLD = 0x80000000
LC_VERSION_MIN_MACOSX = 0x24
LC_BUILD_VERSION = 0x32
PLATFORM_MACOS = 1
MH_MAGIC, MH_MAGIC_64 = 0xFEEDFACE, 0xFEEDFACF
FAT_MAGIC, FAT_MAGIC_64 = 0xCAFEBABE, 0xCAFEBABF
CPU_NAMES = {7: "i386", 0x01000007: "x86_64", 12: "arm", 0x0100000C: "arm64", 0x0200000C: "arm64_32"}


def _version(v: int) -> tuple[int, int, int]:
    """Mach-O nibble version xxxx.yy.zz."""
    return (v >> 16) & 0xFFFF, (v >> 8) & 0xFF, v & 0xFF


def _thin(data: bytes, off: int = 0) -> dict | None:
    """One Mach-O image at `off`: {'arch', 'minos', 'sdk'} (minos None when no version load command is present)."""
    if len(data) < off + 28:
        return None
    for endian in ("<", ">"):
        magic = struct.unpack_from(endian + "I", data, off)[0]
        if magic in (MH_MAGIC, MH_MAGIC_64):
            break
    else:
        return None
    is64 = magic == MH_MAGIC_64
    cputype, _sub, _ftype, ncmds, sizeofcmds = struct.unpack_from(endian + "iiiII", data, off + 4)
    pos = off + (32 if is64 else 28)
    end = min(len(data), pos + sizeofcmds)
    minos = sdk = None
    for _ in range(ncmds):
        if pos + 8 > end:
            break
        cmd, size = struct.unpack_from(endian + "II", data, pos)
        if size < 8:
            break
        cmd &= ~LC_REQ_DYLD
        if cmd == LC_BUILD_VERSION and pos + 24 <= end:
            plat, mn, sd = struct.unpack_from(endian + "III", data, pos + 8)
            if plat == PLATFORM_MACOS:
                minos, sdk = _version(mn), _version(sd)
        elif cmd == LC_VERSION_MIN_MACOSX and pos + 16 <= end:
            mn, sd = struct.unpack_from(endian + "II", data, pos + 8)
            minos, sdk = _version(mn), _version(sd)
        pos += size
    return {"arch": CPU_NAMES.get(cputype & 0xFFFFFFFF, hex(cputype & 0xFFFFFFFF)), "minos": minos, "sdk": sdk}


def parse(data: bytes) -> list[dict]:
    """Every image of a thin or universal (fat) Mach-O file: [{'arch', 'minos': (x, y, z) | None, 'sdk'}]. [] for a
    file that is no Mach-O (a Java class shares the fat magic: it has far more 'architectures' than any binary)."""
    if len(data) < 8:
        return []
    magic = struct.unpack_from(">I", data, 0)[0]
    if magic in (FAT_MAGIC, FAT_MAGIC_64):
        n = struct.unpack_from(">I", data, 4)[0]
        if not 0 < n <= 16:
            return []
        out, pos = [], 8
        for _ in range(n):
            if magic == FAT_MAGIC:
                _cpu, _sub, offset, _size, _align = struct.unpack_from(">iiIII", data, pos)
                pos += 20
            else:
                _cpu, _sub, offset, _size, _align, _res = struct.unpack_from(">iiQQII", data, pos)
                pos += 32
            img = _thin(data, offset)
            if img is not None:
                out.append(img)
        return out
    img = _thin(data, 0)
    return [img] if img is not None else []


def _read_head(path: Path, limit: int = 1 << 16) -> bytes:
    with open(path, "rb") as f:
        return f.read(limit)


def parse_file(path: Path) -> list[dict]:
    """parse() on a file, reading only what the headers need (the load commands sit at the start of each slice)."""
    path = Path(path)
    head = _read_head(path, 8)
    if len(head) < 4:
        return []
    m_be = struct.unpack_from(">I", head, 0)[0]
    m_le = struct.unpack_from("<I", head, 0)[0]
    if m_be in (FAT_MAGIC, FAT_MAGIC_64):
        data = path.read_bytes()                  # slices lie anywhere in the file
        return parse(data)
    if MH_MAGIC in (m_be, m_le) or MH_MAGIC_64 in (m_be, m_le):
        return parse(_read_head(path, 1 << 18))
    return []


def scan(app: Path) -> dict:
    """{'highest': (x, y, z) | None, 'by': [files at that minos], 'files': {relative path: [images]}} for every Mach-O
    file under `app` (symbolic links are skipped: they point at files counted already)."""
    app = Path(app)
    files: dict[str, list[dict]] = {}
    for p in sorted(app.rglob("*")):
        try:
            if p.is_symlink() or not p.is_file() or p.stat().st_size < 28:
                continue
            imgs = parse_file(p)
        except OSError:
            continue
        if imgs:
            files[str(p.relative_to(app))] = imgs
    best, by = None, []
    for rel, imgs in files.items():
        for img in imgs:
            mn = img["minos"]
            if mn is None:
                continue
            if best is None or mn > best:
                best, by = mn, [rel]
            elif mn == best and rel not in by:
                by.append(rel)
    return {"highest": best, "by": by, "files": files}


def _fmt(v) -> str:
    return "none" if v is None else (f"{v[0]}.{v[1]}" + (f".{v[2]}" if v[2] else ""))


def _declared(text: str) -> tuple[int, int, int]:
    p = Path(text)
    s = p.read_text(encoding="utf-8").strip() if p.is_file() else text.strip()
    nums = [int(x) for x in s.split(".")[:3]]
    return tuple((nums + [0, 0, 0])[:3])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="macos_minos", description=__doc__.splitlines()[0])
    ap.add_argument("app", type=Path, help="AURORA.app (or any folder / file)")
    ap.add_argument("--check", help="a version ('15.0') or a file holding one (packaging/min_macos.txt)")
    ap.add_argument("--arch", help="require this architecture in every Mach-O file (arm64, x86_64)")
    args = ap.parse_args(argv)
    res = scan(args.app)
    n = len(res["files"])
    print(f"{n} Mach-O files under {args.app}; highest minimum macOS {_fmt(res['highest'])}, set by:")
    for rel in res["by"][:12]:
        print(f"  {rel}")
    if len(res["by"]) > 12:
        print(f"  ... and {len(res['by']) - 12} more")
    rc = 0
    if n == 0:
        print("no Mach-O file found")
        rc = 1
    info = Path(args.app) / "Contents" / "Info.plist"
    if info.is_file():
        with open(info, "rb") as f:
            pl = plistlib.load(f)
        print(f"Info.plist LSMinimumSystemVersion {pl.get('LSMinimumSystemVersion', '(missing)')}")
    if args.check:
        want = _declared(args.check)
        if res["highest"] is not None and res["highest"] > want:
            print(f"FAIL: the bundle needs macOS {_fmt(res['highest'])} but declares {_fmt(want)}")
            rc = 1
        else:
            print(f"ok: nothing needs more than the declared macOS {_fmt(want)}")
    if args.arch:
        missing = [rel for rel, imgs in res["files"].items() if args.arch not in {i["arch"] for i in imgs}]
        if missing:
            print(f"FAIL: {len(missing)} Mach-O files lack {args.arch}: " + ", ".join(missing[:8]))
            rc = 1
        else:
            print(f"ok: every Mach-O file carries {args.arch}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
