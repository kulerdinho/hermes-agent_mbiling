#!/usr/bin/env bash
# Crée un compte de test tant que l'inscription libre est fermée.
# Usage : ./comptes/creer-compte.sh adresse@exemple.com "Prénom" "Nom"
# Un mot de passe provisoire est affiché une seule fois ici ; Mbiling demande de le changer
# à la première connexion.
set -euo pipefail

[ $# -ge 1 ] || { echo "Usage : $0 adresse@exemple.com [Prénom] [Nom]" >&2; exit 1; }
email="$1" prenom="${2:-}" nom="${3:-}"

dossier="$(cd "$(dirname "$0")/.." && pwd)"
set -a
. "$dossier/.env"
set +a

compose() { sudo docker compose --project-directory "$dossier" --profile comptes "$@"; }
kc() { compose exec -T comptes /opt/keycloak/bin/kcadm.sh "$@" --config /tmp/kcadm.config; }

compose exec -T -e KC_CLI_PASSWORD="$MBILING_COMPTES_ADMIN_MOT_DE_PASSE" comptes \
	/opt/keycloak/bin/kcadm.sh config credentials --config /tmp/kcadm.config \
	--server "http://localhost:8080/comptes" --realm master --user admin >/dev/null

kc create users -r mbiling -s "username=$email" -s "email=$email" -s emailVerified=true \
	-s "firstName=$prenom" -s "lastName=$nom" -s enabled=true >/dev/null

provisoire="$(openssl rand -base64 18 | tr -d '\n/+=' | cut -c1-16)"
kc set-password -r mbiling --username "$email" --new-password "$provisoire" --temporary

echo "Compte créé : $email"
echo "Mot de passe provisoire (à transmettre de vive voix, il ne sera plus affiché) : $provisoire"
