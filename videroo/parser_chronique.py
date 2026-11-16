"""Lecteur du pack « chronique » : un format d'étiquettes, pas de blocs.

Le métaprompt chronique (section 10) ne livre pas des blocs titrés comme le V6,
mais une étiquette par ligne, le prompt juste derrière :

    RÉFÉRENCE KENZA : <la planche, sur une seule ligne>
    IMAGE (Kenza) : <l'image de départ de la scène>
    VIDÉO (dynamique · 10 s · Kenza @image2) : <l'animation>

Le reste de la chaîne ne change pas : on remplit exactement les mêmes
structures que le lecteur V6, donc l'atelier, le runner et l'export n'ont rien
à savoir du type d'histoire. Comme ailleurs, on n'invente jamais un champ
absent : il reste vide et il est signalé.
"""
from __future__ import annotations

import re

from .parser_v6 import ResultatImport

# Les accents sautent souvent d'un copier-coller a l'autre : on accepte les deux.
RE_REFERENCE = re.compile(
    r"^\s*(?:\*\*)?\s*R[ÉE]F[ÉE]RENCE\s+([^\s:][^:]*?)\s*(?:\*\*)?\s*:\s*(.*)$",
    re.IGNORECASE,
)
RE_IMAGE = re.compile(
    r"^\s*(?:\*\*)?\s*IMAGE(?:\s+SC[ÈE]NE\s+(\d+))?\s*\(([^)]*)\)\s*(?:\*\*)?\s*:\s*(.*)$",
    re.IGNORECASE,
)
RE_VIDEO = re.compile(
    r"^\s*(?:\*\*)?\s*VID[ÉE]O(?:\s+SC[ÈE]NE\s+(\d+))?\s*\(([^)]*)\)\s*(?:\*\*)?\s*:\s*(.*)$",
    re.IGNORECASE,
)
RE_ENTETE = re.compile(
    r"^\s*[^\w\s]*\s*(.+?)\s*[—–-]\s*[ÉE]PISODE\s+(\d+)"
    r"\s*(?:[:·–—-]\s*(.+?))?\s*$",
    re.IGNORECASE,
)
#: « PARTIE 1/7 — LE GARÇON À LA FENÊTRE » : l'autre façon d'ouvrir un pack.
RE_PARTIE = re.compile(
    r"^\s*[^\w\s]*\s*PARTIE\s+(\d+)(?:\s*/\s*\d+)?\s*[—–:-]\s*(.+?)\s*$",
    re.IGNORECASE,
)
#: « SCÈNE 3 » seul sur sa ligne : un intertitre, pas du texte de prompt.
RE_SCENE_SEULE = re.compile(r"^\s*(?:\*\*)?\s*SC[ÈE]NE\s+\d+\s*(?:\*\*)?\s*:?\s*$", re.IGNORECASE)
RE_DUREE = re.compile(
    r"Dur[ée]e\s*:\s*(\d+(?:[.,]\d+)?)\s*s(?:.*?(\d+)\s*sc[èe]nes?)?", re.IGNORECASE
)
RE_TITRE_MINIATURE = re.compile(
    r"^\s*[^\w\s]*\s*TITRE\s+MINIATURE\s*:\s*(.+)$", re.IGNORECASE
)
RE_MONTAGE = re.compile(r"^\s*[^\w\s]*\s*MONTAGE\s*:\s*(.+)$", re.IGNORECASE)
RE_LEGENDE = re.compile(r"^\s*[^\w\s]*\s*L[ÉE]GENDE\s*:\s*(.+)$", re.IGNORECASE)
RE_CASTING = re.compile(r"^\s*Casting\s*:\s*(.+)$", re.IGNORECASE)
RE_HISTOIRE = re.compile(r"^\s*L'?histoire\s*:\s*(.+)$", re.IGNORECASE)

#: « dynamique · 10 s · Kenza @image2, Ilyes @image3 »
RE_DUREE_DANS_ETIQUETTE = re.compile(r"(\d+)\s*s\b", re.IGNORECASE)
RE_NOM_TAGUE = re.compile(r"([^\s,;·@]+(?:\s+[^\s,;·@]+)*?)\s*@image\d+", re.IGNORECASE)

TYPES_DE_SCENE = (
    "action", "voix intérieure", "voix interieure", "dialogue", "dynamique",
    "émotion", "emotion", "écran", "ecran",
)

