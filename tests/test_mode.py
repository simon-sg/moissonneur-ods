import json
import os
import tempfile
import unittest

from moissonneur_ods.mode import (
    MODE_COPIE,
    MODE_LIEN,
    construire_medias,
    entrees_donnees,
    url_fiche_ods,
)

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _fix(nom):
    with open(os.path.join(_FIXTURES, nom), encoding="utf-8") as f:
        return json.load(f)


def _ecrire_ok(url, dossier, nom, taille_max, session):
    chemin = os.path.join(dossier, nom)
    with open(chemin, "wb") as f:
        f.write(b"contenu-fake")
    return {"chemin": chemin, "taille": 13}


def _trop_gros(url, dossier, nom, taille_max, session):
    return {"erreur": "trop_gros", "taille": 2_000_000_000}


class TestModeLien(unittest.TestCase):
    def setUp(self):
        self.url = "https://data.rennesmetropole.fr"
        self.dataset = _fix("dataset_complet.json")

    def test_tout_en_service_sans_telechargement(self):
        medias, fichiers, avertissements = construire_medias(
            self.dataset, "local-id", self.url, MODE_LIEN,
        )
        self.assertEqual(fichiers, [])
        self.assertEqual(avertissements, [])
        self.assertTrue(all(m["media_type"] == "SERVICE" for m in medias))
        self.assertEqual(len(medias), 3)
        urls = {m["connector"]["url"] for m in medias}
        self.assertIn(
            "https://data.rennesmetropole.fr/api/explore/v2.1/catalog/datasets/"
            "frequentation-detaillee-du-musee-des-beaux-arts/exports/csv",
            urls,
        )

    def test_url_fiche(self):
        self.assertEqual(
            url_fiche_ods(self.url, "mon-jdd"),
            "https://data.rennesmetropole.fr/explore/dataset/mon-jdd/",
        )

    def test_entrees_donnees_compte_attachments_et_exports(self):
        entrees = entrees_donnees(self.dataset, self.url)
        self.assertEqual(len(entrees), 3)
        origines = [e["origine"] for e in entrees]
        self.assertEqual(origines, ["attachment", "attachment", "alternative_export"])


class TestModeCopie(unittest.TestCase):
    def setUp(self):
        self.url = "https://data.rennesmetropole.fr"
        self.dataset = _fix("dataset_complet.json")
        self.dossier = tempfile.mkdtemp(prefix="moissonneur-ods-test-")

    def test_telechargement_ok_fichiers_file(self):
        medias, fichiers, avertissements = construire_medias(
            self.dataset, "local-id", self.url, MODE_COPIE,
            dossier_telechargement=self.dossier, telecharger=_ecrire_ok,
        )
        self.assertEqual(avertissements, [])
        self.assertEqual(len(fichiers), 3)
        self.assertTrue(all(m["media_type"] == "FILE" for m in medias))
        self.assertEqual(len(os.listdir(self.dossier)), 3)
        for chemin in fichiers:
            self.assertTrue(os.path.isfile(chemin))

    def test_fichier_trop_gros_repli_en_service_sans_planter(self):
        medias, fichiers, avertissements = construire_medias(
            self.dataset, "local-id", self.url, MODE_COPIE,
            dossier_telechargement=self.dossier, telecharger=_trop_gros,
        )
        self.assertEqual(fichiers, [])
        self.assertEqual(len(avertissements), 3)
        self.assertTrue(all(m["media_type"] == "SERVICE" for m in medias))

    def test_les_file_passent_toujours_avant_les_service(self):
        def _mixte(url, dossier, nom, taille_max, session):
            if "csv" in nom:
                chemin = os.path.join(dossier, nom)
                open(chemin, "wb").close()
                return {"chemin": chemin, "taille": 0}
            if "dictionnaire" in nom:
                return {"erreur": "reseau", "detail": "timeout"}
            return _ecrire_ok(url, dossier, nom, taille_max, session)

        medias, fichiers, avertissements = construire_medias(
            self.dataset, "local-id", self.url, MODE_COPIE,
            dossier_telechargement=self.dossier, telecharger=_mixte,
        )
        types = [m["media_type"] for m in medias]
        self.assertEqual(types[:2], ["FILE", "FILE"])
        self.assertEqual(types[2], "SERVICE")
        self.assertEqual(len(fichiers), 2)
        self.assertEqual(len(avertissements), 1)

    def test_mode_inconnu_rejete(self):
        with self.assertRaises(ValueError):
            construire_medias(self.dataset, "local-id", self.url, "autre")


if __name__ == "__main__":
    unittest.main()