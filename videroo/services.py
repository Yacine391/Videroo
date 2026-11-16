"""Opérations métier : projets, import du pack, validation, medias, export."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from . import (
    comptes, config, db, fichiers, media, montage, parser_v6, reglages, runner,
    srt,
)
from .adapters import base as abase


class ErreurMetier(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Projets
# --------------------------------------------------------------------------

def type_histoire_valide(type_histoire: str | None) -> str:
    """Un des types connus, sinon celui par defaut. Jamais d'invention."""
    choisi = (type_histoire or "").strip()
    return choisi if choisi in config.TYPES_PROJET else config.TYPE_PROJET_DEFAUT


def creer_projet(
    nom: str,
    type_histoire: str | None = None,
    comptes_de: str = "",
    chat_chatgpt: str = "",
    refs_dans_chat: bool = False,
    episode: int = 1,
) -> int:
    """Crée le projet ; pour une série commencée avant Videroo, on peut donner
    tout de suite son chat ChatGPT et le numéro de l'épisode à produire. Côté
    Grok, la série vit déjà dans le chat dédié du compte : seul le numéro compte."""
    from .adresses import est_une_conversation
    chat_chatgpt = (chat_chatgpt or "").strip()
    chat_chatgpt = chat_chatgpt if est_une_conversation(chat_chatgpt) else None
    nom = (nom or "").strip() or "Projet sans titre"
    type_histoire = type_histoire_valide(type_histoire)
    projet_id = db.exec1(
        "INSERT INTO projets (nom, statut, type_histoire, dossier, cree_le, maj_le,"
        " duree_cible_min, duree_cible_max) VALUES (?,?,?,?,?,?,?,?)",
        (nom, "brouillon", type_histoire, "", db.maintenant(), db.maintenant(),
         reglages.lire_float("duree_cible_min"), reglages.lire_float("duree_cible_max")),
    )
    dossier = fichiers.creer_dossier_projet(projet_id, nom)
    db.exec1(
        "UPDATE projets SET dossier=?, grok_serie=?, comptes_de=?, chatgpt_chat_url=?,"
        " refs_dans_chat=?, grok_episode=? WHERE id=?",
        (str(dossier), fichiers.slug(nom).replace("-", "_"),
         comptes_de if comptes_de in comptes.proprietaires() else None,
         chat_chatgpt, 1 if (refs_dans_chat and chat_chatgpt) else 0, max(1, int(episode or 1)),
         projet_id),
    )
    if comptes_de in comptes.proprietaires():
        _reprendre_reglages_de(projet_id, comptes_de, bool(chat_chatgpt), episode)
    db.journaliser(
        f"Projet « {nom} » créé dans {dossier} "
        f"(type : {config.TYPES_PROJET[type_histoire]}).",
        projet_id,
    )
    return projet_id


#: Ce qu'une partie reprend de la precedente pour rester la meme histoire :
#  les deux conversations (memes visages, meme style), les regles deja posees
#  dans le chat Grok (pour ne pas les renvoyer) et le format de sortie.
#  L'annonce de serie n'en fait PAS partie : la premiere scene de la partie
#  N+1 doit dire « "<serie>" - Partie N+1 : » dans ce meme chat.
HERITAGE_SERIE = (
    "type_histoire", "grok_serie", "chatgpt_chat_url", "grok_chat_url",
    "grok_regles_installees", "grok_profil_regles", "comptes_de",
    "duree_cible_min",
    "duree_cible_max", "vitesse", "export_largeur", "export_hauteur",
)


def creer_partie_suivante(projet_id: int) -> dict:
    """Prepare la partie N+1 d'une serie, sans rien generer.

    Elle reprend les chats ChatGPT et Grok de la partie N : les personnages y
    ont deja leur visage, les regles et la serie y sont deja posees. Les
    planches sont COPIEES dans son dossier : effacer la partie 1 ne casse pas
    la 2, et au moment ou tu colles le pack de la suite, chaque code deja
    connu (R1, O2...) garde son image au lieu de couter une nouvelle planche.

    Si la partie suivante existe deja, on la rend telle quelle : deux clics ne
    font pas deux « partie 2 ».
    """
    source = projet(projet_id)
    deja = db.q1(
        "SELECT id, grok_episode FROM projets WHERE partie_de=? ORDER BY id LIMIT 1",
        (projet_id,),
    )
    if deja:
        return {"id": deja["id"], "episode": deja["grok_episode"], "cree": False,
                "planches": 0}

    episode = (source.get("grok_episode") or 1) + 1
    racine = re.sub(r"\s*[—–-]\s*partie\s+\d+\s*$", "", source["nom"],
                    flags=re.IGNORECASE).strip() or source["nom"]
    nouveau = creer_projet(f"{racine} — Partie {episode}",
                           source.get("type_histoire"))
    db.exec1(
        f"UPDATE projets SET {', '.join(f'{c}=?' for c in HERITAGE_SERIE)},"
        " grok_episode=?, partie_de=? WHERE id=?",
        (*(source.get(c) for c in HERITAGE_SERIE), episode, projet_id, nouveau),
    )

    dossier = Path(projet(nouveau)["dossier"])
    copiees = 0
    for i, p in enumerate(db.q(
        "SELECT * FROM personnages WHERE projet_id=? ORDER BY ordre, id", (projet_id,)
    )):
        ref_path, origine = None, None
        if fichiers.fichier_utilisable(p["ref_path"]):
            cible = fichiers.chemin_planche(dossier, p["code"], p["surnom"] or "",
                                            p["etat"] or "UNIQUE")
            cible.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p["ref_path"], cible)
            ref_path, origine = str(cible), f"partie {episode - 1}"
            copiees += 1
        db.exec1(
            "INSERT INTO personnages (projet_id, code, surnom, etat, espece, role, age,"
            " genre, voix, etiquette_grok, lien, prompt_planche, ref_path, ref_origine,"
            " ordre) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (nouveau, p["code"], p["surnom"], p["etat"], p["espece"], p["role"],
             p["age"], p["genre"], p["voix"], p["etiquette_grok"], p["lien"],
             p["prompt_planche"], ref_path, origine, i),
        )

    titre = source.get("titre") or source["nom"]
    db.journaliser(
        f"Partie {episode} préparée à la suite de « {titre} » : mêmes chats "
        f"ChatGPT et Grok, {copiees} planche(s) reprise(s). Rien n'est lancé : "
        "colle d'abord le pack de cette partie.",
        nouveau,
    )
    db.journaliser(f"Partie {episode} préparée (projet #{nouveau}).", projet_id)
    return {"id": nouveau, "episode": episode, "cree": True, "planches": copiees}


def serie_du_projet(projet_id: int) -> dict:
    """Ou en est cet episode, et ce qui l'entoure dans sa serie."""
    pr = projet(projet_id)
    precedente = None
    if pr.get("partie_de"):
        precedente = db.q1(
            "SELECT id, nom, titre, grok_episode FROM projets WHERE id=?",
            (pr["partie_de"],),
        )
    suivante = db.q1(
        "SELECT id, nom, titre, grok_episode FROM projets WHERE partie_de=?"
        " ORDER BY id LIMIT 1",
        (projet_id,),
    )
    videos = [s["video_path"] for s in db.q(
        "SELECT video_path FROM scenes WHERE projet_id=?", (projet_id,)
    )]
    return {
        "episode": pr.get("grok_episode") or 1,
        "precedente": dict(precedente) if precedente else None,
        "suivante": dict(suivante) if suivante else None,
        "nb_scenes": len(videos),
        "videos_pretes": sum(1 for v in videos if fichiers.fichier_utilisable(v)),
        "chatgpt_chat_url": pr.get("chatgpt_chat_url") or "",
        "grok_chat_url": pr.get("grok_chat_url") or "",
    }


def mettre_de_cote(chemin: str | None) -> str | None:
    """Renomme un média qui ne correspond plus à sa scène, sans l'effacer.

    Sous son nom habituel (« scene-01_image.png »), la reprise après plantage le
    reprendrait pour un résultat de la scène 1. Renommé, il reste sur le disque
    mais plus rien ne le rattache tout seul.
    """
    if not chemin or not fichiers.fichier_utilisable(chemin):
        return None
    import time
    source = Path(chemin)
    cible = source.with_name(f"{source.stem}_ancien-{time.strftime('%Y%m%d-%H%M%S')}{source.suffix}")
    source.rename(cible)
    return str(cible)


def dernier_projet_de(proprietaire: str, sauf: int = 0) -> dict | None:
    ligne = db.q1(
        "SELECT * FROM projets WHERE comptes_de=? AND id!=? ORDER BY id DESC LIMIT 1",
        (proprietaire, sauf),
    )
    return dict(ligne) if ligne else None


#: Réglages d'une série tenue sur les comptes d'une autre personne. Enregistrés
#  à part (pas dans un projet) : supprimer un épisode ne les fait pas disparaître.
CHAMPS_SERIE_DE = (
    "type_histoire", "grok_serie", "chatgpt_chat_url", "refs_dans_chat",
    "duree_cible_min", "duree_cible_max", "vitesse", "export_largeur", "export_hauteur",
)


def serie_de(proprietaire: str) -> dict | None:
    return db.charger_json(db.reglage(f"serie_de:{proprietaire}"), None)


def memoriser_serie_de(projet_id: int) -> None:
    """Retient les réglages de ce projet comme ceux de la série de son propriétaire."""
    pr = projet(projet_id)
    if not pr.get("comptes_de"):
        return
    base = re.sub(r"\s*[·—–-]\s*[ÉE]pisode\s*\d+.*$", "", pr["nom"], flags=re.IGNORECASE).strip()
    serie = {c: pr.get(c) for c in CHAMPS_SERIE_DE}
    serie.update(nom=base or pr["nom"], episode=pr.get("grok_episode") or 1)
    db.definir_reglage(f"serie_de:{pr['comptes_de']}", json.dumps(serie, ensure_ascii=False))


