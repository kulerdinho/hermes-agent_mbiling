#!/usr/bin/env bash
# Applique au royaume « mbiling » les réglages qui dépendent du serveur (lancé par installer.sh,
# peut être relancé) : adresse de retour et secret de la cellule, inscription ouverte ou fermée,
# envoi des emails. Le royaume lui-même (royaume-mbiling.json) n'est importé qu'à sa création ;
# ce script garde ensuite ces réglages alignés sur serveur/.env.
set -euo pipefail

dossier="$(cd "$(dirname "$0")/.." && pwd)"
set -a
. "$dossier/.env"
set +a

compose() { sudo docker compose --project-directory "$dossier" --profile comptes "$@"; }
# kcadm s'exécute dans le conteneur des comptes ; sa configuration (jeton d'administration)
# reste dans le conteneur, jamais sur le disque du serveur.
kc() { compose exec -T comptes /opt/keycloak/bin/kcadm.sh "$@" --config /tmp/kcadm.config; }

# Attendre que le serveur de comptes réponde (premier démarrage : import du royaume, ~1 min).
for essai in $(seq 1 60); do
	if compose exec -T -e KC_CLI_PASSWORD="$MBILING_COMPTES_ADMIN_MOT_DE_PASSE" comptes \
		/opt/keycloak/bin/kcadm.sh config credentials --config /tmp/kcadm.config \
		--server "http://localhost:8080/comptes" --realm master --user admin >/dev/null 2>&1; then
		break
	fi
	if [ "$essai" -eq 60 ]; then
		echo "Le serveur de comptes ne répond pas : voir « sudo docker compose logs comptes »." >&2
		exit 1
	fi
	sleep 5
done

# La cellule : son adresse de retour publique et son secret (le même que dans sa configuration).
id_cellule="$(kc get clients -r mbiling -q clientId=cellule --fields id --format csv --noquotes | tr -d '\r')"
kc update "clients/$id_cellule" -r mbiling \
	-s "secret=$MBILING_COMPTES_SECRET_CELLULE" \
	-s "redirectUris=[\"https://$MBILING_DOMAINE/auth/callback\"]" \
	-s "webOrigins=[\"https://$MBILING_DOMAINE\"]"

# Inscription libre : fermée tant qu'une seule cellule de test sert tous les comptes (sinon, tout
# inscrit utiliserait la cellule de test et sa clé d'IA). Les comptes de test sont créés à la main.
inscription=false
[ "${MBILING_COMPTES_INSCRIPTION_OUVERTE:-0}" = "1" ] && inscription=true
kc update realms/mbiling -s "registrationAllowed=$inscription"

# Emails (vérification de l'adresse, mot de passe oublié) : seulement si un serveur d'envoi est réglé.
# Sans lui, la vérification de l'adresse est coupée, sinon personne ne pourrait finir son inscription.
if [ -n "${MBILING_EMAIL_SERVEUR:-}" ]; then
	kc update realms/mbiling \
		-s verifyEmail=true \
		-s "smtpServer.host=$MBILING_EMAIL_SERVEUR" \
		-s "smtpServer.port=${MBILING_EMAIL_PORT:-587}" \
		-s "smtpServer.from=$MBILING_EMAIL_EXPEDITEUR" \
		-s "smtpServer.fromDisplayName=Mbiling" \
		-s "smtpServer.starttls=true" \
		-s "smtpServer.auth=true" \
		-s "smtpServer.user=$MBILING_EMAIL_IDENTIFIANT" \
		-s "smtpServer.password=$MBILING_EMAIL_MOT_DE_PASSE"
else
	kc update realms/mbiling -s verifyEmail=false
	echo "Aucun serveur d'emails (MBILING_EMAIL_SERVEUR) : ni vérification d'adresse ni mot de passe oublié."
fi

echo "Serveur de comptes configuré : https://$MBILING_DOMAINE/comptes/realms/mbiling"
