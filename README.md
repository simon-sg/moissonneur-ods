# moissonneur-ods

Moissonneur **générique** d'une plateforme Opendatasoft (ODS/Huwise) vers un nœud
RUDI : « donne-moi l'URL d'une plateforme ODS, je peuple un nœud producteur RUDI avec
tous ses jeux de données ». Testé contre `data.rennesmetropole.fr` (672 JDD, API
Explore v2.1).

La traduction est pilotée par un mapping déclaratif (`translation/mapping_ods_rudi.json`,
documenté dans `docs/MAPPING_ODS_RUDI.md`) et s'appuie sur le package partagé
[rudi-harvest-toolkit](https://github.com/anomalyco/rudi-harvest-toolkit) (pivot de
métadonnées, état incrémental, publication).

## Installation

```bash
# Dépendance locale (rudi-harvest-toolkit pas encore publié sur un registre)
pip install --user --break-system-packages -e ../rudi-harvest-toolkit
pip install --user --break-system-packages -e .
# Attention : environnement Python externally-managed (Debian) → --break-system-packages
```

## Lancer une moisson en une seule commande

```bash
cp conf/rudi_node.example.json conf/rudi_node.json   # url/url_catalog/usr/pwd de votre nœud
cp conf/theme_mapping.example.json conf/theme_mapping.json

python3 -m moissonneur_ods --url https://data.rennesmetropole.fr \
    --mode=lien --rudi-conf conf/rudi_node.json \
    --conf-themes conf/theme_mapping.json
```

Optionnel : `--dry-run` (traduit tout le catalogue sans toucher au nœud ni télécharger
de lourd) et `--menage` (suppression des orphelins, voir plus bas).

## Les deux modes

| Mode | Comportement | Médias RUDI |
|---|---|---|
| `--mode=lien` (défaut) | chaque pièce jointe / export alternatif ODS devient un lien direct, **aucun téléchargement** | `SERVICE` vers l'URL ODS |
| `--mode=copie` | téléchargement local (garde-fou de taille `--taille-max-mo`, repli automatique en `SERVICE` si trop gros) puis upload | `FILE` via `publier_dataset()` |

Un JDD avec `data_visible: false` **ou** sans aucune pièce jointe/export retombe en
métadonnées-seules (`SERVICE` vers la fiche ODS), quel que soit le mode — jamais le run
ne plante pour ça.

## Incrémental et orphelins

- **Incrémental** : `metas.default.modified` est le marqueur de changement
  (`dataset_a_change()` du toolkit). Un second run ne retraite que les JDD modifiés.
  L'état est un JSON par plateforme (ex. `conf/etat_data_rennesmetropole_fr.json`).
