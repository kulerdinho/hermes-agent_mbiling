"""Envoi de l'email de test de Management, avec les réglages SMTP enregistrés."""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any


def envoyer_essai(smtp: dict[str, Any], mot_de_passe: str | None, destinataire: str) -> None:
    message = EmailMessage()
    message["From"] = formataddr((smtp.get("nom_affiche") or "Mbiling", smtp["expediteur"]))
    message["To"] = destinataire
    message["Subject"] = "Mbiling — email de test"
    message.set_content(
        "Bonjour,\n\nCet email confirme que Mbiling peut envoyer ses emails (vérification d'adresse, "
        "mot de passe oublié) avec les réglages enregistrés dans Management.\n\n— Mbiling\n")
    port = int(smtp["port"])
    contexte = ssl.create_default_context()
    if port == 465:
        connexion = smtplib.SMTP_SSL(smtp["serveur"], port, timeout=20, context=contexte)
    else:
        connexion = smtplib.SMTP(smtp["serveur"], port, timeout=20)
    with connexion:
        if port != 465 and smtp.get("starttls", True):
            connexion.starttls(context=contexte)
        if smtp.get("identifiant"):
            connexion.login(smtp["identifiant"], mot_de_passe or "")
        connexion.send_message(message)
