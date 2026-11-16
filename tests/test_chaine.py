"""Chaîne de production : planches → images → vidéos, une étape à la fois."""
import unittest

import tests
from videroo import config, db, fichiers, montage, runner, services
from videroo.adapters import base


class AdaptateurChaine(base.Adaptateur):
    """Produit de vrais fichiers, très courts, sans toucher à aucun service en ligne."""

    def __init__(self, etat=base.TERMINE):
        self.appels: list[str] = []
        self.etat = etat

    def generer(self, ctx):
        self.appels.append(f"{ctx.cible_type}:{ctx.cible_id}")
        if self.etat != base.TERMINE:
            return base.ResultatGeneration(self.etat, "Pas abouti.")
        ctx.chemin_sortie.parent.mkdir(parents=True, exist_ok=True)
        if ctx.cible_type == "video":
            montage.fabriquer_clip_test(ctx.chemin_sortie, duree=1.0, texte="X")
        else:
            tests.fabriquer_image_test(ctx.chemin_sortie, texte=ctx.cible_type[:3])
        return base.ResultatGeneration(
            base.TERMINE, "Produit.", chemin=str(ctx.chemin_sortie)
        )


class BaseChaine(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()
        self.projet_id = services.creer_projet("Chaîne")
        services.appliquer_pack(
            self.projet_id, services.analyser_pack(tests.pack_variantes())
        )
        services.valider_projet(self.projet_id)
        self.adaptateur = AdaptateurChaine()
        for fournisseur in ("chatgpt", "grok"):
            runner.enregistrer_adaptateur(fournisseur, "chaine", self.adaptateur)

    def derouler(self, maximum: int = 40) -> int:
        """Exécute les tâches tant que la chaîne en propose, comme le worker."""
        tours = 0
        while tours < maximum:
            tache = runner.Worker.prochaine()
            if tache is None:
                break
            runner.Worker.executer(tache["id"])
            tours += 1
        return tours


class TestOrdreDeProduction(BaseChaine):
    def test_les_planches_passent_avant_les_images(self):
        cible = runner.prochaine_cible(self.projet_id)
        self.assertIsNotNone(cible)
        self.assertEqual(cible[0], "planche")
        self.assertEqual(cible[2], "chatgpt")

    def test_une_image_attend_ses_planches(self):
        """Tant qu'une planche manque, aucune image de cette scène n'est proposée."""
        for _ in range(3):
            cible = runner.prochaine_cible(self.projet_id)
            self.assertEqual(cible[0], "planche", "les planches d'abord")
            chemin = runner.chemin_attendu(self.projet_id, cible[0], cible[1])
            tests.fabriquer_image_test(chemin)
            runner._enregistrer_resultat_existant(
                self.projet_id, cible[0], cible[1], chemin
            )
        cible = runner.prochaine_cible(self.projet_id)
        self.assertEqual(cible[0], "image")
        self.assertEqual(cible[2], "chatgpt")

    def test_la_video_suit_son_image(self):
        for p in db.q("SELECT * FROM personnages WHERE projet_id=?", (self.projet_id,)):
            chemin = fichiers.chemin_planche(
                services.projet(self.projet_id)["dossier"], p["code"], p["surnom"], p["etat"]
            )
            tests.fabriquer_image_test(chemin)
            db.exec1("UPDATE personnages SET ref_path=? WHERE id=?", (str(chemin), p["id"]))
        scene = db.q1(
            "SELECT * FROM scenes WHERE projet_id=? AND numero=1", (self.projet_id,)
        )
        image = fichiers.chemin_image_scene(
            services.projet(self.projet_id)["dossier"], 1
        )
        tests.fabriquer_image_test(image)
        db.exec1("UPDATE scenes SET image_path=? WHERE id=?", (str(image), scene["id"]))
        # Chaque fournisseur avance dans SA file : des que l'image de la scene 1
        # est la, Grok peut l'animer pendant que ChatGPT prepare la scene 2.
        self.assertEqual(
            runner.prochaine_cible(self.projet_id, pour="grok"),
            ("video", scene["id"], "grok"),
        )
        suivante_chatgpt = runner.prochaine_cible(self.projet_id, pour="chatgpt")
        self.assertEqual(suivante_chatgpt[0], "image")
        self.assertNotEqual(suivante_chatgpt[1], scene["id"])


class TestChaineComplete(BaseChaine):
    def test_tout_est_produit_dans_l_ordre(self):
        runner.demarrer_production(self.projet_id, "chaine")
        tours = self.derouler()
        self.assertGreater(tours, 0)

        types = [a.split(":")[0] for a in self.adaptateur.appels]
        self.assertEqual(types[:3], ["planche", "planche", "planche"])
        self.assertEqual(types.count("image"), 3)
        self.assertEqual(types.count("video"), 3)
        # une image precede toujours la video de la meme scene
        self.assertLess(types.index("image"), types.index("video"))

        d = services.detail(self.projet_id)
        self.assertTrue(all(p["ref_ok"] for p in d["personnages"]), "planches")
        self.assertTrue(all(s["image_ok"] for s in d["scenes"]), "images")
        self.assertTrue(all(s["video_ok"] for s in d["scenes"]), "vidéos")
        self.assertFalse(services.projet(self.projet_id)["pipeline"], "chaîne terminée")

    def test_une_seule_tache_par_element(self):
        runner.demarrer_production(self.projet_id, "chaine")
        self.derouler()
        doublons = db.q(
            "SELECT cible_type, cible_id, COUNT(*) n FROM taches WHERE projet_id=?"
            " GROUP BY cible_type, cible_id HAVING n > 1",
            (self.projet_id,),
        )
        self.assertEqual([dict(d) for d in doublons], [])
        self.assertEqual(len(self.adaptateur.appels), len(set(self.adaptateur.appels)))

    def test_jamais_deux_generations_chez_le_meme_fournisseur(self):
        """ChatGPT et Grok avancent en parallèle, mais chacun un élément à la fois.

        La règle d'origine « une seule génération à la fois » devient « une seule
        par fournisseur » : ils ne partagent ni compte ni quota, et les faire
        attendre l'un l'autre doublait la durée de la chaîne sans rien protéger.
        La garantie qui compte — une seule génération automatique par élément —
        est inchangée.
        """
        runner.demarrer_production(self.projet_id, "chaine")
        for _ in range(12):
            for fournisseur in runner.FOURNISSEURS:
                vivantes = db.q(
                    "SELECT id FROM taches WHERE projet_id=? AND fournisseur=?"
                    " AND statut IN ('en_attente','en_cours')",
                    (self.projet_id, fournisseur),
                )
                self.assertLessEqual(
                    len(vivantes), 1, f"une seule tâche à la fois chez {fournisseur}"
                )
            tache = runner.Worker.prochaine()
            if tache is None:
                break
            runner.Worker.executer(tache["id"])
        # et toujours aucune génération en double sur un même élément
        doublons = db.q(
            "SELECT cible_type, cible_id, COUNT(*) n FROM taches"
            " WHERE projet_id=? AND origine='auto' GROUP BY cible_type, cible_id"
            " HAVING n > 1",
            (self.projet_id,),
        )
        self.assertEqual([dict(d) for d in doublons], [])

    def test_chatgpt_et_grok_sont_lances_ensemble(self):
        """Dès qu'une image existe, sa vidéo part pendant que l'image suivante se fait."""
        runner.demarrer_production(self.projet_id, "chaine")
        vus = set()
        for _ in range(12):
            tache = runner.Worker.prochaine()
            if tache is None:
                break
            runner.Worker.executer(tache["id"])
            vivantes = db.q(
                "SELECT fournisseur FROM taches WHERE projet_id=? AND statut='en_attente'",
                (self.projet_id,),
            )
            vus.update(v["fournisseur"] for v in vivantes)
            if len(vus) > 1:
                break
        self.assertEqual(
            vus, {"chatgpt", "grok"},
            "les deux fournisseurs doivent avoir du travail en file en même temps",
        )


class TestChaineSArrete(BaseChaine):
    def test_un_element_non_abouti_met_la_chaine_en_attente(self):
        bloqueur = AdaptateurChaine(etat=base.A_VERIFIER)
        runner.enregistrer_adaptateur("chatgpt", "bloque", bloqueur)
        runner.demarrer_production(self.projet_id, "bloque")
        tache = runner.Worker.prochaine()
        runner.Worker.executer(tache["id"])
        self.assertFalse(
            services.projet(self.projet_id)["pipeline"], "la chaîne doit s'arrêter"
        )
        self.assertIsNone(runner.Worker.prochaine(), "aucune relance automatique")
        self.assertEqual(len(bloqueur.appels), 1)

    def test_pause_puis_reprise(self):
        runner.demarrer_production(self.projet_id, "chaine")
        tache = runner.Worker.prochaine()
        runner.Worker.executer(tache["id"])
        runner.changer_statut_projet(self.projet_id, "pause")
        self.assertIsNone(runner.Worker.prochaine(), "en pause, rien ne part")
        runner.changer_statut_projet(self.projet_id, "en_cours")
        runner.planifier(self.projet_id)
        self.assertIsNotNone(runner.Worker.prochaine())

    def test_arret_du_projet_coupe_la_chaine(self):
        runner.demarrer_production(self.projet_id, "chaine")
        runner.arreter_projet(self.projet_id)
        self.assertFalse(services.projet(self.projet_id)["pipeline"])
        self.assertIsNone(runner.Worker.prochaine())

    def test_arret_explicite_de_la_chaine(self):
        runner.demarrer_production(self.projet_id, "chaine")
        runner.arreter_production(self.projet_id)
        self.assertFalse(services.projet(self.projet_id)["pipeline"])
        self.derouler()
        # la tache deja en file part, mais la chaine ne propose plus rien ensuite
        self.assertLessEqual(len(self.adaptateur.appels), 1)


class TestChaineAssistee(BaseChaine):
    def test_l_import_manuel_fait_avancer_la_chaine(self):
        runner.demarrer_production(self.projet_id, "chaine")
        premiere = db.q1(
            "SELECT * FROM taches WHERE projet_id=? ORDER BY id LIMIT 1", (self.projet_id,)
        )
        self.assertEqual(premiere["cible_type"], "planche")

        chemin = runner.chemin_attendu(self.projet_id, "planche", premiere["cible_id"])
        tests.fabriquer_image_test(chemin, texte="IMPORT")
        services.importer_media(
            self.projet_id, "planche", premiere["cible_id"], "ref.png", chemin.read_bytes()
        )
        suivante = db.q1(
            "SELECT * FROM taches WHERE projet_id=? AND statut='en_attente'"
            " ORDER BY id DESC LIMIT 1",
            (self.projet_id,),
        )
        self.assertIsNotNone(suivante, "l'import doit enclencher l'étape suivante")
        self.assertNotEqual(suivante["cible_id"], premiere["cible_id"])


class TestEtatProduction(BaseChaine):
    def test_compteurs_et_etape_suivante(self):
        etat = runner.etat_production(self.projet_id)
        self.assertFalse(etat["active"])
        self.assertEqual(etat["restants"], {"planche": 3, "image": 3, "video": 3})
        self.assertEqual(etat["suivante"]["cible_type"], "planche")

        runner.demarrer_production(self.projet_id, "chaine")
        self.assertTrue(runner.etat_production(self.projet_id)["active"])
        self.derouler()
        final = runner.etat_production(self.projet_id)
        self.assertFalse(final["active"])
        self.assertIsNone(final["suivante"])
        self.assertEqual(final["restants"], {"planche": 0, "image": 0, "video": 0})

    def test_refus_si_aucune_scene_validee(self):
        autre = services.creer_projet("Sans validation")
        services.appliquer_pack(autre, services.analyser_pack(tests.pack_variantes()))
        with self.assertRaises(runner.GenerationRefusee) as ctx:
            runner.demarrer_production(autre, "chaine")
        self.assertIn("Aucune scène validée", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


class TestChaineAssisteeReelle(BaseChaine):
    """En mode assisté, « à vérifier » est l'état normal : la chaîne attend l'import."""

    def setUp(self):
        super().setUp()
        self.assiste = AdaptateurChaine(etat=base.A_VERIFIER)
        for fournisseur in ("chatgpt", "grok"):
            runner.enregistrer_adaptateur(fournisseur, "assiste", self.assiste)

    def test_la_chaine_reste_armee_et_avance_a_l_import(self):
        runner.demarrer_production(self.projet_id, "assiste")
        tache = runner.Worker.prochaine()
        runner.Worker.executer(tache["id"])

        self.assertTrue(
            services.projet(self.projet_id)["pipeline"],
            "la chaîne doit rester armée en mode assisté",
        )
        self.assertIsNone(runner.Worker.prochaine(), "rien ne part sans l'import")

        chemin = runner.chemin_attendu(self.projet_id, "planche", tache["cible_id"])
        tests.fabriquer_image_test(chemin, texte="OK")
        services.importer_media(
            self.projet_id, "planche", tache["cible_id"], "ref.png", chemin.read_bytes()
        )
        suite = runner.Worker.prochaine()
        self.assertIsNotNone(suite, "l'import doit enclencher l'étape suivante")
        self.assertNotEqual(suite["cible_id"], tache["cible_id"])


class TestConversationParHistoire(BaseChaine):
    """Une histoire = un chat. Toutes ses planches et images y restent."""

    def test_memorisee_a_la_premiere_generation(self):
        self.assertFalse(services.projet(self.projet_id)["chatgpt_chat_url"])
        runner.memoriser_conversation(
            self.projet_id, "chatgpt", "https://chatgpt.com/c/histoire-1"
        )
        self.assertEqual(
            services.projet(self.projet_id)["chatgpt_chat_url"],
            "https://chatgpt.com/c/histoire-1",
        )

    def test_jamais_ecrasee_ensuite(self):
        runner.memoriser_conversation(
            self.projet_id, "chatgpt", "https://chatgpt.com/c/histoire-1"
        )
        runner.memoriser_conversation(
            self.projet_id, "chatgpt", "https://chatgpt.com/c/autre-chat"
        )
        self.assertEqual(
            services.projet(self.projet_id)["chatgpt_chat_url"],
            "https://chatgpt.com/c/histoire-1",
            "toutes les planches doivent rester dans le même chat",
        )

    def test_une_adresse_sans_conversation_est_ignoree(self):
        runner.memoriser_conversation(self.projet_id, "chatgpt", "https://chatgpt.com/")
        self.assertFalse(services.projet(self.projet_id)["chatgpt_chat_url"])

    def test_le_contexte_transmet_la_conversation_a_l_adaptateur(self):
        runner.memoriser_conversation(
            self.projet_id, "chatgpt", "https://chatgpt.com/c/histoire-1"
        )
        perso = db.q1(
            "SELECT * FROM personnages WHERE projet_id=? ORDER BY ordre LIMIT 1",
            (self.projet_id,),
        )
        tache_id = runner.demander(
            self.projet_id, "planche", perso["id"], fournisseur="chatgpt", mode="chaine"
        )
        t = db.q1("SELECT * FROM taches WHERE id=?", (tache_id,))
        ctx = runner.construire_contexte(
            t, runner.chemin_attendu(self.projet_id, "planche", perso["id"])
        )
        self.assertEqual(ctx.url_chat, "https://chatgpt.com/c/histoire-1")

    def test_deux_histoires_deux_chats(self):
        autre = services.creer_projet("Autre histoire")
        services.appliquer_pack(autre, services.analyser_pack(tests.pack_variantes()))
        runner.memoriser_conversation(
            self.projet_id, "chatgpt", "https://chatgpt.com/c/histoire-1"
        )
        runner.memoriser_conversation(autre, "chatgpt", "https://chatgpt.com/c/histoire-2")
        self.assertNotEqual(
            services.projet(self.projet_id)["chatgpt_chat_url"],
            services.projet(autre)["chatgpt_chat_url"],
        )

    def test_nouveau_chat_remet_a_zero(self):
        runner.memoriser_conversation(
            self.projet_id, "chatgpt", "https://chatgpt.com/c/histoire-1"
        )
        db.exec1(
            "UPDATE projets SET chatgpt_chat_url=NULL WHERE id=?", (self.projet_id,)
        )
        self.assertFalse(services.projet(self.projet_id)["chatgpt_chat_url"])
        runner.memoriser_conversation(
            self.projet_id, "chatgpt", "https://chatgpt.com/c/histoire-3"
        )
        self.assertEqual(
            services.projet(self.projet_id)["chatgpt_chat_url"],
            "https://chatgpt.com/c/histoire-3",
        )


class AdaptateurQuiRate(base.Adaptateur):
    """Rate sur les elements demandes, reussit ailleurs. Dit s'il a envoye."""

    def __init__(self, rates: set, envoye: bool, etat=base.ECHEC):
        self.rates = rates          # {(cible_type, cible_id), ...}
        self.envoye = envoye        # le prompt est-il parti avant l'echec ?
        self.etat = etat
        self.appels: list[str] = []

    def generer(self, ctx):
        self.appels.append(f"{ctx.cible_type}:{ctx.cible_id}")
        if (ctx.cible_type, ctx.cible_id) in self.rates:
            ctx.envoye = self.envoye
            return base.ResultatGeneration(self.etat, "Raté pour le test.")
        ctx.envoye = True
        ctx.chemin_sortie.parent.mkdir(parents=True, exist_ok=True)
        if ctx.cible_type == "video":
            montage.fabriquer_clip_test(ctx.chemin_sortie, duree=1.0, texte="X")
        else:
            tests.fabriquer_image_test(ctx.chemin_sortie, texte=ctx.cible_type[:3])
        return base.ResultatGeneration(
            base.TERMINE, "Produit.", chemin=str(ctx.chemin_sortie)
        )


class BaseReprise(BaseChaine):
    """Planches deja faites : on travaille directement sur les scenes."""

    def setUp(self):
        super().setUp()
        for p in db.q("SELECT * FROM personnages WHERE projet_id=?", (self.projet_id,)):
            chemin = fichiers.chemin_planche(
                services.projet(self.projet_id)["dossier"],
                p["code"], p["surnom"], p["etat"],
            )
            tests.fabriquer_image_test(chemin)
            db.exec1(
                "UPDATE personnages SET ref_path=? WHERE id=?", (str(chemin), p["id"])
            )

    def scene(self, numero: int):
        return db.q1(
            "SELECT * FROM scenes WHERE projet_id=? AND numero=?",
            (self.projet_id, numero),
        )

    def brancher(self, adaptateur):
        for fournisseur in ("chatgpt", "grok"):
            runner.enregistrer_adaptateur(fournisseur, "chaine", adaptateur)
        self.adaptateur = adaptateur


class TestRepriseApresEchec(BaseReprise):
    """« Reprendre où ça en était », pas « sauter à la scène suivante »."""

    def test_un_echec_sans_envoi_est_retente_sur_le_meme_element(self):
        scene1 = self.scene(1)
        ad = AdaptateurQuiRate({("image", scene1["id"])}, envoye=False)
        self.brancher(ad)
        runner.demarrer_production(self.projet_id, "chaine")
        self.derouler(maximum=12)

        essais = [a for a in ad.appels if a == f"image:{scene1['id']}"]
        self.assertEqual(
            len(essais), runner.ESSAIS_AUTO_MAX,
            "la chaîne doit réessayer le même élément, pas passer au suivant",
        )
        for t in runner.taches_cible(self.projet_id, "image", scene1["id"]):
            self.assertEqual(t["envoye"], 0, "rien n'était parti : à marquer comme tel")

    def test_la_chaine_ne_fabrique_pas_la_scene_suivante_pendant_un_blocage(self):
        scene1, scene2 = self.scene(1), self.scene(2)
        ad = AdaptateurQuiRate({("image", scene1["id"])}, envoye=True)
        self.brancher(ad)
        runner.demarrer_production(self.projet_id, "chaine")
        self.derouler(maximum=12)

        self.assertNotIn(
            f"image:{scene2['id']}", ad.appels,
            "l'image de la scène 2 ne doit pas partir alors que la 1 est bloquée",
        )
        etat, motif = runner.etat_cible(self.projet_id, "image", scene1["id"])
        self.assertEqual(etat, runner.BLOQUE)
        self.assertIn("quota", motif)

    def test_un_echec_apres_envoi_ne_se_relance_jamais_tout_seul(self):
        scene1 = self.scene(1)
        ad = AdaptateurQuiRate({("image", scene1["id"])}, envoye=True)
        self.brancher(ad)
        runner.demarrer_production(self.projet_id, "chaine")
        self.derouler(maximum=12)

        self.assertEqual(
            [a for a in ad.appels if a == f"image:{scene1['id']}"],
            [f"image:{scene1['id']}"],
            "un prompt parti ne se renvoie jamais sans un clic explicite",
        )

    def test_apres_trois_essais_a_blanc_la_chaine_s_arrete_et_le_dit(self):
        scene1 = self.scene(1)
        self.brancher(AdaptateurQuiRate({("image", scene1["id"])}, envoye=False))
        runner.demarrer_production(self.projet_id, "chaine")
        self.derouler(maximum=20)

        etat, motif = runner.etat_cible(self.projet_id, "image", scene1["id"])
        self.assertEqual(etat, runner.BLOQUE)
        self.assertIn("avant le moindre envoi", motif)
        blocages = runner.etat_production(self.projet_id)["blocages"]
        self.assertTrue(
            any(b["cible_type"] == "image" for b in blocages),
            "le blocage doit être affichable, pas seulement dans le journal",
        )

    def test_relancer_apres_reparation_repart_de_l_element_bloque(self):
        scene1, scene2 = self.scene(1), self.scene(2)
        self.brancher(AdaptateurQuiRate({("image", scene1["id"])}, envoye=False))
        runner.demarrer_production(self.projet_id, "chaine")
        self.derouler(maximum=20)
        self.assertEqual(
            runner.etat_cible(self.projet_id, "image", scene1["id"])[0], runner.BLOQUE
        )

        # La panne est reparee, et c'est TOI qui relances : la chaine doit
        # repartir de la scene 1, pas de la scene 2.
        self.brancher(AdaptateurChaine())
        runner.reessayer(
            runner.taches_cible(self.projet_id, "image", scene1["id"])[-1]["id"]
        )
        self.derouler(maximum=40)
        self.assertTrue(
            fichiers.fichier_utilisable(self.scene(1)["image_path"]),
            "la scène 1 doit être produite avant qu'on parle de la suite",
        )
        self.assertTrue(fichiers.fichier_utilisable(self.scene(2)["image_path"]))

    def test_la_video_est_bloquee_quand_son_image_l_est(self):
        scene1 = self.scene(1)
        self.brancher(AdaptateurQuiRate({("image", scene1["id"])}, envoye=True))
        runner.demarrer_production(self.projet_id, "chaine")
        self.derouler(maximum=12)

        etat, motif = runner.etat_cible(self.projet_id, "video", scene1["id"])
        self.assertEqual(etat, runner.BLOQUE)
        self.assertIn("image de départ", motif)

    def test_a_verifier_bloque_la_chaine_au_lieu_de_relancer(self):
        scene1 = self.scene(1)
        ad = AdaptateurQuiRate(
            {("image", scene1["id"])}, envoye=True, etat=base.A_VERIFIER
        )
        self.brancher(ad)
        runner.demarrer_production(self.projet_id, "chaine")
        self.derouler(maximum=12)

        etat, motif = runner.etat_cible(self.projet_id, "image", scene1["id"])
        self.assertEqual(etat, runner.BLOQUE)
        self.assertIn("Récupérer le résultat", motif)
        self.assertEqual(
            len([a for a in ad.appels if a == f"image:{scene1['id']}"]), 1
        )


class TestCeQuiEstAffichePendantUnArret(BaseReprise):
    """Un arrêt invisible ressemble à « rien à faire » : il faut qu'il se voie."""

    def test_le_message_de_lancement_nomme_lelement_bloquant(self):
        scene1 = self.scene(1)
        self.brancher(AdaptateurQuiRate({("image", scene1["id"])}, envoye=True))
        runner.demarrer_production(self.projet_id, "chaine")
        self.derouler(maximum=12)

        resultat = runner.demarrer_production(self.projet_id, "chaine")
        self.assertIsNone(resultat["tache_id"])
        phrase = runner.phrase_des_blocages(resultat["etat"])
        self.assertIn("Image scene 1", phrase)
        self.assertIn("Arrêtée sur", phrase)

    def test_pas_de_phrase_darret_quand_la_chaine_avance(self):
        runner.demarrer_production(self.projet_id, "chaine")
        self.assertEqual(
            runner.phrase_des_blocages(runner.etat_production(self.projet_id)), ""
        )

    def test_le_bandeau_de_larret_est_dans_le_pilote_collant(self):
        """Sous le pilote, il disparaîtrait dès qu'on descend voir les scènes."""
        page = (config.RACINE / "videroo" / "templates" / "atelier.html").read_text(
            encoding="utf-8"
        )
        avant, trouve, _ = page.partition('id="pilote-blocages"')
        self.assertTrue(trouve, "le bandeau d'arrêt a disparu de la page")
        depuis_le_pilote = avant[avant.rindex('<div class="pilote"'):]
        self.assertNotIn(
            "\n</div>", depuis_le_pilote,
            "le bandeau est sorti du pilote collant : il ne se verra plus en bas de page",
        )
        css = (config.RACINE / "videroo" / "static" / "style.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".pilote-blocages", css)
        self.assertIn("position:sticky", css.split(".pilote{")[1].split("}")[0])
