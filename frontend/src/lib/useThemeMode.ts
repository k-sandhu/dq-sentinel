import { useEffect, useState } from "react";

/** Tracks the app's light/dark theme so theme-aware widgets (the CodeMirror SQL
 *  editor, #104) can follow it. The toggle in Layout.tsx mutates
 *  `html[data-theme]` imperatively (and persists it to localStorage "dq-theme"),
 *  with no event or context to subscribe to — so we observe the attribute. */
export function useThemeMode(): "light" | "dark" {
  const read = (): "light" | "dark" =>
    document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  const [mode, setMode] = useState<"light" | "dark">(read);

  useEffect(() => {
    const el = document.documentElement;
    const observer = new MutationObserver(() => setMode(read()));
    observer.observe(el, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  return mode;
}
