"""Construction des médias selon le mode global du run : ``lien`` ou ``copie``.

- ``lien`` : chaque pièce jointe / export alternatif ODS devient un média ``SERVICE``
  pointant vers l'URL ODS — aucun téléchargement ;
- ``copie`` : téléchargement local (garde-fou de taille) puis upload ``FILE`` via
  ``rudi_harvest_toolkit.rudi_client.publier_dataset``.

Un JDD sans pièce jointe ni export — ou dont un export est trop volumineux / en
échec de téléchargement — ne fait jamais échouer le run : il retombe en média
``SERVICE`` (repli automatique, loggué). Les médias ``FILE`` sont toujours placés en
tête de ``available_formats`` : ``publier_dataset`` aligne ``fichiers_filtres[i]`` sur
``available_formats[i]``.
"""
from __future__ import annotations

import os
import tempfile
import unicodedata
import uuid
from urllib.parse import unquote, urlparse

from rudi_harvest_toolkit.http import session as _session_defaut

MODE_LIEN = "lien"
MODE_COPIE = "copie"
MODES = (MODE_LIEN, MODE_COPIE)

TAILLE_MAX_DEFAUT = 500 * 1024 * 1024  # 500 Mo — même limite que le nœud RUDI.

_URL_PLACEHOLDER = "À_RENSEIGNER_APRES_DEPOT_SUR_NOEUD"


def url_fiche_ods(url_plateforme: str, dataset_id: str) -> str:
    """URL de la fiche publique du JDD sur la plateforme Opendatasoft."""
    return f"{url_plateforme.rstrip('/')}/explore/dataset/{dataset_id}/"


def _url_absolue(url: str, url_plateforme: str) -> str:
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return url_plateforme.rstrip("/") + url
    return url_plateforme.rstrip("/") + "/" + url


def _nom_depuis_url(url: str) -> str:
    chemin = urlparse(url).path
    nom = unquote(os.path.basename(chemin)) or "donnees"
    return nom


def _nom_media(base: str, deja_utilises: set[str]) -> str:
    ascii_nom = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    ascii_nom = ascii_nom.replace(" ", "_") or "donnees"
    nom = ascii_nom
    compteur = 1
    while nom in deja_utilises:
        racine, ext = os.path.splitext(ascii_nom)
        nom = f"{racine}_{compteur}{ext}"
        compteur += 1
    deja_utilises.add(nom)
    return nom


