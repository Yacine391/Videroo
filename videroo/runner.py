"""Moteur de génération : garde-fous quotas, file d'attente, reprise.

Règles non négociables appliquees ici :
  1. UNE seule génération automatique par élément valide ;
  2. UNE seule génération active à la fois ;
  3. aucun clic automatique sur « Régénérer » ;
  4. un delai dépassé ne relance jamais rien (statut « à vérifier ») ;
  5. l'état est enregistré avant ET après chaque lancement ;
  6. après un plantage, on vérifie qu'un résultat n'existe pas déjà ;
  7. toute régénération vient d'une action explicite de l'utilisateur.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import comptes, config, db, fichiers, media, reglages
from .adresses import est_une_conversation
from .adapters import base
from .adapters import navigateur as nav


class GenerationRefusee(RuntimeError):
    """Refus volontaire : le message est affichable tel quel dans l'interface."""


CIBLES = ("planche", "image", "video", "miniature")

_adaptateurs: dict[tuple[str, str], base.Adaptateur] = {}


def enregistrer_adaptateur(fournisseur: str, mode: str, adaptateur: base.Adaptateur) -> None:
    _adaptateurs[(fournisseur, mode)] = adaptateur


def adaptateur(fournisseur: str, mode: str) -> base.Adaptateur | None:
    return _adaptateurs.get((fournisseur, mode))


