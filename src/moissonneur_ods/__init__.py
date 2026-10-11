"""Moissonneur Opendatasoft (ODS/Huwise) → nœud RUDI.

Briques :

- ``ods_client`` : lecture du catalogue via l'API Explore v2.1 (pagination,
  retry/backoff, vérification de version) ;
- ``translation`` : traduction déclarative ``metas.default``/``attachments``/
  ``alternative_exports`` → métadonnées RUDI (``traducteur``, ``themes``,
  ``licences``) ;
- ``mode`` : construction des médias — mode ``lien`` (SERVICE) ou ``copie`` (FILE) ;
- ``state`` : wrapper mince autour de ``rudi_harvest_toolkit.state`` (incrémental) ;
- ``orphelins`` : détection scopée par ``collection_tag`` + suppression confirmée ;
- ``decouvertes`` : persistance du rapport ``--dry-run`` (thèmes/licences non mappés) ;
- ``cli`` : point d'entrée ``python3 -m moissonneur_ods``.
"""

__version__ = "0.1.0"
