"""Parseur du pack de production produit par le métaprompt V6.

Principes :
  * on n'invente jamais un champ absent : il est laisse vide et signalé ;
  * on distingue le métaprompt lui-même d'un pack d'histoire déjà généré ;
  * on accepte Markdown, texte brut ou JSON.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from . import config, reglages

# --------------------------------------------------------------------------
# Detection du type de document
# --------------------------------------------------------------------------

MARQUEURS_METAPROMPT = (
    "METAPROMPT V6",
    "PHASE 0 — QUESTION D'OUVERTURE",
    "LOI ZÉRO",
    "BANQUE DE STYLES",
    "GABARIT — VERSION NORMALE",
    "GABARIT — VERSION DYNAMIQUE",
    "RÈGLES NUMÉROTÉES",
    "CONSTRUCTEUR DE LA RÈGLE DU MÉCHANT",
    "Tu es un réalisateur expert",
)

MARQUEURS_PACK = (
    "HISTOIRE :",
    "PLANCHE DE RÉFÉRENCE",
    "PROMPT IMAGE",
    "TEST DU PETIT COUSIN",
    "Inventaire de l'image de départ",
)


@dataclass
class Avertissement:
    niveau: str  # "manquant" | "suspect" | "info"
    cible: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"niveau": self.niveau, "cible": self.cible, "message": self.message}


@dataclass
class ResultatImport:
    type_document: str = "inconnu"  # pack | metaprompt | inconnu
    titre: str = ""
    moteur: str = ""
    univers: str = ""
    parties: str = ""
    cliffhanger: str = ""
    une_phrase: str = ""
    regle_mechant: str = ""
    menace_evolutive: str = ""
    prix_final: str = ""
    replique_tiktok: str = ""
    duree_annoncee: float | None = None
    nb_scenes_annonce: int | None = None
    instructions_miniature: str = ""
    #: Le texte a ecrire SUR la miniature, quand le pack le donne lui-meme.
    titre_miniature: str = ""
    personnages: list[dict[str, Any]] = field(default_factory=list)
    scenes: list[dict[str, Any]] = field(default_factory=list)
    avertissements: list[Avertissement] = field(default_factory=list)
    brut: str = ""
    #: Étiquettes de scène que personne ne sait encore lire (lecteur_souple).
    etiquettes_inconnues: list[dict[str, Any]] = field(default_factory=list)
    #: Vitesse d'export demandée par le pack (« accélération ×1,3 »), sinon None.
    vitesse: float | None = None

    @property
    def duree_calculee(self) -> float:
        return float(sum(s.get("duree") or 0 for s in self.scenes))

    def avertir(self, niveau: str, cible: str, message: str) -> None:
        self.avertissements.append(Avertissement(niveau, cible, message))

    def to_dict(self) -> dict[str, Any]:
        return {
            "type_document": self.type_document,
            "titre": self.titre,
            "moteur": self.moteur,
            "univers": self.univers,
            "parties": self.parties,
            "cliffhanger": self.cliffhanger,
            "une_phrase": self.une_phrase,
            "regle_mechant": self.regle_mechant,
            "menace_evolutive": self.menace_evolutive,
            "prix_final": self.prix_final,
            "replique_tiktok": self.replique_tiktok,
            "duree_annoncee": self.duree_annoncee,
            "duree_calculee": self.duree_calculee,
            "nb_scenes_annonce": self.nb_scenes_annonce,
            "instructions_miniature": self.instructions_miniature,
            "personnages": self.personnages,
            "scenes": self.scenes,
            "avertissements": [a.to_dict() for a in self.avertissements],
            "etiquettes_inconnues": self.etiquettes_inconnues,
        }


# --------------------------------------------------------------------------
# Outils de nettoyage
# --------------------------------------------------------------------------

def _sans_accents(texte: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn"
    )


def _nettoyer_ligne(ligne: str) -> str:
    """Retire puces, dieses et gras markdown d'une ligne."""
    l = ligne.strip()
    l = re.sub(r"^#{1,6}\s*", "", l)
    l = re.sub(r"^[-*•>]\s+", "", l)
    l = l.strip()
    while l.startswith("**"):
        l = l[2:].strip()
    while l.endswith("**"):
        l = l[:-2].strip()
    return l


def _retirer_blocs_code(texte: str) -> str:
    """Le V6 interdit les blocs code, mais un copier-coller peut en ajouter."""
    lignes = texte.split("\n")
    sortie: list[str] = []
    for ligne in lignes:
        if ligne.strip().startswith("```"):
            continue
        sortie.append(ligne)
    return "\n".join(sortie)


# Un meme champ peut etre ecrit en entier ou en abrege selon les packs.
ALIAS: dict[str, list[str]] = {
    "inventaire": ["inventaire de l'image de depart", "inventaire", "image de depart"],
    "refs": [
        "references a charger dans chatgpt",
        "references a charger",
        "references",
        "refs",
        # « 🎭 Référence ChatGPT : NAVETINA. » quand la scène n'a qu'un personnage.
        "reference",
    ],
    # « ⏱️ Grok — 6 secondes » dans les packs les plus courts.
    "duree_grok": ["duree a regler dans grok", "duree grok", "duree a regler", "grok"],
    "une_phrase": ["en une phrase", "l'histoire", "histoire en une phrase", "enjeu"],
    "prix_final": ["le prix final", "prix final", "prix du sacrifice", "prix paye"],
    "menace": ["menace evolutive", "menace", "obstacle"],
}


# Certains packs disent noir sur blanc qu'il n'y a pas d'antagoniste : la
# pauvrete, la maladie ou le temps tiennent ce role. Ce n'est pas un oubli.
RE_SANS_MECHANT = re.compile(
    r"(?:pas|aucun|aucune|sans|ni)\s+(?:de\s+|d'|un\s+)?"
    r"(?:m[ée]chant[e]?|antagoniste|vilain)",
    re.IGNORECASE,
)


def _normaliser_etiquette(texte: str) -> str:
    propre = _sans_accents(texte).lower().replace("’", "'")
    propre = re.sub(r"[^a-z0-9' ]", " ", propre)
    return re.sub(r"\s+", " ", propre).strip()


