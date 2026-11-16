"""Vérification des medias avec ffprobe et petits utilitaires ffmpeg."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path


class OutilManquant(RuntimeError):
    pass


def chemin_outil(nom: str) -> str:
    chemin = shutil.which(nom)
    if not chemin:
        raise OutilManquant(
            f"{nom} est introuvable. Installe-le avec : sudo apt install ffmpeg"
        )
    return chemin


def outils_disponibles() -> dict[str, str | None]:
    return {"ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe")}


@dataclass
class InfosMedia:
    chemin: str
    existe: bool = False
    lisible: bool = False
    duree: float = 0.0
    largeur: int = 0
    hauteur: int = 0
    fps: float = 0.0
    a_audio: bool = False
    codec_video: str = ""
    codec_audio: str = ""
    taille_octets: int = 0
    erreur: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _fraction(valeur: str | None) -> float:
    if not valeur:
        return 0.0
    try:
        if "/" in valeur:
            haut, bas = valeur.split("/", 1)
            bas_f = float(bas)
            return float(haut) / bas_f if bas_f else 0.0
        return float(valeur)
    except ValueError:
        return 0.0


def analyser_media(chemin: str | Path) -> InfosMedia:
    """Inspecte un fichier. N'echoue jamais : l'erreur est dans le résultat."""
    p = Path(chemin)
    infos = InfosMedia(chemin=str(p))
    if not p.exists():
        infos.erreur = "Fichier absent."
        return infos
    infos.existe = True
    infos.taille_octets = p.stat().st_size
    if infos.taille_octets == 0:
        infos.erreur = "Fichier vide (0 octet)."
        return infos
    try:
        sortie = subprocess.run(
            [
                chemin_outil("ffprobe"),
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(p),
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except OutilManquant as exc:
        infos.erreur = str(exc)
        return infos
    except subprocess.TimeoutExpired:
        infos.erreur = "ffprobe n'a pas repondu (90 s)."
        return infos

    if sortie.returncode != 0:
        infos.erreur = (sortie.stderr or "ffprobe a échoué.").strip()[:400]
        return infos

    try:
        donnees = json.loads(sortie.stdout or "{}")
    except ValueError:
        infos.erreur = "Réponse ffprobe illisible."
        return infos

    flux = donnees.get("streams") or []
    fmt = donnees.get("format") or {}
    infos.duree = float(fmt.get("duration") or 0.0)
    for f in flux:
        if f.get("codec_type") == "video" and not infos.codec_video:
            infos.codec_video = f.get("codec_name") or ""
            infos.largeur = int(f.get("width") or 0)
            infos.hauteur = int(f.get("height") or 0)
            infos.fps = _fraction(f.get("avg_frame_rate") or f.get("r_frame_rate"))
            if not infos.duree:
                infos.duree = float(f.get("duration") or 0.0)
        elif f.get("codec_type") == "audio" and not infos.codec_audio:
            infos.codec_audio = f.get("codec_name") or ""
            infos.a_audio = True

    if not infos.codec_video and not infos.codec_audio:
        infos.erreur = "Aucun flux audio ni vidéo détecté."
        return infos
    infos.lisible = True
    return infos


def est_image(chemin: str | Path) -> bool:
    return Path(chemin).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".avif"}


def analyser_image(chemin: str | Path) -> InfosMedia:
    infos = analyser_media(chemin)
    if infos.lisible and infos.largeur and infos.hauteur:
        infos.duree = 0.0
    return infos


def extraire_images_cles(
    source: str | Path, dossier_sortie: str | Path, nombre: int = 6
) -> list[str]:
    """Extrait N images reparties dans la vidéo (fonction complementaire)."""
    infos = analyser_media(source)
    if not infos.lisible or infos.duree <= 0:
        raise ValueError(infos.erreur or "Vidéo illisible.")
    sortie = Path(dossier_sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    fichiers: list[str] = []
    pas = infos.duree / (nombre + 1)
    for i in range(1, nombre + 1):
        instant = pas * i
        cible = sortie / f"image_cle_{i:02d}.jpg"
        subprocess.run(
            [
                chemin_outil("ffmpeg"),
                "-y",
                "-ss",
                f"{instant:.3f}",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(cible),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if cible.exists() and cible.stat().st_size > 0:
            fichiers.append(str(cible))
    return fichiers


def extraire_audio(source: str | Path, cible: str | Path) -> str:
    """Extrait la piste audio en WAV 16 kHz mono (base d'une transcription locale)."""
    infos = analyser_media(source)
    if not infos.a_audio:
        raise ValueError("Cette vidéo ne contient aucune piste audio.")
    Path(cible).parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(
        [
            chemin_outil("ffmpeg"),
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(cible),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if res.returncode != 0:
        raise RuntimeError((res.stderr or "").strip()[-600:])
    return str(cible)


# --------------------------------------------------------------------------
# À quelle scène appartient une vidéo ?
# --------------------------------------------------------------------------

_VIGNETTE = (18, 32)  # 9:16, assez pour reconnaître un cadrage, pas plus
#: Une autre scène doit ressembler NETTEMENT mieux pour que la vidéo soit
#  refusée : deux plans du même décor restent proches (projet 38, scènes 5 et 6 :
#  écarts 29.6 et 30.8 pour la bonne vidéo). Mesuré le 12 novembre 2026 sur les
#  vidéos décalées : 6.4 contre 12.9, 21.1 contre 42.2, 10.5 contre 48.2.
RAPPORT_REFUS = 0.75


def vignette(chemin: str | Path) -> bytes | None:
    """La première image d'un fichier (image ou vidéo), en 18x32 niveaux de gris."""
    largeur, hauteur = _VIGNETTE
    try:
        res = subprocess.run(
            [
                chemin_outil("ffmpeg"), "-v", "error", "-i", str(chemin),
                "-vf",
                f"scale={largeur}:{hauteur}:force_original_aspect_ratio=increase,"
                f"crop={largeur}:{hauteur},format=gray",
                "-frames:v", "1", "-f", "rawvideo", "-",
            ],
            capture_output=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError, OutilManquant):
        return None
    attendu = largeur * hauteur
    return res.stdout if len(res.stdout) == attendu else None


def ecart_vignettes(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(a))


def scene_plus_proche(
    video: str | Path, image_de_depart: str | Path, autres: dict[int, str | Path]
) -> tuple[int, float, float] | None:
    """(numéro, écart avec elle, écart avec notre image) si une AUTRE scène
    correspond nettement mieux au début de la vidéo ; sinon None.

    Grok anime l'image jointe : une vidéo commence sur son image de départ. Une
    vidéo qui démarre sur l'image d'une autre scène est la vidéo de cette
    scène-là — typiquement une seconde prise arrivée en retard (le 12 novembre
    2026, elle a décalé d'une scène toutes les vidéos du projet 38). Dans le
    doute (outil absent, fichier illisible, écarts proches), on ne refuse pas.
    """
    debut = vignette(video)
    notre = vignette(image_de_depart)
    if debut is None or notre is None:
        return None
    ecart_notre = ecart_vignettes(debut, notre)
    meilleur: tuple[int, float] | None = None
    for numero, chemin in autres.items():
        image = vignette(chemin)
        if image is None:
            continue
        ecart = ecart_vignettes(debut, image)
        if meilleur is None or ecart < meilleur[1]:
            meilleur = (numero, ecart)
    if meilleur and meilleur[1] < ecart_notre * RAPPORT_REFUS:
        return meilleur[0], meilleur[1], ecart_notre
    return None


def ecart_debut(video: str | Path, image: str | Path) -> float | None:
    """Écart entre le début d'une vidéo et une image (plus petit = plus proche)."""
    a, b = vignette(video), vignette(image)
    return None if a is None or b is None else ecart_vignettes(a, b)
