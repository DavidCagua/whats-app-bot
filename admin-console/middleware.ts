import { NextRequest, NextResponse } from "next/server"

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl
  
  // Skip middleware for static files and API routes
  if (
    pathname.startsWith("/_next") ||
    pathname.startsWith("/api") ||
    pathname.includes(".")
  ) {
    return NextResponse.next()
  }

  // Check for auth session cookie
  const sessionCookie = request.cookies.get("authjs.session-token") || 
                       request.cookies.get("__Secure-authjs.session-token")

  // If on login page and already authenticated, redirect to dashboard
  if (pathname === "/login" && sessionCookie) {
    return NextResponse.redirect(new URL("/", request.url))
  }

  // If not on login page and not authenticated, redirect to login
  if (pathname !== "/login" && !sessionCookie) {
    return NextResponse.redirect(new URL("/login", request.url))
  }

  return NextResponse.next()
}

export const config = {
  matcher: [
    /*
     * Match all request paths except for the ones starting with:
     * - api/auth (authentication API routes)
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico (favicon file)
     */
    "/((?!api/auth|_next/static|_next/image|favicon.ico).*)",
  ],
}