def _droite_si_etiquette(propre: str, cibles: list[str], tirets: bool = False) -> str | None:
    """Ce qui suit l'étiquette, si la ligne commence par l'une des `cibles`.

    « Ce qui change : … » ou, dans les packs plus récents, « Ce qui change — … ».
    Le tiret ne vaut que dans le corps d'une scène (`tirets`) : en tête de pack,
    « Durée — 70 à 90 s » ou un titre « X — Y » n'en sont pas des étiquettes.
    """
    for separateur in ((":", " — ", " – ") if tirets else (":",)):
        if separateur not in propre:
            continue
        gauche, droite = propre.split(separateur, 1)
        gauche_norm = _normaliser_etiquette(gauche)
        if any(gauche_norm.endswith(c) or c in gauche_norm for c in cibles):
            return droite
    return None


def _valeur_apres(texte: str, etiquette, tirets: bool = False) -> str:
    """Valeur d'une ligne 'Etiquette : valeur' (accents, emoji et abreges tolerants)."""
    cibles = [etiquette] if isinstance(etiquette, str) else list(etiquette)
    cibles = [_sans_accents(c).lower() for c in cibles]
    for ligne in texte.split("\n"):
        droite = _droite_si_etiquette(_nettoyer_ligne(ligne), cibles, tirets)
        if droite is not None:
            return droite.strip().strip("*").strip()
    return ""


RE_PARTIE_SUR = re.compile(
    r"\bPARTIE\s+(\d+)\s+(?:SUR|/)\s*(\d+)", re.IGNORECASE
)


def _phrase_autour(texte: str, position: int, limite: int = 180) -> str:
    """La phrase du document qui contient cette position, citée telle quelle."""
    debut = max(
        texte.rfind(". ", 0, position) + 1,
        texte.rfind("\n", 0, position) + 1,
    )
    fin = texte.find(".", position)
    fin = len(texte) if fin == -1 else fin + 1
    phrase = " ".join(texte[debut:fin].split()).strip()
    return phrase[: limite - 1] + "…" if len(phrase) > limite else phrase


def _parties_annoncees(texte: str) -> str:
    """« PARTIE 1 SUR 2 » posée seule en tête, sans deux-points."""
    m = RE_PARTIE_SUR.search(texte)
    return f"{m.group(1)} sur {m.group(2)}" if m else ""


def _valeur_tableau(texte: str, etiquette) -> str:
    """Valeur d'une étiquette posée seule sur sa ligne, la valeur juste en dessous.

    Le canevas de ChatGPT rend « Durée / 1 min 17 s » en deux lignes séparées
    plutôt qu'en « Durée : 1 min 17 s ». Sans ça l'en-tête reste vide alors que
    le renseignement est bien dans le document.
    """
    cibles = [etiquette] if isinstance(etiquette, str) else list(etiquette)
    cibles = [_sans_accents(c).lower() for c in cibles]
    lignes = texte.split("\n")
    for i, ligne in enumerate(lignes):
        propre = _nettoyer_ligne(ligne)
        if not propre or ":" in propre or len(propre) > 30:
            continue
        if _normaliser_etiquette(propre) not in cibles:
            continue
        for suite in lignes[i + 1 :]:
            valeur = _nettoyer_ligne(suite)
            if not valeur:
                continue
            if ":" in valeur or len(valeur) > 120:
                return ""
            return valeur.strip("*").strip()
        return ""
    return ""


def _secondes(texte: str) -> float | None:
    """« 1 min 17 s », « 77 s », « 1:17 » → 77. Rien d'inventé si c'est illisible."""
    if not texte:
        return None
    propre = _sans_accents(texte).lower()
    m = re.search(r"(\d+)\s*(?:min|m)\b[^\d]*(\d+)?", propre)
    if m:
        return float(m.group(1)) * 60 + float(m.group(2) or 0)
    m = re.match(r"\s*(\d+)\s*:\s*(\d{1,2})\s*$", propre)
    if m:
        return float(m.group(1)) * 60 + float(m.group(2))
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:s|sec|secondes?)\b", propre)
    if m:
        return float(m.group(1).replace(",", "."))
    m = re.search(r"(\d+(?:[.,]\d+)?)", propre)
    if m:
        return float(m.group(1).replace(",", "."))
    return None


def _bloc_multiligne(texte: str, etiquette, tirets: bool = False) -> str:
    """Comme _valeur_apres mais prolonge jusqu'à la prochaine etiquette/ligne vide."""
    cibles = [etiquette] if isinstance(etiquette, str) else list(etiquette)
    cibles = [_sans_accents(c).lower() for c in cibles]
    lignes = texte.split("\n")
    for i, ligne in enumerate(lignes):
        droite = _droite_si_etiquette(_nettoyer_ligne(ligne), cibles, tirets)
        if droite is None:
            continue
        morceaux = [droite.strip()]
        for suite in lignes[i + 1 :]:
            s = suite.strip()
            if not s:
                break
            if ":" in s and re.match(r"^[^:]{2,40}:", _nettoyer_ligne(s)):
                break
            if re.match(r"^\*\*", s) or s.startswith("SCÈNE") or s.startswith("SCENE"):
                break
            # « ⏱️ Grok — 6 secondes » : une ligne qui commence par un emoji ouvre
            # un autre champ, même sans deux-points.
            if re.match(r"^[^\w\s«\"'(\[*-]", s):
                break
            morceaux.append(s)
        return " ".join(m for m in morceaux if m).strip()
    return ""


