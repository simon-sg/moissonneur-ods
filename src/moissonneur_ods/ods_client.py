"""Lecture du catalogue d'une plateforme Opendatasoft via l'API Explore v2.1.

Pagination ``GET {url_plateforme}/api/explore/v2.1/catalog/datasets?limit=N&offset=M``
avec retry/backoff sur les erreurs transitoires (5xx, 429, timeout réseau) — une
erreur en cours de pagination ne doit pas forcer un redémarrage depuis zéro.

L'API v2.1 est vérifiée explicitement dès le premier appel : une plateforme encore en
v1 (ou dans une autre version) échoue avec un message net plutôt que de laisser
remonter une erreur de parsing JSON opaque.
"""
from __future__ import annotations

import time

from rudi_harvest_toolkit.http import session as _session_defaut

_CHEMIN_CATALOGUE = "/api/explore/v2.1/catalog/datasets"
_STATUS_A_REESSAYER = (429, 500, 502, 503, 504)


class OdsError(Exception):
    """Erreur fonctionnelle de lecture du catalogue ODS (message explicite)."""


def _base(url_plateforme: str) -> str:
    return (url_plateforme or "").rstrip("/")


def _url_catalogue(url_plateforme: str) -> str:
    return _base(url_plateforme) + _CHEMIN_CATALOGUE


def _get_avec_retry(session, url: str, params: dict, timeout: int,
                    essais: int = 4, pause: float = 1.0):
    """GET avec backoff exponentiel sur erreurs réseau et statuts transitoires.

    Ne réessaie jamais un 4xx non transitoire (404/403…) : il doit propager tout de
    suite, avec un message clair côté appelant.
    """
    derniere_erreur: Exception | None = None
    for tentative in range(essais):
        try:
            reponse = session.get(url, params=params, timeout=timeout)
        except Exception as e:
            derniere_erreur = e
            if tentative < essais - 1:
                time.sleep(pause * (2 ** tentative))
                continue
            raise OdsError(
                f"échec réseau sur {url} après {essais} tentatives : {e}"
            ) from e
        if reponse.status_code in _STATUS_A_REESSAYER:
            derniere_erreur = OdsError(f"HTTP {reponse.status_code} sur {url}")
            if tentative < essais - 1:
                time.sleep(pause * (2 ** tentative))
                continue
            raise derniere_erreur
        return reponse
    raise OdsError(f"échec sur {url} : {derniere_erreur}")


def _lire_json(reponse, url: str) -> dict:
    """Valide qu'une réponse est bien un catalogue Explore v2.1 exploitable."""
    if reponse.status_code == 404:
        raise OdsError(
            f"API Explore v2.1 introuvable sur {url} (HTTP 404) — la plateforme "
            "n'expose peut-être pas l'API v2.1 (v1 ou version différente non supportée)."
        )
    try:
        reponse.raise_for_status()
    except Exception as e:
        corps = (getattr(reponse, "text", "") or "")[:200]
        raise OdsError(f"HTTP {reponse.status_code} sur {url} : {corps}") from e
    try:
        donnees = reponse.json()
    except Exception as e:
        raise OdsError(
            f"réponse non-JSON sur {url} — l'API Explore v2.1 est probablement absente "
            f"ou dans une autre version ({e})."
        ) from e
    if not isinstance(donnees, dict) or "results" not in donnees or "total_count" not in donnees:
        raise OdsError(
            f"réponse inattendue sur {url} : clés 'results'/'total_count' absentes — "
            "API Explore v2.1 attendue."
        )
    return donnees


def lister_datasets(url_plateforme: str, taille_page: int = 100, *,
                    session=None, timeout: int = 30, taille_max: int | None = None):
    """Pagine le catalogue entier et yield chaque JDD (dict brut de l'API).

    S'arrête à ``total_count`` épuisé, ou dès qu'une page revient vide. Un
    ``total_count`` qui change en cours de route (catalogue modifié pendant la
    pagination) est toléré : on log et on suit les résultats réellement reçus.
    Aucun doublon n'est émis (dédoublonnage par ``dataset_id``).
    """
    session = session or _session_defaut
    url = _url_catalogue(url_plateforme)
    offset = 0
    total_annonce: int | None = None
    deja_vus: set[str] = set()
    nb_emis = 0

    while True:
        donnees = _lire_json(
            _get_avec_retry(session, url, {"limit": taille_page, "offset": offset}, timeout),
            url,
        )
        resultats = donnees.get("results") or []
        total = donnees.get("total_count")

        if total_annonce is None:
            total_annonce = total
        elif total != total_annonce:
            print(f"[ods_client] total_count a changé en cours de pagination "
                  f"({total_annonce} → {total}), on suit les résultats reçus.")
            total_annonce = total

        if not resultats:
            return

        for ds in resultats:
            cle = ds.get("dataset_id") or ds.get("id")
            if cle and cle in deja_vus:
                continue
            if cle:
                deja_vus.add(cle)
            nb_emis += 1
            yield ds

        offset += len(resultats)
        if total_annonce is not None and offset >= total_annonce:
            return
        if taille_max is not None and nb_emis >= taille_max:
            return


def _lire_profil_json(reponse, url: str) -> dict:
    """Valide une réponse JSON quelconque (détail d'un JDD, qui n'a pas de pagination)."""
    if reponse.status_code == 404:
        raise OdsError(f"objet introuvable sur {url} (HTTP 404).")
    try:
        reponse.raise_for_status()
    except Exception as e:
        corps = (getattr(reponse, "text", "") or "")[:200]
        raise OdsError(f"HTTP {reponse.status_code} sur {url} : {corps}") from e
    try:
        donnees = reponse.json()
    except Exception as e:
        raise OdsError(f"réponse non-JSON sur {url} ({e}).") from e
    if not isinstance(donnees, dict):
        raise OdsError(f"réponse inattendue sur {url} : un objet JSON était attendu.")
    return donnees


def obtenir_dataset(url_plateforme: str, dataset_id: str, *,
                    session=None, timeout: int = 30) -> dict:
    """Retourne le détail complet d'un JDD (fiche catalogue v2.1)."""
    session = session or _session_defaut
    url = f"{_base(url_plateforme)}{_CHEMIN_CATALOGUE}/{dataset_id}"
    return _lire_profil_json(_get_avec_retry(session, url, {}, timeout), url)
