#!/usr/bin/env bash
# Renouvelle tous les secrets du serveur (fin de la phase 0, ou après une fuite) :
#   - jetons internes (cellule, Relais, appareil de test) et clé de signature des sessions ;
#   - mot de passe de connexion, saisi ici par l'administrateur (jamais affiché ni écrit ailleurs que .env) ;
#   - PC inscrits : leur registre est mis de côté, l'application Mbiling les réinscrit seule
#     à la connexion suivante.
# Ne touche pas au jeton du tunnel Cloudflare ni à la clé d'IA (gérés chez leurs fournisseurs).
set -euo pipefail

dossier="$(cd "$(dirname "$0")" && pwd)"
env_fichier="$dossier/.env"
[ -f "$env_fichier" ] || { echo "Il manque $env_fichier." >&2; exit 1; }

read -rsp "Nouveau mot de passe de connexion (12 caractères au moins, sans apostrophe) : " mdp; echo
read -rsp "Saisissez-le une seconde fois : " mdp_bis; echo
[ "$mdp" = "$mdp_bis" ] || { echo "Les deux saisies diffèrent : rien n'a été changé." >&2; exit 1; }
[ "${#mdp}" -ge 12 ] || { echo "Trop court : rien n'a été changé." >&2; exit 1; }
case "$mdp" in *"'"*) echo "Apostrophe interdite : rien n'a été changé." >&2; exit 1 ;; esac

horodatage="$(date +%Y%m%d-%H%M%S)"
copie="$env_fichier.avant-renouvellement-$horodatage"
cp -p "$env_fichier" "$copie"
chmod 600 "$copie"

sed -i -E '/^(MBILING_JETON_CELLULE|MBILING_RELAIS_JETON_INTERNE|MBILING_JETON_APPAREIL_PC_TEST_1|MBILING_CONNEXION_SECRET|MBILING_CONNEXION_MOT_DE_PASSE)=/d' "$env_fichier"
printf "MBILING_CONNEXION_MOT_DE_PASSE='%s'\n" "$mdp" >>"$env_fichier"
unset mdp mdp_bis

donnees="$(grep -E '^MBILING_DONNEES=' "$env_fichier" | cut -d= -f2-)"
registre="$donnees/relais/appareils.json"
if sudo test -f "$registre"; then
	sudo mv "$registre" "$registre.revoque-$horodatage"
	echo "PC inscrits révoqués : ils se réinscriront à leur prochaine connexion."
fi

# installer.sh génère les jetons manquants, recalcule l'empreinte du mot de passe et relance les services.
"$dossier/installer.sh"

echo
echo "Secrets renouvelés. Ancien .env conservé dans $copie (à supprimer quand tout fonctionne)."
echo "Les utilisateurs doivent se reconnecter avec le nouveau mot de passe."
