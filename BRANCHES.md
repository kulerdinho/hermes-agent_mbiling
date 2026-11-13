# Mbiling — règles des branches

## Les trois étapes

| Branche | Rôle | Reçoit uniquement |
|---|---|---|
| `dev` | Intégration : tout le travail des développeurs arrive ici. | des branches `feature/…`, `fix/…`, `chore/…`, `docs/…`, `hotfix/…` |
| `staging` | Test : c'est ici qu'on valide que le code est bon pour la production. | `dev` |
| `main` | Production : ce que les clients utilisent. | `staging` |

`upstream` contient Mbiling Agent tel que publié par Nous Research. On n'y travaille jamais :
elle ne change que lorsqu'on importe une nouvelle version de Mbiling.

## Le chemin d'une modification

1. Partir de `dev` à jour : `git switch dev && git pull && git switch -c feature/mon-sujet`.
2. Commiter, puis pousser la branche de travail.
3. Ouvrir une demande de fusion (pull request) vers `dev`. Les contrôles automatiques se lancent.
4. Quand `dev` est prêt à être testé : demande de fusion `dev` → `staging`.
5. Tester sur l'environnement de test. Si tout est validé : demande de fusion `staging` → `main`.

Une correction urgente suit le même chemin (`hotfix/…` → `dev` → `staging` → `main`) : on ne
pousse jamais directement sur `main`.

## Ce qui est automatique

À chaque demande de fusion vers `dev`, `staging` ou `main`, le flux `Mbiling CI` vérifie :

- **le sens de la fusion** : la branche d'origine est bien celle autorisée par le tableau ci-dessus ;
- **l'absence de secrets** (mots de passe, jetons, clés) dans les commits ajoutés ;
- **le renommage** : aucune mention visible de l'ancien nom ;
- **les Mains** : le Relais, le plugin et le client PC fonctionnent de bout en bout.

Le contrôle final `valide` ne réussit que si tous les autres réussissent. C'est lui qui est
exigé par les protections de branches.

## Les protections (réglées sur GitHub)

Pour `dev`, `staging` et `main` :

- aucune poussée directe : tout passe par une demande de fusion ;
- le contrôle `valide` doit réussir avant la fusion ;
- la branche doit être à jour avec sa cible avant la fusion ;
- pas de réécriture de l'historique (poussée forcée interdite) ;
- pas de suppression de la branche ;
- les règles s'appliquent aussi aux administrateurs.

## Ce qu'on ne commite jamais

Fichiers `.env`, jetons, mots de passe, clés privées, bases de données. Les valeurs de
configuration vont dans des fichiers d'exemple (`*.exemple.*`) ; les secrets dans des variables
d'environnement ou dans le coffre.
