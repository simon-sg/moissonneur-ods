"""Rapport ``--dry-run`` persistant : libellés thème/licence non mappés.

Sans fichier d'acquittement, chaque ``--dry-run`` resignalerait indéfiniment les mêmes
libellés déjà vus et traités une fois. On enregistre donc chaque libellé découvert avec
un statut (``a_traiter`` ou ``ignore_volontairement``) et on ne réaffiche que ceux qui
restent à traiter.
"""
from __future__ import annotations

import datetime
import json
import os

STATUT_A_TRAITER = "a_traiter"
STATUT_IGNORE = "ignore_volontairement"
CATEGORIES = ("themes", "licences")


def _maintenant() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Decouvertes:
    """Charge/écrit le rapport de découvertes JSON (thèmes + licences non mappés)."""

    def __init__(self, chemin: str):
        self.chemin = chemin
        self.donnees: dict[str, dict[str, dict]] = {c: {} for c in CATEGORIES}
        if chemin and os.path.isfile(chemin):
            try:
                with open(chemin, encoding="utf-8") as f:
                    contenu = json.load(f)
                for categorie in CATEGORIES:
                    if isinstance(contenu.get(categorie), dict):
                        self.donnees[categorie] = contenu[categorie]
            except (OSError, json.JSONDecodeError) as e:
                print(f"[decouvertes] {chemin} illisible ({e}), repart d'un rapport vide.")

    def statut(self, categorie: str, libelle: str) -> str | None:
        entree = self.donnees.get(categorie, {}).get(libelle)
        return entree.get("statut") if isinstance(entree, dict) else entree

    def enregistrer(self, categorie: str, libelle: str,
                    statut: str = STATUT_A_TRAITER) -> None:
        """Enregistre un libellé découvert ; conserve un statut existant s'il est déjà posé."""
        if not libelle:
            return
        existant = self.donnees.setdefault(categorie, {}).get(libelle)
        if isinstance(existant, dict):
            existant["dernier_vu"] = _maintenant()
            if existing_statut := existant.get("statut"):
                statut = existing_statut
            else:
                existant["statut"] = statut
            return
        self.donnees.setdefault(categorie, {})[libelle] = {
            "statut": statut,
            "premier_vu": _maintenant(),
            "dernier_vu": _maintenant(),
        }

    def non_ignores(self, categorie: str) -> list[str]:
        return sorted(
            libelle for libelle, entree in self.donnees.get(categorie, {}).items()
            if (entree.get("statut") if isinstance(entree, dict) else entree) != STATUT_IGNORE
        )

    def sauvegarder(self) -> None:
        if not self.chemin:
            return
        dossier = os.path.dirname(self.chemin)
        if dossier:
            os.makedirs(dossier, exist_ok=True)
        with open(self.chemin, "w", encoding="utf-8") as f:
            json.dump(self.donnees, f, ensure_ascii=False, indent=2)
