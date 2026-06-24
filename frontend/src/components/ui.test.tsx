import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Modal, StatusPill } from "./ui";

describe("StatusPill", () => {
  it("always renders the status word (never colour-only) with a tone class", () => {
    const { container } = render(<StatusPill value="fail" />);
    expect(screen.getByText("fail")).toBeInTheDocument();
    expect(container.querySelector(".pill")?.className).toMatch(/tone-/);
  });

  it("renders a neutral em-dash for a null value", () => {
    render(<StatusPill value={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});

describe("Modal", () => {
  it("renders its title and closes on Escape", () => {
    const onClose = vi.fn();
    render(
      <Modal title="Appearance" onClose={onClose}>
        <button type="button">Inside</button>
      </Modal>,
    );
    expect(screen.getByText("Appearance")).toBeInTheDocument();
    // Escape on a child bubbles to the dialog's onKeyDown -> requestClose -> onClose.
    fireEvent.keyDown(screen.getByText("Inside"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
