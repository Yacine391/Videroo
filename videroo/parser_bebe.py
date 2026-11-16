"""Lecteur du pack « bébé IA » : des blocs titrés, mais pas ceux du V6.

Le métaprompt BABY REALISTIC STORY DRAMA livre ses planches et ses scènes dans
une forme à lui :

    R1 — ROSE, l'héroïne
    9 mois · Héroïne
    Voix : française fictive, claire, douce
    Prompt planche R1 — ChatGPT Image
    <le prompt de la planche, un paragraphe>

    O1 — Le doudou
    Prompt : <le prompt de l'objet, sur la même ligne>

    SCÈNE 01 — LE DOUDOU INTERDIT
    6 secondes · Version normale · Hook
    Références ChatGPT : R1, R2, O1, O3.
    Prompt image — ChatGPT
    <le prompt de l'image de départ>
    Dialogue
    JADE : « Ce doudou ne devait jamais revenir ici. »
    Prompt vidéo — Grok
    <le prompt d'animation, plusieurs paragraphes>

Deux differences qui comptent pour la suite de la chaine :

  * les objets (O1, O2…) sont de VRAIES planches, a generer et a joindre comme
    les personnages. Le pack les cite dans « Références ChatGPT », donc ils
    deviennent des references a part entiere : un code O1 ne remplace pas une
    image jointe, pas plus qu'un R1 ;
  * la section 18 du metaprompt donne un gabarit (« PROMPT IMAGE — CHATGPT »,
    « Références à charger dans ChatGPT : », « Durée à régler dans Grok : »)
    que ChatGPT rend parfois tel quel, parfois sous la forme de l'exemple. Les
    deux ecritures sont acceptees.

Comme les deux autres lecteurs, celui-ci remplit exactement les structures du
V6 : l'atelier, le runner, la miniature et l'export n'ont rien a savoir du type
d'histoire. Et comme ailleurs, rien n'est invente : un champ absent reste vide
et il est signale.
"""
from __future__ import annotations

import re

from .parser_v6 import RE_PARTIE_SUR, ResultatImport

