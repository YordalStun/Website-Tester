"""Build the Windows program with PyInstaller.

    python build.py            -> dist/WebsiteTester.exe  (single file)
    python build.py --folder   -> dist/WebsiteTester/     (folder version, starts faster)
"""
import os
import sys

import PyInstaller.__main__

HERE = os.path.dirname(os.path.abspath(__file__))
EXCLUDE = ("tkinter", "unittest", "pydoc", "test", "PySide6.QtWebEngineCore", "PySide6.QtQml", "PySide6.QtQuick",
           "PySide6.Qt3DCore", "PySide6.QtMultimedia", "numpy", "PIL")


def main() -> None:
    folder = "--folder" in sys.argv
    args = [
        os.path.join(HERE, "run.py"),
        "--name", "WebsiteTester",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--icon", os.path.join(HERE, "resources", "icon.ico"),
        "--add-data", f"{os.path.join(HERE, 'resources')}{os.pathsep}resources",
        "--collect-submodules", "dns",  # dnspython loads its record types on demand
    ]
    for mod in EXCLUDE:
        args += ["--exclude-module", mod]
    args.append("--onedir" if folder else "--onefile")
    if os.path.exists(os.path.join(HERE, "version_info.txt")):
        args += ["--version-file", os.path.join(HERE, "version_info.txt")]
    PyInstaller.__main__.run(args)


if __name__ == "__main__":
    main()
