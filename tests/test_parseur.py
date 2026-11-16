"""Le parseur lit un pack V6 reel et signale ce qui manque sans rien inventer."""
import unittest

import tests
from videroo import parser_v6


class TestParseurPack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = parser_v6.analyser(tests.pack_exemple())

    def test_type_pack(self):
        self.assertEqual(self.r.type_document, "pack")

    def test_entete(self):
        self.assertEqual(self.r.titre, "La Clé du Gymnase")
        self.assertIn("EXPULSION", self.r.moteur)
        self.assertEqual(self.r.duree_annoncee, 77.0)
        self.assertEqual(self.r.nb_scenes_annonce, 10)
        self.assertIn("rends la clé", self.r.regle_mechant)
        self.assertIn("gymnase", self.r.cliffhanger)
        self.assertIn("pas le club", self.r.replique_tiktok)

    def test_personnages(self):
        self.assertEqual(len(self.r.personnages), 3)
        codes = [p["code"] for p in self.r.personnages]
        self.assertEqual(codes, ["R1", "R2", "R3"])
        p = self.r.personnages[0]
        self.assertEqual(p["surnom"], "ARTICHO")
        self.assertEqual(p["espece"], "Artichaut")
        self.assertEqual(p["age"], "âgé")
        self.assertEqual(p["genre"], "homme")
        self.assertEqual(p["etiquette_grok"], "L'HOMME AU BLOUSON VERT")
        self.assertIn("grave et râpeuse", p["voix"])
        self.assertGreater(len(p["prompt_planche"].split()), 200)

    def test_scenes(self):
        self.assertEqual(len(self.r.scenes), 10)
        self.assertEqual([s["numero"] for s in self.r.scenes], list(range(1, 11)))
        self.assertEqual(self.r.duree_calculee, 77.0)
        for s in self.r.scenes:
            self.assertIn(s["duree"], (6, 10, 15), f"scene {s['numero']}")
            self.assertTrue(s["prompt_image"].strip(), f"scene {s['numero']} image")
            self.assertTrue(s["prompt_video"].strip(), f"scene {s['numero']} video")
            self.assertTrue(s["inventaire"].strip(), f"scene {s['numero']} inventaire")
            self.assertTrue(s["ce_qui_change"].strip(), f"scene {s['numero']} changement")
            self.assertIn(s["version_video"], ("normale", "dynamique"))

    def test_references_liees_aux_personnages(self):
        s1 = self.r.scenes[0]
        self.assertEqual(s1["refs_codes"], ["R1", "R2"])
        s4 = self.r.scenes[3]
        self.assertEqual(sorted(s4["refs_codes"]), ["R1", "R2", "R3"])
        s3 = self.r.scenes[2]
        self.assertEqual(s3["refs_codes"], ["R1"])

    def test_dialogues(self):
        s7 = self.r.scenes[6]
        self.assertEqual(s7["duree"], 15)
        self.assertEqual(s7["nb_repliques"], 4)
        self.assertEqual(s7["dialogues"][0]["etiquette"], "LA FEMME AU MANTEAU JAUNE")
        self.assertIn("On vote maintenant", s7["dialogues"][0]["texte"])
        self.assertGreater(s7["nb_mots"], 40)

    def test_miniature_et_prompts_verrouilles(self):
        self.assertIn("Miniature verticale 9:16", self.r.instructions_miniature)
        self.assertIn("@image1", self.r.scenes[0]["prompt_video"])
        self.assertIn("Chaque personnage présent", self.r.scenes[0]["prompt_image"])

    def test_pas_d_avertissement_bloquant(self):
        graves = [a for a in self.r.avertissements if a.niveau == "manquant"]
        self.assertEqual(graves, [], f"inattendu : {[a.message for a in graves]}")


