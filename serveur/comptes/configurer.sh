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
# Déconnexion poussée : quand une session se ferme (« Mon compte », Management), le serveur de comptes
# prévient aussitôt la cellule par le réseau interne, qui refuse alors les jetons de cette session.
id_cellule="$(kc get clients -r mbiling -q clientId=cellule --fields id --format csv --noquotes | tr -d '\r')"
kc update "clients/$id_cellule" -r mbiling \
	-s "secret=$MBILING_COMPTES_SECRET_CELLULE" \
	-s "redirectUris=[\"https://$MBILING_DOMAINE/auth/callback\"]" \
	-s "webOrigins=[\"https://$MBILING_DOMAINE\"]" \
	-s "frontchannelLogout=false" \
	-s "attributes.\"backchannel.logout.url\"=http://cellule:9119/auth/backchannel-logout" \
	-s "attributes.\"backchannel.logout.session.required\"=true"

# Inscription libre : fermée tant qu'une seule cellule de test sert tous les comptes (sinon, tout
# inscrit utiliserait la cellule de test et sa clé d'IA). Les comptes de test sont créés à la main.
inscription=false
[ "${MBILING_COMPTES_INSCRIPTION_OUVERTE:-0}" = "1" ] && inscription=true
kc update realms/mbiling -s "registrationAllowed=$inscription"

# --- Management (espace d'administration) ---------------------------------------------------------

# Ses rôles : un compte n'entre dans Management qu'avec l'un d'eux (permissions : mbiling_cloud/management/droits.json).
for role in management-superadmin management-admin management-support management-lecteur; do
	if ! kc get "roles/$role" -r mbiling >/dev/null 2>&1; then
		kc create roles -r mbiling -s "name=$role" -s "description=Rôle de l'espace Management" >/dev/null
	fi
done

id_client() { kc get clients -r mbiling -q "clientId=$1" --fields id --format csv --noquotes | tr -d '\r'; }

# Le client « management » : connexion des administrateurs (code + PKCE).
if [ -z "$(id_client management)" ]; then
	compose exec -T comptes /opt/keycloak/bin/kcadm.sh create clients -r mbiling --config /tmp/kcadm.config -f - >/dev/null <<'JSON'
{"clientId": "management", "name": "Management Mbiling", "enabled": true, "protocol": "openid-connect",
 "publicClient": false, "clientAuthenticatorType": "client-secret", "standardFlowEnabled": true,
 "implicitFlowEnabled": false, "directAccessGrantsEnabled": false, "serviceAccountsEnabled": false,
 "attributes": {"pkce.code.challenge.method": "S256"}}
JSON
fi
id_management="$(id_client management)"
kc update "clients/$id_management" -r mbiling \
	-s "secret=$MBILING_MANAGEMENT_CLIENT_SECRET" \
	-s "redirectUris=[\"https://$MBILING_DOMAINE/management/retour\"]" \
	-s "webOrigins=[\"https://$MBILING_DOMAINE\"]" \
	-s "attributes.\"post.logout.redirect.uris\"=https://$MBILING_DOMAINE/management/"

# Le compte de service « management-api » : Management règle le royaume (emails), lit les comptes et
# leurs facteurs de connexion, et les suspend ou réactive (écran Comptes), rien d'autre.
if [ -z "$(id_client management-api)" ]; then
	compose exec -T comptes /opt/keycloak/bin/kcadm.sh create clients -r mbiling --config /tmp/kcadm.config -f - >/dev/null <<'JSON'
{"clientId": "management-api", "name": "Management Mbiling (API)", "enabled": true, "protocol": "openid-connect",
 "publicClient": false, "clientAuthenticatorType": "client-secret", "standardFlowEnabled": false,
 "implicitFlowEnabled": false, "directAccessGrantsEnabled": false, "serviceAccountsEnabled": true}
JSON
fi
kc update "clients/$(id_client management-api)" -r mbiling -s "secret=$MBILING_MANAGEMENT_API_SECRET"
kc add-roles -r mbiling --uusername service-account-management-api --cclientid realm-management \
	--rolename manage-realm --rolename view-users --rolename manage-users

