import type { KeyboardEvent } from "react";

import { Icon } from "../ui";

/** The message composer: a textarea + a Send button that flips to a Stop button while
 *  the assistant is generating. Presentational — the page owns input + enabled state. */
export function ChatComposer({
  input,
  placeholder,
  busy,
  textareaDisabled,
  sendDisabled,
  onInput,
  onKey,
  onSend,
  onStop,
}: {
  input: string;
  placeholder: string;
  busy: boolean;
  textareaDisabled: boolean;
  sendDisabled: boolean;
  onInput: (value: string) => void;
  onKey: (e: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSend: () => void;
  onStop: () => void;
}) {
  return (
    <div className="chat-composer">
      <textarea
        rows={2}
        placeholder={placeholder}
        value={input}
        onChange={(e) => onInput(e.target.value)}
        onKeyDown={onKey}
        disabled={textareaDisabled}
      />
      {busy ? (
        <button className="danger" onClick={onStop} title="Stop generating">
          <Icon name="x" size={14} /> Stop
        </button>
      ) : (
        <button className="primary" onClick={onSend} disabled={sendDisabled}>
          <Icon name="bolt" size={14} /> Send
        </button>
      )}
    </div>
  );
}