#: « R1 — ROSE, l'héroïne » ou « O1 — Le doudou ». Le code est en tete de ligne.
RE_CODE_TITRE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*([ROP]\d+)\s*[—–-]\s*(.+?)\s*(?:\*\*)?\s*$"
)
#: « Prompt planche R1 — ChatGPT Image »
RE_PROMPT_PLANCHE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*PROMPT\s+(?:DE\s+)?PLANCHE\s*([ROP]\d+)?\b.*$",
    re.IGNORECASE,
)
#: « Prompt : … » d'une reference d'objet : le prompt est sur la meme ligne.
RE_PROMPT_COURT = re.compile(r"^\s*(?:\*\*)?\s*PROMPT\s*:\s*(.*)$", re.IGNORECASE)
#: « SCÈNE 01 — LE DOUDOU INTERDIT » et « SCÈNE 1 — TITRE — 10 SECONDES »
RE_SCENE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*SC[ÈE]NE\s+(\d+)\s*[—–:-]\s*(.+?)\s*(?:\*\*)?\s*$",
    re.IGNORECASE,
)
#: « Prompt image — ChatGPT » / « PROMPT IMAGE — CHATGPT »
RE_PROMPT_IMAGE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*PROMPT\s+IMAGE\b.*$", re.IGNORECASE
)
#: « Prompt vidéo — Grok » / « PROMPT VIDÉO GROK — VERSION RECOMMANDÉE »
RE_PROMPT_VIDEO = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*PROMPT\s+VID[ÉE]O\b.*$", re.IGNORECASE
)
#: « Références ChatGPT : R1, R2, O1, O3. » / « Références à charger… : … »
RE_REFERENCES = re.compile(
    r"^\s*(?:\*\*)?\s*R[ÉE]F[ÉE]RENCES?\b[^:]{0,60}:\s*(.+)$", re.IGNORECASE
)
RE_INVENTAIRE = re.compile(
    r"^\s*(?:\*\*)?\s*INVENTAIRE\b[^:]{0,60}:\s*(.*)$", re.IGNORECASE
)
RE_ATTACHE = re.compile(r"^\s*(?:\*\*)?\s*ATTACHE\s*:\s*(.+)$", re.IGNORECASE)
RE_CE_QUI_CHANGE = re.compile(
    r"^\s*(?:\*\*)?\s*CE\s+QUI\s+CHANGE\s*:\s*(.+)$", re.IGNORECASE
)
RE_DUREE_GROK = re.compile(
    r"^\s*(?:\*\*)?\s*DUR[ÉE]E\s+[^:]{0,40}GROK\s*:\s*(\d+)", re.IGNORECASE
)
#: « 6 secondes · Version normale · Hook » ou « — 10 SECONDES » dans le titre.
RE_SECONDES = re.compile(r"(\d+)\s*secondes?\b", re.IGNORECASE)
RE_VERSION = re.compile(r"\bVersion\s+(normale|dynamique)\b", re.IGNORECASE)
#: « JADE : « Ce doudou ne devait jamais revenir ici. » »
RE_REPLIQUE = re.compile(
    r"^\s*(?:\*\*)?\s*([A-ZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜ][A-ZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜ'’\s-]{1,28}?)"
    r'\s*(?:\*\*)?\s*:\s*[«"](.+?)[»"]\s*$'
)
#: « 9 mois · Héroïne »
RE_AGE_ROLE = re.compile(r"^\s*(\d+\s*mois)\s*·\s*(.+?)\s*$", re.IGNORECASE)
RE_VOIX = re.compile(r"^\s*(?:\*\*)?\s*VOIX\s*:\s*(.+)$", re.IGNORECASE)
RE_TENUE = re.compile(r"^\s*(?:\*\*)?\s*TENUE\s*:\s*(.+)$", re.IGNORECASE)
#: Le bouton « Copier R1 » de l'interface ChatGPT, colle avec le texte.
RE_COPIER = re.compile(r"^\s*Copier\s+\S{1,20}\s*$", re.IGNORECASE)
#: Les titres de section du package (metaprompt, section 14). Ils ferment le
#  prompt en cours : sans eux, « 3. Les 10 scènes » se retrouvait collé à la
#  fin du dernier prompt d'objet. Les « 1. CHARACTERS », « 2. ACTION » d'un
#  prompt Grok, eux, ne sont PAS des titres de section : ils restent dedans.
RE_SECTION_PACK = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\d+\.\s*)?(?:\*\*)?\s*"
    r"(?:LE\s+SC[ÉE]NARIO|PLANCHES?\s+DE\s+R[ÉE]F[ÉE]RENCE|EN-?T[ÊE]TE|"
    r"LES\s+\d+\s+SC[ÈE]NES|R[ÉE]F[ÉE]RENCES?\s+DES\s+OBJETS|"
    r"PROPOSITION\s+FINALE)\b",
    re.IGNORECASE,
)

#: Fin du pack : apres, ce ne sont plus des prompts.
RE_FIN_PACK = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\d+\.\s*)?(?:\*\*)?\s*(?:TABLEAU\s+FINAL|SCRIPT\s+FINAL|"
    r"TEST\s+DE\s+COH[ÉE]RENCE|CONTR[ÔO]LE\s+FINAL)",
    re.IGNORECASE,
)
#: Mot a mot du metaprompt, section 20 : l'ouverture commune des prompts Grok.
OUVERTURE_GROK = "ultra-photorealistic live-action style footage of real human infants"

#: Les lignes de titrage qu'on ne verse jamais dans un prompt.
MARQUEURS = (
    RE_CODE_TITRE, RE_PROMPT_PLANCHE, RE_PROMPT_COURT, RE_SCENE, RE_PROMPT_IMAGE,
    RE_PROMPT_VIDEO, RE_REFERENCES, RE_INVENTAIRE, RE_ATTACHE, RE_CE_QUI_CHANGE,
    RE_DUREE_GROK, RE_COPIER, RE_FIN_PACK, RE_VOIX, RE_TENUE, RE_SECTION_PACK,
)
RE_DIALOGUE_TITRE = re.compile(
    r"^\s*(?:\*\*)?\s*DIALOGUES?\s*(?:\*\*)?\s*:?\s*$", re.IGNORECASE
)