RE_SCENE_TITRE = re.compile(
    # « 🎞️ Scène 1 — Le prix du test · 6 s · 00:00–00:06 » : emoji de tête accepté.
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*(?:[^\w\s]+\s*)?SC[ÈE]NE\s+(\d+)\s*[—–-]\s*(.*?)\s*(?:\*\*)?\s*$",
    re.IGNORECASE,
)
RE_PLANCHE_TITRE = re.compile(
    # « PLANCHE — X », « PLANCHE DE RÉFÉRENCE — X »,
    # « PROMPT PLANCHE OBJET — X », « PLANCHE DE RÉFÉRENCE OBJET — X »…
    # Le séparateur est un vrai tiret de titre (« — », « – » ou « - » entre
    # espaces), jamais le trait d'union d'un mot : « Planche de référence 3D
    # semi-réaliste, format… » est un prompt, pas un titre (10 novembre 2026).
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*[^\w\s]*\s*(?:PROMPT\s+)?PLANCHE"
    r"[^—–:]{0,40}?(?:\s+-\s+|\s*[—–]\s*)(.{1,80}?)\s*(?:\*\*)?\s*$",
    re.IGNORECASE,
)
RE_PROMPT_IMAGE = re.compile(
    r"^\s*(?:\*\*)?\s*[^\w\s]*\s*PROMPT\s+IMAGE\s*(?:\*\*)?\s*$", re.IGNORECASE
)
RE_VERSION_VIDEO = re.compile(
    # « 🎬 Version normale · 6 s » ou « 🎬 PROMPT GROK — Version normale · 6 s ».
    r"^\s*(?:\*\*)?\s*[^\w\s]*\s*(?:PROMPT\s+GROK\s*[—–-]\s*)?"
    r"Version\s+(normale|dynamique)\s*[:·]\s*(\d+)\s*s",
    re.IGNORECASE,
)
RE_DUREE_TITRE = re.compile(r"(\d+)\s*secondes?\s*$", re.IGNORECASE)
#: « Le prix du test · 6 s · 00:00–00:06 » : durée et minutage après le titre.
RE_DUREE_POINT = re.compile(r"\s*·\s*(\d+)\s*s\b.*$", re.IGNORECASE)
#: « Personnages — Rox, Pixel, Splash et Pablo. » quand le pack ne livre pas de planches.
RE_LISTE_PERSONNAGES = re.compile(r"^\s*[^\w\s]*\s*Personnages\s*[—–:-]\s*(.+?)\.?\s*$", re.IGNORECASE)
RE_REPLIQUE = re.compile(
    r"R[ÉE]PLIQUE\s+(\d+)\s*[—–-]\s*([^:(]+?)\s*(?:\(([^)]*)\))?\s*:\s*(.+)$",
    re.IGNORECASE,
)
RE_REPLIQUE_NOMMEE = re.compile(r"^([A-ZÀ-Ý][A-ZÀ-Ý' .-]{1,30})\s*[—–:-]\s*«\s*(.+?)\s*»\s*$")
RE_FIN_PACK = re.compile(
    r"^\s*(?:\*\*)?\s*(?:[^\w\s]*\s*)?(SCRIPT COMPLET|TEST DU PETIT COUSIN|"
    r"TABLEAU DE SCRIPT|Chaque scène a été livrée)",
    re.IGNORECASE,
)


# Intitulés de section : ce ne sont jamais des titres d'histoire.
SECTIONS = {
    "", "l'histoire", "histoire", "histoire complete", "standalone", "synopsis",
    "le synopsis", "en une phrase", "resume", "le resume", "pitch", "le pitch",
    "planches de reference", "personnages", "scenes", "script complet",
    "test du petit cousin", "fin du package",
}


#: Étiquettes qui annoncent VRAIMENT le titre. « Même histoire : mère qui
#  cache sa fille → … » contient le mot « histoire » mais c'est un déroulé :
#  pris pour le titre, il a été écrit en gros au milieu de la miniature
#  (projet 36, 12 novembre 2026). D'où l'égalité stricte, pas l'inclusion.
ETIQUETTES_TITRE = {
    "histoire", "titre", "titre de l'histoire", "nom de l'histoire", "titre de la video",
}


def _ressemble_a_un_titre(valeur: str) -> bool:
    """Court, sans flèches d'enchaînement, et pas une phrase terminée."""
    return bool(valeur) and len(valeur) <= 80 and "→" not in valeur and not (
        valeur.endswith((".", "…"))
    )


def _titre_etiquete(tete: str) -> str:
    for ligne in tete.split("\n"):
        propre = _nettoyer_ligne(ligne)
        if ":" not in propre:
            continue
        gauche, droite = propre.split(":", 1)
        if _normaliser_etiquette(gauche) in ETIQUETTES_TITRE:
            valeur = _entre_guillemets(droite.strip().strip("*").strip())
            if _ressemble_a_un_titre(valeur):
                return valeur
    return ""


def _entre_guillemets(valeur: str) -> str:
    """« Adaptation — « ELLE AVAIT HONTE DE SA FILLE » » → le titre seul."""
    m = re.search(r"«\s*(.+?)\s*»", valeur)
    return m.group(1) if m else valeur


def _titre_par_defaut(texte: str) -> str:
    """Titre repris de la premiere ligne, seulement si ça en a vraiment l'air.

    Un titre est court et ne finit pas par un point. Une phrase de synopsis ne
    doit jamais être prise pour un titre : mieux vaut n'en proposer aucun.
    """
    examinees = 0
    for ligne in texte.split("\n"):
        propre = _nettoyer_ligne(ligne)
        if not propre:
            continue
        examinees += 1
        if examinees > 5:
            return ""
        if ":" in propre:
            continue
        if RE_SCENE_TITRE.match(ligne) or RE_PLANCHE_TITRE.match(ligne):
            continue
        normalise = _normaliser_etiquette(propre)
        if normalise in SECTIONS or normalise.startswith("phase "):
            continue
        propre = _entre_guillemets(propre)
        if not _ressemble_a_un_titre(propre):
            continue  # c'est une phrase, pas un titre
        return propre
    return ""


def _bloc_apres_titre(texte: str, titres: list[str], limite: int = 1500) -> str:
    """Contenu placé sous un titre sans deux-points, par exemple « 📌 L'histoire »."""
    cibles = [_sans_accents(t).lower() for t in titres]
    lignes = texte.split("\n")
    for i, ligne in enumerate(lignes):
        if ":" in ligne:
            continue
        if _normaliser_etiquette(_nettoyer_ligne(ligne)) not in cibles:
            continue
        morceaux: list[str] = []
        for suite in lignes[i + 1 :]:
            depouille = suite.strip()
            if depouille.startswith(("📌", "📈", "🎨", "🎬", "🔊", "🧾", "🎭")):
                break
            if RE_SCENE_TITRE.match(suite) or RE_PLANCHE_TITRE.match(suite):
                break
            morceaux.append(depouille)
            if sum(len(m) for m in morceaux) > limite:
                break
        return "\n".join(morceaux).strip()
    return ""


def detecter_type(texte: str) -> str:
    haut = texte[:12000]
    score_meta = sum(1 for m in MARQUEURS_METAPROMPT if m in texte)
    score_pack = sum(1 for m in MARQUEURS_PACK if m in texte)
    a_scene_reelle = bool(
        re.search(r"SC[ÈE]NE\s+1\s*[—–-]", texte, re.IGNORECASE)
    ) and bool(re.search(r"PROMPT\s+IMAGE", texte, re.IGNORECASE))
    if score_meta >= 2 and not a_scene_reelle:
        return "metaprompt"
    if "METAPROMPT V6" in haut and not a_scene_reelle:
        return "metaprompt"
    if a_scene_reelle or score_pack >= 3:
        return "pack"
    return "inconnu"


# --------------------------------------------------------------------------
# Decoupage du document
# --------------------------------------------------------------------------

