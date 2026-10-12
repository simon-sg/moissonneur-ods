"""Construction des médias selon le mode global du run : ``lien`` ou ``copie``.

- ``lien`` : chaque pièce jointe / export alternatif ODS devient, par défaut, un
  média ``FILE`` **hybride** : l'empreinte (SHA-256) et la taille sont calculées en
  lisant le fichier une fois en flux (jamais écrit sur disque, jamais stocké sur
  notre nœud), mais ``connector.url`` reste l'URL distante ODS — ça donne un vrai
  bouton télécharger côté portail (réservé à ``media_type: FILE``, voir
  ``data-set-infos.component.html``/``detail-functions.ts`` de
  ``rudi-portal-source``) sans jamais copier la donnée. Accepté avec l'utilisateur
  le 2026-10-12 : le compromis est que le checksum devient obsolète si la donnée
  ODS change après la moisson sans nouveau run (RUDI n'a aucun moyen de le savoir,
  puisqu'il ne recalcule jamais lui-même ce qu'il n'a pas stocké). Repli en
  ``SERVICE`` simple (pas de bouton, juste l'URL en texte copiable) si le type MIME
  n'est pas dans l'énumération fermée du nœud (``FileTypes.js``) ou si la lecture
  échoue/dépasse la taille max — jamais d'échec de run pour ça.
- ``copie`` : téléchargement local (garde-fou de taille) puis upload ``FILE`` via
  ``rudi_harvest_toolkit.rudi_client.publier_dataset``.

Un JDD sans pièce jointe ni export — ou dont un export est trop volumineux / en
échec de téléchargement/lecture — ne fait jamais échouer le run : il retombe en
média ``SERVICE`` (repli automatique, loggué). Les médias ``FILE`` sont toujours
placés en tête de ``available_formats``. En mode copie, ``publier_dataset`` aligne
``fichiers_filtres[i]`` sur ``available_formats[i]`` et uploade ; en mode lien, le
FILE hybride est déjà complet (``file_size``/``checksum`` posés ici) —
``publier_dataset`` le reconnaît et ne tente jamais de l'uploader (voir
``rudi_harvest_toolkit.rudi_client._media_file_deja_complet``).
"""
from __future__ import annotations

import hashlib
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

_ALGO_EMPREINTE = "SHA-256"

# Miroir de l'énumération fermée côté nœud (rudi-catalog, definitions/thesaurus/
# FileTypes.js) : un FILE hors de cette liste est rejeté par le nœud à la
# publication — vérifié avant toute lecture distante, jamais après.
_TYPES_FICHIER_ACCEPTES = frozenset({
    "application/epub+zip", "application/geo+json", "application/geopackage+sqlite3",
    "application/graphql", "application/gzip", "application/javascript", "application/json",
    "application/ld+json", "application/msword", "application/netcdf", "application/octet-stream",
    "application/pdf", "application/sql", "application/vnd.api+json", "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint", "application/vnd.oasis.opendocument.presentation",
    "application/vnd.oasis.opendocument.spreadsheet", "application/vnd.oasis.opendocument.text",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/x-7z-compressed", "application/x-bzip", "application/x-bzip2",
    "application/x-executable", "application/x-tar", "application/x-www-form-urlencoded",
    "application/xml", "application/zip", "application/zstd",
    "audio/aac", "audio/m4a", "audio/mpeg", "audio/ogg", "audio/wav", "audio/webm",
    "font/otf", "font/ttf",
    "image/apng", "image/bmp", "image/flif", "image/gif", "image/jpeg", "image/png",
    "image/tiff", "image/vnd.microsoft.icon", "image/webp", "image/x-mng",
    "multipart/form-data",
    "text/css", "text/csv", "text/html", "text/markdown", "text/php", "text/plain",
    "text/x-yaml", "text/xml",
    "video/3gpp", "video/mp4", "video/mpeg", "video/ogg", "video/quicktime", "video/webm",
    "video/x-matroska", "video/x-ms-wmv", "video/x-msvideo",
})


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
    """UUID déterministe (même entrée → même id, utile à l'incrémental), mais dont
    les nibbles version/variant sont forcés pour ressembler à un vrai UUID v4 —
    ``rudi_node_write`` (``check_is_uuid4``, client Python, pas le nœud lui-même
    qui valide juste un format générique) exige strictement ``UUID.version == 4``,
    ce qu'un vrai ``uuid5`` ne satisfait jamais. Sans conséquence pour un média
    SERVICE (son id est de toute façon régénéré par ``publier_dataset`` avant
    publication) ; nécessaire en revanche pour un FILE hybride (voir
    ``_media_file_distant``), qui n'est jamais régénéré."""
    brut = uuid.uuid5(uuid.NAMESPACE_URL, f"{local_id}/ods-media/{cle}")
    octets = bytearray(brut.bytes)
    octets[6] = (octets[6] & 0x0F) | 0x40  # version = 4
    octets[8] = (octets[8] & 0x3F) | 0x80  # variant = 10xx
    return str(uuid.UUID(bytes=bytes(octets)))


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