def _est_un_marqueur(ligne: str) -> bool:
    """Vrai si cette ligne ouvre autre chose : on arrete la de collecter."""
    if RE_DIALOGUE_TITRE.match(ligne) or RE_REPLIQUE.match(ligne):
        return True
    return any(motif.match(ligne) for motif in MARQUEURS)


def detecter(texte: str) -> bool:
    """Vrai si ce document est un pack bébé, a ses marqueurs propres."""
    lignes = (texte or "").splitlines()
    a_scene = any(RE_SCENE.match(l) for l in lignes)
    a_video = any(RE_PROMPT_VIDEO.match(l) for l in lignes)
    a_planche = any(RE_PROMPT_PLANCHE.match(l) for l in lignes)
    a_ouverture = OUVERTURE_GROK in (texte or "")
    return a_scene and a_video and (a_planche or a_ouverture)


def _codes_cites(brut: str) -> list[str]:
    """Les codes d'une ligne « Références … : R1, R2, O1 », sans doublon."""
    codes: list[str] = []
    for code in re.findall(r"\b([ROP]\d+)\b", brut or ""):
        majuscule = code.upper()
        if majuscule not in codes:
            codes.append(majuscule)
    return codes


def _nouvelle_scene(numero: int) -> dict:
    return {
        "numero": numero,
        "titre": "",
        "emoji": "",
        "duree": None,
        "attache": "",
        "inventaire": "",
        "ce_qui_change": "",
        "refs_codes": [],
        "refs_brut": "",
        "prompt_image": "",
        "prompt_video": "",
        "version_video": "",
        "dialogues": [],
        "nb_repliques": 0,
        "nb_mots": 0,
    }


def _nouveau_personnage(code: str, titre: str) -> dict:
    """Separe le nom de ce qui le decrit, sans rien ajouter de notre cru.

    « ROSE, l'héroïne » donne ROSE ; « Le document de modification : une feuille
    ivoire… » donne LE DOCUMENT DE MODIFICATION. Le nom sert de nom de fichier
    pour la planche : il ne peut pas etre un paragraphe.
    """
    coupure = min(
        (i for i in (titre.find(","), titre.find(" : ")) if i > 0),
        default=-1,
    )
    nom, role = (titre[:coupure], titre[coupure:].lstrip(" ,:")) if coupure > 0 else (titre, "")
    return {
        "code": code.upper(),
        "surnom": nom.strip().upper(),
        "etat": "UNIQUE",
        "espece": "objet" if code.upper().startswith("O") else "",
        "role": role.strip(),
        "age": "",
        "genre": "",
        "voix": "",
        "etiquette_grok": "",
        "lien": "",
        "prompt_planche": "",
    }