def _segmenter(texte: str) -> tuple[str, list[tuple[int, str, str]], str]:
    """Retourne (zone_entete_personnages, [(numero, titre, corps)], queue)."""
    lignes = texte.split("\n")
    debuts: list[tuple[int, int, str]] = []
    for i, ligne in enumerate(lignes):
        m = RE_SCENE_TITRE.match(ligne)
        if m:
            debuts.append((i, int(m.group(1)), m.group(2).strip()))

    if not debuts:
        return texte, [], ""

    tete = "\n".join(lignes[: debuts[0][0]])
    scenes: list[tuple[int, str, str]] = []
    queue = ""
    for idx, (ligne_i, numero, titre) in enumerate(debuts):
        fin = debuts[idx + 1][0] if idx + 1 < len(debuts) else len(lignes)
        corps_lignes = lignes[ligne_i + 1 : fin]
        # Couper la queue du package (tableau de script, test du petit cousin).
        for j, cl in enumerate(corps_lignes):
            if RE_FIN_PACK.match(cl):
                if idx + 1 >= len(debuts):
                    queue = "\n".join(corps_lignes[j:])
                corps_lignes = corps_lignes[:j]
                break
        scenes.append((numero, titre, "\n".join(corps_lignes)))
    return tete, scenes, queue


#: « P2 — LA VOITURE VOLANTE D'ORANGINO » juste au-dessus d'un prompt de planche.
RE_CODE_EN_TETE = re.compile(r"^\s*(?:\*\*)?\s*([RP]\d+)\s*[—–-]\s*\S")
#: Ligne de tableau « R1<tab>Orangino, orange, 26 ans<tab>… » (ou séparée par « | »).
RE_LIGNE_TABLEAU = re.compile(r"^\s*\|?\s*([RP]\d+)\s*(?:\t|\|)+\s*([^,\t|]+)")


def _code_juste_avant(zone: list[str], pris: set[str]) -> str:
    """Le code annoncé sur la dernière ligne « P2 — … » au-dessus de la planche."""
    for ligne in reversed(zone):
        m = RE_CODE_EN_TETE.match(_nettoyer_ligne(ligne))
        if m:
            code = m.group(1).upper()
            return "" if code in pris else code
    return ""


def _personnages_du_tableau(lignes: list[str], res: ResultatImport) -> None:
    """Personnages déjà connus, listés en tableau, sans planche dans ce pack.

    Leur image vit ailleurs (chat ChatGPT de la série, ou import à la main) :
    on les crée pour que « R3 Courgetina » dans une scène trouve son personnage.
    """
    pris = {p["code"] for p in res.personnages}
    for ligne in lignes:
        m = RE_LIGNE_TABLEAU.match(ligne)
        if not m or m.group(1).upper() in pris:
            continue
        code, nom = m.group(1).upper(), m.group(2).strip()
        pris.add(code)
        res.personnages.append({
            "code": code, "surnom": nom.upper(), "etat": "UNIQUE", "espece": "",
            "role": "", "age": "", "genre": "", "voix": "", "etiquette_grok": "",
            "lien": "", "prompt_planche": "",
        })
        res.avertir(
            "manquant",
            f"{code} {nom.upper()}",
            "Le pack ne livre pas sa planche : importe son image de référence dans "
            "l'onglet Personnages, ou indique qu'elle est déjà dans le chat ChatGPT "
            "(onglet Générations).",
        )


def _personnages_sans_planche(lignes: list[str], res: ResultatImport) -> None:
    """Un épisode suivant ne relivre pas les planches : il nomme ses personnages.

    On crée chaque personnage sans prompt de planche. Son image de référence se
    réimporte à la main (onglet Personnages) ; d'ici là, les scènes qui le citent
    sont refusées avant tout envoi, comme pour toute planche absente.
    """
    for ligne in lignes:
        m = RE_LISTE_PERSONNAGES.match(ligne)
        if m:
            # « Rox, Coco, Kiki, Luna et Pixel. Shadow apparaît dans l'archive. » :
            # la liste s'arrête à la première phrase (17 novembre 2026, épisode 9 :
            # le 5ᵉ personnage s'appelait « PIXEL. SHADOW APPARAÎT DANS L'ARCHIVE »,
            # et Pixel n'était reconnu dans aucune scène).
            liste = re.split(r"\.\s+(?=[A-ZÀ-Ý])", m.group(1), maxsplit=1)[0]
            noms = [n.strip().strip(".").strip() for n in re.split(r",| et ", liste)]
            noms = [n for n in noms if n]
            break
    else:
        res.avertir(
            "manquant",
            "personnages",
            "Aucune planche de référence trouvée. Vérifie que le pack contient les "
            "blocs 'PLANCHE DE RÉFÉRENCE — <SURNOM>' ou une ligne "
            "« Personnages — A, B et C ».",
        )
        return
    for i, nom in enumerate(noms, start=1):
        res.personnages.append({
            "code": f"R{i}", "surnom": nom.upper(), "etat": "UNIQUE", "espece": "",
            "role": "", "age": "", "genre": "", "voix": "", "etiquette_grok": "",
            "lien": "", "prompt_planche": "",
        })
        res.avertir(
            "manquant",
            f"R{i} {nom.upper()}",
            "Le pack ne livre pas sa planche : importe son image de référence dans "
            "l'onglet Personnages. Sans elle, ses scènes sont refusées avant tout envoi.",
        )


