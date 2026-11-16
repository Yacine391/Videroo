"""Lecteur qui apprend : la couche commune posée après les trois lecteurs.

Les packs changent de forme d'un épisode à l'autre (« 🎬 PROMPT GROK — … »,
« ⏱️ Grok — 6 secondes », « 🧾 Image de départ — … »). Jusqu'ici chaque variante
demandait une modification du code. Désormais :

* une étiquette inconnue dans une scène incomplète est NOMMÉE à l'import, avec
  un extrait, et tu dis une fois ce qu'elle est (prompt image, prompt vidéo,
  durée, références…) ;
* ce choix est retenu (réglage ``etiquettes_apprises``) et vaut pour tous les
  packs suivants, quel que soit le type d'histoire ;
* si aucun lecteur n'a trouvé de scène, les débuts de scène numérotés (« Scène
  1 », « Plan 2 », « SÉQUENCE 3 »…) sont repérés tout seuls.

Rien n'est inventé ni reformulé : un champ n'est rempli qu'avec le texte du pack,
recopié tel quel, et seulement s'il était vide.
"""
from __future__ import annotations

import json
import re
import unicodedata

from . import db

REGLAGE = "etiquettes_apprises"

#: Ce qu'une étiquette peut désigner. « ignorer » la fait taire pour de bon.
CHAMPS = {
    "prompt_image": "Prompt image (ChatGPT)",
    "prompt_video": "Prompt vidéo (Grok)",
    "duree": "Durée de la scène",
    "refs": "Références / personnages à joindre",
    "inventaire": "Inventaire de l'image de départ",
    "ce_qui_change": "Ce qui change",
    "ignorer": "Rien d'utile — à ignorer",
}
CHAMPS_REQUIS = ("prompt_image", "prompt_video", "duree")

#: Étiquettes que les lecteurs savent déjà lire, avec leur sens : jamais
#  proposées, elles ferment un bloc de prompt, et elles servent aussi quand ce
#  sont les numéros de scène, pas un lecteur, qui ont découpé le pack.
PAR_DEFAUT = {
    "prompt image": "prompt_image",
    "version normale": "prompt_video", "version dynamique": "prompt_video",
    "prompt grok": "prompt_video", "prompt video": "prompt_video",
    "attache": "ignorer",
    "ce qui change": "ce_qui_change",
    "inventaire": "inventaire", "inventaire de l'image de depart": "inventaire",
    "image de depart": "inventaire",
    "references": "refs", "references a charger dans chatgpt": "refs",
    "references a charger": "refs", "reference": "refs", "refs": "refs",
    "duree": "duree", "duree grok": "duree", "duree a regler dans grok": "duree",
    "duree a regler": "duree", "grok": "duree",
    # Série réaliste en plans (« Le mariage forcé ») : reconnues d'office.
    "prompt image de depart": "prompt_image",
    "references de personnages a joindre": "refs",
    "references a joindre": "refs",
    "duree a conserver avant acceleration": "duree",
    "duree a conserver": "duree",
    "repere dans le montage brut": "ignorer",
    "note de raccordement montage": "ignorer",
    "note de raccordement": "ignorer",
    "note de montage": "ignorer",
}
CONNUES = set(PAR_DEFAUT)

#: Le SENS d'une étiquette, quand son texte exact n'est pas connu. Le 16 novembre
#  2026, le même format est revenu avec « Durée utile avant accélération »,
#  « Repère brut » et « RACCORD ET MONTAGE » : trois mots changés, et toutes les
#  durées manquaient — pendant que la note de montage partait chez Grok. On
#  reconnaît donc les mots qui portent le sens, pas la phrase entière.
SENS_PAR_MOTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("prompt", "image"), "prompt_image"),
    (("prompt", "video"), "prompt_video"),
    (("prompt", "grok"), "prompt_video"),
    (("duree",), "duree"),
    (("references",), "refs"),
    (("reference",), "refs"),
    (("inventaire",), "inventaire"),
    (("ce", "qui", "change"), "ce_qui_change"),
    (("repere",), "ignorer"),
    (("raccord",), "ignorer"),
    (("raccordement",), "ignorer"),
    (("montage",), "ignorer"),
)


