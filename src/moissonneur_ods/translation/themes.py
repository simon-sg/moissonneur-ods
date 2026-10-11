"""Correspondance des libellés de thème Opendatasoft vers les thèmes RUDI.

``metas.default.theme`` est une liste de libellés **libres**, propres à chaque
plateforme (pas un référentiel contrôlé inter-instances). La correspondance est donc
une table curée par plateforme (``conf/theme_mapping.json``) ; un libellé non mappé
n'est jamais deviné silencieusement : il remonte en avertissement et un thème de repli
configurable est appliqué.
"""
from __future__ import annotations

import json
import re
import unicodedata

# Les 14 codes de thème acceptés par un nœud RUDI (schéma fermé).
THEMES_RUDI = {
    "economy", "citizenship", "energyNetworks", "culture", "transportation",
    "children", "environment", "townPlanning", "location", "education",
    "publicSpace", "health", "housing", "society",
}

# Libellés FR des thèmes RUDI (repris de moissonneuse-batteuse).
LIBELLES_THEMES = {
    "economy": "économie", "citizenship": "citoyenneté", "energyNetworks": "réseaux, énergie",
    "culture": "culture, sports, loisirs", "transportation": "mobilité, transport",
    "children": "enfance", "environment": "environnement", "townPlanning": "urbanisme",
    "location": "référentiels géographiques", "education": "éducation",
    "publicSpace": "espace public", "health": "santé, sécurité",
    "housing": "logement", "society": "social",
}

# Thème de repli quand aucun libellé ODS n'a de correspondance (configurable au CLI).
THEME_REPLI = "society"


def normaliser_libelle(libelle: str | None) -> str:
    """Clé de correspondance insensible à la casse, aux accents et à la ponctuation."""
    texte = unicodedata.normalize("NFKD", libelle or "").encode("ascii", "ignore").decode()
    texte = re.sub(r"[^a-z0-9]+", " ", texte.lower())
    return " ".join(texte.split())


def charger_mapping_plateforme(chemin: str | None) -> dict[str, str]:
    """Charge ``{libellé ODS: code RUDI}`` depuis un JSON, ou ``{}`` si absent."""
    if not chemin:
        return {}
    try:
        with open(chemin, encoding="utf-8") as f:
            donnees = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[themes] table {chemin} illisible ({e}), ignorée.")
        return {}
    if not isinstance(donnees, dict):
        print(f"[themes] table {chemin} : objet JSON attendu, ignorée.")
        return {}
    return {str(k): str(v) for k, v in donnees.items()}


def choisir_theme(libelles: list[str] | None, table_plateforme: dict[str, str] | None = None,
                  defaut: str | None = THEME_REPLI) -> tuple[str | None, list[str]]:
    """Retourne ``(code RUDI, libellés restants)``.

    Prend le **premier** libellé ODS qui a une correspondance valide dans la table ;
    tous les autres libellés sont retournés pour être versés dans ``keywords[]``.
    Si aucun libellé ne correspond, retourne ``(None, tous les libellés)`` — c'est à
    l'appelant de signaler et d'appliquer le repli, jamais cette fonction en silence.
    """
    libelles = [l for l in (libelles or []) if l]
    table = {normaliser_libelle(k): v for k, v in (table_plateforme or {}).items()}
    if defaut:
        table.setdefault(normaliser_libelle(defaut), defaut)

    for index, libelle in enumerate(libelles):
        code = table.get(normaliser_libelle(libelle))
        if code in THEMES_RUDI:
            restants = [l for j, l in enumerate(libelles) if j != index]
            return code, restants
    return None, libelles
