"""Accès SQLite : schema, migrations simples et aides de requete."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import config

_local = threading.local()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS projets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nom TEXT NOT NULL,
    titre TEXT,
    statut TEXT NOT NULL DEFAULT 'brouillon',
    type_histoire TEXT NOT NULL DEFAULT 'fruits',
    dossier TEXT NOT NULL,
    moteur TEXT,
    univers TEXT,
    parties TEXT,
    cliffhanger TEXT,
    une_phrase TEXT,
    regle_mechant TEXT,
    menace_evolutive TEXT,
    prix_final TEXT,
    replique_tiktok TEXT,
    duree_annoncee REAL,
    nb_scenes_annonce INTEGER,
    instructions_miniature TEXT,
    titre_miniature TEXT,
    miniature_path TEXT,
    video_conforme INTEGER,
    video_ecart REAL,
    export_path TEXT,
    srt_path TEXT,
    musique_path TEXT,
    musique_volume REAL DEFAULT 0.15,
    vitesse REAL DEFAULT 1.0,
    export_largeur INTEGER DEFAULT 1080,
    export_hauteur INTEGER DEFAULT 1920,
    duree_cible_min REAL DEFAULT 70.0,
    duree_cible_max REAL DEFAULT 80.0,
    grok_serie TEXT,
    grok_episode INTEGER DEFAULT 1,
    grok_chat_url TEXT,
    grok_regles_installees INTEGER DEFAULT 0,
    grok_profil_regles TEXT,
    grok_serie_annoncee_url TEXT,
    grok_serie_annoncee INTEGER DEFAULT 0,
    pipeline INTEGER DEFAULT 0,
    pipeline_mode TEXT,
    chatgpt_chat_url TEXT,
    valide INTEGER DEFAULT 0,
    archive INTEGER DEFAULT 0,
    pack_brut TEXT,
    avertissements TEXT,
    cree_le TEXT NOT NULL,
    maj_le TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS personnages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    projet_id INTEGER NOT NULL REFERENCES projets(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    surnom TEXT NOT NULL,
    etat TEXT NOT NULL DEFAULT 'UNIQUE',
    espece TEXT,
    role TEXT,
    age TEXT,
    genre TEXT,
    voix TEXT,
    etiquette_grok TEXT,
    lien TEXT,
    prompt_planche TEXT,
    ref_path TEXT,
    ref_origine TEXT,
    ordre INTEGER DEFAULT 0,
    UNIQUE (projet_id, code)
);

CREATE TABLE IF NOT EXISTS scenes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    projet_id INTEGER NOT NULL REFERENCES projets(id) ON DELETE CASCADE,
    numero INTEGER NOT NULL,
    titre TEXT,
    emoji TEXT,
    duree INTEGER,
    attache TEXT,
    inventaire TEXT,
    ce_qui_change TEXT,
    refs_codes TEXT,
    refs_brut TEXT,
    prompt_image TEXT,
    prompt_video TEXT,
    version_video TEXT,
    dialogues TEXT,
    nb_repliques INTEGER,
    nb_mots INTEGER,
    image_path TEXT,
    image_origine TEXT,
    video_path TEXT,
    video_origine TEXT,
    video_duree REAL,
    video_audio INTEGER,
    video_largeur INTEGER,
    video_hauteur INTEGER,
    export_path TEXT,
    trim_debut REAL DEFAULT 0,
    trim_fin REAL DEFAULT 0,
    valide INTEGER DEFAULT 0,
    UNIQUE (projet_id, numero)
);

CREATE TABLE IF NOT EXISTS taches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    projet_id INTEGER NOT NULL REFERENCES projets(id) ON DELETE CASCADE,
    cible_type TEXT NOT NULL,
    cible_id INTEGER NOT NULL,
    libelle TEXT,
    fournisseur TEXT NOT NULL,
    mode TEXT NOT NULL,
    origine TEXT NOT NULL,
    statut TEXT NOT NULL DEFAULT 'en_attente',
    empreinte TEXT,
    resultat_path TEXT,
    message TEXT,
    -- 1 tant qu'on ne sait pas : on suppose le quota depense. Seul un
    -- adaptateur qui a constate n'avoir RIEN envoye redescend a 0.
    envoye INTEGER NOT NULL DEFAULT 1,
    cree_le TEXT NOT NULL,
    lance_le TEXT,
    fini_le TEXT
);

CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    projet_id INTEGER,
    tache_id INTEGER,
    horodatage TEXT NOT NULL,
    niveau TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sous_titres (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    projet_id INTEGER NOT NULL REFERENCES projets(id) ON DELETE CASCADE,
    ordre INTEGER NOT NULL,
    debut REAL NOT NULL,
    fin REAL NOT NULL,
    texte TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bibliotheque (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    titre TEXT,
    notes TEXT,
    indicateurs TEXT,
    ajoute_le TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comptes_ia (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site TEXT NOT NULL,
    libelle TEXT NOT NULL,
    email TEXT,
    profil TEXT NOT NULL,
    ordre INTEGER DEFAULT 0,
    actif INTEGER DEFAULT 1,
    etat TEXT DEFAULT '',
    etat_source TEXT DEFAULT '',
    verifie_le TEXT,
    quota_epuise_le TEXT,
    derniere_utilisation TEXT,
    reset_jour INTEGER,
    reset_heure TEXT,
    note TEXT,
    chat_url TEXT,
    UNIQUE (site, profil)
);

CREATE TABLE IF NOT EXISTS reglages (
    cle TEXT PRIMARY KEY,
    valeur TEXT
);

CREATE INDEX IF NOT EXISTS idx_journal_projet ON journal (projet_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_taches_projet ON taches (projet_id, id DESC);
"""