#: Ce qu'on écrit dans la case « aucun personnage visible ».
AUCUN = {"aucun", "aucune", "aucun personnage", "none", "-", ""}


def _est_une_etiquette(ligne: str) -> bool:
    """Une ligne qui ouvre un autre champ, et ne peut donc pas continuer un prompt."""
    return any(r.match(ligne) for r in (
        RE_REFERENCE, RE_IMAGE, RE_VIDEO, RE_SCENE_SEULE, RE_TITRE_MINIATURE,
        RE_MONTAGE, RE_LEGENDE, RE_PARTIE, RE_ENTETE,
    ))


def detecter(texte: str) -> bool:
    """Vrai si ce document est un pack chronique, à ses étiquettes."""
    lignes = (texte or "").splitlines()
    a_image = any(RE_IMAGE.match(l) for l in lignes)
    a_video = any(RE_VIDEO.match(l) for l in lignes)
    return a_image and a_video


def _code(rang: int) -> str:
    return f"R{rang}"


def _est_un_champ(morceau: str) -> bool:
    """Un type de scene ou une duree n'est jamais un prenom."""
    net = morceau.strip().lower()
    return (
        not net
        or net in AUCUN
        or net in TYPES_DE_SCENE
        or bool(RE_DUREE_DANS_ETIQUETTE.fullmatch(net))
    )


def _noms_de(entre_parentheses: str) -> list[str]:
    """Les prénoms cités, qu'ils portent un tag @image ou non."""
    brut = (entre_parentheses or "").strip()
    if brut.lower() in AUCUN:
        return []
    tagues = [n.strip() for n in RE_NOM_TAGUE.findall(brut)]
    tagues = [n for n in tagues if n and not _est_un_champ(n)]
    if tagues:
        return tagues
    noms = []
    for morceau in re.split(r"[,;·]", brut):
        morceau = re.sub(r"@image\d+", "", morceau, flags=re.IGNORECASE).strip()
        if not morceau or morceau.lower() in AUCUN:
            continue
        if _est_un_champ(morceau):
            continue
        noms.append(morceau)
    return noms


def _type_et_duree(entre_parentheses: str) -> tuple[str, int | None]:
    morceaux = [m.strip() for m in (entre_parentheses or "").split("·")]
    type_scene, duree = "", None
    for m in morceaux:
        if not type_scene and m.lower() in TYPES_DE_SCENE:
            type_scene = m.lower()
        trouve = RE_DUREE_DANS_ETIQUETTE.fullmatch(m)
        if duree is None and trouve:
            duree = int(trouve.group(1))
    if duree is None:
        trouve = RE_DUREE_DANS_ETIQUETTE.search(entre_parentheses or "")
        if trouve:
            duree = int(trouve.group(1))
    return type_scene, duree


