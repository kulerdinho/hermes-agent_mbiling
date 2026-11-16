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

## La cellule et le Relais

- **L'image du moteur** est construite sur le serveur, depuis ce dépôt :
  `sudo docker build -t mbiling-moteur:phase0 .` (à la racine du dépôt). Le Relais utilise la même image.
- **Le modèle d'IA :** `MBILING_MODELE`, plus la clé de **test** plafonnée (`MBILING_CLE_IA_VARIABLE`
  et `MBILING_CLE_IA`). `installer.sh` copie la clé dans les secrets de la cellule.
- **Les jetons internes** (cellule, Relais, PC de test) sont générés dans `.env` au premier lancement.
  `MBILING_JETON_CELLULE` est l'accès d'administration (page `/login`, champ du jeton).
- **La connexion des utilisateurs** (Desktop, navigateur) : identifiant `MBILING_CONNEXION_IDENTIFIANT`
  et mot de passe `MBILING_CONNEXION_MOT_DE_PASSE`, généré dans `.env` au premier lancement. La cellule
  n'en reçoit que l'empreinte. Pour changer le mot de passe : le remplacer dans `.env`, relancer `installer.sh`.
- **Renouveler tous les secrets** (fin de la phase 0, ou après une fuite) : `./renouveler-secrets.sh`.
  Il demande le nouveau mot de passe (saisie masquée), régénère les jetons internes et la clé des sessions,
  révoque les PC inscrits (l'application les réinscrit à la connexion suivante), puis relance `installer.sh`.
  Le jeton du tunnel et la clé d'IA se renouvellent chez leurs fournisseurs.
- **Où s'exécutent les commandes des agents :** `MBILING_TERMINAL_CELLULE` (par défaut `mbiling_mains`,
  le PC du client), imposé à tous les profils : un écran de réglages ne peut pas les ramener dans la cellule.
- **La configuration de départ** (`cellule/modele-config.yaml`) n'est copiée qu'à la création de la
  cellule, et les profils de `MBILING_PROFILS` ne sont créés qu'une fois.
- **Ressources :** la cellule est plafonnée (`MBILING_CELLULE_MEMOIRE`, `MBILING_CELLULE_CPU`), et un relevé
  est noté toutes les 5 minutes : `journalctl -u mbiling-surveillance`.

## Vérifier

```bash
curl -s http://127.0.0.1:8090/sante            # ok
systemctl list-timers mbiling-sauvegarde.timer  # prochaine sauvegarde
sudo systemctl start mbiling-sauvegarde.service # sauvegarde immédiate
```
