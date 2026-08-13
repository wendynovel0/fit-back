"""
Router de autenticación — FitMind.
Usa hashlib (SHA-256 + salt) en vez de passlib/bcrypt
para compatibilidad con Python 3.14.
"""

import os
import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request, Response
from pydantic import BaseModel, field_validator
from jose import JWTError, jwt
from dotenv import load_dotenv

from database.connection import get_connection
from services.email_service import send_verification_code_email

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY or SECRET_KEY in ("fitmind-secret", "changeme"):
    raise RuntimeError(
        "SECRET_KEY no está configurado (o usa un valor por defecto inseguro). "
        "Define una variable de entorno SECRET_KEY con "
        "`python3 -c \"import secrets;print(secrets.token_hex(32))\"`."
    )
ALGORITHM = os.getenv("ALGORITHM", "HS256")
# OTC-AUTH-04: el default bajó de 10080 min (7 días) a 60 min. Un access
# token de 7 días sin jti/revocación era una ventana enorme ante robo de
# token. Sesiones largas se sostienen con el refresh token (que sí rota y sí
# se puede revocar), no alargando el access token.
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
JWT_ISSUER = os.getenv("JWT_ISSUER", "fitmind-api")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "fitmind-app")

# ─── Cookies de sesión (migración desde localStorage) ─────────────────────────
#
# Antes, access_token/refresh_token viajaban en el body JSON y el frontend
# los guardaba en localStorage — cualquier XSS en la SPA (una dependencia
# comprometida, un campo mal sanitizado, etc.) podía leerlos con
# `localStorage.getItem(...)` y exfiltrar la sesión completa. Ahora ambos
# tokens se mandan como cookies httpOnly: el JS del navegador no puede
# leerlas ni escribirlas bajo ningún escenario de XSS, solo el navegador las
# adjunta automáticamente en cada request al mismo origen.
#
# Cambiar de "Authorization: Bearer" (header, que un atacante cross-site NO
# puede forzar a mandar) a cookies (que el navegador SÍ adjunta solo) abre
# la puerta a CSRF. Se cierra con dos capas:
#   1. SameSite=Strict en ambas cookies de sesión: el navegador directamente
#      no las manda en requests que se originan desde otro sitio.
#   2. Patrón de "doble cookie" para CSRF: se manda una tercera cookie
#      (csrf_token, NO httpOnly) con un valor aleatorio; el frontend la lee
#      y la reenvía como header X-CSRF-Token en cada mutación. Un atacante
#      cross-site no puede leer cookies de este origen, así que no puede
#      construir ese header aunque logre disparar el request (ver
#      csrf_protect_middleware en main.py).
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
# COOKIE_SECURE debe ser True en cualquier despliegue real (el navegador
# rechaza mandar cookies "Secure" por HTTP plano). Se ata a ENVIRONMENT por
# default, con override explícito por si el proxy TLS termina antes que la
# app vea "production" (mismo patrón ya usado para HSTS en main.py).
COOKIE_SECURE = os.getenv("COOKIE_SECURE", str(ENVIRONMENT == "production")).lower() == "true"
ACCESS_COOKIE_NAME = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"
CSRF_COOKIE_NAME = "csrf_token"

router = APIRouter(prefix="/auth", tags=["Auth"])
logger = logging.getLogger("fitmind.auth")


# ─── Schemas ──────────────────────────────────────────────────────────────────

MIN_PASSWORD_LENGTH = 6