def _extraire_personnages(tete: str, res: ResultatImport) -> None:
    lignes = tete.split("\n")
    planches: list[tuple[int, str]] = []
    for i, ligne in enumerate(lignes):
        m = RE_PLANCHE_TITRE.match(ligne)
        if m:
            planches.append((i, _nettoyer_ligne(m.group(1))))

    if not planches:
        if any(RE_LIGNE_TABLEAU.match(l) for l in lignes):
            _personnages_du_tableau(lignes, res)
        else:
            _personnages_sans_planche(lignes, res)
        return

    codes_pris: set[str] = set()
    codes_explicites = 0
    for idx, (ligne_i, nom_complet) in enumerate(planches):
        surnom, etat = _separer_etat(nom_complet)

        # Bloc d'infos : du debut (ou de la planche precedente) jusqu'a ce titre.
        debut_infos = 0 if idx == 0 else planches[idx - 1][0] + 1
        zone_avant = lignes[debut_infos:ligne_i]
        infos = _isoler_bloc_personnage(zone_avant, surnom)

        # Prompt de planche : apres le titre, jusqu'au bloc du personnage suivant.
        fin_prompt = len(lignes)
        if idx + 1 < len(planches):
            fin_prompt = _debut_bloc_suivant(
                lignes, ligne_i + 1, planches[idx + 1][0], planches[idx + 1][1]
            )
        prompt = "\n".join(lignes[ligne_i + 1 : fin_prompt]).strip()

        entete = _ligne_entete_personnage(infos, surnom)
        espece, role, age, genre = _decouper_entete(entete)

        code = _code_juste_avant(zone_avant, codes_pris) or _code_explicite(infos, surnom)
        if code:
            codes_explicites += 1
        else:
            code = f"R{idx + 1}"
        base, rang = code, 1
        while code in codes_pris:
            rang += 1
            code = f"{base}{chr(ord('A') + rang - 1)}"
        codes_pris.add(code)

        perso = {
            "code": code,
            "surnom": surnom,
            "etat": etat,
            "espece": espece,
            "role": role,
            "age": age,
            "genre": genre,
            "voix": _valeur_apres(infos, "voix"),
            "etiquette_grok": _valeur_apres(infos, "étiquette grok"),
            "lien": _valeur_apres(infos, "lien avec le heros"),
            "prompt_planche": prompt,
        }
        res.personnages.append(perso)

        cible = f"{perso['code']} {surnom}"
        if not prompt:
            res.avertir("manquant", cible, "Prompt de planche de référence vide.")
        elif len(prompt.split()) < 60:
            res.avertir(
                "suspect",
                cible,
                f"Prompt de planche très court ({len(prompt.split())} mots) : le V6 "
                "en demande 200 à 300.",
            )
        if perso["code"].upper().startswith("R"):
            if not perso["voix"]:
                res.avertir("manquant", cible, "Verrou de voix absent.")
            if not perso["etiquette_grok"]:
                res.avertir("manquant", cible, "Étiquette Grok absente.")

    if codes_explicites < len(planches):
        res.avertir(
            "info",
            "personnages",
            f"{len(planches) - codes_explicites} identifiant(s) attribué(s) "
            "automatiquement dans l'ordre des planches. Corrige-les si ton pack "
            "utilise d'autres codes.",
        )
    _personnages_du_tableau(lignes, res)

def _separer_etat(nom: str) -> tuple[str, str]:
    m = re.match(r"^(.*?)\s+(AVANT|APR[ÈE]S)\s*$", nom, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).upper().replace("APRES", "APRÈS")
    return nom.strip(), "UNIQUE"


RE_LIGNE_CODE = re.compile(r"^\(?\s*([RP]\d+)\s*\)?\s*[—–:.-]\s*(.+)$")
RE_CODE_ISOLE = re.compile(r"\b([RP]\d+)\b")


def _isoler_bloc_personnage(zone: list[str], surnom: str) -> str:
    """Ne garde que la fin de la zone, à partir de la ligne d'entete du personnage.

    L'entete s'écrit « PHYSALINO — … » ou « R1 — PHYSALINO » selon les packs.
    """
    cible = _sans_accents(surnom).upper()
    depart = 0
    for i, ligne in enumerate(zone):
        propre = _sans_accents(_nettoyer_ligne(ligne)).upper()
        if propre.startswith(cible):
            depart = i
            break
        m = RE_LIGNE_CODE.match(propre)
        if m and m.group(2).strip().startswith(cible):
            depart = i
            break
    return "\n".join(zone[depart:])


def _code_explicite(infos: str, surnom: str) -> str:
    """Récupère « R1 » (« R1 — PHYSALINO ») ou « P1 » (« Objet de référence — P1 : … »)."""
    cible = _sans_accents(surnom).upper()
    courtes = [
        _sans_accents(_nettoyer_ligne(l)).upper()
        for l in infos.split("\n")
        if len(_nettoyer_ligne(l)) <= 120
    ]
    for propre in courtes:
        m = RE_LIGNE_CODE.match(propre)
        if m and m.group(2).strip().startswith(cible):
            return m.group(1).upper()
    for propre in courtes:
        m = RE_CODE_ISOLE.search(propre)
        if m:
            return m.group(1).upper()
    return ""


def _debut_bloc_suivant(
    lignes: list[str], depart: int, limite: int, surnom_suivant: str
) -> int:
    """Trouve ou commence le bloc d'infos du personnage suivant."""
    surnom, _ = _separer_etat(surnom_suivant)
    cible = _sans_accents(surnom).upper()
    for i in range(depart, limite):
        brut = lignes[i]
        propre = _sans_accents(_nettoyer_ligne(brut)).upper()
        if propre.startswith(cible) and "—" in brut:
            return i
        if propre.startswith("VOIX (VERROU)") or brut.strip().startswith("🔒"):
            return max(depart, i - 1)
        # Entete courte du bloc suivant : « R2 — BROCOLANDRO », « … — P1 : … ».
        if (
            len(propre) <= 120
            and RE_CODE_ISOLE.search(propre)
            and ("—" in brut or ":" in brut)
        ):
            return i
    return limite


def _ligne_entete_personnage(infos: str, surnom: str) -> str:
    cible = _sans_accents(surnom).upper()
    for ligne in infos.split("\n"):
        propre = _nettoyer_ligne(ligne)
        if _sans_accents(propre).upper().startswith(cible) and "—" in propre:
            return propre
    return ""


def _decouper_entete(entete: str) -> tuple[str, str, str, str]:
    """'POMITO — Pomme, le patron, [ÂGE : adulte], [GENRE : homme]'."""
    if not entete:
        return "", "", "", ""
    apres = entete.split("—", 1)[1] if "—" in entete else entete
    age = ""
    genre = ""
    m = re.search(r"\[?\s*[ÂA]GE\s*:\s*([^\],]+)", apres, re.IGNORECASE)
    if m:
        age = m.group(1).strip().strip("]").strip()
    m = re.search(r"\[?\s*GENRE\s*:\s*([^\],]+)", apres, re.IGNORECASE)
    if m:
        genre = m.group(1).strip().strip("]").strip()
    sans_crochets = re.sub(r"\[[^\]]*\]", "", apres)
    if not age:
        m = re.search(r"\b(\d{1,3})\s*ans\b", sans_crochets, re.IGNORECASE)
        if m:
            age = f"{m.group(1)} ans"
    if not genre:
        m = re.search(
            r"\b(homme|femme|gar[çc]on|fille|adolescente|adolescent)\b",
            sans_crochets,
            re.IGNORECASE,
        )
        if m:
            genre = m.group(1).lower()
    morceaux = [p.strip() for p in sans_crochets.split(",") if p.strip()]
    espece = morceaux[0] if morceaux else ""
    reste = []
    for part in morceaux[1:]:
        compare = part.strip().rstrip(".").lower()
        if (age and compare == age.lower()) or (genre and compare == genre.lower()):
            continue
        reste.append(part.strip())
    role = ", ".join(reste)
    return espece, role, age, genre


