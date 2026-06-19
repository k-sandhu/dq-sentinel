#!/usr/bin/env python3
"""Generate the self-contained, single-file builds in ./standalone/.

These are byte-for-byte what gets handed out for offline viewing (no `assets/`
folder, no unzip). The multi-file sources in this directory are canonical; the
standalone builds are produced from them so the two always match.

    python3 build-standalone.py

- prototype.html / components.html: app.css + app.js inlined. Fully offline,
  no network needed to build OR view.
- incidents-react.html: the JSX is pre-compiled (esbuild) and React itself is
  inlined (react/react-dom UMD), so it runs offline. Building this one needs
  network (npx esbuild + npm pack react); viewing it does not.
"""
import re, subprocess, sys, tempfile, pathlib, glob, os

HERE = pathlib.Path(__file__).parent
OUT = HERE / "standalone"; OUT.mkdir(exist_ok=True)
css = (HERE / "assets/app.css").read_text()
js  = (HERE / "assets/app.js").read_text()

def inline_assets(html: str) -> str:
    html = html.replace('<link rel="stylesheet" href="assets/app.css">', f"<style>\n{css}\n</style>")
    return re.sub(r'<script src="assets/app.js"></script>',
                  "<script>\n" + js.replace("\\", "\\\\") + "\n</script>", html)

# --- offline, no network ---
for src, dst in [("app.html", "prototype.html"), ("components.html", "components.html")]:
    (OUT / dst).write_text(inline_assets((HERE / src).read_text()))
    print("built", dst)

# --- offline React (build needs network) ---
def build_react():
    src = (HERE / "react-incidents.html").read_text()
    jsx = re.search(r'data-presets="react">(.*?)</script>', src, re.S).group(1)
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        (td / "inc.jsx").write_text(jsx)
        subprocess.run(["npx", "--yes", "esbuild@0.21.5", str(td / "inc.jsx"),
                        f"--outfile={td/'inc.js'}"], check=True, cwd=td,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        compiled = (td / "inc.js").read_text()
        subprocess.run(["npm", "pack", "react@18.3.1", "react-dom@18.3.1"], check=True, cwd=td,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for tgz in td.glob("*.tgz"):
            subprocess.run(["tar", "xf", str(tgz)], check=True, cwd=td)
        react = (td / "package/umd/react.production.min.js").read_text()
        rdom  = (td / "package/umd/react-dom.production.min.js").read_text()
    h = src.replace('<link rel="stylesheet" href="assets/app.css">', f"<style>\n{css}\n</style>")
    h = h.replace('<script src="https://unpkg.com/react@18/umd/react.production.min.js" crossorigin></script>',
                  f"<script>\n{react}\n</script>\n<script>\n{rdom}\n</script>")
    h = h.replace('<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js" crossorigin></script>', "")
    h = h.replace('<script src="https://unpkg.com/@babel/standalone/babel.min.js" crossorigin></script>', "")
    h = re.sub(r'<script type="text/babel" data-presets="react">.*?</script>',
               "<script>\n" + compiled.replace("\\", "\\\\") + "\n</script>", h, flags=re.S)
    h = h.replace('<script src="assets/app.js"></script>', f"<script>\n{js}\n</script>")
    h = h.replace("Needs internet at view-time for the CDN.",
                  "Fully self-contained / offline (React + app pre-compiled and inlined).")
    (OUT / "incidents-react.html").write_text(h)
    print("built incidents-react.html")

try:
    build_react()
except Exception as e:  # noqa: BLE001
    print(f"skipped incidents-react.html (needs network: npx esbuild + npm pack): {e}", file=sys.stderr)