def analyser(texte: str, connus: list[dict] | None = None) -> ResultatImport:
    """Lit un pack chronique et remplit les mêmes structures que le V6."""
    res = ResultatImport(brut=texte or "")
    if not detecter(texte):
        res.avertir(
            "manquant",
            "document",
            "Aucune étiquette « IMAGE (…) : » et « VIDÉO (…) : » trouvée. "
            "Vérifie que tu as collé le pack livré par le métaprompt chronique, "
            "section 10, et pas la conversation entière.",
        )
        return res

    # Un copier-coller emporte parfois l'épisode précédent avec le nouveau : deux
    # en-têtes « … — ÉPISODE N ». Mélanger les deux doublerait scènes et durée.
    # On ne garde que le dernier épisode du document, et on le dit.
    lignes_doc = (texte or "").splitlines()
    entetes = [
        (i, m) for i, l in enumerate(lignes_doc)
        for m in [RE_ENTETE.match(l) or RE_PARTIE.match(l)] if m
    ]
    if len(entetes) > 1:
        i_dernier, m_dernier = entetes[-1]
        numeros = [m.group(2) if m.re is RE_ENTETE else m.group(1) for _, m in entetes]
        texte = "\n".join(lignes_doc[i_dernier:])
        res.brut = texte
        res.avertir(
            "info", "document",
            f"Le texte collé contient {len(entetes)} épisodes ({', '.join(numeros)}) : "
            f"seul le dernier (épisode {numeros[-1]}) a été lu. Si ce n'est pas le bon, "
            "recolle uniquement l'épisode voulu.",
        )

    res.type_document = "pack"
    res.univers = "chronique"
    par_nom: dict[str, str] = {}
    # Personnages déjà connus de la série (partie 1) : une partie 2 ne relivre
    # pas forcément leurs RÉFÉRENCE, mais ses IMAGE les citent toujours.
    for p in connus or []:
        res.personnages.append(dict(p))
        par_nom[p["surnom"].casefold()] = p["code"]
    scene_en_cours: dict | None = None
    numero = 0

    def cloturer() -> None:
        nonlocal scene_en_cours
        if scene_en_cours is not None:
            res.scenes.append(scene_en_cours)
            scene_en_cours = None

    # Certains packs mettent l'étiquette seule sur sa ligne et le texte juste
    # en dessous (« IMAGE (MAYA, KAIRO) : » puis le prompt). Tant que le champ
    # ouvert est vide, ou que le texte continue sans ligne blanche, il le reçoit.
    ouvert: tuple[dict, str] | None = None

    for ligne in (texte or "").splitlines():
        if ouvert is not None:
            cible, champ = ouvert
            if _est_une_etiquette(ligne):
                ouvert = None
            elif not ligne.strip():
                if cible[champ]:
                    ouvert = None
                continue
            else:
                cible[champ] = f"{cible[champ]} {ligne.strip()}".strip()
                continue

        if RE_SCENE_SEULE.match(ligne):
            continue

        m = RE_REFERENCE.match(ligne)
        if m:
            nom = m.group(1).strip()
            prompt = m.group(2).strip()
            deja = par_nom.get(nom.casefold())
            if deja:
                # Le même personnage cité deux fois (pack qui répète ses références) :
                # un seul personnage, donc une seule planche à payer.
                perso = next(p for p in res.personnages if p["code"] == deja)
                if not perso["prompt_planche"] and prompt:
                    perso["prompt_planche"] = prompt
                elif not perso["prompt_planche"]:
                    ouvert = (perso, "prompt_planche")
                continue
            code = _code(len(res.personnages) + 1)
            par_nom[nom.casefold()] = code
            res.personnages.append({
                "code": code,
                "surnom": nom.upper(),
                "etat": "UNIQUE",
                "espece": "",
                "role": "",
                "age": "",
                "genre": "",
                "voix": "",
                "etiquette_grok": "",
                "lien": "",
                "prompt_planche": prompt,
            })
            if not prompt:
                ouvert = (res.personnages[-1], "prompt_planche")
            continue

        m = RE_IMAGE.match(ligne)
        if m:
            cloturer()
            impose, dedans, prompt = m.group(1), m.group(2), m.group(3).strip()
            numero = int(impose) if impose else numero + 1
            noms = _noms_de(dedans)
            codes = []
            for nom in noms:
                code = par_nom.get(nom.casefold())
                if code:
                    codes.append(code)
                else:
                    res.avertir(
                        "suspect", f"Scène {numero}",
                        f"« {nom} » est cité dans l'IMAGE mais n'a pas de ligne "
                        "RÉFÉRENCE : sa planche ne sera pas jointe.",
                    )
            scene_en_cours = {
                "numero": numero,
                "titre": "",
                "emoji": "",
                "duree": None,
                "attache": "",
                "inventaire": "",
                "ce_qui_change": "",
                "refs_codes": codes,
                "refs_video": [],
                "refs_brut": dedans.strip(),
                "prompt_image": prompt,
                "prompt_video": "",
                "version_video": "",
                "dialogues": [],
                "nb_repliques": 0,
                "nb_mots": 0,
            }
            if not prompt:
                ouvert = (scene_en_cours, "prompt_image")
            continue

        m = RE_VIDEO.match(ligne)
        if m:
            impose, dedans, prompt = m.group(1), m.group(2), m.group(3).strip()
            type_scene, duree = _type_et_duree(dedans)
            if scene_en_cours is None:
                numero = int(impose) if impose else numero + 1
                scene_en_cours = {
                    "numero": numero, "titre": "", "emoji": "", "duree": None,
                    "attache": "", "inventaire": "", "ce_qui_change": "",
                    "refs_codes": [], "refs_video": [], "refs_brut": "",
                    "prompt_image": "",
                    "prompt_video": "", "version_video": "", "dialogues": [],
                    "nb_repliques": 0, "nb_mots": 0,
                }
                res.avertir(
                    "manquant", f"Scène {numero}",
                    "VIDÉO sans IMAGE au-dessus : cette scène n'a pas d'image "
                    "de départ à animer.",
                )
            scene_en_cours["prompt_video"] = prompt
            scene_en_cours["duree"] = duree
            scene_en_cours["titre"] = (type_scene or "scène").capitalize()
            scene_en_cours["version_video"] = type_scene
            scene_en_cours["nb_mots"] = len(prompt.split())
            # L'ordre compte : le prompt dit « @image2 is KENZA, @image3 is
            # ILYES ». Grok numerote les pieces jointes dans l'ordre ou on les
            # depose, @image1 etant l'image de depart. Si on joint les planches
            # dans un autre ordre, @image3 designe le mauvais visage.
            for nom in _noms_de(dedans):
                code = par_nom.get(nom.casefold())
                if not code:
                    continue
                if code not in scene_en_cours["refs_codes"]:
                    scene_en_cours["refs_codes"].append(code)
                if code not in scene_en_cours["refs_video"]:
                    scene_en_cours["refs_video"].append(code)
            if duree is None:
                res.avertir(
                    "manquant", f"Scène {scene_en_cours['numero']}",
                    "Durée absente de l'étiquette VIDÉO : Grok ne saura pas "
                    "combien de secondes produire.",
                )
            if not prompt:
                ouvert = (scene_en_cours, "prompt_video")
            cloturer()
            continue

        m = RE_PARTIE.match(ligne)
        if m and not res.titre:
            res.titre = m.group(2).strip()
            res.parties = m.group(1)
            continue

        m = RE_ENTETE.match(ligne)
        if m and not res.titre:
            res.titre = m.group(1).strip()
            res.parties = m.group(2)
            continue

        m = RE_TITRE_MINIATURE.match(ligne)
        if m:
            res.titre_miniature = m.group(1).strip()
            continue

        m = RE_MONTAGE.match(ligne)
        if m:
            res.menace_evolutive = m.group(1).strip()
            continue

        m = RE_LEGENDE.match(ligne)
        if m:
            res.prix_final = m.group(1).strip()
            continue

        m = RE_HISTOIRE.match(ligne)
        if m and not res.une_phrase:
            res.une_phrase = m.group(1).strip()
            continue

        m = RE_CASTING.match(ligne)
        if m and not res.moteur:
            res.moteur = m.group(1).strip()
            continue

        trouve = RE_DUREE.search(ligne)
        if trouve and res.duree_annoncee is None:
            res.duree_annoncee = float(trouve.group(1).replace(",", "."))
            if trouve.group(2):
                res.nb_scenes_annonce = int(trouve.group(2))

    cloturer()

    if not res.personnages:
        res.avertir(
            "manquant", "personnages",
            "Aucune ligne « RÉFÉRENCE <PRÉNOM> : … » trouvée : sans planche, "
            "les visages changeront d'un plan à l'autre.",
        )
    if not res.scenes:
        res.avertir(
            "manquant", "scenes",
            "Aucune scène lue. Chaque scène s'écrit en deux lignes : "
            "« IMAGE (…) : … » puis « VIDÉO (type · durée · …) : … ».",
        )
    # Les vides se jugent à la fin : le texte a pu arriver sur les lignes suivantes.
    for p in res.personnages:
        if not p["prompt_planche"]:
            res.avertir(
                "manquant", f"RÉFÉRENCE {p['surnom']}",
                "Planche vide : rien après les deux-points, ni sur les lignes suivantes.",
            )
    for s in res.scenes:
        s["nb_mots"] = len(s["prompt_video"].split())
        if not s["prompt_image"]:
            res.avertir("manquant", f"Scène {s['numero']}", "Prompt d'image vide.")
        if s["prompt_video"]:
            continue
        if s["version_video"] or s["duree"] is not None:
            res.avertir("manquant", f"Scène {s['numero']}", "Prompt de vidéo vide.")
        else:
            res.avertir(
                "manquant", f"Scène {s['numero']}",
                "Image sans VIDÉO en dessous : rien à animer pour cette scène.",
            )
    if res.duree_annoncee and res.scenes:
        ecart = abs(res.duree_calculee - res.duree_annoncee)
        if ecart > 1:
            res.avertir(
                "suspect", "duree",
                f"Somme des scènes = {res.duree_calculee:g} s, en-tête annonce "
                f"{res.duree_annoncee:g} s.",
            )
    if res.nb_scenes_annonce and len(res.scenes) != res.nb_scenes_annonce:
        res.avertir(
            "suspect", "scenes",
            f"{len(res.scenes)} scène(s) lue(s), en-tête annonce "
            f"{res.nb_scenes_annonce}.",
        )
    return res
