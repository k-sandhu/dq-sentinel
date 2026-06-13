// App-wide confirmation dialog (BF-4): a promise-based useConfirm() so destructive
// actions route through the styled Modal instead of native confirm()/no guard at all.
import { createContext, useCallback, useContext, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Modal } from "./ui";

export interface ConfirmOptions {
  title: string;
  body?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Renders the confirm button in solid red for destructive actions. */
  danger?: boolean;
  /** When set, the user must type this exact string (e.g. a name) to enable confirm. */
  typeToConfirm?: string;
}

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn>(() => Promise.resolve(false));

export function useConfirm(): ConfirmFn {
  return useContext(ConfirmContext);
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [opts, setOpts] = useState<ConfirmOptions | null>(null);
  const [typed, setTyped] = useState("");
  const resolverRef = useRef<((ok: boolean) => void) | null>(null);

  const confirm = useCallback<ConfirmFn>((options) => {
    setOpts(options);
    setTyped("");
    return new Promise<boolean>((resolve) => {
      resolverRef.current = resolve;
    });
  }, []);

  const settle = (ok: boolean) => {
    resolverRef.current?.(ok);
    resolverRef.current = null;
    setOpts(null);
    setTyped("");
  };

  const needsType = !!opts?.typeToConfirm;
  const blocked = needsType && typed !== opts?.typeToConfirm;

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {opts && (
        <Modal
          title={opts.title}
          onClose={() => settle(false)}
          footer={
            <>
              <button onClick={() => settle(false)}>{opts.cancelLabel ?? "Cancel"}</button>
              <button
                className={opts.danger ? "danger-solid" : "primary"}
                disabled={blocked}
                onClick={() => settle(true)}
                autoFocus={!needsType}
              >
                {opts.confirmLabel ?? "Confirm"}
              </button>
            </>
          }
        >
          {opts.body && <div style={{ fontSize: 13.5, lineHeight: 1.55 }}>{opts.body}</div>}
          {needsType && (
            <label className="field" style={{ marginTop: 12 }}>
              Type <code>{opts.typeToConfirm}</code> to confirm
              <input type="text" value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus />
            </label>
          )}
        </Modal>
      )}
    </ConfirmContext.Provider>
  );
}
