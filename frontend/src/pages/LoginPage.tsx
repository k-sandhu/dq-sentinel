/// <reference types="vite/client" />
import { useState } from "react";
import type { FormEvent } from "react";
import { useAuth } from "../auth";
import { Icon } from "../components/ui";

export default function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(email, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={submit}>
        <div className="logo">
          <span className="logo-mark">
            <Icon name="shield" size={18} />
          </span>
          DQ Sentinel
        </div>
        {error && <div className="error-box">{error}</div>}
        <label className="field">
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            /* Generic example, not the seeded account (#308): the placeholder ships in
               every build, so it must not name a real default user. */
            placeholder="you@company.com"
            autoFocus
            required
          />
        </label>
        <label className="field">
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        <button className="primary" type="submit" disabled={busy} style={{ width: "100%", justifyContent: "center", marginTop: 6 }}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        {/*
          Seeded-credential hint, dev builds only (#308). `import.meta.env.DEV` is
          statically replaced by Vite at build time, so in a production bundle this
          whole branch — the credential string included — is dead code and is
          eliminated. It must stay an `import.meta.env.DEV` literal: hiding it behind
          a runtime flag, a CSS rule or a variable indirection would still ship the
          string to an unauthenticated page.
        */}
        {import.meta.env.DEV && (
          <p
            data-testid="dev-login-hint"
            style={{ fontSize: 12, color: "var(--text-light)", textAlign: "center", marginBottom: 0 }}
          >
            Default dev login: admin@example.com / admin123
          </p>
        )}
      </form>
    </div>
  );
}