def _extraire_scene(numero: int, titre_brut: str, corps: str, res: ResultatImport) -> dict[str, Any]:
    emoji = ""
    titre = titre_brut
    duree: int | None = None

    m = RE_DUREE_TITRE.search(titre_brut) or RE_DUREE_POINT.search(titre_brut)
    if m:
        duree = int(m.group(1))
        titre = titre_brut[: m.start()].rstrip(" —–-").strip()
    # Separer l'emoji de tete du titre.
    m = re.match(r"^\s*([^\w\s(]+)\s+(.*)$", titre)
    if m:
        emoji, titre = m.group(1).strip(), m.group(2).strip()

    duree_grok = _valeur_apres(corps, ALIAS["duree_grok"], tirets=True)
    if duree_grok:
        m = re.search(r"(\d+)", duree_grok)
        if m and duree is None:
            duree = int(m.group(1))
        elif m and duree is not None and int(m.group(1)) != duree:
            res.avertir(
                "suspect",
                f"scene {numero}",
                f"Durée du titre ({duree} s) differente de la ligne Grok "
                f"({m.group(1)} s).",
            )

    lignes = corps.split("\n")
    prompt_image = ""
    prompt_video = ""
    version_video = ""
    i_img = i_vid = None
    for i, ligne in enumerate(lignes):
        if i_img is None and RE_PROMPT_IMAGE.match(ligne):
            i_img = i
        mv = RE_VERSION_VIDEO.match(ligne)
        if mv and i_vid is None:
            i_vid = i
            version_video = mv.group(1).lower()
            if duree is None:
                duree = int(mv.group(2))

    if i_img is not None:
        fin = i_vid if i_vid is not None else len(lignes)
        prompt_image = "\n".join(lignes[i_img + 1 : fin]).strip()
    if i_vid is not None:
        prompt_video = "\n".join(lignes[i_vid + 1 :]).strip()

    dialogues = _extraire_dialogues(prompt_video)
    nb_mots = sum(len(d["texte"].split()) for d in dialogues)

    refs_brut = _valeur_apres(corps, ALIAS["refs"], tirets=True)
    scene = {
        "numero": numero,
        "titre": titre,
        "emoji": emoji,
        "duree": duree,
        "attache": _valeur_apres(corps, "attache", tirets=True),
        "inventaire": _bloc_multiligne(corps, ALIAS["inventaire"], tirets=True),
        "ce_qui_change": _valeur_apres(corps, "ce qui change", tirets=True),
        "refs_brut": refs_brut,
        "refs_noms": _decouper_refs(refs_brut),
        "refs_codes": [],
        "prompt_image": prompt_image,
        "prompt_video": prompt_video,
        "version_video": version_video,
        "dialogues": dialogues,
        "nb_repliques": len(dialogues),
        "nb_mots": nb_mots,
    }

    cible = f"scene {numero}"
    if duree is None:
        res.avertir("manquant", cible, "Durée introuvable (titre et ligne Grok muets).")
    elif duree not in reglages.durees_grok():
        res.avertir(
            "suspect",
            cible,
            f"Durée {duree} s hors des valeurs acceptées par Grok "
            f"{reglages.durees_grok()}.",
        )
    if not prompt_image:
        res.avertir("manquant", cible, "Prompt image absent.")
    if not prompt_video:
        res.avertir("manquant", cible, "Prompt vidéo absent.")
    if not scene["inventaire"]:
        res.avertir("manquant", cible, "Ligne 'Inventaire de l'image de départ' absente.")
    if not scene["ce_qui_change"]:
        res.avertir("manquant", cible, "Ligne 'Ce qui change' absente.")
    if not refs_brut:
        res.avertir(
            "manquant",
            cible,
            "Ligne 'Références à charger dans ChatGPT' absente : impossible de savoir "
            "quelles planches joindre.",
        )
    if not dialogues:
        res.avertir("suspect", cible, "Aucune réplique numérotée détectée dans le prompt vidéo.")
    elif duree in config.CALIBRAGE_MOTS:
        bas, haut = config.CALIBRAGE_MOTS[duree]
        if not (bas <= nb_mots <= haut):
            res.avertir(
                "info",
                cible,
                f"{nb_mots} mots de dialogue pour {duree} s (calibrage V6 : {bas}-{haut}).",
            )
    return scene


def _decouper_refs(refs_brut: str) -> list[dict[str, str]]:
    if not refs_brut:
        return []
    # « NAVETINA. Les deux camarades secondaires sont décrits dans le prompt » :
    # la liste s'arrête à la première phrase, le reste est un commentaire.
    refs_brut = re.split(r"\.\s+(?=[A-ZÀ-Ý])", refs_brut, maxsplit=1)[0]
    morceaux = re.split(r"[,;·]| et ", refs_brut)
    sortie: list[dict[str, str]] = []
    for m in morceaux:
        nom = m.strip().strip(".").strip()
        if not nom:
            continue
        etat = "UNIQUE"
        me = re.search(r"[—–-]\s*(AVANT|APR[ÈE]S)\s*$", nom, re.IGNORECASE)
        if me:
            etat = me.group(1).upper().replace("APRES", "APRÈS")
            nom = nom[: me.start()].strip()
        nom = re.sub(r"\s*[—–-]\s*$", "", nom).strip()
        # « Pixel(1).PNG », « La planche de Rox » : le fichier de référence, pas le nom.
        nom = re.sub(r"\.(png|jpe?g|webp)$", "", nom, flags=re.IGNORECASE)
        nom = re.sub(r"\s*\(\d+\)$", "", nom)
        nom = re.sub(r"^(?:la\s+)?planche\s+(?:de\s+|d')", "", nom, flags=re.IGNORECASE).strip()
        # Certains packs ecrivent « R1 Physalino » ou « (R1) Physalino ».
        code = ""
        mc = re.match(r"^\(?\s*([RP]\d+)\s*\)?\s*[—–:.-]?\s*(.*)$", nom, re.IGNORECASE)
        if mc:
            code = mc.group(1).upper()
            nom = mc.group(2).strip() or code
        if nom or code:
            sortie.append({"nom": nom, "etat": etat, "code": code})
    return sortie


