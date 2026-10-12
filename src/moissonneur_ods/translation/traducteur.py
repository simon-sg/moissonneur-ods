"""Moteur de traduction déclaratif ``metas.default`` ODS → arguments du pivot RUDI.

``ObjectTranslator``/``FieldTranslator`` (~80 lignes) sont pilotés par
``mapping_ods_rudi.json`` : un ``chemin`` (dot-path) dans la source, éventuellement une
valeur par défaut, un traitement « liste » et un caractère obligatoire. La fonction
``traduire_dataset`` orchestre ensuite thème, licence, médias et appel à
``rudi_harvest_toolkit.pivot.construire_rudi_metadata``.
"""
from __future__ import annotations

import html as html_lib
import json
import os
import re
import uuid
from urllib.parse import urlparse

from rudi_harvest_toolkit.pivot import construire_rudi_metadata

from ..mode import MODE_LIEN, construire_medias, url_fiche_ods
from .frequences import resoudre_update_frequency
from .licences import resoudre_licence
from .themes import THEME_REPLI, choisir_theme

_CHEMIN_MAPPING = os.path.join(os.path.dirname(__file__), "mapping_ods_rudi.json")

_RE_TAGS = re.compile(r"<[^>]+>")
_RE_BR = re.compile(r"(?i)<br\s*/?>")
_RE_FIN_BLOC = re.compile(r"(?i)</(p|div|li|tr|h[1-6]|table)\s*>")
_RE_LI = re.compile(r"(?i)<li[^>]*>")


def html_vers_texte(contenu: str | None) -> str:
    """Convertit une description HTML ODS en texte brut (sans balises).

    Nécessaire car ``construire_rudi_metadata`` échappe le texte avant d'en dériver
    ``summary[].html`` : lui passer du HTML déjà balisé produirait des entités
    échappées affichées telles quelles sur le portail.
    """
    if not contenu:
        return ""
    texte = _RE_BR.sub("\n", contenu)
    texte = _RE_LI.sub("- ", texte)
    texte = _RE_FIN_BLOC.sub("\n\n", texte)
    texte = _RE_TAGS.sub("", texte)
    texte = html_lib.unescape(texte)
    lignes = [ligne.strip() for ligne in texte.splitlines()]
    texte = "\n".join(lignes)
    texte = re.sub(r"\n{3,}", "\n\n", texte)
    return texte.strip()


def _resoudre_chemin(source, chemin: str):
    valeur = source
    for cle in chemin.split("."):
        if isinstance(valeur, dict):
            valeur = valeur.get(cle)
        else:
            return None
        if valeur is None:
            return None
    return valeur


def _normaliser_liste(valeur) -> list:
    if valeur is None:
        return []
    if isinstance(valeur, list):
        return valeur
    return [valeur]


class FieldTranslator:
    """Décrit l'extraction d'un champ de la source ODS vers un nom cible."""

    def __init__(self, chemin: str, defaut=None, liste: bool = False,
                 obligatoire: bool = False, transform=None):
        self.chemin = chemin
        self.defaut = defaut
        self.liste = liste
        self.obligatoire = obligatoire
        self.transform = transform

    def extraire(self, source):
        valeur = _resoudre_chemin(source, self.chemin)
        if valeur is None:
            valeur = self.defaut
        if self.liste:
            valeur = _normaliser_liste(valeur)
        if self.transform:
            valeur = self.transform(valeur)
        if self.obligatoire and not valeur:
            raise ValueError(f"champ obligatoire absent ou vide : {self.chemin!r}")
        return valeur


class ObjectTranslator:
    """Applique un ensemble de ``FieldTranslator`` à une source JSON."""

    def __init__(self, champs: dict, source_racine: str | None = None):
        self.source_racine = source_racine
        self.champs = {
            nom: (spec if isinstance(spec, FieldTranslator) else FieldTranslator(**spec))
            for nom, spec in champs.items()
        }

    def traduire(self, objet: dict) -> dict:
        source = objet
        if self.source_racine:
            source = _resoudre_chemin(objet, self.source_racine) or {}
        return {nom: champ.extraire(source) for nom, champ in self.champs.items()}

    @classmethod
    def depuis_mapping(cls, mapping: dict) -> "ObjectTranslator":
        return cls(mapping.get("champs", {}), mapping.get("source_racine"))


def charger_mapping(chemin: str | None = None) -> dict:
    """Charge le mapping déclaratif (par défaut le JSON livré avec le paquet)."""
    chemin = chemin or _CHEMIN_MAPPING
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