def maintenant() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connexion() -> sqlite3.Connection:
    """Une connexion par thread (le worker tourne dans son propre thread)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.preparer_dossiers()
        conn = sqlite3.connect(config.CHEMIN_BASE, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return conn


# Colonnes ajoutees apres coup : migration douce au demarrage.
COLONNES_SUPPLEMENTAIRES: dict[str, dict[str, str]] = {
    "scenes": {
        # Les planches a joindre a Grok, dans l'ordre exact des @imageN du
        # prompt video. Vide pour les packs qui n'en citent pas.
        "refs_video": "TEXT",
        "export_path": "TEXT",
        "video_conforme": "INTEGER",
        "video_ecart": "REAL",
    },
    "taches": {
        "envoye": "INTEGER NOT NULL DEFAULT 1",
        # Combien de fois « Recuperer le resultat » a ramene un fichier qui
        # etait deja celui d'un autre element, et de quel element il s'agissait.
        # Sans ca, un element « a verifier » renvoie indefiniment vers une
        # recuperation dont on sait deja qu'elle ne ramenera rien de neuf.
        "recup_doublons": "INTEGER NOT NULL DEFAULT 0",
        "recup_doublon_de": "TEXT",
    },
    "comptes_ia": {
        "reset_jour": "INTEGER",
        "reset_heure": "TEXT",
        "chat_url": "TEXT",
        # Vide = mes comptes, ceux de la rotation. Sinon le compte appartient a
        # quelqu'un d'autre (ex. « Laura ») : il ne sert qu'a ses projets.
        "proprietaire": "TEXT",
        # Quand le fournisseur a DIT quand le quota revient (« dans 1 heure ») :
        # l'heure de retour. Prime sur l'horaire hebdomadaire et le délai global.
        "quota_retour_le": "TEXT",
    },
    "projets": {
        "type_histoire": "TEXT NOT NULL DEFAULT 'fruits'",
        "titre_miniature": "TEXT",
        "grok_serie_annoncee": "INTEGER DEFAULT 0",
        "grok_profil_regles": "TEXT",
        "grok_serie_annoncee_url": "TEXT",
        "pipeline": "INTEGER DEFAULT 0",
        "pipeline_mode": "TEXT",
        "partie_de": "INTEGER",
        # A qui sont les comptes de ce projet ; NULL = ma rotation.
        "comptes_de": "TEXT",
        # 1 = les planches sont déjà dans le chat ChatGPT du projet : pas besoin
        # de les joindre aux images (Grok, lui, reçoit toujours de vrais fichiers).
        "refs_dans_chat": "INTEGER DEFAULT 0",   # projet de la partie precedente d'une serie
        "etiquettes_inconnues": "TEXT",   # lecteur_souple : étiquettes à apprendre
        # 1 = la chaîne attend un quota dont l'heure de retour est connue et
        # proche : elle a le droit de repartir seule. Jamais posé autrement.
        "attend_quota": "INTEGER DEFAULT 0",
    },
}


#: Les deux garde-fous quotas, crees APRES la migration des colonnes parce
#  qu'ils citent « envoye ». Ils sont recrees a chaque demarrage : une base
#  faite avant cette version porte l'ancienne definition, qui interdisait
#  toute seconde tentative automatique meme quand la premiere n'avait rien
#  envoye du tout — et la chaine sautait alors la scene ratee.
GARDE_FOUS = """
DROP INDEX IF EXISTS idx_une_auto_par_cible;
DROP INDEX IF EXISTS idx_une_active_par_cible;

