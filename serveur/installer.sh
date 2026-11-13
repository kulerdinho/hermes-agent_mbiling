#!/usr/bin/env bash
# Installe ou met à jour les services Mbiling du serveur. Peut être relancé sans risque.
# Prérequis : Docker et Docker Compose, sudo, et un fichier `.env` (voir `.env.exemple`).
set -euo pipefail

dossier="$(cd "$(dirname "$0")" && pwd)"
if [ ! -f "$dossier/.env" ]; then
	echo "Il manque $dossier/.env : copier .env.exemple, puis l'adapter." >&2
	exit 1
fi
set -a
. "$dossier/.env"
set +a

# Dossiers de données et de sauvegarde.
sudo install -d -m 750 "$MBILING_DONNEES" "$MBILING_DONNEES/cellules" "$MBILING_DONNEES/relais"
sudo install -d -m 700 "$MBILING_SAUVEGARDES"

# Sauvegarde nocturne : les modèles systemd reçoivent le chemin et l'heure de `.env`.
for unite in mbiling-sauvegarde.service mbiling-sauvegarde.timer; do
	sed -e "s|@DOSSIER@|$dossier|g" -e "s|@HEURE@|$MBILING_SAUVEGARDE_HEURE|g" \
		"$dossier/systemd/$unite" | sudo tee "/etc/systemd/system/$unite" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now mbiling-sauvegarde.timer >/dev/null

# Le tunnel ne démarre que si l'administrateur a déposé son jeton.
profils=()
if sudo test -f "$MBILING_JETON_TUNNEL_FICHIER"; then
	# L'image cloudflared tourne sous l'utilisateur 65532 : il doit pouvoir lire le jeton.
	sudo chown 65532:65532 "$MBILING_JETON_TUNNEL_FICHIER"
	sudo chmod 400 "$MBILING_JETON_TUNNEL_FICHIER"
	profils=(--profile tunnel)
else
	echo "Pas de jeton de tunnel ($MBILING_JETON_TUNNEL_FICHIER) : passerelle en local seulement."
fi

sudo docker compose --project-directory "$dossier" "${profils[@]}" up -d --remove-orphans
sudo docker compose --project-directory "$dossier" "${profils[@]}" ps