def nom_episode_suivant(proprietaire: str) -> str:
    serie = serie_de(proprietaire) or {}
    return f"{serie.get('nom') or 'Série de ' + proprietaire} · Épisode {(serie.get('episode') or 0) + 1}"


def _reprendre_reglages_de(projet_id: int, proprietaire: str, chat_donne: bool, episode: int) -> None:
    """Les comptes d'une autre personne servent à UNE série (Laura : Secret Pets).

    Le nouveau projet reprend les réglages enregistrés de cette série : type,
    série Grok, chat ChatGPT et planches déjà dedans, format, et passe à
    l'épisode suivant. Ce que tu as saisi toi-même à la création reste prioritaire.
    """
    serie = serie_de(proprietaire)
    if serie:
        champs = {
            c: serie[c] for c in CHAMPS_SERIE_DE
            if c not in ("chatgpt_chat_url", "refs_dans_chat") and serie.get(c) is not None
        }
        champs["grok_episode"] = episode if episode and episode > 1 else (serie.get("episode") or 0) + 1
        if not chat_donne:
            champs["chatgpt_chat_url"] = serie.get("chatgpt_chat_url")
            champs["refs_dans_chat"] = serie.get("refs_dans_chat") or 0
        precedent = dernier_projet_de(proprietaire, projet_id)
        if precedent:
            champs["partie_de"] = precedent["id"]
        db.exec1(
            f"UPDATE projets SET {', '.join(f'{c}=?' for c in champs)} WHERE id=?",
            (*champs.values(), projet_id),
        )
        db.journaliser(
            f"Réglages de la série « {serie.get('nom')} » (comptes de {proprietaire}) "
            f"repris : série Grok {serie.get('grok_serie')}, épisode {champs['grok_episode']}.",
            projet_id,
        )
    memoriser_serie_de(projet_id)

def lister_projets(archive: bool = False) -> list[dict]:
    lignes = db.q(
        "SELECT * FROM projets WHERE archive=? ORDER BY maj_le DESC",
        (1 if archive else 0,),
    )
    sortie = []
    for l in lignes:
        d = dict(l)
        d["nb_scenes"] = (
            db.q1("SELECT COUNT(*) n FROM scenes WHERE projet_id=?", (l["id"],))["n"]
        )
        d["nb_images"] = db.q1(
            "SELECT COUNT(*) n FROM scenes WHERE projet_id=? AND image_path IS NOT NULL"
            " AND image_path <> ''",
            (l["id"],),
        )["n"]
        d["nb_videos"] = db.q1(
            "SELECT COUNT(*) n FROM scenes WHERE projet_id=? AND video_path IS NOT NULL"
            " AND video_path <> ''",
            (l["id"],),
        )["n"]
        sortie.append(d)
    return sortie


def projet(projet_id: int) -> dict:
    ligne = db.q1("SELECT * FROM projets WHERE id=?", (projet_id,))
    if not ligne:
        raise ErreurMetier(f"Projet {projet_id} inconnu.")
    return dict(ligne)


def archiver(projet_id: int, archive: bool = True) -> None:
    db.exec1(
        "UPDATE projets SET archive=?, maj_le=? WHERE id=?",
        (1 if archive else 0, db.maintenant(), projet_id),
    )
    db.journaliser("Projet archivé." if archive else "Projet désarchivé.", projet_id)


CHAMPS_PROJET_EDITABLES = {
    "nom", "titre", "moteur", "univers", "parties", "cliffhanger", "une_phrase",
    "regle_mechant", "menace_evolutive", "prix_final", "replique_tiktok",
    "instructions_miniature", "grok_serie", "grok_episode", "grok_chat_url",
    "chatgpt_chat_url", "duree_cible_min", "duree_cible_max", "vitesse",
    "musique_volume", "export_largeur", "export_hauteur", "grok_regles_installees",
    "grok_serie_annoncee", "refs_dans_chat",
}


def maj_projet(projet_id: int, champs: dict[str, Any]) -> None:
    retenus = {k: v for k, v in champs.items() if k in CHAMPS_PROJET_EDITABLES}
    if not retenus:
        return
    assignations = ", ".join(f"{k}=?" for k in retenus)
    db.exec1(
        f"UPDATE projets SET {assignations}, maj_le=? WHERE id=?",
        (*retenus.values(), db.maintenant(), projet_id),
    )


# --------------------------------------------------------------------------
# Import du pack V6
# --------------------------------------------------------------------------

def personnages_connus(projet_id: int) -> list[dict]:
    """Les personnages déjà là, ou à défaut ceux de la partie précédente."""
    champs = ("code", "surnom", "etat", "espece", "role", "age", "genre", "voix",
              "etiquette_grok", "lien", "prompt_planche")
    pr = projet(projet_id)
    for pid in (projet_id, pr.get("partie_de")):
        if not pid:
            continue
        lignes = db.q(f"SELECT {', '.join(champs)} FROM personnages WHERE projet_id=? ORDER BY ordre, id", (pid,))
        if lignes:
            return [{c: (l[c] or "") for c in champs} for l in lignes]
    return []


def analyser_pack(
    texte: str, type_histoire: str | None = None, projet_id: int | None = None
) -> parser_v6.ResultatImport:
    """Lit le pack, puis le complète avec ce que tu as appris à l'application."""
    from . import lecteur_souple

    resultat = _analyser_pack(texte, type_histoire, projet_id)
    if resultat.type_document != "metaprompt":
        if not resultat.personnages and projet_id:
            # Un pack sans planches (« utiliser les planches validées ») garde
            # les personnages déjà rangés dans le projet : sinon l'import les
            # effacerait, avec le lien vers leurs images.
            resultat.personnages = [
                {"code": p["code"], "surnom": p["surnom"], "etat": p["etat"] or "UNIQUE",
                 "espece": p["espece"], "role": p["role"], "age": p["age"],
                 "genre": p["genre"], "voix": p["voix"], "etiquette_grok": p["etiquette_grok"],
                 "lien": p["lien"], "prompt_planche": p["prompt_planche"]}
                for p in personnages_connus(projet_id)
            ]
            if resultat.personnages:
                resultat.avertissements = [
                    a for a in resultat.avertissements if a.cible != "personnages"
                ]
        lecteur_souple.completer(texte or "", resultat)
    return resultat


def _analyser_pack(
    texte: str, type_histoire: str | None = None, projet_id: int | None = None
) -> parser_v6.ResultatImport:
    """Lit le pack avec le lecteur de sa famille.

    Les trois formats n'ont rien en commun : le V6 livre des blocs titres, la
    chronique une etiquette par ligne, le bebe ses propres en-tetes avec des
    planches d'objets. Si le document est manifestement ecrit dans une autre
    forme, on le lit quand meme et on le dit : mieux vaut un pack importe avec
    une remarque qu'un refus sec devant un copier-coller valable.
    """
    from . import lecteur_souple, parser_bebe, parser_chronique

    lecteurs = {
        "fruits": (parser_v6.analyser, "V6"),
        "chronique": (parser_chronique.analyser, "chronique"),
        "bebe": (parser_bebe.analyser, "bébé"),
        "serie": (lecteur_souple.analyser_serie, "série en plans"),
    }
    type_histoire = type_histoire_valide(type_histoire)
    premier, attendu = lecteurs[type_histoire]

    connus = personnages_connus(projet_id) if projet_id else []
    if type_histoire == "chronique" and connus:
        resultat = parser_chronique.analyser(texte, connus)
    else:
        resultat = premier(texte)
    if resultat.scenes or resultat.personnages or resultat.type_document == "metaprompt":
        return resultat
    if type_histoire == "serie":
        # Les plans sont lus ensuite par lecteur_souple : aucun autre lecteur ne
        # doit prendre le résumé du pack pour le découpage.
        return resultat

    for famille, (lecteur, nom) in lecteurs.items():
        if famille == type_histoire:
            continue
        secours = lecteur(texte)
        if not (secours.scenes or secours.personnages):
            continue
        secours.avertir(
            "info",
            "document",
            f"Ce projet est de type « {config.TYPES_PROJET[type_histoire]} », qui "
            f"attend un pack au format {attendu} ; le document collé est au format "
            f"{nom}. Il a été lu quand même, rien n'a été inventé.",
        )
        return secours
    return resultat


