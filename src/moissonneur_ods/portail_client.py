"""Client de raccordement d'un nœud RUDI cible à un portail ROOB.

Paramétrise ``rudi-portal-local/raccordement/declarer_noeud.py`` : mêmes endpoints,
même logique (idempotente, rejouable sans erreur), mais les constantes en dur
deviennent une ``ConfigRaccordement`` explicite. Divergences volontaires vs le
script de référence, toutes documentées dans le plan de raccordement
(``virtual-noodling-mist``) :
- ``harvestable=False`` à la création du nœud : le pull Kalim (harvest) est
  documenté **bloquant sans contournement externe** (RAPPORT_BUGS_RUDI.md,
  bug A1) — on ne planifie jamais un harvest qui ne peut qu'échouer. Seule la
  voie push (le nœud pousse lui-même son catalogue) est utilisable. ``notifiable``
  reste ``True`` (défaut ``false`` côté entité) : il est nécessaire pour que le
  nœud soit autorisé à pousser vers le portail.
- ``node_version`` est lu depuis le nœud cible (``GET {node_catalog_url}/version``),
  jamais supposé — le script de référence maquille ``"2.7.4"``, inexact sur
  ``rudinode-ods`` (réellement 2.7.2).
- ``node_url_prefix`` dérivé automatiquement de ``node_catalog_url`` (schéma+hôte+
  port) quand non fourni : la recherche-avant-création de ``creer_ou_trouver_noeud``
  matche par ce préfixe — s'il est faux, un nœud serait recréé en double à chaque run.
- Le fichier de credentials n'est jamais écrit avant que ``tester_token_noeud``
  n'ait validé que le compte ROBOT peut obtenir un token.

Le module embarque aussi les deux étapes du scénario « nœud déjà peuplé » :
- ``generer_sql_upsert_organisations`` : rattrapage des organisations par
  génération d'un script SQL destiné au schéma partagé ``strukture_data`` du
  portail — jamais exécuté par l'outil, car l'API ``POST /strukture/v1/
  organizations`` ignore l'uuid imposé (RAPPORT_BUGS_RUDI.md, bug C2).
- ``renvoyer_ressources`` : adaptation de
  ``rudi-portal-local/raccordement/repousser_metadonnees.py`` — pousse toutes les
  ressources du nœud directement vers Kalim, contournant le pull cassé (bug A1),
  le renvoi natif du nœud silencieusement skippé (bug D1) et l'envoi en masse
  auto-saturant de ``/catalog/admin/portal/resources/send`` (bug D2).
"""
from __future__ import annotations

import base64
import json
import secrets
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from rudi_harvest_toolkit.rudi_client import lister_organisations, url_catalog

CREATED = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

# Rythme entre deux envois de ressources vers Kalim (repris de
# repousser_metadonnees.py) : ne pas reproduire la saturation décrite en bug D2.
PAUSE_S = 0.5
# Version d'API portail attendue par la validation Kalim (constApi.js du nœud),
# injectée par la transformation ``portal_ready``.
PORTAL_API_VERSION = "1.4.1"


class ErreurRaccordement(Exception):
    """Toute erreur bloquante du raccordement / du renvoi de ressources."""


