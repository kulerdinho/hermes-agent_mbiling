# Réglages par défaut de Mbiling Desktop

Ces fichiers sont livrés avec l'application (dossier `resources/mbiling-config`). Au premier
lancement, chacun est copié dans le dossier de données de l'application, où il peut être modifié :
la copie de l'utilisateur prime sur celle livrée.

**Exception : les adresses des serveurs viennent toujours de l'application.** `connexion-par-defaut.json`
n'est jamais copié ni lu dans le dossier de données, et l'adresse du Relais (`relais_url`) est toujours
lue dans le `mains.json` livré. Ainsi, une mise à jour qui change de serveur n'est jamais annulée par
une copie laissée par une version précédente.

| Fichier | Contenu |
|---|---|
| `connexion-par-defaut.json` | Adresse du serveur Mbiling (phase 0 : `https://mbiling.chloe-consulting.bid`, connexion par identifiant et mot de passe ; `api.mbiling.com` et Google/Apple viendront avec le vrai domaine et le serveur de comptes) auquel l'application se connecte (connexion par compte uniquement). `connexion_avancee` : réservé à l'équipe, propose aussi la connexion manuelle et l'installation locale ; `false` pour les utilisateurs. `fournisseurs` : boutons « Continuer avec … » (Google, Apple), transmis au service de comptes ; le serveur n'accepte que ceux listés dans `dashboard.oauth.idp_hint.allowed`. |
| `mains.json` | Les Mains : activées ou non, adresse du Relais, délais, chemin de bash (facultatif). `navigateur` : le navigateur du PC que l'agent peut piloter (`actif`, `prefere` edge/chrome, `chemin` facultatif, `profil` : profil réservé à Mbiling, par défaut dans le dossier de données de l'application, `delai_lancement_s`, `arguments`). |
| `mains-politique.json` | Dossiers autorisés et motifs interdits, appliqués sur le PC avant toute commande, quoi que demande le serveur. `sites_interdits` : adresses que l'agent ne peut jamais ouvrir dans le navigateur du PC. `{documents}` désigne le dossier Documents de l'utilisateur. |

Le jeton d'appareil n'est jamais dans ces fichiers : il est obtenu automatiquement à la connexion
au compte Mbiling et conservé chiffré par Windows.