def appliquer_pack(projet_id: int, resultat: parser_v6.ResultatImport) -> dict:
    """Ecrit le pack analyse dans la base. Conserve les medias déjà obtenus."""
    if resultat.type_document == "metaprompt":
        raise ErreurMetier(
            "Ce document est le métaprompt V6, pas un pack d'histoire. "
            "Enregistre-le dans l'onglet Métaprompt."
        )
    if not resultat.scenes and not resultat.personnages:
        raise ErreurMetier(
            "Aucune scène ni personnage n'a pu être lu. Vérifie le document : "
            "rien n'a été enregistré."
        )

    pr = projet(projet_id)
    dossier = Path(pr["dossier"])

    # Clé = code ET nom : si un réimport renumérote (R4 était Kenza, devient Amira),
    # Amira ne doit pas hériter de l'image de Kenza.
    anciens_refs = {
        (p["code"], (p["surnom"] or "").casefold()): (p["ref_path"], p["ref_origine"])
        for p in db.q(
            "SELECT code, surnom, ref_path, ref_origine FROM personnages WHERE projet_id=?",
            (projet_id,),
        )
    }
    anciens_medias = {
        s["numero"]: dict(s)
        for s in db.q("SELECT * FROM scenes WHERE projet_id=?", (projet_id,))
    }

    db.exec1("DELETE FROM personnages WHERE projet_id=?", (projet_id,))
    db.exec1("DELETE FROM scenes WHERE projet_id=?", (projet_id,))

    for i, p in enumerate(resultat.personnages):
        ref_path, ref_origine = anciens_refs.get(
            (p["code"], (p["surnom"] or "").casefold()), (None, None)
        )
        if not ref_path:
            # Planche copiée depuis la partie précédente (« Passer à la partie N »)
            # mais pas encore rattachée : elle est déjà dans le dossier du projet.
            copie = fichiers.chemin_planche(dossier, p["code"], p["surnom"] or "", p["etat"] or "UNIQUE")
            if fichiers.fichier_utilisable(copie):
                ref_path, ref_origine = str(copie), "partie précédente"
        if ref_path and not fichiers.fichier_utilisable(ref_path):
            ref_path, ref_origine = None, None
        db.exec1(
            "INSERT INTO personnages (projet_id, code, surnom, etat, espece, role, age,"
            " genre, voix, etiquette_grok, lien, prompt_planche, ref_path, ref_origine,"
            " ordre) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                projet_id, p["code"], p["surnom"], p["etat"], p["espece"], p["role"],
                p["age"], p["genre"], p["voix"], p["etiquette_grok"], p["lien"],
                p["prompt_planche"], ref_path, ref_origine, i,
            ),
        )

    for s in resultat.scenes:
        ancien = anciens_medias.get(s["numero"], {})
        image_path = ancien.get("image_path")
        video_path = ancien.get("video_path")
        # Une scène dont le prompt a changé n'est plus la même scène : garder son
        # ancienne image (ou vidéo) la ferait passer pour faite avec un mauvais plan.
        # Le fichier reste sur le disque, seul le rattachement est retiré.
        # Le fichier est aussi renommé : laissé sous son nom habituel, la reprise
        # après plantage le « retrouverait » et le rattacherait aussitôt.
        if image_path and (ancien.get("prompt_image") or "").strip() != (s["prompt_image"] or "").strip():
            mettre_de_cote(image_path)
            mettre_de_cote(video_path)
            image_path, video_path = None, None
        elif video_path and (ancien.get("prompt_video") or "").strip() != (s["prompt_video"] or "").strip():
            mettre_de_cote(video_path)
            video_path = None
        if image_path and not fichiers.fichier_utilisable(image_path):
            image_path = None
        if video_path and not fichiers.fichier_utilisable(video_path):
            video_path = None
        db.exec1(
            "INSERT INTO scenes (projet_id, numero, titre, emoji, duree, attache,"
            " inventaire, ce_qui_change, refs_codes, refs_video, refs_brut,"
            " prompt_image,"
            " prompt_video, version_video, dialogues, nb_repliques, nb_mots,"
            " image_path, image_origine, video_path, video_origine, video_duree,"
            " video_audio, trim_debut, trim_fin, valide)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                projet_id, s["numero"], s["titre"], s["emoji"], s["duree"], s["attache"],
                s["inventaire"], s["ce_qui_change"],
                json.dumps(s["refs_codes"], ensure_ascii=False),
                json.dumps(s.get("refs_video") or [], ensure_ascii=False),
                s["refs_brut"], s["prompt_image"], s["prompt_video"], s["version_video"],
                json.dumps(s["dialogues"], ensure_ascii=False),
                s["nb_repliques"], s["nb_mots"],
                image_path, ancien.get("image_origine"), video_path,
                ancien.get("video_origine"), ancien.get("video_duree"),
                ancien.get("video_audio"), ancien.get("trim_debut") or 0,
                ancien.get("trim_fin") or 0, 0,
            ),
        )

    db.exec1(
        "UPDATE projets SET titre=?, moteur=?, univers=?, parties=?, cliffhanger=?,"
        " une_phrase=?, regle_mechant=?, menace_evolutive=?, prix_final=?,"
        " replique_tiktok=?, duree_annoncee=?, nb_scenes_annonce=?,"
        " instructions_miniature=COALESCE(NULLIF(?,''), instructions_miniature),"
        " titre_miniature=COALESCE(NULLIF(?,''), titre_miniature),"
        " pack_brut=?, avertissements=?, etiquettes_inconnues=?, statut=?, valide=0,"
        " maj_le=? WHERE id=?",
        (
            resultat.titre or pr["titre"], resultat.moteur, resultat.univers,
            resultat.parties, resultat.cliffhanger, resultat.une_phrase,
            resultat.regle_mechant, resultat.menace_evolutive, resultat.prix_final,
            resultat.replique_tiktok, resultat.duree_annoncee, resultat.nb_scenes_annonce,
            resultat.instructions_miniature, resultat.titre_miniature, resultat.brut,
            json.dumps([a.to_dict() for a in resultat.avertissements], ensure_ascii=False),
            json.dumps(resultat.etiquettes_inconnues, ensure_ascii=False),
            "brouillon", db.maintenant(), projet_id,
        ),
    )
    if resultat.vitesse and 0.5 <= resultat.vitesse <= 3:
        # Le pack prévoit lui-même l'accélération du montage (« ×1,3 »).
        db.exec1("UPDATE projets SET vitesse=? WHERE id=?", (resultat.vitesse, projet_id))
    (dossier / "pack_importe.md").write_text(resultat.brut, encoding="utf-8")
    db.journaliser(
        f"Pack importé : {len(resultat.personnages)} personnage(s), "
        f"{len(resultat.scenes)} scène(s), {len(resultat.avertissements)} point(s) "
        "à vérifier. Les medias déjà obtenus sont conservés.",
        projet_id,
    )
    return {
        "personnages": len(resultat.personnages),
        "scenes": len(resultat.scenes),
        "avertissements": [a.to_dict() for a in resultat.avertissements],
    }


# --------------------------------------------------------------------------
# Personnages / scenes
# --------------------------------------------------------------------------

CHAMPS_PERSO = {
    "code", "surnom", "etat", "espece", "role", "age", "genre", "voix",
    "etiquette_grok", "lien", "prompt_planche",
}


def _oublier_avertissement_planche(personnage_id: int) -> None:
    """Le prompt manquant vient d'etre ecrit : l'avertissement devient faux.

    Les avertissements sont figes a l'import du pack, et personne ne les
    recalcule quand tu repares une planche a la main. Le laisser en place
    afficherait « MANQUANT » sur une reference desormais generable, et te
    ferait croire que les scenes qui la citent sont encore bloquees.
    """
    perso = db.q1(
        "SELECT projet_id, code FROM personnages WHERE id=?", (personnage_id,)
    )
    if not perso:
        return
    projet = db.q1(
        "SELECT avertissements FROM projets WHERE id=?", (perso["projet_id"],)
    )
    if not projet:
        return
    restants = [
        a
        for a in db.charger_json(projet["avertissements"], [])
        if not (
            str(a.get("cible", "")).split(" ", 1)[0] == perso["code"]
            and "Aucun prompt de planche" in str(a.get("message", ""))
        )
    ]
    db.exec1(
        "UPDATE projets SET avertissements=? WHERE id=?",
        (json.dumps(restants, ensure_ascii=False), perso["projet_id"]),
    )


def maj_personnage(personnage_id: int, champs: dict[str, Any]) -> None:
    retenus = {k: v for k, v in champs.items() if k in CHAMPS_PERSO}
    if not retenus:
        return
    assignations = ", ".join(f"{k}=?" for k in retenus)
    db.exec1(
        f"UPDATE personnages SET {assignations} WHERE id=?",
        (*retenus.values(), personnage_id),
    )
    if (retenus.get("prompt_planche") or "").strip():
        _oublier_avertissement_planche(personnage_id)


CHAMPS_SCENE = {
    "titre", "emoji", "duree", "attache", "inventaire", "ce_qui_change",
    "prompt_image", "prompt_video", "version_video", "trim_debut", "trim_fin",
}


def maj_scene(scene_id: int, champs: dict[str, Any]) -> None:
    retenus = {k: v for k, v in champs.items() if k in CHAMPS_SCENE}
    if "refs_codes" in champs:
        codes = champs["refs_codes"]
        if isinstance(codes, str):
            codes = [c.strip().upper() for c in codes.replace(";", ",").split(",") if c.strip()]
        retenus["refs_codes"] = json.dumps(codes, ensure_ascii=False)
    if not retenus:
        return
    assignations = ", ".join(f"{k}=?" for k in retenus)
    db.exec1(f"UPDATE scenes SET {assignations} WHERE id=?", (*retenus.values(), scene_id))


def problemes_scene(scene: dict, codes_connus: set[str]) -> list[str]:
    soucis: list[str] = []
    if not (scene.get("prompt_image") or "").strip():
        soucis.append("prompt image vide")
    if not (scene.get("prompt_video") or "").strip():
        soucis.append("prompt vidéo vide")
    if scene.get("duree") is None:
        soucis.append(
            "durée introuvable dans le pack (onglet Histoire : dis à l'application "
            "quelle étiquette donne la durée)"
        )
    elif scene.get("duree") not in config.DUREES_GROK:
        soucis.append(f"durée {scene.get('duree')} s non prise en charge par Grok")
    codes = db.charger_json(scene.get("refs_codes"), [])
    if not codes:
        soucis.append("aucun personnage de référence")
    for c in codes:
        if c not in codes_connus:
            soucis.append(f"référence inconnue : {c}")
    return soucis


