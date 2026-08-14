// Клиент API.
//
// Отвечает за две вещи, которые легко сделать неправильно:
//
// 1. CSRF. Сервер кладёт токен в НЕ-HttpOnly cookie; на каждый небезопасный
//    запрос мы обязаны продублировать его в заголовке X-CSRF-Token. Браузер
//    сам этого не делает - в этом и смысл защиты. Читаем cookie здесь.
// 2. Единый разбор ошибок. Сервер отвечает конвертом
//    { error: { code, message }, request_id }. Превращаем его в типизированный
//    ApiError, чтобы интерфейс ветвился по code, а не по тексту.

export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> };
  request_id: string;
}

export class ApiError extends Error {
  code: string;
  status: number;
  requestId: string;
  details?: Record<string, unknown>;

  constructor(status: number, body: ApiErrorBody) {
    super(body.error?.message ?? "Ошибка запроса");
    this.name = "ApiError";
    this.status = status;
    this.code = body.error?.code ?? "unknown";
    this.requestId = body.request_id ?? "-";
    this.details = body.error?.details;
  }
}

const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

function readCookie(name: string): string {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(";")) {
    const c = part.trim();
    if (c.startsWith(prefix)) return decodeURIComponent(c.slice(prefix.length));
  }
  return "";
}

// Имя CSRF-cookie повторяет логику сервера: префикс __Host- живёт только
// поверх HTTPS, по HTTP сервер его снимает. Проверяем оба варианта.
function csrfToken(): string {
  return readCookie("__Host-sid-csrf") || readCookie("sid-csrf");
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json" };

  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (!SAFE.has(method)) {
    const token = csrfToken();
    if (token) headers["X-CSRF-Token"] = token;
  }

  const response = await fetch(`/api${path}`, {
    method,
    headers,
    // credentials: include - иначе браузер не пошлёт cookie сессии.
    credentials: "same-origin",
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    signal: options.signal,
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const data = text ? JSON.parse(text) : null;

  if (!response.ok) {
    throw new ApiError(response.status, data as ApiErrorBody);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { signal }),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: "POST", body }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: "PATCH", body }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

// типы, общие для страниц

export interface User {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  is_superuser: boolean;
  totp_enabled: boolean;
  email_verified_at: string | null;
  last_login_at: string | null;
  created_at: string;
}

export interface OrgSummary {
  id: string;
  slug: string;
  name: string;
  plan: string;
  role: string;
  members_count: number;
  pending_reviews: number;
}

export interface Review {
  id: string;
  author_name: string;
  rating: number;
  title: string;
  body: string;
  created_at: string;
}

export interface ReviewAdmin extends Review {
  author_email: string;
  status: string;
  moderation_note: string;
  moderated_at: string | null;
}

export interface Paged<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ReviewStats {
  total: number;
  average: number;
  distribution: Record<string, number>;
}
