#!/usr/bin/env python3
"""Build Madeira's Arial stand-ins from Liberation Sans.

Unity's OS font lookup (DynamicFontMap) scans C:\\windows\\Fonts and keys each face by
its FreeType family name, matched exactly; it ignores file names and the registry. A
Unity font that asks for "Arial" therefore only finds a face whose family name is
"Arial". Liberation Sans is metric-compatible with Arial, so this renames its family.

The OFL forbids a modified version from using the Reserved Font Name "Liberation", so
every name record that carries it is rewritten; the copyright, trademark and license
records are kept.

Proton does the same for the same reason (Makefile.in, arial_NAMES/arial_ORIG).

Input: the unmodified Liberation Fonts 2.1.5 release,
  https://github.com/liberationfonts/liberation-fonts/files/7261482/liberation-fonts-ttf-2.1.5.tar.gz
  SHA-256 7191c669bf38899f73a2094ed00f7b800553364f90e2637010a69c0e268f25d0

Usage: make-arial.py <dir with LiberationSans-*.ttf> <output dir>
Needs fontTools (pip install fonttools).
"""
import sys
from pathlib import Path

from fontTools.ttLib import TTFont

STYLES = {
    "Regular": "arial.ttf",
    "Bold": "arialbd.ttf",
    "Italic": "ariali.ttf",
    "BoldItalic": "arialbi.ttf",
}
# Family, subfamily-qualified unique ID, full name, PostScript name, and the typographic
# family/subfamily records if a font carries them.
RENAMED_IDS = {1, 3, 4, 6, 16, 17}


def rename(src: Path, dst: Path) -> None:
    font = TTFont(src)
    name = font["name"]
    for rec in name.names:
        if rec.nameID not in RENAMED_IDS:
            continue
        text = rec.toUnicode()
        new = text.replace("Liberation Sans", "Arial").replace("LiberationSans", "Arial")
        if new != text:
            rec.string = new
    # 21/22 (WWS family) would give FreeType another family name to report.
    name.names = [r for r in name.names if r.nameID not in (21, 22)]
    leftover = [r.nameID for r in name.names if r.nameID in RENAMED_IDS and "Liberation" in r.toUnicode()]
    if leftover:
        sys.exit(f"{src.name}: name IDs {leftover} still say Liberation")
    font.save(dst)


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    for style, out in STYLES.items():
        rename(src_dir / f"LiberationSans-{style}.ttf", out_dir / out)
        print(f"LiberationSans-{style}.ttf -> {out}")


if __name__ == "__main__":
    main()