def _extraire_dialogues(prompt_video: str) -> list[dict[str, Any]]:
    dialogues: list[dict[str, Any]] = []
    for ligne in prompt_video.split("\n"):
        m = RE_REPLIQUE.search(ligne.strip())
        if not m:
            continue
        texte = m.group(4).strip()
        guillemets = re.findall(r'"([^"]+)"', texte)
        if guillemets:
            texte = " ".join(guillemets).strip()
        else:
            texte = texte.strip('"').strip()
        dialogues.append(
            {
                "numero": int(m.group(1)),
                "etiquette": m.group(2).strip(),
                "indice": (m.group(3) or "").strip(),
                "texte": texte,
            }
        )
    if not dialogues:
        # Packs plus courts : « PIXEL — « Ils veulent ton micro. » », sans numéro.
        for ligne in prompt_video.split("\n"):
            m = RE_REPLIQUE_NOMMEE.match(ligne.strip())
            if m:
                dialogues.append({"numero": len(dialogues) + 1, "etiquette": m.group(1).strip(),
                                  "indice": "", "texte": m.group(2).strip()})
    dialogues.sort(key=lambda d: d["numero"])
    return dialogues


def _lier_refs(res: ResultatImport) -> None:
    index: dict[str, dict[str, Any]] = {}
    for p in res.personnages:
        cle = _sans_accents(p["surnom"]).upper()
        index.setdefault(cle, p)
        index[f"{cle}|{p['etat']}"] = p

    par_code = {p["code"].upper(): p for p in res.personnages}
    for scene in res.scenes:
        codes: list[str] = []
        for ref in scene["refs_noms"]:
            cle = _sans_accents(ref["nom"]).upper()
            perso = (
                par_code.get((ref.get("code") or "").upper())
                or index.get(f"{cle}|{ref['etat']}")
                or index.get(cle)
            )
            if perso:
                codes.append(perso["code"])
            else:
                etiquette = " ".join(x for x in (ref.get("code"), ref["nom"]) if x)
                res.avertir(
                    "manquant",
                    f"scène {scene['numero']}",
                    f"La référence « {etiquette} » ne correspond à aucune planche du "
                    "pack. Aucune image ne sera jointe pour ce personnage.",
                )
        scene["refs_codes"] = codes


def _extraire_miniature(texte: str) -> str:
    lignes = texte.split("\n")
    for i, ligne in enumerate(lignes):
        propre = _sans_accents(_nettoyer_ligne(ligne)).upper()
        if "MINIATURE" in propre and len(propre) < 80:
            bloc = []
            for suite in lignes[i + 1 :]:
                if RE_SCENE_TITRE.match(suite) or RE_PLANCHE_TITRE.match(suite):
                    break
                bloc.append(suite)
                if len("\n".join(bloc)) > 4000:
                    break
            return "\n".join(bloc).strip()
    return ""


# --------------------------------------------------------------------------
# Entree publique
# --------------------------------------------------------------------------

def analyser(texte: str) -> ResultatImport:
    brut = texte or ""
    res = ResultatImport(brut=brut)
    if not brut.strip():
        res.avertir("manquant", "document", "Document vide.")
        return res

    nettoye = _retirer_blocs_code(brut.replace("\r\n", "\n").replace("\r", "\n"))

    # JSON ?
    depouille = nettoye.strip()
    if depouille.startswith("{"):
        try:
            return _depuis_json(json.loads(depouille), brut)
        except ValueError:
            res.avertir(
                "suspect",
                "document",
                "Le document commence par '{' mais n'est pas un JSON valide : "
                "il a été lu comme du texte.",
            )

    res.type_document = detecter_type(nettoye)
    if res.type_document == "metaprompt":
        res.avertir(
            "suspect",
            "document",
            "Ce document est le METAPROMPT V6 lui-même, pas un pack d'histoire. "
            "Colle-le dans l'onglet Métaprompt, puis importe ici le package produit "
            "par ChatGPT.",
        )
        return res

    # L'en-tete se lit AVANT la premiere scene : sinon la ligne « Durée Grok : 6 s »
    # d'une scene serait prise pour la duree annoncee de l'episode.
    tete, segments, queue = _segmenter(nettoye)

    res.titre = _titre_etiquete(tete)
    res.moteur = _valeur_apres(tete, "moteur")
    res.univers = _valeur_apres(tete, ["univers", "genre"]) or _valeur_tableau(
        tete, ["univers", "genre"]
    )
    res.parties = _valeur_apres(tete, "parties") or _parties_annoncees(tete)
    res.cliffhanger = _valeur_apres(nettoye, "cliffhanger")
    res.une_phrase = _valeur_apres(tete, ALIAS["une_phrase"])
    res.regle_mechant = _valeur_apres(
        tete, ["la règle du méchant", "regle du mechant", "première réplique"]
    )
    res.menace_evolutive = _valeur_apres(tete, ALIAS["menace"])
    res.prix_final = _valeur_apres(tete, ALIAS["prix_final"])
    res.replique_tiktok = _valeur_apres(nettoye, "réplique son tiktok")

    duree = _valeur_apres(tete, "duree") or _valeur_tableau(tete, "duree")
    res.duree_annoncee = _secondes(duree)
    # « 77 secondes · 10 scènes » dit le nombre directement ; sinon une ligne
    # « Scènes : … » (dont le premier chiffre n'est pas toujours le compte).
    direct = re.search(r"\b(\d+)\s*sc[èe]nes\b", tete, re.IGNORECASE)
    nbs = direct.group(1) if direct else (
        _valeur_apres(tete, ["scenes", "production"])
        or _valeur_tableau(tete, ["scenes", "nombre de scenes"])
    )
    m = re.search(r"(\d+)", nbs)
    if m:
        res.nb_scenes_annonce = int(m.group(1))

    if not res.une_phrase:
        res.une_phrase = _bloc_apres_titre(
            nettoye, ["en une phrase", "l'histoire", "synopsis", "le synopsis"]
        )
    _extraire_personnages(tete, res)

    if not segments:
        res.avertir(
            "manquant",
            "scenes",
            "Aucune scène détectée. Les titres doivent ressembler a "
            "'SCÈNE 1 — 🏡 TITRE — 6 secondes'.",
        )
    vus: set[int] = set()
    for numero, titre, corps in segments:
        if numero in vus:
            res.avertir("suspect", f"scene {numero}", "Scène livree deux fois : doublon ignore.")
            continue
        vus.add(numero)
        res.scenes.append(_extraire_scene(numero, titre, corps, res))
    res.scenes.sort(key=lambda s: s["numero"])

    attendus = set(range(1, len(res.scenes) + 1))
    if vus and vus != attendus:
        manquants = sorted(attendus - vus)
        res.avertir(
            "manquant",
            "scenes",
            f"Numerotation non continue. Numeros absents : {manquants}.",
        )

    _lier_refs(res)
    res.instructions_miniature = _extraire_miniature(queue or nettoye)

    if not res.titre:
        deduit = _titre_par_defaut(nettoye)
        if deduit:
            res.titre = deduit
            res.avertir(
                "info",
                "entête",
                f"Pas de ligne « 🎬 HISTOIRE : … » : le titre « {deduit} » a été repris "
                "de la première ligne du document. Corrige-le si besoin.",
            )
        else:
            res.avertir(
                "info",
                "entête",
                "Aucun titre reconnu dans le pack : le nom du projet est conservé. "
                "Ajoute une ligne « 🎬 HISTOIRE : … » en tête si tu veux qu'il soit "
                "repris automatiquement.",
            )
    if not res.regle_mechant:
        declare = RE_SANS_MECHANT.search(nettoye)
        if declare:
            res.avertir(
                "info",
                "entête",
                "Pas de règle du méchant, et c'est voulu : le pack écrit « "
                + _phrase_autour(nettoye, declare.start())
                + " ». Rien à corriger.",
            )
        else:
            res.avertir(
                "manquant",
                "entête",
                "Règle du méchant absente : ni « 📌 LA RÈGLE DU MÉCHANT : … » ni "
                "« Première réplique : … » dans l'en-tête. Si l'histoire n'a pas "
                "d'antagoniste, écris-le dans le pack et ce point disparaîtra. "
                "Si tu as collé le pack à partir du synopsis, reprends le "
                "copier-coller un peu plus haut.",
            )
    if not res.instructions_miniature:
        res.avertir(
            "info",
            "miniature",
            "Aucune instruction de miniature dans le pack : la miniature utilisera "
            "ton modèle enregistré (titre + badge de partie + personnages). "
            "Tu peux le remplacer dans l'Atelier avant de la lancer.",
        )

    duree_calc = res.duree_calculee
    if res.duree_annoncee and abs(duree_calc - res.duree_annoncee) > 1:
        res.avertir(
            "suspect",
            "duree",
            f"Somme des scènes = {duree_calc:g} s, en-tête annonce "
            f"{res.duree_annoncee:g} s.",
        )
    # Pas de fourchette imposée : un épisode peut avoir 8 comme 15 scènes. On
    # ne contrôle que la cohérence du pack avec lui-même (durée et nombre annoncés).
    if res.nb_scenes_annonce and res.nb_scenes_annonce != len(res.scenes):
        res.avertir(
            "suspect",
            "scenes",
            f"{len(res.scenes)} scènes lues, {res.nb_scenes_annonce} annoncees.",
        )
    return res


