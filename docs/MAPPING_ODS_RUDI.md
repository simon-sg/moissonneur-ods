# Mapping métadonnées ODS → RUDI

Correspondance entre un jeu de données moissonné via l'API Explore v2.1
(`/api/explore/v2.1/catalog/datasets`) d'une plateforme Opendatasoft et la métadonnée
RUDI produite par `moissonneur-ods`.

Le mapping déclaratif **exécutable** est `src/moissonneur_ods/translation/mapping_ods_rudi.json`
(moteur `ObjectTranslator`/`FieldTranslator`, voir `translation/traducteur.py`). Ce
document est sa jumelle lisible.

## Champs lus dans la fiche ODS

La traduction lit trois emplacements du JDD ODS :

| Emplacement ODS | Rôle |
|---|---|
| `metas.default` | métadonnées descriptives (titre, description, thème, licence, dates…) |
| `attachments[]` | pièces jointes (URL, titre, mimetype) — curaté par le producteur, vide dans la quasi-totalité des cas réels |
| `alternative_exports[]` | exports alternatifs (URL, titre, mimetype) — idem, curaté et vide en pratique |
| `dataset_id` | identifiant du JDD sur la plateforme (racine de la fiche) |
| `has_records` / `features[]` | utilisés pour le repli vers les exports standards ODS (`/exports/{format}`) quand `alternative_exports` est vide — voir règle d'arbitrage 3 |

## Tableau de correspondance