def _media_id(local_id: str, cle: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{local_id}/ods-media/{cle}"))


def _media_service(media_id: str, nom: str, caption: str, url: str) -> dict:
    return {
        "media_id": media_id,
        "media_type": "SERVICE",
        "media_name": nom,
        "media_caption": caption,
        "connector": {"url": url, "interface_contract": "dwnl"},
    }


def _media_file(media_id: str, nom: str, caption: str) -> dict:
    return {
        "media_id": media_id,
        "media_type": "FILE",
        "media_name": nom,
        "media_caption": caption,
        "connector": {"url": _URL_PLACEHOLDER, "interface_contract": "dwnl"},
    }


def entrees_donnees(dataset: dict, url_plateforme: str) -> list[dict]:
    """Entrées de données d'un JDD ODS : pièces jointes puis exports alternatifs."""
    entrees: list[dict] = []
    for att in dataset.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        url = _url_absolue(att.get("url") or "", url_plateforme)
        if not url:
            continue
        entrees.append({
            "url": url,
            "titre": att.get("title") or att.get("id") or _nom_depuis_url(url),
            "mimetype": att.get("mimetype"),
            "origine": "attachment",
        })
    for exp in dataset.get("alternative_exports") or []:
        if not isinstance(exp, dict):
            continue
        url = _url_absolue(exp.get("url") or "", url_plateforme)
        if not url:
            continue
        entrees.append({
            "url": url,
            "titre": exp.get("title") or exp.get("id") or _nom_depuis_url(url),
            "mimetype": exp.get("mimetype"),
            "origine": "alternative_export",
        })
    return entrees


def _telecharger(url: str, dossier: str, nom: str, taille_max: int,
                 session, timeout: int = 120) -> dict:
    """Télécharge un fichier avec garde-fou de taille. Retourne ``{chemin}`` ou ``{erreur}``."""
    if not dossier:
        dossier = tempfile.mkdtemp(prefix="moissonneur-ods-")
    os.makedirs(dossier, exist_ok=True)
    chemin = os.path.join(dossier, nom)
    try:
        reponse = session.get(url, stream=True, timeout=timeout, allow_redirects=True)
        try:
            reponse.raise_for_status()
        except Exception as e:
            return {"erreur": f"HTTP {getattr(reponse, 'status_code', '?')}", "detail": str(e)}
        longueur = reponse.headers.get("Content-Length")
        if longueur and longueur.isdigit() and int(longueur) > taille_max:
            return {"erreur": "trop_gros", "taille": int(longueur)}
        ecrits = 0
        with open(chemin, "wb") as f:
            for morceau in reponse.iter_content(chunk_size=65536):
                if not morceau:
                    continue
                ecrits += len(morceau)
                if ecrits > taille_max:
                    f.close()
                    os.remove(chemin)
                    return {"erreur": "trop_gros", "taille": ecrits}
                f.write(morceau)
        return {"chemin": chemin, "taille": ecrits}
    except Exception as e:
        if os.path.exists(chemin):
            try:
                os.remove(chemin)
            except OSError:
                pass
        return {"erreur": "reseau", "detail": str(e)}


def estimer_taille(url: str, session=None, timeout: int = 30) -> int | None:
    """Taille (octets) d'une ressource via HEAD, ou ``None`` si indéterminée.

    Utilisé uniquement pour la volumétrie estimée du ``--dry-run`` : aucun
    téléchargement de contenu.
    """
    session = session or _session_defaut
    try:
        reponse = session.head(url, timeout=timeout, allow_redirects=True)
        longueur = reponse.headers.get("Content-Length")
        if longueur and longueur.isdigit():
            return int(longueur)
    except Exception:
        return None
    return None


def construire_medias(dataset: dict, local_id: str, url_plateforme: str, mode: str, *,
                      dossier_telechargement: str | None = None,
                      taille_max_octets: int = TAILLE_MAX_DEFAUT,
                      session=None, telecharger=None) -> tuple[list[dict], list[str], list[str]]:
    """Retourne ``(medias, fichiers_locaux, avertissements)`` pour un JDD.

    ``medias`` : entrées ``available_formats`` (FILE d'abord, puis SERVICE).
    ``fichiers_locaux`` : chemins des FILE, dans le même ordre que les FILE de ``medias``.
    """
    if mode not in MODES:
        raise ValueError(f"Mode inconnu : {mode!r} (attendu : {MODES})")
    session = session or _session_defaut
    telecharger = telecharger or _telecharger

    entrees = entrees_donnees(dataset, url_plateforme)
    noms_utilises: set[str] = set()
    file_medias: list[dict] = []
    service_medias: list[dict] = []
    fichiers: list[str] = []
    avertissements: list[str] = []

    for entree in entrees:
        nom = _nom_media(_nom_depuis_url(entree["url"]), noms_utilises)
        caption = entree["titre"] or nom
        media_id = _media_id(local_id, entree["url"])

        if mode == MODE_LIEN:
            service_medias.append(_media_service(media_id, nom, caption, entree["url"]))
            continue

        resultat = telecharger(entree["url"], dossier_telechargement, nom,
                               taille_max_octets, session)
        if resultat.get("erreur"):
            raison = resultat["erreur"]
            if raison == "trop_gros":
                avertissements.append(
                    f"{nom} : trop volumineux ({resultat.get('taille', '?')} octets) — "
                    f"repli en SERVICE vers {entree['url']}"
                )
            else:
                avertissements.append(
                    f"{nom} : échec du téléchargement ({raison}) — repli en SERVICE "
                    f"vers {entree['url']}"
                )
            service_medias.append(_media_service(media_id, nom, caption, entree["url"]))
        else:
            file_medias.append(_media_file(media_id, nom, caption))
            fichiers.append(resultat["chemin"])

    return file_medias + service_medias, fichiers, avertissements
