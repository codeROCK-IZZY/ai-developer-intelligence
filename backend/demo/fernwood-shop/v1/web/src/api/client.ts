/** Minimal typed client for the storefront API. */
export interface Product {
  sku: string;
  name: string;
  slug: string;
  summary: string;
}

export interface OrderResult {
  id: number;
  total_cents: number;
  status: string;
}

const BASE = "/api";

/** Call the API with JSON; throws with the server's message when the response is not OK. */
export async function apiFetch<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`API ${response.status}: ${detail}`);
  }
  return (await response.json()) as T;
}

export function searchProducts(query: string): Promise<Product[]> {
  return apiFetch<Product[]>(`/products/search?q=${encodeURIComponent(query)}`);
}

/** Exchange e-mail and password for an access token. */
export async function login(email: string, password: string): Promise<string> {
  const result = await apiFetch<{ access_token: string }>("/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  return result.access_token;
}

export function placeOrder(
  token: string,
  items: Record<string, number>,
  region: string,
  cardToken: string,
): Promise<OrderResult> {
  const body = JSON.stringify({ items, region, card_token: cardToken });
  return apiFetch<OrderResult>("/orders", { method: "POST", body }, token);
}
