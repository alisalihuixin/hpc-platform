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
  GroupUsageRanking,
  JobCreate,
  JobDetail,
  JobList,
  JobRead,
  JobStatus,
  LoginRequest,
  LogoutRequest,
  Message,
  NodeList,
  NodeStatus,
  QuotaList,
  RefreshRequest,
  Token,
  UserCreate,
  UserList,
  UserRead,
  UserUsage,
} from './types';

// Origin of the API. Empty means same-origin: the generated OpenAPI paths
// already carry the `/api/v1` prefix (Caddy routes `/api/*` to the backend),
// so adding a base path here would double it.
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? '';

const ACCESS_TOKEN_KEY = 'hpc.access_token';
const REFRESH_TOKEN_KEY = 'hpc.refresh_token';

// ---- endpoint table (validated against the generated OpenAPI paths) -------
export const endpoints = {
  login: '/api/v1/auth/login',
  refresh: '/api/v1/auth/refresh',
  logout: '/api/v1/auth/logout',
  me: '/api/v1/auth/me',
  users: '/api/v1/users',
  nodes: '/api/v1/nodes',
  jobs: '/api/v1/jobs',
  quotasMe: '/api/v1/quotas/me',
  usageMe: '/api/v1/usage/me',
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
    } else if (Array.isArray(body.detail)) {
      // FastAPI 422: [{loc, msg, type}]. Surface the field messages.
      const messages = body.detail
        .map((item) =>
          item && typeof item === 'object' && 'msg' in item
            ? String((item as { msg: unknown }).msg)
            : null,
        )
        .filter((message): message is string => Boolean(message));
      if (messages.length > 0) detail = messages.join('；');
    }
  } catch {
    // non-JSON error body - keep the status text
  }
  return new ApiError(response.status, detail);
}

/** Serialise defined query parameters, skipping empty strings. */
function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
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

// ---- F4 jobs -------------------------------------------------------------
export interface JobQuery {
  status?: JobStatus;
  since?: string;
  until?: string;
  skip?: number;
  limit?: number;
}

export const jobsApi = {
  async list(params: JobQuery = {}): Promise<JobList> {
    return request<JobList>(`${endpoints.jobs}${query({ ...params })}`);
  },

  async create(payload: JobCreate): Promise<JobRead> {
    return request<JobRead>(endpoints.jobs, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  async get(jobId: number): Promise<JobDetail> {
    return request<JobDetail>(`${endpoints.jobs}/${jobId}`);
  },

  async cancel(jobId: number): Promise<JobRead> {
    return request<JobRead>(`${endpoints.jobs}/${jobId}/cancel`, { method: 'POST' });
  },
};

// ---- F2 nodes ------------------------------------------------------------
export const nodesApi = {
  async list(status?: NodeStatus): Promise<NodeList> {
    return request<NodeList>(`${endpoints.nodes}${query({ status, limit: 200 })}`);
  },
};

// ---- F3 quotas -----------------------------------------------------------
export const quotasApi = {
  async mine(): Promise<QuotaList> {
    return request<QuotaList>(endpoints.quotasMe);
  },
};

// ---- F5 usage ------------------------------------------------------------
export interface UsageQuery {
  since?: string;
  until?: string;
}

export const usageApi = {
  async mine(params: UsageQuery = {}): Promise<UserUsage> {
    return request<UserUsage>(`${endpoints.usageMe}${query({ ...params })}`);
  },

  async groupRanking(groupId: number, params: UsageQuery = {}): Promise<GroupUsageRanking> {
    return request<GroupUsageRanking>(
      `/api/v1/usage/groups/${groupId}${query({ ...params })}`,
    );
  },
};
