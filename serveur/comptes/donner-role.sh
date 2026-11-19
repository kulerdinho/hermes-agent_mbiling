#!/usr/bin/env bash
# Donne (ou retire) un rôle de l'espace Management à un compte.
# Usage : ./comptes/donner-role.sh adresse@exemple.com superadmin|admin|support|lecteur [--retirer]
# Le rôle compte à la prochaine connexion de la personne à Management. La double authentification
# lui sera demandée (et configurée au premier passage).
set -euo pipefail

[ $# -ge 2 ] || { echo "Usage : $0 adresse@exemple.com superadmin|admin|support|lecteur [--retirer]" >&2; exit 1; }
email="$1" role="management-$2" action=add-roles
case "$2" in superadmin | admin | support | lecteur) ;; *) echo "Rôle inconnu : $2" >&2; exit 1 ;; esac
[ "${3:-}" = "--retirer" ] && action=remove-roles

dossier="$(cd "$(dirname "$0")/.." && pwd)"
set -a
. "$dossier/.env"
set +a

compose() { sudo docker compose --project-directory "$dossier" --profile comptes "$@"; }
compose exec -T -e KC_CLI_PASSWORD="$MBILING_COMPTES_ADMIN_MOT_DE_PASSE" comptes \
	/opt/keycloak/bin/kcadm.sh config credentials --config /tmp/kcadm.config \
	--server "http://localhost:8080/comptes" --realm master --user admin >/dev/null
compose exec -T comptes /opt/keycloak/bin/kcadm.sh "$action" -r mbiling --uusername "$email" \
	--rolename "$role" --config /tmp/kcadm.config
echo "$([ "$action" = add-roles ] && echo "Rôle donné" || echo "Rôle retiré") : $role pour $email"