class TestParseurRobustesse(unittest.TestCase):
    def test_metaprompt_distingue_du_pack(self):
        r = parser_v6.analyser(tests.metaprompt_exemple())
        self.assertEqual(r.type_document, "metaprompt")
        self.assertEqual(r.scenes, [])
        self.assertTrue(any("METAPROMPT" in a.message for a in r.avertissements))

    def test_document_vide(self):
        r = parser_v6.analyser("")
        self.assertTrue(any(a.niveau == "manquant" for a in r.avertissements))

    def test_champs_manquants_signales_sans_invention(self):
        texte = (
            "🎬 HISTOIRE : Essai\n\n"
            "**🎨 PLANCHE DE RÉFÉRENCE — TOMATO**\n\nUn prompt trop court.\n\n"
            "SCÈNE 1 — 🏠 UNE SCENE — 7 secondes\n\n"
            "Attache : Tomato\n"
        )
        r = parser_v6.analyser(texte)
        messages = " | ".join(a.message for a in r.avertissements)
        self.assertIn("Prompt image absent", messages)
        self.assertIn("Prompt vidéo absent", messages)
        self.assertIn("hors des valeurs acceptées par Grok", messages)
        self.assertEqual(r.scenes[0]["prompt_image"], "")
        self.assertEqual(r.scenes[0]["refs_codes"], [])

    def test_reference_inconnue_signalee(self):
        texte = (
            "🎬 HISTOIRE : Essai\n\n"
            "**🎨 PLANCHE DE RÉFÉRENCE — TOMATO**\n\nPrompt.\n\n"
            "SCÈNE 1 — 🏠 TITRE — 6 secondes\n\n"
            "🎭 Références à charger dans ChatGPT : Tomato, Inconnuito\n\n"
            "**🖼️ PROMPT IMAGE**\n\nimage\n\n"
            "**🎬 Version normale : 6 s ✅ recommandée — raison**\n\nvideo\n"
        )
        r = parser_v6.analyser(texte)
        self.assertEqual(r.scenes[0]["refs_codes"], ["R1"])
        self.assertTrue(any("Inconnuito" in a.message for a in r.avertissements))

    def test_import_json(self):
        contenu = (
            '{"titre":"Depuis JSON","scenes":[{"numero":1,"duree":6,'
            '"prompt_image":"img","prompt_video":"vid","dialogues":["Bonjour toi"]}],'
            '"personnages":[{"surnom":"KIWITO","espece":"Kiwi","prompt_planche":"pl"}]}'
        )
        r = parser_v6.analyser(contenu)
        self.assertEqual(r.type_document, "pack")
        self.assertEqual(r.titre, "Depuis JSON")
        self.assertEqual(len(r.scenes), 1)
        self.assertEqual(r.personnages[0]["code"], "R1")
        self.assertEqual(r.scenes[0]["nb_mots"], 2)

    def test_blocs_code_toleres(self):
        texte = tests.pack_exemple().replace(
            "**🖼️ PROMPT IMAGE**", "**🖼️ PROMPT IMAGE**\n```", 1
        )
        r = parser_v6.analyser(texte)
        self.assertEqual(len(r.scenes), 10)
        self.assertTrue(r.scenes[0]["prompt_image"].strip())


if __name__ == "__main__":
    unittest.main()


class TestParseurFormatsVariantes(unittest.TestCase):
    """Formats réellement produits par ChatGPT, differents du gabarit strict."""

    @classmethod
    def setUpClass(cls):
        cls.r = parser_v6.analyser(tests.pack_variantes())

    def test_titre_repris_de_la_premiere_ligne(self):
        self.assertEqual(self.r.titre, "LA DERNIÈRE TOURNÉE DU MARDI")
        self.assertTrue(
            any("première ligne" in a.message for a in self.r.avertissements),
            "la deduction du titre doit etre signalee",
        )

    def test_libelles_abreges_reconnus(self):
        for s in self.r.scenes:
            self.assertTrue(s["inventaire"], f"scene {s['numero']} : inventaire abrege")
            self.assertTrue(s["refs_codes"], f"scene {s['numero']} : refs abregees")
        messages = " ".join(a.message for a in self.r.avertissements)
        self.assertNotIn("Inventaire", messages)
        self.assertNotIn("Références à charger", messages)

    def test_codes_explicites_utilises(self):
        self.assertEqual([p["code"] for p in self.r.personnages], ["R1", "R2", "P1"])
        messages = " ".join(a.message for a in self.r.avertissements)
        self.assertNotIn("attribué(s)", messages, "aucun code ne doit etre invente ici")

    def test_planche_objet_reconnue(self):
        objet = self.r.personnages[-1]
        self.assertEqual(objet["code"], "P1")
        self.assertEqual(objet["surnom"], "CARNET DE BORD")
        self.assertIn("carnet de bord", objet["prompt_planche"].lower())
        self.assertGreater(len(objet["prompt_planche"].split()), 100)

    def test_pas_de_voix_reclamee_pour_un_objet(self):
        for a in self.r.avertissements:
            self.assertNotIn("P1", a.cible + a.message if a.niveau == "manquant" else "")

    def test_references_prefixees_par_leur_code(self):
        self.assertEqual(self.r.scenes[0]["refs_codes"], ["R1", "R2"])
        self.assertEqual(self.r.scenes[1]["refs_codes"], ["R1", "R2", "P1"])
        self.assertEqual(self.r.scenes[2]["refs_codes"], ["R1", "R2", "P1"])

    def test_prompts_de_planche_non_pollues(self):
        for p in self.r.personnages:
            self.assertNotIn("Objet de référence", p["prompt_planche"])
            self.assertNotIn("PLANCHE DE RÉFÉRENCE", p["prompt_planche"])
            self.assertNotIn("Étiquette Grok", p["prompt_planche"])

    def test_synopsis_sans_deux_points(self):
        self.assertIn("Pistachio conduit la camionnette", self.r.une_phrase)

    def test_age_et_genre_hors_crochets(self):
        p = self.r.personnages[0]
        self.assertEqual(p["espece"], "Pistache")
        self.assertEqual(p["age"], "47 ans")
        self.assertEqual(p["genre"], "homme")
        self.assertIn("chauffeur", p["role"].lower())
        self.assertNotIn("47 ans", p["role"])

    def test_aucun_avertissement_bloquant(self):
        graves = [a for a in self.r.avertissements if a.niveau == "manquant"]
        self.assertEqual(graves, [], f"inattendu : {[a.message for a in graves]}")

    def test_le_format_strict_reste_lu(self):
        """Le gabarit strict du V6 ne doit pas regresser."""
        strict = parser_v6.analyser(tests.pack_exemple())
        self.assertEqual(len(strict.scenes), 10)
        self.assertEqual([p["code"] for p in strict.personnages], ["R1", "R2", "R3"])
        self.assertEqual(strict.scenes[0]["refs_codes"], ["R1", "R2"])