def analyser(texte: str) -> ResultatImport:
    """Lit un pack bébé et remplit les mêmes structures que le V6."""
    res = ResultatImport(brut=texte or "")
    lignes = (texte or "").splitlines()
    if not detecter(texte):
        res.avertir(
            "manquant",
            "document",
            "Aucune scène avec son « Prompt vidéo » n'a été trouvée. Vérifie que "
            "tu as collé le package complet livré par le métaprompt bébé (planches "
            "puis scènes), et pas seulement la liste des dix histoires.",
        )
        return res

    res.type_document = "pack"
    res.univers = "bébé IA"

    # Le titre : la premiere ligne non vide avant la premiere planche.
    for ligne in lignes:
        net = ligne.strip()
        if not net:
            continue
        if RE_CODE_TITRE.match(ligne) or RE_SCENE.match(ligne):
            break
        res.titre = net
        res.titre_miniature = net
        break

    m = RE_PARTIE_SUR.search(texte or "")
    if m:
        res.parties = f"partie {m.group(1)} sur {m.group(2)}"

    personnage: dict | None = None
    scene: dict | None = None
    collecte: str = ""           # "planche" | "image" | "video" | ""
    tampon: list[str] = []
    fini = False

    def deverser() -> None:
        """Range le paragraphe en cours dans le champ qu'il remplissait."""
        nonlocal collecte, tampon
        texte_collecte = "\n".join(tampon).strip()
        if collecte == "planche" and personnage is not None and texte_collecte:
            personnage["prompt_planche"] = texte_collecte
        elif collecte == "image" and scene is not None and texte_collecte:
            scene["prompt_image"] = texte_collecte
        elif collecte == "video" and scene is not None and texte_collecte:
            scene["prompt_video"] = texte_collecte
        collecte, tampon = "", []

    def cloturer_scene() -> None:
        nonlocal scene
        if scene is not None:
            scene["nb_repliques"] = len(scene["dialogues"])
            scene["nb_mots"] = sum(
                len(str(d["texte"]).split()) for d in scene["dialogues"]
            )
            res.scenes.append(scene)
            scene = None

    for ligne in lignes:
        if RE_FIN_PACK.match(ligne):
            deverser()
            fini = True
            continue
        if fini:
            continue

        m = RE_SCENE.match(ligne)
        if m:
            deverser()
            cloturer_scene()
            personnage = None
            titre = m.group(2).strip()
            scene = _nouvelle_scene(int(m.group(1)))
            # Le gabarit colle parfois la duree au titre : « TITRE — 10 SECONDES ».
            duree = RE_SECONDES.search(titre)
            if duree:
                scene["duree"] = int(duree.group(1))
                titre = re.sub(r"\s*[—–-]?\s*\d+\s*secondes?\s*$", "", titre,
                               flags=re.IGNORECASE).strip()
            scene["titre"] = titre
            continue

        m = RE_CODE_TITRE.match(ligne)
        if m and scene is None:
            deverser()
            personnage = _nouveau_personnage(m.group(1), m.group(2))
            res.personnages.append(personnage)
            continue

        m = RE_PROMPT_PLANCHE.match(ligne)
        if m:
            deverser()
            code = (m.group(1) or "").upper()
            if code:
                # « Prompt planche R1 » peut arriver sans bloc d'en-tete.
                connu = next((p for p in res.personnages if p["code"] == code), None)
                if connu is None:
                    connu = _nouveau_personnage(code, code)
                    res.personnages.append(connu)
                personnage = connu
            if personnage is None:
                res.avertir(
                    "suspect", "document",
                    f"« {ligne.strip()} » : impossible de savoir à quelle référence "
                    "cette planche appartient. Elle est ignorée, rien n'est deviné.",
                )
                continue
            collecte = "planche"
            continue

        m = RE_PROMPT_COURT.match(ligne)
        if m and scene is None and personnage is not None:
            deverser()
            personnage["prompt_planche"] = m.group(1).strip()
            collecte, tampon = "planche", [m.group(1).strip()]
            continue

        m = RE_PROMPT_IMAGE.match(ligne)
        if m and scene is not None:
            deverser()
            collecte = "image"
            continue

        m = RE_PROMPT_VIDEO.match(ligne)
        if m and scene is not None:
            deverser()
            collecte = "video"
            continue

        if scene is not None:
            m = RE_REFERENCES.match(ligne)
            if m:
                deverser()
                scene["refs_brut"] = m.group(1).strip()
                scene["refs_codes"] = _codes_cites(m.group(1))
                continue
            m = RE_INVENTAIRE.match(ligne)
            if m:
                deverser()
                scene["inventaire"] = m.group(1).strip()
                continue
            m = RE_ATTACHE.match(ligne)
            if m:
                deverser()
                scene["attache"] = m.group(1).strip()
                continue
            m = RE_CE_QUI_CHANGE.match(ligne)
            if m:
                deverser()
                scene["ce_qui_change"] = m.group(1).strip()
                continue
            m = RE_DUREE_GROK.match(ligne)
            if m:
                deverser()
                scene["duree"] = int(m.group(1))
                continue
            m = RE_REPLIQUE.match(ligne)
            if m:
                deverser()
                scene["dialogues"].append({
                    "numero": len(scene["dialogues"]) + 1,
                    "etiquette": m.group(1).strip(),
                    "indice": "",
                    "texte": m.group(2).strip(),
                })
                continue
            if RE_DIALOGUE_TITRE.match(ligne):
                deverser()
                continue
            # « 6 secondes · Version normale · Hook » : hors de tout prompt.
            if not collecte:
                duree = RE_SECONDES.search(ligne)
                if duree and scene["duree"] is None:
                    scene["duree"] = int(duree.group(1))
                version = RE_VERSION.search(ligne)
                if version and not scene["version_video"]:
                    scene["version_video"] = version.group(1).lower()
                continue

        if personnage is not None and not collecte:
            m = RE_AGE_ROLE.match(ligne)
            if m:
                personnage["age"] = m.group(1).strip()
                if not personnage["role"]:
                    personnage["role"] = m.group(2).strip()
                continue
            m = RE_VOIX.match(ligne)
            if m:
                personnage["voix"] = m.group(1).strip()
                continue

        if collecte:
            if RE_COPIER.match(ligne):
                deverser()
                continue
            if _est_un_marqueur(ligne):
                deverser()
                continue
            tampon.append(ligne.rstrip())

    deverser()
    cloturer_scene()
    _controler(res)
    return res


