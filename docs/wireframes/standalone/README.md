# Standalone builds

Single-file, self-contained versions of the wireframes — **no `assets/` folder, no
unzip, nothing to serve.** Just open the file in a browser. These are exactly the
files handed out for offline review, generated from the canonical multi-file sources
one directory up.

| File | What it is | Offline? |
|---|---|---|
| `prototype.html` | The full 17-screen prototype + Appearance & preferences panel (CSS + JS inlined) | ✅ fully offline |
| `components.html` | The component reference sheet (CSS + JS inlined) | ✅ fully offline |
| `incidents-react.html` | The Incidents flow in real React — JSX pre-compiled, React itself inlined | ✅ fully offline |

Regenerate after editing the sources:

```bash
cd docs/wireframes && python3 build-standalone.py
```

`prototype.html` / `components.html` rebuild with no network. `incidents-react.html`
needs network to **build** (it runs `npx esbuild` + `npm pack react`), but not to **view**.