-- GARDE-FOU CENTRAL : une seule generation AUTOMATIQUE par element.
-- Une tentative qui s'est arretee AVANT le moindre envoi (envoye = 0) n'a
-- rien coute : elle ne verrouille pas l'element. Toute autre relance doit
-- etre explicite (origine = 'manuel').
CREATE UNIQUE INDEX idx_une_auto_par_cible
    ON taches (projet_id, cible_type, cible_id)
    WHERE origine = 'auto'
      AND (statut IN ('en_attente', 'en_cours', 'termine', 'a_verifier')
           OR (statut = 'echec' AND envoye = 1));

-- GARDE-FOU : une seule tache active par element, quelle que soit l'origine.
CREATE UNIQUE INDEX idx_une_active_par_cible
    ON taches (projet_id, cible_type, cible_id)
    WHERE statut IN ('en_attente', 'en_cours');
"""


def initialiser() -> None:
    conn = connexion()
    conn.executescript(SCHEMA)
    for table, colonnes in COLONNES_SUPPLEMENTAIRES.items():
        existantes = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for nom, definition in colonnes.items():
            if nom not in existantes:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {nom} {definition}")
    conn.executescript(GARDE_FOUS)


def q(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return connexion().execute(sql, tuple(params)).fetchall()


def q1(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return connexion().execute(sql, tuple(params)).fetchone()


def exec1(sql: str, params: Iterable[Any] = ()) -> int:
    cur = connexion().execute(sql, tuple(params))
    return cur.lastrowid or 0


def journaliser(
    message: str,
    projet_id: int | None = None,
    niveau: str = "info",
    tache_id: int | None = None,
) -> None:
    exec1(
        "INSERT INTO journal (projet_id, tache_id, horodatage, niveau, message)"
        " VALUES (?,?,?,?,?)",
        (projet_id, tache_id, maintenant(), niveau, message),
    )
    ligne = f"{maintenant()} [{niveau}] projet={projet_id} {message}\n"
    try:
        config.DOSSIER_JOURNAUX.mkdir(parents=True, exist_ok=True)
        with open(config.DOSSIER_JOURNAUX / "videroo.log", "a", encoding="utf-8") as fh:
            fh.write(ligne)
    except OSError:
        pass


def reglage(cle: str, defaut: str | None = None) -> str | None:
    ligne = q1("SELECT valeur FROM reglages WHERE cle = ?", (cle,))
    return ligne["valeur"] if ligne else defaut


def definir_reglage(cle: str, valeur: str) -> None:
    exec1(
        "INSERT INTO reglages (cle, valeur) VALUES (?,?)"
        " ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
        (cle, valeur),
    )


def charger_json(valeur: Any, defaut: Any) -> Any:
    """Accepte une chaine JSON ou une valeur deja decodee."""
    if not valeur:
        return defaut
    if isinstance(valeur, (list, dict)):
        return valeur
    try:
        return json.loads(valeur)
    except (TypeError, ValueError):
        return defaut


def toucher_projet(projet_id: int) -> None:
    exec1("UPDATE projets SET maj_le = ? WHERE id = ?", (maintenant(), projet_id))


def dossier_projet(projet_id: int) -> Path:
    ligne = q1("SELECT dossier FROM projets WHERE id = ?", (projet_id,))
    if not ligne:
        raise KeyError(f"projet {projet_id} inconnu")
    return Path(ligne["dossier"])