@dataclass
class ConfigRaccordement:
    portal_url: str
    admin_user: str
    admin_pass: str
    provider_code: str
    provider_label: str
    node_catalog_url: str
    node_url_prefix: str | None = None
    node_cron: str = "0 */5 * * * *"
    node_version: str | None = None
    credentials_path: str | None = None

    def __post_init__(self):
        if not self.node_url_prefix:
            parsed = urlparse(self.node_catalog_url)
            self.node_url_prefix = f"{parsed.scheme}://{parsed.netloc}"


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def obtenir_token_admin(session, cfg: ConfigRaccordement) -> str:
    """Obtenir un token admin via OAuth2 client_credentials (repli form login)."""
    r = session.post(
        f"{cfg.portal_url}/oauth2/token",
        auth=(cfg.admin_user, cfg.admin_pass),
        data={"grant_type": "client_credentials", "username": cfg.admin_user, "password": cfg.admin_pass},
        timeout=30,
    )
    if r.status_code < 400:
        return r.json()["access_token"]
    # Repli : form login
    r2 = session.post(
        f"{cfg.portal_url}/authenticate",
        data={"login": cfg.admin_user, "password": cfg.admin_pass},
        timeout=30,
    )
    if r2.status_code < 400:
        body = r2.json() if r2.headers.get("content-type", "").startswith("application/json") else {}
        token = body.get("token") or body.get("access_token") or body.get("jwt")
        if token:
            return token
    raise ErreurRaccordement(
        f"Impossible d'obtenir un token admin.\nOAuth2: {r.status_code} {r.text}\nLogin: {r2.status_code} {r2.text}"
    )


def lire_version_noeud(cfg: ConfigRaccordement) -> str:
    """Lit la version exposée par le nœud cible lui-même, ne la suppose jamais.

    Le nœud RUDI expose sa version d'API publique sur ``/catalog/version`` (et
    non ``/catalog/v1/version``, qui répond 404) — vérifié sur ``rudinode-ods``
    (renvoie ``1.4.3``). Repli prudent sur ``"1.4.0"`` (défaut du toolkit) si
    aucun endpoint ne répond : le champ n'est qu'informationnel côté portail.
    """
    candidats = [f"{cfg.node_catalog_url}/version"]
    if cfg.node_url_prefix:
        candidats.append(f"{cfg.node_url_prefix}/catalog/version")
    for url in candidats:
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200 and r.text.strip():
                version = r.text.strip()
                print(f"  Version nœud lue ({url}) : {version}")
                return version
        except Exception as e:
            print(f"  AVERTISSEMENT : {url} injoignable ({e}).")
    print("  AVERTISSEMENT : version du nœud illisible, valeur par défaut 1.4.0 utilisée.")
    return "1.4.0"


def creer_ou_trouver_producteur(session, token, cfg: ConfigRaccordement) -> str:
    """Chercher ou créer le producteur, matché par ``code``."""
    h = _headers(token)
    r = session.get(f"{cfg.portal_url}/strukture/v1/providers", headers=h, timeout=30)
    if r.status_code < 400:
        body = r.json()
        items = body if isinstance(body, list) else body.get("elements", body.get("items", body.get("content", [])))
        for p in items:
            if p.get("code") == cfg.provider_code:
                print(f"  Producteur déjà existant : {p['uuid']}")
                return p["uuid"]
    r = session.post(
        f"{cfg.portal_url}/strukture/v1/providers",
        headers=h,
        json={"code": cfg.provider_code, "label": cfg.provider_label, "openingDate": CREATED},
        timeout=30,
    )
    if r.status_code in (200, 201):
        uuid_ = r.json().get("uuid") or r.json().get("id")
        print(f"  Producteur créé : {uuid_}")
        return uuid_
    raise ErreurRaccordement(f"Échec création producteur : {r.status_code} {r.text}")


def creer_ou_trouver_noeud(session, token, provider_uuid: str, cfg: ConfigRaccordement) -> str:
    """Chercher ou créer le nœud (``harvestable=False``) pour ce producteur."""
    h = _headers(token)
    r = session.get(f"{cfg.portal_url}/strukture/v1/providers/{provider_uuid}/nodes", headers=h, timeout=30)
    if r.status_code < 400:
        body = r.json()
        nodes = body if isinstance(body, list) else body.get("elements", body.get("items", body.get("content", [])))
        for n in nodes:
            if (n.get("url") or "").startswith(cfg.node_url_prefix):
                print(f"  Nœud déjà existant : {n['uuid']} (url={n.get('url')})")
                return n["uuid"]
    r = session.post(
        f"{cfg.portal_url}/strukture/v1/providers/{provider_uuid}/nodes",
        headers=h,
        json={
            "url": cfg.node_catalog_url,
            # Jamais harvestable=True : le pull Kalim est documenté bloquant sans
            # contournement (bug A1) — un harvest planifié ne peut qu'échouer.
            "harvestable": False,
            # notifiable reste True : requis pour que le nœud puisse pousser.
            "notifiable": True,
            "harvestingCron": cfg.node_cron,
            "version": cfg.node_version or "1.4.0",
            "openingDate": CREATED,
        },
        timeout=30,
    )
    if r.status_code in (200, 201):
        uuid_ = r.json().get("uuid") or r.json().get("id")
        print(f"  Nœud créé : {uuid_}")
        return uuid_
    raise ErreurRaccordement(f"Échec création nœud : {r.status_code} {r.text}")


