import type { NoteWidget as NoteWidgetT } from "../../api/types";
import Markdown from "../Markdown";

/** A free-text note (runbook links, context). Rendered with the shared Markdown
 *  component — ReactMarkdown + remark-gfm, NO raw HTML (same safety rule as #65),
 *  so pasted markup can't inject script/markup. */
export default function NoteWidget({ widget }: { widget: NoteWidgetT }) {
  const md = widget.config.markdown?.trim();
  if (!md) return <div className="empty" style={{ padding: 10 }}>Empty note</div>;
  return (
    <div className="cd-note">
      <Markdown>{md}</Markdown>
    </div>
  );
}