def valider_scene(scene_id: int, valide: bool = True) -> list[str]:
    scene = db.q1("SELECT * FROM scenes WHERE id=?", (scene_id,))
    if not scene:
        raise ErreurMetier("Scène inconnue.")
    codes_connus = {
        p["code"] for p in db.q(
            "SELECT code FROM personnages WHERE projet_id=?", (scene["projet_id"],)
        )
    }
    soucis = problemes_scene(dict(scene), codes_connus)
    if valide and soucis:
        raise ErreurMetier(
            f"Scène {scene['numero']} non validable : " + " ; ".join(soucis) + "."
        )
    db.exec1("UPDATE scenes SET valide=? WHERE id=?", (1 if valide else 0, scene_id))
    db.journaliser(
        f"Scène {scene['numero']} {'validée' if valide else 'dévalidée'}.",
        scene["projet_id"],
    )
    return soucis


def valider_projet(projet_id: int) -> dict:
    """Valide toutes les scènes validables et passe le projet en « valide »."""
    codes_connus = {
        p["code"] for p in db.q("SELECT code FROM personnages WHERE projet_id=?", (projet_id,))
    }
    validees, refusees = [], []
    for s in db.q("SELECT * FROM scenes WHERE projet_id=? ORDER BY numero", (projet_id,)):
        soucis = problemes_scene(dict(s), codes_connus)
        if soucis:
            refusees.append({"numero": s["numero"], "problemes": soucis})
        else:
            db.exec1("UPDATE scenes SET valide=1 WHERE id=?", (s["id"],))
            validees.append(s["numero"])
    if validees and not refusees:
        db.exec1(
            "UPDATE projets SET valide=1, statut=CASE WHEN statut='brouillon' THEN 'valide'"
            " ELSE statut END, maj_le=? WHERE id=?",
            (db.maintenant(), projet_id),
        )
    db.journaliser(
        f"Validation : {len(validees)} scène(s) validée(s), {len(refusees)} refusée(s).",
        projet_id,
    )
    return {"validees": validees, "refusees": refusees}


def _avertissements_a_jour(projet_id: int, pr: dict) -> list[dict]:
    """Les avertissements de l'import, relus avec l'état actuel du projet.

    Ils sont figés à l'import ; « planche non livrée » cesse d'être un manque dès
    que tu as indiqué que les planches sont déjà dans le chat ChatGPT, ou dès que
    l'image de ce personnage a été importée.
    """
    dans_le_chat = runner.planches_dans_le_chat(projet_id)
    avec_image = {
        p["code"] for p in db.q(
            "SELECT code, ref_path FROM personnages WHERE projet_id=?", (projet_id,)
        ) if fichiers.fichier_utilisable(p["ref_path"])
    }
    sortie = []
    for a in db.charger_json(pr.get("avertissements"), []):
        if str(a.get("message", "")).startswith("Le pack ne livre pas sa planche"):
            code = str(a.get("cible", "")).split(" ", 1)[0]
            if code in avec_image:
                continue
            if dans_le_chat:
                a = {**a, "niveau": "info",
                     "message": "Planche déjà dans le chat ChatGPT de la série : rien à importer."}
        sortie.append(a)
    return sortie


def detail(projet_id: int) -> dict:
    pr = projet(projet_id)
    personnages = []
    for p in db.q("SELECT * FROM personnages WHERE projet_id=? ORDER BY ordre, id", (projet_id,)):
        d = dict(p)
        d["ref_ok"] = fichiers.fichier_utilisable(p["ref_path"])
        personnages.append(d)
    codes_connus = {p["code"] for p in personnages}
    index_perso = {p["code"]: p for p in personnages}

    scenes = []
    for s in db.q("SELECT * FROM scenes WHERE projet_id=? ORDER BY numero", (projet_id,)):
        d = dict(s)
        d["refs_codes"] = db.charger_json(s["refs_codes"], [])
        d["dialogues"] = db.charger_json(s["dialogues"], [])
        d["refs"] = [index_perso.get(c, {"code": c, "surnom": "?", "ref_ok": False})
                     for c in d["refs_codes"]]
        d["image_ok"] = fichiers.fichier_utilisable(s["image_path"])
        d["video_ok"] = fichiers.fichier_utilisable(s["video_path"])
        d["problemes"] = problemes_scene(d, codes_connus)
        d["etat"] = etat_scene(d)
        d["duree_utile"] = max(
            0.0, (s["video_duree"] or 0) - (s["trim_debut"] or 0) - (s["trim_fin"] or 0)
        )
        scenes.append(d)

    taches = [dict(t) for t in db.q(
        "SELECT * FROM taches WHERE projet_id=? ORDER BY id DESC LIMIT 200", (projet_id,)
    )]
    journal = [dict(j) for j in db.q(
        "SELECT * FROM journal WHERE projet_id=? ORDER BY id DESC LIMIT 120", (projet_id,)
    )]
    return {
        "projet": pr,
        "personnages": personnages,
        "scenes": scenes,
        "taches": taches,
        "journal": journal,
        "avertissements": _avertissements_a_jour(projet_id, pr),
        "miniature_ok": fichiers.fichier_utilisable(pr.get("miniature_path")),
        "export_ok": fichiers.fichier_utilisable(pr.get("export_path")),
        "tache_active": dict(runner.tache_active()) if runner.tache_active() else None,
        "duree_totale_pack": sum(s["duree"] or 0 for s in scenes),
    }


def etat_scene(scene: dict) -> str:
    if scene.get("video_ok"):
        return "video_prete"
    if scene.get("image_ok"):
        return "image_prete"
    if scene.get("valide"):
        return "validee"
    return "a_preparer"


# --------------------------------------------------------------------------
# Import manuel de medias
# --------------------------------------------------------------------------

EXT_IMAGE = {".png", ".jpg", ".jpeg", ".webp"}
EXT_VIDEO = {".mp4", ".mov", ".webm", ".mkv"}
EXT_AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}


def importer_media(
    projet_id: int, cible_type: str, cible_id: int, nom_original: str, donnees: bytes
) -> dict:
    """Enregistre un fichier fourni par l'utilisateur sous un nom stable."""
    pr = projet(projet_id)
    if not donnees:
        raise ErreurMetier("Fichier vide.")
    extension = Path(nom_original or "").suffix.lower()
    attendu = runner.chemin_attendu(projet_id, cible_type, cible_id)

    if cible_type == "video":
        if extension not in EXT_VIDEO:
            raise ErreurMetier(
                f"Extension {extension or '(aucune)'} refusée pour une vidéo "
                f"(attendu : {', '.join(sorted(EXT_VIDEO))})."
            )
        cible = attendu.with_suffix(extension)
    else:
        if extension not in EXT_IMAGE:
            raise ErreurMetier(
                f"Extension {extension or '(aucune)'} refusée pour une image "
                f"(attendu : {', '.join(sorted(EXT_IMAGE))})."
            )
        cible = attendu.with_suffix(extension)

    cible.parent.mkdir(parents=True, exist_ok=True)
    fichiers.verifier_appartenance(cible.parent, projet_id, pr["dossier"])
    if fichiers.fichier_utilisable(cible):
        archive = cible.with_name(f"{cible.stem}_remplace{cible.suffix}")
        cible.replace(archive)
        db.journaliser(
            f"Import manuel : ancien fichier conservé sous {archive.name}.", projet_id
        )
    cible.write_bytes(donnees)

    infos = media.analyser_media(cible)
    if not infos.lisible:
        cible.unlink(missing_ok=True)
        raise ErreurMetier(
            f"Fichier refusé : {infos.erreur or 'illisible'}. Rien n'a été enregistré."
        )
    if cible_type == "video" and infos.duree <= 0:
        cible.unlink(missing_ok=True)
        raise ErreurMetier("Vidéo refusée : durée nulle.")

    runner._enregistrer_resultat_existant(projet_id, cible_type, cible_id, cible)
    if cible_type in ("planche", "image", "miniature"):
        colonne = {
            "planche": ("personnages", "ref_origine", cible_id),
            "image": ("scenes", "image_origine", cible_id),
        }.get(cible_type)
        if colonne:
            db.exec1(
                f"UPDATE {colonne[0]} SET {colonne[1]}='importe' WHERE id=?", (colonne[2],)
            )
    elif cible_type == "video":
        db.exec1("UPDATE scenes SET video_origine='importe' WHERE id=?", (cible_id,))

    for t in runner.taches_cible(projet_id, cible_type, cible_id):
        if t["statut"] in ("en_attente", "a_verifier", "echec"):
            db.exec1(
                "UPDATE taches SET statut='termine', fini_le=?, resultat_path=?,"
                " message=? WHERE id=?",
                (db.maintenant(), str(cible), "Résultat importé manuellement.", t["id"]),
            )
    runner.planifier(projet_id)
    db.journaliser(
        f"Import manuel accepté : {cible.name} "
        f"({infos.largeur}x{infos.hauteur}"
        + (f", {infos.duree:.2f} s, audio {'oui' if infos.a_audio else 'non'}" if cible_type == 'video' else "")
        + ").",
        projet_id,
    )
    db.toucher_projet(projet_id)
    return {"chemin": str(cible), "infos": infos.to_dict()}


def importer_musique(projet_id: int, nom_original: str, donnees: bytes) -> str:
    pr = projet(projet_id)
    extension = Path(nom_original or "").suffix.lower()
    if extension not in EXT_AUDIO:
        raise ErreurMetier(
            f"Extension {extension or '(aucune)'} refusée pour une musique "
            f"(attendu : {', '.join(sorted(EXT_AUDIO))})."
        )
    cible = Path(pr["dossier"]) / "musique" / f"musique{extension}"
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_bytes(donnees)
    infos = media.analyser_media(cible)
    if not infos.lisible or not infos.a_audio:
        cible.unlink(missing_ok=True)
        raise ErreurMetier(f"Musique refusée : {infos.erreur or 'aucune piste audio'}.")
    db.exec1("UPDATE projets SET musique_path=? WHERE id=?", (str(cible), projet_id))
    db.journaliser(f"Musique importée ({infos.duree:.1f} s).", projet_id)
    return str(cible)


