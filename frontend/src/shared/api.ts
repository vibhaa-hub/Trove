/** Thin fetch wrapper shared by all three apps. Same-origin only; cookies carry the session. */

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

type Json = Record<string, unknown> | unknown[];
export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: Json;
  signal?: AbortSignal;
}

export interface Client {
  get<T>(path: string, signal?: AbortSignal): Promise<T>;
  post<T>(path: string, body?: Json): Promise<T>;
  put<T>(path: string, body?: Json): Promise<T>;
  patch<T>(path: string, body?: Json): Promise<T>;
  del<T>(path: string, body?: Json): Promise<T>;
}

/**
 * Create a client for one API area. `onUnauthorized` runs on 401 so the app can drop back to
 * its sign-in screen; `quietPaths` are probes (e.g. "/me") whose 401 is expected.
 */
export function createClient(base = "", onUnauthorized?: () => void, quietPaths: string[] = []): Client {
  async function request<T>(path: string, { method = "GET", body, signal }: RequestOptions = {}): Promise<T> {
    let response: Response;
    try {
      response = await fetch(base + path, {
        method,
        signal,
        credentials: "same-origin",
        headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (error) {
      if ((error as Error).name === "AbortError") throw error;
      throw new ApiError("Can't reach Trove. Check your connection and try again.", 0);
    }
    let payload: unknown = {};
    try {
      payload = await response.json();
    } catch {
      /* empty or non-JSON body */
    }
    if (!response.ok) {
      if (response.status === 401 && !quietPaths.includes(path)) onUnauthorized?.();
      const message = (payload as { error?: string }).error ?? `Request failed (${response.status})`;
      throw new ApiError(message, response.status);
    }
    return payload as T;
  }
  return {
    get: (path, signal) => request(path, { signal }),
    post: (path, body = {}) => request(path, { method: "POST", body }),
    put: (path, body = {}) => request(path, { method: "PUT", body }),
    patch: (path, body = {}) => request(path, { method: "PATCH", body }),
    del: (path, body = {}) => request(path, { method: "DELETE", body }),
  };
}

export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;

export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(new Error(`Could not read ${file.name}`));
    reader.readAsDataURL(file);
  });
}

/** Read a chosen file from form data, enforcing presence and the 5 MB limit. */
export function takeFile(data: FormData, key: string, label = "File"): File {
  const file = data.get(key);
  if (!(file instanceof File) || !file.size) throw new Error(`Choose a file for “${label}”.`);
  if (file.size > MAX_UPLOAD_BYTES) throw new Error(`${file.name} is larger than 5 MB.`);
  return file;
}

export const enc = encodeURIComponent;

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
