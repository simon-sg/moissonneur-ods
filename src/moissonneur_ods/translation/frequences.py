"""Correspondance des fréquences de mise à jour Opendatasoft vers l'énumération RUDI.

``dataset_update_frequency`` côté RUDI (rudi-catalog,
``definitions/thesaurus/UpdateFrequencies.js``) est une énumération fermée
(``continual``/``daily``/``weekly``/``fortnightly``/``monthly``/``quarterly``/
``biannually``/``annually``/``asNeeded``/``irregular``/``notPlanned``/``unknown``) —
une valeur hors de cette liste est **silencieusement effacée** côté nœud
(``metadata[...] = undefined`` si invalide). ``metas.default.update_frequency``
d'Opendatasoft est un libellé libre par plateforme : jamais deviné silencieusement,
toute valeur non référencée retombe sur ``unknown`` (qui fait partie de
l'énumération) et est signalée dans le rapport ``--dry-run``.
"""
from __future__ import annotations

from .themes import normaliser_libelle

FREQUENCES_RUDI = {
    "continual", "daily", "weekly", "fortnightly", "monthly", "quarterly",
    "biannually", "annually", "asNeeded", "irregular", "notPlanned", "unknown",
}

# Alias courants observés sur des plateformes Opendatasoft francophones — clés
# normalisées (minuscules, sans accents ni ponctuation) via normaliser_libelle.
_ALIAS: dict[str, str] = {
    "temps reel": "continual",
    "continu": "continual",
    "quotidienne": "daily",
    "quotidien": "daily",
    "journaliere": "daily",
    "hebdomadaire": "weekly",
    "bihebdomadaire": "fortnightly",
    "quinzomadaire": "fortnightly",
    "toutes les deux semaines": "fortnightly",
    "mensuelle": "monthly",
    "mensuel": "monthly",
    "trimestrielle": "quarterly",
    "trimestriel": "quarterly",
    "semestrielle": "biannually",
    "semestriel": "biannually",
    "annuelle": "annually",
    "annuel": "annually",
    "selon les besoins": "asNeeded",
    "a la demande": "asNeeded",
    "irreguliere": "irregular",
    "irregulier": "irregular",
    "ponctuelle": "notPlanned",
    "ponctuel": "notPlanned",
    "jamais": "notPlanned",
    "aucune mise a jour prevue": "notPlanned",
    "inconnue": "unknown",
    "inconnu": "unknown",
}


def resoudre_update_frequency(label: str | None,
                              table_supplementaire: dict[str, str] | None = None
                              ) -> tuple[str | None, str]:
    """Retourne ``(code RUDI ou None, statut)``.

    Statuts : ``connu``, ``inconnu`` (libellé présent mais non référencé → repli
    ``unknown``) ou ``absent`` (aucun libellé → ``None``, le champ RUDI est omis
    plutôt que de forcer ``unknown`` sans raison).
    """
    if not label:
        return None, "absent"

    if label in FREQUENCES_RUDI:
        # Certaines plateformes ODS utilisent déjà directement les codes RUDI
        # (anglais) — ne pas les faire passer par la table d'alias français.
        return label, "connu"

    table = dict(_ALIAS)
    for cle, code in (table_supplementaire or {}).items():
        table[normaliser_libelle(cle)] = code

    code = table.get(normaliser_libelle(label))
    if code in FREQUENCES_RUDI:
        return code, "connu"
    return "unknown", "inconnu"
