import type { ChatMessage } from "../../api/types";
import Markdown from "../Markdown";
import { StepList } from "./StepList";

/** One chat turn: a plain user bubble, or an assistant bubble rendering its step
 *  thread (falling back to raw markdown content when there are no steps). */
export function MessageView({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return <div className="chat-msg user">{message.content}</div>;
  }
  return (
    <div className="chat-msg assistant">
      <StepList steps={message.steps} />
      {message.steps.length === 0 && message.content && <Markdown>{message.content}</Markdown>}
    </div>
  );
}