def local_id_pour(url_plateforme: str, dataset_id: str) -> str:
    """UUID déterministe dérivé de ``dataset_id`` + plateforme (aucune collision inter-plateformes)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url_plateforme.rstrip('/')}#{dataset_id}"))


def _synopsis(description: str, titre: str, hote: str, max_len: int = 150) -> str:
    texte = " ".join((description or "").split())
    if len(texte) >= 20:
        if len(texte) > max_len:
            coupe = texte[:max_len].rsplit(" ", 1)[0].rstrip(",;:.")
            return (coupe or texte[:max_len]) + "…"
        return texte
    return f"Jeu de données « {titre} » publié sur {hote}."[:max_len]


def traduire_dataset(dataset: dict, *, url_plateforme: str,
                     producer: dict | None = None, contacts_source: list | None = None,
                     mode: str = MODE_LIEN, theme_repli: str = THEME_REPLI,
                     table_themes: dict | None = None, licence_repli: dict | None = None,
                     table_licences: dict | None = None,
                     dossier_telechargement: str | None = None,
                     taille_max_octets: int | None = None,
                     session_http=None, geography: dict | None = None,
                     collection_tag: str | None = None, mapping: dict | None = None,
                     url_fiche: str | None = None) -> tuple[dict, dict]:
    """Traduit un JDD ODS en ``rudi_metadata`` + ``infos`` (avertissements, médias…).

    Ne publie rien et ne télécharge rien de lourd en mode ``lien``. Retourne un tuple
    ``(rudi_metadata, infos)`` où ``infos['fichiers_locaux']`` liste les fichiers à
    uploader en mode ``copie``.
    """
    mapping = mapping or charger_mapping()
    dataset_id = dataset.get("dataset_id") or dataset.get("id")
    if not dataset_id:
        raise ValueError("JDD ODS sans 'dataset_id' ni 'id'.")
    if not url_plateforme:
        raise ValueError("url_plateforme obligatoire.")

    base = url_plateforme.rstrip("/")
    hote = urlparse(base).hostname or base
    local_id = local_id_pour(base, dataset_id)

    champs = ObjectTranslator.depuis_mapping(mapping).traduire(dataset)
    titre = (champs.get("titre") or dataset_id).strip()
    description = html_vers_texte(champs.get("description_brute"))
    synopsis = _synopsis(description, titre, hote)

    avertissements: list[str] = []

    code_theme, restants = choisir_theme(champs.get("themes"), table_themes, theme_repli)
    themes_non_mappes: list[str] = []
    if code_theme is None:
        themes_non_mappes = list(champs.get("themes") or [])
        code_theme = theme_repli
        if themes_non_mappes:
            avertissements.append(
                f"thèmes ODS non mappés {themes_non_mappes} → repli '{code_theme}'"
            )
        else:
            avertissements.append(f"aucun thème ODS → repli '{code_theme}'")
    # Territoire/référence géographique ODS (libellés, pas de champ RUDI dédié) :
    # versés en mots-clés plutôt que perdus — même logique que les libellés de
    # thème non mappés (`restants`).
    keywords = (list(champs.get("keywords") or []) + restants
               + list(champs.get("territory") or [])
               + list(champs.get("geographic_reference") or []))

    licence, statut_licence = resoudre_licence(
        champs.get("licence_label"), champs.get("licence_url"),
        table_supplementaire=table_licences, repli=licence_repli,
    )
    if statut_licence == "inconnu":
        avertissements.append(
            f"licence ODS non référencée « {champs.get('licence_label')} » → bloc CUSTOM (passthrough)"
        )
    elif statut_licence == "absent":
        avertissements.append("licence ODS absente et aucun repli configuré")
    elif statut_licence == "absent_repli":
        avertissements.append("licence ODS absente → repli configuré appliqué")

    frequence, statut_frequence = resoudre_update_frequency(champs.get("update_frequency"))
    if statut_frequence == "inconnu":
        avertissements.append(
            f"fréquence de mise à jour ODS non référencée « {champs.get('update_frequency')} » "
            "→ repli 'unknown'"
        )

    url_fiche = url_fiche or url_fiche_ods(base, dataset_id)
    producer = producer or {"organization_name": champs.get("publisher") or hote}

    kwargs_taille = {} if taille_max_octets is None else {"taille_max_octets": taille_max_octets}
    medias, fichiers, avert_medias = construire_medias(
        dataset, local_id, base, mode,
        dossier_telechargement=dossier_telechargement,
        session=session_http, **kwargs_taille,
    )
    avertissements.extend(avert_medias)

    rudi_metadata = construire_rudi_metadata(
        local_id=local_id,
        titre=titre,
        synopsis=synopsis,
        description=description or titre,
        theme=code_theme,
        keywords=keywords,
        producer=producer,
        url_source=url_fiche,
        url_fiche=url_fiche,
        medias=medias,
        contacts_source=contacts_source or [],
        date_source=champs.get("modified"),
        metadata_source_label=hote,
        licence=licence,
        geography=geography,
        ajouter_medias_source=True,
    )
    if champs.get("language"):
        rudi_metadata["resource_languages"] = [champs["language"]]
    if frequence:
        rudi_metadata["dataset_update_frequency"] = frequence
    if collection_tag:
        rudi_metadata["collection_tag"] = collection_tag

    if statut_licence == "absent" and rudi_metadata["access_condition"].get("licence") is not None:
        avertissements.append(
            "aucun repli de licence configuré — la licence par défaut du pivot "
            "(Etalab) reste appliquée ; configurez --licence-repli pour la maîtriser."
        )

    volumetrie = 0
    for chemin in fichiers:
        try:
            volumetrie += os.path.getsize(chemin)
        except OSError:
            pass

    infos = {
        "dataset_id": dataset_id,
        "local_id": local_id,
        "titre": titre,
        "theme": code_theme,
        "themes_non_mappes": themes_non_mappes,
        "licence_statut": statut_licence,
        "licence_label": champs.get("licence_label"),
        "avertissements": avertissements,
        "fichiers_locaux": fichiers,
        "nb_medias_ods": len(medias) - sum(
            1 for m in medias if m.get("media_name") in ("source-data", "source-metadata")
        ),
        "nb_medias": len(medias),
        "volumetrie_octets": volumetrie,
    }
    return rudi_metadata, infos
