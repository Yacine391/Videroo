"""Montage et export : un seul encodage ffmpeg pour tout l'episode.

On evite les conversions repetees : chaque clip est coupe, mis au format
vertical et concatene dans une unique passe ffmpeg.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .media import InfosMedia, analyser_media, chemin_outil


@dataclass
class Clip:
    numero: int
    chemin: str
    trim_debut: float = 0.0
    trim_fin: float = 0.0
    titre: str = ""
    infos: InfosMedia | None = None

    @property
    def duree_source(self) -> float:
        return self.infos.duree if self.infos else 0.0

    @property
    def duree_utile(self) -> float:
        return max(0.0, self.duree_source - self.trim_debut - self.trim_fin)


@dataclass
class ReglagesExport:
    largeur: int = config.EXPORT_LARGEUR
    hauteur: int = config.EXPORT_HAUTEUR
    fps: int = config.EXPORT_FPS
    vitesse: float = 1.0
    musique_path: str | None = None
    musique_volume: float = 0.15
    srt_path: str | None = None
    incruster_srt: bool = False
    crf: int = 20
    preset: str = "medium"
    duree_cible_min: float = config.DUREE_CIBLE_MIN


@dataclass
class PlanMontage:
    clips: list[Clip] = field(default_factory=list)
    duree_brute: float = 0.0
    duree_finale: float = 0.0
    avertissements: list[str] = field(default_factory=list)
    bloquants: list[str] = field(default_factory=list)

    @property
    def utilisable(self) -> bool:
        return bool(self.clips) and not self.bloquants

    def to_dict(self) -> dict:
        return {
            "clips": [
                {
                    "numero": c.numero,
                    "titre": c.titre,
                    "chemin": c.chemin,
                    "duree_source": round(c.duree_source, 3),
                    "duree_utile": round(c.duree_utile, 3),
                    "trim_debut": c.trim_debut,
                    "trim_fin": c.trim_fin,
                    "a_audio": bool(c.infos and c.infos.a_audio),
                    "largeur": c.infos.largeur if c.infos else 0,
                    "hauteur": c.infos.hauteur if c.infos else 0,
                    "lisible": bool(c.infos and c.infos.lisible),
                }
                for c in self.clips
            ],
            "duree_brute": round(self.duree_brute, 2),
            "duree_finale": round(self.duree_finale, 2),
            "avertissements": self.avertissements,
            "bloquants": self.bloquants,
            "utilisable": self.utilisable,
        }


def preparer_plan(clips: list[Clip], reglages: ReglagesExport) -> PlanMontage:
    plan = PlanMontage()
    vitesse = reglages.vitesse or 1.0
    if vitesse <= 0:
        plan.bloquants.append("La vitesse doit être superieure a 0.")
        vitesse = 1.0

    for clip in clips:
        if clip.infos is None:
            clip.infos = analyser_media(clip.chemin)
        plan.clips.append(clip)
        etiquette = f"Scène {clip.numero}"
        if not clip.infos.existe:
            plan.bloquants.append(f"{etiquette} : fichier vidéo absent ({clip.chemin}).")
            continue
        if not clip.infos.lisible:
            plan.bloquants.append(
                f"{etiquette} : vidéo illisible — {clip.infos.erreur or 'cause inconnue'}."
            )
            continue
        if clip.duree_utile <= 0.05:
            plan.bloquants.append(
                f"{etiquette} : les coupes laissent {clip.duree_utile:.2f} s de vidéo."
            )
            continue
        if not clip.infos.a_audio:
            plan.avertissements.append(
                f"{etiquette} : aucune piste audio, du silence sera inséré "
                "(les dialogues Grok sont peut-être absents de ce clip)."
            )
        if clip.infos.largeur and (
            clip.infos.largeur < reglages.largeur or clip.infos.hauteur < reglages.hauteur
        ):
            plan.avertissements.append(
                f"{etiquette} : source {clip.infos.largeur}x{clip.infos.hauteur}, "
                f"plus petite que {reglages.largeur}x{reglages.hauteur}. L'agrandissement "
                "ne crée aucun détail supplémentaire."
            )
        if clip.infos.fps and reglages.fps > clip.infos.fps + 1:
            plan.avertissements.append(
                f"{etiquette} : source à {clip.infos.fps:.0f} i/s portée à "
                f"{reglages.fps} i/s. Des images sont dupliquées, la fluidité réelle "
                "n'augmente pas."
            )

    plan.duree_brute = sum(c.duree_utile for c in plan.clips)
    plan.duree_finale = plan.duree_brute / vitesse

    if not plan.clips:
        plan.bloquants.append("Aucun clip valide a assembler.")
    if plan.duree_finale and plan.duree_finale < reglages.duree_cible_min:
        plan.avertissements.append(
            f"Épisode final de {plan.duree_finale:.1f} s, en dessous de ta durée "
            f"souhaitée ({reglages.duree_cible_min:.0f} s). Réduis la vitesse ou les "
            "coupes, ou ajoute une scène."
        )
    if reglages.musique_path and not Path(reglages.musique_path).exists():
        plan.bloquants.append("Fichier de musique introuvable.")
    if reglages.incruster_srt and not (
        reglages.srt_path and Path(reglages.srt_path).exists()
    ):
        plan.bloquants.append("Incrustation demandée mais aucun fichier SRT disponible.")
    return plan


def _chaine_atempo(vitesse: float) -> str:
    """atempo est fiable entre 0.5 et 2.0 : on enchaine les facteurs."""
    facteurs: list[float] = []
    restant = vitesse
    while restant > 2.0:
        facteurs.append(2.0)
        restant /= 2.0
    while restant < 0.5:
        facteurs.append(0.5)
        restant /= 0.5
    facteurs.append(restant)
    return ",".join(f"atempo={f:.6f}" for f in facteurs)


@dataclass
class ResultatExport:
    reussi: bool
    chemin: str = ""
    duree: float = 0.0
    message: str = ""
    commande: str = ""
    journal: str = ""


def assembler(
    clips: list[Clip],
    cible: str | Path,
    reglages: ReglagesExport | None = None,
) -> ResultatExport:
    reglages = reglages or ReglagesExport()
    plan = preparer_plan(clips, reglages)
    if not plan.utilisable:
        return ResultatExport(False, message=" ".join(plan.bloquants))

    cible = Path(cible)
    cible.parent.mkdir(parents=True, exist_ok=True)
    W, H, FPS = reglages.largeur, reglages.hauteur, reglages.fps
    vitesse = reglages.vitesse or 1.0

    entrees: list[str] = []
    filtres: list[str] = []
    etiquettes: list[str] = []
    index = 0
    silences: list[tuple[int, float]] = []  # (index d'entree, duree)

    for clip in plan.clips:
        assert clip.infos is not None
        entrees += ["-i", str(clip.chemin)]
        mon_index = index
        index += 1
        fin = clip.trim_debut + clip.duree_utile
        filtres.append(
            f"[{mon_index}:v]trim=start={clip.trim_debut:.3f}:end={fin:.3f},"
            f"setpts=PTS-STARTPTS,"
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
            f"fps={FPS},format=yuv420p[v{mon_index}]"
        )
        if clip.infos.a_audio:
            filtres.append(
                f"[{mon_index}:a]atrim=start={clip.trim_debut:.3f}:end={fin:.3f},"
                f"asetpts=PTS-STARTPTS,aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{mon_index}]"
            )
            etiquettes.append(f"[v{mon_index}][a{mon_index}]")
        else:
            silences.append((mon_index, clip.duree_utile))
            etiquettes.append(f"[v{mon_index}][SIL{mon_index}]")

    for mon_index, duree in silences:
        entrees += [
            "-f",
            "lavfi",
            "-t",
            f"{duree:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
        ]
        filtres.append(
            f"[{index}:a]aformat=sample_fmts=fltp:channel_layouts=stereo[SIL{mon_index}]"
        )
        index += 1

    n = len(plan.clips)
    filtres.append(f"{''.join(etiquettes)}concat=n={n}:v=1:a=1[cv][ca]")

    video_courante = "cv"
    audio_courant = "ca"
    if abs(vitesse - 1.0) > 1e-6:
        filtres.append(f"[{video_courante}]setpts=PTS/{vitesse:.6f}[vs]")
        filtres.append(f"[{audio_courant}]{_chaine_atempo(vitesse)}[as]")
        video_courante, audio_courant = "vs", "as"

    srt_temporaire: Path | None = None
    if reglages.incruster_srt and reglages.srt_path:
        # Nom simple et sans accent : le filtre subtitles est sensible a l'echappement.
        dossier = Path(tempfile.mkdtemp(prefix="videroo_srt_"))
        srt_temporaire = dossier / "st.srt"
        shutil.copyfile(reglages.srt_path, srt_temporaire)
        style = (
            "FontName=DejaVu Sans,Fontsize=22,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,"
            "Alignment=2,MarginV=90"
        )
        filtres.append(
            f"[{video_courante}]subtitles={srt_temporaire}:force_style='{style}'[vsub]"
        )
        video_courante = "vsub"

    if reglages.musique_path:
        entrees += ["-stream_loop", "-1", "-i", str(reglages.musique_path)]
        idx_musique = index
        index += 1
        volume = max(0.0, min(2.0, reglages.musique_volume))
        filtres.append(
            f"[{idx_musique}:a]volume={volume:.3f},"
            f"atrim=start=0:end={plan.duree_finale:.3f},asetpts=PTS-STARTPTS,"
            f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[mus]"
        )
        filtres.append(
            f"[{audio_courant}][mus]amix=inputs=2:duration=first:"
            f"dropout_transition=0:normalize=0[am]"
        )
        audio_courant = "am"

    commande = [
        chemin_outil("ffmpeg"),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        *entrees,
        "-filter_complex",
        ";".join(filtres),
        "-map",
        f"[{video_courante}]",
        "-map",
        f"[{audio_courant}]",
        "-c:v",
        "libx264",
        "-preset",
        reglages.preset,
        "-crf",
        str(reglages.crf),
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FPS),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(cible),
    ]

    try:
        res = subprocess.run(commande, capture_output=True, text=True, timeout=5400)
    except subprocess.TimeoutExpired:
        return ResultatExport(False, message="ffmpeg a dépassé 90 minutes.")
    finally:
        if srt_temporaire:
            shutil.rmtree(srt_temporaire.parent, ignore_errors=True)

    if res.returncode != 0 or not cible.exists():
        return ResultatExport(
            False,
            message="ffmpeg a échoué. Detail dans le journal.",
            commande=" ".join(commande),
            journal=(res.stderr or "")[-3000:],
        )

    infos = analyser_media(cible)
    if not infos.lisible:
        return ResultatExport(
            False,
            chemin=str(cible),
            message=f"Fichier produit mais illisible : {infos.erreur}",
            commande=" ".join(commande),
            journal=(res.stderr or "")[-3000:],
        )
    return ResultatExport(
        True,
        chemin=str(cible),
        duree=infos.duree,
        message=(
            f"Export réussi : {infos.duree:.2f} s, {infos.largeur}x{infos.hauteur}, "
            f"audio {'oui' if infos.a_audio else 'non'}."
        ),
        commande=" ".join(commande),
        journal=(res.stderr or "")[-2000:],
    )


def fabriquer_clip_test(
    cible: str | Path,
    duree: float = 3.0,
    couleur: str = "navy",
    texte: str = "TEST",
    avec_audio: bool = True,
    largeur: int = 720,
    hauteur: int = 1280,
) -> str:
    """Genere un clip synthetique pour les tests locaux (zéro quota IA consomme)."""
    cible = Path(cible)
    cible.parent.mkdir(parents=True, exist_ok=True)
    commande = [
        chemin_outil("ffmpeg"),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={couleur}:s={largeur}x{hauteur}:d={duree}:r=25",
    ]
    if avec_audio:
        commande += [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duree}:sample_rate=48000",
        ]
    commande += [
        "-vf",
        f"drawtext=text='{texte}':fontcolor=white:fontsize=72:x=(w-tw)/2:y=(h-th)/2",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-t",
        str(duree),
    ]
    if avec_audio:
        commande += ["-c:a", "aac", "-b:a", "96k", "-shortest"]
    commande.append(str(cible))
    res = subprocess.run(commande, capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or "")[-800:])
    return str(cible)
