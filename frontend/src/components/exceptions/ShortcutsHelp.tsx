// The "?" cheat-sheet popover — doubles as the discoverability surface for the
// keyboard shortcuts (#63 a11y).

import { Modal } from "../ui";

const SHORTCUTS: { keys: string; desc: string }[] = [
  { keys: "j / k", desc: "Move row focus down / up" },
  { keys: "x", desc: "Toggle select on the focused row" },
  { keys: "o / Enter", desc: "Open the detail panel" },
  { keys: "Esc", desc: "Close panel / clear selection" },
  { keys: "a", desc: "Acknowledge" },
  { keys: "e", desc: "Mark expected" },
  { keys: "r", desc: "Resolve" },
  { keys: "m", desc: "Mute" },
  { keys: "u", desc: "Reopen" },
  { keys: "Shift+A", desc: "Assign to me" },
  { keys: "?", desc: "Toggle this help" },
];

export default function ShortcutsHelp({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="Keyboard shortcuts" onClose={onClose}>
      <div className="xw-help">
        <p className="xw-muted">
          Letter actions apply to the current selection, or the focused row when nothing is selected.
        </p>
        <table className="xw-help-table">
          <tbody>
            {SHORTCUTS.map((s) => (
              <tr key={s.keys}>
                <td>
                  <span className="kbd">{s.keys}</span>
                </td>
                <td>{s.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Modal>
  );
}
