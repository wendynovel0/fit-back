"""
FitMind AI Backend — FastAPI Application

Punto de entrada principal.
Inicia la base de datos, monta todos los routers, configura CORS.

Ejecutar:
  uvicorn main:app --reload --port 8000
"""

import os
import secrets
import time
from collections import defaultdict, deque
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from database.connection import init_db
from routers.auth import router as auth_router
from routers.chat import router as chat_router
from routers.profile import router as profile_router
from routers.secondary import (
    workouts_router,
    nutrition_router,
    progress_router,
    observability_router,
)

load_dotenv()

ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000",
).split(",")

# ─── Entorno ───────────────────────────────────────────────────────────────────
# En producción (ENVIRONMENT=production) se deshabilita Swagger/ReDoc/OpenAPI
# para no exponer el esquema completo de la API (punto 8 del checklist).

ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
IS_PROD = ENVIRONMENT == "production"

# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="FitMind AI API",
    description="Backend de la plataforma fitness inteligente impulsada por IA.",
    version="1.0.0",
    docs_url=None if IS_PROD else "/docs",
    redoc_url=None if IS_PROD else "/redoc",
    openapi_url=None if IS_PROD else "/openapi.json",
)

# ─── CORS ────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Conversation-ID", "X-Session-ID"],
)

# ─── Headers de seguridad (punto 12 del checklist) ────────────────────────────


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(self), camera=()"
    # API pura (JSON), no sirve HTML propio → CSP restrictiva por defecto.
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if IS_PROD:
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains; preload"
        )
    return response

# ─── Protección CSRF (patrón "doble cookie") ──────────────────────────────────
#
# La sesión pasó de "Authorization: Bearer <token>" (header — un sitio
# atacante no puede forzar al navegador a mandarlo) a cookies httpOnly (que
# el navegador SÍ adjunta solo en cada request al mismo origen, incluido uno
# disparado desde otro sitio). SameSite=Strict en las cookies de sesión ya
# bloquea la enorme mayoría de esos escenarios, pero esta capa adicional
# verifica, para toda mutación autenticada, que quien arma el request pueda
# leer la cookie no-httpOnly 'csrf_token' de este origen (ver
# _set_session_cookies en routers/auth.py) — algo que un sitio cross-origin
# no puede hacer por la same-origin policy del navegador, aunque logre
# disparar el request.
CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Endpoints exentos: login/register/verify-email/resend-verification son
# pre-sesión (todavía no existe cookie csrf_token que comparar), y
# /auth/refresh no necesita el chequeo porque no toma ningún parámetro cuya
# integridad dependa de CSRF — su única defensa relevante (que la cookie de
# refresh no viaje cross-site) ya la da SameSite=Strict.
CSRF_EXEMPT_PREFIXES = (
    "/auth/login", "/auth/register", "/auth/verify-email",
    "/auth/resend-verification", "/auth/refresh",
)


@app.middleware("http")
async def csrf_protect_middleware(request: Request, call_next):
    if request.method not in CSRF_SAFE_METHODS:
        path = request.url.path
        if not any(path.startswith(p) for p in CSRF_EXEMPT_PREFIXES):
            # Solo aplica si hay una sesión de cookie en juego; requests sin
            # cookie de acceso ya van a fallar la autenticación normal y no
            # necesitan este chequeo extra.
            if request.cookies.get("access_token"):
                cookie_csrf = request.cookies.get("csrf_token")
                header_csrf = request.headers.get("x-csrf-token")
                if not cookie_csrf or not header_csrf or not secrets.compare_digest(cookie_csrf, header_csrf):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Token CSRF inválido o ausente."},
                    )
    return await call_next(request)

# ─── Rate limiting básico (in-memory sliding window por IP) ──────────────────
# Nota: para producción multi-instancia esto debe vivir en Redis, no en memoria.

_RATE_LIMITS = {"/auth/login": (10, 60), "/auth/register": (5, 60), "/chat": (60, 60)}
_request_log: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    limit_conf = next((v for k, v in _RATE_LIMITS.items() if path.startswith(k)), None)
    if limit_conf:
        max_requests, window_s = limit_conf
        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{path.split('/')[1] if len(path.split('/')) > 1 else path}"
        now = time.time()
        log = _request_log[key]
        while log and now - log[0] > window_s:
            log.popleft()
        if len(log) >= max_requests:
            return JSONResponse(
                status_code=429,
                content={"detail": "Demasiadas solicitudes. Intenta de nuevo en unos segundos."},
            )
        log.append(now)
    return await call_next(request)


# ─── Routers ─────────────────────────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(profile_router)
app.include_router(workouts_router)
app.include_router(nutrition_router)
app.include_router(progress_router)
app.include_router(observability_router)

# ─── Startup ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    init_db()
    print("🚀 FitMind AI Backend iniciado correctamente.")
    if IS_PROD:
        print("   Entorno: production (docs/redoc/openapi deshabilitados)")
    else:
        print("   Entorno: development — Docs: http://localhost:8000/docs")


# ─── Global exception handler ─────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    # El detalle completo se loguea del lado del servidor (útil para debug),
    # pero NUNCA se expone al cliente: evita fugas de rutas internas,
    # queries SQL, nombres de variables, etc. (info disclosure).
    print(f"[ERROR] {request.method} {request.url.path} -> {exc!r}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno del servidor. Intenta de nuevo más tarde."},
    )


# ─── Health check ─────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
def health():
    # Sin versión, sin nombre de modelo, sin estado de BD: solo confirma
    # que el proceso está vivo (punto 20 del checklist).
    return {"status": "ok"}


@app.get("/", tags=["Health"])
def root():
    return {"message": "FitMind AI API"}
