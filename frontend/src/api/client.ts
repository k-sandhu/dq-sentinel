// Thin typed fetch wrapper. All server state goes through TanStack Query using
// these helpers; never fetch() directly from components.

const BASE = "/api/v1";
const TOKEN_KEY = "dq_token";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

function requestHeaders(hasJsonBody = false): Record<string, string> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (hasJsonBody) headers["Content-Type"] = "application/json";
  return headers;
}

async function parseErrorMessage(resp: Response): Promise<string> {
  let message = `${resp.status} ${resp.statusText}`;
  try {
    const data = await resp.json();
    if (typeof data.detail === "string") message = data.detail;
    else if (Array.isArray(data.detail)) {
      message = data.detail
        .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).join(".")}: ${d.msg}`)
        .join("; ");
    }
  } catch {
    /* non-JSON error body */
  }
  return message;
}

async function ensureOk(resp: Response, path: string): Promise<void> {
  if (resp.status === 401 && !path.startsWith("/auth/login")) {
    setToken(null);
    window.location.href = "/login";
    throw new ApiError(401, "Session expired");
  }
  if (!resp.ok) {
    throw new ApiError(resp.status, await parseErrorMessage(resp));
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method,
    headers: requestHeaders(body !== undefined),
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  await ensureOk(resp, path);
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

async function download(path: string, filename: string): Promise<void> {
  const resp = await fetch(`${BASE}${path}`, {
    method: "GET",
    headers: requestHeaders(),
  });

  await ensureOk(resp, path);

  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
  download,
};
