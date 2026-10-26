import { useId, useRef, useState } from "react";
import { Link } from "react-router";
import { Icon } from "./ui";

/** Header disclosure for the standalone reference pages. Hidden links unmount
 *  so keyboard focus cannot enter an invisible popup. */
export default function DocsLauncher() {
  const [open, setOpen] = useState(false);
  const fabRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  const closeForNavigation = () => {
    // A cancelled dirty-editor navigation must restore focus to a mounted node.
    fabRef.current?.focus();
    setOpen(false);
  };

  return (
    <div
      className={`doc-launcher${open ? " open" : ""}`}
      onMouseLeave={(event) => {
        // Pointer movement must not unmount a keyboard user's focused link.
        if (!event.currentTarget.contains(document.activeElement)) setOpen(false);
      }}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          setOpen(false);
          fabRef.current?.focus();
        }
      }}
    >
      <button
        ref={fabRef}
        type="button"
        className="doc-launcher-fab"
        aria-controls={menuId}
        aria-expanded={open}
        aria-label="Open documentation and features"
        title="Docs & features"
        onClick={() => setOpen((o) => !o)}
      >
        <Icon name="help" size={22} />
      </button>
      {open && <nav id={menuId} className="doc-launcher-menu" aria-label="Documentation and features">
        <Link to="/docs" className="doc-launcher-item" onClick={closeForNavigation}>
          <Icon name="book" size={16} />
          <span>
            <strong>Documentation</strong>
            <em>Guides &amp; reference from the docs folder</em>
          </span>
        </Link>
        <Link to="/features" className="doc-launcher-item" onClick={closeForNavigation}>
          <Icon name="sparkles" size={16} />
          <span>
            <strong>Features</strong>
            <em>Everything built into the app so far</em>
          </span>
        </Link>
      </nav>}
    </div>
  );
}
