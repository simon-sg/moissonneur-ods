"""Wrapper mince autour de ``rudi_harvest_toolkit.state``.

Ajoute seulement le nommage par plateforme du fichier d'état et la mise à jour de
l'entrée d'un JDD — ``dataset_a_change`` reste la fonction d'incrémental du toolkit,
clé sur ``metas.default.modified``.
"""
from __future__ import annotations

from urllib.parse import urlparse

from rudi_harvest_toolkit.state import (  # noqa: F401
    charger_etat,
    dataset_a_change,
    sauvegarder_etat,
)

__all__ = [
    "charger_etat",
    "sauvegarder_etat",
    "dataset_a_change",
    "nom_fichier_etat",
    "chemin_etat",
    "enregistrer_jdd",
]


def nom_fichier_etat(url_plateforme: str) -> str:
    hote = urlparse(url_plateforme).hostname or url_plateforme
    return "etat_" + hote.replace(":", "_").replace(".", "_") + ".json"


def chemin_etat(url_plateforme: str, dossier: str) -> str:
    return f"{dossier.rstrip('/')}/{nom_fichier_etat(url_plateforme)}"


def enregistrer_jdd(etat: dict, dataset_id: str, modified: str | None,
                    local_id: str | None = None, titre: str | None = None) -> None:
    """Enregistre/actualise l'état d'un JDD traité (clé = ``dataset_id`` ODS)."""
    entree = etat.setdefault(dataset_id, {})
    entree["last_modified"] = modified
    if local_id:
        entree["local_id"] = local_id
    if titre:
        entree["titre"] = titre
