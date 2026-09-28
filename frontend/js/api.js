// Thin client for the NEXUS BI API. Identical requests share one in-flight promise.

const BASE = "/api/v1";
const memo = new Map();

export class ApiError extends Error {
  constructor(message, status, details) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

export function url(path, params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") query.set(key, value);
  }
  const qs = query.toString();
  return `${BASE}${path}${qs ? `?${qs}` : ""}`;
}

export function get(path, params = {}) {
  const target = url(path, params);
  if (!memo.has(target)) {
    const request = fetch(target, { headers: { Accept: "application/json" } })
      .then(async (response) => {
        const body = await response.json().catch(() => null);
        if (!response.ok) {
          const error = body?.error;
          throw new ApiError(error?.message || `Request failed (${response.status})`, response.status, error?.details);
        }
        return body;
      })
      .catch((error) => {
        memo.delete(target); // never cache failures
        if (error instanceof ApiError) throw error;
        throw new ApiError("The API is not reachable. Is the server running?", 0);
      });
    memo.set(target, request);
  }
  return memo.get(target);
}

/** POST without memoisation (used by the AI analyst: every question is a new request). */
export async function post(path, body, headers = {}) {
  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", ...headers },
      body: JSON.stringify(body),
    });
  } catch (error) {
    throw new ApiError("The API is not reachable. Is the server running?", 0);
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = data?.error;
    throw new ApiError(error?.message || `Request failed (${response.status})`, response.status, error?.details);
  }
  return data;
}
