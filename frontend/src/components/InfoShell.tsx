import type { ReactNode } from "react";
import { Link, NavLink } from "react-router";
import { Icon } from "./ui";

/** Chrome for the standalone reference pages (/docs, /features). These live
 *  outside the main app Layout — no sidebar — so they get their own slim top
 *  bar: brand home link, a Docs↔Features switch, and a "Back to app" button. */
export default function InfoShell({ children }: { children: ReactNode }) {
  return (
    <div className="info-shell">
      <header className="info-topbar">
        <Link to="/" className="info-brand" aria-label="DQ Sentinel — home">
          <span className="logo-mark">
            <Icon name="shield" size={15} />
          </span>
          DQ Sentinel
        </Link>
        <nav className="info-nav" aria-label="Reference">
          <NavLink to="/docs" className={({ isActive }) => `info-tab${isActive ? " active" : ""}`}>
            <Icon name="book" size={15} />
            Documentation
          </NavLink>
          <NavLink to="/features" className={({ isActive }) => `info-tab${isActive ? " active" : ""}`}>
            <Icon name="sparkles" size={15} />
            Features
          </NavLink>
        </nav>
        <Link to="/" className="btn small info-back">
          <Icon name="arrow-left" size={14} />
          Back to app
        </Link>
      </header>
      <main className="info-main">{children}</main>
    </div>
  );
}
