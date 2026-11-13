# Serveur Mbiling (phase 0)

La porte d'entrée du serveur : passerelle Caddy, tunnel Cloudflare et sauvegarde nocturne.
Le cahier de la phase 0 (lot 1) décrit le but ; ce dossier en est la mise en œuvre.

## Le chemin d'une requête

```
Internet ── Cloudflare (TLS) ── tunnel ──► 127.0.0.1:8090 (passerelle) ──► cellule (lot 3)
                                                                       └──► Relais /mains (lot 4)
```

Le serveur est derrière un NAT : aucun port public n'est ouvert. Le tunnel sort vers
Cloudflare, qui présente le certificat de `MBILING_DOMAINE`.

## Installer ou mettre à jour

Sur le serveur, dans `/home/django/projets/mbiling/serveur` :

```bash
cp .env.exemple .env      # la première fois seulement, puis l'adapter
./installer.sh
```

Le script peut être relancé autant de fois que nécessaire.

## Le tunnel

L'administrateur Cloudflare crée le tunnel, avec une adresse publique `MBILING_DOMAINE` qui pointe
vers `http://localhost:8090`. Il dépose ensuite le jeton dans `MBILING_JETON_TUNNEL_FICHIER`
(par défaut `/etc/mbiling/cloudflared.token`). Il ne le transmet jamais par message ni par git.
On relance ensuite `./installer.sh`.

## Vérifier

```bash
curl -s http://127.0.0.1:8090/sante            # ok
systemctl list-timers mbiling-sauvegarde.timer  # prochaine sauvegarde
sudo systemctl start mbiling-sauvegarde.service # sauvegarde immédiate
```
