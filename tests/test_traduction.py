import json
import os
import unittest

from rudi_harvest_toolkit.pivot import LICENCE_ETALAB

from moissonneur_ods.orphelins import collection_tag_pour
from moissonneur_ods.translation.traducteur import local_id_pour, traduire_dataset

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _fix(nom):
    with open(os.path.join(_FIXTURES, nom), encoding="utf-8") as f:
        return json.load(f)


def _empreinte_fake(url, taille_max, session):
    """Mode lien calcule désormais une empreinte distante (FILE hybride, voir
    mode.py) même pour un dataset "complet" — jamais de vrai appel réseau en test."""
    return {"hash": "deadbeef" * 8, "taille": 42}


class TestTraductionJddComplet(unittest.TestCase):
    def setUp(self):
        self.url = "https://data.rennesmetropole.fr"
        self.dataset = _fix("dataset_complet.json")

    def test_traduction_pleine(self):
        rudi, infos = traduire_dataset(
            self.dataset, url_plateforme=self.url, mode="lien",
            table_themes={"Culture, Sports, Loisirs": "culture"},
            collection_tag=collection_tag_pour(self.url),
            empreinte_distante=_empreinte_fake,
        )
        self.assertEqual(infos["theme"], "culture")
        self.assertEqual(rudi["theme"], "culture")
        self.assertIn("musée", rudi["keywords"])
        self.assertNotIn("Culture, Sports, Loisirs", rudi["keywords"])
        self.assertEqual(rudi["dataset_dates"]["updated"], "2025-03-12T09:41:37Z")
        # created ET updated : le nœud dérive created=now() si seul updated est
        # fourni (voir rudi_harvest_toolkit.pivot) — toujours les deux, identiques.
        self.assertEqual(rudi["dataset_dates"]["created"], "2025-03-12T09:41:37Z")
        self.assertEqual(rudi["producer"]["organization_name"], "Ville de Rennes")
        self.assertEqual(rudi["access_condition"]["licence"]["licence_type"], "STANDARD")
        self.assertEqual(rudi["access_condition"]["licence"]["licence_label"], "odbl-1.0")
        self.assertEqual(rudi["collection_tag"], f"moissonneur-ods:{self.url}")
        self.assertEqual(infos["nb_medias_ods"], 3)
        self.assertEqual(infos["fichiers_locaux"], [])
        # Métadonnées ODS jusque-là perdues : langue, fréquence de mise à jour,
        # territoire/référence géographique (ces deux derniers en mots-clés,
        # faute de champ RUDI dédié).
        self.assertEqual(rudi["resource_languages"], ["fr"])
        self.assertEqual(rudi["dataset_update_frequency"], "monthly")
        self.assertIn("Rennes Métropole", rudi["keywords"])
        self.assertIn("fr_35", rudi["keywords"])

    def test_frequence_mise_a_jour_non_mappee_avertit_et_repli_unknown(self):
        dataset = dict(self.dataset)
        dataset["metas"] = {"default": dict(self.dataset["metas"]["default"],
                                            update_frequency="Toutes les lunes bleues")}
        rudi, infos = traduire_dataset(dataset, url_plateforme=self.url, mode="lien",
                                       empreinte_distante=_empreinte_fake)
        self.assertEqual(rudi["dataset_update_frequency"], "unknown")
        self.assertTrue(any("fréquence de mise à jour ODS non référencée" in a
                           for a in infos["avertissements"]))

    def test_langue_et_frequence_absentes_omises_sans_avertissement(self):
        dataset = dict(self.dataset)
        defaut = dict(self.dataset["metas"]["default"])
        defaut.pop("language", None)
        defaut.pop("update_frequency", None)
        dataset["metas"] = {"default": defaut}
        rudi, infos = traduire_dataset(dataset, url_plateforme=self.url, mode="lien",
                                       empreinte_distante=_empreinte_fake)
        self.assertNotIn("resource_languages", rudi)
        self.assertNotIn("dataset_update_frequency", rudi)
        self.assertFalse(any("fréquence de mise à jour" in a for a in infos["avertissements"]))

    def test_local_id_deterministe_et_scopé_par_plateforme(self):
        rudi, _ = traduire_dataset(self.dataset, url_plateforme=self.url, mode="lien",
                                   empreinte_distante=_empreinte_fake)
        rudi2, _ = traduire_dataset(self.dataset, url_plateforme=self.url, mode="lien",
                                    empreinte_distante=_empreinte_fake)
        self.assertEqual(rudi["local_id"], rudi2["local_id"])
        autre_url = "https://data.autreplateforme.fr"
        rudi_autre, _ = traduire_dataset(self.dataset, url_plateforme=autre_url, mode="lien",
                                         empreinte_distante=_empreinte_fake)
        self.assertNotEqual(rudi["local_id"], rudi_autre["local_id"])
        attendu = local_id_pour(self.url, self.dataset["dataset_id"])
        self.assertEqual(rudi["local_id"], attendu)

    def test_description_html_convertie_en_texte_brut(self):
        rudi, _ = traduire_dataset(self.dataset, url_plateforme=self.url, mode="lien",
                                   empreinte_distante=_empreinte_fake)
        texte = rudi["summary"][0]["text"]
        self.assertNotIn("<p>", texte)
        self.assertIn("Entrées payantes", texte)


