#!/usr/bin/env bash
# Crée un compte (tant que l'inscription libre est fermée).
# Usage : ./comptes/creer-compte.sh [--par-email] adresse@exemple.com [Prénom] [Nom]
#   --par-email : la personne reçoit un email pour choisir elle-même son mot de passe (lien valable
#                 12 h) ; personne d'autre ne le connaît. Demande les emails réglés (Management).
#   sinon       : un mot de passe provisoire est affiché une seule fois ici, à changer à la première connexion.
set -euo pipefail

par_email=0
if [ "${1:-}" = "--par-email" ]; then par_email=1; shift; fi
[ $# -ge 1 ] || { echo "Usage : $0 [--par-email] adresse@exemple.com [Prénom] [Nom]" >&2; exit 1; }
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
echo "Compte créé : $email"

if [ "$par_email" = "1" ]; then
	id="$(kc get users -r mbiling -q "email=$email" -q exact=true --fields id --format csv --noquotes | tr -d '\r')"
	kc update "users/$id/execute-actions-email" -r mbiling -q lifespan=43200 -b '["UPDATE_PASSWORD"]'
	echo "Email envoyé à $email pour choisir son mot de passe (lien valable 12 h)."
else
	provisoire="$(openssl rand -base64 18 | tr -d '\n/+=' | cut -c1-16)"
	kc set-password -r mbiling --username "$email" --new-password "$provisoire" --temporary
	echo "Mot de passe provisoire (à transmettre de vive voix, il ne sera plus affiché) : $provisoire"
fi
