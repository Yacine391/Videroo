"""Les trois familles d'histoires : fruits, chronique, bébé IA.

Le choix se fait à la création du projet. Il décide du métaprompt à copier et
du lecteur de pack ; tout le reste de la chaîne — planches, image puis vidéo
par scène, miniature, export — est strictement le même pour les trois.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import tests
from videroo import (
    config, db, parser_bebe, parser_chronique, parser_v6, ressources, services,
)

PACK_CHRONIQUE = """🎬 LA GOLF DE NASSIM — ÉPISODE 2
Casting : Kenza (19, la princesse du quartier), Ilyes (20, le beau gosse calme)
L'histoire : Kenza descend voir Ilyes en douce. Son frère est à la fenêtre.
Durée : 16 s — 2 scènes — décors : hall, parking

RÉFÉRENCE KENZA : Planche de référence photoréaliste de Kenza, 9:16, fond blanc de studio.

RÉFÉRENCE ILYES : Planche de référence photoréaliste d'Ilyes, 9:16, fond blanc de studio.

IMAGE (Kenza) : Hall d'immeuble, boîtes aux lettres taguées, Kenza au centre, téléphone en main.

VIDÉO (dynamique · 10 s · Kenza @image2, Ilyes @image3) : Ilyes pousse la porte vitrée et s'arrête face à elle.

IMAGE (aucun) : Le parking vide entre les tours, une Golf noire garée, lampadaire orangé.

VIDÉO (action · 6 s · Kenza @image2) : Kenza traverse le parking en courant.

✂️ MONTAGE : coupes franches · 0,5 s de noir avant la dernière scène
🔥 TITRE MINIATURE : IL M'A VUE DESCENDRE
📝 LÉGENDE : Ép 2 — elle a cru que personne ne regardait. #chronique
"""


PACK_BEBE = """LE SECRET CACHÉ DANS SON DOUDOU

PARTIE 1 SUR 3

2. Planches de référence — ChatGPT Image
R1 — ROSE, l'héroïne

9 mois · Héroïne

Voix : française fictive, claire, douce, curieuse.

Prompt planche R1 — ChatGPT Image
Crée une planche de référence verticale 9:16, photoréaliste, fond blanc pur, quatre vues
cohérentes du même bébé humain féminin de 9 mois, peau brun clair, boucles noires.
 Copier R1

R2 — JADE, la cousine qui connaît le secret

10 mois · Cousine

Prompt planche R2 — ChatGPT Image
Crée une planche de référence verticale 9:16, photoréaliste, fond blanc pur uniforme,
quatre vues cohérentes du même bébé humain féminin de 10 mois, yeux verts.
 Copier R2

Références des objets importants

O1 — Le doudou

Prompt : Planche d'objet verticale 9:16, fond blanc pur, quatre vues du même ours en
peluche brun ancien, pièce de tissu bleu sur l'oreille gauche.

3. Les 10 scènes — Prompts de production
SCÈNE 01 — LE DOUDOU INTERDIT
00:00–00:06
6 secondes · Version normale · Hook

Inventaire : Rose à gauche, Jade à droite, ours O1 sur ses genoux.

Références ChatGPT : R1, R2, O1.

Ce qui change : Jade reconnaît l'ours et révèle qu'il aurait dû disparaître.

Prompt image — ChatGPT
Image de départ verticale 9:16, photographie ultra-photoréaliste de deux véritables bébés
humains dans le salon chaleureux d'une vieille maison familiale.

Dialogue

JADE : « Ce doudou ne devait jamais revenir ici. »

ROSE : « Pourquoi ? »

Prompt vidéo — Grok
Vertical 9:16 ultra-photorealistic live-action style footage of real human infants,
animate the attached starting image (@image1).

Action: Jade stares at the teddy bear and speaks.

SCÈNE 02 — LA PHOTOGRAPHIE
00:06–00:16 · 10 secondes · Version dynamique

Références ChatGPT : R1, R2.

Prompt image — ChatGPT
Image verticale 9:16, scène photoréaliste dans le même salon familial.

Dialogue

ROSE : « C'est le même que sur la photo de grand-père. »

Prompt vidéo — Grok
Vertical 9:16 ultra-photorealistic live-action style footage of real human infants,
animate the attached starting image (@image1).

4. Script final pour CapCut
Temps	Scène	Durée	Dialogues
00:00–00:06	Le doudou interdit	6 s	Jade : « Ce doudou ne devait jamais revenir ici. »
"""


class TestChoixDuType(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()

    def test_les_trois_types_existent(self):
        self.assertEqual(
            set(config.TYPES_PROJET), {"fruits", "chronique", "bebe", "serie"}
        )

    def test_le_type_est_enregistre_a_la_creation(self):
        for cle in config.TYPES_PROJET:
            pid = services.creer_projet(f"Projet {cle}", cle)
            ligne = db.q1("SELECT type_histoire FROM projets WHERE id=?", (pid,))
            self.assertEqual(ligne["type_histoire"], cle)

    def test_un_type_inconnu_retombe_sur_les_fruits_sans_planter(self):
        pid = services.creer_projet("Bizarre", "chronique-du-futur")
        ligne = db.q1("SELECT type_histoire FROM projets WHERE id=?", (pid,))
        self.assertEqual(ligne["type_histoire"], config.TYPE_PROJET_DEFAUT)

    def test_un_projet_sans_type_reste_aux_fruits(self):
        """Les projets d'avant ce choix ne changent pas de comportement."""
        pid = services.creer_projet("Ancien")
        ligne = db.q1("SELECT type_histoire FROM projets WHERE id=?", (pid,))
        self.assertEqual(ligne["type_histoire"], "fruits")