def sens(k: str, appris: dict[str, str]) -> str | None:
    """Ce que désigne l'étiquette `k` : appris > connu > mots-clés > rien."""
    if k in appris:
        return appris[k]
    mots = set(k.split())
    for cles, champ in SENS_PAR_MOTS:
        if set(cles) <= mots:
            return champ
    return None
#: Mots d'en-tête de pack qui ne sont jamais des débuts de scène.
PAS_DES_SCENES = {"partie", "episode", "saison", "version", "planche", "personnage", "r", "p"}

#: Mots-clés des avertissements que remplir un champ rend caducs.
MOTS_AVERTISSEMENT = {
    "prompt_image": ("prompt image",),
    "prompt_video": ("prompt vidéo", "prompt video"),
    "duree": ("durée", "duree"),
    "refs": ("références", "reference"),
    "inventaire": ("inventaire",),
    "ce_qui_change": ("ce qui change",),
}

RE_GUILLEMET = re.compile(r"^\s*[«\"“]")
RE_HORAIRE = re.compile(r"^\d{1,2}:\d{2}")
# « 9:16 » et « 00:06 » ne sont pas des séparateurs d'étiquette.
RE_SEPARATEUR = re.compile(r"\s*(?:(?<!\d):(?!\d)|\s[—–-]\s|[—–])\s*")
RE_DEBUT_SCENE = re.compile(
    r"^([A-Za-zÀ-ÿ]+(?:\s[A-Za-zÀ-ÿ]+)?)\s*(?:n[°o]\s*)?(\d{1,2})\b\s*(.*)$"
)


# --------------------------------------------------------------------------
# Étiquettes
# --------------------------------------------------------------------------


def _sans_accents(texte: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn"
    )


def _propre(ligne: str) -> str:
    """La ligne sans markdown ni emoji de tête : « **🎬 X** » → « X »."""
    sans = ligne.replace("**", "").replace("__", "").strip()
    sans = re.sub(r"^[#>\-*•\s]+", "", sans)
    return re.sub(r"^[^\w«\"“(]+", "", sans).strip()


def cle(etiquette: str) -> str:
    """Forme stable d'une étiquette : minuscules, sans accents, sans chiffres."""
    texte = _sans_accents(etiquette).lower().replace("’", "'")
    texte = re.sub(r"[^a-z' ]", " ", texte)
    mots = [m for m in texte.split() if m not in ("s", "sec", "secondes", "seconde")]
    return " ".join(mots).strip(" '")


def apprises() -> dict[str, str]:
    try:
        brut = json.loads(db.reglage(REGLAGE) or "{}")
    except (ValueError, TypeError):
        return {}
    return {k: v for k, v in brut.items() if v in CHAMPS}


def apprendre(choix: dict[str, str]) -> int:
    """Retient {clé d'étiquette: champ}. Rend le nombre d'étiquettes retenues."""
    connues = apprises()
    retenues = 0
    for etiquette, champ in choix.items():
        k = cle(etiquette)
        if k and champ in CHAMPS:
            connues[k] = champ
            retenues += 1
    db.definir_reglage(REGLAGE, json.dumps(connues, ensure_ascii=False, sort_keys=True))
    return retenues


def oublier(etiquette: str) -> None:
    connues = apprises()
    connues.pop(cle(etiquette), None)
    db.definir_reglage(REGLAGE, json.dumps(connues, ensure_ascii=False, sort_keys=True))


