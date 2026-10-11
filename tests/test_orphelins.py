import unittest
from unittest import mock

from moissonneur_ods.orphelins import (
    collection_tag_pour,
    detecter_orphelins,
    menage_orphelins,
    supprimer_orphelins,
)

_URL = "https://data.rennesmetropole.fr"


class TestScopingParCollectionTag(unittest.TestCase):
    def setUp(self):
        self.toutes = [
            {"local_id": "l1", "resource_title": "Titre 1",
             "collection_tag": collection_tag_pour(_URL)},
            {"local_id": "l2", "resource_title": "Titre 2",
             "collection_tag": collection_tag_pour(_URL)},
            {"local_id": "l3", "resource_title": "Autre moissonneur",
             "collection_tag": "moissonneur-ods:https://data.autre.fr"},
            {"local_id": "l4", "resource_title": "Moissonneuse-batteuse", "collection_tag": None},
            {"local_id": "l5", "resource_title": "Sans tag"},
        ]

    def test_seuls_les_orphelins_du_tag_du_run_sont_proposes(self):
        resultat = detecter_orphelins({}, _URL, {"l2"},
                                      toutes_metadonnees=self.toutes)
        self.assertEqual(resultat["collection_tag"], collection_tag_pour(_URL))
        self.assertEqual(resultat["tagged"], 2)
        self.assertEqual(resultat["sans_tag"], 2)
        self.assertEqual(resultat["total_noeud"], 5)
        ids_proposes = [m["local_id"] for m in resultat["orphelins"]]
        self.assertEqual(ids_proposes, ["l1"])
        self.assertNotIn("l3", ids_proposes)
        self.assertNotIn("l4", ids_proposes)
        self.assertNotIn("l5", ids_proposes)

    def test_aucun_orphelin_si_tout_est_actif(self):
        resultat = detecter_orphelins({}, _URL, {"l1", "l2"},
                                      toutes_metadonnees=self.toutes)
        self.assertEqual(resultat["orphelins"], [])

    def test_automatique_reclame_toutes_metadonnees_rudi_sans_injection(self):
        with mock.patch("moissonneur_ods.orphelins.toutes_metadonnees_rudi",
                        return_value=self.toutes) as m:
            resultat = detecter_orphelins({"url": "x"}, _URL, set())
        m.assert_called_once_with({"url": "x"})
        self.assertEqual(len(resultat["orphelins"]), 2)


class TestSuppressionConfirmee(unittest.TestCase):
    def setUp(self):
        self.orphelins = [
            {"local_id": "l1", "resource_title": "T1", "global_id": "g1",
             "collection_tag": collection_tag_pour(_URL)},
        ]

    def test_suppression_sans_saisie_exacte_est_annulee(self):
        with mock.patch("moissonneur_ods.orphelins.supprimer_dataset",
                        return_value=True) as suppr:
            ok, echecs = supprimer_orphelins({}, self.orphelins, confirmer=lambda msg: "oui")
        self.assertEqual((ok, echecs), (0, 0))
        suppr.assert_not_called()

    def test_suppression_apres_saisie_exacte(self):
        with mock.patch("moissonneur_ods.orphelins.supprimer_dataset",
                        return_value=True) as suppr:
            ok, echecs = supprimer_orphelins({}, self.orphelins,
                                             confirmer=lambda msg: "SUPPRIMER")
        self.assertEqual((ok, echecs), (1, 0))
        suppr.assert_called_once_with({}, "g1")

    def test_suppression_echouee_comptabilise_les_echecs(self):
        with mock.patch("moissonneur_ods.orphelins.supprimer_dataset",
                        return_value=False):
            ok, echecs = supprimer_orphelins({}, self.orphelins,
                                             confirmer=lambda msg: "SUPPRIMER")
        self.assertEqual((ok, echecs), (0, 1))

    def test_menage_dry_run_ne_supprime_jamais(self):
        toutes = [
            {"local_id": "l1", "resource_title": "T1",
             "collection_tag": collection_tag_pour(_URL)},
        ]
        with mock.patch("moissonneur_ods.orphelins.supprimer_dataset",
                        return_value=True) as suppr:
            resultat = menage_orphelins({}, _URL, set(), toutes_metadonnees=toutes,
                                        dry_run=True, confirmer=lambda msg: "SUPPRIMER")
        self.assertEqual(resultat["orphelins"], [toutes[0]])
        suppr.assert_not_called()


if __name__ == "__main__":
    unittest.main()