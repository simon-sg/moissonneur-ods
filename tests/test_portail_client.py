"""Tests unitaires de portail_client (mocks HTTP, zéro appel réseau)."""
import base64
import json
import os
import tempfile
import unittest
from unittest import mock

from support import FakeResponse

from moissonneur_ods import portail_client
from moissonneur_ods.portail_client import (
    ConfigRaccordement,
    creer_ou_trouver_noeud,
    creer_ou_trouver_producteur,
    generer_ini_portail,
    marquer_organisations_traitees,
    obtenir_token_admin,
    organisations_nouvellement_validees,
    raccorder,
    renvoyer_ressources,
    ressources_pour_organisations,
    supprimer_ressources_portail,
)


class FakeSessionPortail:
    """Fake session ``requests`` : route par (méthode, fragment d'URL)."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.appels = []

    def _repondre(self, methode, url, kwargs):
        self.appels.append((methode, url, kwargs))
        for (m, fragment), reponse in sorted(self.routes.items(), key=lambda kv: -len(kv[0][1])):
            if m == methode and fragment in url:
                return reponse
        return FakeResponse(404, text=f"non mocké : {methode} {url}")

    def get(self, url, **kwargs):
        return self._repondre("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._repondre("POST", url, kwargs)


def _cfg(**kwargs):
    base = dict(
        portal_url="http://portail.test",
        admin_user="rudi",
        admin_pass="secret",
        provider_code="MOISS_ODS_RM",
        provider_label="Moissonneur ODS",
        node_catalog_url="http://172.17.0.1:3030/catalog/v1",
        node_version="2.7.2",
    )
    base.update(kwargs)
    return ConfigRaccordement(**base)


class TestConfigRaccordement(unittest.TestCase):
    def test_prefixe_url_derive_du_catalogue(self):
        self.assertEqual(_cfg().node_url_prefix, "http://172.17.0.1:3030")

    def test_prefixe_explicite_conserve(self):
        self.assertEqual(_cfg(node_url_prefix="http://autre:9999").node_url_prefix, "http://autre:9999")


class TestTokenAdmin(unittest.TestCase):
    def test_oauth2_client_credentials(self):
        session = FakeSessionPortail({
            ("POST", "/oauth2/token"): FakeResponse(200, {"access_token": "tok-admin"}),
        })
        self.assertEqual(obtenir_token_admin(session, _cfg()), "tok-admin")

    def test_repli_form_login(self):
        session = FakeSessionPortail({
            ("POST", "/oauth2/token"): FakeResponse(401, text="non"),
            ("POST", "/authenticate"): FakeResponse(
                200, {"token": "jwt-admin"}, headers={"content-type": "application/json"}),
        })
        self.assertEqual(obtenir_token_admin(session, _cfg()), "jwt-admin")

    def test_echec_des_deux_voies_leve_erreur(self):
        session = FakeSessionPortail({
            ("POST", "/oauth2/token"): FakeResponse(401, text="non"),
            ("POST", "/authenticate"): FakeResponse(401, text="non"),
        })
        with self.assertRaises(portail_client.ErreurRaccordement):
            obtenir_token_admin(session, _cfg())


class TestProducteurNoeud(unittest.TestCase):
    def test_producteur_existant_reutilise(self):
        session = FakeSessionPortail({
            ("GET", "/strukture/v1/providers"): FakeResponse(
                200, [{"code": "MOISS_ODS_RM", "uuid": "prov-1"}]),
        })
        self.assertEqual(creer_ou_trouver_producteur(session, "tok", _cfg()), "prov-1")
        self.assertEqual([m for m, _, _ in session.appels], ["GET"])

    def test_producteur_cree_si_absent(self):
        session = FakeSessionPortail({
            ("GET", "/strukture/v1/providers"): FakeResponse(200, {"elements": []}),
            ("POST", "/strukture/v1/providers"): FakeResponse(200, {"uuid": "prov-2"}),
        })
        self.assertEqual(creer_ou_trouver_producteur(session, "tok", _cfg()), "prov-2")

    def test_noeud_cree_toujours_non_harvestable(self):
        session = FakeSessionPortail({
            ("GET", "/providers/prov-1/nodes"): FakeResponse(200, []),
            ("POST", "/providers/prov-1/nodes"): FakeResponse(200, {"uuid": "node-1"}),
        })
        node_uuid = creer_ou_trouver_noeud(session, "tok", "prov-1", _cfg())
        self.assertEqual(node_uuid, "node-1")
        payload = next(k for m, u, k in session.appels if m == "POST")["json"]
        self.assertIs(payload["harvestable"], False)
        self.assertIs(payload["notifiable"], True)
        self.assertEqual(payload["url"], "http://172.17.0.1:3030/catalog/v1")
        self.assertEqual(payload["version"], "2.7.2")

    def test_noeud_existant_matche_par_prefixe(self):
        session = FakeSessionPortail({
            ("GET", "/providers/prov-1/nodes"): FakeResponse(200, {
                "elements": [{"uuid": "node-9", "url": "http://172.17.0.1:3030/catalog/v1"}],
            }),
        })
        self.assertEqual(creer_ou_trouver_noeud(session, "tok", "prov-1", _cfg()), "node-9")
        self.assertEqual([m for m, _, _ in session.appels], ["GET"])

    def test_echec_creation_noeud_leve_erreur(self):
        session = FakeSessionPortail({
            ("GET", "/providers/prov-1/nodes"): FakeResponse(200, []),
            ("POST", "/providers/prov-1/nodes"): FakeResponse(500, text="boom"),
        })
        with self.assertRaises(portail_client.ErreurRaccordement):
            creer_ou_trouver_noeud(session, "tok", "prov-1", _cfg())


class TestRaccorder(unittest.TestCase):
    @staticmethod
    def _routes(compte_existe=False, node_uuid="node-1"):
        users = [{"login": node_uuid}] if compte_existe else []
        return {
            ("POST", "/oauth2/token"): FakeResponse(200, {"access_token": "admin-tok"}),
            ("GET", "/providers/prov-1/nodes"): FakeResponse(200, []),
            ("POST", "/providers/prov-1/nodes"): FakeResponse(200, {"uuid": node_uuid}),
            ("GET", "/strukture/v1/providers"): FakeResponse(
                200, [{"code": "MOISS_ODS_RM", "uuid": "prov-1"}]),
            ("GET", "/acl/v1/users?login="): FakeResponse(200, {"elements": users}),
            ("GET", "/acl/v1/roles"): FakeResponse(200, [{"code": "PROVIDER", "uuid": "role-1"}]),
            ("POST", "/acl/v1/users"): FakeResponse(201, {}),
        }

    def test_succes_ecrit_credentials_et_retourne_uuids(self):
        fake = FakeSessionPortail(self._routes())
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "credentials_portail.json")
            with mock.patch.object(portail_client.requests, "Session", return_value=fake), \
                 mock.patch.object(portail_client.requests, "post",
                                   return_value=FakeResponse(200, {"access_token": "node-tok"})):
                creds = raccorder(_cfg(credentials_path=chemin))
            self.assertEqual(creds["provider_uuid"], "prov-1")
            self.assertEqual(creds["node_uuid"], "node-1")
            self.assertTrue(os.path.isfile(chemin))
            self.assertEqual(os.stat(chemin).st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(open(chemin).read())["provider_uuid"], "prov-1")

    def test_echec_token_noeud_n_ecrit_pas_les_credentials(self):
        fake = FakeSessionPortail(self._routes())
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "credentials_portail.json")
            with mock.patch.object(portail_client.requests, "Session", return_value=fake), \
                 mock.patch.object(portail_client.requests, "post",
                                   return_value=FakeResponse(401, text="bad credentials")):
                with self.assertRaises(portail_client.ErreurRaccordement):
                    raccorder(_cfg(credentials_path=chemin))
            self.assertFalse(os.path.exists(chemin))

    def test_compte_robot_existant_reutilise_mot_de_passe(self):
        fake = FakeSessionPortail(self._routes(compte_existe=True))
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(portail_client.requests, "Session", return_value=fake), \
                 mock.patch.object(portail_client.requests, "post",
                                   return_value=FakeResponse(200, {"access_token": "node-tok"})):
                creds = raccorder(_cfg(credentials_path=os.path.join(d, "c.json")),
                                  credentials_existants={"node_password": "ancien"})
        self.assertEqual(creds["node_password"], "ancien")
        self.assertEqual([u for m, u, _ in fake.appels if m == "POST" and u.endswith("/acl/v1/users")], [])


class TestIniPortail(unittest.TestCase):
    def test_format_et_permissions(self):
        creds = {"portal_url": "http://rudi.localhost", "node_uuid": "node-1",
                 "node_password": "p@s s"}
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "rudi-catalog-portal.ini")
            generer_ini_portail(chemin, creds)
            contenu = open(chemin).read()
            self.assertEqual(os.stat(chemin).st_mode & 0o777, 0o600)
        self.assertIn("[portal]", contenu)
        self.assertIn('portal_url = "http://rudi.localhost"', contenu)
        self.assertIn('login = "node-1"', contenu)
        self.assertIn("portal_req_timeout = 60000", contenu)
        self.assertIn("is_pwd_b64 = true", contenu)
        attendu = base64.b64encode(b"p@s s").decode()
        self.assertIn(f'passw = "{attendu}"', contenu)


class TestRenvoyerRessources(unittest.TestCase):
    def test_delegue_au_toolkit_avec_conf_rudi(self):
        """renvoyer_ressources() ne doit plus jamais parler à Kalim en direct avec le
        compte ROBOT du portail — elle délègue à renvoyer_ressources_connues() du
        toolkit, qui PUT chaque ressource sur la route producteur normale du nœud."""
        creds = {"portal_url": "http://portail.test", "node_uuid": "node-1",
                 "node_login": "node-1", "node_password": "pw"}
        conf_rudi = {"url": "http://node:3032", "usr": "u", "pwd": "p"}
        metas = [{"global_id": "g1"}, {"global_id": "g2"}]
        with mock.patch.object(portail_client, "renvoyer_ressources_connues") as renvoyer, \
             mock.patch.object(portail_client, "url_catalog", return_value="http://node:3030/catalog"):
            renvoyer.return_value = {"ok": 2, "echecs": []}
            bilan = renvoyer_ressources(creds, conf_rudi, metas)

        self.assertEqual(bilan, {"ok": 2, "echecs": []})
        renvoyer.assert_called_once_with(conf_rudi, metas, pause_s=mock.ANY)
        # jamais de credentials/token portail transmis à l'appel de renvoi lui-même
        self.assertNotIn("credentials", renvoyer.call_args.kwargs)

    def test_aucune_ressource_leve_erreur(self):
        creds = {"portal_url": "http://portail.test", "node_uuid": "n",
                 "node_login": "n", "node_password": "pw"}
        with mock.patch.object(portail_client, "url_catalog", return_value="http://node:3030/catalog"):
            with self.assertRaises(portail_client.ErreurRaccordement):
                renvoyer_ressources(creds, {"url": "http://node:3032"}, [])


class TestSupprimerRessourcesPortail(unittest.TestCase):
    def test_delegue_au_toolkit_et_agrege_le_bilan(self):
        creds = {"portal_url": "http://portail.test", "node_uuid": "node-1",
                 "node_login": "node-1", "node_password": "pw"}
        suppressions = []

        def fake_delete(url, **kwargs):
            suppressions.append(url)
            return FakeResponse(200 if url.endswith("/g1") else 404,
                                text="" if url.endswith("/g1") else "introuvable")

        with mock.patch("rudi_harvest_toolkit.rudi_client.session") as sess, \
             mock.patch("rudi_harvest_toolkit.rudi_client.time.sleep"), \
             mock.patch.object(portail_client.time, "sleep"):
            sess.post.return_value = FakeResponse(200, payload={"access_token": "tok"})
            sess.delete.side_effect = fake_delete
            bilan = supprimer_ressources_portail(creds, ["g1", "g2"])

        self.assertEqual(bilan, {"DELETE": 1, "echec": 1})
        self.assertEqual(suppressions, [
            "http://portail.test/kalim/v1/resources/g1",
            "http://portail.test/kalim/v1/resources/g2",
        ])
        # même en-tête d'auth que le push (compte ROBOT), via le toolkit
        self.assertEqual(sess.delete.call_args.kwargs["headers"]["Authorization"], "Bearer tok")

    def test_aucun_global_id_leve_erreur(self):
        creds = {"portal_url": "http://portail.test", "node_uuid": "n",
                 "node_login": "n", "node_password": "pw"}
        with self.assertRaises(portail_client.ErreurRaccordement):
            supprimer_ressources_portail(creds, [])


class TestOrganisationsNouvellementValidees(unittest.TestCase):
    def test_exclut_les_organisations_deja_traitees(self):
        with tempfile.TemporaryDirectory() as d:
            chemin_etat = os.path.join(d, "etat_organisations_validees.json")
            with open(chemin_etat, "w", encoding="utf-8") as f:
                json.dump({"organisations_traitees": ["org-1"]}, f)
            with mock.patch("rudi_harvest_toolkit.rudi_client.lister_organisations",
                                   return_value=[{"organization_id": "org-1"},
                                                {"organization_id": "org-2"}]):
                self.assertEqual(
                    organisations_nouvellement_validees({"url": "http://n:3032"}, chemin_etat),
                    ["org-2"],
                )

    def test_aucun_etat_prealable_retourne_toutes_les_validees(self):
        with tempfile.TemporaryDirectory() as d:
            chemin_etat = os.path.join(d, "etat_organisations_validees.json")
            with mock.patch("rudi_harvest_toolkit.rudi_client.lister_organisations",
                                   return_value=[{"organization_id": "org-9"}]):
                self.assertEqual(
                    organisations_nouvellement_validees({"url": "http://n:3032"}, chemin_etat),
                    ["org-9"],
                )

    def test_ne_modifie_pas_le_fichier_etat(self):
        with tempfile.TemporaryDirectory() as d:
            chemin_etat = os.path.join(d, "etat_organisations_validees.json")
            with mock.patch("rudi_harvest_toolkit.rudi_client.lister_organisations",
                                   return_value=[{"organization_id": "org-1"}]):
                organisations_nouvellement_validees({"url": "http://n:3032"}, chemin_etat)
            self.assertFalse(os.path.isfile(chemin_etat))


class TestMarquerOrganisationsTraitees(unittest.TestCase):
    def test_fusionne_avec_etat_existant(self):
        with tempfile.TemporaryDirectory() as d:
            chemin_etat = os.path.join(d, "etat.json")
            marquer_organisations_traitees(chemin_etat, ["org-1"])
            marquer_organisations_traitees(chemin_etat, ["org-2"])
            with open(chemin_etat, encoding="utf-8") as f:
                self.assertEqual(sorted(json.load(f)["organisations_traitees"]),
                                 ["org-1", "org-2"])


class TestRessourcesPourOrganisations(unittest.TestCase):
    def test_filtre_sur_producer_organization_id(self):
        metas = [
            {"global_id": "g1", "producer": {"organization_id": "org-1"}},
            {"global_id": "g2", "producer": {"organization_id": "org-2"}},
            {"global_id": "g3"},  # pas de producer : jamais sélectionnée
        ]
        self.assertEqual(
            [m["global_id"] for m in ressources_pour_organisations(metas, ["org-1"])],
            ["g1"],
        )


if __name__ == "__main__":
    unittest.main()