"""CLI du moissonneur Opendatasoft → nœud RUDI.

    python3 -m moissonneur_ods --url https://data.rennesmetropole.fr \
        --mode=lien --rudi-conf conf/rudi_node.json [--dry-run] [--menage]

``--menage`` est une commande séparée : elle n'est jamais combinée silencieusement avec
une moisson normale.
"""
from __future__ import annotations

import argparse
import os

from rudi_harvest_toolkit.pivot import LICENCE_ETALAB
from rudi_harvest_toolkit.publish import publier_si_configue
from rudi_harvest_toolkit.rudi_client import charger_conf_rudi

from . import state
from .decouvertes import Decouvertes
from .mode import MODES, MODE_COPIE, entrees_donnees, estimer_taille, url_fiche_ods
from .ods_client import OdsError, lister_datasets
from .orphelins import collection_tag_pour, menage_orphelins
from .translation.licences import LICENCE_ODBL
from .translation.themes import THEME_REPLI, THEMES_RUDI, charger_mapping_plateforme
from .translation.traducteur import local_id_pour, traduire_dataset

_LICENCES_REPLI = {"none": None, "etalab": LICENCE_ETALAB, "odbl": LICENCE_ODBL}


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        prog="moissonneur_ods",
        description="Moissonne une plateforme Opendatasoft vers un nœud RUDI.",
    )
    parseur.add_argument("--url", required=True,
                         help="URL de la plateforme ODS (ex. https://data.rennesmetropole.fr)")
    parseur.add_argument("--mode", choices=MODES, default="lien",
                         help="lien : médias SERVICE vers ODS ; copie : téléchargement + upload FILE")
    parseur.add_argument("--rudi-conf", default=None,
                         help="chemin du JSON de configuration du nœud RUDI (url/usr/pwd)")
    parseur.add_argument("--dry-run", action="store_true",
                         help="traduit tout le catalogue sans publier ni télécharger de lourd")
    parseur.add_argument("--menage", action="store_true",
                         help="détecte et supprime (après confirmation) les orphelins de ce moissonneur")
    parseur.add_argument("--conf-themes", default=None,
                         help="table JSON {libellé ODS: thème RUDI} propre à la plateforme")
    parseur.add_argument("--theme-repli", default=THEME_REPLI, choices=sorted(THEMES_RUDI),
                         help=f"thème appliqué si aucun libellé ODS n'est mappé (défaut : {THEME_REPLI})")
    parseur.add_argument("--licence-repli", choices=sorted(_LICENCES_REPLI), default="none",
                         help="licence appliquée si ODS n'en fournit aucune (défaut : aucune)")
    parseur.add_argument("--taille-page", type=int, default=100,
                         help="taille de page de l'API Explore (défaut 100)")
    parseur.add_argument("--taille-max-mo", type=int, default=500,
                         help="taille maximale d'un fichier en mode copie (défaut 500 Mo)")
    parseur.add_argument("--dossier-telechargement", default=None,
                         help="répertoire des fichiers téléchargés en mode copie")
    parseur.add_argument("--rapport", default="conf/theme_mapping_decouvertes.json",
                         help="fichier JSON du rapport de découvertes thèmes/licences")
    parseur.add_argument("--dossier-etat", default="conf",
                         help="répertoire du fichier d'état incrémental")
    parseur.add_argument("--limite", type=int, default=None,
                         help="nombre maximum de JDD traités (utile pour un run de test)")
    return parseur


def _licence_repli(nom: str):
    bloc = _LICENCES_REPLI.get(nom)
    return dict(bloc) if bloc else None


def _local_ids_du_catalogue(url_plateforme: str, *, taille_page: int, limite: int | None) -> set[str]:
    ids = set()
    for dataset in lister_datasets(url_plateforme, taille_page=taille_page, taille_max=limite):
        dataset_id = dataset.get("dataset_id") or dataset.get("id")
        if dataset_id:
            ids.add(local_id_pour(url_plateforme, dataset_id))
    return ids


def executer_menage(args) -> int:
    conf = charger_conf_rudi(args.rudi_conf) if args.rudi_conf else None
    if not conf:
        print("Configuration du nœud RUDI absente ou introuvable (--rudi-conf) — ménage impossible.")
        return 2
    print(f"Collecte du catalogue ODS {args.url} pour délimiter le périmètre…")
    local_ids = _local_ids_du_catalogue(args.url, taille_page=args.taille_page, limite=args.limite)
    print(f"  {len(local_ids)} JDD actifs dans le catalogue ODS.")
    menage_orphelins(conf, args.url, local_ids, dry_run=args.dry_run)
    return 0