- **Orphelins** : chaque JDD publié est tagué `collection_tag=moissonneur-ods:<url-plateforme>`.
  `--menage` ne compare donc que le périmètre de **ce** moissonneur (jamais
  l'intégralité du nœud, qui peut héberger les datasets de la moissonneuse-batteuse),
  liste les orphelins (titre + `local_id`) puis supprime **uniquement après saisie
  exacte `SUPPRIMER`**.

```bash
python3 -m moissonneur_ods --url https://data.rennesmetropole.fr \
    --rudi-conf conf/rudi_node.json --menage --dry-run   # liste sans supprimer
python3 -m moissonneur_ods --url https://data.rennesmetropole.fr \
    --rudi-conf conf/rudi_node.json --menage             # confirme puis supprime
```

## Lire le rapport `--dry-run`

Le dry-run écrit `conf/theme_mapping_decouvertes.json` : chaque libellé de
**thème**/**licence** non mappé y est enregistré avec un statut
(`a_traiter`/`ignore_volontairement`). Un libellé passé en `ignore_volontairement`
(édition manuelle du JSON) n'est plus re-signalé aux runs suivants. Le dry-run affiche
aussi en fin de sortie : nombre de JDD traités, volumétrie estimée en mode copie,
nombre de JDD sans export/pièce jointe, et les libellés restant à traiter.

```json
{
  "themes": {"Thème inconnu XYZ": {"statut": "a_traiter", "premier_vu": "...", "dernier_vu": "..."}},
  "licences": {}
}
```

## Raccorder le nœud à un portail ROOB

`--raccorder-portail` déclare le nœud cible comme producteur/nœud d'un portail ROOB
(`rudi.localhost` en local), crée un compte **ROBOT** ACL et génère le
`rudi-catalog-portal.ini` (format `PORTAL_CONF` du conteneur nœud). Ce sont des
commandes **séparées** d'une moisson : le raccordement se fait une fois.

```bash
export RUDI_PORTAL_ADMIN_PASS=…        # ou saisie masquée demandée à l'exécution

python3 -m moissonneur_ods --rudi-conf conf/rudi_node.json \
    --raccorder-portail --scenario neuf \
    --portail-url http://rudi.localhost \
    --provider-code MOISS_ODS_RM --provider-label "Moissonneur ODS Rennes" \
    --node-catalog-url http://172.17.0.1:3030/catalog/v1
```

Deux scénarios, explicites (jamais devinés) :

| Scénario | Pour un nœud… | Effets |
|---|---|---|
| `neuf` | vide (0 ressource, vérifié) | producteur + nœud + compte ROBOT + `.ini` |
| `peuple` | déjà peuplé avant le raccordement | idem, **plus** un rappel d'enchaîner avec `--renvoyer-ressources` |

`--node-catalog-url` doit finir exactement par `/catalog/v1` (jamais `/api/v1`, qui
répond 308). Par défaut il est dérivé de `url_catalog` de la conf nœud.

L'outil s'arrête **avant toute action** sur confirmation textuelle : `RACCORDER`,
puis `RENVOYER` pour `--renvoyer-ressources`.

Artefacts générés (dans le répertoire de `--rudi-conf`, gitignored) :

- `conf/credentials_portail.json` (`0600`) — `provider_uuid`/`node_uuid` + mot de passe
  ROBOT ; **écrit seulement après** validation du token nœud.
- `conf/rudi-catalog-portal.ini` (`0600`) — à copier dans le volume de conf du nœud
  (le cycle de vie du conteneur reste manuel, hors périmètre de l'outil) :

  ```bash
  docker cp conf/rudi-catalog-portal.ini <conteneur-nœud>:/data/conf/rudi-catalog-portal.ini
  # puis (re)démarrer avec -e PORTAL_CONF=/data/conf/rudi-catalog-portal.ini
  ```

Une fois le `.ini` appliqué et le conteneur nœud redémarré (scénario `peuple`) :

```bash
python3 -m moissonneur_ods --rudi-conf conf/rudi_node.json --renvoyer-ressources
```

`--renvoyer-ressources` pousse les ressources du nœud **directement** vers Kalim
(`{portail}/kalim/v1/resources`, POST ou PUT selon présence déjà au catalogue), avec
le compte ROBOT du nœud — jamais via l'endpoint nœud
`/catalog/admin/portal/resources/send` (auth incompatible + auto-saturant, bug D2).

**Les organisations référencées (`producer.organization_id`) n'ont besoin d'aucune
action manuelle en base** : confirmé en conditions réelles le 2026-10-11, quand Kalim
reçoit une ressource dont l'organisation est inconnue côté portail, il la crée
automatiquement **avec le bon uuid** — un mécanisme différent de l'API admin
`POST /strukture/v1/organizations` (qui, elle, ignore l'uuid imposé, bug C2 du
rapport). L'organisation apparaît en attente de validation dans l'UI Strukture du
portail ; **c'est à vous de la valider** — c'est le geste humain que cette
procédure préserve volontairement, pas un oubli de l'outil. Une fois validée, les
ressources qui la référencent peuvent être pleinement publiées.

Notes : le nœud est déclaré `harvestable=false` (le pull Kalim est bloquant sans
contournement, bug A1) ; `notifiable=true` reste requis pour que le nœud puisse
pousser. `--force` ne sert qu'à relancer un raccordement déjà enregistré ou à passer
outre le contrôle « nœud vide » de `--scenario neuf`.

La poussée vers Kalim est **asynchrone** : `--renvoyer-ressources` ne fait que déposer
une fiche par ressource (un `integration_request` côté Kalim). Leur intégration réelle
et leur apparition dans le catalogue public (`konsult`) dépendent du planificateur de
Kalim et peuvent prendre plusieurs minutes — voire être retardées par la file de
traitement d'un autre nœud. Vérification (après quelques minutes) :

```bash
curl -s 'http://rudi.localhost/konsult/v1/datasets/metadatas?limit=1&offset=0'  # champ total
```

## Tester bout-en-bout contre plusieurs plateformes ODS

`--reinitialiser-noeud` vide le contenu local d'un nœud (ressources +
organisations, et le(s) fichier(s) d'état) sans toucher à un éventuel
raccordement portail — pour enchaîner des tests sur des plateformes et des
paramètres différents sans recréer le nœud à chaque fois :

```bash
python3 -m moissonneur_ods --url https://data.rennesmetropole.fr \
    --mode=lien --rudi-conf conf/rudi_node.json

python3 -m moissonneur_ods --rudi-conf conf/rudi_node.json --reinitialiser-noeud
# Taper REINITIALISER pour confirmer

python3 -m moissonneur_ods --url https://data.nantesmetropole.fr \
    --mode=copie --rudi-conf conf/rudi_node.json
```

Validé en conditions réelles (2026-10-11) : moisson Rennes (8 JDD) → réinit
(8 ressources + 6 organisations + état supprimés, 0 échec) → moisson Nantes
(6 JDD d'une plateforme totalement différente) sur le même nœud, sans résidu.

## Tests

Zéro appel réseau — fixtures JSON enregistrées (`tests/fixtures/`).

```bash
python3 -m unittest discover tests/
```

## Aide

```bash
python3 -m moissonneur_ods --help
```

## Limites connues

- v1 uniquement : seul le mode `lien`/`copie` global est supporté (pas de choix par JDD).
- Seule l'API Explore **v2.1** est gérée (vérifiée au premier appel — erreur claire sinon).
- La géographie n'est jamais devinée : aucun `geography` n'est injecté.
- Généralité confirmée sur une 2ᵉ instance ODS réelle (`data.nantesmetropole.fr`,
  en plus de `data.rennesmetropole.fr`) — chaque nouvelle plateforme a ses propres
  libellés de thème/licence à curer (`--conf-themes`, rapport `--dry-run`).