def _etiquette(ligne: str) -> tuple[str, str, str] | None:
    """(clé, étiquette telle qu'écrite, ce qui suit) si la ligne en porte une.

    Deux formes : « Étiquette : valeur » / « Étiquette — valeur », et un intitulé
    seul sur sa ligne (« 🎬 PROMPT IMAGE »). Une réplique (« PIXEL — « … » »),
    un minutage ou une phrase ne sont pas des étiquettes.
    """
    brute = ligne.strip()
    propre = _propre(brute)
    if not propre or RE_HORAIRE.match(propre):
        return None
    morceaux = RE_SEPARATEUR.split(propre, maxsplit=1)
    if len(morceaux) == 2 and morceaux[0]:
        gauche, droite = morceaux
        if RE_GUILLEMET.match(droite):
            return None  # réplique de dialogue
        if re.search(r"[.!?…]\s", gauche):
            return None  # une phrase avant les deux-points : du texte, pas une étiquette
        if 1 <= len(gauche.split()) <= 6 and len(gauche) <= 50:
            k = cle(gauche)
            if k:
                return k, gauche.strip(), droite.strip()
        return None
    # Intitulé seul : court, sans ponctuation finale, et visiblement mis en avant.
    mots = propre.split()
    if not (1 <= len(mots) <= 6) or propre.endswith((".", "!", "?", "…", "»", '"')):
        return None
    en_avant = (
        brute != propre  # emoji, # ou ** devant
        or propre.isupper()
        or propre.endswith(":")
    )
    k = cle(propre)
    return (k, propre.rstrip(":").strip(), "") if (en_avant and k) else None


def _est_fermante(k: str, appris: dict[str, str]) -> bool:
    """Une étiquette connue, même « à ignorer », termine le champ précédent.

    « NOTE DE MONTAGE — NE PAS LIRE À VOIX HAUTE » est à ignorer, mais elle
    marque bien la fin du prompt vidéo : sans ça, la note partait chez Grok.
    """
    return k in CONNUES or sens(k, appris) is not None


def _bloc(lignes: list[str], i: int, droite: str, appris: dict[str, str], long: bool) -> str:
    """Le texte d'un champ : ce qui suit l'étiquette, jusqu'au champ suivant.

    `long` (un prompt) court jusqu'à la prochaine étiquette de champ ; sinon on
    s'arrête au premier paragraphe. Une étiquette inconnue au milieu d'un prompt
    (« Caméra : lente ») n'est pas une fin : elle fait partie du prompt.
    """
    morceaux: list[str] = []
    if droite and (not long or len(droite.split()) > 8):
        morceaux.append(droite)
    for ligne in lignes[i + 1:]:
        trouvee = _etiquette(ligne)
        if trouvee and _est_fermante(trouvee[0], appris):
            break
        if not long and not ligne.strip():
            if morceaux:
                break
            continue
        morceaux.append(ligne.rstrip())
    return "\n".join(morceaux).strip()


def _duree(*textes: str) -> int | None:
    for texte in textes:
        m = re.search(r"(\d{1,2})\s*(?:s\b|sec|secondes?)", texte or "", re.IGNORECASE)
        if m:
            return int(m.group(1))
    for texte in textes:
        m = re.search(r"\b(\d{1,2})\b", texte or "")
        if m:
            return int(m.group(1))
    return None


# --------------------------------------------------------------------------
# Scènes
# --------------------------------------------------------------------------


def _scene_vide(numero: int, titre: str) -> dict:
    return {
        "numero": numero, "titre": titre, "emoji": "", "duree": None, "attache": "",
        "inventaire": "", "ce_qui_change": "", "refs_brut": "", "refs_noms": [],
        "refs_codes": [], "refs_video": [], "prompt_image": "", "prompt_video": "",
        "version_video": "", "dialogues": [], "nb_repliques": 0, "nb_mots": 0,
    }


