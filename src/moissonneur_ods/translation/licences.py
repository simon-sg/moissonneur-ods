"""Correspondance des licences Opendatasoft vers les blocs licence RUDI.

Opendatasoft expose déjà ``license`` (libellé) et ``license_url``. On distingue trois
cas, jamais confondus :

- libellé **connu** → bloc ``STANDARD`` (ODbL, Licence Ouverte/Etalab) ou
  ``pivot.LICENCE_ETALAB`` ;
- libellé **inconnu mais présent** → bloc ``CUSTOM`` en passthrough
  (``custom_licence_label``/``custom_licence_uri`` conservés) ;
- libellé **absent** → repli configurable par l'appelant, signalé dans le rapport
  ``--dry-run`` (jamais de licence inventée silencieusement).
"""
from __future__ import annotations

from rudi_harvest_toolkit.pivot import LICENCE_ETALAB, construire_licence

from .themes import normaliser_libelle

LICENCE_ODBL = {
    "licence_type": "STANDARD",
    "licence_label": "odbl-1.0",
    "licence_uri": "https://opendatacommons.org/licenses/odbl/1-0",
}

# Alias regroupés par bloc RUDI. Les clés de LICENCES_CONNUES sont normalisées
# (minuscules, sans accents ni ponctuation) pour absorber les variantes de libellé
# d'une plateforme ODS à l'autre.
_ALIAS: list[tuple[list[str], dict]] = [
    (
        ["odbl", "open database license", "open database license odbl",
         "odc odbl", "cc odbl", "open data commons odbl"],
        LICENCE_ODBL,
    ),
    (
        ["licence ouverte", "licence ouverte open licence", "open licence",
         "open licence etalab", "etalab", "etalab 2 0", "lov2", "licence etalab"],
        LICENCE_ETALAB,
    ),
]

LICENCES_CONNUES: dict[str, dict] = {
    normaliser_libelle(alias): bloc
    for alias_group, bloc in _ALIAS
    for alias in alias_group
}


def resoudre_licence(label: str | None, url: str | None = None,
                     table_supplementaire: dict[str, dict] | None = None,
                     repli: dict | None = None) -> tuple[dict | None, str]:
    """Retourne ``(bloc licence RUDI, statut)``.

    Statuts : ``connu``, ``inconnu`` (présent mais non référencé → CUSTOM),
    ``absent`` (aucun libellé, aucun repli) ou ``absent_repli`` (aucun libellé,
    repli appliqué).
    """
    if not label:
        if repli is not None:
            return dict(repli), "absent_repli"
        return None, "absent"

    table = dict(LICENCES_CONNUES)
    for cle, bloc in (table_supplementaire or {}).items():
        table[normaliser_libelle(cle)] = bloc

    bloc = table.get(normaliser_libelle(label))
    if bloc is not None:
        return dict(bloc), "connu"

    custom = construire_licence(label, url, {})
    if custom is None:
        custom = {"licence_type": "CUSTOM",
                  "custom_licence_label": [{"lang": "fr", "text": label}]}
        if url:
            custom["custom_licence_uri"] = url
    return custom, "inconnu"
