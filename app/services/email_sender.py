import os
import smtplib
from email.message import EmailMessage


def _first_non_empty(*names, default=""):
    for name in names:
        value = os.getenv(name, "")
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _bool_from_env(*names, default=False):
    for name in names:
        value = os.getenv(name)
        if value is not None:
            return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
    return default


def resolve_smtp_settings():
    host = _first_non_empty("SMTP_HOST", "SGQ_SMTP_HOST")
    port = _first_non_empty("SMTP_PORT", "SGQ_SMTP_PORT", default="587")
    username = _first_non_empty("SMTP_USERNAME", "SGQ_SMTP_USER")
    password = _first_non_empty("SMTP_PASSWORD", "SGQ_SMTP_PASSWORD")
    sender = _first_non_empty("SMTP_FROM", "SGQ_FROM_EMAIL", default="noreply@empresa.com")
    use_tls = _bool_from_env("SMTP_USE_TLS", "SGQ_SMTP_TLS", default=False)
    use_ssl = _bool_from_env("SMTP_SSL", "SGQ_SMTP_SSL", default=True)

    try:
        port_value = int(port)
    except (TypeError, ValueError):
        port_value = 587

    return {
        "host": host,
        "port": port_value,
        "username": username,
        "password": password,
        "from_email": sender,
        "use_tls": use_tls,
        "use_ssl": use_ssl,
    }


def send_signature_email(
    recipient_email: str,
    attachment_path: str,
    employee_name: str = "Colaborador",
    whatsapp_card_path: str | None = None,
):
    settings = resolve_smtp_settings()
    smtp_host = settings["host"]

    if not smtp_host:
        return {"status": "simulated", "message": "E-mail simulado; SMTP não configurado."}
    if settings["username"] and not settings["password"]:
        return {
            "status": "error",
            "code": "smtp_configuration",
            "message": "SMTP_USERNAME está definido, mas SMTP_PASSWORD está vazio.",
        }

    message = EmailMessage()
    message["From"] = settings["from_email"]
    message["To"] = recipient_email
    message["Subject"] = "Sua assinatura de e-mail — Dinho Distribuidora"
    message.set_content(
        "Olá!\n\nSua assinatura foi gerada com sucesso.\n\n"
        "Anexamos a assinatura pronta para uso e a imagem personalizada para WhatsApp.\n"
        "Instrução rápida: salve a assinatura e configure-a no seu cliente de e-mail."
    )
    with open(attachment_path, "rb") as file:
        message.add_attachment(file.read(), maintype="image", subtype="png", filename="assinatura.png")
    if whatsapp_card_path:
        with open(whatsapp_card_path, "rb") as file:
            message.add_attachment(file.read(), maintype="image", subtype="png", filename="imagem-whatsapp.png")

    try:
        if settings["use_ssl"]:
            smtp = smtplib.SMTP_SSL(smtp_host, settings["port"])
        else:
            smtp = smtplib.SMTP(smtp_host, settings["port"])

        if settings["use_tls"] and not settings["use_ssl"]:
            smtp.starttls()

        if settings["username"]:
            smtp.login(settings["username"], settings["password"])

        smtp.send_message(message)
        smtp.quit()
    except smtplib.SMTPAuthenticationError as exc:
        return {
            "status": "error",
            "code": "smtp_authentication",
            "message": f"Falha de autenticação no servidor SMTP ({exc.smtp_code}).",
        }
    except (smtplib.SMTPException, OSError) as exc:
        return {"status": "error", "code": "smtp_connection", "message": f"Falha ao enviar e-mail: {exc}"}

    return {"status": "sent", "message": "E-mail enviado com sucesso."}