def retirer_musique(projet_id: int) -> None:
    db.exec1("UPDATE projets SET musique_path=NULL WHERE id=?", (projet_id,))
    db.journaliser("Musique retirée du montage.", projet_id)


# --------------------------------------------------------------------------
# Sous-titres
# --------------------------------------------------------------------------

def sous_titres(projet_id: int) -> list[dict]:
    return [dict(r) for r in db.q(
        "SELECT * FROM sous_titres WHERE projet_id=? ORDER BY ordre, debut", (projet_id,)
    )]


def remplacer_sous_titres(projet_id: int, lignes: list[dict]) -> int:
    db.exec1("DELETE FROM sous_titres WHERE projet_id=?", (projet_id,))
    for i, l in enumerate(lignes):
        texte = (l.get("texte") or "").strip()
        if not texte:
            continue
        db.exec1(
            "INSERT INTO sous_titres (projet_id, ordre, debut, fin, texte)"
            " VALUES (?,?,?,?,?)",
            (projet_id, i, float(l.get("debut") or 0), float(l.get("fin") or 0), texte),
        )
    nombre = len(db.q("SELECT id FROM sous_titres WHERE projet_id=?", (projet_id,)))
    db.journaliser(f"Sous-titres enregistrés : {nombre} ligne(s).", projet_id)
    return nombre


def proposer_sous_titres(projet_id: int) -> int:
    """Timings calcules depuis les dialogues du pack : point de départ editable."""
    d = detail(projet_id)
    scenes = [
        {
            "duree": s["duree"],
            "duree_utile": s["duree_utile"] or s["duree"],
            "dialogues": s["dialogues"],
        }
        for s in d["scenes"]
        if s["video_ok"] or s["valide"]
    ]
    lignes = srt.depuis_scenes(scenes, float(d["projet"].get("vitesse") or 1.0))
    return remplacer_sous_titres(projet_id, lignes)


def exporter_srt(projet_id: int) -> str:
    pr = projet(projet_id)
    lignes = sous_titres(projet_id)
    if not lignes:
        raise ErreurMetier("Aucun sous-titre à exporter.")
    cible = fichiers.chemin_srt(pr["dossier"], pr.get("titre") or pr["nom"])
    srt.ecrire(lignes, cible)
    db.exec1("UPDATE projets SET srt_path=? WHERE id=?", (str(cible), projet_id))
    db.journaliser(f"SRT exporté : {Path(cible).name} ({len(lignes)} lignes).", projet_id)
    return str(cible)


def importer_srt(projet_id: int, contenu: str) -> int:
    lignes = srt.lire(contenu)
    if not lignes:
        raise ErreurMetier("Aucune ligne SRT valide trouvée dans ce fichier.")
    return remplacer_sous_titres(projet_id, lignes)


# --------------------------------------------------------------------------
# Montage / export
# --------------------------------------------------------------------------

def clips_du_projet(projet_id: int) -> list[montage.Clip]:
    clips: list[montage.Clip] = []
    for s in db.q(
        "SELECT * FROM scenes WHERE projet_id=? ORDER BY numero", (projet_id,)
    ):
        if not fichiers.fichier_utilisable(s["video_path"]):
            continue
        clips.append(
            montage.Clip(
                numero=s["numero"],
                chemin=s["video_path"],
                trim_debut=float(s["trim_debut"] or 0),
                trim_fin=float(s["trim_fin"] or 0),
                titre=s["titre"] or "",
            )
        )
    return clips


def reglages_export(projet_id: int, incruster_srt: bool = False) -> montage.ReglagesExport:
    pr = projet(projet_id)
    return montage.ReglagesExport(
        largeur=int(pr.get("export_largeur") or reglages.lire_int("export_largeur")),
        hauteur=int(pr.get("export_hauteur") or reglages.lire_int("export_hauteur")),
        fps=reglages.lire_int("export_fps"),
        crf=reglages.lire_int("export_crf"),
        preset=str(reglages.lire("export_preset")),
        vitesse=float(pr.get("vitesse") or 1.0),
        musique_path=pr.get("musique_path") or None,
        musique_volume=float(
            pr.get("musique_volume") or reglages.lire_float("musique_volume")
        ),
        srt_path=pr.get("srt_path") or None,
        incruster_srt=incruster_srt,
        duree_cible_min=float(
            pr.get("duree_cible_min") or reglages.lire_float("duree_cible_min")
        ),
    )


def plan_montage(projet_id: int, incruster_srt: bool = False) -> dict:
    clips = clips_du_projet(projet_id)
    plan = montage.preparer_plan(clips, reglages_export(projet_id, incruster_srt))
    donnees = plan.to_dict()
    scenes_total = db.q1("SELECT COUNT(*) n FROM scenes WHERE projet_id=?", (projet_id,))["n"]
    donnees["scenes_total"] = scenes_total
    donnees["scenes_sans_video"] = scenes_total - len(clips)
    return donnees


def exporter(projet_id: int, incruster_srt: bool = False) -> dict:
    pr = projet(projet_id)
    clips = clips_du_projet(projet_id)
    if not clips:
        raise ErreurMetier("Aucune vidéo de scène disponible : rien a assembler.")
    reglages = reglages_export(projet_id, incruster_srt)
    if incruster_srt and not reglages.srt_path:
        try:
            reglages.srt_path = exporter_srt(projet_id)
        except ErreurMetier as exc:
            raise ErreurMetier(f"Incrustation impossible : {exc}") from exc
    cible = fichiers.chemin_export(pr["dossier"], pr.get("titre") or pr["nom"])
    db.journaliser(
        f"Export lancé : {len(clips)} clip(s), vitesse {reglages.vitesse:g}, "
        f"{reglages.largeur}x{reglages.hauteur}"
        + (", musique" if reglages.musique_path else "")
        + (", sous-titres incrustes" if incruster_srt else ""),
        projet_id,
    )
    resultat = montage.assembler(clips, cible, reglages)
    if resultat.reussi:
        db.exec1(
            "UPDATE projets SET export_path=?, maj_le=? WHERE id=?",
            (resultat.chemin, db.maintenant(), projet_id),
        )
        db.journaliser(f"Export terminé : {resultat.message}", projet_id)
    else:
        db.journaliser(
            f"Export échoué : {resultat.message} | {resultat.journal[-500:]}",
            projet_id,
            "erreur",
        )
    return {
        "reussi": resultat.reussi,
        "chemin": resultat.chemin,
        "duree": resultat.duree,
        "message": resultat.message,
        "journal": resultat.journal,
    }


def ajuster_duree_scene(projet_id: int, scene_id: int) -> dict:
    """Règle les coupes pour que la scène dure exactement la durée voulue.

    On ne retouche jamais le fichier d'origine : seules les coupes changent, et
    l'export les applique. Une vidéo plus courte que demandé ne peut pas être
    rallongée : on le dit au lieu d'inventer des images.
    """
    scene = db.q1("SELECT * FROM scenes WHERE id=? AND projet_id=?", (scene_id, projet_id))
    if not scene:
        raise ErreurMetier("Scène introuvable dans ce projet.")
    voulue = scene["duree"]
    obtenue = scene["video_duree"] or 0
    if not fichiers.fichier_utilisable(scene["video_path"]) or obtenue <= 0:
        raise ErreurMetier(f"Scène {scene['numero']} : aucune vidéo exploitable.")
    if not voulue:
        raise ErreurMetier(f"Scène {scene['numero']} : aucune durée voulue définie.")

    tolerance = reglages.lire_float("tolerance_duree")
    if obtenue < voulue - tolerance:
        raise ErreurMetier(
            f"Scène {scene['numero']} : la vidéo fait {obtenue:.2f} s, moins que les "
            f"{voulue} s voulues. On ne peut pas la rallonger. Raccourcis la durée de la "
            "scène, ou régénère-la explicitement."
        )

    trim_debut = float(scene["trim_debut"] or 0)
    trim_fin = obtenue - trim_debut - voulue
    if trim_fin < 0:
        trim_debut = 0.0
        trim_fin = max(0.0, obtenue - voulue)
    db.exec1(
        "UPDATE scenes SET trim_debut=?, trim_fin=? WHERE id=?",
        (round(trim_debut, 3), round(trim_fin, 3), scene_id),
    )
    db.journaliser(
        f"Scène {scene['numero']} ajustée à {voulue} s : coupe de "
        f"{trim_debut:.2f} s au début et {trim_fin:.2f} s à la fin "
        f"(vidéo d'origine {obtenue:.2f} s, fichier inchangé).",
        projet_id,
    )
    return {
        "numero": scene["numero"],
        "trim_debut": round(trim_debut, 3),
        "trim_fin": round(trim_fin, 3),
        "duree_obtenue": obtenue,
        "duree_voulue": voulue,
    }


def scenes_non_conformes(projet_id: int) -> list[dict]:
    """Scènes dont la vidéo ne dure pas ce qui était demandé."""
    sortie = []
    for s in db.q(
        "SELECT * FROM scenes WHERE projet_id=? AND video_conforme = 0 ORDER BY numero",
        (projet_id,),
    ):
        utile = max(
            0.0,
            (s["video_duree"] or 0) - (s["trim_debut"] or 0) - (s["trim_fin"] or 0),
        )
        sortie.append(
            {
                "id": s["id"],
                "numero": s["numero"],
                "titre": s["titre"] or "",
                "voulue": s["duree"],
                "obtenue": s["video_duree"],
                "ecart": s["video_ecart"],
                "utile": utile,
                "rattrapee": abs(utile - (s["duree"] or 0))
                <= reglages.lire_float("tolerance_duree"),
            }
        )
    return sortie


