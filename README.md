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
- Généralité hors `data.rennesmetropole.fr` à confirmer sur une 2ᵉ instance ODS
  (libellés de thème/licence, formes d'URL d'`attachments[]`).