# --- Mon compte (espace de chaque utilisateur) ---------------------------------------------------

# Le client « compte » : connexion de tout utilisateur (code + PKCE), et compte de service qui lit et
# ferme les sessions de cet utilisateur (« Mes appareils »), rien d'autre.
if [ -z "$(id_client compte)" ]; then
	compose exec -T comptes /opt/keycloak/bin/kcadm.sh create clients -r mbiling --config /tmp/kcadm.config -f - >/dev/null <<'JSON'
{"clientId": "compte", "name": "Mon compte Mbiling", "enabled": true, "protocol": "openid-connect",
 "publicClient": false, "clientAuthenticatorType": "client-secret", "standardFlowEnabled": true,
 "implicitFlowEnabled": false, "directAccessGrantsEnabled": false, "serviceAccountsEnabled": true,
 "attributes": {"pkce.code.challenge.method": "S256"}}
JSON
fi
kc update "clients/$(id_client compte)" -r mbiling \
	-s "secret=$MBILING_COMPTE_CLIENT_SECRET" \
	-s "redirectUris=[\"https://$MBILING_DOMAINE/compte/retour\"]" \
	-s "webOrigins=[\"https://$MBILING_DOMAINE\"]" \
	-s "attributes.\"post.logout.redirect.uris\"=https://$MBILING_DOMAINE/compte/"
kc add-roles -r mbiling --uusername service-account-compte --cclientid realm-management \
	--rolename view-users --rolename manage-users

# Connexion à Management : double authentification TOUJOURS demandée (configurée au premier passage),
# sans reprise d'une session ouverte ailleurs (cookie) ni connexion Google/Apple.
flux=navigateur-management
if ! kc get authentication/flows -r mbiling --fields alias --format csv --noquotes | tr -d '\r"' | grep -qx "$flux"; then
	kc create authentication/flows/browser/copy -r mbiling -s "newName=$flux" >/dev/null
fi
kc get "authentication/flows/$flux/executions" -r mbiling | python3 -c '
import json, re, sys
for e in json.load(sys.stdin):
    nom = e.get("displayName", "")
    # Ne restent que : identifiant et mot de passe, puis code à 6 chiffres. Toute condition (qui
    # pourrait sauter l étape du code) et tout autre chemin (cookie, Google, organisations) sont coupés.
    voulu = ("DISABLED" if nom in ("Cookie", "Kerberos", "Identity Provider Redirector") or nom.startswith("Condition - ")
             or (e.get("level") == 0 and "Organization" in nom)
             else "REQUIRED" if nom.endswith(" forms") or re.search(r"Conditional (OTP|2FA)", nom) or nom == "OTP Form"
             else None)
    if voulu and e.get("requirement") != voulu:
        e["requirement"] = voulu
        print(json.dumps(e))
' | while IFS= read -r execution; do
	printf '%s' "$execution" | compose exec -T comptes /opt/keycloak/bin/kcadm.sh update \
		"authentication/flows/$flux/executions" -r mbiling --config /tmp/kcadm.config -f - >/dev/null
done
id_flux="$(kc get authentication/flows -r mbiling | python3 -c "import json,sys; print(next(f['id'] for f in json.load(sys.stdin) if f['alias'] == '$flux'))")"
kc update "clients/$id_management" -r mbiling -s "authenticationFlowBindingOverrides.browser=$id_flux"

# --- Emails ---------------------------------------------------------------------------------------

# Les réglages d'emails appartiennent à Management (écran « Emails »). Les lignes MBILING_EMAIL_* de
# .env ne servent qu'à la toute première installation : Management les importe une fois, puis on les
# vide. Vides, ce script ne touche plus aux emails (il n'écrase jamais un réglage fait dans Management).
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
fi

echo "Serveur de comptes configuré : https://$MBILING_DOMAINE/comptes/realms/mbiling"