def obtenir_role_provider(session, token, cfg: ConfigRaccordement):
    """Récupérer l'objet rôle PROVIDER."""
    h = _headers(token)
    r = session.get(f"{cfg.portal_url}/acl/v1/roles", headers=h, timeout=30)
    if r.status_code >= 400:
        raise ErreurRaccordement(f"Échec lecture rôles : {r.status_code} {r.text}")
    body = r.json()
    roles = body if isinstance(body, list) else body.get("elements", body.get("items", body.get("content", [])))
    for role in roles:
        if role.get("code") == "PROVIDER":
            return role
    raise ErreurRaccordement(f"Rôle PROVIDER non trouvé parmi {[r_.get('code') for r_ in roles]}")


def compte_robot_existe(session, token, cfg: ConfigRaccordement, node_uuid: str) -> bool:
    """Le compte ROBOT ACL du nœud existe-t-il déjà ?"""
    h = _headers(token)
    r = session.get(f"{cfg.portal_url}/acl/v1/users?login={node_uuid}", headers=h, timeout=30)
    if r.status_code < 400:
        body = r.json()
        users = body if isinstance(body, list) else body.get("elements", body.get("items", body.get("content", [])))
        return any(u.get("login") == node_uuid for u in users)
    return False


def creer_compte_robot(session, token, cfg: ConfigRaccordement, node_uuid: str, password: str) -> None:
    """Créer le compte ROBOT ACL pour le nœud (``login = node_uuid`` exactement)."""
    h = _headers(token)
    role_provider = obtenir_role_provider(session, token, cfg)
    payload = {
        "login": node_uuid,
        "password": password,
        "type": "ROBOT",
        "company": cfg.provider_code,
        "firstname": "Noeud",
        "lastname": "Producteur",
        "roles": [role_provider],
    }
    r = session.post(
        f"{cfg.portal_url}/acl/v1/users",
        headers=h,
        json=payload,
        timeout=30,
    )
    if r.status_code in (200, 201):
        print(f"  Compte ROBOT créé : login={node_uuid}")
        return
    raise ErreurRaccordement(f"Échec création compte ROBOT : {r.status_code} {r.text}")


def tester_token_noeud(cfg: ConfigRaccordement, node_uuid: str, password: str) -> bool:
    """Garde-fou le plus important, à préserver mot pour mot : ne jamais écrire le
    fichier de credentials avant d'avoir validé que ce compte robot peut obtenir
    un token."""
    try:
        r = requests.post(
            f"{cfg.portal_url}/oauth2/token",
            auth=(node_uuid, password),
            data={"grant_type": "client_credentials", "username": node_uuid, "password": password},
            timeout=30,
        )
        ok = r.status_code < 400 and "access_token" in r.json()
    except Exception:
        ok = False
        r = None
    if ok:
        print("  ✓ Token nœud obtenu avec succès")
        return True
    detail = f" {r.status_code} {r.text}" if r is not None else " (exception réseau)"
    print(f"  ✗ Échec token nœud :{detail}")
    return False


