#!/usr/bin/env bash
# Installe ou met à jour les services Mbiling du serveur. Peut être relancé sans risque.
# Prérequis : Docker et Docker Compose, sudo, et un fichier `.env` (voir `.env.exemple`).
set -euo pipefail

dossier="$(cd "$(dirname "$0")" && pwd)"
env_fichier="$dossier/.env"
if [ ! -f "$env_fichier" ]; then
	echo "Il manque $env_fichier : copier .env.exemple, puis l'adapter." >&2
	exit 1
fi

# Réglages apparus dans .env.exemple depuis la dernière installation : ajoutés avec leur valeur par défaut.
grep -E '^[A-Z_]+=' "$dossier/.env.exemple" | while IFS= read -r ligne; do
	if ! grep -q "^${ligne%%=*}=" "$env_fichier"; then
		echo "$ligne" >>"$env_fichier"
		echo "Ajouté à .env : ${ligne%%=*}"
	fi
done

# Jetons internes : générés une seule fois, longs et aléatoires, conservés dans .env.
for nom in MBILING_JETON_CELLULE MBILING_RELAIS_JETON_INTERNE MBILING_JETON_APPAREIL_PC_TEST_1; do
	if ! grep -qE "^$nom=.+" "$env_fichier"; then
		sed -i "/^$nom=/d" "$env_fichier"
		echo "$nom=$(openssl rand -base64 48 | tr -d '\n/+=' | cut -c1-48)" >>"$env_fichier"
		echo "Jeton généré : $nom"
	fi
done
chmod 600 "$env_fichier"

set -a
. "$env_fichier"
set +a

compose() { sudo docker compose --project-directory "$dossier" "${profils[@]}" "$@"; }

# Dossiers de données et de sauvegarde.
cellule_donnees="$MBILING_DONNEES/cellules/$MBILING_ORGANISATION/data"
sudo install -d -m 750 "$MBILING_DONNEES" "$MBILING_DONNEES/cellules" "$MBILING_DONNEES/relais" "$cellule_donnees"
sudo install -d -m 700 "$MBILING_SAUVEGARDES"

# Configuration de départ de la cellule, seulement à sa création.
if ! sudo test -f "$cellule_donnees/config.yaml"; then
	sed "s|@MODELE@|$MBILING_MODELE|" "$dossier/cellule/modele-config.yaml" |
		sudo tee "$cellule_donnees/config.yaml" >/dev/null
	echo "Configuration de la cellule créée depuis le modèle."
fi

# Clé d'IA de test : écrite dans les secrets de la cellule (son .env), jamais dans config.yaml.
if [ -n "${MBILING_CLE_IA:-}" ] && [ -n "${MBILING_CLE_IA_VARIABLE:-}" ]; then
	sudo touch "$cellule_donnees/.env"
	sudo sed -i "/^$MBILING_CLE_IA_VARIABLE=/d" "$cellule_donnees/.env"
	echo "$MBILING_CLE_IA_VARIABLE=$MBILING_CLE_IA" | sudo tee -a "$cellule_donnees/.env" >/dev/null
	sudo chmod 600 "$cellule_donnees/.env"
fi

# Minuteurs systemd : sauvegarde nocturne et relevé des ressources.
for unite in mbiling-sauvegarde.service mbiling-sauvegarde.timer \
	mbiling-surveillance.service mbiling-surveillance.timer; do
	sed -e "s|@DOSSIER@|$dossier|g" -e "s|@HEURE@|$MBILING_SAUVEGARDE_HEURE|g" \
		"$dossier/systemd/$unite" | sudo tee "/etc/systemd/system/$unite" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now mbiling-sauvegarde.timer mbiling-surveillance.timer >/dev/null

# Le tunnel ne démarre que si l'administrateur a déposé son jeton.
profils=()
if sudo test -f "$MBILING_JETON_TUNNEL_FICHIER"; then
	# L'image cloudflared tourne sous l'utilisateur 65532 : il doit pouvoir lire le jeton.
	sudo chown 65532:65532 "$MBILING_JETON_TUNNEL_FICHIER"
	sudo chmod 400 "$MBILING_JETON_TUNNEL_FICHIER"
	profils=(--profile tunnel)
else
	echo "Pas de jeton de tunnel ($MBILING_JETON_TUNNEL_FICHIER) : accès local seulement."
fi

compose up -d --remove-orphans

# Profils d'agents de la cellule, créés une seule fois (copie de la configuration de départ).
for profil in $MBILING_PROFILS; do
	if ! sudo test -d "$cellule_donnees/profiles/$profil"; then
		compose exec -T cellule /opt/mbiling/docker/main-wrapper.sh profile create "$profil" --clone
	fi
done

compose ps
