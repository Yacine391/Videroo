"""Secret Pets, épisode 9 (17 novembre 2026) : la scène 7 jamais générée.

Deux défauts dans la lecture du pack, et un écran qui ne disait rien :

* « Personnages — Rox, Coco, Kiki, Luna et Pixel. Shadow apparaît dans
  l'archive. » donnait un 5ᵉ personnage « PIXEL. SHADOW APPARAÎT DANS
  L'ARCHIVE » : Pixel n'était reconnu dans aucune scène.
* La scène 7 est une archive d'objets (« Aucun visage ni personnage en entier
  dans le cadre ») : la règle « au moins un personnage de référence » la
  refusait, même validée à la main. Aucune issue.
* L'atelier affichait « Rien en attente » alors que la scène 7 manquait.
"""
import unittest

import tests
from videroo import db, parser_v6, runner, services

PACK = (
    "Secret Pets — Épisode 9 · La candidate de la saison zéro\n"
    "Luna a envoyé la photo à Coco.\n\n"
    "77 secondes · 3 scènes · Vertical 9:16\n"
    "Personnages — Rox, Coco, Kiki, Luna et Pixel. Shadow apparaît dans l’archive.\n\n"
    "🎞️ Scène 1 — Sous la chaise · 00:00–00:06\n"
    "Ce qui change — Coco reconnaît la chaise.\n\n"
    "🎭 Références — Coco(1).PNG, Rox et dernier plan de l’épisode 8.\n"
    "🧾 Image de départ — Coco à gauche, Rox à droite.\n"
    "⏱️ Grok — 6 secondes\n\n"
    "🖼️ PROMPT IMAGE\n\n"
    "Créer une image verticale 9:16 : Coco à gauche, Rox à droite, devant la chaise.\n\n"
    "🎬 PROMPT GROK — Version dynamique · 6 s\n\n"
    "Animer cette image. COCO — « C’est la chaise. »\n"
    "🎞️ Scène 2 — La consigne originale · 00:06–00:12\n"
    "Ce qui change — L’archive confirme le remplacement.\n\n"
    "🎭 Références — Plaque « ROX / CANDIDAT » et boîtier noir des épisodes précédents.\n"
    "🧾 Image de départ — Image d’archive en plein écran.\n"
    "⏱️ Grok — 6 secondes\n\n"
    "🖼️ PROMPT IMAGE\n\n"
    "Créer une image verticale 9:16 : un badge ivoire LUNA et une plaque ROX / CANDIDAT "
    "sur un pupitre. Aucun visage ni personnage en entier dans le cadre.\n\n"
    "🎬 PROMPT GROK — Version dynamique · 6 s\n\n"
    "Animer cette image comme une archive. VOIX D’ARCHIVE — « On la remplace par Rox. »\n"
    "🎞️ Scène 3 — Reviens un peu en arrière · 00:12–00:18\n"
    "Ce qui change — Luna demande à Pixel de remonter la vidéo.\n\n"
    "🎭 Références — Pixel(1).PNG, Rox, Luna.\n"
    "🧾 Image de départ — Pixel à gauche, Rox au centre, Luna à droite.\n"
    "⏱️ Grok — 6 secondes\n\n"
    "🖼️ PROMPT IMAGE\n\n"
    "Créer une image verticale 9:16 : Pixel, Rox et Luna devant l’ordinateur.\n\n"
    "🎬 PROMPT GROK — Version dynamique · 6 s\n\n"
    "Animer cette image. LUNA — « Reviens un peu en arrière. »\n"
)


class TestListeDesPersonnages(unittest.TestCase):
    def test_la_liste_s_arrete_a_la_premiere_phrase(self):
        r = parser_v6.analyser(PACK)
        self.assertEqual([p["surnom"] for p in r.personnages],
                         ["ROX", "COCO", "KIKI", "LUNA", "PIXEL"])

    def test_pixel_est_reconnu_dans_les_scenes(self):
        tests.reinitialiser()
        pid = services.creer_projet("Épisode 9", "fruits")
        services.appliquer_pack(pid, services.analyser_pack(PACK))
        pixel = db.q1("SELECT code FROM personnages WHERE projet_id=? AND surnom='PIXEL'", (pid,))
        self.assertIsNotNone(pixel)
        s3 = db.q1("SELECT refs_codes FROM scenes WHERE projet_id=? AND numero=3", (pid,))
        self.assertIn(pixel["code"], db.charger_json(s3["refs_codes"], []))


