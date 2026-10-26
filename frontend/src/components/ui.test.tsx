import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
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
  it("returns focus to its opener after an auto-focused dialog closes", async () => {
    function Example() {
      const [open, setOpen] = useState(false);
      return <>
        <button onClick={() => setOpen(true)}>Open dialog</button>
        {open && <Modal title="Confirm change" onClose={() => setOpen(false)}
          footer={<button autoFocus onClick={() => setOpen(false)}>Keep editing</button>}>
          Unsaved content
        </Modal>}
      </>;
    }
    const user = userEvent.setup();
    render(<Example />);
    const opener = screen.getByRole("button", { name: "Open dialog" });
    await user.click(opener);
    expect(screen.getByRole("button", { name: "Keep editing" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

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