def raccorder(cfg: ConfigRaccordement, credentials_existants: dict | None = None) -> dict:
    """Orchestre le raccordement producteur + nœud + compte ROBOT sur le portail.

    Idempotent (réutilise le mot de passe existant si le compte ROBOT existe déjà).
    Retourne le dict credentials — il contient **explicitement** ``provider_uuid``
    et ``node_uuid``, nécessaires ensuite à ``generer_sql_upsert_organisations``.
    Le fichier de credentials n'est écrit qu'après succès du test de token.
    """
    creds_existants = credentials_existants or {}
    print("=== Raccordement au portail ROOB ===\n")

    session = requests.Session()

    print("1. Token admin...")
    token = obtenir_token_admin(session, cfg)
    print(f"  Token obtenu ({len(token)} chars)\n")

    print("2. Producteur...")
    provider_uuid = creer_ou_trouver_producteur(session, token, cfg)
    print()

    print("3. Nœud...")
    if not cfg.node_version:
        cfg.node_version = lire_version_noeud(cfg)
    node_uuid = creer_ou_trouver_noeud(session, token, provider_uuid, cfg)
    print()

    print("4. Mot de passe + compte ROBOT...")
    if compte_robot_existe(session, token, cfg, node_uuid):
        password = creds_existants.get("node_password")
        if not password:
            raise ErreurRaccordement(
                "Compte ROBOT déjà existant mais mot de passe inconnu "
                f"(absent de {cfg.credentials_path or 'credentials_portail.json'}).\n"
                "Supprimer le compte côté portail (ou restaurer le fichier de credentials) puis rejouer."
            )
        print(f"  Compte ROBOT déjà existant : {node_uuid} (mot de passe repris du fichier credentials)")
    else:
        password = secrets.token_urlsafe(24)
        creer_compte_robot(session, token, cfg, node_uuid, password)
    print()

    print("5. Vérification token nœud...")
    ok = tester_token_noeud(cfg, node_uuid, password)
    print()

    if not ok:
        print("=== ÉCHEC : le token nœud n'a pas pu être obtenu (fichier credentials inchangé) ===")
        raise ErreurRaccordement("Token nœud invalide — raccordement interrompu, aucun fichier credentials écrit.")

    creds = {
        "portal_url": cfg.portal_url,
        "provider_uuid": provider_uuid,
        "node_uuid": node_uuid,
        "node_login": node_uuid,
        "node_password": password,
        "created": creds_existants.get("created", CREATED),
    }
    if cfg.credentials_path:
        chemin = Path(cfg.credentials_path)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps(creds, indent=2) + "\n")
        chemin.chmod(0o600)
        print(f"6. Credentials écrits dans {chemin}")
    print("\n=== Succès ===")
    return creds


def generer_ini_portail(chemin: str, credentials: dict) -> None:
    """Écrit ``rudi-catalog-portal.ini`` à partir des credentials du raccordement.

    Format ``PORTAL_CONF`` documenté dans rudi-node-build/rudi-node-container/
    README.md (§1H). ``chmod 0o600`` : le fichier contient le mot de passe du nœud
    en base64 réversible (``is_pwd_b64 = true``), pas seulement le JSON credentials.
    """
    passw_b64 = base64.b64encode(credentials["node_password"].encode("utf-8")).decode("ascii")
    contenu = (
        "[portal]\n"
        f'portal_url = "{credentials["portal_url"]}"\n'
        "portal_req_timeout = 60000\n"
        f'login = "{credentials["node_uuid"]}"\n'
        f'passw = "{passw_b64}"\n'
        "is_pwd_b64 = true\n"
    )
    chemin_ini = Path(chemin)
    chemin_ini.parent.mkdir(parents=True, exist_ok=True)
    chemin_ini.write_text(contenu)
    chemin_ini.chmod(0o600)


# ---------------------------------------------------------------------------
# Rattrapage des organisations d'un nœud déjà peuplé (--scenario peuple)
# ---------------------------------------------------------------------------

def _echapper_sql(valeur: str) -> str:
    """Échappement SQL minimal d'un champ texte (doublage des apostrophes)."""
    return (valeur or "").replace("'", "''")