class TestPlanSansPersonnage(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()
        self.pid = services.creer_projet("Épisode 9", "fruits")
        services.appliquer_pack(self.pid, services.analyser_pack(PACK))
        self.archive = db.q1("SELECT * FROM scenes WHERE projet_id=? AND numero=2", (self.pid,))

    def test_l_archive_d_objets_se_valide_sans_planche(self):
        self.assertEqual(db.charger_json(self.archive["refs_codes"], []), [])
        self.assertTrue(services.plan_sans_personnage(dict(self.archive)))
        bilan = services.valider_projet(self.pid)
        self.assertIn(2, bilan["validees"], bilan["refusees"])

    def test_un_nom_non_reconnu_garde_le_plan_bloque(self):
        """« Pixel » qui ne correspond à rien n'est pas un objet : on ne valide pas."""
        scene = dict(db.q1("SELECT * FROM scenes WHERE projet_id=? AND numero=3", (self.pid,)))
        scene["refs_codes"] = "[]"
        self.assertFalse(services.plan_sans_personnage(scene))
        soucis = services.problemes_scene(scene, set())
        self.assertEqual(len(soucis), 1)
        self.assertTrue(soucis[0].startswith(services.SANS_REFERENCE))
        self.assertIn("« Pixel »", soucis[0])

    def test_sans_ligne_de_references_on_ne_devine_pas(self):
        scene = dict(self.archive)
        scene["refs_brut"] = ""
        self.assertFalse(services.plan_sans_personnage(scene))
        self.assertEqual(services.problemes_scene(scene, set()), [services.SANS_REFERENCE])

    def test_ta_confirmation_valide_le_plan(self):
        scene = db.q1("SELECT * FROM scenes WHERE projet_id=? AND numero=2", (self.pid,))
        db.exec1("UPDATE scenes SET refs_brut='' WHERE id=?", (scene["id"],))
        with self.assertRaises(services.ErreurMetier):
            services.valider_scene(scene["id"], True)
        services.confirmer_sans_personnage(scene["id"])
        apres = db.q1("SELECT valide, sans_personnage FROM scenes WHERE id=?", (scene["id"],))
        self.assertEqual((apres["valide"], apres["sans_personnage"]), (1, 1))

    def test_la_confirmation_est_refusee_si_la_scene_cite_des_personnages(self):
        scene = db.q1("SELECT * FROM scenes WHERE projet_id=? AND numero=1", (self.pid,))
        with self.assertRaises(services.ErreurMetier):
            services.confirmer_sans_personnage(scene["id"])
        self.assertEqual(
            db.q1("SELECT sans_personnage FROM scenes WHERE id=?", (scene["id"],))["sans_personnage"], 0
        )


class TestLAtelierDitPourquoi(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()
        self.pid = services.creer_projet("Épisode 9", "fruits")
        services.appliquer_pack(self.pid, services.analyser_pack(PACK))
        self.scene = db.q1("SELECT * FROM scenes WHERE projet_id=? AND numero=2", (self.pid,))
        db.exec1("UPDATE scenes SET refs_brut='', valide=0 WHERE id=?", (self.scene["id"],))

    def test_la_scene_hors_chaine_est_nommee_avec_sa_raison(self):
        hors = runner.etat_production(self.pid)["hors_chaine"]
        self.assertIn(2, [h["numero"] for h in hors])
        motif = next(h for h in hors if h["numero"] == 2)["motif"]
        self.assertIn("laissée de côté", motif)
        self.assertIn(services.SANS_REFERENCE, motif)

    def test_la_carte_donne_la_raison_pas_un_renvoi(self):
        with self.assertRaises(runner.GenerationRefusee) as refus:
            runner.controles_avant_lancement(self.pid, "image", self.scene["id"])
        self.assertIn(services.SANS_REFERENCE, str(refus.exception))
        self.assertNotIn("Valide-la dans l'onglet Scènes (c'est", str(refus.exception))

    def test_une_scene_faite_a_la_main_n_est_pas_signalee(self):
        from videroo import fichiers
        pr = services.projet(self.pid)
        image = fichiers.chemin_image_scene(pr["dossier"], 2)
        tests.fabriquer_image_test(image)
        video = fichiers.chemin_video_scene(pr["dossier"], 2)
        from videroo import montage
        montage.fabriquer_clip_test(video, duree=6.0, texte="S2")
        db.exec1("UPDATE scenes SET image_path=?, video_path=? WHERE id=?",
                 (str(image), str(video), self.scene["id"]))
        self.assertNotIn(2, [h["numero"] for h in runner.etat_production(self.pid)["hors_chaine"]])


if __name__ == "__main__":
    unittest.main()
