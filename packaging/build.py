"""Offline Windows release builder. Requires full Python, Git, .NET Framework csc."""
import argparse
from pathlib import Path
import shutil
import subprocess
import zipfile

args = argparse.ArgumentParser()
args.add_argument("--python-root", required=True)
args.add_argument("--git-root", required=True)
args.add_argument("--out", required=True)
args.add_argument("--work", required=True)
a = args.parse_args()
source = Path(__file__).resolve().parents[1]
out = Path(a.out).resolve(); out.mkdir(parents=True, exist_ok=True)
work = Path(a.work).resolve(); work.mkdir(parents=True, exist_ok=True)
stage = work / "portable"
stage.mkdir(exist_ok=True)
for name in ("lu.py", "README.md"):
    shutil.copy2(source / name, stage / name)
shutil.copytree(source / "luple", stage / "luple", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
pyroot = Path(a.python_root)
runtime = stage / "runtime/python"; runtime.mkdir(parents=True, exist_ok=True)
for path in pyroot.iterdir():
    if path.is_file() and (path.suffix in (".dll", ".exe") or path.name == "LICENSE.txt"):
        shutil.copy2(path, runtime / path.name)
shutil.copytree(pyroot / "DLLs", runtime / "DLLs", dirs_exist_ok=True)
with zipfile.ZipFile(runtime / "python312.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for path in (pyroot / "Lib").rglob("*"):
        rel = path.relative_to(pyroot / "Lib")
        if path.is_file() and not any(p in ("site-packages", "__pycache__", "test", "tests", "idlelib", "tkinter", "turtledemo", "ensurepip") for p in rel.parts):
            z.write(path, str(rel))
(runtime / "python312._pth").write_text("python312.zip\nDLLs\n..\\..\n", encoding="utf-8")
shutil.copytree(a.git_root, stage / "runtime/git", dirs_exist_ok=True)
csc = Path("C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe")
subprocess.run([str(csc), "/nologo", "/target:exe", "/platform:x64", "/win32manifest:" + str(source / "packaging/app.manifest"), "/reference:System.Windows.Forms.dll", "/out:" + str(stage / "lu.exe"), str(source / "packaging/Launcher.cs")], check=True)
(stage / "THIRD-PARTY.txt").write_text("Bundled, unmodified Python runtime: runtime/python/LICENSE.txt\nBundled Git for Windows: runtime/git/LICENSE.txt and runtime/git/mingw64/share/licenses\nGit for Windows source releases: https://github.com/git-for-windows/git/releases\nPython source releases: https://www.python.org/downloads/source/\n", encoding="utf-8")
portable = out / "LupleGit-0.5.1-Windows-x64.zip"
with zipfile.ZipFile(portable, "w", zipfile.ZIP_DEFLATED) as z:
    for path in stage.rglob("*"):
        if path.is_file(): z.write(path, path.relative_to(stage).as_posix())
subprocess.run([str(csc), "/nologo", "/target:exe", "/platform:x64", "/win32manifest:" + str(source / "packaging/app.manifest"), "/reference:System.Core.dll", "/reference:System.IO.Compression.dll", "/reference:System.IO.Compression.FileSystem.dll", "/resource:" + str(portable) + ",luple.zip", "/out:" + str(out / "LupleGit-Setup-0.5.1.exe"), str(source / "packaging/Setup.cs")], check=True)
print(portable)
print(out / "LupleGit-Setup-0.5.0.exe")

