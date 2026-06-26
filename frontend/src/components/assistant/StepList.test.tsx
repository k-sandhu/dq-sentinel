import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ChatStep } from "../../api/types";
import { StepList } from "./StepList";

const steps = (arr: Partial<ChatStep>[]) => arr as unknown as ChatStep[];

describe("StepList (act-not-answer thread)", () => {
  it("renders a text step as markdown", () => {
    const { container } = render(<StepList steps={steps([{ type: "text", content: "Hello **world**" }])} />);
    expect(container.textContent).toContain("Hello world");
  });

  it("pairs a sql step with its following result in one activity, showing the purpose", () => {
    const { container } = render(
      <StepList
        steps={steps([
          { type: "sql", purpose: "Count orders", sql: "SELECT count(*) FROM orders" },
          { type: "result", content: "42" },
        ])}
      />,
    );
    // the result is consumed into the sql step → exactly one collapsible
    expect(container.querySelectorAll("details.chat-activity").length).toBe(1);
    expect(screen.getByText("Count orders")).toBeInTheDocument();
    expect(screen.getByText("SELECT count(*) FROM orders")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("flags a failed query result", () => {
    render(
      <StepList
        steps={steps([
          { type: "sql", purpose: "Bad query", sql: "SELECT oops" },
          { type: "result", content: "boom", error: true },
        ])}
      />,
    );
    expect(screen.getByText("failed")).toBeInTheDocument();
  });

  it("opens a write-authoring tool by default with a clear verb", () => {
    const { container } = render(<StepList steps={steps([{ type: "tool", name: "create_check" }])} />);
    expect(container.querySelector("details.chat-activity")).toHaveAttribute("open");
    expect(screen.getByText("Created a check")).toBeInTheDocument();
  });

  it("labels a read tool without opening it", () => {
    const { container } = render(<StepList steps={steps([{ type: "tool", name: "get_dataset_health" }])} />);
    expect(container.querySelector("details.chat-activity")).not.toHaveAttribute("open");
    expect(screen.getByText(/Looked at/)).toBeInTheDocument();
  });

  it("renders an error step in an error box", () => {
    const { container } = render(<StepList steps={steps([{ type: "error", content: "kaput" }])} />);
    expect(container.querySelector(".error-box")?.textContent).toContain("kaput");
  });
});
