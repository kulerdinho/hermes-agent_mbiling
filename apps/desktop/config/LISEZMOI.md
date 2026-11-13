# Réglages par défaut de Mbiling Desktop

Ces fichiers sont livrés avec l'application (dossier `resources/mbiling-config`). Au premier
lancement, chacun est copié dans le dossier de données de l'application, où il peut être modifié :
la copie de l'utilisateur prime toujours sur celle livrée.

| Fichier | Contenu |
|---|---|
| `connexion-par-defaut.json` | Adresse du serveur Mbiling auquel l'application se connecte (connexion par compte uniquement). `connexion_avancee` : réservé à l'équipe, propose aussi la connexion manuelle et l'installation locale ; `false` pour les utilisateurs. |
| `mains.json` | Les Mains : activées ou non, adresse du Relais, délais, chemin de bash (facultatif). |
| `mains-politique.json` | Dossiers autorisés et motifs interdits, appliqués sur le PC avant toute commande, quoi que demande le serveur. `{documents}` désigne le dossier Documents de l'utilisateur. |

Le jeton d'appareil n'est jamais dans ces fichiers : il est saisi dans les réglages de
l'application et conservé chiffré par Windows.