def _estimer_volumetrie(dataset: dict, url_plateforme: str, session) -> int:
    total = 0
    for entree in entrees_donnees(dataset, url_plateforme):
        taille = estimer_taille(entree["url"], session=session)
        if taille:
            total += taille
    return total


def executer_moisson(args) -> int:
    conf = charger_conf_rudi(args.rudi_conf) if args.rudi_conf else None
    if not args.dry_run and not conf:
        print("Configuration du nœud RUDI absente (--rudi-conf) — moisson impossible hors --dry-run.")
        return 2

    table_themes = charger_mapping_plateforme(args.conf_themes)
    licence_repli = _licence_repli(args.licence_repli)
    taille_max_octets = args.taille_max_mo * 1024 * 1024
    collection_tag = collection_tag_pour(args.url)

    chemin_etat = state.chemin_etat(args.url, args.dossier_etat)
    etat = state.charger_etat(chemin_etat)
    decouvertes = Decouvertes(args.rapport)

    from rudi_harvest_toolkit.http import session as session_http

    stats = {"total": 0, "traites": 0, "inchanges": 0, "publies": 0, "echecs": 0,
             "sans_export": 0, "volumetrie": 0}

    print(f"Moisson de {args.url} — mode={args.mode}"
          + (" (--dry-run)" if args.dry_run else "") + "…")

    for dataset in lister_datasets(args.url, taille_page=args.taille_page, taille_max=args.limite):
        stats["total"] += 1
        dataset_id = dataset.get("dataset_id") or dataset.get("id")
        modified = (dataset.get("metas") or {}).get("default", {}).get("modified")
        change = state.dataset_a_change(etat, dataset_id, modified)

        if not args.dry_run and not change:
            stats["inchanges"] += 1
            continue

        try:
            rudi_metadata, infos = traduire_dataset(
                dataset, url_plateforme=args.url, mode=args.mode,
                theme_repli=args.theme_repli, table_themes=table_themes,
                licence_repli=licence_repli, taille_max_octets=taille_max_octets,
                dossier_telechargement=args.dossier_telechargement,
                session_http=session_http, collection_tag=collection_tag,
            )
        except Exception as e:
            stats["echecs"] += 1
            print(f"  [ERREUR] {dataset_id} : {e}")
            continue

        stats["traites"] += 1
        if infos["nb_medias_ods"] == 0:
            stats["sans_export"] += 1
        for avertissement in infos["avertissements"]:
            print(f"  [AVERTISSEMENT] {dataset_id} : {avertissement}")

        if args.dry_run:
            for libelle in infos["themes_non_mappes"]:
                decouvertes.enregistrer("themes", libelle)
            if infos["licence_statut"] == "inconnu":
                decouvertes.enregistrer("licences", infos["licence_label"])
            if args.mode == MODE_COPIE:
                stats["volumetrie"] += _estimer_volumetrie(dataset, args.url, session_http)
            continue

        publie = publier_si_configue(conf, rudi_metadata, infos["fichiers_locaux"])
        if publie:
            stats["publies"] += 1
            state.enregistrer_jdd(etat, dataset_id, modified,
                                  local_id=infos["local_id"], titre=infos["titre"])
        else:
            stats["echecs"] += 1

    print("\n=== Résumé ===")
    print(f"  JDD parcourus          : {stats['total']}")
    print(f"  JDD traités            : {stats['traites']}")
    if not args.dry_run:
        print(f"  JDD inchangés (sautés) : {stats['inchanges']}")
        print(f"  Publiés                : {stats['publies']}")
    print(f"  Sans export/pièce jointe : {stats['sans_export']}")
    print(f"  Échecs                 : {stats['echecs']}")
    if args.dry_run and args.mode == MODE_COPIE:
        print(f"  Volumétrie estimée     : {stats['volumetrie'] / 1_048_576:.1f} Mo")
    if args.dry_run:
        _afficher_decouvertes(decouvertes)
        decouvertes.sauvegarder()
        print(f"\n  Rapport de découvertes écrit : {args.rapport}")
    else:
        state.sauvegarder_etat(chemin_etat, etat)
        print(f"\n  État incrémental écrit : {chemin_etat}")
    return 0


def _afficher_decouvertes(decouvertes: Decouvertes) -> None:
    themes = decouvertes.non_ignores("themes")
    licences = decouvertes.non_ignores("licences")
    print("\n  Libellés à traiter (non ignorés) :")
    print(f"    thèmes   : {themes or 'aucun'}")
    print(f"    licences : {licences or 'aucune'}")


def main(argv=None) -> int:
    args = construire_parseur().parse_args(argv)
    try:
        if args.menage:
            return executer_menage(args)
        return executer_moisson(args)
    except OdsError as e:
        print(f"ERREUR ODS : {e}")
        return 3
    except KeyboardInterrupt:
        print("\nInterrompu.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