class TestJddSansThemeSansLicenceSansExport(unittest.TestCase):
    def setUp(self):
        self.url = "https://data.rennesmetropole.fr"
        self.dataset = _fix("dataset_sans_theme_licence.json")

    def test_repli_theme_et_licence_absente_avec_avertissement(self):
        rudi, infos = traduire_dataset(self.dataset, url_plateforme=self.url, mode="copie")
        self.assertEqual(infos["theme"], "society")
        self.assertEqual(rudi["theme"], "society")
        self.assertEqual(infos["licence_statut"], "absent")
        self.assertEqual(rudi["access_condition"]["licence"], LICENCE_ETALAB)
        self.assertEqual(infos["nb_medias_ods"], 0)

        noms = {m["media_name"] for m in rudi["available_formats"]}
        self.assertIn("source-metadata", noms)
        self.assertIn("source-data", noms)
        self.assertNotIn("Fréquentation 2024 (CSV)", noms)

    def test_mode_copie_reste_metadonnees_seules_sans_echouer(self):
        _, infos = traduire_dataset(self.dataset, url_plateforme=self.url, mode="copie")
        self.assertEqual(infos["fichiers_locaux"], [])

    def test_avertissement_pour_absence_theme_et_licence(self):
        _, infos = traduire_dataset(self.dataset, url_plateforme=self.url, mode="lien")
        messages = " ".join(infos["avertissements"])
        self.assertIn("aucun thème", messages)
        self.assertIn("licence ODS absente", messages)
        self.assertIn("Etalab", messages)

    def test_licence_repli_configurable(self):
        rudi, infos = traduire_dataset(
            self.dataset, url_plateforme=self.url, mode="lien", licence_repli=LICENCE_ETALAB,
        )
        self.assertEqual(infos["licence_statut"], "absent_repli")
        self.assertEqual(rudi["access_condition"]["licence"]["licence_label"], "etalab-2.0")


class TestThemeNonMappeEtLicenceInconnue(unittest.TestCase):
    def setUp(self):
        self.url = "https://data.rennesmetropole.fr"
        self.dataset = _fix("dataset_data_visible_false.json")

    def test_theme_non_mappe_warning_et_repli_sans_planter(self):
        rudi, infos = traduire_dataset(self.dataset, url_plateforme=self.url, mode="lien")
        self.assertEqual(infos["themes_non_mappes"], ["Thème inconnu XYZ"])
        self.assertEqual(rudi["theme"], "society")
        messages = " ".join(infos["avertissements"])
        self.assertIn("Thème inconnu XYZ", messages)
        self.assertEqual(infos["licence_statut"], "inconnu")
        licence = rudi["access_condition"]["licence"]
        self.assertEqual(licence["licence_type"], "CUSTOM")
        self.assertEqual(licence["custom_licence_uri"], "https://example.org/licence-bizarre")

    def test_table_themes_plateforme_supplante_le_repli(self):
        table = {"Thème inconnu XYZ": "environment"}
        _, infos = traduire_dataset(
            self.dataset, url_plateforme=self.url, mode="lien", table_themes=table,
        )
        self.assertEqual(infos["theme"], "environment")
        self.assertEqual(infos["themes_non_mappes"], [])


if __name__ == "__main__":
    unittest.main()