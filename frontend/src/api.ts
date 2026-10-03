export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(text.trim().slice(0, 180) || response.statusText);
  }
  if (!response.ok) {
    throw new Error(body?.detail || response.statusText);
  }
  return body as T;
}
