"""Détection scopée des JDD orphelins et suppression confirmée.

Le nœud RUDI peut héberger des datasets d'autres moissonneurs (ex.
``moissonneuse-batteuse``). Le périmètre de ce moissonneur est donc délimité par
``collection_tag`` — champ de première classe de ``RudiMetadata`` (contrairement à
``metadata_info``, dont le schéma fermé perd silencieusement tout champ additionnel).
Chaque JDD publié porte ``collection_tag = "moissonneur-ods:<url-plateforme>"``.

La détection ne compare **que** les métadonnées portant ce tag, et signale en plus le
nombre de ressources du nœud sans tag reconnu (marquage potentiellement perdu après un
réimport manuel) — jamais d'hypothèse silencieuse sur la fiabilité du marquage.
"""
from __future__ import annotations

from rudi_harvest_toolkit.rudi_client import supprimer_dataset, toutes_metadonnees_rudi

PREFIXE_COLLECTION = "moissonneur-ods"


def collection_tag_pour(url_plateforme: str) -> str:
    return f"{PREFIXE_COLLECTION}:{url_plateforme.rstrip('/')}"


def _titre(meta: dict) -> str:
    return meta.get("resource_title") or meta.get("dataset_name") or meta.get("local_id", "?")


def detecter_orphelins(conf: dict, url_plateforme: str, local_ids_actifs: set[str],
                       toutes_metadonnees: list[dict] | None = None) -> dict:
    """Retourne ``{collection_tag, orphelins, total_noeud, tagged, sans_tag}``.

    ``local_ids_actifs`` : ``local_id`` des JDD actuellement présents dans le
    catalogue ODS moissonné. Un orphelin est un dataset du nœud portant le tag de ce
    run dont le ``local_id`` n'est plus actif.
    """
    tag = collection_tag_pour(url_plateforme)
    if toutes_metadonnees is None:
        toutes_metadonnees = toutes_metadonnees_rudi(conf)

    tagged = [m for m in toutes_metadonnees if m.get("collection_tag") == tag]
    sans_tag = [m for m in toutes_metadonnees if not m.get("collection_tag")]
    orphelins = [m for m in tagged if m.get("local_id") not in local_ids_actifs]

    return {
        "collection_tag": tag,
        "orphelins": orphelins,
        "total_noeud": len(toutes_metadonnees),
        "tagged": len(tagged),
        "sans_tag": len(sans_tag),
    }


def afficher_orphelins(resultat: dict) -> None:
    orphelins = resultat["orphelins"]
    print(f"\nCollection : {resultat['collection_tag']}")
    print(f"  {resultat['tagged']}/{resultat['total_noeud']} dataset(s) du nœud portent ce tag "
          f"({resultat['sans_tag']} sans tag reconnu).")
    if not orphelins:
        print("  Aucun orphelin dans le périmètre de ce moissonneur.")
        return
    print(f"\n {len(orphelins)} orphelin(s) détecté(s) dans le périmètre de ce moissonneur :")
    for meta in orphelins:
        print(f"  • {_titre(meta)}  (local_id={meta.get('local_id', '?')})")


def supprimer_orphelins(conf: dict, orphelins: list[dict],
                        confirmer=input) -> tuple[int, int]:
    """Supprime les orphelins après saisie exacte ``SUPPRIMER``. Retourne (ok, échecs)."""
    if not orphelins:
        print("Aucun orphelin à supprimer.")
        return 0, 0
    saisie = confirmer(
        f"\nSupprimer définitivement {len(orphelins)} JDD orphelin(s) du nœud RUDI ?\n"
        f"  Tapez 'SUPPRIMER' pour confirmer (ou Entrée pour annuler) : "
    ).strip()
    if saisie != "SUPPRIMER":
        print("Suppression annulée.")
        return 0, 0

    ok = echecs = 0
    for meta in orphelins:
        identifiant = meta.get("global_id") or meta.get("local_id")
        if identifiant and supprimer_dataset(conf, identifiant):
            ok += 1
        else:
            echecs += 1
    print(f"\nSupprimé(s) : {ok}, échec(s) : {echecs}")
    return ok, echecs


def menage_orphelins(conf: dict, url_plateforme: str, local_ids_actifs: set[str],
                     *, toutes_metadonnees: list[dict] | None = None,
                     dry_run: bool = False, confirmer=input) -> dict:
    """Détecte, affiche et (hors dry-run) supprime les orphelins. Retourne le résultat."""
    resultat = detecter_orphelins(conf, url_plateforme, local_ids_actifs,
                                  toutes_metadonnees=toutes_metadonnees)
    afficher_orphelins(resultat)
    if resultat["sans_tag"]:
        print(f"\n  ⚠️  {resultat['sans_tag']} ressource(s) du nœud sans collection_tag reconnu — "
              "non proposées à la suppression (périmètre incertain), à inspecter manuellement.")
    if dry_run:
        print("  (--dry-run : aucune suppression effectuée.)")
        return resultat
    resultat["supprimes"], resultat["echecs"] = supprimer_orphelins(
        conf, resultat["orphelins"], confirmer=confirmer
    )
    return resultat