class TestParseurEnTeteTronquee(unittest.TestCase):
    """Cas réel : le pack est collé à partir du synopsis, sans son en-tête."""

    PACK = (
        "L'histoire\n"
        "À 23 h 54, Goyavella apprend que le nouveau propriétaire compte éteindre la "
        "lumière à minuit pour transformer la tour en hôtel de luxe.\n\n"
        "Mais le ferry Luciole, transportant 46 passagers, s'approche des rochers.\n\n"
        "Première réplique : \"À minuit, je coupe ce phare. Tu es virée !\"\n\n"
        "Cliffhanger : Qui éteint les autres phares ?\n\n"
        "🎨 PHASE 1 — PLANCHES DE RÉFÉRENCE\n"
        "R1 — GOYAVELLA\n"
        "Espèce : Goyave rose corail · Âge : 27 ans · Rôle : Gardienne du phare\n\n"
        "🔒 Voix (VERROU) : voix adulte femme : alto clair, débit vif.\n\n"
        "🏷️ Étiquette Grok : LA FEMME À L'IMPERMÉABLE JAUNE\n\n"
        "🎨 PLANCHE DE RÉFÉRENCE — GOYAVELLA\n\n"
        "Une planche de référence de personnage épurée sur fond blanc pur, quatre vues "
        "en pied et trois gros plans, corps adulte élancé, matière de goyave intacte.\n\n"
        "P1 — MÉCANISME DU PHARE\n"
        "🎨 PLANCHE DE RÉFÉRENCE OBJET — PHARE P1\n\n"
        "Crée une planche de référence technique verticale 9:16 sur fond blanc pur "
        "montrant le mécanisme sous quatre angles, plus trois détails en gros plan.\n\n"
        "SCÈNE 1 — 🌃 LA LUMIÈRE COUPÉE — 6 secondes\n\n"
        "Attache : Goyavella\n\n"
        "🧾 Inventaire : la lanterne, le levier rouge\n\n"
        "🎭 Références : R1 Goyavella, P1 Mécanisme du phare\n\n"
        "⏱️ Durée Grok : 6 s — 1 réplique, 20 mots\n\n"
        "Ce qui change : la lumière s'éteint.\n\n"
        "🖼️ PROMPT IMAGE\n\n"
        "Intérieur de la lanterne du phare, lentille de Fresnel, levier rouge.\n\n"
        "🎬 Version normale : 6 s ✅ recommandée — l'ultimatum doit être entendu\n\n"
        "Dialogue: [6 secondes.\n"
        "RÉPLIQUE 1 — LA FEMME À L'IMPERMÉABLE JAUNE : \"Le ferry arrive, il faut la "
        "rallumer tout de suite.\"]\n"
    )

    @classmethod
    def setUpClass(cls):
        cls.r = parser_v6.analyser(cls.PACK)

    def test_la_duree_d_une_scene_n_est_pas_prise_pour_celle_de_l_episode(self):
        self.assertIsNone(
            self.r.duree_annoncee,
            "sans en-tête, aucune durée annoncée ne doit être inventée",
        )
        messages = " ".join(a.message for a in self.r.avertissements)
        self.assertNotIn("en-tête annonce 6 s", messages)

    def test_aucun_titre_invente_a_partir_du_synopsis(self):
        self.assertEqual(self.r.titre, "", "une phrase de synopsis n'est pas un titre")
        self.assertTrue(
            any("Aucun titre reconnu" in a.message for a in self.r.avertissements)
        )

    def test_premiere_replique_tient_lieu_de_regle_du_mechant(self):
        self.assertIn("je coupe ce phare", self.r.regle_mechant)

    def test_planche_objet_avec_qualificatifs_multiples(self):
        codes = [p["code"] for p in self.r.personnages]
        self.assertEqual(codes, ["R1", "P1"])
        objet = self.r.personnages[1]
        self.assertEqual(objet["surnom"], "PHARE P1")
        self.assertIn("mécanisme", objet["prompt_planche"].lower())

    def test_reference_objet_reliee_a_sa_planche(self):
        self.assertEqual(self.r.scenes[0]["refs_codes"], ["R1", "P1"])

    def test_le_synopsis_est_recupere(self):
        self.assertIn("Goyavella", self.r.une_phrase)

    def test_aucun_point_bloquant(self):
        graves = [a for a in self.r.avertissements if a.niveau == "manquant"]
        self.assertEqual(graves, [], f"inattendu : {[a.message for a in graves]}")


