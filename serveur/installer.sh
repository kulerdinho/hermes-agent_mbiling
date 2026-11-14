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
# Connexion des utilisateurs : mot de passe (court, à taper) et clé de signature des sessions.
if ! grep -qE "^MBILING_CONNEXION_MOT_DE_PASSE=.+" "$env_fichier"; then
	sed -i "/^MBILING_CONNEXION_MOT_DE_PASSE=/d" "$env_fichier"
	echo "MBILING_CONNEXION_MOT_DE_PASSE=$(openssl rand -base64 24 | tr -d '\n/+=' | cut -c1-20)" >>"$env_fichier"
	echo "Mot de passe de connexion généré (à lire dans .env)."
fi
if ! grep -qE "^MBILING_CONNEXION_SECRET=.+" "$env_fichier"; then
	sed -i "/^MBILING_CONNEXION_SECRET=/d" "$env_fichier"
	echo "MBILING_CONNEXION_SECRET=$(openssl rand -base64 32 | tr -d '\n')" >>"$env_fichier"
fi
chmod 600 "$env_fichier"

set -a
. "$env_fichier"
set +a

# Empreinte du mot de passe, calculée par le moteur lui-même (scrypt), recalculée à chaque
# installation pour suivre un changement de mot de passe. Entre apostrophes : elle contient des $.
empreinte_mdp="$(printf '%s' "$MBILING_CONNEXION_MOT_DE_PASSE" | sudo docker run --rm -i -w /opt/mbiling \
	--entrypoint /opt/mbiling/.venv/bin/python "$MBILING_IMAGE_MOTEUR" -c \
	"import sys; from plugins.dashboard_auth.basic import hash_password; print(hash_password(sys.stdin.read()))")"
sed -i "/^MBILING_CONNEXION_EMPREINTE=/d" "$env_fichier"
echo "MBILING_CONNEXION_EMPREINTE='$empreinte_mdp'" >>"$env_fichier"
# Exportée : pour docker compose, l'environnement du shell passe avant le fichier .env.
export MBILING_CONNEXION_EMPREINTE="$empreinte_mdp"

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

# Secrets de la cellule : écrits dans le .env de la cellule et de chacun de ses profils
# (chaque profil ne lit que le sien), jamais dans config.yaml.
ecrire_secret_cellule() {
	local nom="$1" valeur="$2" fichier
	for fichier in "$cellule_donnees/.env" $(sudo sh -c "ls -d '$cellule_donnees'/profiles/*/ 2>/dev/null" | sed 's|/$|/.env|'); do
		sudo touch "$fichier"
		sudo sed -i "/^$nom=/d" "$fichier"
		echo "$nom=$valeur" | sudo tee -a "$fichier" >/dev/null
		sudo chown 10000:10000 "$fichier"
		sudo chmod 600 "$fichier"
	done
}

ecrire_secrets_cellule() {
	# Clé d'IA de test.
	if [ -n "${MBILING_CLE_IA:-}" ] && [ -n "${MBILING_CLE_IA_VARIABLE:-}" ]; then
		ecrire_secret_cellule "$MBILING_CLE_IA_VARIABLE" "$MBILING_CLE_IA"
	fi
	# Navigateur du PC (lot 6) : l'adresse CDP passe par le Relais et porte son jeton interne.
	ecrire_secret_cellule BROWSER_CDP_URL "http://relais:8000/cdp/$MBILING_RELAIS_JETON_INTERNE/$MBILING_ORGANISATION"
}

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

# Caddy ne relit pas seul son fichier monté (administration désactivée) : on ne le
# redémarre que si le Caddyfile a changé depuis la dernière installation.
empreinte="$(sha256sum "$dossier/Caddyfile" | cut -d' ' -f1)"
if [ "$(sudo cat "$MBILING_DONNEES/.caddyfile.sha256" 2>/dev/null)" != "$empreinte" ]; then
	compose restart passerelle
	echo "$empreinte" | sudo tee "$MBILING_DONNEES/.caddyfile.sha256" >/dev/null
fi

# Profils d'agents de la cellule, créés une seule fois (copie de la configuration de départ),
# sous l'utilisateur du moteur pour que ses fichiers lui appartiennent.
for profil in $MBILING_PROFILS; do
	if ! sudo test -d "$cellule_donnees/profiles/$profil"; then
		compose exec -T -u mbiling -e HOME=/opt/data cellule mbiling profile create "$profil" --clone
	fi
done

# Après la création des profils, pour qu'ils reçoivent aussi les secrets ; la cellule relit
# ses .env au redémarrage.
ecrire_secrets_cellule
compose restart cellule

compose ps
