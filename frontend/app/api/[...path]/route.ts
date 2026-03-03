import { NextRequest } from 'next/server'

const API_PREFIX = '/api/'

function normalizeBackendBase(raw: string): string {
  const cleaned = raw.trim().replace(/\/+$/, '')
  if (!cleaned) return ''
  if (cleaned.includes('://')) return cleaned
  return `http://${cleaned}`
}

function candidateBackendBases(): string[] {
  const envCandidates = [
    process.env.BACKEND_PROXY_TARGET || '',
    process.env.BACKEND_FALLBACK_URL || '',
    process.env.NEXT_PUBLIC_API_URL || '',
  ]
  const normalized = envCandidates.map(normalizeBackendBase).filter(Boolean)
  const deduped = Array.from(new Set(normalized))
  if (deduped.length) return deduped
  return ['http://localhost:8000']
}

function buildTargetUrl(base: string, pathSegments: string[], search: string): string {
  const path = pathSegments.join('/')
  const fullPath = `${API_PREFIX}${path}`
  return `${base}${fullPath}${search}`
}

async function proxy(request: NextRequest, pathSegments: string[]): Promise<Response> {
  const headers = new Headers(request.headers)
  headers.delete('host')
  headers.delete('origin')
  headers.set('x-forwarded-host', request.headers.get('host') || '')
  headers.set('x-forwarded-proto', request.nextUrl.protocol.replace(':', ''))

  const method = request.method.toUpperCase()
  const requestInit: RequestInit = {
    method,
    headers,
    redirect: 'manual',
    cache: 'no-store',
  }
  if (method !== 'GET' && method !== 'HEAD') {
    requestInit.body = await request.arrayBuffer()
  }

  const tried: string[] = []
  let lastError = ''
  for (const base of candidateBackendBases()) {
    const targetUrl = buildTargetUrl(base, pathSegments, request.nextUrl.search)
    tried.push(base)
    try {
      const upstream = await fetch(targetUrl, requestInit)
      const responseHeaders = new Headers(upstream.headers)
      responseHeaders.delete('content-length')
      return new Response(upstream.body, {
        status: upstream.status,
        statusText: upstream.statusText,
        headers: responseHeaders,
      })
    } catch (err) {
      lastError = err instanceof Error ? err.message : 'unknown fetch error'
    }
  }

  return Response.json(
    {
      detail: 'Backend proxy failed',
      tried,
      last_error: lastError,
    },
    { status: 502 }
  )
}

type RouteContext = {
  params: { path: string[] }
}

export async function GET(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context.params.path)
}

export async function POST(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context.params.path)
}

export async function PUT(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context.params.path)
}

export async function PATCH(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context.params.path)
}

export async function DELETE(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context.params.path)
}

export async function OPTIONS(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context.params.path)
}

export async function HEAD(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context.params.path)
}
