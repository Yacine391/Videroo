"""Mode assisté : l'application préparé tout, l'utilisateur fait le geste final.

Aucune génération n'est declenchee ici. La tâche reste « à vérifier » jusqu'à
l'import manuel du résultat, donc aucun quota n'est consomme à l'insu de
l'utilisateur.
"""
from __future__ import annotations

from pathlib import Path

from .. import db, reglages
from . import base, navigateur, selecteurs


class AdaptateurAssiste(base.Adaptateur):
    def __init__(self, site: str) -> None:
        self.site = site
        self.nom = f"{site}-assiste"

    def generer(self, ctx: base.ContexteTache) -> base.ResultatGeneration:
        sel = selecteurs.charger().get(self.site, {})
        url = ctx.url_chat or reglages.lire(f"url_{self.site}", "") or sel.get("url", "")

        dossier = ctx.dossier_projet / "imports"
        dossier.mkdir(parents=True, exist_ok=True)
        fichier_prompt = dossier / f"tache-{ctx.tache_id:04d}_prompt.txt"
        entete = [f"# {ctx.libelle}", f"# Site : {self.site}", f"# URL : {url}"]
        if ctx.duree:
            entete.append(f"# Durée a regler dans Grok : {ctx.duree} s")
        if ctx.fichiers_joints:
            entete.append("# Fichiers a joindre :")
            entete += [f"#   {c}" for c in ctx.fichiers_joints]
        fichier_prompt.write_text(
            "\n".join(entete) + "\n\n" + ctx.prompt + "\n", encoding="utf-8"
        )

        ouverture = ""
        try:
            page = navigateur.session().page(self.site, url, ctx.profil)
            ouverture = f"La page {page.url} est ouverte dans le navigateur dédié."
        except Exception as exc:
            ouverture = (
                f"Le navigateur dédié n'a pas pu être ouvert ({exc}). "
                f"Ouvre {url} toi-même dans Chrome."
            )

        liste = (
            "\n".join(f"  - {c}" for c in ctx.fichiers_joints)
            if ctx.fichiers_joints
            else "  (aucun fichier a joindre)"
        )
        instruction = (
            f"{ouverture}\n"
            f"1) Copie le prompt depuis la fiche de la tâche (ou le fichier "
            f"{fichier_prompt}).\n"
            f"2) Joins ces fichiers :\n{liste}\n"
            + (f"3) Règle la durée sur {ctx.duree} s.\n" if ctx.duree else "")
            + "4) Lance UNE SEULE génération.\n"
            "5) Reviens ici et clique « Importer le résultat » pour enregistrer le "
            "fichier obtenu."
        )
        db.journaliser(
            f"Mode assisté préparé pour {ctx.libelle} : prompt ecrit dans "
            f"{fichier_prompt.name}. Aucune génération lancée par l'application.",
            ctx.projet_id,
            "info",
            ctx.tache_id,
        )
        return base.ResultatGeneration(
            etat=base.A_VERIFIER,
            message="Mode assisté : en attente de ton import. Aucune génération lancée par l'application.",
            instruction=instruction,
            url=url,
        )