| Champ RUDI | Champ ODS | Description | Obligatoire |
|---|---|---|---|
| `local_id` | `dataset_id` + URL plateforme | UUID v5 déterministe (`uuid5(URL_plateforme#dataset_id)`), stable d'un run à l'autre et sans collision inter-plateformes | oui |
| `resource_title` | `metas.default.title` | Titre du JDD (tronqué à 150 car. par le pivot) | oui (sinon `dataset_id`) |
| `synopsis` | `metas.default.description` | Résumé court ≤150 car. dérivé de la description (texte brut) | dérivé |
| `summary[].text` / `summary[].html` | `metas.default.description` | Description complète, HTML ODS converti en texte brut puis re-rendu par le pivot | dérivé |
| `theme` | `metas.default.theme[0]` (via `translation/themes.py`) | Premier libellé ODS mappé → code RUDI (14 valeurs fermées) ; sinon repli configurable `--theme-repli` + avertissement | oui (toujours une valeur) |
| `keywords` | `metas.default.keyword[]` + thèmes ODS restants | Mots-clés ODS + tous les libellés de thème non utilisés comme thème principal | oui |
| `producer.organization_name` | `metas.default.publisher` | Producteur ; repli sur le nom d'hôte de la plateforme | oui |
| `contacts` | — | Aucune donnée de contact fiable côté ODS : liste vide, le nœud crée un contact de repli au nom du producteur | non |
| `dataset_dates.created` **et** `dataset_dates.updated` | `metas.default.modified` | Date ISO 8601, normalisée UTC, **les deux identiques** — sert **aussi** de marqueur `dataset_a_change()` (incrémental). Les deux, jamais une seule : le nœud (`checkDates`, rudi-catalog) donne un défaut `nowISO()` à `created` s'il est seul absent, ce qui écraserait silencieusement la vraie date source par l'heure du harvest. ODS n'expose qu'un seul repère temporel fiable (`modified`), jamais de vraie date de création distincte | non |
| `available_formats` | `attachments[]` + `alternative_exports[]` | Un média par entrée, selon le mode (`SERVICE` lien / `FILE` copie) ; + `source-data` et `source-metadata` vers la fiche ODS | dérivé |
| `access_condition.licence` | `metas.default.license` + `license_url` | Voir `translation/licences.py` : STANDARD (ODbL, Etalab), CUSTOM (libellé inconnu, passthrough), ou repli configurable | oui (toujours une valeur, repli signalé) |
| `resource_languages` | `metas.default.language` | Code ISO 639-1 ODS (ex. `fr`) repris tel quel dans une liste à un élément | non (champ omis si absent) |
| `dataset_update_frequency` | `metas.default.update_frequency` | Voir `translation/frequences.py` : libellé déjà un code RUDI (`monthly`…) repris tel quel, libellé français connu mappé (table d'alias), libellé inconnu → repli `unknown` + avertissement — jamais deviné silencieusement | non (champ omis si absent) |
| `keywords` (complément) | `metas.default.territory[]` + `metas.default.geographic_reference[]` | Pas de champ RUDI dédié au territoire : versés en mots-clés plutôt que perdus, même logique que les libellés de thème non retenus comme thème principal | non |
| `geography` | — | Non déduit de façon silencieuse ; jamais injecté. (Aucun équivalent fiable côté ODS en v1.) | non |
| `collection_tag` | — | `moissonneur-ods:<url_plateforme>` — périmètre de la détection d'orphelins | oui (marquage moissonneur) |

## Règles d'arbitrage

1. **Thème** : `metas.default.theme` est une liste de libellés libres, propres à la
   plateforme — pas un référentiel contrôlé inter-instances. Le **premier** libellé
   ayant une correspondance dans `conf/theme_mapping.json` (table curée par
   plateforme, chargée via `--conf-themes`) devient `theme` ; tous les autres libellés
   sont versés dans `keywords`. Aucun libellé mappé → avertissement explicite +
   `--theme-repli` (défaut `society`, configurable), **jamais de devinette silencieuse**.
2. **Licence** : ODS fournit `license` + `license_url`. Libellé connu (ODbL, Licence
   Ouverte/Etalab) → bloc `STANDARD` ; libellé inconnu → bloc `CUSTOM`
   (`custom_licence_label`/`custom_licence_uri` en passthrough) ; **absence** →
   `--licence-repli` (sinon la valeur par défaut d'Etalab du pivot reste appliquée,
   signalée en avertissement et dans le rapport `--dry-run`).
3. **Médias** : chaque entrée `attachments[]`/`alternative_exports[]` devient un média.
   Mode `lien` → `SERVICE` vers l'URL ODS. Mode `copie` → téléchargement (garde-fou de
   taille `--taille-max-mo`, repli automatique en `SERVICE` si trop gros/échec) puis
   `FILE`. Un JDD `data_visible: false` ou sans pièce jointe/export retombe en
   **métadonnées-seules** (`source-metadata` vers la fiche ODS), quel que soit le mode.
   **Repli exports standards** (analyse du 2026-10-12, vérifiée sur plusieurs vraies
   plateformes ODS) : `alternative_exports[]` est vide dans la quasi-totalité des cas
   réels (c'est un champ curaté par le producteur, pas la représentation standard du
   JDD). Sans ce repli, le seul média produit était le générique `source-data`/
   `source-metadata` du pivot — un lien vers la page explore HTML, pas un fichier,
   sans bouton télécharger possible. L'API Explore v2.1 expose pourtant
   systématiquement, pour tout JDD avec des enregistrements (`has_records`), un
   export déjà servi comme un vrai fichier plat (`Content-Disposition: attachment`,
   `Content-Type` correct — vérifié directement) : `/api/explore/v2.1/catalog/
   datasets/{dataset_id}/exports/{format}`. Quand `alternative_exports` est vide,
   `entrees_donnees()` retombe sur cet export : toujours CSV, + GeoJSON si le JDD a
   une composante géographique (`features` contient `"geo"`).
4. **Incrémental** : `modified` est le marqueur de changement (via
   `rudi_harvest_toolkit.state.dataset_a_change`) ; un JDD inchangé est sauté.
5. **Orphelins** : le périmètre du moissonneur est `collection_tag=moissonneur-ods:<plateforme>`
   (jamais `metadata_info`, dont le schéma fermé perdrait le marqueur). Seuls les
   datasets du nœud portant ce tag peuvent être proposés à la suppression, après saisie
   exacte `SUPPRIMER`.
6. **Champs ODS délibérément non repris** : `metas.default.records_count` (statistique
   vivante côté plateforme, pas une métadonnée descriptive — aucun champ RUDI
   pertinent) et `metas.default.attributions` (libellé de crédit libre, sans équivalent
   RUDI évident) ne sont pas mappés. Absence volontaire, pas un oubli.