def empreinte(texte: str) -> str:
    return hashlib.sha256((texte or "").encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Lecture d'etat
# --------------------------------------------------------------------------

#: Les deux fournisseurs travaillent en parallèle : pendant que Grok rend une
#  vidéo, ChatGPT prépare l'image de la scène suivante. Chacun garde sa règle
#  « une seule génération automatique par élément » et son propre quota.
FOURNISSEURS = ("chatgpt", "grok")


def tache_active(fournisseur: str | None = None) -> sqlite3.Row | None:
    """La génération en cours — celle d'un fournisseur, ou la première venue."""
    if fournisseur:
        return db.q1(
            "SELECT * FROM taches WHERE statut='en_cours' AND fournisseur=?"
            " ORDER BY id LIMIT 1",
            (fournisseur,),
        )
    return db.q1("SELECT * FROM taches WHERE statut = 'en_cours' ORDER BY id LIMIT 1")


def taches_actives() -> list[sqlite3.Row]:
    """Toutes les générations en cours : au plus une par fournisseur."""
    return list(db.q("SELECT * FROM taches WHERE statut='en_cours' ORDER BY id"))


def taches_cible(projet_id: int, cible_type: str, cible_id: int) -> list[sqlite3.Row]:
    return db.q(
        "SELECT * FROM taches WHERE projet_id=? AND cible_type=? AND cible_id=?"
        " ORDER BY id",
        (projet_id, cible_type, cible_id),
    )


def resume_projet(projet_id: int) -> dict:
    lignes = db.q(
        "SELECT cible_type, statut, COUNT(*) n FROM taches WHERE projet_id=?"
        " GROUP BY cible_type, statut",
        (projet_id,),
    )
    resume: dict[str, dict[str, int]] = {}
    for l in lignes:
        resume.setdefault(l["cible_type"], {})[l["statut"]] = l["n"]
    return resume


# --------------------------------------------------------------------------
# Resolution de la cible
# --------------------------------------------------------------------------

def _projet(projet_id: int) -> sqlite3.Row:
    ligne = db.q1("SELECT * FROM projets WHERE id=?", (projet_id,))
    if not ligne:
        raise GenerationRefusee(f"Projet {projet_id} inconnu.")
    return ligne


def chemin_attendu(projet_id: int, cible_type: str, cible_id: int) -> Path:
    projet = _projet(projet_id)
    dossier = Path(projet["dossier"])
    if cible_type == "planche":
        p = db.q1("SELECT * FROM personnages WHERE id=? AND projet_id=?", (cible_id, projet_id))
        if not p:
            raise GenerationRefusee("Personnage introuvable dans ce projet.")
        return fichiers.chemin_planche(dossier, p["code"], p["surnom"], p["etat"])
    if cible_type in ("image", "video"):
        s = db.q1("SELECT * FROM scenes WHERE id=? AND projet_id=?", (cible_id, projet_id))
        if not s:
            raise GenerationRefusee("Scène introuvable dans ce projet.")
        return (
            fichiers.chemin_image_scene(dossier, s["numero"])
            if cible_type == "image"
            else fichiers.chemin_video_scene(dossier, s["numero"])
        )
    if cible_type == "miniature":
        return fichiers.chemin_miniature(dossier)
    raise GenerationRefusee(f"Type de cible inconnu : {cible_type}")


def _codes_video(scene) -> list[str]:
    """Les planches citees par le prompt video, dans l'ordre des @imageN."""
    try:
        return db.charger_json(scene["refs_video"], []) or []
    except (IndexError, KeyError):
        return []


def pieces_jointes_video(projet_id: int, scene_id: int) -> list[Path]:
    """Ce qui part vraiment avec un prompt video, dans l'ordre des @imageN.

    @image1 est l'image de depart, @image2 et les suivantes sont les planches
    des personnages, dans l'ordre exact ou le prompt video les cite. Grok
    numerote les pieces jointes dans l'ordre ou on les depose : un autre ordre
    ferait designer a « @image3 is ILYES » le visage de quelqu'un d'autre.
    """
    scene = db.q1("SELECT * FROM scenes WHERE id=?", (scene_id,))
    joints = [Path(scene["image_path"])]
    for code in _codes_video(scene):
        perso = db.q1(
            "SELECT ref_path FROM personnages WHERE projet_id=? AND code=?",
            (projet_id, code),
        )
        if perso and fichiers.fichier_utilisable(perso["ref_path"]):
            joints.append(Path(perso["ref_path"]))
    return joints


def planches_dans_le_chat(projet_id: int) -> bool:
    """Tu as dit que les planches sont déjà dans la conversation ChatGPT du projet.

    Ça ne vaut que si cette conversation est connue : un chat neuf, lui, ne
    contient aucune planche, et l'image sortirait avec d'autres visages.
    """
    projet = _projet(projet_id)
    return bool(projet["refs_dans_chat"]) and est_une_conversation(projet["chatgpt_chat_url"] or "")


def _refs_manquantes(
    projet_id: int, scene: sqlite3.Row, codes: list[str] | None = None,
    dans_le_chat: bool = False,
) -> list[str]:
    """Vérifie que chaque planche requise est un VRAI fichier du bon projet.

    `dans_le_chat` : les planches d'avant sont déjà dans la conversation ChatGPT.
    Seules celles que ce pack fait générer (elles ont un prompt) doivent alors
    exister en fichier : celles-là, le chat ne les a jamais vues.
    """
    if codes is None:
        codes = db.charger_json(scene["refs_codes"], [])
    manquantes: list[str] = []
    projet = _projet(projet_id)
    for code in codes:
        p = db.q1(
            "SELECT * FROM personnages WHERE projet_id=? AND code=?", (projet_id, code)
        )
        if not p:
            manquantes.append(f"{code} (personnage absent du projet)")
            continue
        if dans_le_chat and not (p["prompt_planche"] or "").strip():
            continue
        if not fichiers.fichier_utilisable(p["ref_path"]):
            manquantes.append(f"{code} {p['surnom']} (aucune image de référence)")
            continue
        try:
            fichiers.verifier_appartenance(p["ref_path"], projet_id, projet["dossier"])
        except fichiers.MelangeDeProjets as exc:
            manquantes.append(f"{code} {p['surnom']} : {exc}")
    return manquantes


def controles_avant_lancement(projet_id: int, cible_type: str, cible_id: int) -> None:
    """Lève GenerationRefusee si l'élément n'est pas prêt. Zéro quota consommé."""
    projet = _projet(projet_id)
    if projet["archive"]:
        raise GenerationRefusee("Projet archivé : désarchive-le avant de générer.")
    if projet["statut"] == "arrete":
        raise GenerationRefusee(
            "Projet arrêté. Clique sur Reprendre pour autoriser de nouvelles tâches."
        )

    if cible_type == "planche":
        p = db.q1("SELECT * FROM personnages WHERE id=? AND projet_id=?", (cible_id, projet_id))
        if not p:
            raise GenerationRefusee("Personnage introuvable.")
        if not (p["prompt_planche"] or "").strip():
            raise GenerationRefusee(
                f"{p['code']} {p['surnom']} : le prompt de planche est vide. "
                "Complète-le dans l'onglet Personnages avant de générer."
            )
        return

    if cible_type == "miniature":
        if not prompt_miniature(projet_id).strip():
            raise GenerationRefusee(
                "Aucun prompt de miniature : le modèle est introuvable et rien n'est "
                "écrit dans l'onglet Export."
            )
        return

    scene = db.q1("SELECT * FROM scenes WHERE id=? AND projet_id=?", (cible_id, projet_id))
    if not scene:
        raise GenerationRefusee("Scène introuvable.")
    if not scene["valide"]:
        raise GenerationRefusee(
            f"Scène {scene['numero']} non validée. Valide-la dans l'onglet Scènes "
            "(c'est la validation qui autorise une génération)."
        )

    if cible_type == "image":
        if not (scene["prompt_image"] or "").strip():
            raise GenerationRefusee(f"Scène {scene['numero']} : prompt image vide.")
        manquantes = _refs_manquantes(
            projet_id, scene, dans_le_chat=planches_dans_le_chat(projet_id)
        )
        if manquantes:
            raise GenerationRefusee(
                f"Scène {scene['numero']} : références réellement absentes — "
                + " ; ".join(manquantes)
                + ". Génère ou importe ces planches d'abord : un code comme R1 ne "
                "remplace pas une image jointe."
            )
        return

    # cible_type == "video"
    if not (scene["prompt_video"] or "").strip():
        raise GenerationRefusee(f"Scène {scene['numero']} : prompt vidéo vide.")
    if not fichiers.fichier_utilisable(scene["image_path"]):
        raise GenerationRefusee(
            f"Scène {scene['numero']} : aucune image de départ validée. "
            "Génère ou importe l'image avant d'animer."
        )
    fichiers.verifier_appartenance(scene["image_path"], projet_id, projet["dossier"])
    # Un prompt qui dit « @image3 is ILYES » exige que la planche d'Ilyes parte
    # vraiment avec l'envoi. Ce refus a lieu avant le moindre clic : zero quota.
    manquantes = _refs_manquantes(projet_id, scene, _codes_video(scene))
    if manquantes:
        raise GenerationRefusee(
            f"Scène {scene['numero']} : le prompt vidéo cite des planches qui ne "
            "sont pas sur le disque — " + " ; ".join(manquantes)
            + ". Produis-les d'abord : un repère comme @image3 ne remplace pas "
            "une image réellement jointe."
        )
    durees = reglages.durees_grok()
    if scene["duree"] not in durees:
        raise GenerationRefusee(
            f"Scène {scene['numero']} : durée {scene['duree']} s non prise en charge "
            f"par Grok (valeurs acceptées : {', '.join(str(d) for d in durees)} s). "
            "Corrige la durée avant de lancer la génération."
        )


# --------------------------------------------------------------------------
# Demande de generation
# --------------------------------------------------------------------------

def _prompt_de(projet_id: int, cible_type: str, cible_id: int) -> str:
    if cible_type == "planche":
        p = db.q1("SELECT prompt_planche FROM personnages WHERE id=?", (cible_id,))
        return (p["prompt_planche"] if p else "") or ""
    if cible_type == "miniature":
        return prompt_miniature(projet_id)
    colonne = "prompt_image" if cible_type == "image" else "prompt_video"
    s = db.q1(f"SELECT {colonne} AS p FROM scenes WHERE id=?", (cible_id,))
    return (s["p"] if s else "") or ""



def marqueurs_chatgpt(projet_id: int, cible_type: str, cible_id: int) -> tuple[str, list[str]]:
    """Une ligne du prompt de cet élément que NUL autre prompt du projet ne contient.

    Elle sert à retrouver NOTRE message dans la conversation ChatGPT, que
    toutes les scènes (et les parties précédentes) partagent. Les prompts d'un
    même épisode commencent par le même bloc commun : la première ligne ne
    distingue rien, il faut une ligne propre à la scène. On rend aussi les
    marqueurs des autres éléments : le premier qui suit le nôtre borne sa réponse.
    """
    def lignes(texte: str) -> list[str]:
        return [" ".join(l.split())[:160] for l in (texte or "").splitlines()
                if len(" ".join(l.split())) >= 20]

    prompts: dict[tuple[str, int], str] = {}
    for p in db.q("SELECT id FROM personnages WHERE projet_id=?", (projet_id,)):
        prompts[("planche", p["id"])] = _prompt_de(projet_id, "planche", p["id"])
    for sc in db.q("SELECT id FROM scenes WHERE projet_id=?", (projet_id,)):
        prompts[("image", sc["id"])] = _prompt_de(projet_id, "image", sc["id"])
    prompts[("miniature", 0)] = _prompt_de(projet_id, "miniature", 0)

    plats = {cle: " ".join(texte.split()) for cle, texte in prompts.items()}

    def propre_a(cle: tuple[str, int]) -> str:
        for ligne in lignes(prompts.get(cle, "")):
            if not any(ligne in autre for c, autre in plats.items() if c != cle):
                return ligne
        return ""

    moi = (cible_type, cible_id if cible_type != "miniature" else 0)
    mien = propre_a(moi)
    autres = [m for m in (propre_a(c) for c in prompts if c != moi) if m]
    return mien, autres


def prompt_miniature(projet_id: int) -> str:
    """Le prompt de miniature : celui du projet s'il existe, sinon le modèle.

    Le modèle est le texte de Yacine, conservé mot pour mot ; seuls le titre de
    l'histoire et le numéro de partie y sont remplacés.
    """
    from . import ressources

    projet = _projet(projet_id)
    ecrit = (projet["instructions_miniature"] or "").strip()
    if ecrit:
        return ecrit
    # La chronique donne elle-meme le texte a ecrire sur la miniature
    # (« TITRE MINIATURE »), qui n'est pas le titre de la serie. Quand le pack
    # ne le precise pas, on garde le titre de l'histoire, comme avant.
    titre = _colonne(projet, "titre_miniature") or projet["titre"] or projet["nom"]
    return ressources.prompt_miniature(titre, projet["grok_episode"] or 1)


def _colonne(ligne, nom: str) -> str:
    """Lit une colonne qui peut manquer sur une base pas encore migree."""
    try:
        return (ligne[nom] or "").strip()
    except (IndexError, KeyError):
        return ""


def _libelle_de(projet_id: int, cible_type: str, cible_id: int) -> str:
    if cible_type == "planche":
        p = db.q1("SELECT code, surnom, etat FROM personnages WHERE id=?", (cible_id,))
        if p:
            etat = "" if p["etat"] in ("", "UNIQUE") else f" ({p['etat']})"
            return f"Planche {p['code']} {p['surnom']}{etat}"
    if cible_type == "miniature":
        return "Miniature"
    s = db.q1("SELECT numero, titre FROM scenes WHERE id=?", (cible_id,))
    if s:
        quoi = "Image" if cible_type == "image" else "Video"
        return f"{quoi} scene {s['numero']} — {s['titre'] or ''}".strip()
    return f"{cible_type} {cible_id}"


def demander(
    projet_id: int,
    cible_type: str,
    cible_id: int,
    *,
    fournisseur: str,
    mode: str = "assiste",
    origine: str = "auto",
) -> int:
    """Met une génération en file. Lève GenerationRefusee avec un motif clair."""
    if cible_type not in CIBLES:
        raise GenerationRefusee(f"Type de cible inconnu : {cible_type}")
    if origine not in ("auto", "manuel"):
        raise GenerationRefusee("Origine invalide.")

    controles_avant_lancement(projet_id, cible_type, cible_id)
    libelle = _libelle_de(projet_id, cible_type, cible_id)

    # 6. Un resultat existe-t-il deja sur le disque ?
    cible = chemin_attendu(projet_id, cible_type, cible_id)
    if fichiers.fichier_utilisable(cible):
        _enregistrer_resultat_existant(projet_id, cible_type, cible_id, cible)
        raise GenerationRefusee(
            f"{libelle} : c'est déjà fait. Le fichier est sur ton disque "
            f"({cible.name}) et rattaché à cet élément — regarde sa vignette. "
            "Rien n'a été relancé, aucun quota dépensé. Pour le remplacer, "
            "utilise « Régénérer (explicite) »."
        )

    anciennes = taches_cible(projet_id, cible_type, cible_id)
    for t in anciennes:
        if t["statut"] in ("en_attente", "en_cours"):
            raise GenerationRefusee(
                f"{libelle} : une tâche est déjà {t['statut'].replace('_', ' ')} "
                f"(#{t['id']}). Aucune seconde génération lancée."
            )
    if origine == "auto":
        for t in anciennes:
            if t["origine"] != "auto":
                continue
            if t["statut"] not in config.STATUTS_TACHE_VIVANTS:
                continue
            # Un echec qui s'est arrete AVANT le moindre envoi n'a rien coute :
            # il ne consomme pas l'unique generation automatique de l'element.
            if t["statut"] == "echec" and not _a_envoye(t):
                continue
            raise GenerationRefusee(
                f"{libelle} : la génération automatique a déjà eu lieu "
                f"(tâche #{t['id']}, statut « {t['statut'].replace('_', ' ')} »). "
                "Une relance doit venir d'un clic explicite de ta part."
            )

    prompt = _prompt_de(projet_id, cible_type, cible_id)
    try:
        tache_id = db.exec1(
            "INSERT INTO taches (projet_id, cible_type, cible_id, libelle, fournisseur,"
            " mode, origine, statut, empreinte, cree_le) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                projet_id,
                cible_type,
                cible_id,
                libelle,
                fournisseur,
                mode,
                origine,
                "en_attente",
                empreinte(prompt),
                db.maintenant(),
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise GenerationRefusee(
            f"{libelle} : refus du garde-fou base de données ({exc}). "
            "Une génération existe déjà pour cet élément."
        ) from exc

    db.journaliser(
        f"ÉTAT AVANT — {libelle} mis en file (mode {mode}, origine {origine}).",
        projet_id,
        "info",
        tache_id,
    )
    return tache_id


def empreinte_fichier(chemin: str | Path | None) -> str:
    """Empreinte du CONTENU d'un fichier, pour reconnaître deux copies identiques."""
    if not chemin:
        return ""
    try:
        return hashlib.sha256(Path(chemin).read_bytes()).hexdigest()
    except OSError:
        return ""


def medias_deja_rattaches(
    projet_id: int, sauf: tuple[str, int] | None = None
) -> dict[str, str]:
    """Empreinte du contenu -> libellé de chaque média déjà rattaché au projet.

    `sauf` est l'élément qu'on est en train de récupérer : son propre fichier ne
    doit pas se compter comme un doublon de lui-même.
    """
    connus: dict[str, str] = {}

    def retenir(genre: str, cible_id: int, chemin, libelle: str) -> None:
        if sauf is not None and sauf == (genre, cible_id):
            return
        trace = empreinte_fichier(chemin)
        if trace:
            connus.setdefault(trace, libelle)

    for p in db.q(
        "SELECT id, code, surnom, ref_path FROM personnages WHERE projet_id=?",
        (projet_id,),
    ):
        retenir("planche", p["id"], p["ref_path"],
                f"la planche {p['code']} {p['surnom']}")
    for s in db.q(
        "SELECT id, numero, image_path, video_path FROM scenes WHERE projet_id=?",
        (projet_id,),
    ):
        retenir("image", s["id"], s["image_path"], f"l'image de la scène {s['numero']}")
        retenir("video", s["id"], s["video_path"], f"la vidéo de la scène {s['numero']}")
    pr = db.q1("SELECT miniature_path, chatgpt_chat_url FROM projets WHERE id=?",
               (projet_id,))
    if pr is not None:
        retenir("miniature", 0, pr["miniature_path"], "la miniature")

    # Les parties d'une série partagent la MÊME conversation ChatGPT : ses
    # images d'avant (scènes, planches, miniature) y sont toujours. Le 16
    # novembre 2026, « Récupérer » a rattaché à la scène 1 de la partie 4 la
    # miniature de la partie 3 — le contrôle ne regardait que la partie 4.
    if pr is not None and pr["chatgpt_chat_url"]:
        for autre in db.q(
            "SELECT id, nom, miniature_path FROM projets WHERE chatgpt_chat_url=?"
            " AND id<>?",
            (pr["chatgpt_chat_url"], projet_id),
        ):
            de = f"du projet #{autre['id']} « {autre['nom']} »"
            for p in db.q("SELECT code, surnom, ref_path FROM personnages WHERE projet_id=?",
                          (autre["id"],)):
                retenir("", -1, p["ref_path"], f"la planche {p['code']} {p['surnom']} {de}")
            for s in db.q("SELECT numero, image_path FROM scenes WHERE projet_id=?",
                          (autre["id"],)):
                retenir("", -1, s["image_path"], f"l'image de la scène {s['numero']} {de}")
            retenir("", -1, autre["miniature_path"], f"la miniature {de}")
    return connus


def _ecarter_doublon(chemin: Path) -> str:
    """Efface le fichier recupere a tort et dit ce qu'il en est advenu.

    On vient de comparer les empreintes : ce fichier est la copie exacte,
    octet pour octet, d'un media que le projet possede deja. Le garder ne
    protege rien du tout -- l'original est intact -- et chaque tentative
    empilait une copie de plus (une video Grok pese ~5 Mo). On l'efface donc,
    et le journal dit de quel media c'etait la copie.
    """
    try:
        chemin.unlink()
    except OSError as exc:
        return f"il n'a pas pu être effacé ({exc}) et reste sous {chemin.name}"
    return "il a été effacé : son contenu est déjà sur le disque, intact"


def _enregistrer_resultat_existant(
    projet_id: int, cible_type: str, cible_id: int, cible: Path
) -> None:
    """Rattache un fichier déjà présent (reprise après plantage, import manuel)."""
    if cible_type == "planche":
        db.exec1(
            "UPDATE personnages SET ref_path=?, ref_origine=COALESCE(ref_origine,'retrouve')"
            " WHERE id=?",
            (str(cible), cible_id),
        )
    elif cible_type == "image":
        db.exec1(
            "UPDATE scenes SET image_path=?, image_origine=COALESCE(image_origine,'retrouve')"
            " WHERE id=?",
            (str(cible), cible_id),
        )
    elif cible_type == "video":
        infos = media.analyser_media(cible)
        voulue = db.q1("SELECT duree FROM scenes WHERE id=?", (cible_id,))
        duree_voulue = voulue["duree"] if voulue else None
        ecart = (infos.duree - duree_voulue) if duree_voulue else None
        tolerance = reglages.lire_float("tolerance_duree")
        conforme = 1 if ecart is None or abs(ecart) <= tolerance else 0
        db.exec1(
            "UPDATE scenes SET video_path=?, video_duree=?, video_audio=?,"
            " video_largeur=?, video_hauteur=?, video_ecart=?, video_conforme=?,"
            " video_origine=COALESCE(video_origine,'retrouve') WHERE id=?",
            (
                str(cible),
                infos.duree,
                1 if infos.a_audio else 0,
                infos.largeur,
                infos.hauteur,
                ecart,
                conforme,
                cible_id,
            ),
        )
        if not conforme:
            db.journaliser(
                f"Durée non conforme : {infos.duree:.2f} s obtenues au lieu de "
                f"{duree_voulue} s demandées (écart {ecart:+.2f} s, toléré "
                f"± {tolerance:g} s). La vidéo est conservée ; tu peux l'ajuster ou la "
                "régénérer explicitement.",
                projet_id,
                "alerte",
            )
    elif cible_type == "miniature":
        db.exec1("UPDATE projets SET miniature_path=? WHERE id=?", (str(cible), projet_id))
    db.journaliser(
        f"Résultat déjà présent sur le disque, rattaché sans aucune génération : {cible.name}",
        projet_id,
    )


def recuperer_resultat(
    projet_id: int, cible_type: str, cible_id: int, rang: int = 1
) -> dict:
    """Va chercher chez le fournisseur un resultat DEJA produit. Rien n'est envoye.

    « Apres un plantage, verifier si un resultat existe deja avant de relancer
    quoi que ce soit. » Sans ce chemin, une recolte ratee ne se reparait qu'en
    relancant la generation : le fournisseur refait — et refacture — une image
    qu'il a deja faite. Ici on ouvre la conversation, on lit, on telecharge.
    Aucune saisie, aucun envoi, aucun clic sur Regenerer.
    """
    if cible_type not in ("image", "video", "planche", "miniature"):
        raise GenerationRefusee(f"Type inconnu : {cible_type}.")

    fournisseur = "grok" if cible_type == "video" else "chatgpt"
    precedente = db.q1(
        "SELECT * FROM taches WHERE projet_id=? AND cible_type=? AND cible_id=?"
        " ORDER BY id DESC LIMIT 1",
        (projet_id, cible_type, cible_id),
    )
    if precedente is not None:
        fournisseur = precedente["fournisseur"] or fournisseur

    # Chaque fournisseur a SON navigateur : une image ChatGPT en cours
    # n'empêche pas d'aller lire une vidéo chez Grok. Seule une génération du
    # MÊME fournisseur se disputerait la page.
    occupe = tache_active(fournisseur)
    if occupe is not None:
        raise GenerationRefusee(
            f"Une génération {fournisseur} est en cours ({occupe['libelle']}) : "
            "attends qu'elle finisse avant d'aller chercher un résultat chez "
            f"{fournisseur}, sinon les deux se disputeraient sa page."
        )

    cible = chemin_attendu(projet_id, cible_type, cible_id)

    outil = adaptateur(fournisseur, "auto")
    if outil is None:
        raise GenerationRefusee(f"Aucun adaptateur automatique pour « {fournisseur} ».")

    fausse_tache = {
        "projet_id": projet_id,
        "id": precedente["id"] if precedente is not None else None,
        "cible_type": cible_type,
        "cible_id": cible_id,
        "libelle": _libelle_de(projet_id, cible_type, cible_id),
        "fournisseur": fournisseur,
    }
    ctx = construire_contexte(fausse_tache, cible)
    # Le rang compte a rebours : 1 = la derniere produite, 2 = celle d'avant.
    # Une conversation sert plusieurs scenes, et la derniere n'est pas toujours
    # celle qu'on cherche : sans ce reglage, une recolte ratee deux scenes plus
    # tot restait irrecuperable autrement qu'en regenerant.
    ctx.extras = {**ctx.extras, "rang": max(1, int(rang))}
    db.journaliser(
        f"RÉCUPÉRATION (sans génération) — {ctx.libelle} chez {fournisseur}.",
        projet_id,
    )
    resultat = nav.session(fournisseur).executer(outil.recuperer, ctx)

    if resultat.etat == base.QUOTA:
        # Le fournisseur avait REFUSÉ ce prompt : la preuve est dans sa réponse.
        # Il n'y a donc rien à récupérer, et l'élément n'est plus « à vérifier » :
        # il repartira (une première fois, pas une relance) quand un compte
        # aura du quota. L'heure du compte n'est pas touchée ici : on ne sait
        # pas de quand date ce refus.
        if precedente is not None and precedente["statut"] in ("a_verifier", "echec"):
            db.exec1(
                "UPDATE taches SET statut='quota', message=?, fini_le=? WHERE id=?",
                (resultat.message, db.maintenant(), precedente["id"]),
            )
        db.journaliser(f"Récupération : {resultat.message}", projet_id, "alerte")
        return {"ok": False, "message": resultat.message, "etat": base.QUOTA}
    if resultat.etat != base.TERMINE:
        db.journaliser(
            f"Récupération sans résultat : {resultat.message}", projet_id, "alerte"
        )
        return {"ok": False, "message": resultat.message, "etat": resultat.etat}

    chemin = Path(resultat.chemin or cible)
    if not fichiers.fichier_utilisable(chemin):
        message = f"Fichier annoncé mais inutilisable ({chemin}). Rien n'a été rattaché."
        db.journaliser(message, projet_id, "alerte")
        return {"ok": False, "message": message, "etat": base.A_VERIFIER}
    fichiers.verifier_appartenance(chemin, projet_id, db.dossier_projet(projet_id))

    # La conversation ChatGPT contient AUSSI les planches. Quand l'image de la
    # scene n'a jamais ete produite, la derniere image de la galerie est la
    # derniere planche : sans ce controle, on rattachait la planche a la scene,
    # et Grok animait la planche. On compare le contenu, pas le nom de fichier :
    # une meme image telechargee deux fois a deux noms differents.
    deja = medias_deja_rattaches(projet_id, sauf=(cible_type, cible_id))
    double = deja.get(empreinte_fichier(chemin))
    if double:
        sort = _ecarter_doublon(chemin)
        message = (
            f"Ce qui a été récupéré est exactement {double} — le même fichier, "
            f"au pixel près. Rien n'a été rattaché à {ctx.libelle}, et aucune "
            "génération n'a été lancée. Essaie « Pas la bonne — celle d'avant » "
            "pour remonter d'un cran dans la conversation, ou regénère "
            "explicitement si l'image n'a jamais été produite."
        )
        if precedente is not None:
            db.exec1(
                "UPDATE taches SET recup_doublons = COALESCE(recup_doublons, 0) + 1,"
                " recup_doublon_de=? WHERE id=?",
                (double, precedente["id"]),
            )
        db.journaliser(
            f"Récupération refusée : le fichier obtenu est {double}. "
            f"Rien n'a été rattaché, et {sort}.",
            projet_id,
            "alerte",
        )
        return {"ok": False, "message": message, "etat": base.A_VERIFIER}

    _enregistrer_resultat_existant(projet_id, cible_type, cible_id, chemin)
    if precedente is not None:
        db.exec1(
            "UPDATE taches SET statut=?, resultat_path=?, message=?, fini_le=?"
            " WHERE id=?",
            (base.TERMINE, str(chemin), resultat.message, db.maintenant(),
             precedente["id"]),
        )
    db.journaliser(f"Récupéré sans générer : {resultat.message}", projet_id)
    return {"ok": True, "message": resultat.message, "etat": base.TERMINE}


def reessayer(tache_id: int) -> int:
    """Relance EXPLICITE d'un élément (bouton Réessayer / Régénérer)."""
    t = db.q1("SELECT * FROM taches WHERE id=?", (tache_id,))
    if not t:
        raise GenerationRefusee("Tâche inconnue.")
    if t["statut"] in ("en_attente", "en_cours"):
        raise GenerationRefusee("Cette tâche est encore en cours : rien n'a été relancé.")
    return demander(
        t["projet_id"],
        t["cible_type"],
        t["cible_id"],
        fournisseur=t["fournisseur"],
        mode=t["mode"],
        origine="manuel",
    )


def regenerer(
    projet_id: int, cible_type: str, cible_id: int, fournisseur: str, mode: str = "assiste"
) -> int:
    """Régénération explicite : archivé le fichier existant puis remet en file."""
    cible = chemin_attendu(projet_id, cible_type, cible_id)
    if fichiers.fichier_utilisable(cible):
        archive = cible.with_name(
            f"{cible.stem}_remplace_{time.strftime('%Y%m%d-%H%M%S')}{cible.suffix}"
        )
        cible.replace(archive)
        db.journaliser(
            f"Régénération explicite : ancien fichier conservé sous {archive.name}.",
            projet_id,
        )
    for t in taches_cible(projet_id, cible_type, cible_id):
        if t["statut"] in ("en_attente", "en_cours"):
            raise GenerationRefusee("Une tâche est déjà active sur cet élément.")
    return demander(
        projet_id, cible_type, cible_id, fournisseur=fournisseur, mode=mode, origine="manuel"
    )


def annuler_tache(tache_id: int) -> None:
    t = db.q1("SELECT * FROM taches WHERE id=?", (tache_id,))
    if not t:
        raise GenerationRefusee("Tâche inconnue.")
    if t["statut"] == "en_cours":
        raise GenerationRefusee(
            "Tâche en cours : mets le projet en pause, puis attends la fin de "
            "l'élément lancé (on ne coupe pas une génération déjà payee)."
        )
    db.exec1(
        "UPDATE taches SET statut='annule', fini_le=?, message=? WHERE id=?",
        (db.maintenant(), "Annulée par l'utilisateur.", tache_id),
    )
    db.journaliser(f"Tâche #{tache_id} annulée.", t["projet_id"], "info", tache_id)


# --------------------------------------------------------------------------
# Pause / reprise / arret
# --------------------------------------------------------------------------

def changer_statut_projet(projet_id: int, statut: str) -> None:
    if statut not in config.STATUTS_PROJET:
        raise GenerationRefusee(f"Statut inconnu : {statut}")
    db.exec1(
        "UPDATE projets SET statut=?, maj_le=? WHERE id=?",
        (statut, db.maintenant(), projet_id),
    )
    db.journaliser(f"Projet passé en « {statut} ».", projet_id)


def arreter_projet(projet_id: int) -> int:
    """Empêche les prochaines tâches sans effacer les résultats obtenus."""
    lignes = db.q(
        "SELECT id FROM taches WHERE projet_id=? AND statut='en_attente'", (projet_id,)
    )
    for l in lignes:
        db.exec1(
            "UPDATE taches SET statut='annule', fini_le=?, message=? WHERE id=?",
            (db.maintenant(), "Projet arrêté : tâche non lancée.", l["id"]),
        )
    db.exec1("UPDATE projets SET pipeline=0 WHERE id=?", (projet_id,))
    changer_statut_projet(projet_id, "arrete")
    db.journaliser(
        f"Arrêt du projet : {len(lignes)} tâche(s) en attente annulée(s). "
        "Aucun résultat déjà obtenu n'a été supprimé.",
        projet_id,
        "alerte",
    )
    return len(lignes)


# --------------------------------------------------------------------------
# Chaîne de production : planches → images → vidéos → miniature
# --------------------------------------------------------------------------

#: Au-dela, la chaine n'insiste plus toute seule, meme quand ses tentatives
#  n'ont rien envoye : un selecteur casse echouerait a l'infini. Elle s'arrete
#  sur l'element et te dit quoi reparer.
ESSAIS_AUTO_MAX = 3


def _recuperations_a_vide(tache) -> int:
    """Combien de fois une recuperation n'a ramene qu'un media deja possede."""
    try:
        return int(tache["recup_doublons"] or 0)
    except (IndexError, KeyError):
        return 0


def _a_envoye(tache) -> bool:
    """Le prompt est-il vraiment parti ? Sur une base pas encore migree, oui."""
    try:
        return bool(tache["envoye"])
    except (IndexError, KeyError):
        return True


def _media_de(projet_id: int, cible_type: str, cible_id: int) -> str:
    """Le fichier deja rattache a cet element, ou "" s'il n'y en a pas."""
    if cible_type == "planche":
        l = db.q1("SELECT ref_path FROM personnages WHERE id=? AND projet_id=?",
                  (cible_id, projet_id))
        return (l["ref_path"] if l else "") or ""
    if cible_type == "miniature":
        l = db.q1("SELECT miniature_path FROM projets WHERE id=?", (projet_id,))
        return (l["miniature_path"] if l else "") or ""
    colonne = "image_path" if cible_type == "image" else "video_path"
    l = db.q1(f"SELECT {colonne} AS c FROM scenes WHERE id=? AND projet_id=?",
              (cible_id, projet_id))
    return (l["c"] if l else "") or ""


#: Les quatre etats possibles d'un element, du point de vue de la chaine.
FAIT = "fait"            # le fichier est la, il n'y a plus rien a faire
A_FAIRE = "a_faire"      # la chaine peut le lancer elle-meme, maintenant
ATTENTE = "attente"      # rien a decider : ca arrive, ou c'est deja en cours
BLOQUE = "bloque"        # il faut TA decision : la chaine s'arrete dessus
QUOTA_ATTENDU = "quota"  # refusé faute de quota : repartira quand un compte revient


def etat_cible(projet_id: int, cible_type: str, cible_id: int) -> tuple[str, str]:
    """Ou en est cet element, et pourquoi la chaine ne peut pas le lancer.

    Le motif est redige pour etre affiche tel quel. « bloque » ne veut jamais
    dire « rate » : ca veut dire qu'une relance automatique risquerait de
    depenser un quota deja depense, donc que la decision te revient.
    """
    if fichiers.fichier_utilisable(_media_de(projet_id, cible_type, cible_id)):
        return (FAIT, "")

    taches = taches_cible(projet_id, cible_type, cible_id)
    for t in taches:
        if t["statut"] in ("en_attente", "en_cours"):
            etat = t["statut"].replace("_", " ")
            return (ATTENTE, f"une génération est déjà {etat} (tâche #{t['id']}).")

    rates_a_blanc = 0
    for t in taches:
        if t["statut"] == "a_verifier":
            # Si la recuperation a deja ramene un media qu'on possedait deja,
            # c'est prouve : rien de neuf n'existe chez le fournisseur. Continuer
            # a reclamer « Recuperer le resultat » serait une boucle sans issue.
            vides = _recuperations_a_vide(t)
            if vides:
                fois = "une fois" if vides == 1 else f"{vides} fois"
                return (BLOQUE, (
                    f"la tâche #{t['id']} est « à vérifier », mais la récupération a "
                    f"déjà été tentée {fois} et ramène "
                    f"{t['recup_doublon_de'] or 'un fichier déjà rattaché ailleurs'} "
                    "— un fichier que tu as déjà. Rien de neuf n'existe chez le "
                    "fournisseur : seule une régénération explicite de ta part peut "
                    "produire cet élément, et elle coûtera un crédit."
                ))
            if not _a_envoye(t):
                # Rien n'est parti (connexion demandée, navigateur indisponible…) :
                # parler de « résultat à récupérer » enverrait chercher une image
                # qui ne peut pas exister.
                return (BLOQUE, (
                    f"la tâche #{t['id']} s'est arrêtée AVANT tout envoi — "
                    f"{(t['message'] or '').strip()} Rien n'a été payé : règle ce "
                    "point, puis clique « Réessayer »."
                ))
            return (BLOQUE, (
                f"la tâche #{t['id']} est « à vérifier » : une génération est peut-être "
                "allée au bout chez le fournisseur. Clique « Récupérer le résultat » — "
                "relancer ici te ferait payer deux fois."
            ))
        if t["statut"] == "termine":
            return (BLOQUE, (
                f"la tâche #{t['id']} est terminée mais son fichier n'est plus sur le "
                "disque. Réimporte-le, ou régénère explicitement."
            ))
        if t["statut"] == "echec":
            if _a_envoye(t):
                # Le message de la tâche dit CE qui s'est passé (un refus net
                # du fournisseur n'a rien coûté) : le bandeau ne doit pas le
                # remplacer par une supposition générique.
                detail = (t["message"] or "").strip()
                return (BLOQUE, (
                    f"la tâche #{t['id']} a échoué après l'envoi du prompt"
                    + (f" — {detail}" if detail else ".")
                    + " Le prompt étant parti, du quota a pu être pris (sauf refus net "
                    "du fournisseur, dit ci-dessus) : une relance doit venir de toi."
                ))
            if t["origine"] == "auto":
                rates_a_blanc += 1

    if rates_a_blanc >= ESSAIS_AUTO_MAX:
        return (BLOQUE, (
            f"{rates_a_blanc} tentatives automatiques se sont arrêtées avant le moindre "
            "envoi — aucun quota perdu, mais la chaîne n'insiste plus toute seule. "
            "Répare ce que dit le message d'échec, puis clique « Réessayer »."
        ))

    # Dernière tentative refusée faute de quota : rien n'existe, rien n'a été
    # payé. Tant qu'aucun compte du projet n'est revenu, on attend ; dès qu'il y
    # en a un, l'élément est simplement « à faire ».
    if taches and taches[-1]["statut"] == "quota":
        fournisseur = taches[-1]["fournisseur"]
        pool = _projet(projet_id)["comptes_de"]
        if _refus_quota_d_affilee(taches) >= REFUS_QUOTA_MAX:
            return (BLOQUE, (
                f"{fournisseur} a refusé cet élément {REFUS_QUOTA_MAX} fois de suite "
                "faute de quota : la chaîne n'insiste plus toute seule. Rien n'a été "
                "payé. Clique « Réessayer » quand le quota est vraiment revenu."
            ))
        if fournisseur in comptes.SITES and comptes.compte_actif(fournisseur, pool) is None:
            return (QUOTA_ATTENDU, (
                f"limite atteinte chez {fournisseur}, rien n'a été payé. "
                f"{phrase_retour(fournisseur, pool)}"
            ))

    if cible_type == "image":
        en_route = _planche_en_route(projet_id, cible_id)
        if en_route is not None:
            return en_route

    if cible_type == "video":
        image = _media_de(projet_id, "image", cible_id)
        if not fichiers.fichier_utilisable(image):
            etat_image, motif = etat_cible(projet_id, "image", cible_id)
            if etat_image == BLOQUE:
                return (BLOQUE, f"son image de départ est bloquée : {motif}")
            if etat_image == QUOTA_ATTENDU:
                return (QUOTA_ATTENDU, f"son image de départ attend le quota : {motif}")
            return (ATTENTE, "son image de départ n'est pas encore là.")

    try:
        controles_avant_lancement(projet_id, cible_type, cible_id)
    except (GenerationRefusee, fichiers.MelangeDeProjets) as exc:
        return (BLOQUE, str(exc))
    return (A_FAIRE, "")


def _planche_en_route(projet_id: int, scene_id: int) -> tuple[str, str] | None:
    """L'image d'une scène dont une planche manque ENCORE, mais que la chaîne produit.

    « Références réellement absentes » est une décision à prendre quand le pack
    ne livre pas la planche. Quand la planche a son prompt, elle est simplement
    dans la file (ou elle attend le quota) : ce n'est pas un blocage, et le dire
    comme tel affichait « la chaîne attend ta décision » à chaque démarrage.
    """
    scene = db.q1("SELECT refs_codes FROM scenes WHERE id=? AND projet_id=?",
                  (scene_id, projet_id))
    if not scene:
        return None
    for code in db.charger_json(scene["refs_codes"], []):
        p = db.q1("SELECT * FROM personnages WHERE projet_id=? AND code=?", (projet_id, code))
        if not p or fichiers.fichier_utilisable(p["ref_path"]):
            continue
        if not (p["prompt_planche"] or "").strip():
            continue  # le pack ne la livre pas : c'est aux contrôles de le dire
        etat, motif = etat_cible(projet_id, "planche", p["id"])
        nom = f"{p['code']} {p['surnom']}".strip()
        if etat == BLOQUE:
            return (BLOQUE, f"sa planche {nom} est bloquée : {motif}")
        if etat == QUOTA_ATTENDU:
            return (QUOTA_ATTENDU, f"sa planche {nom} attend le quota : {motif}")
        if etat in (ATTENTE, A_FAIRE):
            return (ATTENTE, f"sa planche {nom} n'est pas encore là.")
    return None


def _elements_dans_l_ordre(projet_id: int) -> list[tuple[str, int, str]]:
    """Tout ce qu'il y a a produire, dans l'ordre de fabrication.

    Les planches d'abord, puis scene par scene son image puis sa video, et la
    miniature en dernier. C'est cet ordre, et lui seul, que la chaine suit.
    """
    elements: list[tuple[str, int, str]] = []
    for p in db.q(
        "SELECT id, prompt_planche FROM personnages WHERE projet_id=? ORDER BY ordre, id",
        (projet_id,),
    ):
        # Une planche sans prompt n'est pas « a faire » : il n'y a rien a
        # envoyer. Si une scene en a vraiment besoin, c'est elle qui le dira.
        if (p["prompt_planche"] or "").strip():
            elements.append(("planche", p["id"], "chatgpt"))
    for scene in db.q(
        "SELECT id, valide FROM scenes WHERE projet_id=? ORDER BY numero", (projet_id,)
    ):
        if not scene["valide"]:
            continue
        elements.append(("image", scene["id"], "chatgpt"))
        elements.append(("video", scene["id"], "grok"))
    elements.append(("miniature", 0, "chatgpt"))
    return elements


def prochaine_etape(projet_id: int, pour: str) -> dict:
    """Ce que ce fournisseur peut lancer maintenant — ou ce qui l'en empeche.

    Chaque fournisseur suit SON ordre, strictement : ChatGPT fait les planches
    puis l'image de la scene 1, de la scene 2, de la scene 3… Quand un element
    demande ta decision, la chaine S'ARRETE DESSUS au lieu de sauter au
    suivant. Sans cet arret, un echec sur les scenes 2 et 3 faisait fabriquer
    l'image de la scene 4 : des trous dans l'episode, et plus personne ne sait
    ou on en est.
    """
    for cible_type, cible_id, fournisseur in _elements_dans_l_ordre(projet_id):
        if fournisseur != pour:
            continue  # l'autre fournisseur travaille en parallele, pas en file
        etat, motif = etat_cible(projet_id, cible_type, cible_id)
        if etat == FAIT:
            continue
        # Meme arretee, l'etape dit TOUJOURS sur quel element elle s'arrete :
        # c'est ce que tu dois voir a l'ecran pour pouvoir le debloquer.
        commun = {
            "libelle": _libelle_de(projet_id, cible_type, cible_id),
            "cible_type": cible_type,
            "cible_id": cible_id,
            "fournisseur": fournisseur,
            "etat": etat,
            "motif": motif,
        }
        if etat == A_FAIRE:
            return {**commun, "cible": (cible_type, cible_id, fournisseur)}
        return {**commun, "cible": None}
    return {"cible": None, "libelle": "", "cible_type": "", "cible_id": 0,
            "fournisseur": pour, "etat": "", "motif": ""}


def prochaine_cible(projet_id: int, pour: str | None = None) -> tuple[str, int, str] | None:
    """Prochain element a produire, dans l'ordre logique de fabrication."""
    for fournisseur in ((pour,) if pour else FOURNISSEURS):
        etape = prochaine_etape(projet_id, fournisseur)
        if etape["cible"]:
            return etape["cible"]
    return None


def etat_production(projet_id: int) -> dict:
    """Ce qui reste à faire, affichable sans rien lancer."""
    projet = _projet(projet_id)
    restants = {"planche": 0, "image": 0, "video": 0}
    dans_le_chat = planches_dans_le_chat(projet_id)
    for p in db.q("SELECT * FROM personnages WHERE projet_id=?", (projet_id,)):
        # Même règle que _refs_manquantes : une planche déjà dans le chat
        # ChatGPT, que ce pack ne fait pas refaire, n'est pas « à faire ».
        if dans_le_chat and not (p["prompt_planche"] or "").strip():
            continue
        if not fichiers.fichier_utilisable(p["ref_path"]):
            restants["planche"] += 1
    for sc in db.q("SELECT * FROM scenes WHERE projet_id=?", (projet_id,)):
        if not fichiers.fichier_utilisable(sc["image_path"]):
            restants["image"] += 1
        if not fichiers.fichier_utilisable(sc["video_path"]):
            restants["video"] += 1

    # Un etat par fournisseur : chacun avance dans SA file, et quand l'un est
    # arrete sur un element il faut pouvoir le dire sans masquer l'autre.
    etapes: dict[str, dict] = {}
    suivante = None
    blocages: list[dict] = []
    attentes: list[dict] = []
    for fournisseur in FOURNISSEURS:
        etape = prochaine_etape(projet_id, fournisseur)
        ligne = {
            "fournisseur": fournisseur,
            "libelle": etape["libelle"],
            "etat": etape["etat"],
            "motif": etape["motif"],
            "cible_type": etape["cible_type"],
            "cible_id": etape["cible_id"],
        }
        etapes[fournisseur] = ligne
        if etape["cible"] and suivante is None:
            suivante = ligne
        if etape["etat"] == BLOQUE:
            blocages.append(ligne)
        elif etape["etat"] == QUOTA_ATTENDU:
            attentes.append(ligne)

    return {
        "attentes_quota": attentes,
        "reprise_auto": bool(
            projet["pipeline"] and attentes and retour_proche(projet_id) is not None
        ),
        "active": bool(projet["pipeline"]),
        "mode": projet["pipeline_mode"] or db.reglage("mode_defaut", "assiste"),
        "suivante": suivante,
        "etapes": etapes,
        "blocages": blocages,
        "restants": restants,
    }


def _fournisseur_libre(fournisseur: str) -> bool:
    """Vrai si ce fournisseur n'a ni génération en cours ni tâche en file.

    La règle « une seule génération à la fois » devient « une seule PAR
    fournisseur » : ChatGPT et Grok ne partagent ni quota ni compte, les faire
    attendre l'un l'autre ne protégeait rien et doublait la durée de la chaîne.
    """
    if tache_active(fournisseur) is not None:
        return False
    return not db.q1(
        "SELECT id FROM taches WHERE statut='en_attente' AND fournisseur=?",
        (fournisseur,),
    )


def planifier(projet_id: int) -> list[int]:
    """Met en file la prochaine étape de CHAQUE fournisseur libre."""
    projet = _projet(projet_id)
    if not projet["pipeline"] or projet["archive"]:
        return []
    if projet["statut"] in ("pause", "arrete"):
        return []

    mode = projet["pipeline_mode"] or reglages.lire("mode_defaut")
    lancees: list[int] = []
    reste = False
    quota_lointain: list[str] = []
    attend = False
    for fournisseur in FOURNISSEURS:
        if not _fournisseur_libre(fournisseur):
            reste = True
            continue
        etape = prochaine_etape(projet_id, fournisseur)
        if etape["etat"] == BLOQUE:
            # On ne saute PAS par-dessus pour aller faire la scene suivante :
            # la chaine s'arrete ici et te dit sur quoi. Elle repartira d'elle
            # meme des que cet element sera reglé.
            reste = True
            db.journaliser(
                f"Chaîne {fournisseur} en arrêt sur « {etape['libelle']} » : "
                f"{etape['motif']} Rien n'a été lancé, rien n'a été sauté.",
                projet_id,
                "alerte",
            )
            continue
        if etape["etat"] == QUOTA_ATTENDU:
            # On n'attend tout seul que si l'heure de retour est SUE et proche.
            # Sinon la chaîne s'arrête : elle ne doit pas repartir d'elle-même
            # dans trois jours, quand tu n'y penses plus.
            if retour_proche(projet_id) is not None:
                reste = True
                attend = True
            else:
                quota_lointain.append(fournisseur)
            continue
        if etape["etat"] == ATTENTE:
            reste = True
            continue
        if etape["cible"] is None:
            continue
        reste = True
        cible_type, cible_id, _ = etape["cible"]
        try:
            lancees.append(
                demander(
                    projet_id, cible_type, cible_id,
                    fournisseur=fournisseur, mode=mode, origine="auto",
                )
            )
        except (GenerationRefusee, fichiers.MelangeDeProjets) as exc:
            db.journaliser(
                f"Étape {fournisseur} non lancée : {exc}", projet_id, "alerte"
            )

    _poser_attente_quota(projet_id, attend)
    if not reste and not lancees:
        db.exec1("UPDATE projets SET pipeline=0 WHERE id=?", (projet_id,))
        if quota_lointain:
            a_court = [
                f for f in FOURNISSEURS
                if f in comptes.SITES and comptes.a_des_comptes(f, projet["comptes_de"])
                and comptes.compte_actif(f, projet["comptes_de"]) is None
            ]
            db.journaliser(
                "Chaîne arrêtée faute de quota : "
                + " ".join(
                    f"{f} — {phrase_retour(f, projet['comptes_de'])}" for f in a_court
                )
                + " Clique « Lancer la génération » à ce moment-là : rien n'a été "
                "payé, la chaîne reprendra où elle s'est arrêtée.",
                projet_id,
                "alerte",
            )
        else:
            db.journaliser(
                "Chaîne de production terminée : plus rien à générer automatiquement.",
                projet_id,
            )
    return lancees


def phrase_des_blocages(etat: dict) -> str:
    """Une phrase pour dire sur quoi la chaine est arretee, ou "" si elle avance.

    « Rien a produire » est vrai mais trompeur quand la chaine est en fait
    parquee sur une scene qui attend ta decision : il faut nommer la scene.
    """
    phrases = [
        f"Arrêtée sur « {b['libelle']} » ({b['fournisseur']}) : {b['motif']}"
        for b in etat.get("blocages") or []
    ]
    suite = (
        " La chaîne repartira seule à ce moment-là."
        if etat.get("reprise_auto")
        else " Relance la chaîne à ce moment-là."
    )
    phrases += [
        f"« {a['libelle']} » ({a['fournisseur']}) attend le quota : {a['motif']}{suite}"
        for a in etat.get("attentes_quota") or []
    ]
    return " ".join(phrases)


def demarrer_production(projet_id: int, mode: str) -> dict:
    """Lance la chaîne complète. Elle avance d'un élément à chaque réussite."""
    projet = _projet(projet_id)
    if projet["archive"]:
        raise GenerationRefusee("Projet archivé : désarchive-le d'abord.")
    if not db.q1("SELECT id FROM scenes WHERE projet_id=? AND valide=1", (projet_id,)):
        raise GenerationRefusee(
            "Aucune scène validée. Clique d'abord sur « Valider toutes les scènes "
            "possibles » dans l'onglet Scènes."
        )
    db.exec1(
        "UPDATE projets SET pipeline=1, pipeline_mode=?, statut=CASE WHEN statut IN"
        " ('pause','arrete','brouillon') THEN 'en_cours' ELSE statut END, maj_le=?"
        " WHERE id=?",
        (mode, db.maintenant(), projet_id),
    )
    db.journaliser(
        f"Chaîne de production lancée en mode {mode} : planches, puis image et vidéo "
        "de chaque scène, une génération à la fois.",
        projet_id,
    )
    lancees = planifier(projet_id)
    libelles = [
        db.q1("SELECT libelle FROM taches WHERE id=?", (i,))["libelle"] for i in lancees
    ]
    return {
        "tache_id": lancees[0] if lancees else None,
        "taches": lancees,
        "libelle": " + ".join(l for l in libelles if l),
        "etat": etat_production(projet_id),
    }


def arreter_production(projet_id: int) -> None:
    db.exec1("UPDATE projets SET pipeline=0, attend_quota=0 WHERE id=?", (projet_id,))
    db.journaliser(
        "Chaîne de production arrêtée. Les résultats obtenus sont conservés.", projet_id
    )


# --------------------------------------------------------------------------
# Reprise apres plantage
# --------------------------------------------------------------------------

def recuperer_apres_plantage() -> list[str]:
    """Au démarrage : aucune tâche 'en_cours' ne peut survivre a un redemarrage."""
    rapports: list[str] = []
    for t in db.q("SELECT * FROM taches WHERE statut IN ('en_cours','en_attente')"):
        try:
            cible = chemin_attendu(t["projet_id"], t["cible_type"], t["cible_id"])
        except GenerationRefusee:
            cible = None
        if cible and fichiers.fichier_utilisable(cible):
            _enregistrer_resultat_existant(
                t["projet_id"], t["cible_type"], t["cible_id"], cible
            )
            db.exec1(
                "UPDATE taches SET statut='termine', fini_le=?, resultat_path=?, message=?"
                " WHERE id=?",
                (
                    db.maintenant(),
                    str(cible),
                    "Résultat retrouvé sur le disque après redemarrage.",
                    t["id"],
                ),
            )
            rapports.append(f"#{t['id']} {t['libelle']} : résultat retrouvé, aucune relance.")
            continue
        if t["statut"] == "en_cours":
            db.exec1(
                "UPDATE taches SET statut='a_verifier', fini_le=?, message=? WHERE id=?",
                (
                    db.maintenant(),
                    "Interrompue par un arrêt de l'application. À vérifier chez le "
                    "fournisseur avant toute relance.",
                    t["id"],
                ),
            )
            rapports.append(
                f"#{t['id']} {t['libelle']} : interrompue, passee en « à vérifier » "
                "(aucune relance automatique)."
            )
    for r in rapports:
        db.journaliser(f"Reprise : {r}", niveau="alerte")
    for ligne in db.q("SELECT id FROM projets WHERE pipeline=1 AND archive=0"):
        planifier(ligne["id"])
    return rapports


# --------------------------------------------------------------------------
# Worker
# --------------------------------------------------------------------------

class Worker(threading.Thread):
    """Un thread par fournisseur : une seule génération à la fois CHEZ LUI.

    Deux workers tournent côte à côte. Pendant que Grok rend la vidéo d'une
    scène, celui de ChatGPT prépare l'image de la suivante. Ils ne partagent ni
    compte, ni quota, ni navigateur — et la garantie « une seule génération
    automatique par élément » reste entière, elle porte sur l'élément.
    """

    def __init__(self, fournisseur: str, intervalle: float = 2.0) -> None:
        super().__init__(name=f"videroo-worker-{fournisseur}", daemon=True)
        self.fournisseur = fournisseur
        self._arret = threading.Event()
        self.intervalle = intervalle
        self._prochain_reveil = 0.0

    def stop(self) -> None:
        self._arret.set()

    def run(self) -> None:  # pragma: no cover - boucle de fond
        while not self._arret.is_set():
            try:
                tache = self.prochaine(self.fournisseur)
                if tache is None:
                    if time.monotonic() >= self._prochain_reveil:
                        self._prochain_reveil = time.monotonic() + 30
                        if tache_active(self.fournisseur) is None:
                            reveiller_apres_quota(self.fournisseur)
                    self._arret.wait(self.intervalle)
                    continue
                self.executer(tache["id"])
            except Exception as exc:  # garde-fou : le worker ne meurt jamais
                db.journaliser(
                    f"Erreur du worker {self.fournisseur} : {exc}", niveau="erreur"
                )
                self._arret.wait(self.intervalle)

    @staticmethod
    def prochaine(fournisseur: str | None = None) -> sqlite3.Row | None:
        if tache_active(fournisseur) is not None:
            return None  # chez ce fournisseur, une seule génération à la fois
        condition = " AND t.fournisseur = ?" if fournisseur else ""
        parametres = (fournisseur,) if fournisseur else ()
        return db.q1(
            "SELECT t.* FROM taches t JOIN projets p ON p.id = t.projet_id"
            " WHERE t.statut = 'en_attente' AND p.archive = 0"
            "   AND p.statut NOT IN ('pause','arrete')"
            + condition
            + " ORDER BY t.id LIMIT 1",
            parametres,
        )

    @staticmethod
    def executer(tache_id: int) -> str:
        t = db.q1("SELECT * FROM taches WHERE id=?", (tache_id,))
        if not t or t["statut"] != "en_attente":
            return "ignoree"
        projet_id = t["projet_id"]

        # Derniere verification juste avant de depenser un quota.
        try:
            controles_avant_lancement(projet_id, t["cible_type"], t["cible_id"])
        except GenerationRefusee as exc:
            db.exec1(
                "UPDATE taches SET statut='echec', envoye=0, fini_le=?, message=?"
                " WHERE id=?",
                (db.maintenant(), str(exc), tache_id),
            )
            db.journaliser(f"Lancement annule avant dépense : {exc}", projet_id, "alerte", tache_id)
            return "echec"

        cible = chemin_attendu(projet_id, t["cible_type"], t["cible_id"])
        if fichiers.fichier_utilisable(cible):
            _enregistrer_resultat_existant(projet_id, t["cible_type"], t["cible_id"], cible)
            db.exec1(
                "UPDATE taches SET statut='termine', fini_le=?, resultat_path=?, message=?"
                " WHERE id=?",
                (db.maintenant(), str(cible), "Résultat déjà présent : aucune génération.", tache_id),
            )
            return "deja_fait"

        ad = adaptateur(t["fournisseur"], t["mode"])
        if ad is None:
            message = (
                f"Aucun adaptateur pour {t['fournisseur']} en mode {t['mode']}. "
                "Choisis un autre mode dans Reglages."
            )
            db.exec1(
                "UPDATE taches SET statut='echec', envoye=0, fini_le=?, message=?"
                " WHERE id=?",
                (db.maintenant(), message, tache_id),
            )
            db.journaliser(message, projet_id, "erreur", tache_id)
            return "echec"

        db.exec1(
            "UPDATE taches SET statut='en_cours', lance_le=? WHERE id=?",
            (db.maintenant(), tache_id),
        )
        db.journaliser(
            f"ÉTAT AVANT LANCEMENT — {t['libelle']} via {t['fournisseur']} "
            f"(mode {t['mode']}). Sortie attendue : {cible.name}",
            projet_id,
            "info",
            tache_id,
        )

        deja_essayes: set[int] = set()
        envoye = False  # vrai des qu'un prompt est reellement parti, tout compte fait
        dit = ""        # ce que le fournisseur a dit en refusant, mot pour mot
        pool = _projet(projet_id)["comptes_de"]
        while True:
            ctx = construire_contexte(t, cible)
            # Aucun compte de CE projet n'a de quota : on ne part pas. Sans ce
            # refus, la tâche partait sur le profil « principal » — le compte de
            # quelqu'un d'autre (une série de Laura sur le Grok de Yacine).
            sans_compte = (
                t["mode"] != "demo"  # une simulation ne touche aucun compte
                and t["fournisseur"] in comptes.SITES
                and not ctx.compte_id
                and comptes.a_des_comptes(t["fournisseur"], pool)
            )
            if sans_compte or (ctx.compte_id and ctx.compte_id in deja_essayes):
                resultat = _resultat_quota(t["fournisseur"], pool, dit)
                break
            if ctx.compte_id:
                deja_essayes.add(ctx.compte_id)
                db.journaliser(
                    f"Compte utilisé : « {ctx.compte_libelle} » (profil {ctx.profil}).",
                    projet_id,
                    "info",
                    tache_id,
                )
            try:
                # Tout le pilotage navigateur passe par son thread dédié.
                resultat = nav.session(t["fournisseur"]).executer(ad.generer, ctx)
            except Exception as exc:
                resultat = base.ResultatGeneration(
                    base.A_VERIFIER,
                    f"Interruption technique : {exc}. Vérifie chez le fournisseur si une "
                    "génération a bien été lancée AVANT toute relance.",
                )
            envoye = envoye or ctx.envoye
            if resultat.etat != base.QUOTA:
                if ctx.compte_id:
                    comptes.noter_utilisation(ctx.compte_id)
                    if resultat.compte_a_court:
                        # Quota annonce APRES l'envoi : on bascule le compte
                        # pour la suite, mais on ne relance rien ici.
                        comptes.marquer_quota_epuise(ctx.compte_id, resultat.message)
                        suite = comptes.compte_actif(t["fournisseur"], pool)
                        db.journaliser(
                            f"Compte « {ctx.compte_libelle} » mis de côté : "
                            + (f"la suite partira sur « {suite['libelle']} ». "
                               if suite else "aucun compte de secours disponible. ")
                            + "Rien n'a été relancé.",
                            t["projet_id"], "alerte", t["id"],
                        )
                break
            # QUOTA = le fournisseur n'a RIEN produit : il l'a dit en réponse à
            # notre prompt (« vous avez atteint la limite… »), ou tu as déclaré
            # le compte à court. Il n'y a rien à récupérer et rien n'a été payé :
            # basculer sur un autre compte est la première génération, pas une
            # relance. Le compte est mis de côté jusqu'à l'heure qu'il annonce.
            dit = resultat.message
            if ctx.compte_id:
                comptes.marquer_quota_epuise(ctx.compte_id, resultat.message)
            secours = (
                comptes.comptes_de_secours(t["fournisseur"], ctx.compte_id, pool)
                if ctx.compte_id else []
            )
            if not secours:
                resultat = _resultat_quota(t["fournisseur"], pool, dit)
                break
            db.journaliser(
                f"Bascule automatique vers le compte « {secours[0]['libelle']} » : "
                "rien n'a été produit sur le précédent, rien n'est perdu.",
                projet_id,
                "alerte",
                tache_id,
            )

        statut = enregistrer_resultat(tache_id, resultat, cible, envoye=envoye)
        if statut == base.TERMINE:
            planifier(projet_id)
        elif statut == base.QUOTA:
            _apres_quota(projet_id, t)
        elif statut == base.ECHEC and not envoye:
            # Rien n'est parti, rien n'a ete depense : la chaine reprend sur le
            # MEME element plutot que de le sauter. Elle s'arretera d'elle-meme
            # apres ESSAIS_AUTO_MAX tentatives a blanc.
            planifier(projet_id)
        elif t["mode"] == "assiste" and statut == base.A_VERIFIER:
            db.journaliser(
                "Chaîne en attente : lance cette génération toi-même, puis clique "
                "« Importer le résultat ». L'étape suivante partira automatiquement.",
                projet_id,
                "alerte",
                tache_id,
            )
        elif db.q1("SELECT pipeline FROM projets WHERE id=?", (projet_id,))["pipeline"]:
            db.exec1("UPDATE projets SET pipeline=0 WHERE id=?", (projet_id,))
            db.journaliser(
                "Chaîne de production mise en attente : l'élément précédent n'est pas "
                "terminé. Vérifie-le, puis relance la chaîne quand tu veux.",
                projet_id,
                "alerte",
            )
        return statut


# --------------------------------------------------------------------------
# Quota : dire vrai, attendre le retour annoncé, repartir sans rien payer deux fois
# --------------------------------------------------------------------------

#: Au-delà, la chaîne n'attend pas toute seule : elle s'arrête et tu relances.
ATTENTE_QUOTA_MAX = timedelta(hours=3)
#: Refus d'affilée sur le même élément avant que la chaîne cesse d'insister.
REFUS_QUOTA_MAX = 4


def _refus_quota_d_affilee(taches: list[sqlite3.Row]) -> int:
    """Combien de fois de suite, en dernier, cet élément a été refusé faute de quota."""
    compte = 0
    for ancienne in reversed(taches):
        if ancienne["statut"] != "quota":
            break
        compte += 1
    return compte


def phrase_retour(fournisseur: str, pool: str | None) -> str:
    """Quand un compte de ce pool revient — sans rien inventer."""
    retour, source, libelle = comptes.prochain_retour(fournisseur, pool)
    if retour is None:
        return "Aucune heure de retour n'est connue."
    quand = comptes.quand_lisible(retour)
    if source == "annonce":
        return f"Retour annoncé par le fournisseur {quand} (compte « {libelle} »)."
    if source == "horaire":
        return f"Recharge de « {libelle} » réglée par toi : {quand}."
    return (
        "Le fournisseur ne dit pas quand le quota revient ; d'après ton délai "
        f"global, « {libelle} » sera réessayé {quand}."
    )


def _resultat_quota(fournisseur: str, pool: str | None, dit: str) -> base.ResultatGeneration:
    """Plus aucun compte de ce projet n'a de quota : l'état « quota », dit tel quel."""
    debut = dit.strip() or (
        f"Aucun compte {fournisseur} de ce projet n'a de quota disponible : rien "
        "n'a été envoyé."
    )
    return base.ResultatGeneration(
        base.QUOTA,
        f"{debut} Il n'y a rien à récupérer et rien n'a été payé. "
        f"{phrase_retour(fournisseur, pool)}",
    )


def quota_attendu_sous_peu(fournisseur: str, pool: str | None) -> datetime | None:
    """L'heure de retour, si elle est SUE (annoncée ou réglée) et assez proche."""
    retour, source, _ = comptes.prochain_retour(fournisseur, pool)
    if retour is None or source not in ("annonce", "horaire"):
        return None
    return retour if retour - datetime.now() <= ATTENTE_QUOTA_MAX else None


def retour_proche(projet_id: int) -> datetime | None:
    """L'heure où TOUS les fournisseurs à court de ce projet seront revenus.

    None si l'un d'eux n'a pas d'heure de retour sue et proche : la chaîne ne
    doit alors pas attendre toute seule.
    """
    pool = _projet(projet_id)["comptes_de"]
    heures: list[datetime] = []
    for fournisseur in FOURNISSEURS:
        if fournisseur not in comptes.SITES or not comptes.a_des_comptes(fournisseur, pool):
            continue
        if comptes.compte_actif(fournisseur, pool) is not None:
            continue
        retour = quota_attendu_sous_peu(fournisseur, pool)
        if retour is None:
            return None
        heures.append(retour)
    return max(heures) if heures else None


def _poser_attente_quota(projet_id: int, attend: bool) -> None:
    db.exec1("UPDATE projets SET attend_quota=? WHERE id=?", (1 if attend else 0, projet_id))


def _apres_quota(projet_id: int, t: sqlite3.Row) -> None:
    """La chaîne attend le retour annoncé, ou s'arrête en disant quand revenir."""
    projet = _projet(projet_id)
    if not projet["pipeline"]:
        return
    refus = _refus_quota_d_affilee(
        taches_cible(projet_id, t["cible_type"], t["cible_id"])
    )
    retour = retour_proche(projet_id)
    if retour is not None and refus < REFUS_QUOTA_MAX:
        _poser_attente_quota(projet_id, True)
        db.journaliser(
            f"Chaîne {t['fournisseur']} en attente du quota : elle repartira seule sur "
            f"« {t['libelle']} » {comptes.quand_lisible(retour)}. Rien n'a été payé, "
            "rien ne sera payé deux fois. « Arrêter » annule cette attente.",
            projet_id,
            "alerte",
        )
        return
    # planifier décide : l'autre fournisseur continue s'il a du travail, sinon
    # la chaîne s'arrête en disant quand revenir.
    planifier(projet_id)


def reveiller_apres_quota(fournisseur: str) -> list[int]:
    """Relance les chaînes qui attendaient un quota maintenant revenu.

    Appelé par le worker quand il n'a rien à faire. Ne touche QUE les chaînes
    en marche (tu ne les as pas arrêtées) dont le prochain élément a été refusé
    faute de quota : rien d'autre ne repart jamais tout seul.
    """
    relancees: list[int] = []
    for p in db.q(
        "SELECT id FROM projets WHERE pipeline=1 AND attend_quota=1 AND archive=0"
        " AND statut NOT IN ('pause','arrete')"
    ):
        etape = prochaine_etape(p["id"], fournisseur)
        if etape["etat"] != A_FAIRE or not etape["cible"]:
            continue
        cible_type, cible_id, _ = etape["cible"]
        passees = taches_cible(p["id"], cible_type, cible_id)
        if not passees or passees[-1]["statut"] != "quota":
            continue
        db.journaliser(
            f"Quota {fournisseur} revenu : la chaîne reprend sur « {etape['libelle']} ».",
            p["id"],
        )
        relancees.extend(planifier(p["id"]))
    return relancees


def construire_contexte(t: sqlite3.Row, cible: Path) -> base.ContexteTache:
    projet = _projet(t["projet_id"])
    dossier = Path(projet["dossier"])
    joints: list[Path] = []
    duree = None
    extras: dict = {}

    if t["cible_type"] == "image":
        s = db.q1("SELECT * FROM scenes WHERE id=?", (t["cible_id"],))
        for code in db.charger_json(s["refs_codes"], []):
            p = db.q1(
                "SELECT ref_path FROM personnages WHERE projet_id=? AND code=?",
                (t["projet_id"], code),
            )
            if p and fichiers.fichier_utilisable(p["ref_path"]):
                joints.append(Path(p["ref_path"]))
        extras = {"scene_numero": s["numero"]}
    elif t["cible_type"] == "video":
        s = db.q1("SELECT * FROM scenes WHERE id=?", (t["cible_id"],))
        joints = pieces_jointes_video(t["projet_id"], t["cible_id"])
        duree = s["duree"]
        # Les images de départ des AUTRES scènes : une vidéo qui démarre sur
        # l'une d'elles n'est pas celle-ci (seconde prise arrivée en retard).
        autres = {
            a["numero"]: a["image_path"]
            for a in db.q(
                "SELECT numero, image_path FROM scenes WHERE projet_id=? AND id<>?",
                (t["projet_id"], t["cible_id"]),
            )
            if fichiers.fichier_utilisable(a["image_path"])
        }
        extras = {
            "scene_numero": s["numero"], "version": s["version_video"],
            "images_scenes": autres,
        }
    elif t["cible_type"] == "miniature":
        # « parfaitement identiques » : sans les planches jointes, la demande est
        # un vœu pieux. Un code comme R1 ne remplace pas une image.
        for p in db.q(
            "SELECT ref_path FROM personnages WHERE projet_id=? ORDER BY ordre, id",
            (t["projet_id"],),
        ):
            if fichiers.fichier_utilisable(p["ref_path"]):
                joints.append(Path(p["ref_path"]))

    if t["fournisseur"] == "chatgpt":
        # Les images déjà rangées ailleurs dans le projet ne peuvent pas être le
        # résultat de cette tâche-ci : l'adaptateur s'en sert pour ne jamais
        # rattacher l'image de la scène d'avant à la scène en cours.
        extras["connues"] = medias_deja_rattaches(
            t["projet_id"], (t["cible_type"], t["cible_id"])
        )
        if t["cible_type"] != "video":
            extras["marqueur"], extras["autres_marqueurs"] = marqueurs_chatgpt(
                t["projet_id"], t["cible_type"], t["cible_id"]
            )
    origine = t["origine"] if "origine" in t.keys() else "manuel"
    extras["origine"] = origine or "manuel"

    def _log(message: str) -> None:
        db.journaliser(message, t["projet_id"], "info", t["id"])

    actif = (
        comptes.compte_actif(t["fournisseur"], projet["comptes_de"])
        if t["fournisseur"] in comptes.SITES
        else None
    )

    return base.ContexteTache(
        projet_id=t["projet_id"],
        tache_id=t["id"],
        cible_type=t["cible_type"],
        cible_id=t["cible_id"],
        libelle=t["libelle"] or "",
        prompt=_prompt_de(t["projet_id"], t["cible_type"], t["cible_id"]),
        dossier_projet=dossier,
        chemin_sortie=cible,
        fichiers_joints=joints,
        duree=duree,
        serie=projet["grok_serie"] or "",
        episode=projet["grok_episode"] or 1,
        url_chat=(
            projet["grok_chat_url"] if t["fournisseur"] == "grok" else projet["chatgpt_chat_url"]
        )
        or "",
        chat_compte=(actif.get("chat_url") or "") if actif else "",
        profil=actif["profil"] if actif else config.PROFIL_PRINCIPAL,
        compte_id=actif["id"] if actif else 0,
        compte_libelle=actif["libelle"] if actif else "",
        extras=extras,
        journaliser=_log,
    )


def memoriser_conversation(projet_id: int, fournisseur: str, adresse: str) -> None:
    """Garde l'adresse du chat ouvert pour cette histoire, afin d'y rester.

    Toutes les planches et toutes les images d'une même histoire doivent sortir
    de la même conversation ; une nouvelle histoire repart d'un chat neuf.
    """
    if not adresse or "/c/" not in adresse:
        return
    colonne = "chatgpt_chat_url" if fournisseur == "chatgpt" else "grok_chat_url"
    actuelle = db.q1(f"SELECT {colonne} AS u FROM projets WHERE id=?", (projet_id,))
    if actuelle and (actuelle["u"] or "").strip():
        return
    db.exec1(f"UPDATE projets SET {colonne}=? WHERE id=?", (adresse, projet_id))
    db.journaliser(
        f"Conversation {fournisseur} de cette histoire mémorisée : {adresse}. "
        "Les prochaines générations y resteront.",
        projet_id,
    )


def enregistrer_resultat(
    tache_id: int,
    resultat: base.ResultatGeneration,
    cible: Path,
    envoye: bool = True,
) -> str:
    t = db.q1("SELECT * FROM taches WHERE id=?", (tache_id,))
    if not t:
        return "inconnue"
    # Enregistre AVANT de changer le statut : c'est ce fait-la qui decide si la
    # chaine a le droit de retenter cet element toute seule.
    db.exec1("UPDATE taches SET envoye=? WHERE id=?", (1 if envoye else 0, tache_id))
    projet_id = t["projet_id"]
    # Côté Grok, seule une vidéo réussie prouve que c'était le bon chat : le
    # 16 novembre 2026, un envoi parti par erreur dans une vieille discussion a
    # fait retenir cette discussion comme chat de l'histoire.
    if t["fournisseur"] != "grok" or resultat.etat == base.TERMINE:
        memoriser_conversation(projet_id, t["fournisseur"], resultat.url)
    statut = resultat.etat
    message = resultat.message

    if statut == base.TERMINE:
        chemin = Path(resultat.chemin or cible)
        if not fichiers.fichier_utilisable(chemin):
            statut = base.A_VERIFIER
            message = (
                "L'adaptateur annonce une réussite mais aucun fichier utilisable n'a été "
                f"enregistré ({chemin}). Rien n'est relancé automatiquement. " + message
            )
        else:
            try:
                fichiers.verifier_appartenance(
                    chemin, projet_id, db.dossier_projet(projet_id)
                )
            except fichiers.MelangeDeProjets as exc:
                statut = base.ECHEC
                message = str(exc)

    if statut == base.TERMINE:
        chemin = Path(resultat.chemin or cible)
        if t["cible_type"] == "video":
            infos = media.analyser_media(chemin)
            if not infos.lisible:
                statut = base.A_VERIFIER
                message = f"Vidéo téléchargée mais illisible : {infos.erreur}. {message}"
            else:
                message = (
                    f"{message} Vérifie : {infos.duree:.2f} s, "
                    f"{infos.largeur}x{infos.hauteur}, audio "
                    f"{'présent' if infos.a_audio else 'ABSENT'}."
                )
                s = db.q1("SELECT duree FROM scenes WHERE id=?", (t["cible_id"],))
                if s and s["duree"] and abs(infos.duree - s["duree"]) > 1.5:
                    message += (
                        f" Attention : {infos.duree:.1f} s au lieu de {s['duree']} s "
                        "demandees."
                    )
        _enregistrer_resultat_existant(projet_id, t["cible_type"], t["cible_id"], chemin)

    if statut == base.INTERVENTION:
        changer_statut_projet(projet_id, "pause")
        db.exec1(
            "UPDATE taches SET statut='a_verifier', fini_le=?, message=? WHERE id=?",
            (db.maintenant(), f"{message} {resultat.instruction}".strip(), tache_id),
        )
        db.journaliser(
            f"ÉTAT APRÈS — PAUSE requise : {message} Action : {resultat.instruction}",
            projet_id,
            "alerte",
            tache_id,
        )
        return "intervention"

    db.exec1(
        "UPDATE taches SET statut=?, fini_le=?, resultat_path=?, message=? WHERE id=?",
        (
            statut,
            db.maintenant(),
            str(resultat.chemin or ""),
            (f"{message} {resultat.instruction}".strip() + (" [SIMULATION]" if resultat.simulation else "")),
            tache_id,
        ),
    )
    db.journaliser(
        f"ÉTAT APRÈS — {t['libelle']} : {statut} — {message}"
        + (" [SIMULATION — ce n'est pas une vraie génération]" if resultat.simulation else ""),
        projet_id,
        "info" if statut == base.TERMINE else "alerte",
        tache_id,
    )
    db.toucher_projet(projet_id)
    return statut
