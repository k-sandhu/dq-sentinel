import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const login = vi.fn();
vi.mock("../auth", () => ({ useAuth: () => ({ login, user: null, loading: false, logout: vi.fn() }) }));

import LoginPage from "./LoginPage";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});

describe("LoginPage seeded-credential hint (#308)", () => {
  it("does NOT advertise the seeded admin credentials outside a dev build", () => {
    // Production build signal. Vite replaces `import.meta.env.DEV` with `false` in a
    // real build (so the string is dead-code-eliminated); vitest lets us stub it to
    // assert the rendered output for that case.
    vi.stubEnv("DEV", false);
    render(<LoginPage />);

    expect(screen.queryByTestId("dev-login-hint")).toBeNull();
    expect(screen.queryByText(/admin123/)).toBeNull();
    expect(screen.queryByText(/default dev login/i)).toBeNull();
    // The placeholder ships in every build, so it must not name the seeded account either.
    expect(screen.getByLabelText(/email/i)).toHaveAttribute("placeholder", "you@company.com");
    // The form itself must still be there — the gate hides the hint, not the page.
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });

  it("still shows the hint in a dev build, so local setup stays frictionless", () => {
    vi.stubEnv("DEV", true);
    render(<LoginPage />);

    expect(screen.getByTestId("dev-login-hint")).toHaveTextContent(
      "Default dev login: admin@example.com / admin123",
    );
  });
});

describe("LoginPage form", () => {
  it("submits the typed credentials", async () => {
    vi.stubEnv("DEV", false);
    const user = userEvent.setup();
    render(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "analyst@example.com");
    await user.type(screen.getByLabelText(/password/i), "hunter2");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(login).toHaveBeenCalledWith("analyst@example.com", "hunter2");
  });

  it("surfaces a failed login as an error message", async () => {
    vi.stubEnv("DEV", false);
    login.mockRejectedValueOnce(new Error("Invalid credentials"));
    const user = userEvent.setup();
    render(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "analyst@example.com");
    await user.type(screen.getByLabelText(/password/i), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Invalid credentials")).toBeInTheDocument();
  });
});
