/**
 * Typed HTTP client for the FastAPI backend.
 *
 * Endpoint paths are checked against the generated `paths` interface, and the
 * payload/response shapes come from `components['schemas']`.
 *
 * Tokens live in `localStorage` for M1 simplicity. A production hardening step
 * is to move the refresh token into an httpOnly, SameSite=Strict cookie.
 */
import type { paths } from './schema';
import type {
  LoginRequest,
  LogoutRequest,
  Message,
  RefreshRequest,
  Token,
  UserCreate,
  UserList,
  UserRead,
} from './types';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? '/api';

const ACCESS_TOKEN_KEY = 'hpc.access_token';
const REFRESH_TOKEN_KEY = 'hpc.refresh_token';

// ---- endpoint table (validated against the generated OpenAPI paths) -------
export const endpoints = {
  login: '/api/v1/auth/login',
  refresh: '/api/v1/auth/refresh',
  logout: '/api/v1/auth/logout',
  me: '/api/v1/auth/me',
  users: '/api/v1/users',
} satisfies Record<string, keyof paths>;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// ---- token storage -------------------------------------------------------
function isBrowser(): boolean {
  return typeof window !== 'undefined';
}

export function getAccessToken(): string | null {
  return isBrowser() ? window.localStorage.getItem(ACCESS_TOKEN_KEY) : null;
}

export function getRefreshToken(): string | null {
  return isBrowser() ? window.localStorage.getItem(REFRESH_TOKEN_KEY) : null;
}

export function storeTokens(tokens: Token): void {
  if (!isBrowser()) return;
  window.localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
  window.localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
}

export function clearTokens(): void {
  if (!isBrowser()) return;
  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
  window.localStorage.removeItem(REFRESH_TOKEN_KEY);
}

// ---- core fetch ----------------------------------------------------------
async function parseError(response: Response): Promise<ApiError> {
  let detail = response.statusText || `HTTP ${response.status}`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') {
      detail = body.detail;
    }
  } catch {
    // non-JSON error body - keep the status text
  }
  return new ApiError(response.status, detail);
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  withAuth = true,
  retryOn401 = true,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (withAuth) {
    const token = getAccessToken();
    if (token) headers.set('Authorization', `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });

  if (response.status === 401 && withAuth && retryOn401 && getRefreshToken()) {
    const refreshed = await tryRefresh();
    if (refreshed) return request<T>(path, init, withAuth, false);
  }

  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Attempt a silent refresh; returns false when the refresh token is unusable. */
async function tryRefresh(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;
  try {
    const body: RefreshRequest = { refresh_token: refreshToken };
    const tokens = await request<Token>(
      endpoints.refresh,
      { method: 'POST', body: JSON.stringify(body) },
      false,
      false,
    );
    storeTokens(tokens);
    return true;
  } catch {
    clearTokens();
    return false;
  }
}

// ---- typed API surface ---------------------------------------------------
export const authApi = {
  async login(payload: LoginRequest): Promise<Token> {
    const tokens = await request<Token>(
      endpoints.login,
      { method: 'POST', body: JSON.stringify(payload) },
      false,
    );
    storeTokens(tokens);
    return tokens;
  },

  async me(): Promise<UserRead> {
    return request<UserRead>(endpoints.me);
  },

  async logout(): Promise<Message> {
    const refreshToken = getRefreshToken();
    const body: LogoutRequest = { refresh_token: refreshToken };
    try {
      return await request<Message>(endpoints.logout, {
        method: 'POST',
        body: JSON.stringify(body),
      });
    } finally {
      clearTokens();
    }
  },
};

export const usersApi = {
  async list(skip = 0, limit = 50): Promise<UserList> {
    return request<UserList>(`${endpoints.users}?skip=${skip}&limit=${limit}`);
  },

  async create(payload: UserCreate): Promise<UserRead> {
    return request<UserRead>(endpoints.users, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
};
