"""Build one offline Windows GUI executable from a clean Python environment."""

from pathlib import Path
import sys

import PyInstaller.__main__
import pikepdf


def main():
    if sys.platform != "win32":
        raise SystemExit("Run this build on Windows x64.")
    args = ["--noconfirm", "--clean", "--onefile", "--windowed", "--noupx",
            "--name", "PDFCompact", "--exclude-module", "pypdfium2",
            "--exclude-module", "pywinauto"]
    # Keep the wheel's original DLL directory and notices inside the EXE.
    native = Path(pikepdf.__file__).resolve().parent.parent / "pikepdf.libs"
    if not any(native.glob("*qpdf*.dll")):
        raise SystemExit("The Windows pikepdf wheel's qpdf DLL is missing.")
    for item in sorted(native.iterdir()):
        if item.is_file():
            option = "--add-binary" if item.suffix.lower() == ".dll" else "--add-data"
            args.extend([option, f"{item}:pikepdf.libs"])
    for name in ("pikepdf", "Pillow", "lxml", "packaging", "pyinstaller"):
        args.extend(["--copy-metadata", name])
    base = Path(sys.base_prefix)
    args.extend(["--add-data", f"{base / 'LICENSE.txt'}:licenses/python"])
    for notice in (base / "tcl").glob("*/license.terms"):
        args.extend(["--add-data", f"{notice}:licenses/{notice.parent.name}"])
    PyInstaller.__main__.run([*args, "app.py"])


if __name__ == "__main__":
    main()
