"""Garde-fous quotas : une seule generation, aucune relance implicite, reprise fiable."""
import unittest
from pathlib import Path

import tests
from videroo import db, fichiers, montage, runner, services
from videroo.adapters import base


class AdaptateurFactice(base.Adaptateur):
    """Adaptateur de test : compte les appels, ne touche aucun service en ligne."""

    def __init__(self, etat=base.TERMINE, ecrire_fichier=True):
        self.appels = 0
        self.etat = etat
        self.ecrire_fichier = ecrire_fichier

    def generer(self, ctx):
        self.appels += 1
        if self.ecrire_fichier:
            ctx.chemin_sortie.parent.mkdir(parents=True, exist_ok=True)
            if ctx.cible_type == "video":
                montage.fabriquer_clip_test(ctx.chemin_sortie, duree=float(ctx.duree or 6))
            else:
                tests.fabriquer_image_test(ctx.chemin_sortie, texte=ctx.cible_type)
            return base.ResultatGeneration(self.etat, "Factice.", chemin=str(ctx.chemin_sortie))
        return base.ResultatGeneration(self.etat, "Factice sans fichier.")


class BaseProjet(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()
        self.projet_id = services.creer_projet("Projet garde-fous")
        services.appliquer_pack(
            self.projet_id, services.analyser_pack(tests.pack_exemple())
        )
        self.pr = services.projet(self.projet_id)
        self.factice = AdaptateurFactice()
        for fournisseur in ("chatgpt", "grok"):
            runner.enregistrer_adaptateur(fournisseur, "test", self.factice)

    def perso(self, code="R1"):
        return db.q1(
            "SELECT * FROM personnages WHERE projet_id=? AND code=?", (self.projet_id, code)
        )

    def scene(self, numero=1):
        return db.q1(
            "SELECT * FROM scenes WHERE projet_id=? AND numero=?", (self.projet_id, numero)
        )

    def preparer_references(self):
        for p in db.q("SELECT * FROM personnages WHERE projet_id=?", (self.projet_id,)):
            chemin = fichiers.chemin_planche(
                self.pr["dossier"], p["code"], p["surnom"], p["etat"]
            )
            tests.fabriquer_image_test(chemin, texte=p["code"])
            db.exec1(
                "UPDATE personnages SET ref_path=?, ref_origine='test' WHERE id=?",
                (str(chemin), p["id"]),
            )

    def preparer_image(self, numero=1):
        s = self.scene(numero)
        chemin = fichiers.chemin_image_scene(self.pr["dossier"], numero)
        tests.fabriquer_image_test(chemin, texte=f"S{numero}")
        db.exec1("UPDATE scenes SET image_path=? WHERE id=?", (str(chemin), s["id"]))
        return s


class TestUneSeuleGeneration(BaseProjet):
    def test_une_seule_generation_automatique_par_element(self):
        services.valider_projet(self.projet_id)
        perso = self.perso()
        runner.demander(
            self.projet_id, "planche", perso["id"], fournisseur="chatgpt", mode="test"
        )
        with self.assertRaises(runner.GenerationRefusee) as ctx:
            runner.demander(
                self.projet_id, "planche", perso["id"], fournisseur="chatgpt", mode="test"
            )
        self.assertIn("déjà", str(ctx.exception).lower())
        self.assertEqual(
            db.q1(
                "SELECT COUNT(*) n FROM taches WHERE projet_id=? AND cible_type='planche'"
                " AND cible_id=?",
                (self.projet_id, perso["id"]),
            )["n"],
            1,
        )

    def test_refus_persiste_apres_un_echec(self):
        perso = self.perso()
        tache = runner.demander(
            self.projet_id, "planche", perso["id"], fournisseur="chatgpt", mode="test"
        )
        db.exec1("UPDATE taches SET statut='echec' WHERE id=?", (tache,))
        with self.assertRaises(runner.GenerationRefusee):
            runner.demander(
                self.projet_id, "planche", perso["id"], fournisseur="chatgpt", mode="test"
            )

    def test_regeneration_explicite_autorisee_et_archive_l_ancien(self):
        perso = self.perso()
        chemin = fichiers.chemin_planche(
            self.pr["dossier"], perso["code"], perso["surnom"], perso["etat"]
        )
        tests.fabriquer_image_test(chemin)
        db.exec1("UPDATE personnages SET ref_path=? WHERE id=?", (str(chemin), perso["id"]))
        runner.regenerer(self.projet_id, "planche", perso["id"], "chatgpt", "test")
        self.assertFalse(chemin.exists(), "l'ancien fichier doit etre deplace")
        archives = list(chemin.parent.glob(f"{chemin.stem}_remplace_*{chemin.suffix}"))
        self.assertEqual(len(archives), 1, "l'ancien fichier doit etre conserve a cote")
        tache = db.q1(
            "SELECT * FROM taches WHERE projet_id=? ORDER BY id DESC LIMIT 1", (self.projet_id,)
        )
        self.assertEqual(tache["origine"], "manuel")

    def test_resultat_deja_present_bloque_et_se_rattache(self):
        perso = self.perso()
        chemin = fichiers.chemin_planche(
            self.pr["dossier"], perso["code"], perso["surnom"], perso["etat"]
        )
        tests.fabriquer_image_test(chemin)
        with self.assertRaises(runner.GenerationRefusee) as ctx:
            runner.demander(
                self.projet_id, "planche", perso["id"], fournisseur="chatgpt", mode="test"
            )
        self.assertIn("c'est déjà fait", str(ctx.exception))
        self.assertEqual(
            db.q1("SELECT ref_path FROM personnages WHERE id=?", (perso["id"],))["ref_path"],
            str(chemin),
        )
        self.assertEqual(self.factice.appels, 0, "aucune generation ne doit partir")


class TestUneSeuleTacheActive(BaseProjet):
    def test_le_worker_ne_prend_rien_pendant_une_generation(self):
        services.valider_projet(self.projet_id)
        self.preparer_references()
        t1 = runner.demander(
            self.projet_id, "image", self.scene(1)["id"], fournisseur="chatgpt", mode="test"
        )
        t2 = runner.demander(
            self.projet_id, "image", self.scene(2)["id"], fournisseur="chatgpt", mode="test"
        )
        self.assertEqual(runner.Worker.prochaine()["id"], t1)
        db.exec1("UPDATE taches SET statut='en_cours' WHERE id=?", (t1,))
        self.assertIsNone(runner.Worker.prochaine(), "une seule generation a la fois")
        db.exec1("UPDATE taches SET statut='termine' WHERE id=?", (t1,))
        self.assertEqual(runner.Worker.prochaine()["id"], t2)

    def test_pause_et_arret(self):
        services.valider_projet(self.projet_id)
        self.preparer_references()
        runner.demander(
            self.projet_id, "image", self.scene(1)["id"], fournisseur="chatgpt", mode="test"
        )
        runner.changer_statut_projet(self.projet_id, "pause")
        self.assertIsNone(runner.Worker.prochaine(), "en pause, rien ne part")
        runner.changer_statut_projet(self.projet_id, "en_cours")
        self.assertIsNotNone(runner.Worker.prochaine())

        # L'arret annule les taches en attente mais ne supprime aucun resultat.
        self.preparer_image(3)
        chemin_image = Path(
            db.q1("SELECT image_path FROM scenes WHERE projet_id=? AND numero=3",
                  (self.projet_id,))["image_path"]
        )
        annulees = runner.arreter_projet(self.projet_id)
        self.assertGreaterEqual(annulees, 1)
        self.assertTrue(chemin_image.exists(), "les resultats obtenus restent intacts")
        self.assertEqual(services.projet(self.projet_id)["statut"], "arrete")
        with self.assertRaises(runner.GenerationRefusee):
            runner.demander(
                self.projet_id, "image", self.scene(4)["id"], fournisseur="chatgpt", mode="test"
            )


class TestControlesAvantDepense(BaseProjet):
    def test_scene_non_validee_refusee(self):
        with self.assertRaises(runner.GenerationRefusee) as ctx:
            runner.demander(
                self.projet_id, "image", self.scene(1)["id"], fournisseur="chatgpt", mode="test"
            )
        self.assertIn("non validée", str(ctx.exception))

    def test_references_absentes_refusees_avant_tout_envoi(self):
        services.valider_projet(self.projet_id)
        with self.assertRaises(runner.GenerationRefusee) as ctx:
            runner.demander(
                self.projet_id, "image", self.scene(1)["id"], fournisseur="chatgpt", mode="test"
            )
        message = str(ctx.exception)
        self.assertIn("références réellement absentes", message)
        self.assertIn("ne remplace pas une image jointe", message)
        self.assertEqual(self.factice.appels, 0)

    def test_video_sans_image_de_depart_refusee(self):
        services.valider_projet(self.projet_id)
        self.preparer_references()
        with self.assertRaises(runner.GenerationRefusee) as ctx:
            runner.demander(
                self.projet_id, "video", self.scene(1)["id"], fournisseur="grok", mode="test"
            )
        self.assertIn("image de départ", str(ctx.exception))

    def test_duree_non_prise_en_charge_signalee_avant_generation(self):
        services.valider_projet(self.projet_id)
        self.preparer_references()
        s = self.preparer_image(1)
        db.exec1("UPDATE scenes SET duree=8 WHERE id=?", (s["id"],))
        with self.assertRaises(runner.GenerationRefusee) as ctx:
            runner.demander(
                self.projet_id, "video", s["id"], fournisseur="grok", mode="test"
            )
        self.assertIn("non prise en charge", str(ctx.exception))
        self.assertEqual(self.factice.appels, 0)

    def test_melange_entre_projets_refuse(self):
        autre = services.creer_projet("Autre projet")
        services.appliquer_pack(autre, services.analyser_pack(tests.pack_exemple()))
        services.valider_projet(self.projet_id)
        # On fait pointer une reference du projet 1 vers un fichier du projet 2.
        chemin_etranger = Path(services.projet(autre)["dossier"]) / "references" / "vole.png"
        tests.fabriquer_image_test(chemin_etranger)
        db.exec1(
            "UPDATE personnages SET ref_path=? WHERE projet_id=? AND code='R1'",
            (str(chemin_etranger), self.projet_id),
        )
        for p in db.q(
            "SELECT * FROM personnages WHERE projet_id=? AND code<>'R1'", (self.projet_id,)
        ):
            c = fichiers.chemin_planche(self.pr["dossier"], p["code"], p["surnom"], p["etat"])
            tests.fabriquer_image_test(c)
            db.exec1("UPDATE personnages SET ref_path=? WHERE id=?", (str(c), p["id"]))
        with self.assertRaises(runner.GenerationRefusee) as ctx:
            runner.demander(
                self.projet_id, "image", self.scene(1)["id"], fournisseur="chatgpt", mode="test"
            )
        self.assertIn("n'appartient pas au dossier du projet", str(ctx.exception))


class TestExecutionEtReprise(BaseProjet):
    def test_execution_reussie_rattache_le_fichier(self):
        services.valider_projet(self.projet_id)
        perso = self.perso()
        tache = runner.demander(
            self.projet_id, "planche", perso["id"], fournisseur="chatgpt", mode="test"
        )
        statut = runner.Worker.executer(tache)
        self.assertEqual(statut, base.TERMINE)
        self.assertEqual(self.factice.appels, 1)
        ligne = db.q1("SELECT * FROM personnages WHERE id=?", (perso["id"],))
        self.assertTrue(fichiers.fichier_utilisable(ligne["ref_path"]))
        journal = db.q(
            "SELECT message FROM journal WHERE tache_id=? ORDER BY id", (tache,)
        )
        messages = " ".join(l["message"] for l in journal)
        self.assertIn("ÉTAT AVANT", messages)
        self.assertIn("ÉTAT APRÈS", messages)

    def test_reussite_annoncee_sans_fichier_devient_a_verifier(self):
        services.valider_projet(self.projet_id)
        menteur = AdaptateurFactice(etat=base.TERMINE, ecrire_fichier=False)
        runner.enregistrer_adaptateur("chatgpt", "menteur", menteur)
        perso = self.perso()
        tache = runner.demander(
            self.projet_id, "planche", perso["id"], fournisseur="chatgpt", mode="menteur"
        )
        statut = runner.Worker.executer(tache)
        self.assertEqual(statut, base.A_VERIFIER)
        self.assertIsNone(
            db.q1("SELECT ref_path FROM personnages WHERE id=?", (perso["id"],))["ref_path"]
        )

    def test_delai_depasse_ne_relance_rien(self):
        services.valider_projet(self.projet_id)
        lent = AdaptateurFactice(etat=base.A_VERIFIER, ecrire_fichier=False)
        runner.enregistrer_adaptateur("chatgpt", "lent", lent)
        perso = self.perso()
        tache = runner.demander(
            self.projet_id, "planche", perso["id"], fournisseur="chatgpt", mode="lent"
        )
        self.assertEqual(runner.Worker.executer(tache), base.A_VERIFIER)
        self.assertEqual(lent.appels, 1)
        self.assertIsNone(runner.Worker.prochaine(), "aucune relance automatique")
        with self.assertRaises(runner.GenerationRefusee):
            runner.demander(
                self.projet_id, "planche", perso["id"], fournisseur="chatgpt", mode="lent"
            )
        # Seul un reessai explicite remet une tache en file.
        nouvelle = runner.reessayer(tache)
        self.assertEqual(
            db.q1("SELECT origine FROM taches WHERE id=?", (nouvelle,))["origine"], "manuel"
        )

    def test_intervention_met_le_projet_en_pause(self):
        services.valider_projet(self.projet_id)

        class Bloqueur(base.Adaptateur):
            def generer(self, ctx):
                return base.ResultatGeneration(
                    base.INTERVENTION, "Connexion expiree.", instruction="Reconnecte-toi."
                )

        runner.enregistrer_adaptateur("chatgpt", "bloqueur", Bloqueur())
        perso = self.perso()
        tache = runner.demander(
            self.projet_id, "planche", perso["id"], fournisseur="chatgpt", mode="bloqueur"
        )
        self.assertEqual(runner.Worker.executer(tache), "intervention")
        self.assertEqual(services.projet(self.projet_id)["statut"], "pause")
        ligne = db.q1("SELECT * FROM taches WHERE id=?", (tache,))
        self.assertEqual(ligne["statut"], "a_verifier")
        self.assertIn("Reconnecte-toi", ligne["message"])

    def test_reprise_apres_plantage_verifie_le_disque(self):
        services.valider_projet(self.projet_id)
        perso = self.perso()
        tache = runner.demander(
            self.projet_id, "planche", perso["id"], fournisseur="chatgpt", mode="test"
        )
        db.exec1("UPDATE taches SET statut='en_cours' WHERE id=?", (tache,))
        # Cas 1 : le resultat existe deja -> on le rattache, aucune relance.
        chemin = fichiers.chemin_planche(
            self.pr["dossier"], perso["code"], perso["surnom"], perso["etat"]
        )
        tests.fabriquer_image_test(chemin)
        rapports = runner.recuperer_apres_plantage()
        self.assertTrue(any("résultat retrouvé" in r for r in rapports))
        self.assertEqual(db.q1("SELECT statut FROM taches WHERE id=?", (tache,))["statut"], "termine")
        self.assertEqual(self.factice.appels, 0)

    def test_reprise_sans_resultat_passe_a_verifier(self):
        services.valider_projet(self.projet_id)
        perso = self.perso("R2")
        tache = runner.demander(
            self.projet_id, "planche", perso["id"], fournisseur="chatgpt", mode="test"
        )
        db.exec1("UPDATE taches SET statut='en_cours' WHERE id=?", (tache,))
        rapports = runner.recuperer_apres_plantage()
        self.assertTrue(any("à vérifier" in r for r in rapports))
        self.assertEqual(
            db.q1("SELECT statut FROM taches WHERE id=?", (tache,))["statut"], "a_verifier"
        )
        self.assertIsNone(runner.Worker.prochaine())


if __name__ == "__main__":
    unittest.main()
