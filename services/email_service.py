"""
Servicio de envío de emails — FitMind.

Envía correo real por SMTP (probado contra MailerSend, pero funciona con
cualquier proveedor SMTP estándar: SES, Postmark, Resend, Gmail, etc.).

Diseño:
  - No lanza excepciones hacia el caller: devuelve True/False y loguea el
    detalle. Un fallo de envío NO debe tumbar el endpoint de registro (la
    cuenta ya se creó en DB); el usuario siempre puede pedir un reenvío
    desde /auth/resend-verification.
  - Todo lo sensible (host/usuario/password) sale de variables de entorno,
    nunca hardcodeado en el código.
"""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("fitmind.email")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")

# El remitente NO tiene por qué ser igual al usuario SMTP (MailerSend, SES,
# etc. separan "credencial para autenticar" de "dirección que aparece en
# el From:"). Si no se define, se usa el username SMTP como fallback.
EMAIL_FROM_ADDRESS = os.getenv("EMAIL_FROM_ADDRESS", SMTP_USERNAME)
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "FitMind")


def _is_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD and EMAIL_FROM_ADDRESS)


def send_email(to_email: str, subject: str, html_body: str, text_body: str) -> bool:
    """Envía un email por SMTP. Devuelve True si se entregó al servidor SMTP,
    False si falló (revisa logs para el detalle)."""

    if not _is_configured():
        # Fallback de desarrollo: si no hay SMTP configurado, no rompemos el
        # flujo — logueamos el contenido para poder probar sin credenciales.
        logger.warning(
            "SMTP no configurado (faltan SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD/"
            "EMAIL_FROM_ADDRESS). No se envió email real a %s. Cuerpo:\n%s",
            to_email, text_body,
        )
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>"
    msg["To"] = to_email
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    try:
        if SMTP_PORT == 465:
            # SMTPS implícito (TLS desde el handshake inicial)
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=15) as server:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            # 587 (o 2525) → STARTTLS explícito, lo que espera MailerSend
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.ehlo()
                if SMTP_USE_TLS:
                    context = ssl.create_default_context()
                    server.starttls(context=context)
                    server.ehlo()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(msg)
        logger.info("Email enviado a %s (asunto: %s)", to_email, subject)
        return True
    except Exception:
        # Nunca propagamos el error de SMTP (ni su texto) hacia el cliente:
        # mismo principio que P0-05 del reporte — detalle interno solo en logs.
        logger.exception("Falló el envío de email a %s", to_email)
        return False


def send_verification_code_email(to_email: str, nombre: str, code: str, expire_minutes: int) -> bool:
    subject = "Tu código de verificación de FitMind"

    text_body = (
        f"Hola {nombre},\n\n"
        f"Tu código de verificación de FitMind es: {code}\n\n"
        f"Ingresá este código en la app para confirmar tu email y poder iniciar sesión.\n"
        f"El código vence en {expire_minutes} minutos.\n\n"
        f"Si no creaste una cuenta en FitMind, podés ignorar este mensaje.\n"
    )

    html_body = f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:0;background-color:#f4f4f5;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f5;padding:32px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:12px;overflow:hidden;">
            <tr>
              <td style="background-color:#111111;padding:24px 32px;">
                <span style="color:#ffffff;font-size:20px;font-weight:700;">FitMind</span>
              </td>
            </tr>
            <tr>
              <td style="padding:32px;">
                <p style="margin:0 0 8px;font-size:15px;color:#111111;">Hola {nombre},</p>
                <p style="margin:0 0 24px;font-size:15px;color:#444444;line-height:1.5;">
                  Usá este código para verificar tu email y activar tu cuenta de FitMind:
                </p>
                <div style="text-align:center;margin:0 0 24px;">
                  <span style="display:inline-block;font-size:32px;font-weight:700;letter-spacing:8px;color:#111111;background-color:#f4f4f5;padding:16px 24px;border-radius:8px;">
                    {code}
                  </span>
                </div>
                <p style="margin:0 0 4px;font-size:13px;color:#888888;">
                  Este código vence en {expire_minutes} minutos.
                </p>
                <p style="margin:0;font-size:13px;color:#888888;">
                  Si no creaste una cuenta en FitMind, podés ignorar este email.
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""

    return send_email(to_email, subject, html_body, text_body)