def _debuts_de_scene(lignes: list[str]) -> dict[int, int]:
    """{numéro de scène: indice de ligne} : la suite numérotée la plus longue.

    « Scène 1 … Scène 2 … », « PLAN 1 », « Séquence 3 » : le mot importe peu,
    c'est la suite 1, 2, 3… sous un même mot qui fait le début de scène.
    """
    # Un même numéro peut revenir : « Plan 1 : titre en surimpression » dans
    # les consignes de montage, puis « PLAN 01 — LE BOUQUET » plus bas. Chaque
    # « 1 » est essayé comme départ ; la suite la plus longue gagne, et à
    # longueur égale la plus tardive, celle qui porte les prompts.
    par_mot: dict[str, dict[int, list[int]]] = {}
    for i, ligne in enumerate(lignes):
        m = RE_DEBUT_SCENE.match(_propre(ligne))
        if not m:
            continue
        mot = cle(m.group(1))
        if not mot or mot.split()[0] in PAS_DES_SCENES:
            continue
        par_mot.setdefault(mot, {}).setdefault(int(m.group(2)), []).append(i)
    meilleur: dict[int, int] = {}
    for debuts in par_mot.values():
        for depart in debuts.get(1, []):
            suite = {1: depart}
            n = 2
            while True:
                suivants = [i for i in debuts.get(n, []) if i > suite[n - 1]]
                if not suivants:
                    break
                suite[n] = suivants[0]
                n += 1
            if len(suite) >= len(meilleur):
                meilleur = suite
    return meilleur if len(meilleur) >= 2 else {}


#: {numéro: durée} lus dans les titres « PLAN 01 — … (10 s) », toutes suites confondues.
_durees_des_titres: dict[int, int] = {}
RE_DUREE_DE_TITRE = re.compile(r"\((\d{1,2})\s*(?:s|sec|secondes?)\)", re.IGNORECASE)


def _durees_dans_les_titres(lignes: list[str]) -> dict[int, int]:
    """La durée écrite dans le titre d'un plan, même dans le résumé du pack.

    « PLAN 01 — TU VOULAIS QUOI ? | 00:00–00:10 (10 s) » : le découpage
    chronométré donne la durée, le plan détaillé plus bas ne la répète pas
    toujours. Premier titre trouvé pour chaque numéro.
    """
    durees: dict[int, int] = {}
    for ligne in lignes:
        m = RE_DEBUT_SCENE.match(_propre(ligne))
        if not m:
            continue
        d = RE_DUREE_DE_TITRE.search(m.group(3) or "")
        if d:
            durees.setdefault(int(m.group(2)), int(d.group(1)))
    return durees


RE_RUBRIQUE = re.compile(r"^\s*(?:\*\*|#+\s*)?[^\w\s«\"“(\-*•>]")


def _fin_de_la_derniere(lignes: list[str], debut: int, appris: dict[str, str]) -> int:
    """Où s'arrête la dernière scène : avant la première rubrique hors scène.

    Après la dernière scène, les packs ajoutent souvent un tableau (« 📊 Script
    complet »), la miniature ou une checklist. Une ligne à emoji qui n'est pas
    un champ de scène ouvre cette queue.
    """
    for j in range(debut, len(lignes)):
        if not RE_RUBRIQUE.match(lignes[j]):
            continue
        tete = re.split(r"\s[·|]\s|\s[—–-]\s|:", _propre(lignes[j]), maxsplit=1)[0]
        if cle(tete) not in appris:
            return j
    return len(lignes)


def _corps_des_scenes(
    texte: str, appris: dict[str, str]
) -> tuple[dict[int, list[str]], dict[int, str]]:
    """(corps de chaque scène en lignes, titre de chaque scène), lus dans le texte brut."""
    lignes = texte.replace("\r\n", "\n").split("\n")
    debuts = _debuts_de_scene(lignes)
    corps: dict[int, list[str]] = {}
    titres: dict[int, str] = {}
    _durees_des_titres.clear()
    _durees_des_titres.update(_durees_dans_les_titres(lignes))
    ordre = sorted(debuts.items(), key=lambda kv: kv[1])
    for rang, (numero, i) in enumerate(ordre):
        fin = (
            ordre[rang + 1][1] if rang + 1 < len(ordre)
            else _fin_de_la_derniere(lignes, i + 1, appris)
        )
        corps[numero] = lignes[i + 1:fin]
        m = RE_DEBUT_SCENE.match(_propre(lignes[i]))
        reste = (m.group(3) if m else "").strip(" —–-·:")
        # « Le marché · 00:00–00:06 » ou « Le marché (6 s) » : le titre seul.
        titres[numero] = re.split(r"\s[·|]\s|\s\(", reste, maxsplit=1)[0].strip(" —–-·:")
    return corps, titres


