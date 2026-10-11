import json
import os
import unittest

from moissonneur_ods.ods_client import OdsError, lister_datasets, obtenir_dataset

from support import FakeOdsSession, FakeResponse

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _fix(nom):
    with open(os.path.join(_FIXTURES, nom), encoding="utf-8") as f:
        return json.load(f)


class TestPagination(unittest.TestCase):
    def setUp(self):
        self.url = "https://data.rennesmetropole.fr"
        self.page0 = _fix("catalog_page_0.json")
        self.page1 = _fix("catalog_page_1.json")

    def test_pagination_sarrete_a_total_count_sans_doublon(self):
        session = FakeOdsSession({0: FakeResponse(200, self.page0),
                                  2: FakeResponse(200, self.page1)})
        ids = [d["dataset_id"] for d in lister_datasets(self.url, taille_page=2, session=session)]
        self.assertEqual(ids, ["jeu-a", "jeu-b", "jeu-c"])
        self.assertEqual([offset for _, offset in session.appels], [0, 2])

    def test_pagination_tolere_un_total_qui_change(self):
        page0 = _fix("catalog_total_change_page_0.json")   # total 5
        page1 = _fix("catalog_total_change_page_1.json")   # total 4
        page3 = _fix("catalog_page_3.json")                # total 4, page vide
        session = FakeOdsSession({0: FakeResponse(200, page0),
                                  2: FakeResponse(200, page1),
                                  3: FakeResponse(200, page3)})
        ids = [d["dataset_id"] for d in lister_datasets(self.url, taille_page=2, session=session)]
        self.assertEqual(ids, ["jeu-a", "jeu-b", "jeu-c"])
        self.assertEqual([offset for _, offset in session.appels], [0, 2, 3])

    def test_page_vide_interrompt_sans_boucle_infinie(self):
        session = FakeOdsSession({0: FakeResponse(200, {"total_count": 3, "results": []})})
        resultat = list(lister_datasets(self.url, taille_page=2, session=session))
        self.assertEqual(resultat, [])
        self.assertEqual(len(session.appels), 1)


class TestErreursClaires(unittest.TestCase):
    def test_404_remonte_une_erreur_explicite(self):
        session = FakeOdsSession({0: FakeResponse(404, None, text="not found")})
        with self.assertRaises(OdsError) as ctx:
            list(lister_datasets("https://plateforme-v1.fr", session=session))
        self.assertIn("v2.1", str(ctx.exception))

    def test_reponse_non_json_remonte_une_erreur_explicite(self):
        class SessionNonJson:
            def get(self, url, params=None, timeout=None, **kw):
                return FakeResponse(200, None, text="<html>oops</html>")
        with self.assertRaises(OdsError) as ctx:
            list(lister_datasets("https://x.fr", session=SessionNonJson()))
        self.assertIn("non-JSON", str(ctx.exception))

    def test_cles_manquantes_remontent_une_erreur_explicite(self):
        session = FakeOdsSession({0: FakeResponse(200, {"foo": 1})})
        with self.assertRaises(OdsError) as ctx:
            list(lister_datasets("https://x.fr", session=session))
        self.assertIn("results", str(ctx.exception))

    def test_retry_puis_echec_apres_statuts_transitoires(self):
        reponse_500 = FakeResponse(500, None, text="boum")

        class Session5xx:
            def __init__(self):
                self.appels = 0
            def get(self, url, params=None, timeout=None, **kw):
                self.appels += 1
                return reponse_500

        session = Session5xx()
        with self.assertRaises(OdsError):
            list(lister_datasets("https://x.fr", session=session))
        self.assertEqual(session.appels, 4)


class TestObtenirDataset(unittest.TestCase):
    def test_retourne_le_jeu_complet(self):
        dataset = _fix("dataset_complet.json")
        session = FakeOdsSession({})
        session.appels = []
        def _get(url, params=None, timeout=None, **kw):
            session.appels.append(url)
            return FakeResponse(200, dataset)
        session.get = _get
        resultat = obtenir_dataset("https://data.rennesmetropole.fr", "mon-jdd", session=session)
        self.assertEqual(resultat["dataset_id"], "frequentation-detaillee-du-musee-des-beaux-arts")


if __name__ == "__main__":
    unittest.main()