def _commentaire_sql(valeur: str) -> str:
    """Neutralise les retours à la ligne d'une valeur avant usage en commentaire ``--``."""
    return (valeur or "").replace("\r", " ").replace("\n", " ")


def generer_sql_upsert_organisations(conf_rudi: dict, provider_uuid: str, chemin_sortie: str) -> int:
    """Génère un script SQL d'upsert des organisations vers ``strukture_data``.

    Schéma réel (migrations Flyway de Strukture, V7→V15) — l'upsert touche :
    - ``organization`` : ``uuid`` (contrainte unique ``organization_uk`` posée par
      V8), ``name`` (V8 renomme ``label`` en ``name`` et supprime ``code``/``order_``),
      ``description`` (ajoutée V9, rendue NOT NULL sans défaut par V12),
      ``opening_date`` (NOT NULL). Les colonnes de workflow de V12
      (``organization_status``/``status``/``functional_status``/``initiator``/
      ``process_definition_key``) sont laissées sur leurs ``DEFAULT`` et jamais
      touchées par un ``UPDATE`` — elles sont gérées par le portail.
    - ``linked_producer`` (V13) : colonnes ``NOT NULL`` **sans défaut** toutes
      fournies explicitement ; contrainte unique ``(organization_fk, provider_fk)``
      posée par V15. Valeurs alignées sur celles du workflow applicatif
      (``LinkedProducerHelper``/``LinkedProducerServiceImpl``) pour un lien
      immédiatement utilisable : ``status='COMPLETED'``,
      ``linked_producer_status='VALIDATED'``, ``functional_status='Lien créé'``,
      ``process_definition_key='linked-producer-process'``,
      ``initiator=node_uuid``.

    Ne lit **que** le nœud local — zéro connexion au portail ou à sa base depuis
    moissonneur-ods. Le fichier n'est jamais exécuté par l'outil : il cible le
    schéma partagé de l'infra ROOB et doit être relu puis exécuté manuellement.
    Retourne le nombre d'organisations écrites dans le script.
    """
    node_uuid = (conf_rudi.get("portail") or {}).get("node_uuid", "")
    if not node_uuid:
        raise ValueError(
            "conf_rudi['portail']['node_uuid'] absent — le SQL d'upsert doit être généré après "
            "l'étape de raccordement (--raccorder-portail), qui enregistre le nœud dans la conf."
        )
    organisations = lister_organisations(conf_rudi)
    date_gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lignes = [
        "-- Script d'upsert des organisations vers strukture_data (schéma partagé ROOB)",
        f"-- Nœud source           : {_commentaire_sql(conf_rudi.get('url', '?'))}",
        f"-- Producteur cible      : {provider_uuid}",
        f"-- Date de génération    : {date_gen}",
        f"-- Nombre d'organisations : {len(organisations)}",
        "--",
        "-- ⚠️  Ce script cible le schéma partagé de l'infra ROOB (strukture_data).",
        "--     Vérifier la base cible avant exécution. À exécuter manuellement hors",
        "--     de moissonneur-ods (l'outil ne l'exécute jamais).",
        "--",
        "-- Colonnes de workflow organisées par les DEFAULT de V12 (VALIDATED/COMPLETED/",
        "-- Acceptée/''/''), non spécifiées ici ; jamais touchées par les UPDATE.",
        "",
        "BEGIN;",
        "",
    ]

    nb = 0
    for org in organisations:
        org_uuid = org.get("organization_id") or ""
        nom_source = org.get("organization_name") or ""
        if not org_uuid or not nom_source:
            lignes += ["-- Organisation ignorée (organization_id ou name manquant) : "
                       + _commentaire_sql(json.dumps(org, ensure_ascii=False)), ""]
            continue
        nom = _echapper_sql(nom_source)
        description = _echapper_sql(org.get("organization_summary") or org.get("organization_caption") or "")
        lp_description = _echapper_sql(
            f"Rattachement de l'organisation '{nom_source}' au provider '{provider_uuid}'"
        )
        lien_uuid = str(uuid.uuid4())
        lignes += [
            f"-- Organisation: {_commentaire_sql(nom_source)}",
            "INSERT INTO strukture_data.organization (uuid, name, description, opening_date)",
            f"VALUES ('{org_uuid}', '{nom}', '{description}', now())",
            "ON CONFLICT (uuid) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description;",
            "",
            "INSERT INTO strukture_data.linked_producer",
            "    (uuid, creation_date, description, functional_status, initiator,",
            "     process_definition_key, status, linked_producer_status, organization_fk, provider_fk)",
            "VALUES",
            f"    ('{lien_uuid}', now(), '{lp_description}', 'Lien créé', '{node_uuid}', 'linked-producer-process',",
            "     'COMPLETED', 'VALIDATED',",
            f"     (SELECT id FROM strukture_data.organization WHERE uuid = '{org_uuid}'),",
            f"     (SELECT id FROM strukture_data.provider WHERE uuid = '{provider_uuid}'))",
            "ON CONFLICT (organization_fk, provider_fk) DO NOTHING;",
            "",
        ]
        nb += 1

    lignes += ["COMMIT;", ""]
    chemin = Path(chemin_sortie)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text("\n".join(lignes))
    return nb


