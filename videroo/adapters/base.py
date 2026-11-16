"""Contrat commun aux adaptateurs d'automatisation."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


# Etats possibles a la sortie d'un adaptateur.
TERMINE = "termine"           # resultat reel telecharge et verifie
A_VERIFIER = "a_verifier"      # on ne sait pas : surtout ne pas relancer
ECHEC = "echec"                # echec explicite, relance manuelle seulement
INTERVENTION = "intervention"  # connexion, captcha : projet mis en pause
QUOTA = "quota"                # quota atteint AVANT tout envoi : on peut basculer


@dataclass
class ContexteTache:
    projet_id: int
    tache_id: int
    cible_type: str                 # planche | image | video | miniature
    cible_id: int
    libelle: str
    prompt: str
    dossier_projet: Path
    chemin_sortie: Path
    fichiers_joints: list[Path] = field(default_factory=list)
    duree: int | None = None
    serie: str = ""
    episode: int = 1
    url_chat: str = ""
    chat_compte: str = ""          # chat dédié du compte en cours, s'il en a un
    profil: str = "principal"
    compte_id: int = 0
    compte_libelle: str = ""
    #: Passe a True A L'INSTANT OU le prompt part vraiment chez le fournisseur.
    #  C'est le seul fait qui distingue un echec sans aucun quota depense (la
    #  piece jointe n'a pas pu etre ajoutee, la zone de saisie ne s'est pas
    #  videe) d'un echec apres envoi. Le premier peut etre retente tout seul,
    #  le second jamais : il faudrait te le demander.
    envoye: bool = False
    extras: dict = field(default_factory=dict)
    journaliser: Callable[[str], None] = lambda message: None


@dataclass
class ResultatGeneration:
    etat: str
    message: str
    chemin: str = ""
    instruction: str = ""   # ce que l'utilisateur doit faire, en francais
    url: str = ""
    simulation: bool = False
    #: Le fournisseur a annonce la fin du quota APRES l'envoi. On met le compte
    #  de cote pour la suite, sans relancer quoi que ce soit sur cet element.
    compte_a_court: bool = False


class Adaptateur:
    nom = "base"

    def generer(self, ctx: ContexteTache) -> ResultatGeneration:  # pragma: no cover
        raise NotImplementedError

    def recuperer(self, ctx: ContexteTache) -> ResultatGeneration:
        """Va chercher un resultat DEJA produit, sans rien envoyer.

        « Apres un plantage, verifier si un resultat existe deja avant de
        relancer quoi que ce soit » : sans ce chemin, la seule facon de reparer
        une recolte ratee etait de relancer la generation, donc de payer deux
        fois une image que le fournisseur avait deja faite. Aucune saisie, aucun
        envoi, aucun clic sur Regenerer : on ouvre, on lit, on telecharge.
        """
        return ResultatGeneration(
            INTERVENTION,
            f"« {self.nom} » ne sait pas aller rechercher un résultat déjà produit.",
            instruction="Enregistre le fichier depuis la page du fournisseur, puis "
            "utilise « Importer le résultat ».",
        )