class RegisterRequest(BaseModel):
    nombre: str
    email: str
    password: str
    edad: Optional[int] = None
    peso_kg: Optional[float] = None
    altura_cm: Optional[float] = None
    objetivo: Optional[str] = "ganar músculo"
    nivel: Optional[str] = "principiante"

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        # No permitir contraseñas vacías o compuestas solo de espacios en blanco
        # (ej. "      "), que antes solo se validaban por longitud en el frontend.
        if not v or not v.strip():
            raise ValueError(
                "La contraseña no puede estar vacía ni contener solo espacios en blanco."
            )
        if len(v.strip()) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres "
                "(sin contar espacios al inicio o al final)."
            )
        return v

    @field_validator("nombre")
    @classmethod
    def validate_nombre(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("El nombre no puede estar vacío.")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    """Ya no incluye tokens en el body — viajan como cookies httpOnly
    (ver _set_session_cookies). El body solo confirma el usuario logueado."""
    user: dict


# ─── Password helpers (sin passlib/bcrypt — evita romper en Python 3.14) ──────
#
# passlib/bcrypt dependen de extensiones C que históricamente rompen en
# versiones muy nuevas de Python. En vez de eso usamos PBKDF2-HMAC-SHA256 con
# 210,000 iteraciones (recomendación OWASP 2023+), que es puro stdlib, sigue
# siendo una KDF lenta apropiada para contraseñas (a diferencia de un solo
# SHA-256), y no requiere ninguna dependencia binaria.

PBKDF2_ITERATIONS = 210_000


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 con salt aleatorio. Formato: iteraciones$salt$hash"""
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS).hex()
    return f"{PBKDF2_ITERATIONS}${salt}${h}"


def verify_password(plain: str, stored: str) -> bool:
    try:
        # Compatibilidad retro con el formato legado 'salt$hash' (SHA-256 simple)
        parts = stored.split("$")
        if len(parts) == 2:
            salt, h = parts
            return hashlib.sha256((salt + plain).encode()).hexdigest() == h
        iterations, salt, h = parts
        computed = hashlib.pbkdf2_hmac(
            "sha256", plain.encode(), bytes.fromhex(salt), int(iterations)
        ).hex()
        return computed == h
    except Exception:
        return False


# ─── Verificación de email (OTC-AUTH-01) ───────────────────────────────────────
#
# Antes, /register devolvía tokens de sesión completos y usables de
# inmediato — cualquiera podía crear cuentas ilimitadas con emails falsos o
# ajenos sin ninguna prueba de que el email es real y le pertenece.
# Ahora: /register crea la cuenta SIN sesión utilizable, envía por email un
# código numérico de un solo uso, y solo tras /auth/verify-email (con ese
# código) el usuario puede hacer login.
#
# El envío real va por SMTP (services/email_service.py — probado contra
# MailerSend). Si SMTP no está configurado, el email service loguea el
# contenido en vez de fallar duro, para poder seguir probando en local.

EMAIL_VERIFICATION_EXPIRE_MINUTES = 15
EMAIL_VERIFICATION_MAX_ATTEMPTS = 5
# Cooldown mínimo entre reenvíos de código para el mismo usuario, para no
# poder hacer spamear la bandeja de entrada de un tercero desde /register o
# /resend-verification.
EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS = 45


def _generate_code() -> str:
    """Código numérico de 6 dígitos (con ceros a la izquierda), legible en un
    email y fácil de tipear a mano."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _issue_verification_code(user_id: int) -> Optional[str]:
    """Invalida cualquier código previo sin usar del usuario y emite uno
    nuevo. Devuelve el código en claro (para enviarlo por email), o None si
    hay un envío muy reciente y todavía no corresponde emitir otro
    (cooldown anti-spam)."""
    conn = get_connection()

    last = conn.execute(
        "SELECT creado_en FROM email_verification_tokens "
        "WHERE usuario_id = ? ORDER BY creado_en DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if last:
        last_dt = datetime.fromisoformat(last["creado_en"])
        elapsed = (datetime.utcnow() - last_dt).total_seconds()
        if elapsed < EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS:
            conn.close()
            return None

    # Invalidar códigos anteriores no usados: solo el más reciente debe ser válido.
    conn.execute(
        "UPDATE email_verification_tokens SET usado = 1 WHERE usuario_id = ? AND usado = 0",
        (user_id,),
    )

    raw_code = _generate_code()
    expira_en = (datetime.utcnow() + timedelta(minutes=EMAIL_VERIFICATION_EXPIRE_MINUTES)).isoformat()
    conn.execute(
        "INSERT INTO email_verification_tokens (usuario_id, token_hash, expira_en) VALUES (?,?,?)",
        (user_id, _hash_token(raw_code), expira_en),
    )
    conn.commit()
    conn.close()
    return raw_code


def send_verification_email(email: str, nombre: str, code: str) -> None:
    ok = send_verification_code_email(email, nombre, code, EMAIL_VERIFICATION_EXPIRE_MINUTES)
    if not ok:
        # No rompemos el flujo de registro por un fallo de envío (la cuenta
        # ya existe): queda logueado server-side y el usuario puede pedir
        # un reenvío desde /auth/resend-verification.
        logger.warning("No se pudo enviar el código de verificación a %s", email)


class VerifyEmailRequest(BaseModel):
    email: str
    code: str

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit() or len(v) != 6:
            raise ValueError("El código debe tener 6 dígitos.")
        return v


class ResendVerificationRequest(BaseModel):
    email: str


# ─── Refresh tokens con rotación (Sección 7 del CTO review) ───────────────────

REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))