# --------------------------------------------------------------------------
# Passage commun
# --------------------------------------------------------------------------


def completer(texte: str, res) -> None:
    """Complète `res` (ResultatImport) à partir du texte brut. Ne remplace rien."""
    from . import parser_v6

    appris = {**PAR_DEFAUT, **apprises()}
    corps, titres = _corps_des_scenes(texte, appris)

    sans_prompt = all(
        not s.get("prompt_image") and not s.get("prompt_video") for s in res.scenes
    )
    if corps and res.scenes and sans_prompt:
        # Le lecteur a pris un résumé (« SCÈNE 1 … SCÈNE 6 », sans aucun prompt)
        # pour le découpage, alors que la numérotation trouve plus de plans plus
        # bas. Ce sont les plans qui portent les prompts : on repart d'eux, et
        # les remarques sur les scènes du résumé n'ont plus d'objet.
        res.avertissements = [
            a for a in res.avertissements
            if not a.cible.lower().startswith(("scene", "scène"))
        ]
        res.scenes = []
    if not res.scenes and corps:
        res.scenes = [_scene_vide(n, titres.get(n, "")) for n in sorted(corps)]
        res.avertir(
            "info", "document",
            f"Format de pack inconnu : {len(res.scenes)} scène(s) repérée(s) par leur "
            "numérotation. Dis à l'application ce que sont les étiquettes ci-dessous, "
            "elle s'en souviendra pour les packs suivants.",
        )
        if res.type_document == "inconnu":
            res.type_document = "pack"

    inconnues: dict[str, dict] = {}
    # « Rox — voix adulte… » : le nom d'un personnage n'est pas une étiquette.
    noms = {cle(p.get("surnom") or "") for p in res.personnages} - {""}
    refs_remplies = False
    refs_de_scene: list[dict] = []
    for scene in res.scenes:
        lignes = corps.get(scene["numero"])
        if not lignes:
            continue
        remplis = _remplir(scene, lignes, appris)
        if scene.get("duree") is None and scene["numero"] in _durees_des_titres:
            scene["duree"] = _durees_des_titres[scene["numero"]]
            remplis.add("duree")
        if "refs" in remplis:
            scene["refs_noms"] = parser_v6._decouper_refs(scene["refs_brut"])
            refs_remplies = True
            refs_de_scene.append(scene)
        if "prompt_video" in remplis and not scene.get("dialogues"):
            scene["dialogues"] = parser_v6._extraire_dialogues(scene["prompt_video"])
            scene["nb_repliques"] = len(scene["dialogues"])
            scene["nb_mots"] = sum(len(d["texte"].split()) for d in scene["dialogues"])
        if "duree" in remplis:
            _duree_pour_grok(scene, res)
        if remplis:
            _oublier_avertissements(res, scene["numero"], remplis)
        manquants = [c for c in CHAMPS_REQUIS if not scene.get(c)]
        if manquants:
            _relever_inconnues(scene["numero"], lignes, appris, inconnues, noms)

    _joindre_bloc_commun(texte, res)
    if refs_remplies and not res.personnages:
        _personnages_depuis_les_references(res, refs_de_scene)
    if refs_remplies:
        res.avertissements = [
            a for a in res.avertissements if "ne correspond à aucune planche" not in a.message
        ]
        parser_v6._lier_refs(res)
        # « Références de personnages à joindre » vaut pour la vidéo aussi : sans
        # @imageN dans le prompt, Grok recevrait l'image de départ sans les
        # planches que le prompt lui demande d'utiliser. Même ordre que la liste.
        for scene in refs_de_scene:
            if not scene.get("refs_video"):
                scene["refs_video"] = list(scene["refs_codes"])
    res.etiquettes_inconnues = sorted(
        inconnues.values(), key=lambda e: (-len(e["scenes"]), e["etiquette"])
    )


