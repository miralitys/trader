import { NextRequest } from 'next/server'

const API_PREFIX = '/api/'

function resolveBackendBase(): string {
  const raw =
    process.env.BACKEND_PROXY_TARGET ||
    process.env.NEXT_PUBLIC_API_URL ||
    'http://localhost:8000'

  const cleaned = raw.trim().replace(/\/+$/, '')
  if (!cleaned) {
    return 'http://localhost:8000'
  }

  if (cleaned.includes('://')) {
    return cleaned
  }

  return `http://${cleaned}`
}

function buildTargetUrl(pathSegments: string[], search: string): string {
  const base = resolveBackendBase()
  const path = pathSegments.join('/')
  const fullPath = `${API_PREFIX}${path}`
  return `${base}${fullPath}${search}`
}

async function proxy(request: NextRequest, pathSegments: string[]): Promise<Response> {
  const targetUrl = buildTargetUrl(pathSegments, request.nextUrl.search)
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

  const upstream = await fetch(targetUrl, requestInit)
  const responseHeaders = new Headers(upstream.headers)
  responseHeaders.delete('content-length')

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  })
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
