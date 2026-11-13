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
tar -C "$MBILING_DONNEES" -czf "$archive.partiel" .
chmod 600 "$archive.partiel"
mv "$archive.partiel" "$archive"

find "$MBILING_SAUVEGARDES" -maxdepth 1 -name 'mbiling-donnees-*.tar.gz' \
	-mtime +"$MBILING_SAUVEGARDE_JOURS" -delete

echo "Sauvegarde : $archive ($(du -h "$archive" | cut -f1))"