def _remplir(scene: dict, lignes: list[str], appris: dict[str, str]) -> set[str]:
    remplis: set[str] = set()
    for i, ligne in enumerate(lignes):
        trouvee = _etiquette(ligne)
        if not trouvee:
            continue
        k, _, droite = trouvee
        champ = sens(k, appris)
        if not champ or champ == "ignorer":
            continue
        if champ == "duree":
            if scene.get("duree") is None:
                valeur = _duree(droite, ligne, _bloc(lignes, i, "", appris, False))
                if valeur:
                    scene["duree"] = valeur
                    remplis.add("duree")
            continue
        cle_scene = "refs_brut" if champ == "refs" else champ
        if scene.get(cle_scene):
            continue
        valeur = _bloc(lignes, i, droite, appris, champ in ("prompt_image", "prompt_video"))
        if not valeur:
            continue
        scene[cle_scene] = valeur
        remplis.add(champ)
        if champ == "prompt_video" and scene.get("duree") is None:
            # « PROMPT GROK — Version normale · 6 s » porte aussi la durée.
            d = _duree(droite or ligne)
            if d:
                scene["duree"] = d
                remplis.add("duree")
    return remplis


def _relever_inconnues(
    numero: int, lignes, appris, inconnues: dict[str, dict], noms: set[str]
) -> None:
    for i, ligne in enumerate(lignes):
        trouvee = _etiquette(ligne)
        if not trouvee:
            continue
        k, ecrite, droite = trouvee
        if k in CONNUES or sens(k, appris) is not None or k in noms:
            continue
        entree = inconnues.setdefault(
            k,
            {
                "cle": k,
                "etiquette": ecrite,
                "ligne": ligne.strip()[:160],
                "extrait": _bloc(lignes, i, droite, appris, True)[:220],
                "scenes": [],
            },
        )
        if numero not in entree["scenes"]:
            entree["scenes"].append(numero)


def _oublier_avertissements(res, numero: int, remplis: set[str]) -> None:
    cibles = {f"scene {numero}", f"scène {numero}"}
    mots = [m for champ in remplis for m in MOTS_AVERTISSEMENT.get(champ, ())]

    def caduc(a) -> bool:
        return a.cible in cibles and any(m in a.message.lower() for m in mots)

    res.avertissements = [a for a in res.avertissements if not caduc(a)]


def _duree_pour_grok(scene: dict, res) -> None:
    """Une durée de montage (7 s) devient la durée Grok qui la contient (10 s).

    Grok ne propose que quelques durées. Le pack le dit lui-même : la durée
    du plan est la portion à garder au montage, pas celle de la génération.
    On génère donc la durée acceptée juste au-dessus, et on le note.
    """
    from . import reglages

    acceptees = sorted(reglages.durees_grok())
    voulue = scene["duree"]
    if not acceptees or voulue in acceptees:
        return
    grok = next((d for d in acceptees if d >= voulue), acceptees[-1])
    scene["duree"] = grok
    res.avertir(
        "info", f"scene {scene['numero']}",
        f"Plan de {voulue} s : généré en {grok} s (durée Grok la plus proche "
        f"au-dessus). Garde {voulue} s au montage.",
    )


RE_TITRE_BLOC = re.compile(r"^\W*(?:\d+\s*[.)]\s*)?BLOC\s+COMMUN\b", re.IGNORECASE)
RE_APPEL_BLOC = re.compile(
    r"Appliquer\s+(?:les\s+r[èe]gles\s+visuelles\s+du|le)\s+BLOC\s+COMMUN\.?",
    re.IGNORECASE,
)


def bloc_commun(texte: str) -> str:
    """Le texte de la section « BLOC COMMUN », s'il y en a une."""
    lignes = texte.replace("\r\n", "\n").split("\n")
    for i, ligne in enumerate(lignes):
        if not RE_TITRE_BLOC.match(ligne.strip()):
            continue
        corps: list[str] = []
        for suite in lignes[i + 1:]:
            nette = suite.strip()
            if re.match(r"^[=—–_\-]{6,}$", nette) and corps:
                break
            if re.match(r"^\d+\s*[.)]\s+[A-ZÀ-Ý]", nette) and corps:
                break
            if re.match(r"^[=—–_\-]{6,}$", nette):
                continue
            corps.append(suite.rstrip())
        bloc = "\n".join(corps).strip()
        if bloc:
            return bloc
    return ""


