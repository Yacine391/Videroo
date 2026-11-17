"""Plusieurs comptes par service, et bascule automatique quand le quota tombe."""
import unittest
from pathlib import Path
from datetime import datetime, timedelta

import tests
from videroo import comptes, config, db, fichiers, reglages, runner, services
from videroo.adapters import base


class BaseComptes(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()
        comptes.assurer_comptes_par_defaut()

    def grok(self):
        return comptes.liste("grok")


class TestModeleDesComptes(BaseComptes):
    def test_un_compte_par_defaut_sur_le_profil_historique(self):
        for site in comptes.SITES:
            lignes = comptes.liste(site)
            self.assertEqual(len(lignes), 1, site)
            self.assertEqual(lignes[0]["profil"], config.PROFIL_PRINCIPAL)
        self.assertEqual(
            config.chemin_profil(config.PROFIL_PRINCIPAL),
            config.DOSSIER_PROFIL_NAVIGATEUR,
            "la session déjà ouverte doit être conservée",
        )

    def test_ajouter_des_comptes_avec_des_profils_distincts(self):
        comptes.ajouter("grok", "Yacine", "yacinehenine92i@gmail.com")
        comptes.ajouter("grok", "Dariya")
        lignes = self.grok()
        self.assertEqual(len(lignes), 3)
        profils = [c["profil"] for c in lignes]
        self.assertEqual(len(set(profils)), 3, "chaque compte a son profil")
        self.assertIn("grok-yacine", profils)
        self.assertIn("grok-dariya", profils)
        for c in lignes[1:]:
            self.assertTrue(config.chemin_profil(c["profil"]).is_dir())

    def test_noms_identiques_ne_se_marchent_pas_dessus(self):
        comptes.ajouter("grok", "Yacine")
        comptes.ajouter("grok", "Yacine")
        profils = [c["profil"] for c in self.grok()]
        self.assertEqual(len(set(profils)), len(profils))

    def test_nom_obligatoire(self):
        with self.assertRaises(comptes.ErreurCompte):
            comptes.ajouter("grok", "   ")

    def test_dernier_compte_non_supprimable(self):
        with self.assertRaises(comptes.ErreurCompte) as ctx:
            comptes.supprimer(self.grok()[0]["id"])
        self.assertIn("dernier compte", str(ctx.exception))

    def test_ordre_modifiable(self):
        comptes.ajouter("grok", "Yacine")
        comptes.ajouter("grok", "Dariya")
        avant = [c["libelle"] for c in self.grok()]
        comptes.deplacer(self.grok()[2]["id"], "monter")
        apres = [c["libelle"] for c in self.grok()]
        self.assertNotEqual(avant, apres)
        self.assertEqual(apres[1], "Dariya")


class TestRotation(BaseComptes):
    def setUp(self):
        super().setUp()
        comptes.modifier(self.grok()[0]["id"], libelle="Yacine 92i")
        self.b = comptes.ajouter("grok", "Yacine")
        self.c = comptes.ajouter("grok", "Dariya")

    def test_le_premier_disponible_travaille(self):
        self.assertEqual(comptes.compte_actif("grok")["libelle"], "Yacine 92i")

    def test_quota_epuise_fait_passer_au_suivant(self):
        premier = comptes.compte_actif("grok")
        comptes.marquer_quota_epuise(premier["id"])
        self.assertEqual(comptes.compte_actif("grok")["libelle"], "Yacine")
        comptes.marquer_quota_epuise(self.b)
        self.assertEqual(comptes.compte_actif("grok")["libelle"], "Dariya")
        comptes.marquer_quota_epuise(self.c)
        self.assertIsNone(comptes.compte_actif("grok"), "plus aucun compte disponible")

    def test_un_compte_hors_rotation_est_saute(self):
        comptes.modifier(comptes.compte_actif("grok")["id"], actif=False)
        self.assertEqual(comptes.compte_actif("grok")["libelle"], "Yacine")

    def test_rearmement_manuel(self):
        premier = comptes.compte_actif("grok")
        comptes.marquer_quota_epuise(premier["id"])
        self.assertNotEqual(comptes.compte_actif("grok")["id"], premier["id"])
        comptes.rearmer(premier["id"])
        self.assertEqual(comptes.compte_actif("grok")["id"], premier["id"])

    def test_rearmement_automatique_apres_le_delai(self):
        reglages.definir("quota_reset_heures", "6")
        premier = comptes.compte_actif("grok")
        vieux = (datetime.now() - timedelta(hours=7)).isoformat(timespec="seconds")
        db.exec1(
            "UPDATE comptes_ia SET quota_epuise_le=? WHERE id=?", (vieux, premier["id"])
        )
        self.assertEqual(
            comptes.compte_actif("grok")["id"],
            premier["id"],
            "au-delà du délai, le compte revient dans la rotation",
        )

    def test_delai_non_ecoule_laisse_le_compte_de_cote(self):
        reglages.definir("quota_reset_heures", "12")
        premier = comptes.compte_actif("grok")
        recent = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
        db.exec1(
            "UPDATE comptes_ia SET quota_epuise_le=? WHERE id=?", (recent, premier["id"])
        )
        self.assertNotEqual(comptes.compte_actif("grok")["id"], premier["id"])

    def test_comptes_de_secours(self):
        premier = comptes.compte_actif("grok")
        secours = comptes.comptes_de_secours("grok", premier["id"])
        self.assertEqual([c["libelle"] for c in secours], ["Yacine", "Dariya"])


class AdaptateurQuota(base.Adaptateur):
    """Renvoie « quota » pour les profils indiqués, réussit pour les autres."""

    def __init__(self, profils_satures: set[str]):
        self.profils_satures = profils_satures
        self.appels: list[str] = []

    def generer(self, ctx):
        self.appels.append(ctx.profil)
        if ctx.profil in self.profils_satures:
            return base.ResultatGeneration(
                base.QUOTA, f"Limite atteinte sur {ctx.compte_libelle}."
            )
        ctx.chemin_sortie.parent.mkdir(parents=True, exist_ok=True)
        tests.fabriquer_image_test(ctx.chemin_sortie, texte="OK")
        return base.ResultatGeneration(
            base.TERMINE, "Produit.", chemin=str(ctx.chemin_sortie)
        )


class TestBasculeDansLeMoteur(BaseComptes):
    def setUp(self):
        super().setUp()
        comptes.modifier(comptes.liste("chatgpt")[0]["id"], libelle="Jules")
        self.second = comptes.ajouter("chatgpt", "Secours")
        self.projet_id = services.creer_projet("Bascule")
        services.appliquer_pack(
            self.projet_id, services.analyser_pack(tests.pack_variantes())
        )
        services.valider_projet(self.projet_id)
        self.perso = db.q1(
            "SELECT * FROM personnages WHERE projet_id=? ORDER BY ordre LIMIT 1",
            (self.projet_id,),
        )

    def _lancer(self, adaptateur):
        runner.enregistrer_adaptateur("chatgpt", "bascule", adaptateur)
        tache = runner.demander(
            self.projet_id, "planche", self.perso["id"],
            fournisseur="chatgpt", mode="bascule",
        )
        return runner.Worker.executer(tache)

    def test_bascule_sur_le_compte_suivant_et_reussit(self):
        adaptateur = AdaptateurQuota({config.PROFIL_PRINCIPAL})
        statut = self._lancer(adaptateur)
        self.assertEqual(statut, base.TERMINE)
        self.assertEqual(len(adaptateur.appels), 2, "un essai par compte, pas plus")
        self.assertEqual(adaptateur.appels[0], config.PROFIL_PRINCIPAL)
        self.assertEqual(adaptateur.appels[1], comptes.compte(self.second)["profil"])
        self.assertFalse(
            comptes.compte(comptes.liste("chatgpt")[0]["id"])["quota_dispo"],
            "le premier compte est marqué épuisé",
        )

    def test_aucun_doublon_de_generation(self):
        adaptateur = AdaptateurQuota({config.PROFIL_PRINCIPAL})
        self._lancer(adaptateur)
        self.assertEqual(
            len(adaptateur.appels), len(set(adaptateur.appels)),
            "chaque compte n'est essayé qu'une fois",
        )
        taches = db.q(
            "SELECT * FROM taches WHERE projet_id=? AND cible_type='planche'",
            (self.projet_id,),
        )
        self.assertEqual(len(taches), 1, "une seule tâche, malgré la bascule")

    def test_tous_satures_donne_un_etat_quota_sans_pause(self):
        """Plus de quota nulle part : ni « pause », ni « à vérifier » — « quota »."""
        tous = {c["profil"] for c in comptes.liste("chatgpt")}
        adaptateur = AdaptateurQuota(tous)
        statut = self._lancer(adaptateur)
        self.assertEqual(statut, base.QUOTA)
        self.assertNotEqual(services.projet(self.projet_id)["statut"], "pause")
        self.assertEqual(len(adaptateur.appels), 2)
        for c in comptes.liste("chatgpt"):
            self.assertFalse(c["quota_dispo"])
        tache = db.q1(
            "SELECT * FROM taches WHERE projet_id=? ORDER BY id DESC LIMIT 1",
            (self.projet_id,),
        )
        self.assertEqual(tache["statut"], "quota")
        self.assertIn("Limite atteinte sur", tache["message"])
        self.assertIn("rien à récupérer", tache["message"])

    def test_refus_apres_envoi_sans_secours_dit_vrai(self):
        """Projet 43, scène 8 : Grok a REFUSÉ le prompt envoyé (plus de crédits)
        et aucun autre compte : rien à récupérer, et « rien n'a été envoyé » est faux."""

        class AdaptateurRefus(AdaptateurQuota):
            def generer(self, ctx):
                ctx.envoye = True  # le prompt est bien parti…
                return super().generer(ctx)  # …et le fournisseur le refuse

        tous = {c["profil"] for c in comptes.liste("chatgpt")}
        statut = self._lancer(AdaptateurRefus(tous))
        self.assertEqual(statut, base.QUOTA)
        tache = db.q1(
            "SELECT * FROM taches WHERE projet_id=? ORDER BY id DESC LIMIT 1",
            (self.projet_id,),
        )
        self.assertEqual(tache["statut"], "quota", "pas « à vérifier » : rien n'existe")
        self.assertNotIn("Rien n'a été envoyé", tache["message"])
        self.assertIn("rien à récupérer", tache["message"])
        self.assertEqual(
            db.q1("SELECT COUNT(*) n FROM taches WHERE projet_id=?", (self.projet_id,))["n"],
            1, "aucune relance automatique tant qu'aucun compte n'est revenu",
        )

    def test_sans_compte_disponible_rien_ne_part_sur_le_profil_d_un_autre(self):
        """Tous les comptes du projet épuisés : avant, la tâche partait sur le
        profil « principal » — le compte de quelqu'un d'autre."""
        for c in comptes.liste("chatgpt"):
            comptes.marquer_quota_epuise(c["id"], "test")
        adaptateur = AdaptateurQuota(set())
        statut = self._lancer(adaptateur)
        self.assertEqual(statut, base.QUOTA)
        self.assertEqual(adaptateur.appels, [], "aucun navigateur ouvert, aucun envoi")
        tache = db.q1(
            "SELECT * FROM taches WHERE projet_id=? ORDER BY id DESC LIMIT 1",
            (self.projet_id,),
        )
        self.assertEqual((tache["statut"], tache["envoye"]), ("quota", 0))
        self.assertIn("rien n'a été envoyé", tache["message"])

    def test_le_compte_utilise_est_journalise(self):
        self._lancer(AdaptateurQuota(set()))
        messages = " ".join(
            l["message"]
            for l in db.q("SELECT message FROM journal WHERE projet_id=?", (self.projet_id,))
        )
        self.assertIn("Compte utilisé : « Jules »", messages)

    def test_derniere_utilisation_notee(self):
        self._lancer(AdaptateurQuota(set()))
        principal = comptes.liste("chatgpt")[0]
        self.assertIsNotNone(principal["derniere_utilisation"])


class TestContexteDeTache(BaseComptes):
    def test_le_contexte_porte_le_compte_actif(self):
        comptes.modifier(comptes.liste("grok")[0]["id"], libelle="Yacine 92i")
        projet_id = services.creer_projet("Contexte")
        services.appliquer_pack(
            projet_id, services.analyser_pack(tests.pack_variantes())
        )
        services.valider_projet(projet_id)
        pr = services.projet(projet_id)
        for p in db.q("SELECT * FROM personnages WHERE projet_id=?", (projet_id,)):
            chemin = fichiers.chemin_planche(
                pr["dossier"], p["code"], p["surnom"], p["etat"]
            )
            tests.fabriquer_image_test(chemin)
            db.exec1("UPDATE personnages SET ref_path=? WHERE id=?", (str(chemin), p["id"]))
        scene = db.q1(
            "SELECT * FROM scenes WHERE projet_id=? AND numero=1", (projet_id,)
        )
        image = fichiers.chemin_image_scene(pr["dossier"], 1)
        tests.fabriquer_image_test(image)
        db.exec1("UPDATE scenes SET image_path=? WHERE id=?", (str(image), scene["id"]))

        tache_id = runner.demander(
            projet_id, "video", scene["id"], fournisseur="grok", mode="test"
        )
        t = db.q1("SELECT * FROM taches WHERE id=?", (tache_id,))
        ctx = runner.construire_contexte(
            t, runner.chemin_attendu(projet_id, "video", scene["id"])
        )
        self.assertEqual(ctx.compte_libelle, "Yacine 92i")
        self.assertEqual(ctx.profil, config.PROFIL_PRINCIPAL)


if __name__ == "__main__":
    unittest.main()


class TestHoraireDeRecharge(BaseComptes):
    """Chaque abonnement a sa propre heure de réinitialisation hebdomadaire."""

    def setUp(self):
        super().setUp()
        self.a = comptes.liste("grok")[0]["id"]          # vendredi 23:55
        self.b = comptes.ajouter("grok", "Yacine")        # mardi 14:34
        self.c = comptes.ajouter("grok", "Dariya")        # mercredi 21:01
        comptes.definir_reinitialisation(self.a, 4, "23:55")
        comptes.definir_reinitialisation(self.b, 1, "14:34")
        comptes.definir_reinitialisation(self.c, 2, "21:01")

    def test_horaires_lisibles(self):
        self.assertEqual(
            comptes.compte(self.a)["horaire"], "tous les vendredi à 23:55"
        )
        self.assertEqual(comptes.compte(self.b)["horaire"], "tous les mardi à 14:34")
        self.assertEqual(comptes.compte(self.c)["horaire"], "tous les mercredi à 21:01")

    def test_prochaine_occurrence(self):
        from datetime import datetime, time

        # jeudi 5 novembre 2026, 10:00
        jeudi = datetime(2026, 11, 5, 10, 0)
        self.assertEqual(
            comptes.prochaine_occurrence(jeudi, 4, time(23, 55)),
            datetime(2026, 11, 6, 23, 55),
            "le vendredi suivant",
        )
        self.assertEqual(
            comptes.prochaine_occurrence(jeudi, 1, time(14, 34)),
            datetime(2026, 11, 10, 14, 34),
            "le mardi suivant",
        )

    def test_juste_apres_l_heure_renvoie_la_semaine_suivante(self):
        from datetime import datetime, time

        mardi_apres = datetime(2026, 11, 10, 14, 35)
        self.assertEqual(
            comptes.prochaine_occurrence(mardi_apres, 1, time(14, 34)),
            datetime(2026, 11, 17, 14, 34),
        )

    def test_pile_a_l_heure_renvoie_la_semaine_suivante(self):
        from datetime import datetime, time

        pile = datetime(2026, 11, 10, 14, 34)
        self.assertEqual(
            comptes.prochaine_occurrence(pile, 1, time(14, 34)),
            datetime(2026, 11, 17, 14, 34),
        )

    def test_epuise_reste_indisponible_avant_sa_recharge(self):
        from datetime import datetime, timedelta

        comptes.marquer_quota_epuise(self.b)
        ligne = comptes.compte(self.b)
        self.assertFalse(ligne["quota_dispo"])
        self.assertTrue(ligne["prochaine_recharge"].startswith("mardi"))
        recharge = comptes.prochaine_recharge(ligne)
        self.assertGreater(recharge, datetime.now())
        self.assertLessEqual(recharge, datetime.now() + timedelta(days=7))

    def test_redevient_disponible_une_fois_l_heure_passee(self):
        from datetime import datetime, timedelta

        # épuisé il y a 8 jours : la recharge hebdomadaire est forcément passée
        vieux = (datetime.now() - timedelta(days=8)).isoformat(timespec="seconds")
        db.exec1("UPDATE comptes_ia SET quota_epuise_le=? WHERE id=?", (vieux, self.b))
        self.assertTrue(comptes.compte(self.b)["quota_dispo"])

    def test_l_horaire_prime_sur_le_delai_global(self):
        from datetime import datetime, timedelta

        reglages.definir("quota_reset_heures", "1")
        recent = (datetime.now() - timedelta(hours=3)).isoformat(timespec="seconds")
        db.exec1("UPDATE comptes_ia SET quota_epuise_le=? WHERE id=?", (recent, self.b))
        self.assertFalse(
            comptes.compte(self.b)["quota_dispo"],
            "3 h se sont écoulées mais la recharge hebdomadaire n'a pas eu lieu",
        )

    def test_sans_horaire_le_delai_global_s_applique(self):
        from datetime import datetime, timedelta

        comptes.definir_reinitialisation(self.c, None, None)
        self.assertEqual(comptes.compte(self.c)["horaire"], "")
        reglages.definir("quota_reset_heures", "2")
        vieux = (datetime.now() - timedelta(hours=3)).isoformat(timespec="seconds")
        db.exec1("UPDATE comptes_ia SET quota_epuise_le=? WHERE id=?", (vieux, self.c))
        self.assertTrue(comptes.compte(self.c)["quota_dispo"])

    def test_rotation_choisit_le_compte_deja_recharge(self):
        from datetime import datetime, timedelta

        maintenant = datetime.now().isoformat(timespec="seconds")
        vieux = (datetime.now() - timedelta(days=8)).isoformat(timespec="seconds")
        db.exec1("UPDATE comptes_ia SET quota_epuise_le=? WHERE id=?", (maintenant, self.a))
        db.exec1("UPDATE comptes_ia SET quota_epuise_le=? WHERE id=?", (maintenant, self.b))
        db.exec1("UPDATE comptes_ia SET quota_epuise_le=? WHERE id=?", (vieux, self.c))
        actif = comptes.compte_actif("grok")
        self.assertEqual(actif["id"], self.c, "seul Dariya est rechargé")

    def test_heure_invalide_refusee(self):
        for mauvaise in ("25:00", "14h34", "", "abc"):
            with self.assertRaises(comptes.ErreurCompte, msg=mauvaise):
                comptes.definir_reinitialisation(self.b, 1, mauvaise)

    def test_jour_invalide_refuse(self):
        with self.assertRaises(comptes.ErreurCompte):
            comptes.definir_reinitialisation(self.b, 9, "14:34")

    def test_message_de_quota_annonce_la_recharge(self):
        comptes.marquer_quota_epuise(self.b, "limite Imagine")
        messages = " ".join(
            l["message"] for l in db.q("SELECT message FROM journal ORDER BY id DESC LIMIT 5")
        )
        self.assertIn("Recharge prévue mardi", messages)


class TestStrategieDeRotation(BaseComptes):
    """On dépense d'abord le quota qui se recharge le plus tôt."""

    def setUp(self):
        super().setUp()
        self.a = comptes.liste("grok")[0]["id"]
        comptes.modifier(self.a, libelle="Yacine 92i")
        self.b = comptes.ajouter("grok", "Yacine")
        self.c = comptes.ajouter("grok", "Dariya")
        comptes.definir_reinitialisation(self.a, 4, "23:55")   # vendredi
        comptes.definir_reinitialisation(self.b, 1, "14:34")   # mardi
        comptes.definir_reinitialisation(self.c, 2, "21:01")   # mercredi

    def test_strategie_par_defaut(self):
        self.assertEqual(reglages.lire("strategie_rotation"), "recharge_proche")

    def test_le_plus_tot_recharge_passe_en_premier(self):
        from datetime import datetime
        from unittest import mock

        # vendredi 6 novembre 2026, 01:30 : la recharge du vendredi est dans 22 h
        vendredi = datetime(2026, 11, 6, 1, 30)
        with mock.patch("videroo.comptes.datetime") as faux:
            faux.now.return_value = vendredi
            faux.combine = datetime.combine
            faux.fromisoformat = datetime.fromisoformat
            noms = [c["libelle"] for c in comptes.ordre_utilisation("grok")]
        self.assertEqual(noms, ["Yacine 92i", "Yacine", "Dariya"])

    def test_l_ordre_change_avec_le_moment(self):
        from datetime import datetime
        from unittest import mock

        # samedi 7 novembre : le vendredi est passé, il recharge dans 7 jours
        samedi = datetime(2026, 11, 7, 10, 0)
        with mock.patch("videroo.comptes.datetime") as faux:
            faux.now.return_value = samedi
            faux.combine = datetime.combine
            faux.fromisoformat = datetime.fromisoformat
            noms = [c["libelle"] for c in comptes.ordre_utilisation("grok")]
        self.assertEqual(noms, ["Yacine", "Dariya", "Yacine 92i"])

    def test_ordre_manuel_respecte_les_fleches(self):
        reglages.definir("strategie_rotation", "ordre_manuel")
        noms = [c["libelle"] for c in comptes.ordre_utilisation("grok")]
        self.assertEqual(noms, ["Yacine 92i", "Yacine", "Dariya"])
        comptes.deplacer(self.c, "monter")
        comptes.deplacer(self.c, "monter")
        noms = [c["libelle"] for c in comptes.ordre_utilisation("grok")]
        self.assertEqual(noms[0], "Dariya")

    def test_compte_sans_horaire_passe_en_dernier(self):
        comptes.definir_reinitialisation(self.a, None, None)
        noms = [c["libelle"] for c in comptes.ordre_utilisation("grok")]
        self.assertEqual(noms[-1], "Yacine 92i")

    def test_un_compte_epuise_sort_de_l_ordre(self):
        comptes.marquer_quota_epuise(self.a)
        noms = [c["libelle"] for c in comptes.ordre_utilisation("grok")]
        self.assertNotIn("Yacine 92i", noms)
        self.assertEqual(len(noms), 2)

    def test_secours_suit_la_meme_strategie(self):
        premier = comptes.compte_actif("grok")
        secours = comptes.comptes_de_secours("grok", premier["id"])
        self.assertNotIn(premier["id"], [c["id"] for c in secours])
        self.assertEqual(len(secours), 2)

    def test_rotation_lisible(self):
        lignes = comptes.rotation_lisible("grok")
        self.assertEqual(len(lignes), 3)
        self.assertTrue(all(l["recharge"] for l in lignes))
        self.assertIn("dans", lignes[0]["recharge"])


class TestIsolationDesProfils(BaseComptes):
    """Deux comptes qui partagent un profil partagent aussi leur session Google."""

    def test_le_partage_est_detecte(self):
        chatgpt = comptes.liste("chatgpt")[0]
        grok = comptes.liste("grok")[0]
        self.assertEqual(chatgpt["profil"], grok["profil"], "cas de départ : profil commun")
        self.assertIn("Grok", " ".join(chatgpt["partage_avec"]))
        self.assertIn("ChatGPT", " ".join(grok["partage_avec"]))

    def test_un_compte_neuf_a_son_propre_profil(self):
        nouveau = comptes.ajouter("grok", "Dariya")
        self.assertEqual(comptes.compte(nouveau)["partage_avec"], [])

    def test_detacher_donne_un_profil_isole(self):
        chatgpt = comptes.liste("chatgpt")[0]
        apres = comptes.detacher_profil(chatgpt["id"])
        self.assertNotEqual(apres["profil"], config.PROFIL_PRINCIPAL)
        self.assertEqual(apres["partage_avec"], [])
        self.assertTrue(config.chemin_profil(apres["profil"]).is_dir())
        self.assertEqual(comptes.liste("grok")[0]["partage_avec"], [])

    def test_detacher_sans_copie_laisse_un_profil_vierge(self):
        source = config.chemin_profil(config.PROFIL_PRINCIPAL)
        source.mkdir(parents=True, exist_ok=True)
        (source / "Cookies").write_text("session-lukyfofo", encoding="utf-8")
        apres = comptes.detacher_profil(comptes.liste("chatgpt")[0]["id"], copier=False)
        cible = config.chemin_profil(apres["profil"])
        self.assertFalse(
            (cible / "Cookies").exists(),
            "sans copie, aucune session ne doit suivre",
        )

    def test_detacher_avec_copie_duplique_la_session(self):
        source = config.chemin_profil(config.PROFIL_PRINCIPAL)
        source.mkdir(parents=True, exist_ok=True)
        (source / "Cookies").write_text("session-lukyfofo", encoding="utf-8")
        (source / "SingletonLock").write_text("verrou", encoding="utf-8")
        apres = comptes.detacher_profil(comptes.liste("chatgpt")[0]["id"], copier=True)
        cible = config.chemin_profil(apres["profil"])
        self.assertEqual((cible / "Cookies").read_text(encoding="utf-8"), "session-lukyfofo")
        self.assertFalse((cible / "SingletonLock").exists(), "les verrous ne suivent pas")

    def test_detacher_remet_l_etat_a_verifier(self):
        chatgpt = comptes.liste("chatgpt")[0]
        comptes.marquer(chatgpt["id"], "connecte")
        apres = comptes.detacher_profil(chatgpt["id"])
        self.assertEqual(apres["etat"], "", "nouveau profil : l'ancien état ne vaut plus")

    def test_effacer_un_profil_partage_est_refuse(self):
        chatgpt = comptes.liste("chatgpt")[0]
        with self.assertRaises(comptes.ErreurCompte) as ctx:
            comptes.effacer_profil(chatgpt["id"])
        self.assertIn("partagé", str(ctx.exception))
        self.assertIn("profil séparé", str(ctx.exception))

    def test_effacer_un_profil_isole_est_permis(self):
        nouveau = comptes.ajouter("grok", "Dariya")
        chemin = comptes.effacer_profil(nouveau)
        self.assertTrue(Path(chemin).is_dir())

    def test_chaque_profil_a_son_dossier(self):
        comptes.detacher_profil(comptes.liste("chatgpt")[0]["id"])
        comptes.ajouter("grok", "Yacine")
        comptes.ajouter("grok", "Dariya")
        dossiers = {c["chemin_profil"] for c in comptes.liste()}
        self.assertEqual(len(dossiers), 4, "quatre comptes, quatre dossiers distincts")