class TestMetapromptParType(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()

    def test_chaque_type_a_son_fichier(self):
        chemins = {
            cle: services.chemin_metaprompt(cle) for cle in config.TYPES_PROJET
        }
        self.assertEqual(
            len(set(chemins.values())), len(config.TYPES_PROJET),
            "deux types partagent un fichier",
        )

    def test_les_fruits_gardent_leur_ancien_fichier(self):
        """Le métaprompt déjà enregistré ne doit pas être à recoller."""
        self.assertEqual(services.chemin_metaprompt("fruits").name, "metaprompt_v6.md")

    def test_chronique_et_bebe_sont_livres_avec_lapplication(self):
        for cle, repere in (
            ("chronique", "RÉALISATEUR DE CHRONIQUES"),
            ("bebe", "BABY REALISTIC STORY DRAMA"),
        ):
            texte = ressources.metaprompt_livre(cle)
            self.assertIn(repere, texte)
            self.assertIn(repere, services.lire_metaprompt(cle)["texte"])

    def test_le_tien_passe_devant_celui_livre(self):
        services.enregistrer_metaprompt("Ma version à moi.", "chronique")
        lu = services.lire_metaprompt("chronique")
        self.assertEqual(lu["texte"], "Ma version à moi.")
        self.assertEqual(lu["origine"], "le tien")

    def test_enregistrer_un_type_nefface_pas_les_autres(self):
        services.enregistrer_metaprompt("Chronique à moi.", "chronique")
        services.enregistrer_metaprompt("Bébé à moi.", "bebe")
        self.assertEqual(services.lire_metaprompt("chronique")["texte"], "Chronique à moi.")
        self.assertEqual(services.lire_metaprompt("bebe")["texte"], "Bébé à moi.")

    def test_un_texte_vide_est_refuse(self):
        with self.assertRaises(services.ErreurMetier):
            services.enregistrer_metaprompt("   ", "chronique")


class TestLecteurChronique(unittest.TestCase):
    """Le pack chronique n'a pas la forme du V6 : étiquettes, pas de blocs."""

    def test_le_lecteur_v6_ne_sait_pas_le_lire(self):
        """C'est la raison d'être du second lecteur, pas un détail."""
        resultat = parser_v6.analyser(PACK_CHRONIQUE)
        self.assertEqual(resultat.personnages, [])
        self.assertEqual(resultat.scenes, [])

    def test_les_planches_sont_lues_dans_lordre(self):
        resultat = parser_chronique.analyser(PACK_CHRONIQUE)
        self.assertEqual(
            [(p["code"], p["surnom"]) for p in resultat.personnages],
            [("R1", "KENZA"), ("R2", "ILYES")],
        )
        self.assertTrue(all(p["prompt_planche"] for p in resultat.personnages))

    def test_chaque_scene_a_son_image_sa_video_et_sa_duree(self):
        resultat = parser_chronique.analyser(PACK_CHRONIQUE)
        self.assertEqual([s["numero"] for s in resultat.scenes], [1, 2])
        self.assertEqual([s["duree"] for s in resultat.scenes], [10, 6])
        for s in resultat.scenes:
            self.assertTrue(s["prompt_image"])
            self.assertTrue(s["prompt_video"])

    def test_un_personnage_qui_arrive_est_quand_meme_joint(self):
        """Ilyes n'est pas sur l'image de départ : il est tagué dans la VIDÉO."""
        resultat = parser_chronique.analyser(PACK_CHRONIQUE)
        self.assertEqual(resultat.scenes[0]["refs_codes"], ["R1", "R2"])

    def test_le_type_de_scene_nest_pas_pris_pour_un_prenom(self):
        """« action · 6 s · Kenza @image2 » ne contient qu'un seul prénom."""
        resultat = parser_chronique.analyser(PACK_CHRONIQUE)
        self.assertEqual(resultat.scenes[1]["refs_codes"], ["R1"])

    def test_image_aucun_ne_joint_aucune_planche_de_depart(self):
        resultat = parser_chronique.analyser(PACK_CHRONIQUE)
        self.assertEqual(resultat.scenes[1]["refs_brut"], "aucun")

    def test_le_titre_de_miniature_est_celui_du_pack(self):
        resultat = parser_chronique.analyser(PACK_CHRONIQUE)
        self.assertEqual(resultat.titre, "LA GOLF DE NASSIM")

        self.assertEqual(resultat.titre_miniature, "IL M'A VUE DESCENDRE")

    def test_un_sous_titre_apres_le_numero_depisode_ne_perd_pas_le_titre(self):
        """« … — ÉPISODE 1 : LE RENDEZ-VOUS PIÉGÉ » laissait le projet sans titre."""
        pack = PACK_CHRONIQUE.replace(
            "🎬 LA GOLF DE NASSIM — ÉPISODE 2",
            "🎬 LA GOLF DE NASSIM — ÉPISODE 2 : LE RENDEZ-VOUS PIÉGÉ",
        )
        self.assertEqual(parser_chronique.analyser(pack).titre, "LA GOLF DE NASSIM")

    def test_un_pack_conforme_ne_declenche_aucune_alerte(self):
        resultat = parser_chronique.analyser(PACK_CHRONIQUE)
        self.assertEqual(
            [a.message for a in resultat.avertissements if a.niveau != "info"], []
        )

    def test_une_duree_absente_est_signalee_pas_inventee(self):
        sans_duree = PACK_CHRONIQUE.replace("dynamique · 10 s · ", "dynamique · ")
        resultat = parser_chronique.analyser(sans_duree)
        self.assertIsNone(resultat.scenes[0]["duree"])
        self.assertTrue(
            any("Durée absente" in a.message for a in resultat.avertissements)
        )

    def test_un_prenom_sans_reference_est_signale(self):
        inconnu = PACK_CHRONIQUE.replace("IMAGE (Kenza) :", "IMAGE (Sofiane) :")
        resultat = parser_chronique.analyser(inconnu)
        self.assertTrue(
            any("Sofiane" in a.message for a in resultat.avertissements)
        )

    def test_un_document_qui_nest_pas_un_pack_est_refuse_proprement(self):
        resultat = parser_chronique.analyser("Bonjour, comment ça va ?")
        self.assertEqual(resultat.scenes, [])
        self.assertEqual(resultat.personnages, [])
        self.assertTrue(resultat.avertissements)


class TestImportParType(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()

    def test_un_projet_chronique_lit_un_pack_chronique(self):
        pid = services.creer_projet("Chronique", "chronique")
        resultat = services.analyser_pack(PACK_CHRONIQUE, "chronique")
        bilan = services.appliquer_pack(pid, resultat)
        self.assertEqual(bilan["personnages"], 2)
        self.assertEqual(bilan["scenes"], 2)

    def test_un_projet_fruits_lit_toujours_son_pack_v6(self):
        """La chaîne d'origine ne bouge pas d'un pouce."""
        pid = services.creer_projet("Fruits", "fruits")
        resultat = services.analyser_pack(tests.pack_variantes(), "fruits")
        bilan = services.appliquer_pack(pid, resultat)
        self.assertGreater(bilan["personnages"], 0)
        self.assertGreater(bilan["scenes"], 0)

    def test_un_pack_colle_dans_le_mauvais_type_est_lu_et_signale(self):
        """Mieux vaut l'importer avec une remarque que refuser sèchement."""
        pid = services.creer_projet("Fruits", "fruits")
        resultat = services.analyser_pack(PACK_CHRONIQUE, "fruits")
        self.assertEqual(len(resultat.scenes), 2)
        self.assertTrue(
            any("format" in a.message for a in resultat.avertissements),
            "l'écart de format n'a pas été signalé",
        )
        self.assertEqual(services.appliquer_pack(pid, resultat)["scenes"], 2)

    def test_les_planches_du_prompt_video_sont_gardees_dans_leur_ordre(self):
        """« @image2 is KENZA, @image3 is ILYES » : l'ordre fait l'identité.

        Grok numérote les pièces jointes dans l'ordre où on les dépose. Si la
        planche d'Ilyes part en deuxième, « @image3 is ILYES » désigne le
        visage de quelqu'un d'autre.
        """
        import json

        pid = services.creer_projet("Chronique", "chronique")
        services.appliquer_pack(pid, services.analyser_pack(PACK_CHRONIQUE, "chronique"))
        scene = db.q1(
            "SELECT refs_video FROM scenes WHERE projet_id=? AND numero=1", (pid,)
        )
        # Scène 1 : IMAGE (Kenza) mais VIDÉO (… Kenza @image2, Ilyes @image3).
        self.assertEqual(json.loads(scene["refs_video"]), ["R1", "R2"])

    def test_le_runner_joint_vraiment_ces_planches_apres_limage_de_depart(self):
        from videroo import fichiers, runner

        pid = services.creer_projet("Chronique", "chronique")
        services.appliquer_pack(pid, services.analyser_pack(PACK_CHRONIQUE, "chronique"))
        dossier = services.projet(pid)["dossier"]
        for perso in db.q("SELECT * FROM personnages WHERE projet_id=?", (pid,)):
            chemin = fichiers.chemin_planche(
                dossier, perso["code"], perso["surnom"], perso["etat"] or "UNIQUE"
            )
            tests.fabriquer_image_test(chemin, texte=perso["code"])
            services.importer_media(
                pid, "planche", perso["id"], chemin.name, chemin.read_bytes()
            )
        scene = db.q1("SELECT * FROM scenes WHERE projet_id=? AND numero=1", (pid,))
        image = fichiers.chemin_image_scene(dossier, scene["numero"])
        tests.fabriquer_image_test(image, texte="S1")
        services.importer_media(pid, "image", scene["id"], image.name, image.read_bytes())

        joints = runner.pieces_jointes_video(pid, scene["id"])
        noms = [Path(c).name for c in joints]
        self.assertEqual(len(noms), 3, noms)
        self.assertEqual(noms[0], Path(image).name)
        self.assertIn("R1", noms[1])
        self.assertIn("R2", noms[2])

    def test_la_miniature_dune_chronique_porte_le_titre_du_pack(self):
        from videroo import runner

        pid = services.creer_projet("Chronique", "chronique")
        services.appliquer_pack(pid, services.analyser_pack(PACK_CHRONIQUE, "chronique"))
        prompt = runner.prompt_miniature(pid)
        self.assertIn("IL M'A VUE DESCENDRE", prompt)
        self.assertNotIn("LA GOLF DE NASSIM", prompt)

    def test_la_miniature_dun_projet_fruits_garde_le_titre_de_lhistoire(self):
        from videroo import runner

        pid = services.creer_projet("Fruits", "fruits")
        services.appliquer_pack(pid, services.analyser_pack(tests.pack_variantes(), "fruits"))
        titre = db.q1("SELECT titre FROM projets WHERE id=?", (pid,))["titre"]
        self.assertIn(titre.strip("«» \t"), runner.prompt_miniature(pid))


class TestLecteurBebe(unittest.TestCase):
    def setUp(self):
        self.res = parser_bebe.analyser(PACK_BEBE)

    def test_les_autres_lecteurs_ne_savent_pas_le_lire(self):
        """Mesure, pas supposition : le V6 ne trouve aucun prompt dans ce pack."""
        from videroo import parser_chronique

        v6 = parser_v6.analyser(PACK_BEBE)
        self.assertEqual([s for s in v6.scenes if s["prompt_image"]], [])
        self.assertEqual([s for s in v6.scenes if s["prompt_video"]], [])
        chronique = parser_chronique.analyser(PACK_BEBE)
        self.assertEqual(chronique.scenes, [])

    def test_les_planches_des_bebes_sont_lues(self):
        bebes = [p for p in self.res.personnages if p["code"].startswith("R")]
        self.assertEqual([p["code"] for p in bebes], ["R1", "R2"])
        self.assertEqual([p["surnom"] for p in bebes], ["ROSE", "JADE"])
        self.assertEqual(bebes[0]["age"], "9 mois")
        self.assertIn("photoréaliste", bebes[0]["prompt_planche"])

    def test_un_objet_est_une_planche_a_part_entiere(self):
        """« Un identifiant comme O1 ne remplace pas une image jointe. »"""
        objets = [p for p in self.res.personnages if p["code"].startswith("O")]
        self.assertEqual([p["code"] for p in objets], ["O1"])
        self.assertEqual(objets[0]["surnom"], "LE DOUDOU")
        self.assertTrue(objets[0]["prompt_planche"].strip())
        self.assertIn("O1", self.res.scenes[0]["refs_codes"])

    def test_chaque_scene_a_son_image_sa_video_et_sa_duree(self):
        self.assertEqual(len(self.res.scenes), 2)
        for s in self.res.scenes:
            self.assertTrue(s["prompt_image"].strip(), f"scène {s['numero']}")
            self.assertTrue(s["prompt_video"].strip(), f"scène {s['numero']}")
        self.assertEqual([s["duree"] for s in self.res.scenes], [6, 10])
        self.assertEqual(
            [s["version_video"] for s in self.res.scenes], ["normale", "dynamique"]
        )

    def test_le_bouton_copier_de_chatgpt_ne_finit_pas_dans_le_prompt(self):
        for p in self.res.personnages:
            self.assertNotIn("Copier", p["prompt_planche"])

    def test_un_titre_de_section_ne_finit_pas_dans_le_prompt(self):
        objet = [p for p in self.res.personnages if p["code"] == "O1"][0]
        self.assertNotIn("Les 10 scènes", objet["prompt_planche"])

    def test_le_tableau_capcut_narrete_pas_dans_les_scenes(self):
        self.assertNotIn("Script final", self.res.scenes[-1]["prompt_video"])

    def test_les_dialogues_sont_rattaches_a_leur_scene(self):
        premiere = self.res.scenes[0]
        self.assertEqual(premiere["nb_repliques"], 2)
        self.assertEqual(premiere["dialogues"][0]["etiquette"], "JADE")
        self.assertIn("doudou", premiere["dialogues"][0]["texte"])

    def test_un_pack_conforme_ne_declenche_aucune_alerte(self):
        self.assertEqual(
            [a.message for a in self.res.avertissements if a.niveau != "info"], []
        )

    def test_une_reference_citee_sans_planche_est_signalee(self):
        texte = PACK_BEBE.replace("Références ChatGPT : R1, R2, O1.",
                                  "Références ChatGPT : R1, R2, O1, O9.")
        res = parser_bebe.analyser(texte)
        self.assertTrue(
            any("O9" in a.message for a in res.avertissements),
            "une référence absente du pack doit être signalée, pas inventée",
        )

    def test_une_planche_sans_prompt_est_signalee_pas_inventee(self):
        texte = PACK_BEBE.replace(
            "Prompt : Planche d'objet verticale 9:16, fond blanc pur, quatre vues du même ours en\npeluche brun ancien, pièce de tissu bleu sur l'oreille gauche.",
            "",
        )
        res = parser_bebe.analyser(texte)
        objet = [p for p in res.personnages if p["code"] == "O1"][0]
        self.assertEqual(objet["prompt_planche"], "")
        self.assertTrue(any("O1" in a.cible for a in res.avertissements))

    def test_un_document_qui_nest_pas_un_pack_est_refuse_proprement(self):
        res = parser_bebe.analyser("Bonjour, voici dix idées d'histoires de bébés.")
        self.assertEqual(res.scenes, [])
        self.assertEqual(res.personnages, [])
        self.assertTrue(res.avertissements)


class TestImportBebe(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()

    def client(self):
        from fastapi.testclient import TestClient

        from videroo.app import app

        return TestClient(app)

    def test_un_projet_bebe_lit_son_pack(self):
        pid = services.creer_projet("Bébé", "bebe")
        bilan = services.appliquer_pack(pid, services.analyser_pack(PACK_BEBE, "bebe"))
        self.assertEqual(bilan["personnages"], 3)
        self.assertEqual(bilan["scenes"], 2)

    def test_les_objets_deviennent_des_references_a_joindre(self):
        from videroo import runner

        pid = services.creer_projet("Bébé", "bebe")
        services.appliquer_pack(pid, services.analyser_pack(PACK_BEBE, "bebe"))
        scene = db.q1("SELECT * FROM scenes WHERE projet_id=? AND numero=1", (pid,))
        manquantes = runner._refs_manquantes(pid, scene)
        self.assertTrue(
            any(m.startswith("O1") for m in manquantes),
            "tant que la planche de l'objet n'existe pas, la scène n'est pas prête",
        )
        self.assertFalse(
            any("absent du projet" in m for m in manquantes),
            "l'objet doit exister comme référence du projet",
        )

    def test_les_durees_du_pack_sont_celles_que_grok_accepte(self):
        from videroo import reglages

        pid = services.creer_projet("Bébé", "bebe")
        services.appliquer_pack(pid, services.analyser_pack(PACK_BEBE, "bebe"))
        acceptees = reglages.durees_grok()
        for s in db.q("SELECT numero, duree FROM scenes WHERE projet_id=?", (pid,)):
            self.assertIn(s["duree"], acceptees, f"scène {s['numero']}")

    def test_creer_et_importer_un_bebe_par_les_vraies_pages(self):
        with self.client() as client:
            reponse = client.post(
                "/projets/nouveau",
                data={"nom": "Mon bébé", "type_histoire": "bebe"},
                follow_redirects=False,
            )
            projet_id = int(reponse.headers["location"].split("/")[2])
            suite = client.post(
                f"/projets/{projet_id}/importer",
                data={"texte": PACK_BEBE},
                follow_redirects=False,
            )
        self.assertEqual(suite.status_code, 303)
        self.assertEqual(
            len(db.q("SELECT id FROM scenes WHERE projet_id=?", (projet_id,))), 2
        )
        self.assertEqual(
            db.q1("SELECT type_histoire FROM projets WHERE id=?", (projet_id,))[
                "type_histoire"
            ],
            "bebe",
        )



class TestPagesDesTypes(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()

    def client(self):
        from fastapi.testclient import TestClient

        from videroo.app import app

        return TestClient(app)

    def test_le_choix_est_propose_a_la_creation(self):
        with self.client() as client:
            page = client.get("/").text
        self.assertIn('name="type_histoire"', page)
        for libelle in config.TYPES_PROJET.values():
            self.assertIn(libelle, page)

    def test_la_page_metaprompt_montre_les_trois(self):
        with self.client() as client:
            page = client.get("/metaprompt").text
        for libelle in config.TYPES_PROJET.values():
            self.assertIn(libelle, page)
        self.assertIn("RÉALISATEUR DE CHRONIQUES", page)
        self.assertIn("BABY REALISTIC STORY DRAMA", page)

    def test_creer_un_projet_chronique_par_la_page(self):
        with self.client() as client:
            reponse = client.post(
                "/projets/nouveau",
                data={"nom": "Ma chronique", "type_histoire": "chronique"},
                follow_redirects=False,
            )
        self.assertEqual(reponse.status_code, 303)
        projet_id = int(reponse.headers["location"].split("/")[2])
        ligne = db.q1("SELECT type_histoire FROM projets WHERE id=?", (projet_id,))
        self.assertEqual(ligne["type_histoire"], "chronique")

    def test_importer_un_pack_chronique_par_la_page(self):
        with self.client() as client:
            reponse = client.post(
                "/projets/nouveau",
                data={"nom": "Ma chronique", "type_histoire": "chronique"},
                follow_redirects=False,
            )
            projet_id = int(reponse.headers["location"].split("/")[2])
            suite = client.post(
                f"/projets/{projet_id}/importer",
                data={"texte": PACK_CHRONIQUE},
                follow_redirects=False,
            )
        self.assertEqual(suite.status_code, 303)
        self.assertIn("2+personnage", suite.headers["location"])
        self.assertEqual(
            len(db.q("SELECT id FROM scenes WHERE projet_id=?", (projet_id,))), 2
        )


if __name__ == "__main__":
    unittest.main()


class TestChroniqueEtiquettesSeules(unittest.TestCase):
    """« IMAGE (…) : » seul sur sa ligne, le prompt juste en dessous."""

    PACK = (
        "PARTIE 1/7 — LE GARÇON À LA FENÊTRE\n"
        "RÉFÉRENCE KAIRO :\nPlanche de Kairo.\n\n"
        "RÉFÉRENCE MAYA :\nPlanche de Maya.\n"
        "SCÈNE 1\n"
        "IMAGE (MAYA, KAIRO) :\nPalier, Maya barre la porte.\n\n"
        "VIDÉO (dynamique · 10 s · MAYA @image2, KAIRO @image3) :\nMaya refuse.\n"
        "SCÈNE 2\n"
        "IMAGE (KAIRO) :\nEscalier.\n\n"
        "VIDÉO (action · 6 s · KAIRO @image2) :\n"
        "🔥 TITRE MINIATURE : IL EST REVENU\n"
    )

    def test_le_texte_sous_l_etiquette_est_lu(self):
        from videroo import parser_chronique
        r = parser_chronique.analyser(self.PACK)
        self.assertEqual(r.titre, "LE GARÇON À LA FENÊTRE")
        self.assertEqual([p["prompt_planche"] for p in r.personnages],
                         ["Planche de Kairo.", "Planche de Maya."])
        s1, s2 = r.scenes
        self.assertEqual(s1["prompt_image"], "Palier, Maya barre la porte.")
        self.assertEqual(s1["prompt_video"], "Maya refuse.")
        self.assertEqual(s1["refs_video"], ["R2", "R1"])
        self.assertEqual(s2["prompt_image"], "Escalier.")
        # Une étiquette suivie d'une autre reste vide, et c'est signalé.
        self.assertEqual(s2["prompt_video"], "")
        self.assertEqual(r.titre_miniature, "IL EST REVENU")
        self.assertIn("Prompt de vidéo vide.", [a.message for a in r.avertissements])


class TestV6EpisodeSuivant(unittest.TestCase):
    """Épisode 3 de Secret Pets : titres à emoji, « Version normale · 6 s »,
    personnages nommés sans planche, références données par nom de fichier."""

    PACK = (
        "Secret Pets · Épisode 3 — Le faux buzz\n"
        "On reprend le même soir.\n\n"
        "Durée — 77 secondes, en 10 scènes.\n"
        "Personnages — Rox, Pixel, Splash et Pablo.\n\n"
        "🎞️ Scène 1 — Le prix du test · 6 s · 00:00–00:06\n"
        "Ce qui change — Rox accepte le test.\n\n"
        "🎭 Références à charger dans ChatGPT — Rox, Pixel(1).PNG, Pablo(4).PNG.\n\n"
        "🧾 Inventaire de l’image de départ — Rox à gauche, Pixel au centre.\n\n"
        "⏱️ Durée à régler dans Grok — 6 secondes.\n\n"
        "🖼️ PROMPT IMAGE\n\n"
        "Créer une image verticale 9:16 dans le salon.\n\n"
        "🎬 Version normale · 6 s ✅ recommandée pour rendre le prix clair\n\n"
        "Vertical 9:16 cinematic 3D animation. Animate @image1.\n\n"
        "RÉPLIQUE 1 — L’HOMME EN VESTE BORDEAUX : \"Tu perds ton prochain buzz (ferme).\"\n"
        "🎞️ Scène 2 — Splash a entendu autre chose · 6 S · 00:06–00:12\n"
        "Ce qui change — Splash situe l’aboiement.\n\n"
        "🎭 Références à charger dans ChatGPT — Pixel(1).PNG, Splash.PNG.\n\n"
        "🧾 Inventaire de l’image de départ — Pixel à gauche.\n\n"
        "⏱️ Durée à régler dans Grok — 6 secondes.\n\n"
        "🖼️ PROMPT IMAGE\n\n"
        "Créer une image du coin canapé.\n\n"
        "🎬 Version normale · 6 s ✅ recommandée\n\n"
        "Vertical 9:16 cinematic 3D animation.\n"
    )

    def test_scenes_personnages_et_references_sont_lus(self):
        from videroo import parser_v6
        r = parser_v6.analyser(self.PACK)
        self.assertEqual([p["surnom"] for p in r.personnages], ["ROX", "PIXEL", "SPLASH", "PABLO"])
        s1, s2 = r.scenes
        self.assertEqual((s1["titre"], s1["duree"], s1["version_video"]), ("Le prix du test", 6, "normale"))
        self.assertEqual(s1["prompt_image"], "Créer une image verticale 9:16 dans le salon.")
        self.assertTrue(s1["prompt_video"].startswith("Vertical 9:16"))
        self.assertEqual(s1["refs_codes"], ["R1", "R2", "R4"])
        self.assertEqual(s2["refs_codes"], ["R2", "R3"])
        self.assertEqual(s2["duree"], 6)
        self.assertEqual(s1["inventaire"], "Rox à gauche, Pixel au centre.")
        self.assertEqual(s1["ce_qui_change"], "Rox accepte le test.")


class TestV6TableauEtNouvellePlanche(unittest.TestCase):
    PACK = (
        "R1\tOrangino, orange, 26 ans\tBleu de travail\n"
        "R3\tCourgetina, courgette, 34 ans\tTailleur crème\n"
        "P2 — LA VOITURE VOLANTE D'ORANGINO\n"
        "🎨 PROMPT PLANCHE DE RÉFÉRENCE — PROTOTYPE AÉRO 01\n"
        "Crée une planche de référence technique.\n"
        "SCÈNE 1 — L'essai — 6 secondes\n"
        "🎭 Références : R1 Orangino, R3 Courgetina, P2 AÉRO 01\n"
    )

    def test_codes_du_tableau_et_de_la_planche(self):
        from videroo import parser_v6
        r = parser_v6.analyser(self.PACK)
        self.assertEqual(sorted(p["code"] for p in r.personnages), ["P2", "R1", "R3"])
        self.assertEqual(r.scenes[0]["refs_codes"], ["R1", "R3", "P2"])


class TestChroniqueReferenceRepetee(unittest.TestCase):
    def test_une_seule_planche_par_personnage(self):
        from videroo import parser_chronique
        pack = (
            "RÉFÉRENCE KENZA : Planche Kenza.\nRÉFÉRENCE ILYES : Planche Ilyes.\n"
            "RÉFÉRENCE KENZA : Planche Kenza.\nRÉFÉRENCE AMIRA : Planche Amira.\n"
            "IMAGE (Kenza, Amira) : Rue.\nVIDÉO (dialogue · 6 s · Kenza @image2, Amira @image3) : Parle.\n"
        )
        r = parser_chronique.analyser(pack)
        self.assertEqual([p["surnom"] for p in r.personnages], ["KENZA", "ILYES", "AMIRA"])
        self.assertEqual(r.scenes[0]["refs_video"], ["R1", "R3"])


class TestChroniqueDeuxEpisodesColles(unittest.TestCase):
    def test_seul_le_dernier_episode_est_lu(self):
        from videroo import parser_chronique
        bloc = lambda n: (
            f"SERIE — ÉPISODE {n} : TITRE\nRÉFÉRENCE KENZA : Planche.\n"
            f"IMAGE (Kenza) : Rue {n}.\nVIDÉO (action · 6 s · Kenza @image2) : Marche.\n"
        )
        r = parser_chronique.analyser(bloc(2) + bloc(3))
        self.assertEqual(len(r.scenes), 1)
        self.assertEqual(r.scenes[0]["prompt_image"], "Rue 3.")
        self.assertIn("épisode 3", " ".join(a.message for a in r.avertissements))


class TestQuinzeScenes(unittest.TestCase):
    def test_un_episode_de_15_scenes_passe_sans_alerte_de_duree(self):
        from videroo import parser_v6
        scene = lambda n: (
            f"🎞️ Scène {n} — Plan {n} · 10 s · 00:00–00:10\n"
            "Ce qui change — ça avance.\n\n🎭 Références à charger dans ChatGPT — Rox.\n\n"
            "🖼️ PROMPT IMAGE\n\nUne image.\n\n🎬 Version normale · 10 s ✅\n\nAnime @image1.\n"
        )
        pack = "Secret Pets · Épisode 5 — Long\nPersonnages — Rox.\n\n" + "".join(scene(n) for n in range(1, 16))
        r = parser_v6.analyser(pack)
        self.assertEqual(len(r.scenes), 15)
        self.assertEqual(r.duree_calculee, 150)
        self.assertFalse([a for a in r.avertissements if a.cible == "duree"])


class TestReferenceAuSingulier(unittest.TestCase):
    def test_une_seule_reference_est_lue(self):
        from videroo import parser_v6
        pack = (
            "PLANCHE DE RÉFÉRENCE — NAVETINA\nPlanche de Navetina.\n"
            "SCÈNE 4 — 🪞 Seule devant son miroir — 6 secondes\n"
            "Attache : Navetina.\n\n🎭 Référence ChatGPT : NAVETINA.\n\n"
            "🖼️ PROMPT IMAGE\n\nUne chambre.\n\n🎬 Version normale : 6 s ✅\n\nAnime @image1.\n"
        )
        r = parser_v6.analyser(pack)
        self.assertEqual(r.scenes[0]["refs_codes"], ["R1"])


class TestV6PromptGrok(unittest.TestCase):
    def test_format_prompt_grok_et_repliques_nommees(self):
        from videroo import parser_v6
        pack = (
            "Secret Pets — Épisode 5 · Le prix du micro\n\n77 secondes · 1 scènes · Vertical 9:16\n"
            "Personnages — Rox, Pixel.\n\n"
            "🎞️ Scène 1 — Le marché · 00:00–00:06\nCe qui change — Pixel lit.\n\n"
            "🎭 Références — Rox, Pixel(1).PNG.\n🧾 Image de départ — Pixel à gauche.\n⏱️ Grok — 6 secondes\n\n"
            "🖼️ PROMPT IMAGE\n\nUn local.\n\n🎬 PROMPT GROK — Version normale · 6 s\n\nAnimation.\n\n"
            "PIXEL — « Ils veulent ton micro. »\n\nROX — « Donc non. »\n"
        )
        r = parser_v6.analyser(pack)
        s = r.scenes[0]
        self.assertEqual((s["duree"], s["version_video"], s["refs_codes"]), (6, "normale", ["R1", "R2"]))
        self.assertEqual([d["etiquette"] for d in s["dialogues"]], ["PIXEL", "ROX"])
        self.assertEqual(s["inventaire"], "Pixel à gauche.")
        self.assertEqual(r.nb_scenes_annonce, 1)


class TestTitreNonDeroule(unittest.TestCase):
    """Projet 36 : « Même histoire : mère qui cache sa fille → … » pris pour le
    titre, puis écrit en gros sur la miniature."""

    def test_le_deroule_nest_pas_le_titre(self):
        from videroo import parser_v6

        texte = (
            "Adaptation — « ELLE AVAIT HONTE DE SA FILLE »\n"
            "📌 Durée : 79 secondes — 11 scènes.\n\n"
            "📌 Même histoire : mère qui cache sa fille → père qui la défend → "
            "repas familial sans elle.\n\n"
            "🎞️ SCÈNE 1 — Le salon · 6 s\nImage : un salon.\n"
        )
        self.assertEqual(parser_v6.analyser(texte).titre, "ELLE AVAIT HONTE DE SA FILLE")

    def test_une_vraie_etiquette_histoire_reste_lue(self):
        from videroo import parser_v6

        texte = "🎬 HISTOIRE : Le prix du micro\n\n🎞️ SCÈNE 1 — Le salon · 6 s\nImage : x.\n"
        self.assertEqual(parser_v6.analyser(texte).titre, "Le prix du micro")


class TestLecteurQuiApprend(unittest.TestCase):
    """Un format jamais vu : les étiquettes sont nommées, apprises une fois,
    puis lues partout — sans toucher au code."""

    PACK = (
        "🎬 HISTOIRE : Le badge\n\n"
        "📍 PLAN 1 — Le marché · 00:00–00:06\n"
        "👥 Casting : Rox\n"
        "⌛ Timing : 6 s\n"
        "🖌️ VISUEL CHATGPT\n"
        "Créer une image verticale 9:16 de Rox dans le local.\n"
        "🎥 ANIMATION · 6 s\n"
        "Animation 3D verticale 9:16. Caméra : lente.\n"
        "ROX — « On y va. »\n\n"
        "📍 PLAN 2 — Le salon · 00:06–00:16\n"
        "⌛ Timing : 10 s\n"
        "🖌️ VISUEL CHATGPT\n"
        "Créer une image du salon.\n"
        "🎥 ANIMATION · 10 s\n"
        "Animation du salon.\n\n"
        "📊 Script complet · 16 secondes\n"
        "Tableau qui n'appartient à aucune scène.\n"
    )

    def setUp(self):
        tests.reinitialiser()

    def test_format_inconnu_puis_appris(self):
        from videroo import lecteur_souple, services

        avant = services.analyser_pack(self.PACK, "fruits")
        self.assertEqual([s["numero"] for s in avant.scenes], [1, 2])
        self.assertEqual(avant.scenes[0]["titre"], "Le marché")
        nommees = {e["etiquette"] for e in avant.etiquettes_inconnues}
        self.assertEqual(nommees, {"Casting", "Timing", "VISUEL CHATGPT", "ANIMATION · 6 s"})

        lecteur_souple.apprendre({
            "VISUEL CHATGPT": "prompt_image", "ANIMATION": "prompt_video",
            "Timing": "duree", "Casting": "refs",
        })
        apres = services.analyser_pack(self.PACK, "fruits")
        s1, s2 = apres.scenes
        self.assertEqual(s1["prompt_image"], "Créer une image verticale 9:16 de Rox dans le local.")
        # « Caméra : lente » reste dans le prompt : ce n'est pas un champ.
        self.assertIn("Caméra : lente.", s1["prompt_video"])
        self.assertEqual((s1["duree"], s2["duree"]), (6, 10))
        self.assertEqual(s1["refs_brut"], "Rox")
        self.assertEqual(s2["prompt_video"], "Animation du salon.")  # pas le tableau final
        self.assertEqual(apres.etiquettes_inconnues, [])

    def test_ignorer_fait_taire_une_etiquette(self):
        from videroo import lecteur_souple, services

        lecteur_souple.apprendre({"Casting": "ignorer"})
        res = services.analyser_pack(self.PACK, "fruits")
        self.assertNotIn("Casting", {e["etiquette"] for e in res.etiquettes_inconnues})
        self.assertEqual(res.scenes[0]["refs_brut"], "")

    def test_le_format_connu_ne_change_pas(self):
        from videroo import parser_v6, services

        texte = tests.pack_variantes()
        self.assertEqual(
            services.analyser_pack(texte, "fruits").scenes, parser_v6.analyser(texte).scenes
        )


class TestEcranApprendre(unittest.TestCase):
    def test_la_page_propose_puis_retient(self):
        from fastapi.testclient import TestClient
        from videroo import app as module_app, db, lecteur_souple, services

        tests.reinitialiser()
        pid = services.creer_projet("Nouveau format", "fruits")
        services.appliquer_pack(
            pid, services.analyser_pack(TestLecteurQuiApprend.PACK, "fruits", pid)
        )
        client = TestClient(module_app.app)
        page = client.get(f"/projets/{pid}/histoire").text
        self.assertIn("Apprends-moi ce nouveau format", page)
        self.assertIn("VISUEL CHATGPT", page)

        reponse = client.post(
            f"/projets/{pid}/etiquettes",
            data={"e::visuel chatgpt": "prompt_image", "e::animation": "prompt_video",
                  "e::timing": "duree", "e::casting": "ignorer"},
            follow_redirects=False,
        )
        self.assertIn(reponse.status_code, (302, 303))
        scenes = db.q("SELECT * FROM scenes WHERE projet_id=? ORDER BY numero", (pid,))
        self.assertEqual([s["duree"] for s in scenes], [6, 10])
        self.assertTrue(all(s["prompt_image"] and s["prompt_video"] for s in scenes))
        self.assertEqual(lecteur_souple.apprises()["timing"], "duree")
        self.assertNotIn("Apprends-moi", client.get(f"/projets/{pid}/histoire").text)


class TestPackEnPlansAvecBlocCommun(unittest.TestCase):
    """« Le mariage forcé » (16 novembre 2026) : un résumé en SCÈNES sans prompt,
    puis 26 PLANS qui renvoient à un BLOC COMMUN et finissent par une note de
    montage à ne pas lire. Les planches sont déjà dans le projet."""

    PACK = (
        "LE MARIAGE FORCÉ\n\n"
        "SCÈNE 1 — L’ACCROCHE | 00:00–00:07\nRésumé sans prompt.\n\n"
        "SCÈNE 2 — LA SUITE | 00:07–00:10\nRésumé sans prompt.\n\n"
        "============\n3. BLOC COMMUN À JOINDRE À CHAQUE PROMPT VIDÉO\n============\n\n"
        "Créer un plan photoréaliste vertical 9:16.\nUne seule personne parle.\n\n"
        "============\n5. DÉCOUPAGE\n============\n"
        "TEXTES À AJOUTER AU MONTAGE\nPlan 1 : « TITRE » en surimpression.\n\n"
        "PLAN 01 — LE BOUQUET\n"
        "Repère dans le montage brut : 00:00–00:07.\n"
        "Durée à conserver avant accélération : 7 secondes.\n"
        "Références de personnages à joindre : Nour, Samira.\n\n"
        "PROMPT IMAGE DE DÉPART\nAppliquer les règles visuelles du BLOC COMMUN.\nVestibule fleuri.\n\n"
        "PROMPT VIDÉO\nAppliquer le BLOC COMMUN.\nNour regarde sa mère.\n"
        "« Maman… pourquoi tu me donnes le bouquet ? »\n\n"
        "NOTE DE RACCORDEMENT / MONTAGE — NE PAS LIRE À VOIX HAUTE\nNe pas lire.\n\n"
        "PLAN 02 — LA CONSIGNE\n"
        "Durée à conserver avant accélération : 3 secondes.\n"
        "Références de personnages à joindre : Samira.\n\n"
        "PROMPT IMAGE DE DÉPART\nChambre claire.\n\n"
        "PROMPT VIDÉO\nSamira parle.\n\n"
        "NOTE DE RACCORDEMENT / MONTAGE — NE PAS LIRE À VOIX HAUTE\nNe pas lire.\n\n"
        "============\nFIN\n"
    )

    def test_les_plans_partent_complets(self):
        from videroo import db, lecteur_souple, services

        tests.reinitialiser()
        pid = services.creer_projet("Mariage", "chronique")
        for i, nom in enumerate(("NOUR", "SAMIRA"), 1):
            db.exec1(
                "INSERT INTO personnages (projet_id, code, surnom, etat, ordre)"
                " VALUES (?,?,?,?,?)", (pid, f"R{i}", nom, "UNIQUE", i),
            )
        lecteur_souple.apprendre({
            "PROMPT IMAGE DE DÉPART": "prompt_image",
            "Références de personnages à joindre": "refs",
            "Durée à conserver avant accélération": "duree",
            "Repère dans le montage brut": "ignorer",
            "NOTE DE RACCORDEMENT / MONTAGE": "ignorer",
        })
        res = services.analyser_pack(self.PACK, "chronique", pid)
        self.assertEqual([s["titre"] for s in res.scenes], ["LE BOUQUET", "LA CONSIGNE"])
        p1, p2 = res.scenes
        # Le bloc remplace le renvoi, tel quel ; le reste du prompt est intact.
        self.assertTrue(p1["prompt_video"].startswith("Créer un plan photoréaliste"))
        self.assertIn("« Maman… pourquoi tu me donnes le bouquet ? »", p1["prompt_video"])
        self.assertNotIn("BLOC COMMUN", p1["prompt_image"])
        self.assertNotIn("Ne pas lire", p1["prompt_video"])  # la note ne part pas
        self.assertEqual((p1["duree"], p2["duree"]), (10, 6))  # durées Grok
        self.assertEqual(p1["refs_codes"], ["R1", "R2"])
        self.assertEqual(p1["refs_video"], ["R1", "R2"])  # planches jointes à Grok
        self.assertEqual([p["surnom"] for p in res.personnages], ["NOUR", "SAMIRA"])


class TestTypeSerieEnPlans(unittest.TestCase):
    """Le type « Série réaliste en plans » lit ce format sans rien apprendre."""

    def test_le_pack_se_lit_tout_seul(self):
        from videroo import db, lecteur_souple, services

        tests.reinitialiser()
        self.assertEqual(lecteur_souple.apprises(), {})
        pid = services.creer_projet("Série", "serie")
        for i, nom in enumerate(("NOUR", "SAMIRA"), 1):
            db.exec1(
                "INSERT INTO personnages (projet_id, code, surnom, etat, ordre)"
                " VALUES (?,?,?,?,?)", (pid, f"R{i}", nom, "UNIQUE", i),
            )
        texte = "Objectif : 2 minutes après accélération ×1,3.\n" + TestPackEnPlansAvecBlocCommun.PACK
        res = services.analyser_pack(texte, "serie", pid)
        self.assertEqual(len(res.scenes), 2)
        self.assertEqual(res.etiquettes_inconnues, [])
        self.assertTrue(all(s["prompt_image"] and s["prompt_video"] for s in res.scenes))
        self.assertEqual(res.vitesse, 1.3)
        # Aucun autre lecteur n'a pris le résumé (« Règle du méchant », etc.).
        self.assertFalse([a for a in res.avertissements if a.niveau != "info"])
        services.appliquer_pack(pid, res)
        self.assertEqual(services.projet(pid)["vitesse"], 1.3)
