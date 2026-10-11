import os
import tempfile
import unittest

from moissonneur_ods.decouvertes import (
    STATUT_A_TRAITER,
    STATUT_IGNORE,
    Decouvertes,
)


class TestDecouvertes(unittest.TestCase):
    def test_enregistre_et_conserve_le_statut_ignore(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = os.path.join(dossier, "decouvertes.json")
            dec = Decouvertes(chemin)
            dec.enregistrer("themes", "Culture bizarre", STATUT_A_TRAITER)
            dec.enregistrer("themes", "Culture bizarre", STATUT_A_TRAITER)
            dec.enregistrer("licences", "Licence inconnue")
            dec.sauvegarder()

            relu = Decouvertes(chemin)
            self.assertEqual(relu.statut("themes", "Culture bizarre"), STATUT_A_TRAITER)
            self.assertEqual(relu.statut("licences", "Licence inconnue"), STATUT_A_TRAITER)
            self.assertEqual(relu.non_ignores("themes"), ["Culture bizarre"])
            self.assertEqual(relu.non_ignores("licences"), ["Licence inconnue"])

    def test_reecriture_apres_edition_manuelle_ne_reecras_pas_ignore(self):
        import json

        with tempfile.TemporaryDirectory() as dossier:
            chemin = os.path.join(dossier, "decouvertes.json")
            dec = Decouvertes(chemin)
            dec.enregistrer("themes", "Culture bizarre")
            dec.sauvegarder()

            with open(chemin, encoding="utf-8") as f:
                data = json.load(f)
            data["themes"]["Culture bizarre"]["statut"] = STATUT_IGNORE
            with open(chemin, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            nouveau = Decouvertes(chemin)
            nouveau.enregistrer("themes", "Culture bizarre")
            self.assertEqual(nouveau.statut("themes", "Culture bizarre"), STATUT_IGNORE)
            self.assertEqual(nouveau.non_ignores("themes"), [])

    def test_fichier_absent_repart_vide(self):
        dec = Decouvertes("/chemin/inexistant/decouvertes.json")
        self.assertEqual(dec.non_ignores("themes"), [])
        self.assertEqual(dec.non_ignores("licences"), [])

    def test_documents_ignores_via_edition_manuelle(self):
        import json

        with tempfile.TemporaryDirectory() as dossier:
            chemin = os.path.join(dossier, "decouvertes.json")
            dec = Decouvertes(chemin)
            dec.enregistrer("themes", "Libellé vu une fois")
            dec.sauvegarder()

            with open(chemin, encoding="utf-8") as f:
                data = json.load(f)
            data["themes"]["Libellé vu une fois"]["statut"] = STATUT_IGNORE
            with open(chemin, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            relu = Decouvertes(chemin)
            self.assertEqual(relu.statut("themes", "Libellé vu une fois"), STATUT_IGNORE)
            self.assertEqual(relu.non_ignores("themes"), [])


class TestTraducteurDeclaratif(unittest.TestCase):
    def test_field_translator_avec_chemin_liste_et_defaut(self):
        from moissonneur_ods.translation.traducteur import (
            FieldTranslator,
            ObjectTranslator,
        )

        source = {"metas": {"default": {"theme": ["a", "b"], "title": "Titre"}}}
        objet = ObjectTranslator(
            {
                "titre": FieldTranslator("metas.default.title"),
                "themes": FieldTranslator("metas.default.theme", liste=True, defaut=[]),
                "manquant": FieldTranslator("metas.default.license", defaut=None),
            }
        )
        resultat = objet.traduire(source)
        self.assertEqual(resultat["titre"], "Titre")
        self.assertEqual(resultat["themes"], ["a", "b"])
        self.assertIsNone(resultat["manquant"])

    def test_champ_obligatoire_absent_leve_une_erreur(self):
        from moissonneur_ods.translation.traducteur import FieldTranslator

        with self.assertRaises(ValueError):
            FieldTranslator("metas.default.title", obligatoire=True).extraire({})


if __name__ == "__main__":
    unittest.main()