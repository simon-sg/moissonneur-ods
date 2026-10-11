"""CLI du moissonneur Opendatasoft → nœud RUDI.

    python3 -m moissonneur_ods --url https://data.rennesmetropole.fr \
        --mode=lien --rudi-conf conf/rudi_node.json [--dry-run] [--menage]

``--menage``, ``--raccorder-portail`` et ``--renvoyer-ressources`` sont des commandes
séparées : elles ne sont jamais combinées silencieusement avec une moisson normale.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from rudi_harvest_toolkit.pivot import LICENCE_ETALAB
from rudi_harvest_toolkit.publish import publier_si_configue
from rudi_harvest_toolkit.rudi_client import (
    charger_conf_rudi,
    lister_organisations,
    toutes_metadonnees_rudi,
    url_catalog,
)

from . import state
from .decouvertes import Decouvertes
from .mode import MODES, MODE_COPIE, entrees_donnees, estimer_taille, url_fiche_ods
from .ods_client import OdsError, lister_datasets
from .orphelins import collection_tag_pour, menage_orphelins
from .portail_client import (
    ConfigRaccordement,
    ErreurRaccordement,
    generer_ini_portail,
    generer_sql_upsert_organisations,
    raccorder,
    renvoyer_ressources,
)
from .translation.licences import LICENCE_ODBL
from .translation.themes import THEME_REPLI, THEMES_RUDI, charger_mapping_plateforme
from .translation.traducteur import local_id_pour, traduire_dataset

_LICENCES_REPLI = {"none": None, "etalab": LICENCE_ETALAB, "odbl": LICENCE_ODBL}


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        prog="moissonneur_ods",
        description="Moissonne une plateforme Opendatasoft vers un nœud RUDI.",
    )
    parseur.add_argument("--url", required=False,
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
    _ajouter_args_raccordement(parseur)
    return parseur


def _ajouter_args_raccordement(parseur: argparse.ArgumentParser) -> None:
    """Arguments du raccordement au portail ROOB (never combiné à une moisson)."""
    groupe = parseur.add_argument_group("Raccordement au portail ROOB (commandes séparées)")
    groupe.add_argument("--raccorder-portail", action="store_true",
                        help="déclare producteur + nœud sur un portail ROOB et crée le compte ROBOT ACL "
                             "(jamais combiné à une moisson normale)")
    groupe.add_argument("--scenario", choices=["neuf", "peuple"], default=None,
                        help="neuf : nœud vide avant tout harvest ; peuple : nœud déjà peuplé "
                             "(rattrapage SQL des organisations + --renvoyer-ressources)")
    groupe.add_argument("--portail-url", default=None,
                        help="URL du portail ROOB (ex. http://rudi.localhost)")
    groupe.add_argument("--provider-code", default=None,
                        help="code du producteur à chercher/créer sur le portail (ex. MOISS_ODS_RM)")
    groupe.add_argument("--provider-label", default=None,
                        help="libellé du producteur à chercher/créer sur le portail")
    groupe.add_argument("--node-catalog-url", default=None,
                        help="URL du catalogue du nœud cible vue depuis la machine qui héberge les services "
                             "portail (ex. http://172.17.0.1:3030/catalog/v1). Doit pointer exactement sur "
                             "/catalog/v1 — jamais /api/v1 (répond 308)")
    groupe.add_argument("--admin-user", default="rudi",
                        help="compte admin du portail (défaut : rudi)")
    groupe.add_argument("--admin-pass-env", default="RUDI_PORTAL_ADMIN_PASS",
                        help="variable d'environnement contenant le mot de passe admin (défaut : "
                             "RUDI_PORTAL_ADMIN_PASS) ; repli sur saisie masquée si absente")
    groupe.add_argument("--force", action="store_true",
                        help="outrepasse les garde-fous anti-double-raccordement / nœud-non-vide")
    groupe.add_argument("--renvoyer-ressources", action="store_true",
                        help="pousse les ressources du nœud vers le portail (Kalim) en direct — à lancer "
                             "après --raccorder-portail --scenario peuple, une fois le .ini appliqué et le "
                             "script SQL des organisations exécuté")


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


def _chemin_conf(args, nom_fichier: str) -> str:
    """Chemin d'un artefact du raccordement (credentials/.ini/SQL) dans le même
    répertoire que la conf nœud (comportement par défaut : ``conf``)."""
    dossier = os.path.dirname(args.rudi_conf) or "conf"
    return os.path.join(dossier, nom_fichier)


def _mot_de_passe_admin(variable: str) -> str:
    """Mot de passe admin portail : variable d'environnement, sinon saisie masquée
    (jamais écrit dans un fichier par l'outil)."""
    mot = os.environ.get(variable)
    if mot:
        return mot
    return getpass.getpass(f"Mot de passe admin portail (ou variable d'environnement {variable}) : ")


def executer_raccordement(args, confirmer=input) -> int:
    """Raccorde le nœud cible à un portail ROOB (commandes séparées)."""
    conf = charger_conf_rudi(args.rudi_conf) if args.rudi_conf else None
    if not conf:
        print("Configuration du nœud RUDI absente ou introuvable (--rudi-conf) — raccordement impossible.")
        return 2
    if not args.scenario:
        print("Scénario requis : --scenario neuf (nœud vide) ou peuple (nœud déjà peuplé).")
        return 2
    if not args.portail_url:
        print("URL du portail requise : --portail-url (ex. http://rudi.localhost).")
        return 2
    if not args.provider_code or not args.provider_label:
        print("--provider-code et --provider-label sont requis pour identifier le producteur sur le portail.")
        return 2

    bloc_portail = conf.get("portail") or {}
    if bloc_portail.get("mode") == "portail":
        if not args.force:
            print(f"Refus : nœud déjà raccordé au portail {bloc_portail.get('portal_url', '?')} "
                  f"le {bloc_portail.get('raccorde_le', '?')}.")
            print("  Pour forcer un nouveau raccordement : --force")
            return 1
        print("(--force) Raccordement relancé malgré le portail déjà enregistré.")

    admin_pass = _mot_de_passe_admin(args.admin_pass_env)

    # Étape de confirmation visuelle que c'est bien le bon portail, avant toute action.
    try:
        r = requests.get(args.portail_url, timeout=30)
        print(f"Portail atteint : HTTP {r.status_code}")
        print(r.text[:400])
    except Exception as e:
        print(f"AVERTISSEMENT : portail injoignable ({e}) — vérifier --portail-url.")

    node_catalog_url = args.node_catalog_url or (url_catalog(conf) + "/v1")

    if args.scenario == "neuf":
        nb = len(toutes_metadonnees_rudi(conf))
        if nb != 0:
            if not args.force:
                print(f"Refus : le nœud contient déjà {nb} ressource(s) — le scénario 'neuf' "
                      "exige un nœud vide (--force pour passer outre).")
                return 1
            print(f"(--force) Scénario 'neuf' poursuivi malgré {nb} ressource(s) déjà présente(s).")

    print("\n=== Récapitulatif du raccordement ===")
    print(f"  Portail       : {args.portail_url}")
    print(f"  Producteur    : {args.provider_code} — {args.provider_label}")
    print(f"  Nœud déclaré  : {node_catalog_url}")
    print("    → doit pointer exactement sur /catalog/v1 (jamais /api/v1, qui répond 308)")
    print(f"  Scénario      : {args.scenario}")
    saisie = confirmer("\nTaper 'RACCORDER' pour continuer (ou Entrée pour annuler) : ").strip()
    if saisie != "RACCORDER":
        print("Raccordement annulé.")
        return 0

    chemin_credentials = _chemin_conf(args, "credentials_portail.json")
    cfg = ConfigRaccordement(
        portal_url=args.portail_url,
        admin_user=args.admin_user,
        admin_pass=admin_pass,
        provider_code=args.provider_code,
        provider_label=args.provider_label,
        node_catalog_url=node_catalog_url,
        credentials_path=chemin_credentials,
    )
    credentials_existants = None
    if os.path.isfile(chemin_credentials):
        try:
            credentials_existants = json.loads(Path(chemin_credentials).read_text())
        except (json.JSONDecodeError, OSError):
            print(f"  Attention : {chemin_credentials} illisible, ignoré")
    creds = raccorder(cfg, credentials_existants)

    chemin_ini = _chemin_conf(args, "rudi-catalog-portal.ini")
    generer_ini_portail(chemin_ini, creds)
    print(f"Fichier généré : {chemin_ini}")

    conf["portail"] = {
        "mode": "portail",
        "portal_url": args.portail_url,
        "provider_uuid": creds["provider_uuid"],
        "node_uuid": creds["node_uuid"],
        "raccorde_le": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    with open(args.rudi_conf, "w", encoding="utf-8") as f:
        json.dump(conf, f, indent=2, ensure_ascii=False)
    print(f"Conf nœud mise à jour : {args.rudi_conf}")

    if args.scenario == "peuple":
        organisations = lister_organisations(conf)
        print(f"\n{len(organisations)} organisation(s) présente(s) sur le nœud local.")
        saisie = confirmer(
            "\nGénérer le script SQL d'upsert de ces organisations vers le schéma partagé "
            "strukture_data du portail ?\n"
            "  Tapez 'GENERER SQL' pour confirmer (ou Entrée pour annuler) : "
        ).strip()
        if saisie != "GENERER SQL":
            print("Génération SQL annulée.")
        else:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            chemin_sql = _chemin_conf(args, f"upsert_organisations_{timestamp}.sql")
            nb = generer_sql_upsert_organisations(conf, creds["provider_uuid"], chemin_sql)
            print(f"\nScript SQL généré : {chemin_sql} ({nb} organisation(s)).")
            print("  ⚠️  Il cible le schéma partagé de l'infra ROOB (strukture_data).")
            print("      À relire, puis à exécuter manuellement — jamais par moissonneur-ods.")

    _afficher_instructions_ini(chemin_ini)
    if args.scenario == "peuple":
        print("\nAprès application du .ini, redémarrage du conteneur nœud et exécution du script SQL :")
        print(f"  python3 -m moissonneur_ods --rudi-conf {args.rudi_conf} --renvoyer-ressources")
        print("  pour pousser vers le portail les ressources déjà publiées avant le raccordement.")
    return 0


def _afficher_instructions_ini(chemin_ini: str) -> None:
    """Frontière de responsabilité : moissonneur-ods ne gère pas le cycle de vie du
    conteneur nœud — aucune automatisation du dépôt du .ini."""
    print(f"\nFichier généré : {chemin_ini}")
    print("""
Pour l'appliquer sur la machine hébergeant le nœud RUDI cible :
  1. Copier ce fichier dans le volume de conf du nœud (ex. /data/conf/rudi-catalog-portal.ini)
  2. (Re)démarrer le conteneur avec -e PORTAL_CONF=/data/conf/rudi-catalog-portal.ini
Ceci n'est PAS fait automatiquement par moissonneur-ods.""")


def executer_renvoyer_ressources(args, confirmer=input) -> int:
    """Pousse les ressources du nœud vers le portail (Kalim) — à lancer une fois le
    .ini appliqué et le SQL des organisations exécuté (scénario peuple)."""
    conf = charger_conf_rudi(args.rudi_conf) if args.rudi_conf else None
    if not conf:
        print("Configuration du nœud RUDI absente ou introuvable (--rudi-conf) — renvoi impossible.")
        return 2
    chemin_credentials = _chemin_conf(args, "credentials_portail.json")
    if not os.path.isfile(chemin_credentials):
        print(f"Fichier {chemin_credentials} introuvable.")
        print("  Exécuter d'abord : python3 -m moissonneur_ods --rudi-conf "
              f"{args.rudi_conf} --raccorder-portail --scenario peuple --portail-url <url> …")
        return 2
    try:
        credentials = json.loads(Path(chemin_credentials).read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"Fichier {chemin_credentials} illisible : {e}")
        return 2

    metadonnees = toutes_metadonnees_rudi(conf)
    ids = [m.get("global_id") for m in metadonnees if m.get("global_id")]
    if not ids:
        print("Aucune ressource avec global_id sur le nœud — rien à renvoyer.")
        return 0
    print(f"{len(ids)} ressource(s) du nœud à pousser directement vers Kalim "
          "(jamais via l'endpoint nœud /catalog/admin/portal/resources/send, bug D2).")
    saisie = confirmer("\nTaper 'RENVOYER' pour continuer (ou Entrée pour annuler) : ").strip()
    if saisie != "RENVOYER":
        print("Renvoi annulé.")
        return 0
    renvoyer_ressources(credentials, conf, metadonnees)
    return 0


def main(argv=None) -> int:
    args = construire_parseur().parse_args(argv)
    try:
        if args.raccorder_portail:
            return executer_raccordement(args)
        if args.renvoyer_ressources:
            return executer_renvoyer_ressources(args)
        if not args.url:
            print("L'URL de la plateforme ODS est requise (--url) hors modes "
                  "--raccorder-portail / --renvoyer-ressources.")
            return 2
        if args.menage:
            return executer_menage(args)
        return executer_moisson(args)
    except ErreurRaccordement as e:
        print(f"\nERREUR raccordement : {e}")
        return 1
    except OdsError as e:
        print(f"ERREUR ODS : {e}")
        return 3
    except KeyboardInterrupt:
        print("\nInterrompu.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
