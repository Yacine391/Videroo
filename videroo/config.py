"""Configuration locale. Tout reste sur la machine de l'utilisateur."""
from __future__ import annotations

import os
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent

DOSSIER_DONNEES = Path(os.environ.get("VIDEROO_DONNEES", RACINE / "donnees"))
DOSSIER_PROJETS = DOSSIER_DONNEES / "projets"
DOSSIER_PROFIL_NAVIGATEUR = DOSSIER_DONNEES / "profil_navigateur"
DOSSIER_PROFILS = DOSSIER_DONNEES / "profils"
# Profil historique : celui qui existait avant le multi-comptes.
PROFIL_PRINCIPAL = "principal"
DOSSIER_JOURNAUX = DOSSIER_DONNEES / "journaux"
DOSSIER_IMPORTS = DOSSIER_DONNEES / "imports"
CHEMIN_BASE = DOSSIER_DONNEES / "videroo.db"

HOTE = os.environ.get("VIDEROO_HOTE", "127.0.0.1")
PORT = int(os.environ.get("VIDEROO_PORT", "8077"))

# Durees acceptees par l'interface Grok (Imagine Video).
DUREES_GROK = (6, 10, 15)

# Calibrage dialogue du metaprompt V6 : duree -> (mots min, mots max)
CALIBRAGE_MOTS = {6: (18, 27), 10: (30, 45), 15: (45, 65)}

# Garde-fous quotas.
GENERATIONS_SIMULTANEES = 1
DELAI_ATTENTE_IMAGE = int(os.environ.get("VIDEROO_DELAI_IMAGE", "420"))
DELAI_ATTENTE_VIDEO = int(os.environ.get("VIDEROO_DELAI_VIDEO", "900"))
#: Une video Grok ne sort jamais en quelques secondes. En dessous de ce
#  delai, une video « nouvelle » est en realite une ancienne que la page
#  vient de charger en descendant dans la conversation.
DELAI_MINIMUM_VIDEO = int(os.environ.get("VIDEROO_DELAI_MIN_VIDEO", "40"))

# Export video par defaut (vertical).
EXPORT_LARGEUR = 1080
EXPORT_HAUTEUR = 1920
EXPORT_FPS = 30

DUREE_CIBLE_MIN = 70.0
DUREE_CIBLE_MAX = 80.0

URL_CHATGPT = "https://chatgpt.com/"
URL_GROK = "https://grok.com/"

#: Les trois familles d'histoires. Chacune a son metaprompt et sa facon
#: d'ecrire le pack ; tout le reste de la chaine est identique.
TYPES_PROJET = {
    "fruits": "Histoire de fruits",
    "chronique": "Chronique",
    "bebe": "B\u00e9b\u00e9 IA",
    # Découpage en PLANS (« PLAN 01 — … »), prompt image de départ, prompt
    # vidéo, BLOC COMMUN : « Le mariage forcé », 16 novembre 2026.
    "serie": "S\u00e9rie r\u00e9aliste en plans",
}
TYPE_PROJET_DEFAUT = "fruits"

STATUTS_PROJET = ("brouillon", "valide", "en_cours", "pause", "arrete", "termine")
# « quota » : le fournisseur a refusé faute de quota. Rien produit, rien payé :
# ce statut ne consomme PAS l'unique génération automatique de l'élément.
STATUTS_TACHE = ("en_attente", "en_cours", "termine", "echec", "a_verifier", "annule", "quota")
# Statuts qui consomment le droit a UNE generation automatique.
STATUTS_TACHE_VIVANTS = ("en_attente", "en_cours", "termine", "echec", "a_verifier")


def chemin_profil(nom: str | None) -> Path:
    """Dossier de profil navigateur d'un compte. « principal » = profil historique."""
    if not nom or nom == PROFIL_PRINCIPAL:
        return DOSSIER_PROFIL_NAVIGATEUR
    return DOSSIER_PROFILS / nom


def preparer_dossiers() -> None:
    for d in (
        DOSSIER_DONNEES,
        DOSSIER_PROJETS,
        DOSSIER_PROFIL_NAVIGATEUR,
        DOSSIER_PROFILS,
        DOSSIER_JOURNAUX,
        DOSSIER_IMPORTS,
    ):
        d.mkdir(parents=True, exist_ok=True)