# ---------------------------------------------------------------------------
# Renvoi des ressources vers le portail (--renvoyer-ressources)
# ---------------------------------------------------------------------------

def _requete(url, methode="GET", corps=None, entetes=None, brut=None, timeout=60):
    """Requête HTTP simple (urllib), retourne ``(statut, texte)`` — repris à
    l'identique de repousser_metadonnees.py."""
    data = brut if brut is not None else (json.dumps(corps).encode() if corps is not None else None)
    req = urllib.request.Request(url, data=data, method=methode, headers=entetes or {})
    if corps is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as rep:
            return rep.status, rep.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def obtenir_token_robot(portal_url: str, login: str, password: str) -> str:
    """Token OAuth2 avec les credentials du compte ROBOT du nœud."""
    basic = base64.b64encode(f"{login}:{password}".encode()).decode()
    corps = (f"grant_type=client_credentials&username={login}&password={password}").encode()
    statut, texte = _requete(
        f"{portal_url}/oauth2/token", "POST", brut=corps,
        entetes={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    if statut != 200:
        raise ErreurRaccordement(f"Échec token portail ({statut}) : {texte[:200]}")
    return json.loads(texte)["access_token"]


def portal_ready(m: dict) -> dict:
    """Équivalent de ``toRudiPortalJSON`` (rudi-catalog Metadata.js) — repris
    mot pour mot de repousser_metadonnees.py."""
    m["metadata_info"]["api_version"] = PORTAL_API_VERSION
    # Dédoublonnage media_id : Kalim crashe « Duplicate key <media_id>_<contract> »
    # sur les fiches dont available_formats contient des doublons.
    vus = set()
    m["available_formats"] = [f for f in m.get("available_formats", [])
                              if not (f.get("media_id") in vus or vus.add(f.get("media_id")))]
    for media in m.get("available_formats", []):
        if media.get("media_type") != "FILE":
            media.pop("file_type", None)
        media.pop("file_storage_status", None)
        media.pop("file_status_update", None)
        # Kalim NPE sur les contrats géo (wfs/wms/wmts) si connector_parameters
        # est null — une liste vide est perdue à la re-sérialisation par la façade
        # Kalim (inclusion NON_EMPTY), donc redevient null à la validation.
        # `versions` (format X.Y.Z, cf. VersionsValidator) est le plus neutre.
        conn = media.get("connector")
        if conn is not None and not conn.get("connector_parameters"):
            ic = (conn.get("interface_contract") or "").lower()
            if ic in ("wfs", "wmts"):
                conn["connector_parameters"] = [{"key": "versions", "value": "2.0.0"}]
            elif ic == "wms":
                conn["connector_parameters"] = [{"key": "versions", "value": "1.3.0"}]
            else:
                conn["connector_parameters"] = []
    m.pop("integration_error_id", None)
    m.pop("metadata_status", None)
    m.get("metadata_info", {}).get("metadata_dates", {}).pop("published", None)
    m.pop("restricted_access", None)
    conf = m.get("access_condition", {}).get("confidentiality")
    if conf and conf.get("gdpr_sensitive") is None:
        conf.pop("gdpr_sensitive", None)
    return m


def renvoyer_ressources(credentials: dict, conf_rudi: dict, metadonnees: list[dict]) -> dict:
    """Pousse toutes les ressources du nœud vers le portail (Kalim), en direct.

    Reprise de repousser_metadonnees.py : GET de la fiche sur le nœud,
    transformation ``portal_ready()``, puis POST (ou PUT si déjà au catalogue
    portail) sur ``{portal_url}/kalim/v1/resources`` avec le compte ROBOT du nœud.
    Jamais l'endpoint nœud ``/catalog/admin/portal/resources/send`` (auth
    incompatible + auto-saturant, bug D2). Rythme ``PAUSE_S`` entre requêtes.
    Retourne le bilan ``{"POST":…, "PUT":…, "echec":…}``.
    """
    portal_url = credentials["portal_url"]
    login = credentials.get("node_login") or credentials["node_uuid"]
    password = credentials["node_password"]
    node_catalog = f"{url_catalog(conf_rudi)}/v1"

    ids = [m.get("global_id") for m in metadonnees if m.get("global_id")]
    if not ids:
        raise ErreurRaccordement("Aucune ressource avec global_id à renvoyer.")
    print(f"{len(ids)} ressources à pousser directement vers Kalim...")
    token = obtenir_token_robot(portal_url, login, password)
    bilan = {"POST": 0, "PUT": 0, "echec": 0}

    for n, gid in enumerate(ids, 1):
        statut, texte = _requete(f"{node_catalog}/resources/{gid}")
        if statut != 200:
            print(f"  [{n}/{len(ids)}] {gid} : GET nœud {statut} — ignoré")
            bilan["echec"] += 1
            continue
        fiche = portal_ready(json.loads(texte))
        titre = fiche.get("resource_title", "?")[:55]

        # PUT si la fiche est déjà au catalogue portail, POST sinon
        deja, _ = _requete(f"{portal_url}/konsult/v1/datasets/{gid}/metadatas",
                           entetes={"Authorization": f"Bearer {token}"})
        methode = "PUT" if deja == 200 else "POST"

        statut, texte = _requete(
            f"{portal_url}/kalim/v1/resources", methode, corps=fiche,
            entetes={"Authorization": f"Bearer {token}"})
        if statut == 401:  # token expiré : renouveler et rejouer
            token = obtenir_token_robot(portal_url, login, password)
            statut, texte = _requete(
                f"{portal_url}/kalim/v1/resources", methode, corps=fiche,
                entetes={"Authorization": f"Bearer {token}"})
        if statut == 200:
            bilan[methode] += 1
            print(f"  [{n}/{len(ids)}] {methode} OK — {titre}", flush=True)
        else:
            bilan["echec"] += 1
            print(f"  [{n}/{len(ids)}] {methode} {statut} — {titre} : "
                  f"{texte[:150]}", flush=True)
        time.sleep(PAUSE_S)

    print(f"\nBilan des envois : {bilan}")
    print("Le traitement Kalim et les rapports vers le nœud sont asynchrones.")
    print("Contrôles (attendre quelques minutes) :")
    print(f"  - catalogue portail : curl -s '{portal_url}/konsult/v1/datasets/metadatas?limit=1&offset=0'  "
          "(champ total)")
    print(f"  - statuts nœud : curl -s '{node_catalog}/resources?limit=1'  (champ total)")
    return bilan