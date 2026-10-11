import json
import os
import tempfile
import unittest
from unittest import mock

from moissonneur_ods import cli


class TestExecuterReinitialisation(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.mkdtemp()
        self.chemin_conf = os.path.join(self.dossier, "rudi_node.json")
        with open(self.chemin_conf, "w", encoding="utf-8") as f:
            json.dump({
                "url": "http://localhost:3032",
                "url_catalog": "http://localhost:3030/catalog",
                "usr": "x", "pwd": "y",
                "portail": {"mode": "portail", "provider_uuid": "p1", "node_uuid": "n1"},
            }, f)
        # Fichiers d'état locaux, scopés par plateforme (comme le vrai nommage).
        for nom in ("etat_data_rennesmetropole_fr.json", "etat_data_autre_fr.json"):
            with open(os.path.join(self.dossier, nom), "w", encoding="utf-8") as f:
                f.write("{}")

    def _args(self):
        return mock.Mock(rudi_conf=self.chemin_conf, dossier_etat=self.dossier)

    def test_sans_confirmation_rien_nest_supprime(self):
        with mock.patch("moissonneur_ods.cli.toutes_metadonnees_rudi",
                        return_value=[{"global_id": "g1"}, {"global_id": "g2"}]), \
             mock.patch("moissonneur_ods.cli.lister_organisations",
                        return_value=[{"organization_id": "o1"}]), \
             mock.patch("moissonneur_ods.cli.supprimer_dataset") as mock_ds, \
             mock.patch("moissonneur_ods.cli.supprimer_organisation") as mock_org:
            code = cli.executer_reinitialisation(self._args(), confirmer=lambda _: "")
        self.assertEqual(code, 0)
        mock_ds.assert_not_called()
        mock_org.assert_not_called()
        self.assertTrue(os.path.isfile(os.path.join(self.dossier, "etat_data_rennesmetropole_fr.json")))

    def test_confirmation_exacte_supprime_ressources_organisations_et_etat(self):
        with mock.patch("moissonneur_ods.cli.toutes_metadonnees_rudi",
                        return_value=[{"global_id": "g1"}, {"global_id": "g2"}]), \
             mock.patch("moissonneur_ods.cli.lister_organisations",
                        return_value=[{"organization_id": "o1"}]), \
             mock.patch("moissonneur_ods.cli.supprimer_dataset", return_value=True) as mock_ds, \
             mock.patch("moissonneur_ods.cli.supprimer_organisation", return_value=True) as mock_org:
            code = cli.executer_reinitialisation(self._args(), confirmer=lambda _: "REINITIALISER")
        self.assertEqual(code, 0)
        self.assertEqual(mock_ds.call_count, 2)
        mock_org.assert_called_once()
        self.assertEqual(
            sorted(os.listdir(self.dossier)),
            ["rudi_node.json"],
        )

    def test_conf_portail_jamais_touchee(self):
        with mock.patch("moissonneur_ods.cli.toutes_metadonnees_rudi", return_value=[]), \
             mock.patch("moissonneur_ods.cli.lister_organisations", return_value=[]), \
             mock.patch("moissonneur_ods.cli.supprimer_dataset"), \
             mock.patch("moissonneur_ods.cli.supprimer_organisation"):
            cli.executer_reinitialisation(self._args(), confirmer=lambda _: "REINITIALISER")
        with open(self.chemin_conf, encoding="utf-8") as f:
            conf = json.load(f)
        self.assertEqual(conf["portail"]["mode"], "portail")
        self.assertEqual(conf["portail"]["provider_uuid"], "p1")

    def test_echecs_de_suppression_comptabilises(self):
        with mock.patch("moissonneur_ods.cli.toutes_metadonnees_rudi",
                        return_value=[{"global_id": "g1"}]), \
             mock.patch("moissonneur_ods.cli.lister_organisations", return_value=[]), \
             mock.patch("moissonneur_ods.cli.supprimer_dataset", return_value=False):
            code = cli.executer_reinitialisation(self._args(), confirmer=lambda _: "REINITIALISER")
        self.assertEqual(code, 1)

    def test_rien_a_reinitialiser_si_noeud_deja_vide(self):
        for nom in os.listdir(self.dossier):
            if nom.startswith("etat_"):
                os.remove(os.path.join(self.dossier, nom))
        appels = []
        with mock.patch("moissonneur_ods.cli.toutes_metadonnees_rudi", return_value=[]), \
             mock.patch("moissonneur_ods.cli.lister_organisations", return_value=[]):
            code = cli.executer_reinitialisation(
                self._args(), confirmer=lambda msg: appels.append(msg) or "REINITIALISER")
        self.assertEqual(code, 0)
        self.assertEqual(appels, [])  # jamais demandé de confirmation si rien à faire


if __name__ == "__main__":
    unittest.main()