def exporter_scene(
    projet_id: int,
    scene_id: int,
    incruster_srt: bool = False,
    avec_musique: bool = False,
) -> dict:
    """Produit le MP4 d'une seule scène, au même format que l'épisode final."""
    pr = projet(projet_id)
    scene = db.q1("SELECT * FROM scenes WHERE id=? AND projet_id=?", (scene_id, projet_id))
    if not scene:
        raise ErreurMetier("Scène introuvable dans ce projet.")
    if not fichiers.fichier_utilisable(scene["video_path"]):
        raise ErreurMetier(
            f"Scène {scene['numero']} : aucune vidéo à exporter. Génère-la ou importe-la "
            "d'abord."
        )
    fichiers.verifier_appartenance(scene["video_path"], projet_id, pr["dossier"])

    reglages_scene = reglages_export(projet_id, incruster_srt=False)
    if not avec_musique:
        reglages_scene.musique_path = None

    if incruster_srt:
        lignes = srt.depuis_scenes(
            [
                {
                    "duree": scene["duree"],
                    "duree_utile": max(
                        0.0,
                        (scene["video_duree"] or 0)
                        - (scene["trim_debut"] or 0)
                        - (scene["trim_fin"] or 0),
                    )
                    or scene["duree"],
                    "dialogues": db.charger_json(scene["dialogues"], []),
                }
            ],
            float(pr.get("vitesse") or 1.0),
        )
        if not lignes:
            raise ErreurMetier(
                f"Scène {scene['numero']} : aucun dialogue, rien à incruster."
            )
        fichier_srt = (
            Path(pr["dossier"]) / "export" / "scenes" / f"scene-{scene['numero']:02d}.srt"
        )
        srt.ecrire(lignes, fichier_srt)
        reglages_scene.srt_path = str(fichier_srt)
        reglages_scene.incruster_srt = True

    clip = montage.Clip(
        numero=scene["numero"],
        chemin=scene["video_path"],
        trim_debut=float(scene["trim_debut"] or 0),
        trim_fin=float(scene["trim_fin"] or 0),
        titre=scene["titre"] or "",
    )
    cible = fichiers.chemin_export_scene(
        pr["dossier"], scene["numero"], scene["titre"] or ""
    )
    db.journaliser(
        f"Export de la scène {scene['numero']} seule"
        + (" avec sous-titres incrustés" if incruster_srt else "")
        + (" et musique" if avec_musique else "")
        + ".",
        projet_id,
    )
    resultat = montage.assembler([clip], cible, reglages_scene)
    if resultat.reussi:
        db.exec1(
            "UPDATE scenes SET export_path=? WHERE id=?", (resultat.chemin, scene_id)
        )
        db.toucher_projet(projet_id)
        db.journaliser(
            f"Scène {scene['numero']} exportée : {Path(resultat.chemin).name} — "
            f"{resultat.message}",
            projet_id,
        )
    else:
        db.journaliser(
            f"Export de la scène {scene['numero']} échoué : {resultat.message}",
            projet_id,
            "erreur",
        )
    return {
        "numero": scene["numero"],
        "titre": scene["titre"] or "",
        "reussi": resultat.reussi,
        "chemin": resultat.chemin,
        "duree": resultat.duree,
        "message": resultat.message,
    }


def exporter_toutes_les_scenes(
    projet_id: int, incruster_srt: bool = False, avec_musique: bool = False
) -> dict:
    """Exporte chaque scène disposant d'une vidéo, une par une."""
    resultats, ignorees = [], []
    for scene in db.q(
        "SELECT * FROM scenes WHERE projet_id=? ORDER BY numero", (projet_id,)
    ):
        if not fichiers.fichier_utilisable(scene["video_path"]):
            ignorees.append(scene["numero"])
            continue
        try:
            resultats.append(
                exporter_scene(projet_id, scene["id"], incruster_srt, avec_musique)
            )
        except ErreurMetier as exc:
            resultats.append(
                {
                    "numero": scene["numero"],
                    "titre": scene["titre"] or "",
                    "reussi": False,
                    "chemin": "",
                    "duree": 0.0,
                    "message": str(exc),
                }
            )
    return {
        "resultats": resultats,
        "reussis": sum(1 for r in resultats if r["reussi"]),
        "echecs": [r for r in resultats if not r["reussi"]],
        "ignorees": ignorees,
    }


def scenes_exportees(projet_id: int) -> list[dict]:
    """Liste des scènes déjà exportées seules, avec leurs caractéristiques."""
    sortie = []
    for scene in db.q(
        "SELECT * FROM scenes WHERE projet_id=? ORDER BY numero", (projet_id,)
    ):
        pret = fichiers.fichier_utilisable(scene["export_path"])
        infos = media.analyser_media(scene["export_path"]).to_dict() if pret else None
        sortie.append(
            {
                "id": scene["id"],
                "numero": scene["numero"],
                "titre": scene["titre"] or "",
                "duree_pack": scene["duree"],
                "a_video": fichiers.fichier_utilisable(scene["video_path"]),
                "exporte": pret,
                "fichier": Path(scene["export_path"]).name if pret else "",
                "infos": infos,
            }
        )
    return sortie


def archiver_scenes(projet_id: int) -> str:
    """Rassemble les scènes exportées dans une archive ZIP téléchargeable."""
    import zipfile

    pr = projet(projet_id)
    exportees = [s for s in scenes_exportees(projet_id) if s["exporte"]]
    if not exportees:
        raise ErreurMetier(
            "Aucune scène exportée pour l'instant. Lance « Exporter toutes les scènes »."
        )
    cible = fichiers.chemin_archive_scenes(pr["dossier"], pr.get("titre") or pr["nom"])
    cible.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(cible, "w", zipfile.ZIP_STORED) as archive:
        for scene in exportees:
            chemin = db.q1(
                "SELECT export_path FROM scenes WHERE id=?", (scene["id"],)
            )["export_path"]
            archive.write(chemin, arcname=Path(chemin).name)
    db.journaliser(
        f"Archive des scènes créée : {cible.name} ({len(exportees)} fichier(s)).",
        projet_id,
    )
    return str(cible)


def controle_final(projet_id: int) -> dict:
    """Recapitulatif avant export definitif."""
    d = detail(projet_id)
    pr = d["projet"]
    manquants: list[str] = []
    for p in d["personnages"]:
        if not p["ref_ok"]:
            manquants.append(f"Planche de référence absente : {p['code']} {p['surnom']}")
    for s in d["scenes"]:
        if not s["image_ok"]:
            manquants.append(f"Scène {s['numero']} : image de départ absente")
        if not s["video_ok"]:
            manquants.append(f"Scène {s['numero']} : vidéo absente")
        elif not s["video_audio"]:
            manquants.append(f"Scène {s['numero']} : vidéo sans audio (dialogues absents ?)")
    for nc in scenes_non_conformes(projet_id):
        if nc["rattrapee"]:
            continue
        manquants.append(
            f"Scène {nc['numero']} : {nc['obtenue']:.2f} s obtenues au lieu de "
            f"{nc['voulue']} s voulues ({nc['ecart']:+.2f} s)"
        )
    if not d["miniature_ok"]:
        manquants.append("Miniature absente")
    plan = plan_montage(projet_id)
    export_infos = (
        media.analyser_media(pr["export_path"]).to_dict()
        if fichiers.fichier_utilisable(pr.get("export_path"))
        else None
    )
    return {
        "projet": pr,
        "scenes": [
            {
                "numero": s["numero"],
                "titre": s["titre"],
                "duree_pack": s["duree"],
                "duree_video": s["video_duree"],
                "duree_utile": s["duree_utile"],
                "audio": bool(s["video_audio"]),
                "etat": s["etat"],
            }
            for s in d["scenes"]
        ],
        "plan": plan,
        "manquants": manquants,
        "miniature_ok": d["miniature_ok"],
        "export": export_infos,
        "duree_totale_pack": d["duree_totale_pack"],
        "sous_titres": len(sous_titres(projet_id)),
    }


# --------------------------------------------------------------------------
# Fonctions complementaires
# --------------------------------------------------------------------------

def prompts_en_serie(projet_id: int, genre: str) -> list[dict]:
    """Mode « prompts image en serie » / « prompts vidéo en serie »."""
    d = detail(projet_id)
    sortie: list[dict] = []
    if genre == "planche":
        for p in d["personnages"]:
            sortie.append({
                "titre": f"{p['code']} — {p['surnom']}",
                "prompt": p["prompt_planche"] or "",
                "pret": p["ref_ok"],
                "cible_type": "planche",
                "cible_id": p["id"],
                "fichiers": [],
            })
        return sortie
    colonne = "prompt_image" if genre == "image" else "prompt_video"
    for s in d["scenes"]:
        joints = (
            [p.get("ref_path") for p in s["refs"] if p.get("ref_ok")]
            if genre == "image"
            else ([s["image_path"]] if s["image_ok"] else [])
        )
        sortie.append({
            "titre": f"Scène {s['numero']} — {s['titre']} ({s['duree']} s)",
            "prompt": s[colonne] or "",
            "pret": s["image_ok"] if genre == "image" else s["video_ok"],
            "cible_type": genre,
            "cible_id": s["id"],
            "duree": s["duree"],
            "fichiers": [Path(c).name for c in joints if c],
        })
    return sortie


