"""Mode DEMONSTRATION : fabrique des fichiers factices pour tester le pipeline.

Rien ici n'est une vraie génération. Chaque résultat est marque [SIMULATION],
place dans un sous-dossier _simulation et renvoye avec l'etat « à vérifier ».
Aucun quota IA n'est consomme.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..media import chemin_outil
from . import base


class AdaptateurDemo(base.Adaptateur):
    nom = "demo"

    def generer(self, ctx: base.ContexteTache) -> base.ResultatGeneration:
        dossier = ctx.dossier_projet / "_simulation"
        dossier.mkdir(parents=True, exist_ok=True)
        cible = dossier / ctx.chemin_sortie.name
        texte = f"SIMULATION {ctx.cible_type} {ctx.extras.get('scene_numero', '')}"
        try:
            if ctx.cible_type == "video":
                duree = float(ctx.duree or 6)
                commande = [
                    chemin_outil("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"color=c=0x202020:s=1080x1920:d={duree}:r=25",
                    "-f", "lavfi", "-i",
                    f"sine=frequency=330:duration={duree}:sample_rate=48000",
                    "-vf",
                    f"drawtext=text='{texte}':fontcolor=red:fontsize=64:x=(w-tw)/2:y=(h-th)/2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", "-t", str(duree), str(cible),
                ]
            else:
                commande = [
                    chemin_outil("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=0x303030:s=1080x1920:d=1",
                    "-vf",
                    f"drawtext=text='{texte}':fontcolor=red:fontsize=64:x=(w-tw)/2:y=(h-th)/2",
                    "-frames:v", "1", str(cible.with_suffix(".png")),
                ]
                cible = cible.with_suffix(".png")
            res = subprocess.run(commande, capture_output=True, text=True, timeout=300)
            if res.returncode != 0:
                return base.ResultatGeneration(
                    base.ECHEC, f"Simulation impossible : {(res.stderr or '')[-300:]}",
                    simulation=True,
                )
        except Exception as exc:
            return base.ResultatGeneration(
                base.ECHEC, f"Simulation impossible : {exc}", simulation=True
            )
        return base.ResultatGeneration(
            base.A_VERIFIER,
            "SIMULATION — fichier factice, ce n'est PAS une génération réelle.",
            chemin=str(cible),
            instruction="Ce fichier sert uniquement a tester le pipeline local. "
            "Il n'est pas rattaché à la scène et ne doit pas être exporté.",
            simulation=True,
        )