def _joindre_bloc_commun(texte: str, res) -> None:
    """« Appliquer le BLOC COMMUN » : ChatGPT et Grok ne l'ont jamais vu.

    Le pack demande de joindre ce bloc à chaque prompt. Un renvoi à un texte que
    le site n'a pas reçu ne sert à rien : la phrase de renvoi est remplacée par
    le bloc lui-même, recopié tel quel. Le reste du prompt ne change pas.
    """
    bloc = bloc_commun(texte)
    if not bloc:
        return
    joints = 0
    for scene in res.scenes:
        for champ in ("prompt_image", "prompt_video"):
            prompt = scene.get(champ) or ""
            if RE_APPEL_BLOC.search(prompt):
                scene[champ] = RE_APPEL_BLOC.sub(lambda _m: bloc, prompt, count=1)
                joints += 1
    if joints:
        res.avertir(
            "info", "document",
            f"Le BLOC COMMUN du pack a été joint à {joints} prompt(s), à la place de "
            "« Appliquer le BLOC COMMUN » : ChatGPT et Grok ne l'auraient pas reçu sinon.",
        )


RE_ACCELERATION = re.compile(r"acc[ée]l[ée]ration\s*[x×]\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)


def analyser_serie(texte: str):
    """Lecteur du type « Série réaliste en plans ».

    Ce format n'a ni planches ni en-tête fixe : les plans, leurs prompts et leurs
    références sont lus par `completer` (étiquettes connues d'office). Ici, on ne
    lit que l'en-tête : le titre (première ligne) et l'accélération prévue au
    montage (« accélération ×1,3 »), qui devient la vitesse d'export.
    """
    from .parser_v6 import ResultatImport

    res = ResultatImport(brut=texte or "", type_document="pack")
    for ligne in (texte or "").replace("\r\n", "\n").split("\n"):
        propre = _propre(ligne)
        if propre:
            res.titre = _entre_guillemets_simples(propre)[:120]
            break
    m = RE_ACCELERATION.search(texte or "")
    if m:
        res.vitesse = float(m.group(1).replace(",", "."))
        res.avertir(
            "info", "document",
            f"Accélération ×{m.group(1)} prévue au montage : vitesse d'export réglée "
            f"sur {res.vitesse:g}.",
        )
    return res


def _entre_guillemets_simples(valeur: str) -> str:
    m = re.search(r"«\s*(.+?)\s*»", valeur)
    return m.group(1) if m else valeur


def _personnages_depuis_les_references(res, scenes: list[dict]) -> None:
    """Nouvelle série sans planches dans le pack : un personnage par nom cité.

    Les noms viennent des lignes « Références de personnages à joindre », dans
    l'ordre où ils apparaissent. Rien n'est inventé : pas de description, pas de
    prompt de planche. Il reste à déposer la planche de chacun.
    """
    vus: list[str] = []
    for scene in scenes:
        for ref in scene.get("refs_noms") or []:
            nom = (ref.get("nom") or "").strip()
            if nom and nom.upper() not in vus and len(nom) <= 40:
                vus.append(nom.upper())
    for rang, nom in enumerate(vus, start=1):
        res.personnages.append({
            "code": f"R{rang}", "surnom": nom, "etat": "UNIQUE", "espece": "",
            "role": "", "age": "", "genre": "", "voix": "", "etiquette_grok": "",
            "lien": "", "prompt_planche": "",
        })
    if vus:
        res.avertir(
            "info", "personnages",
            f"{len(vus)} personnage(s) créé(s) d'après les références des plans : "
            + ", ".join(vus) + ". Dépose la planche de chacun dans l'onglet Personnages.",
        )