def _empreinte_distante(url: str, taille_max: int, session, timeout: int = 120) -> dict:
    """Lit une ressource distante en flux pour calculer son empreinte SHA-256 et sa
    taille — jamais écrite sur disque, jamais stockée. Retourne ``{hash, taille}``
    ou ``{erreur}`` (mêmes codes que ``_telecharger`` : ``trop_gros``/``reseau``)."""
    try:
        reponse = session.get(url, stream=True, timeout=timeout, allow_redirects=True)
        reponse.raise_for_status()
    except Exception as e:
        return {"erreur": "reseau", "detail": str(e)}
    longueur = reponse.headers.get("Content-Length")
    if longueur and longueur.isdigit() and int(longueur) > taille_max:
        return {"erreur": "trop_gros", "taille": int(longueur)}
    empreinte = hashlib.sha256()
    ecrits = 0
    try:
        for morceau in reponse.iter_content(chunk_size=65536):
            if not morceau:
                continue
            ecrits += len(morceau)
            if ecrits > taille_max:
                return {"erreur": "trop_gros", "taille": ecrits}
            empreinte.update(morceau)
    except Exception as e:
        return {"erreur": "reseau", "detail": str(e)}
    return {"hash": empreinte.hexdigest(), "taille": ecrits}


def _media_file_distant(media_id: str, nom: str, caption: str, url: str,
                        mimetype: str, empreinte: dict) -> dict:
    """Média ``FILE`` « hybride » : empreinte/taille réelles, mais ``connector.url``
    reste l'URL distante — jamais uploadé, jamais stocké par nous (voir docstring
    du module)."""
    return {
        "media_id": media_id,
        "media_type": "FILE",
        "media_name": nom,
        "media_caption": caption,
        "connector": {"url": url, "interface_contract": "dwnl"},
        "file_type": mimetype,
        "file_size": empreinte["taille"],
        "checksum": {"algo": _ALGO_EMPREINTE, "hash": empreinte["hash"]},
        "file_storage_status": "available",
    }


def _exports_standards(dataset: dict, url_plateforme: str, dataset_id: str) -> list[dict]:
    """Entrées dérivées des exports standards Opendatasoft (``/exports/{format}``),
    en repli quand ``alternative_exports`` est vide — ce qui est le cas pour la
    quasi-totalité des JDD réels (vérifié sur plusieurs plateformes) :
    ``attachments``/``alternative_exports`` sont des champs **curatés par le
    producteur** (documents annexes, formats alternatifs explicitement choisis),
    rarement remplis, alors que l'API Explore v2.1 expose systématiquement — pour
    tout JDD avec des enregistrements — un export CSV/GeoJSON/etc. qui est déjà
    un **vrai fichier plat téléchargeable** (``Content-Disposition: attachment``,
    ``Content-Type`` correct), pas une réponse d'API paginée. Toujours CSV ; +
    GeoJSON si le JDD a une composante géographique (``features`` contient
    ``"geo"``, vérifié directement sur l'API ODS)."""
    if not dataset.get("has_records"):
        return []
    base = f"{url_plateforme.rstrip('/')}/api/explore/v2.1/catalog/datasets/{dataset_id}/exports"
    formats = [("csv", "text/csv", "Export CSV")]
    if "geo" in (dataset.get("features") or []):
        formats.append(("geojson", "application/geo+json", "Export GeoJSON"))
    return [{"url": f"{base}/{fmt}", "titre": titre, "mimetype": mime, "origine": "export_standard"}
           for fmt, mime, titre in formats]


def entrees_donnees(dataset: dict, url_plateforme: str) -> list[dict]:
    """Entrées de données d'un JDD ODS : pièces jointes, puis exports alternatifs
    (ou, à défaut, exports standards Opendatasoft — voir ``_exports_standards``)."""
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
    exports_curates = [exp for exp in (dataset.get("alternative_exports") or [])
                       if isinstance(exp, dict)]
    for exp in exports_curates:
        url = _url_absolue(exp.get("url") or "", url_plateforme)
        if not url:
            continue
        entrees.append({
            "url": url,
            "titre": exp.get("title") or exp.get("id") or _nom_depuis_url(url),
            "mimetype": exp.get("mimetype"),
            "origine": "alternative_export",
        })
    if not exports_curates:
        dataset_id = dataset.get("dataset_id") or dataset.get("id")
        if dataset_id:
            entrees.extend(_exports_standards(dataset, url_plateforme, dataset_id))
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
                      session=None, telecharger=None,
                      empreinte_distante=None) -> tuple[list[dict], list[str], list[str]]:
    """Retourne ``(medias, fichiers_locaux, avertissements)`` pour un JDD.

    ``medias`` : entrées ``available_formats`` (FILE d'abord, puis SERVICE).
    ``fichiers_locaux`` : chemins des FILE **en mode copie seulement** — un FILE
    hybride en mode lien (voir docstring du module) n'a pas de fichier local, il
    n'en a jamais eu besoin.
    """
    if mode not in MODES:
        raise ValueError(f"Mode inconnu : {mode!r} (attendu : {MODES})")
    session = session or _session_defaut
    telecharger = telecharger or _telecharger
    empreinte_distante = empreinte_distante or _empreinte_distante

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
            mimetype = entree.get("mimetype")
            if mimetype in _TYPES_FICHIER_ACCEPTES:
                resultat = empreinte_distante(entree["url"], taille_max_octets, session)
                if not resultat.get("erreur"):
                    file_medias.append(_media_file_distant(
                        media_id, nom, caption, entree["url"], mimetype, resultat))
                    continue
                raison = resultat["erreur"]
                if raison == "trop_gros":
                    avertissements.append(
                        f"{nom} : trop volumineux pour l'empreinte "
                        f"({resultat.get('taille', '?')} octets) — repli en SERVICE sans bouton "
                        f"télécharger, vers {entree['url']}"
                    )
                else:
                    avertissements.append(
                        f"{nom} : échec de lecture pour l'empreinte ({raison}) — repli en "
                        f"SERVICE sans bouton télécharger, vers {entree['url']}"
                    )
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
