import { useEffect, useState } from "react";

import { AXES, accentSwatch, getAxis, setAccent, setAxis, type AxisName } from "../lib/appearance";
import { subscribePrefs } from "../lib/prefs";
import { Icon, Modal } from "./ui";

// Axes shown in the drawer. `nav` is intentionally omitted until its icons-only /
// centered CSS variants land with the sidebar reskin (#173) — shipping the control
// before the styling would be a no-op the user can see do nothing.
const ORDER: AxisName[] = ["dir", "mode", "density", "font"];

/** The appearance controls (theme · mode · density · font · accent). Each change is
 *  applied to `<html>` immediately and persisted through the prefs.ts chokepoint, so
 *  it survives reload and syncs across tabs via the `dq:prefs` event. */
function AppearanceControls() {
  // The source of truth is the DOM + localStorage (owned by the appearance helpers);
  // this bump just re-renders the controls to reflect the new current values.
  const [, bump] = useState(0);
  const rerender = () => bump((n) => n + 1);
  // Reflect changes made elsewhere — another tab (via `storage`) or any prefs write
  // (`dq:prefs`) — so an open drawer never shows stale selections (#181 review).
  useEffect(() => {
    const unsub = subscribePrefs(rerender);
    window.addEventListener("storage", rerender);
    return () => {
      unsub();
      window.removeEventListener("storage", rerender);
    };
  }, []);

  return (
    <div className="appearance-grid">
      {ORDER.map((name) => {
        const spec = AXES[name];
        const current = getAxis(name);
        return (
          <fieldset className="appearance-axis" key={name}>
            <legend>{spec.label}</legend>
            <div className="seg" role="radiogroup" aria-label={spec.label}>
              {spec.options.map((o) => (
                <button
                  key={o.value}
                  type="button"
                  role="radio"
                  aria-checked={current === o.value}
                  className={"seg-btn" + (current === o.value ? " on" : "")}
                  onClick={() => {
                    setAxis(name, o.value);
                    rerender();
                  }}
                >
                  {o.label}
                </button>
              ))}
            </div>
          </fieldset>
        );
      })}
      <fieldset className="appearance-axis">
        <legend>Accent</legend>
        <div className="accent-row">
          <input
            type="color"
            aria-label="Accent colour"
            value={accentSwatch()}
            onChange={(e) => {
              setAccent(e.target.value);
              rerender();
            }}
          />
          <button
            type="button"
            className="small"
            onClick={() => {
              setAccent(null);
              rerender();
            }}
          >
            Reset
          </button>
        </div>
      </fieldset>
    </div>
  );
}

/** Topbar trigger for the Appearance panel — replaces the standalone dark-mode and
 *  density toggles with one drawer driving every axis (#172). */
export function AppearanceButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className="small icon-only"
        onClick={() => setOpen(true)}
        title="Appearance"
        aria-label="Open appearance settings"
        aria-haspopup="dialog"
      >
        <Icon name="settings" size={14} />
      </button>
      {open && (
        <Modal title="Appearance" onClose={() => setOpen(false)}>
          <AppearanceControls />
        </Modal>
      )}
    </>
  );
}
