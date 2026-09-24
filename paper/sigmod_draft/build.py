"""Build the paper with the project-local Tectonic binary; no sudo required."""
from pathlib import Path
import os
import subprocess
import sys

paper = Path(__file__).resolve().parent
project = paper.parent.parent
compiler = project / "work/latex/bin/tectonic"
if not compiler.is_file():
    sys.exit(f"Missing local compiler: {compiler}")

subprocess.run([sys.executable, str(paper / "render_latex.py")], check=True)
env = os.environ.copy()
env["XDG_CACHE_HOME"] = str(project / "work/latex/cache")
subprocess.run(
    [str(compiler), "--untrusted", "--synctex", "--keep-logs", str(paper / "main.tex")],
    cwd=paper, env=env, check=True,
)
print(f"PDF: {paper / 'main.pdf'}")
