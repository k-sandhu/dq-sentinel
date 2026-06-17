// Workbench SQL editor (#104): CodeMirror 6 with SQL syntax highlighting, line
// numbers, bracket matching, and schema-aware autocomplete fed from the connection's
// introspected schema tree. Replaces the old raw <textarea>.
//
// Keybindings: Ctrl/Cmd+Enter runs (added at highest precedence so it beats the
// default "insert newline"); Ctrl/Cmd+/ toggles comments (from CodeMirror's default
// keymap, included by basicSetup). Light/dark follows the app theme.

import CodeMirror, { EditorView, Prec, keymap } from "@uiw/react-codemirror";
import type { Extension } from "@uiw/react-codemirror";
import { useMemo, useRef } from "react";
import type { SchemaTable } from "../../api/types";
import { sqlExtension } from "../../lib/sqlSchema";
import { useThemeMode } from "../../lib/useThemeMode";

export default function SqlEditor({
  value,
  onChange,
  onRun,
  tables,
  dialect,
  readOnly = false,
  minHeight = 150,
  placeholder,
}: {
  value: string;
  onChange: (next: string) => void;
  onRun?: () => void;
  tables: readonly SchemaTable[];
  dialect: string | null;
  readOnly?: boolean;
  minHeight?: number;
  placeholder?: string;
}) {
  const theme = useThemeMode();

  // Keep the run handler current without rebuilding extensions every keystroke.
  const runRef = useRef(onRun);
  runRef.current = onRun;

  const extensions = useMemo<Extension[]>(
    () => [
      sqlExtension(dialect, tables),
      EditorView.lineWrapping,
      Prec.highest(
        keymap.of([
          {
            key: "Mod-Enter",
            preventDefault: true,
            run: () => {
              runRef.current?.();
              return true;
            },
          },
        ]),
      ),
    ],
    [dialect, tables],
  );

  return (
    <CodeMirror
      className="sql-editor"
      value={value}
      onChange={onChange}
      readOnly={readOnly}
      editable={!readOnly}
      theme={theme}
      placeholder={placeholder}
      extensions={extensions}
      minHeight={`${minHeight}px`}
      basicSetup={{
        lineNumbers: true,
        foldGutter: false,
        highlightActiveLine: !readOnly,
        autocompletion: true,
        // Run is bound above; let everything else (incl. Mod-/ comment toggle) stand.
      }}
    />
  );
}
