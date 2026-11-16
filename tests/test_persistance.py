"""Projets, import du pack, edition et conservation des medias a la reprise."""
import json
import unittest
import tempfile
from pathlib import Path

import tests
from videroo import db, fichiers, montage, services


class TestPersistance(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()
        self.projet_id = services.creer_projet("La Clé du Gymnase")

    def test_creation_cree_le_dossier_et_l_empreinte(self):
        pr = services.projet(self.projet_id)
        dossier = Path(pr["dossier"])
        self.assertTrue(dossier.is_dir())
        for sous in ("references", "scenes", "miniature", "export"):
            self.assertTrue((dossier / sous).is_dir(), sous)
        self.assertEqual(
            fichiers.lire_empreinte(dossier)["projet_id"], self.projet_id
        )

    def test_import_du_pack_enregistre_tout(self):
        resultat = services.analyser_pack(tests.pack_exemple())
        bilan = services.appliquer_pack(self.projet_id, resultat)
        self.assertEqual(bilan["personnages"], 3)
        self.assertEqual(bilan["scenes"], 10)
        d = services.detail(self.projet_id)
        self.assertEqual(d["projet"]["titre"], "La Clé du Gymnase")
        self.assertEqual(len(d["scenes"]), 10)
        self.assertEqual(d["duree_totale_pack"], 77)
        self.assertEqual(d["scenes"][0]["refs_codes"], ["R1", "R2"])
        self.assertEqual(len(d["scenes"][6]["dialogues"]), 4)

    def test_le_metaprompt_est_refuse_a_l_import_d_un_pack(self):
        resultat = services.analyser_pack(tests.metaprompt_exemple())
        with self.assertRaises(services.ErreurMetier):
            services.appliquer_pack(self.projet_id, resultat)
        self.assertEqual(
            db.q1("SELECT COUNT(*) n FROM scenes WHERE projet_id=?", (self.projet_id,))["n"],
            0,
        )

    def test_reimport_conserve_les_medias_obtenus(self):
        services.appliquer_pack(self.projet_id, services.analyser_pack(tests.pack_exemple()))
        scene = db.q1(
            "SELECT * FROM scenes WHERE projet_id=? AND numero=1", (self.projet_id,)
        )
        pr = services.projet(self.projet_id)
        video = fichiers.chemin_video_scene(pr["dossier"], 1)
        montage.fabriquer_clip_test(video, duree=2, texte="S1")
        services.importer_media(
            self.projet_id, "video", scene["id"], "scene1.mp4", video.read_bytes()
        )
        avant = db.q1("SELECT video_path FROM scenes WHERE id=?", (scene["id"],))["video_path"]
        self.assertTrue(fichiers.fichier_utilisable(avant))

        services.appliquer_pack(self.projet_id, services.analyser_pack(tests.pack_exemple()))
        apres = db.q1(
            "SELECT video_path FROM scenes WHERE projet_id=? AND numero=1", (self.projet_id,)
        )["video_path"]
        self.assertEqual(avant, apres, "le media doit survivre au reimport")

    def test_edition_des_champs(self):
        services.appliquer_pack(self.projet_id, services.analyser_pack(tests.pack_exemple()))
        services.maj_projet(self.projet_id, {"titre": "Titre corrige", "grok_episode": 3})
        self.assertEqual(services.projet(self.projet_id)["titre"], "Titre corrige")
        self.assertEqual(services.projet(self.projet_id)["grok_episode"], 3)

        scene = db.q1("SELECT * FROM scenes WHERE projet_id=? AND numero=2", (self.projet_id,))
        services.maj_scene(scene["id"], {"duree": 10, "refs_codes": "R1, R3"})
        maj = db.q1("SELECT * FROM scenes WHERE id=?", (scene["id"],))
        self.assertEqual(maj["duree"], 10)
        self.assertEqual(db.charger_json(maj["refs_codes"], []), ["R1", "R3"])

        perso = db.q1("SELECT * FROM personnages WHERE projet_id=? AND code='R1'", (self.projet_id,))
        services.maj_personnage(perso["id"], {"surnom": "ARTICHO BIS"})
        self.assertEqual(
            db.q1("SELECT surnom FROM personnages WHERE id=?", (perso["id"],))["surnom"],
            "ARTICHO BIS",
        )

    def test_validation_refuse_une_scene_incomplete(self):
        services.appliquer_pack(self.projet_id, services.analyser_pack(tests.pack_exemple()))
        scene = db.q1("SELECT * FROM scenes WHERE projet_id=? AND numero=3", (self.projet_id,))
        services.maj_scene(scene["id"], {"duree": 7})
        with self.assertRaises(services.ErreurMetier):
            services.valider_scene(scene["id"], True)
        bilan = services.valider_projet(self.projet_id)
        self.assertEqual(len(bilan["validees"]), 9)
        self.assertEqual(bilan["refusees"][0]["numero"], 3)

    def test_import_refuse_un_fichier_illisible(self):
        services.appliquer_pack(self.projet_id, services.analyser_pack(tests.pack_exemple()))
        scene = db.q1("SELECT * FROM scenes WHERE projet_id=? AND numero=1", (self.projet_id,))
        with self.assertRaises(services.ErreurMetier):
            services.importer_media(
                self.projet_id, "video", scene["id"], "faux.mp4", b"ceci n'est pas une video"
            )
        self.assertIsNone(
            db.q1("SELECT video_path FROM scenes WHERE id=?", (scene["id"],))["video_path"]
        )

    def test_import_refuse_une_extension_inattendue(self):
        services.appliquer_pack(self.projet_id, services.analyser_pack(tests.pack_exemple()))
        scene = db.q1("SELECT * FROM scenes WHERE projet_id=? AND numero=1", (self.projet_id,))
        with self.assertRaises(services.ErreurMetier):
            services.importer_media(self.projet_id, "image", scene["id"], "doc.pdf", b"x")

    def test_le_journal_enregistre_les_etapes(self):
        services.appliquer_pack(self.projet_id, services.analyser_pack(tests.pack_exemple()))
        lignes = db.q("SELECT * FROM journal WHERE projet_id=?", (self.projet_id,))
        self.assertTrue(any("Pack importé" in l["message"] for l in lignes))


if __name__ == "__main__":
    unittest.main()


class TestAffichageScenes(unittest.TestCase):
    """Le detail affiche ne doit pas inventer de faux problemes."""

    def setUp(self):
        tests.reinitialiser()
        self.projet_id = services.creer_projet("Affichage")
        services.appliquer_pack(
            self.projet_id, services.analyser_pack(tests.pack_exemple())
        )

    def test_aucun_faux_probleme_de_reference(self):
        d = services.detail(self.projet_id)
        for s in d["scenes"]:
            self.assertNotIn(
                "aucun personnage de référence",
                " ".join(s["problemes"]),
                f"scene {s['numero']} : refs = {s['refs_codes']}",
            )
        self.assertEqual(d["scenes"][0]["problemes"], [])
        self.assertEqual(len(d["scenes"][0]["refs"]), 2)

    def test_charger_json_accepte_une_liste(self):
        self.assertEqual(db.charger_json(["R1"], []), ["R1"])
        self.assertEqual(db.charger_json('["R1"]', []), ["R1"])
        self.assertEqual(db.charger_json(None, []), [])


class TestPartieSuivante(unittest.TestCase):
    """La partie 2 reste dans les chats de la partie 1, sans rien lancer."""

    def setUp(self):
        tests.reinitialiser()
        self.pid = services.creer_projet("Le Secret")
        services.appliquer_pack(
            self.pid, services.analyser_pack(tests.pack_variantes())
        )
        db.exec1(
            "UPDATE projets SET chatgpt_chat_url=?, grok_chat_url=?,"
            " grok_regles_installees=1, grok_profil_regles='principal',"
            " grok_serie_annoncee=1, grok_serie_annoncee_url=? WHERE id=?",
            ("https://chatgpt.com/c/aaa", "https://grok.com/c/bbb",
             "https://grok.com/c/bbb", self.pid),
        )
        perso = db.q1(
            "SELECT * FROM personnages WHERE projet_id=? ORDER BY ordre LIMIT 1",
            (self.pid,),
        )
        dossier = Path(services.projet(self.pid)["dossier"])
        self.planche = dossier / "references" / "planche_test.png"
        self.planche.parent.mkdir(parents=True, exist_ok=True)
        self.planche.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        db.exec1("UPDATE personnages SET ref_path=? WHERE id=?",
                 (str(self.planche), perso["id"]))
        self.code = perso["code"]

    def test_memes_chats_regles_gardees_annonce_a_refaire(self):
        suite = services.creer_partie_suivante(self.pid)
        self.assertTrue(suite["cree"])
        p2 = services.projet(suite["id"])
        p1 = services.projet(self.pid)
        self.assertEqual(p2["grok_episode"], 2)
        self.assertEqual(p2["partie_de"], self.pid)
        self.assertEqual(p2["grok_serie"], p1["grok_serie"])
        self.assertEqual(p2["chatgpt_chat_url"], "https://chatgpt.com/c/aaa")
        self.assertEqual(p2["grok_chat_url"], "https://grok.com/c/bbb")
        self.assertEqual(p2["grok_regles_installees"], 1)
        # La partie 2 doit s'annoncer elle-meme dans ce chat.
        self.assertFalse(p2["grok_serie_annoncee_url"])
        self.assertIn("Partie 2", p2["nom"])
        self.assertEqual(p2["pipeline"] or 0, 0)
        self.assertEqual(
            db.q1("SELECT COUNT(*) n FROM taches WHERE projet_id=?", (suite["id"],))["n"], 0
        )

    def test_planche_copiee_puis_gardee_a_l_import_du_pack(self):
        suite = services.creer_partie_suivante(self.pid)
        ref = db.q1("SELECT ref_path FROM personnages WHERE projet_id=? AND code=?",
                    (suite["id"], self.code))["ref_path"]
        dossier2 = Path(services.projet(suite["id"])["dossier"]).resolve()
        self.assertTrue(Path(ref).resolve().is_relative_to(dossier2))
        self.assertTrue(Path(ref).exists())
        services.appliquer_pack(
            suite["id"], services.analyser_pack(tests.pack_variantes())
        )
        apres = db.q1("SELECT ref_path FROM personnages WHERE projet_id=? AND code=?",
                      (suite["id"], self.code))["ref_path"]
        self.assertEqual(apres, ref)

    def test_deux_clics_ne_font_pas_deux_parties_2(self):
        a = services.creer_partie_suivante(self.pid)
        b = services.creer_partie_suivante(self.pid)
        self.assertEqual(a["id"], b["id"])
        self.assertFalse(b["cree"])
        self.assertEqual(
            db.q1("SELECT COUNT(*) n FROM projets WHERE partie_de=?", (self.pid,))["n"], 1
        )

    def test_partie_3_garde_le_nom_de_la_serie(self):
        p2 = services.creer_partie_suivante(self.pid)["id"]
        p3 = services.creer_partie_suivante(p2)["id"]
        nom = services.projet(p3)["nom"]
        self.assertTrue(nom.endswith("Partie 3"))
        self.assertNotIn("Partie 2", nom)
        self.assertEqual(services.projet(p3)["grok_episode"], 3)


class TestPlancheReparee(unittest.TestCase):
    """Ecrire le prompt qui manquait doit effacer l'avertissement devenu faux."""

    def setUp(self):
        tests.reinitialiser()
        self.pid = services.creer_projet("Planche a reparer")
        db.exec1(
            "INSERT INTO personnages (projet_id, code, surnom, prompt_planche, ordre)"
            " VALUES (?,?,?,?,?)",
            (self.pid, "O4", "LE DOCUMENT", "", 0),
        )
        self.perso = db.q1(
            "SELECT id FROM personnages WHERE projet_id=? AND code='O4'", (self.pid,)
        )["id"]
        db.exec1(
            "UPDATE projets SET avertissements=? WHERE id=?",
            (
                json.dumps([
                    {"niveau": "manquant", "cible": "O4 LE DOCUMENT",
                     "message": "Aucun prompt de planche : cette référence..."},
                    {"niveau": "manquant", "cible": "O3 LA PHOTO",
                     "message": "Aucun prompt de planche : cette référence..."},
                ], ensure_ascii=False),
                self.pid,
            ),
        )

    def _avertissements(self):
        return db.charger_json(
            db.q1("SELECT avertissements FROM projets WHERE id=?", (self.pid,))[
                "avertissements"
            ],
            [],
        )

    def test_le_prompt_ecrit_efface_le_seul_avertissement_concerne(self):
        services.maj_personnage(self.perso, {"prompt_planche": "Planche 9:16 ..."})
        restants = [a["cible"] for a in self._avertissements()]
        self.assertEqual(restants, ["O3 LA PHOTO"])

    def test_un_prompt_vide_ne_fait_rien_disparaitre(self):
        services.maj_personnage(self.perso, {"prompt_planche": "   "})
        self.assertEqual(len(self._avertissements()), 2)


class TestSuppressionDunProjet(unittest.TestCase):
    """Effacer un projet detruit des quotas deja payes : on exige son titre."""

    def setUp(self):
        tests.reinitialiser()
        self.pid = services.creer_projet("Projet jetable")
        services.appliquer_pack(
            self.pid, services.analyser_pack(tests.pack_variantes())
        )
        self.dossier = Path(services.projet(self.pid)["dossier"])
        self.titre = services.projet(self.pid)["titre"] or "Projet jetable"

    def test_sans_le_bon_titre_rien_nest_touche(self):
        with self.assertRaises(services.SuppressionRefusee):
            services.supprimer_projet(self.pid, "pas le bon titre")
        self.assertTrue(db.q1("SELECT 1 FROM projets WHERE id=?", (self.pid,)))
        self.assertTrue(self.dossier.exists())

    def test_avec_le_bon_titre_tout_part_base_et_fichiers(self):
        bilan = services.supprimer_projet(self.pid, self.titre)
        self.assertTrue(bilan["fichiers_effaces"])
        self.assertFalse(self.dossier.exists())
        self.assertIsNone(db.q1("SELECT 1 FROM projets WHERE id=?", (self.pid,)))
        for table in ("scenes", "personnages", "taches", "journal"):
            reste = db.q1(
                f"SELECT COUNT(*) n FROM {table} WHERE projet_id=?", (self.pid,)
            )["n"]
            self.assertEqual(reste, 0, table)

    def test_les_autres_projets_ne_bougent_pas(self):
        autre = services.creer_projet("Celui qu'on garde")
        services.supprimer_projet(self.pid, self.titre)
        self.assertTrue(db.q1("SELECT 1 FROM projets WHERE id=?", (autre,)))
        self.assertTrue(Path(services.projet(autre)["dossier"]).exists())


class TestComptesDUneAutrePersonne(unittest.TestCase):
    """Les comptes de Laura ne servent qu'a ses projets, jamais a la rotation."""

    def setUp(self):
        tests.reinitialiser()
        from videroo import comptes
        self.comptes = comptes
        self.moi = comptes.ajouter("grok", "Moi")
        self.laura = comptes.ajouter("grok", "Laura", proprietaire="Laura")

    def test_la_rotation_ignore_les_comptes_de_laura(self):
        ids = [c["id"] for c in self.comptes.ordre_utilisation("grok")]
        self.assertIn(self.moi, ids)
        self.assertNotIn(self.laura, ids)

    def test_un_projet_de_laura_n_utilise_que_ses_comptes(self):
        pid = services.creer_projet("Projet Laura", None, "Laura")
        self.assertEqual(services.projet(pid)["comptes_de"], "Laura")
        self.assertEqual(self.comptes.compte_actif("grok", "Laura")["id"], self.laura)

    def test_laura_a_court_ne_bascule_pas_sur_mes_comptes(self):
        self.comptes.marquer_quota_epuise(self.laura, "test")
        self.assertIsNone(self.comptes.compte_actif("grok", "Laura"))
        self.assertEqual(self.comptes.comptes_de_secours("grok", self.laura, "Laura"), [])

    def test_un_proprietaire_inconnu_retombe_sur_la_rotation(self):
        pid = services.creer_projet("Projet inconnu", None, "Personne")
        self.assertIsNone(services.projet(pid)["comptes_de"])


class TestSerieCommenceeAvant(unittest.TestCase):
    """Une série d'avant Videroo garde ses chats, et ses planches restent dans le chat."""

    def setUp(self):
        tests.reinitialiser()

    def test_chats_donnes_a_la_creation(self):
        from videroo import runner
        pid = services.creer_projet("Secret Pets 3", None, "", "https://chatgpt.com/c/abc-123", True, 3)
        p = services.projet(pid)
        self.assertEqual(p["chatgpt_chat_url"], "https://chatgpt.com/c/abc-123")
        self.assertEqual(p["grok_episode"], 3)
        self.assertTrue(runner.planches_dans_le_chat(pid))

    def test_sans_chat_chatgpt_les_planches_restent_exigees(self):
        from videroo import runner
        pid = services.creer_projet("Sans chat", None, "", "https://chatgpt.com/", True)
        self.assertIsNone(services.projet(pid)["chatgpt_chat_url"])
        self.assertFalse(runner.planches_dans_le_chat(pid))


class TestReglagesDeLaura(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()
        from videroo import comptes
        comptes.ajouter("grok", "laura", proprietaire="Laura")

    def test_le_projet_suivant_reprend_tout(self):
        p1 = services.creer_projet("Secret Pets 3", "fruits", "Laura",
                                   "https://chatgpt.com/c/abc-123", True, 3)
        db.exec1("UPDATE projets SET grok_serie='secret_pets' WHERE id=?", (p1,))
        services.memoriser_serie_de(p1)  # ce que fait l'enregistrement de l'en-tête
        services.supprimer_projet(p1, services.projet(p1)["nom"])  # survit à la suppression
        p2 = services.creer_projet(services.nom_episode_suivant("Laura"), "chronique", "Laura")
        p = services.projet(p2)
        self.assertEqual((p["grok_serie"], p["grok_episode"], p["type_histoire"]), ("secret_pets", 4, "fruits"))
        self.assertEqual(p["chatgpt_chat_url"], "https://chatgpt.com/c/abc-123")
        self.assertEqual(p["refs_dans_chat"], 1)
        self.assertEqual(p["nom"], "Secret Pets 3 · Épisode 4")

    def test_ma_rotation_ne_reprend_rien(self):
        services.creer_projet("Secret Pets 3", None, "Laura", "https://chatgpt.com/c/abc-123", True, 3)
        p = services.projet(services.creer_projet("Autre", None, ""))
        self.assertIsNone(p["chatgpt_chat_url"])


class TestPartie2SansReferences(unittest.TestCase):
    def test_la_partie_2_garde_les_personnages_et_planches(self):
        tests.reinitialiser()
        p1 = services.creer_projet("Le garçon", "chronique")
        pack1 = ("RÉFÉRENCE KAIRO : Planche Kairo.\nRÉFÉRENCE MAYA : Planche Maya.\n"
                 "IMAGE (Maya, Kairo) : Palier.\nVIDÉO (dialogue · 6 s · MAYA @image2, KAIRO @image3) : Parle.\n")
        services.appliquer_pack(p1, services.analyser_pack(pack1, "chronique", p1))
        dossier = Path(services.projet(p1)["dossier"])
        for code, nom in (("R1", "KAIRO"), ("R2", "MAYA")):
            f = dossier / "references" / f"{code}_{nom.lower()}.png"
            f.write_bytes(b"\x89PNG" + b"0" * 64)
            db.exec1("UPDATE personnages SET ref_path=? WHERE projet_id=? AND code=?", (str(f), p1, code))
        p2 = services.creer_partie_suivante(p1)["id"]
        pack2 = "IMAGE (Maya, Kairo) : Cuisine.\nVIDÉO (dialogue · 6 s · MAYA @image2, KAIRO @image3) : Parle.\n"
        b = services.appliquer_pack(p2, services.analyser_pack(pack2, "chronique", p2))
        self.assertEqual(b["personnages"], 2)
        persos = db.q("SELECT code, ref_path FROM personnages WHERE projet_id=? ORDER BY code", (p2,))
        self.assertTrue(all(fichiers.fichier_utilisable(p["ref_path"]) for p in persos))
        self.assertEqual(db.charger_json(db.q1("SELECT refs_video FROM scenes WHERE projet_id=?", (p2,))["refs_video"], []), ["R2", "R1"])


class TestDeposerUneImageSurSaCase(unittest.TestCase):
    """Glisser-déposer : l'image va sur la case où tu la lâches, sans génération."""

    def setUp(self):
        tests.reinitialiser()
        from videroo import db, services

        self.pid = services.creer_projet("Dépôt", "serie")
        self.autre = services.creer_projet("Autre", "serie")
        db.exec1("INSERT INTO personnages (projet_id, code, surnom, etat, ordre) VALUES (?,?,?,?,?)",
                 (self.pid, "R1", "NOUR", "UNIQUE", 1))
        db.exec1("INSERT INTO personnages (projet_id, code, surnom, etat, ordre) VALUES (?,?,?,?,?)",
                 (self.autre, "R1", "AMINE", "UNIQUE", 1))
        self.nour = db.q1("SELECT id FROM personnages WHERE projet_id=?", (self.pid,))["id"]
        self.amine = db.q1("SELECT id FROM personnages WHERE projet_id=?", (self.autre,))["id"]
        dossier = Path(tempfile.mkdtemp())
        self.image = dossier / "photo.png"
        tests.fabriquer_image_test(self.image, texte="NOUR")

    def _deposer(self, cible, nom="photo.png", contenu=None):
        from fastapi.testclient import TestClient
        from videroo import app as module_app

        donnees = contenu if contenu is not None else self.image.read_bytes()
        return TestClient(module_app.app).post(
            f"/projets/{self.pid}/deposer", data={"cible": cible},
            files={"fichier": (nom, donnees, "image/png")},
        ).json()

    def test_planche_puis_miniature(self):
        from videroo import db

        self.assertTrue(self._deposer(f"planche:{self.nour}")["ok"])
        self.assertTrue(db.q1("SELECT ref_path FROM personnages WHERE id=?", (self.nour,))["ref_path"])
        self.assertTrue(self._deposer("miniature:0")["ok"])
        self.assertTrue(db.q1("SELECT miniature_path FROM projets WHERE id=?", (self.pid,))["miniature_path"])
        self.assertEqual(db.q1("SELECT COUNT(*) AS n FROM taches")["n"], 0, "un dépôt a lancé une génération")

    def test_le_personnage_dun_autre_projet_est_refuse(self):
        bilan = self._deposer(f"planche:{self.amine}")
        self.assertFalse(bilan["ok"])

    def test_un_fichier_qui_nest_pas_une_image_est_refuse(self):
        bilan = self._deposer(f"planche:{self.nour}", nom="notes.txt", contenu=b"bonjour")
        self.assertFalse(bilan["ok"])
