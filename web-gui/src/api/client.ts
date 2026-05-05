import { reportWebGuiError } from './errorReporter'

const isDevPort = window.location.port === '5173' || window.location.port === '5174'
export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || (isDevPort ? 'http://127.0.0.1:8000/api' : '/api')
const API_BASE = API_BASE_URL

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public data?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

function getRequestFailureStatusClass(error: unknown): 'timeout' | 'network' {
  if (error instanceof Error && error.name === 'AbortError') {
    return 'timeout'
  }
  return 'network'
}

function buildHeaders(headersLike?: HeadersInit, body?: BodyInit | null): Headers {
  const headers = new Headers(headersLike)
  if (body && typeof body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  return headers
}

async function trackedApiFetch(url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init)
  } catch (error) {
    reportWebGuiError({
      level: 'warning',
      source: 'api-client',
      kind: 'api_network_error',
      statusClass: getRequestFailureStatusClass(error),
      message: `${init.method ?? 'GET'} ${url} failed in browser`,
      extra: error instanceof Error ? `${error.name}: ${error.message}` : String(error),
    })
    throw error
  }
}

function formatApiDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail

  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object') {
          const maybeError = item as { loc?: unknown; msg?: unknown }
          const loc = Array.isArray(maybeError.loc)
            ? maybeError.loc.map((segment) => String(segment)).join(' -> ')
            : null
          const msg = typeof maybeError.msg === 'string' ? maybeError.msg : null
          if (loc && msg) return `${loc}: ${msg}`
          if (msg) return msg
        }
        return null
      })
      .filter((part): part is string => Boolean(part))

    if (parts.length > 0) return parts.join('; ')
  }

  if (detail && typeof detail === 'object') {
    const maybeDetail = detail as { message?: unknown; error?: unknown }
    if (typeof maybeDetail.message === 'string') return maybeDetail.message
    if (typeof maybeDetail.error === 'string') return maybeDetail.error
    try {
      return JSON.stringify(detail)
    } catch {
      return String(detail)
    }
  }

  return String(detail)
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    const detail = formatApiDetail(data?.detail || response.statusText)
    if (response.status >= 500) {
      reportWebGuiError({
        level: 'warning',
        source: 'api-client',
        kind: 'api_http_error',
        statusClass: '5xx',
        message: `${response.status} ${response.statusText} - ${response.url}`,
      })
    }
    throw new ApiError(detail, response.status, data)
  }
  return response.json()
}

export async function authenticatedFetch(
  url: string,
  init: RequestInit = {},
): Promise<Response> {
  return trackedApiFetch(url, {
    ...init,
    headers: buildHeaders(init.headers, init.body ?? null),
  })
}

const attachParams = (endpoint: string, params?: Record<string, string | number | boolean>) => {
  if (!params || Object.keys(params).length === 0) {
    return `${API_BASE}${endpoint}`
  }
  const url = new URL(`${API_BASE}${endpoint}`, window.location.origin)
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null) {
      url.searchParams.set(key, String(value))
    }
  })
  return url.toString()
}

export async function getApiBootstrapStatus(): Promise<{
  ready: boolean
  apiUnavailable?: boolean
  message?: string
}> {
  return { ready: true }
}

export const apiClient = {
  async get<T>(endpoint: string, params?: Record<string, string | number | boolean>): Promise<T> {
    const url = attachParams(endpoint, params)
    const response = await authenticatedFetch(url, {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' },
    })
    return handleResponse<T>(response)
  },

  async post<T>(endpoint: string, data?: unknown, params?: Record<string, string | number | boolean>): Promise<T> {
    const body = data ? JSON.stringify(data) : undefined
    const url = attachParams(endpoint, params)
    const response = await authenticatedFetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    })
    return handleResponse<T>(response)
  },

  async put<T>(endpoint: string, data?: unknown): Promise<T> {
    const body = data ? JSON.stringify(data) : undefined
    const url = `${API_BASE}${endpoint}`
    const response = await authenticatedFetch(url, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body,
    })
    return handleResponse<T>(response)
  },

  async delete<T>(endpoint: string, params?: Record<string, string | number | boolean>): Promise<T> {
    const url = attachParams(endpoint, params)
    const response = await authenticatedFetch(url, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
    })
    return handleResponse<T>(response)
  },
}
