"""Traduction déclarative des métadonnées Opendatasoft vers le modèle RUDI."""

from .licences import LICENCES_CONNUES, resoudre_licence
from .themes import (
    LIBELLES_THEMES,
    THEME_REPLI,
    THEMES_RUDI,
    choisir_theme,
    charger_mapping_plateforme,
    normaliser_libelle,
)
from .traducteur import ObjectTranslator, FieldTranslator, traduire_dataset

__all__ = [
    "LICENCES_CONNUES",
    "resoudre_licence",
    "LIBELLES_THEMES",
    "THEME_REPLI",
    "THEMES_RUDI",
    "choisir_theme",
    "charger_mapping_plateforme",
    "normaliser_libelle",
    "ObjectTranslator",
    "FieldTranslator",
    "traduire_dataset",
]