def _set_session_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """Setea las 3 cookies de sesión (access, refresh, csrf) en la respuesta.

    refresh_token se restringe a path='/auth' (el único prefijo que lo
    necesita: /auth/refresh y /auth/logout) para minimizar dónde viaja.
    access_token y csrf_token van en path='/' porque cualquier endpoint de
    la API los necesita.
    """
    response.set_cookie(
        key=ACCESS_COOKIE_NAME, value=access_token, httponly=True, secure=COOKIE_SECURE,
        samesite="strict", path="/", max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    response.set_cookie(
        key=REFRESH_COOKIE_NAME, value=refresh_token, httponly=True, secure=COOKIE_SECURE,
        samesite="strict", path="/auth", max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400,
    )
    # csrf_token NO es httpOnly a propósito: el frontend necesita leerlo
    # (document.cookie) para reenviarlo como header en cada mutación. No es
    # secreto en sí mismo — su única función es demostrar que quien arma el
    # request puede leer cookies de este origen, cosa que un sitio atacante
    # no puede hacer (same-origin policy).
    response.set_cookie(
        key=CSRF_COOKIE_NAME, value=secrets.token_urlsafe(32), httponly=False, secure=COOKIE_SECURE,
        samesite="strict", path="/", max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE_NAME, path="/")
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/auth")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_refresh_token(user_id: int) -> str:
    """Crea un refresh token de un solo uso y guarda solo su hash en DB."""
    raw_token = secrets.token_urlsafe(48)
    expira_en = (datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
    conn = get_connection()
    conn.execute(
        "INSERT INTO refresh_tokens (usuario_id, token_hash, expira_en) VALUES (?,?,?)",
        (user_id, _hash_token(raw_token), expira_en),
    )
    conn.commit()
    conn.close()
    return raw_token


def rotate_refresh_token(raw_token: str) -> Optional[tuple[int, str]]:
    """Valida un refresh token, lo revoca (rotación) y emite uno nuevo. Retorna (user_id, nuevo_token) o None."""
    token_hash = _hash_token(raw_token)
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM refresh_tokens WHERE token_hash = ? AND revocado = 0",
        (token_hash,),
    ).fetchone()
    if not row:
        conn.close()
        return None
    if row["expira_en"] and datetime.fromisoformat(row["expira_en"]) < datetime.utcnow():
        conn.close()
        return None
    conn.execute("UPDATE refresh_tokens SET revocado = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    new_token = issue_refresh_token(row["usuario_id"])
    return row["usuario_id"], new_token


def revoke_refresh_token(raw_token: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE refresh_tokens SET revocado = 1 WHERE token_hash = ?",
        (_hash_token(raw_token),),
    )
    conn.commit()
    conn.close()


# ─── JWT helpers ──────────────────────────────────────────────────────────────
#
# OTC-AUTH-04: el payload mínimo anterior ({sub, email, exp}) no tenía forma
# de invalidar un token individual antes de su expiración, ni "iss"/"aud"
# para confirmar que el token fue emitido por esta API y para este cliente.
# Se agrega:
#   - iat: momento de emisión (estándar, útil para auditoría/debug)
#   - jti: identificador único del token → permite revocación puntual
#   - iss/aud: atan el token a este servicio, evita reutilización cruzada

def create_access_token(data: dict) -> str:
    payload = data.copy()
    if "sub" in payload:
        payload["sub"] = str(payload["sub"])
    now = datetime.utcnow()
    payload["iat"] = now
    payload["exp"] = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload["jti"] = str(uuid.uuid4())
    payload["iss"] = JWT_ISSUER
    payload["aud"] = JWT_AUDIENCE
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _is_jti_revoked(jti: Optional[str]) -> bool:
    if not jti:
        return False
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM revoked_access_jti WHERE jti = ?", (jti,)
    ).fetchone()
    conn.close()
    return row is not None


def revoke_access_token(token: str) -> None:
    """Revoca el access token actual por su jti (usado en /logout)."""
    try:
        payload = jwt.decode(
            token, SECRET_KEY, algorithms=[ALGORITHM],
            audience=JWT_AUDIENCE, issuer=JWT_ISSUER,
            options={"verify_exp": False},
        )
    except JWTError:
        return
    jti = payload.get("jti")
    exp = payload.get("exp")
    if not jti:
        return
    expira_en = (
        datetime.utcfromtimestamp(exp).isoformat() if exp
        else (datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)).isoformat()
    )
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO revoked_access_jti (jti, expira_en) VALUES (?, ?)",
        (jti, expira_en),
    )
    # Housekeeping: purgar jtis ya expirados (ya no hace falta seguir
    # bloqueándolos, el propio JWT los rechaza por 'exp').
    conn.execute("DELETE FROM revoked_access_jti WHERE expira_en < ?", (datetime.utcnow().isoformat(),))
    conn.commit()
    conn.close()