def _controler(res: ResultatImport) -> None:
    """Signale ce qui manque. On ne complete jamais a la place du pack."""
    codes = {p["code"] for p in res.personnages}
    for p in res.personnages:
        cible = f"{p['code']} {p['surnom']}".strip()
        if not p["prompt_planche"]:
            # Dire QUELLES scenes sont bloquees : sans ca, on apprend le
            # probleme seulement au moment de lancer, scene par scene.
            bloquees = [
                str(s["numero"]) for s in res.scenes
                if p["code"] in (s.get("refs_codes") or [])
            ]
            portee = (
                f" Les scènes {', '.join(bloquees)} la citent : leurs images "
                "seront refusées avant tout envoi, donc sans quota perdu."
                if bloquees else
                " Aucune scène ne la cite, donc rien n'est bloqué."
            )
            res.avertir(
                "manquant", cible,
                "Aucun prompt de planche : cette référence ne pourra pas être "
                "générée, donc pas jointe aux scènes qui la citent." + portee,
            )
        if not p["surnom"]:
            res.avertir("manquant", cible, "Référence sans nom.")

    for s in res.scenes:
        cible = f"scène {s['numero']}"
        if not s["prompt_image"]:
            res.avertir("manquant", cible, "Prompt image absent.")
        if not s["prompt_video"]:
            res.avertir("manquant", cible, "Prompt vidéo absent.")
        if s["duree"] is None:
            res.avertir(
                "manquant", cible,
                "Durée introuvable : ni « N secondes » sous le titre, ni « Durée à "
                "régler dans Grok ». Corrige-la à la main avant de générer.",
            )
        if not s["refs_codes"]:
            res.avertir(
                "manquant", cible,
                "Aucune référence à joindre (« Références ChatGPT : … »). Un code "
                "cité dans le texte ne remplace pas une image réellement jointe.",
            )
        for code in s["refs_codes"]:
            if code not in codes:
                res.avertir(
                    "suspect", cible,
                    f"« {code} » est demandé en référence mais aucune planche {code} "
                    "n'est livrée dans ce pack : elle ne sera pas jointe.",
                )
        if s["prompt_video"] and OUVERTURE_GROK not in s["prompt_video"]:
            res.avertir(
                "suspect", cible,
                "Le prompt vidéo ne commence pas par l'ouverture commune imposée "
                "par le métaprompt bébé. Grok risque de perdre le photoréalisme.",
            )

    numeros = [s["numero"] for s in res.scenes]
    attendus = list(range(1, len(numeros) + 1))
    if numeros and numeros != attendus:
        res.avertir(
            "suspect", "document",
            f"Numéros de scènes : {numeros}. Il en manque ou elles sont dans le "
            "désordre — rien n'a été renuméroté.",
        )
