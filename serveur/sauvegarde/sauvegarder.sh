#!/usr/bin/env bash
# Sauvegarde nocturne des données Mbiling (cellules et Relais), lancée par
# le minuteur systemd mbiling-sauvegarde.timer.
# Phase 0 : archive à chaud. Une base SQLite en pleine écriture peut être
# incohérente dans l'archive ; le lot 3 ajoutera un instantané propre.
set -euo pipefail

dossier="$(cd "$(dirname "$0")/.." && pwd)"
set -a
. "$dossier/.env"
set +a
: "${MBILING_DONNEES:?}" "${MBILING_SAUVEGARDES:?}" "${MBILING_SAUVEGARDE_JOURS:?}"

install -d -m 700 "$MBILING_SAUVEGARDES"
archive="$MBILING_SAUVEGARDES/mbiling-donnees-$(date +%Y%m%d-%H%M%S).tar.gz"

# Écrire sous un nom temporaire : une archive interrompue n'est jamais prise pour une bonne.
# La base des comptes est sauvegardée à part, par un export cohérent (pg_dump) : ses fichiers
# bruts, copiés en pleine écriture, ne seraient pas restaurables.
tar -C "$MBILING_DONNEES" --exclude=./comptes/postgres -czf "$archive.partiel" .
chmod 600 "$archive.partiel"
mv "$archive.partiel" "$archive"
echo "Sauvegarde : $archive ($(du -h "$archive" | cut -f1))"

if [ "${MBILING_COMPTES:-0}" = "1" ]; then
	export_comptes="$MBILING_SAUVEGARDES/mbiling-comptes-$(date +%Y%m%d-%H%M%S).sql.gz"
	docker compose --project-directory "$dossier" --profile comptes exec -T comptes-db \
		pg_dump -U comptes --clean --if-exists comptes | gzip >"$export_comptes.partiel"
	chmod 600 "$export_comptes.partiel"
	mv "$export_comptes.partiel" "$export_comptes"
	echo "Sauvegarde des comptes : $export_comptes ($(du -h "$export_comptes" | cut -f1))"
fi

find "$MBILING_SAUVEGARDES" -maxdepth 1 \( -name 'mbiling-donnees-*.tar.gz' -o -name 'mbiling-comptes-*.sql.gz' \) \
	-mtime +"$MBILING_SAUVEGARDE_JOURS" -delete
