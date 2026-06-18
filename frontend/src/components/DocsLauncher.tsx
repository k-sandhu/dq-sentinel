import { useRef, useState } from "react";
import { Link } from "react-router";
import { Icon } from "./ui";

/** Floating launcher pinned to the bottom-right of the main app. Hovering or
 *  focusing it reveals overlay links to the two standalone reference pages (CSS
 *  :hover / :focus-within); clicking toggles the same menu so it also works by
 *  tap. Rendered inside Layout, so it rides along on every in-app page but not
 *  on the standalone /docs and /features pages (which have their own nav). */
export default function DocsLauncher() {
  const [open, setOpen] = useState(false);
  const fabRef = useRef<HTMLButtonElement>(null);

  return (
    <div
      className={`doc-launcher${open ? " open" : ""}`}
      onMouseLeave={() => setOpen(false)}
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          setOpen(false);
          fabRef.current?.focus();
        }
      }}
    >
      <div className="doc-launcher-menu" role="menu" aria-label="Documentation and features">
        <Link to="/docs" className="doc-launcher-item" role="menuitem" onClick={() => setOpen(false)}>
          <Icon name="book" size={16} />
          <span>
            <strong>Documentation</strong>
            <em>Guides &amp; reference from the docs folder</em>
          </span>
        </Link>
        <Link to="/features" className="doc-launcher-item" role="menuitem" onClick={() => setOpen(false)}>
          <Icon name="sparkles" size={16} />
          <span>
            <strong>Features</strong>
            <em>Everything built into the app so far</em>
          </span>
        </Link>
      </div>
      <button
        ref={fabRef}
        type="button"
        className="doc-launcher-fab"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Open documentation and features"
        title="Docs & features"
        onClick={() => setOpen((o) => !o)}
      >
        <Icon name="help" size={22} />
      </button>
    </div>
  );
}