class TestPackSansMechant(unittest.TestCase):
    """Une histoire peut n'avoir aucun antagoniste : l'obstacle est la pauvrete.

    Le pack de Yacine « IL A TOUT DONNÉ À SA PETITE SŒUR » l'ecrit noir sur
    blanc. Reclamer une regle du mechant serait reclamer une invention.
    """

    PACK = """HISTOIRE : « IL A TOUT DONNÉ À SA PETITE SŒUR »
PARTIE 1 SUR 2

Durée

1 min 17 s

Scènes

10

La vidéo d'origine ne contient pas de méchant : le véritable obstacle est la
pauvreté des deux enfants. Je conserve cette caractéristique au lieu d'inventer
un antagoniste.

Identité du film
- Moteur : sacrifice familial et solidarité.
- Enjeu : nourrir sa petite sœur et lui assurer un toit.
- Prix du sacrifice : Kakino se prive de nourriture.
"""

    @classmethod
    def setUpClass(cls):
        cls.r = parser_v6.analyser(cls.PACK)

    def test_absence_de_mechant_declaree_nest_pas_un_manque(self):
        # L'extrait n'a ni planche ni scene : seul l'entête est jugé ici.
        graves = [
            a for a in self.r.avertissements
            if a.niveau == "manquant" and a.cible == "entête"
        ]
        self.assertEqual(graves, [], f"inattendu : {[a.message for a in graves]}")

    def test_la_phrase_du_pack_est_citee_telle_quelle(self):
        notes = [a for a in self.r.avertissements if a.cible == "entête"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].niveau, "info")
        self.assertIn("ne contient pas de méchant", notes[0].message)

    def test_le_manque_reste_signale_si_rien_nest_declare(self):
        muet = self.PACK.replace("ne contient pas de méchant", "se déroule la nuit")
        r = parser_v6.analyser(muet)
        graves = [a.cible for a in r.avertissements if a.niveau == "manquant"]
        self.assertIn("entête", graves)

    def test_duree_en_minutes_et_secondes(self):
        self.assertEqual(self.r.duree_annoncee, 77.0)

    def test_etiquettes_posees_seules_sur_leur_ligne(self):
        self.assertEqual(self.r.nb_scenes_annonce, 10)

    def test_partie_x_sur_y(self):
        self.assertEqual(self.r.parties, "1 sur 2")

    def test_enjeu_et_prix_du_sacrifice(self):
        self.assertIn("nourrir sa petite sœur", self.r.une_phrase)
        self.assertIn("se prive de nourriture", self.r.prix_final)

    def test_rien_nest_invente_pour_lunivers(self):
        self.assertEqual(self.r.univers, "")