def get_current_user(request: Request) -> dict:
    # El access token ahora viaja SIEMPRE como cookie httpOnly (ver
    # _set_session_cookies) — nunca en un header Authorization que el JS de
    # la SPA tenga que manejar. Si no hay cookie, no hay sesión.
    token = request.cookies.get(ACCESS_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="No autenticado.")
    try:
        payload = jwt.decode(
            token, SECRET_KEY, algorithms=[ALGORITHM],
            audience=JWT_AUDIENCE, issuer=JWT_ISSUER,
        )
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Token inválido.")
        user_id = int(user_id)
        if _is_jti_revoked(payload.get("jti")):
            raise HTTPException(status_code=401, detail="Token revocado. Inicia sesión de nuevo.")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token expirado o inválido.")

    conn = get_connection()
    row = conn.execute("SELECT * FROM usuarios WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="Usuario no encontrado.")
    return dict(row)


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/register", status_code=201)
def register(body: RegisterRequest):
    """
    SEGURIDAD (COVERAGE-GAP-R03 / R05 del pentest): antes, este endpoint
    devolvía 409 "El email ya está registrado" cuando el email existía —
    un oracle booleano perfecto para enumerar cuentas registradas (la misma
    clase de hallazgo que ya se había cerrado en /auth/resend-verification,
    pero que acá seguía abierta). Ahora la respuesta es IDÉNTICA exista o
    no la cuenta: siempre 201 con el mismo mensaje genérico. La diferencia
    de comportamiento queda solo del lado del servidor:
      - Email nuevo → crea la cuenta y manda el código de verificación.
      - Email ya registrado pero NO verificado → NO se toca nombre/password
        (evita que alguien "re-registre" y pise la contraseña de otro),
        solo se reenvía un código nuevo (sujeto al mismo cooldown anti-spam
        de _issue_verification_code).
      - Email ya registrado Y verificado → no se hace nada; no se reenvía
        ningún código a una cuenta ya activa.
    Nota: sigue existiendo un canal lateral de timing (la rama con INSERT
    tarda algo más que la que solo hace SELECT); se acepta como riesgo
    residual de baja severidad dado el esfuerzo de explotarlo a través de
    un túnel HTTP con latencia variable.
    """
    conn = get_connection()
    existing = conn.execute(
        "SELECT id, nombre, email_verificado FROM usuarios WHERE email = ?", (body.email,)
    ).fetchone()

    generic_response = {
        "message": "Si el email es válido, te enviamos un código de 6 dígitos para verificar tu cuenta antes de iniciar sesión.",
        "email": body.email,
    }

    if existing:
        conn.close()
        if not existing["email_verificado"]:
            verify_code = _issue_verification_code(existing["id"])
            if verify_code:
                send_verification_email(body.email, existing["nombre"], verify_code)
        return generic_response

    password_hash = hash_password(body.password)
    cur = conn.execute(
        """
        INSERT INTO usuarios (nombre, email, password_hash, edad, peso_kg, altura_cm, objetivo, nivel, email_verificado)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (body.nombre, body.email, password_hash, body.edad,
         body.peso_kg, body.altura_cm, body.objetivo, body.nivel),
    )
    user_id = cur.lastrowid
    conn.commit()
    conn.close()

    # OTC-AUTH-01: ya NO se emite un access_token utilizable aquí. La cuenta
    # existe pero no puede iniciar sesión hasta confirmar el email con el
    # código de 6 dígitos que se envía a continuación.
    verify_code = _issue_verification_code(user_id)
    if verify_code:
        send_verification_email(body.email, body.nombre, verify_code)

    return generic_response


@router.post("/verify-email", response_model=UserResponse)
def verify_email(body: VerifyEmailRequest, response: Response):
    conn = get_connection()
    user_row = conn.execute("SELECT * FROM usuarios WHERE email = ?", (body.email,)).fetchone()
    if not user_row:
        conn.close()
        # Mismo mensaje genérico que si el código fuera incorrecto: no
        # confirmamos ni negamos existencia de la cuenta por este endpoint.
        raise HTTPException(status_code=400, detail="Código inválido o expirado.")

    if user_row["email_verificado"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Este email ya fue verificado. Inicia sesión.")

    row = conn.execute(
        "SELECT * FROM email_verification_tokens WHERE usuario_id = ? AND usado = 0 "
        "ORDER BY creado_en DESC LIMIT 1",
        (user_row["id"],),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=400, detail="Código inválido o expirado. Solicita uno nuevo.")

    if datetime.fromisoformat(row["expira_en"]) < datetime.utcnow():
        conn.execute("UPDATE email_verification_tokens SET usado = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=400, detail="Código expirado. Solicita uno nuevo.")

    if row["intentos"] >= EMAIL_VERIFICATION_MAX_ATTEMPTS:
        conn.execute("UPDATE email_verification_tokens SET usado = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=400, detail="Demasiados intentos. Solicita un nuevo código.")

    if not secrets.compare_digest(row["token_hash"], _hash_token(body.code)):
        conn.execute(
            "UPDATE email_verification_tokens SET intentos = intentos + 1 WHERE id = ?", (row["id"],)
        )
        conn.commit()
        intentos_restantes = EMAIL_VERIFICATION_MAX_ATTEMPTS - (row["intentos"] + 1)
        conn.close()
        if intentos_restantes <= 0:
            raise HTTPException(status_code=400, detail="Demasiados intentos. Solicita un nuevo código.")
        raise HTTPException(status_code=400, detail="Código incorrecto.")

    conn.execute("UPDATE email_verification_tokens SET usado = 1 WHERE id = ?", (row["id"],))
    conn.execute("UPDATE usuarios SET email_verificado = 1 WHERE id = ?", (user_row["id"],))
    conn.commit()

    fresh_user_row = conn.execute("SELECT * FROM usuarios WHERE id = ?", (user_row["id"],)).fetchone()
    conn.close()
    user = dict(fresh_user_row)
    user.pop("password_hash", None)

    token = create_access_token({"sub": user["id"], "email": user["email"]})
    refresh_token = issue_refresh_token(user["id"])
    _set_session_cookies(response, token, refresh_token)
    return UserResponse(user=user)


@router.post("/resend-verification")
def resend_verification(body: ResendVerificationRequest):
    conn = get_connection()
    row = conn.execute("SELECT * FROM usuarios WHERE email = ?", (body.email,)).fetchone()
    conn.close()
    # Respuesta idéntica exista o no la cuenta / esté o no ya verificada:
    # evita que este endpoint sirva como oracle para enumerar emails
    # registrados (mismo principio que cerró COVERAGE-GAP-R03/R04/R05).
    generic_response = {
        "message": "Si el email está registrado y pendiente de verificación, se envió un nuevo código.",
    }
    if not row or row["email_verificado"]:
        return generic_response
    verify_code = _issue_verification_code(row["id"])
    if verify_code:
        send_verification_email(body.email, row["nombre"], verify_code)
    return generic_response


@router.post("/login", response_model=UserResponse)
def login(body: LoginRequest, response: Response):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM usuarios WHERE email = ?", (body.email,)
    ).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas.")

    user = dict(row)
    if not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas.")

    if not user.get("email_verificado"):
        raise HTTPException(
            status_code=403,
            detail="Debes verificar tu email antes de iniciar sesión. Revisa tu bandeja de entrada.",
        )

    user.pop("password_hash", None)
    token = create_access_token({"sub": user["id"], "email": user["email"]})
    refresh_token = issue_refresh_token(user["id"])
    _set_session_cookies(response, token, refresh_token)
    return UserResponse(user=user)


@router.post("/refresh", response_model=UserResponse)
def refresh(request: Request, response: Response):
    """Rota el refresh token (de un solo uso) y emite un nuevo access token.

    El refresh token ya NO viaja en el body: el frontend nunca lo toca, solo
    llama a este endpoint y el navegador manda la cookie httpOnly
    'refresh_token' (path='/auth') sola. Esto es justamente el punto de
    migrar a cookies: ni un XSS que ejecute JS arbitrario en la página puede
    leer este valor para exfiltrarlo.
    """
    raw_refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw_refresh_token:
        raise HTTPException(status_code=401, detail="No hay sesión activa.")

    result = rotate_refresh_token(raw_refresh_token)
    if not result:
        _clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="Refresh token inválido, expirado o ya usado.")
    user_id, new_refresh_token = result

    conn = get_connection()
    row = conn.execute("SELECT * FROM usuarios WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if not row:
        _clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="Usuario no encontrado.")
    user = dict(row)
    user.pop("password_hash", None)

    token = create_access_token({"sub": user_id, "email": user["email"]})
    _set_session_cookies(response, token, new_refresh_token)
    return UserResponse(user=user)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    current_user: dict = Depends(get_current_user),
):
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if refresh_token:
        revoke_refresh_token(refresh_token)
    # OTC-AUTH-04: además de rotar/revocar el refresh token, revoca el jti
    # del access token actual — si no, seguiría siendo válido hasta su exp.
    access_token = request.cookies.get(ACCESS_COOKIE_NAME)
    if access_token:
        revoke_access_token(access_token)
    _clear_session_cookies(response)
    return {"message": "Sesión cerrada."}


@router.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    user = dict(current_user)
    user.pop("password_hash", None)
    return user


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Dependencia para endpoints restringidos a administradores.

    Uso: current_user: dict = Depends(require_admin)
    """
    if not current_user.get("es_admin"):
        raise HTTPException(status_code=403, detail="Requiere permisos de administrador.")
    return current_user