def extraire_images_cles(projet_id: int, scene_id: int, nombre: int = 6) -> list[str]:
    s = db.q1("SELECT * FROM scenes WHERE id=? AND projet_id=?", (scene_id, projet_id))
    if not s or not fichiers.fichier_utilisable(s["video_path"]):
        raise ErreurMetier("Aucune vidéo disponible pour cette scène.")
    dossier = Path(projet(projet_id)["dossier"]) / "scenes" / f"cles-scene-{s['numero']:02d}"
    return media.extraire_images_cles(s["video_path"], dossier, nombre)


def preparer_transcription(projet_id: int) -> dict:
    """Extrait l'audio de l'export : base d'une transcription locale ulterieure."""
    pr = projet(projet_id)
    if not fichiers.fichier_utilisable(pr.get("export_path")):
        raise ErreurMetier("Exporte d'abord l'episode : la transcription part de l'export.")
    cible = Path(pr["dossier"]) / "export" / "audio_transcription.wav"
    media.extraire_audio(pr["export_path"], cible)
    moteur = shutil.which("whisper") or shutil.which("whisper-ctranslate2")
    return {
        "audio": str(cible),
        "moteur_local": moteur,
        "message": (
            f"Audio extrait. Moteur local détecté : {moteur}. Lance-le sur ce fichier "
            "puis importe le SRT obtenu."
            if moteur
            else "Audio extrait. Aucun moteur de transcription local installe "
            "(ex. pip install openai-whisper). Pour l'instant, importe un SRT ou "
            "corrige les timings proposes a la main."
        ),
    }


def bibliotheque() -> list[dict]:
    return [dict(r) for r in db.q("SELECT * FROM bibliotheque ORDER BY id DESC")]


def ajouter_reference(url: str, titre: str, notes: str, indicateurs: str) -> int:
    if not (url or "").strip():
        raise ErreurMetier("L'adresse de la vidéo est obligatoire.")
    return db.exec1(
        "INSERT INTO bibliotheque (url, titre, notes, indicateurs, ajoute_le)"
        " VALUES (?,?,?,?,?)",
        (url.strip(), titre.strip(), notes.strip(), indicateurs.strip(), db.maintenant()),
    )


class SuppressionRefusee(Exception):
    """La suppression n'a pas eu lieu, et rien n'a ete touche."""


def supprimer_projet(projet_id: int, confirmation: str) -> dict:
    """Arrete le projet puis efface TOUT : base et fichiers. Irreversible.

    On exige le nom exact du projet. Effacer un projet detruit des heures de
    generation et des quotas deja payes : un clic de trop ne doit pas suffire.
    Une generation en cours est d'abord arretee, sinon le travailleur
    ecrirait dans un dossier qui n'existe plus.
    """
    pr = db.q1("SELECT * FROM projets WHERE id=?", (projet_id,))
    if not pr:
        raise SuppressionRefusee("Ce projet n'existe pas (ou plus).")
    attendu = (pr["titre"] or pr["nom"] or "").strip()
    if (confirmation or "").strip() != attendu:
        raise SuppressionRefusee(
            f"Rien n'a été supprimé. Pour confirmer, recopie exactement « {attendu} »."
        )

    runner.arreter_projet(projet_id)
    en_cours = db.q(
        "SELECT id FROM taches WHERE projet_id=? AND statut IN ('en_cours','en_attente')",
        (projet_id,),
    )
    for ligne in en_cours:
        db.exec1(
            "UPDATE taches SET statut='annule', fini_le=?, message=? WHERE id=?",
            (db.maintenant(), "Projet supprimé par toi.", ligne["id"]),
        )

    dossier = Path(pr["dossier"]) if pr["dossier"] else None
    compte = {
        t: db.q1(f"SELECT COUNT(*) n FROM {t} WHERE projet_id=?", (projet_id,))["n"]
        for t in ("personnages", "scenes", "taches", "sous_titres")
    }
    for table in ("sous_titres", "scenes", "personnages", "taches", "journal"):
        db.exec1(f"DELETE FROM {table} WHERE projet_id=?", (projet_id,))
    db.exec1("DELETE FROM projets WHERE id=?", (projet_id,))

    fichiers_effaces = False
    if dossier and dossier.exists():
        # Garde-fou anti-degat : on n'efface qu'un dossier situe DANS le
        # dossier des projets, jamais un chemin arbitraire venu de la base.
        racine = Path(config.DOSSIER_PROJETS).resolve()
        cible = dossier.resolve()
        if cible != racine and racine in cible.parents:
            shutil.rmtree(cible, ignore_errors=True)
            fichiers_effaces = not cible.exists()
    return {
        "titre": attendu,
        "dossier": str(dossier) if dossier else "",
        "fichiers_effaces": fichiers_effaces,
        "compte": compte,
    }


def supprimer_reference(ref_id: int) -> None:
    db.exec1("DELETE FROM bibliotheque WHERE id=?", (ref_id,))


# --------------------------------------------------------------------------
# Metaprompt
# --------------------------------------------------------------------------

def chemin_metaprompt(type_histoire: str | None = None) -> Path:
    """Le fichier du metaprompt de cette famille.

    Les fruits gardent leur ancien nom de fichier : le metaprompt deja
    enregistre reste en place, rien n'est a recoller.
    """
    type_histoire = type_histoire_valide(type_histoire)
    if type_histoire == config.TYPE_PROJET_DEFAUT:
        return config.DOSSIER_DONNEES / "metaprompt_v6.md"
    return config.DOSSIER_DONNEES / f"metaprompt_{type_histoire}.md"


def enregistrer_metaprompt(texte: str, type_histoire: str | None = None) -> dict:
    if not (texte or "").strip():
        raise ErreurMetier("Texte vide.")
    type_histoire = type_histoire_valide(type_histoire)
    config.preparer_dossiers()
    cible = chemin_metaprompt(type_histoire)
    cible.write_text(texte, encoding="utf-8")
    from . import ressources
    empreinte = ressources.empreinte_texte(texte)
    db.definir_reglage(f"metaprompt_empreinte_{type_histoire}", empreinte)
    db.definir_reglage(f"metaprompt_taille_{type_histoire}", str(len(texte)))
    if type_histoire == config.TYPE_PROJET_DEFAUT:
        db.definir_reglage("metaprompt_empreinte", empreinte)
        db.definir_reglage("metaprompt_taille", str(len(texte)))
    db.journaliser(
        f"Métaprompt « {config.TYPES_PROJET[type_histoire]} » enregistré tel quel "
        f"({len(texte)} caractères, sha {empreinte})."
    )
    return {
        "empreinte": empreinte,
        "taille": len(texte),
        "chemin": str(cible),
        "type_histoire": type_histoire,
    }


def lire_metaprompt(type_histoire: str | None = None) -> dict:
    """Le metaprompt de cette famille : le tien s'il existe, sinon celui livre."""
    from . import ressources

    type_histoire = type_histoire_valide(type_histoire)
    cible = chemin_metaprompt(type_histoire)
    texte, origine = "", "aucun"
    if cible.exists():
        texte, origine = cible.read_text(encoding="utf-8"), "le tien"
    else:
        livre = ressources.metaprompt_livre(type_histoire)
        if livre:
            texte, origine = livre, "livré avec l'application"
    if not texte:
        return {
            "present": False, "texte": "", "taille": 0, "empreinte": "",
            "type_histoire": type_histoire, "origine": origine,
            "libelle": config.TYPES_PROJET[type_histoire],
        }
    return {
        "present": True,
        "texte": texte,
        "taille": len(texte),
        "empreinte": ressources.empreinte_texte(texte),
        "type_detecte": parser_v6.detecter_type(texte),
        "type_histoire": type_histoire,
        "origine": origine,
        "libelle": config.TYPES_PROJET[type_histoire],
    }


def tous_les_metaprompts() -> list[dict]:
    return [lire_metaprompt(cle) for cle in config.TYPES_PROJET]


# --------------------------------------------------------------------------
# Fil de production : la vue simple, de haut en bas
# --------------------------------------------------------------------------

def url_media(projet: dict, chemin: str | None) -> str:
    """Adresse de lecture d'un fichier du projet, ou chaîne vide."""
    if not fichiers.fichier_utilisable(chemin):
        return ""
    try:
        relatif = Path(chemin).resolve().relative_to(Path(projet["dossier"]).resolve())
    except (ValueError, OSError):
        return ""
    return f"/media/{projet['id']}/{relatif.as_posix()}"


def _etat_element(
    projet_id: int,
    genre: str,
    cible_id: int,
    media_pret: bool,
    actives: dict[str, dict],
) -> dict:
    """État affichable d'un élément, sans rien lancer ni supposer.

    L'ordre compte : un fichier déjà obtenu prime, puis la tâche en cours, puis
    la dernière tâche finie, et enfin les contrôles — qui disent pourquoi un
    élément ne peut pas partir sans consommer le moindre quota.
    """
    taches = list(runner.taches_cible(projet_id, genre, cible_id))
    derniere = taches[-1] if taches else None
    en_cours = actives.get(f"{genre}-{cible_id}")
    if en_cours is not None:
        return {"etat": "en_cours", "message": "", "tache_id": en_cours["id"]}
    en_file = next((t for t in taches if t["statut"] == "en_attente"), None)
    if en_file is not None:
        return {"etat": "en_attente", "message": "", "tache_id": en_file["id"]}
    if media_pret:
        return {
            "etat": "termine",
            "message": "",
            "tache_id": derniere["id"] if derniere else 0,
        }
    if derniere is not None and derniere["statut"] in ("a_verifier", "echec"):
        return {
            "etat": derniere["statut"],
            "message": derniere["message"] or "",
            "tache_id": derniere["id"],
        }
    try:
        runner.controles_avant_lancement(projet_id, genre, cible_id)
    except runner.GenerationRefusee as exc:
        return {"etat": "bloque", "message": str(exc), "tache_id": 0}
    return {"etat": "a_faire", "message": "", "tache_id": 0}