def _depuis_json(donnees: dict[str, Any], brut: str) -> ResultatImport:
    res = ResultatImport(brut=brut, type_document="pack")
    for champ in (
        "titre",
        "moteur",
        "univers",
        "parties",
        "cliffhanger",
        "une_phrase",
        "regle_mechant",
        "menace_evolutive",
        "prix_final",
        "replique_tiktok",
        "instructions_miniature",
    ):
        setattr(res, champ, str(donnees.get(champ) or ""))
    if donnees.get("duree_annoncee"):
        res.duree_annoncee = float(donnees["duree_annoncee"])
    if donnees.get("nb_scenes_annonce"):
        res.nb_scenes_annonce = int(donnees["nb_scenes_annonce"])

    for i, p in enumerate(donnees.get("personnages") or []):
        res.personnages.append(
            {
                "code": p.get("code") or f"R{i + 1}",
                "surnom": p.get("surnom") or p.get("nom") or f"Personnage {i + 1}",
                "etat": (p.get("etat") or "UNIQUE").upper(),
                "espece": p.get("espece") or "",
                "role": p.get("role") or "",
                "age": p.get("age") or "",
                "genre": p.get("genre") or "",
                "voix": p.get("voix") or "",
                "etiquette_grok": p.get("etiquette_grok") or "",
                "lien": p.get("lien") or "",
                "prompt_planche": p.get("prompt_planche") or "",
            }
        )
    for i, s in enumerate(donnees.get("scenes") or []):
        dialogues = s.get("dialogues") or []
        if dialogues and isinstance(dialogues[0], str):
            dialogues = [
                {"numero": n + 1, "etiquette": "", "indice": "", "texte": t}
                for n, t in enumerate(dialogues)
            ]
        scene = {
            "numero": int(s.get("numero") or i + 1),
            "titre": s.get("titre") or "",
            "emoji": s.get("emoji") or "",
            "duree": int(s["duree"]) if s.get("duree") else None,
            "attache": s.get("attache") or "",
            "inventaire": s.get("inventaire") or "",
            "ce_qui_change": s.get("ce_qui_change") or "",
            "refs_brut": s.get("refs_brut") or ", ".join(s.get("refs") or []),
            "refs_noms": [
                {"nom": n, "etat": "UNIQUE"} for n in (s.get("refs") or [])
            ],
            "refs_codes": s.get("refs_codes") or [],
            "prompt_image": s.get("prompt_image") or "",
            "prompt_video": s.get("prompt_video") or "",
            "version_video": (s.get("version_video") or "").lower(),
            "dialogues": dialogues,
            "nb_repliques": len(dialogues),
            "nb_mots": sum(len(str(d.get("texte", "")).split()) for d in dialogues),
        }
        res.scenes.append(scene)
        if not scene["prompt_image"]:
            res.avertir("manquant", f"scene {scene['numero']}", "Prompt image absent.")
        if not scene["prompt_video"]:
            res.avertir("manquant", f"scene {scene['numero']}", "Prompt vidéo absent.")
        if scene["duree"] is None:
            res.avertir("manquant", f"scene {scene['numero']}", "Durée absente.")
    if not res.scenes:
        res.avertir("manquant", "scenes", "Le JSON ne contient aucune scène.")
    if not res.personnages:
        res.avertir("manquant", "personnages", "Le JSON ne contient aucun personnage.")
    if not res.refs_deja_liees():
        _lier_refs(res)
    return res


def _refs_deja_liees(self: ResultatImport) -> bool:
    return any(s.get("refs_codes") for s in self.scenes)


ResultatImport.refs_deja_liees = _refs_deja_liees  # type: ignore[attr-defined]
