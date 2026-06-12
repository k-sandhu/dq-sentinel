import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Assistant/report markdown. remark-gfm is required for the tables the
 *  models routinely emit — without it they render as raw pipe characters. */
export default function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