def fil_production(projet_id: int) -> dict:
    """Tout ce que la page Atelier affiche : les planches, puis les scènes.

    Chaque élément porte sa clé stable (« planche-19 », « video-5 »), son état,
    son fichier s'il existe et le motif quand il ne peut pas partir.
    """
    pr = projet(projet_id)
    # Deux générations peuvent tourner en même temps : une par fournisseur.
    actives = {
        f"{t['cible_type']}-{t['cible_id']}": dict(t) for t in runner.taches_actives()
    }

    planches = []
    for p in db.q(
        "SELECT * FROM personnages WHERE projet_id=? ORDER BY ordre, id", (projet_id,)
    ):
        pret = fichiers.fichier_utilisable(p["ref_path"])
        etat = _etat_element(projet_id, "planche", p["id"], pret, actives)
        planches.append(
            {
                "cle": f"planche-{p['id']}",
                "genre": "planche",
                "id": p["id"],
                "fournisseur": "chatgpt",
                "titre": f"{p['code']} {p['surnom']}".strip(),
                "detail": p["etat"] or "",
                "media": url_media(pr, p["ref_path"]),
                **etat,
            }
        )

    scenes = []
    for s in db.q(
        "SELECT * FROM scenes WHERE projet_id=? ORDER BY numero", (projet_id,)
    ):
        image_prete = fichiers.fichier_utilisable(s["image_path"])
        video_prete = fichiers.fichier_utilisable(s["video_path"])
        image = {
            "cle": f"image-{s['id']}",
            "genre": "image",
            "id": s["id"],
            "fournisseur": "chatgpt",
            "titre": f"Scène {s['numero']} — image de départ",
            "detail": "",
            "media": url_media(pr, s["image_path"]),
            **_etat_element(projet_id, "image", s["id"], image_prete, actives),
        }
        video = {
            "cle": f"video-{s['id']}",
            "genre": "video",
            "id": s["id"],
            "fournisseur": "grok",
            "titre": f"Scène {s['numero']} — vidéo animée",
            "detail": "",
            "media": url_media(pr, s["video_path"]),
            "telechargement": (
                f"/projets/{projet_id}/telecharger/scene/{s['id']}" if video_prete else ""
            ),
            **_etat_element(projet_id, "video", s["id"], video_prete, actives),
        }
        scenes.append(
            {
                "id": s["id"],
                "numero": s["numero"],
                "titre": s["titre"] or "",
                "duree": s["duree"] or 0,
                "refs": db.charger_json(s["refs_codes"], []),
                "valide": bool(s["valide"]),
                "image": image,
                "video": video,
            }
        )

    production = runner.etat_production(projet_id)
    journal = [
        {
            "horodatage": (j["horodatage"] or "")[11:19],
            "niveau": j["niveau"],
            "message": j["message"],
        }
        for j in db.q(
            "SELECT * FROM journal WHERE projet_id=? ORDER BY id DESC LIMIT 25",
            (projet_id,),
        )
    ]
    miniature_prete = fichiers.fichier_utilisable(pr.get("miniature_path"))
    miniature = {
        "cle": "miniature-0",
        "genre": "miniature",
        "id": 0,
        "fournisseur": "chatgpt",
        "titre": "Miniature TikTok",
        "detail": "9:16 · dernière étape",
        "media": url_media(pr, pr.get("miniature_path")),
        "telechargement": (
            f"/projets/{projet_id}/telecharger/miniature" if miniature_prete else ""
        ),
        **_etat_element(projet_id, "miniature", 0, miniature_prete, actives),
    }

    from .adapters import navigateur as nav

    chromes = nav.chromes_videroo()
    pretes = [s for s in scenes if s["video"]["etat"] == "termine"]
    return {
        "navigateur": {
            "chromes": chromes,
            "memoire_mo": sum(c["memoire_mo"] for c in chromes),
            "sur_ton_ecran": [c for c in chromes if c["sur_ton_ecran"]],
        },
        "projet": {"id": pr["id"], "titre": pr.get("titre") or pr["nom"], "statut": pr["statut"]},
        "planches": planches,
        "scenes": scenes,
        "miniature": miniature,
        "production": production,
        "comptes": {
            s: comptes.etat_rotation(s, projet(projet_id).get("comptes_de"))
            for s in comptes.SITES
        },
        "actives": [
            {
                "cle": cle,
                "libelle": t["libelle"],
                "fournisseur": t["fournisseur"],
                "mode": t["mode"],
                "lance_le": t["lance_le"],
            }
            for cle, t in actives.items()
        ],
        "journal": journal,
        "videos_pretes": len(pretes),
    }


def nom_video_scene(projet: dict, scene: dict) -> str:
    """Nom de fichier lisible pour un téléchargement vers CapCut."""
    base = fichiers.slug(projet.get("titre") or projet["nom"], "episode")
    morceaux = [base, f"scene-{scene['numero']:02d}"]
    titre = fichiers.slug(scene["titre"] or "", "")
    if titre:
        morceaux.append(titre)
    suffixe = Path(scene["video_path"]).suffix or ".mp4"
    return "-".join(morceaux) + suffixe


def nom_miniature(projet: dict) -> str:
    """Nom de fichier lisible pour la miniature qu'on dépose sur TikTok."""
    base = fichiers.slug(projet.get("titre") or projet["nom"], "episode")
    episode = projet.get("grok_episode") or 1
    suffixe = Path(projet["miniature_path"]).suffix or ".png"
    return f"{base}-miniature-partie-{int(episode):02d}{suffixe}"


def miniature_a_telecharger(projet_id: int) -> tuple[Path, str]:
    """Chemin réel et nom proposé pour la miniature TikTok."""
    pr = projet(projet_id)
    if not fichiers.fichier_utilisable(pr.get("miniature_path")):
        raise ErreurMetier(
            "Aucune miniature à télécharger pour l'instant : elle se génère en dernier."
        )
    chemin = fichiers.verifier_appartenance(pr["miniature_path"], projet_id, pr["dossier"])
    return chemin, nom_miniature(pr)


def video_a_telecharger(projet_id: int, scene_id: int) -> tuple[Path, str]:
    """Chemin réel et nom proposé pour la vidéo d'une scène."""
    pr = projet(projet_id)
    scene = db.q1("SELECT * FROM scenes WHERE id=? AND projet_id=?", (scene_id, projet_id))
    if not scene:
        raise ErreurMetier("Scène introuvable dans ce projet.")
    if not fichiers.fichier_utilisable(scene["video_path"]):
        raise ErreurMetier(
            f"Scène {scene['numero']} : aucune vidéo à télécharger pour l'instant."
        )
    chemin = fichiers.verifier_appartenance(scene["video_path"], projet_id, pr["dossier"])
    return chemin, nom_video_scene(pr, dict(scene))


def archiver_videos(projet_id: int) -> tuple[Path, str]:
    """Rassemble les vidéos obtenues dans une archive ZIP, sans ré-encodage.

    Ce sont les fichiers bruts de Grok : c'est ce qui part dans CapCut.
    """
    import zipfile

    pr = projet(projet_id)
    scenes = [
        dict(s)
        for s in db.q("SELECT * FROM scenes WHERE projet_id=? ORDER BY numero", (projet_id,))
        if fichiers.fichier_utilisable(s["video_path"])
    ]
    if not scenes:
        raise ErreurMetier(
            "Aucune vidéo à télécharger pour l'instant : lance la génération d'abord."
        )
    cible = Path(pr["dossier"]) / "export" / f"{fichiers.slug(pr.get('titre') or pr['nom'], 'episode')}-videos.zip"
    cible.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(cible, "w", zipfile.ZIP_STORED) as archive:
        for scene in scenes:
            chemin = fichiers.verifier_appartenance(
                scene["video_path"], projet_id, pr["dossier"]
            )
            archive.write(chemin, arcname=nom_video_scene(pr, scene))
    db.journaliser(
        f"Archive des vidéos créée : {cible.name} ({len(scenes)} vidéo(s)).", projet_id
    )
    return cible, cible.name


# --------------------------------------------------------------------------
# Glisser-déposer des images (planches et miniature)
# --------------------------------------------------------------------------

def deposer_image(projet_id: int, nom_fichier: str, donnees: bytes, cible: str) -> dict:
    """Range une image déposée sur SA case : « planche:<id> » ou « miniature:0 ».

    C'est toi qui choisis la case en y déposant l'image : rien n'est deviné,
    rien n'est généré. Rend {ok, fichier, message}.
    """
    resultat = {"ok": False, "fichier": nom_fichier, "message": ""}
    genre, _, valeur = (cible or "").partition(":")
    if genre == "planche" and valeur.isdigit():
        p = db.q1("SELECT code, surnom FROM personnages WHERE id=? AND projet_id=?",
                  (int(valeur), projet_id))
        if p is None:
            resultat["message"] = "Ce personnage n'appartient pas à ce projet."
            return resultat
        libelle = f"planche {p['code']} {p['surnom']}"
    elif genre == "miniature":
        valeur, libelle = "0", "miniature"
    else:
        resultat["message"] = "Case de dépôt inconnue."
        return resultat
    try:
        importer_media(projet_id, genre, int(valeur), nom_fichier, donnees)
    except (ErreurMetier, fichiers.MelangeDeProjets) as exc:
        resultat["message"] = str(exc)
        return resultat
    resultat.update(ok=True, message=f"Image rangée : {libelle}.")
    return resultat
