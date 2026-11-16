# Videroo

Atelier **local** de production d'épisodes animés verticaux, à partir du pack produit par
ton métaprompt **V6 — Brain Rot Story Drama**.

Tout reste sur ton ordinateur : projets, médias, journaux, base de données, profil
navigateur. Aucun abonnement à un service d'automatisation n'est nécessaire. Les quotas
de tes fournisseurs IA (ChatGPT, Grok) restent les seules limites applicables.

**Aucune API payante.** Videroo n'achète rien et n'a besoin d'aucune clé d'API OpenAI ou
xAI. Il se connecte aux sites `chatgpt.com` et `grok.com` dans un Chrome qui t'appartient,
avec **tes comptes et tes abonnements déjà payés** — exactement comme si tu cliquais
toi-même, en plus rapide. Les adresses en `/local/…` que tu peux voir passer sont les
adresses internes de la page, sur `127.0.0.1` : elles ne sortent pas de ta machine et ne
coûtent rien.

---

## 1. Pourquoi cette architecture

| Besoin | Choix | Raison |
|---|---|---|
| Serveur local | **Python 3.12 + FastAPI**, écoute sur `127.0.0.1` uniquement | léger, aucune exposition réseau |
| Projets et progression | **SQLite** (`donnees/videroo.db`), mode WAL | un seul fichier, zéro service à maintenir |
| Pilotage des sites | **Playwright** sur un profil Chrome dédié | interfaces web normales, aucune API privée |
| Médias | **FFmpeg / FFprobe** | vérification et assemblage, un seul encodage par export |
| File de travail | **un thread worker interne** adossé à une table SQLite | pas de Celery ni Redis : l'exigence « une seule génération active » est native, et la consommation mémoire reste basse sur 16 Go |

---

## 2. Installation

Prérequis : Ubuntu, Python 3.11+, FFmpeg, Google Chrome.

```bash
sudo apt install ffmpeg
```

Puis, dans le dossier du projet :

```bash
./lancer.sh
```

Le script crée l'environnement Python, installe les dépendances et démarre l'application
sur **http://127.0.0.1:8077**.

L'automatisation utilise **ton Google Chrome déjà installé** (canal `chrome`) : vérifié sur
cette machine, aucun téléchargement supplémentaire n'est nécessaire. Si Chrome venait à
manquer, l'application bascule sur Chromium, qui s'installe alors une fois avec :

```bash
./.venv/bin/python -m playwright install chromium
```

Changer le port : `VIDEROO_PORT=9000 ./lancer.sh`

---

## 3. Première utilisation

1. Ouvre **http://127.0.0.1:8077**.
2. Va dans **Admin → Mes comptes**. Clique sur **« Ouvrir ChatGPT et me connecter »** :
   le navigateur dédié s'ouvre sur le site, tu te connectes normalement, puis tu reviens
   cliquer sur **« Vérifier »**. Même chose pour Grok. La pastille passe alors à
   « connecté ». La session est conservée dans `donnees/profil_navigateur` et resservira
   à chaque génération : tu ne te connectes qu'une fois.
3. Onglet **Métaprompt V6** : colle ton métaprompt une seule fois. Il est conservé tel
   quel, jamais réécrit, et son empreinte SHA est affichée pour que tu puisses vérifier
   qu'il n'a pas bougé.

Vérifier un compte **ne consomme aucun quota** : l'application ouvre simplement la page et
regarde si la zone de saisie du site est accessible.

> **Le profil dédié est séparé de ton Chrome habituel.** Être connecté à ChatGPT dans ta
> fenêtre Chrome de tous les jours ne suffit pas : il faut te connecter **dans la fenêtre
> ouverte par Videroo**, une fois par service.

### Plusieurs comptes par service, avec bascule sur quota

Chaque compte a **son propre profil navigateur**, donc sa propre session : trois
abonnements Grok cohabitent sans se mélanger, et ChatGPT peut utiliser une identité Google
différente de Grok.

> **Un profil = une identité Google.** Deux comptes qui partagent un dossier de profil
> partagent aussi leur session Google : en cliquant « me connecter » sur l'un, tu te
> retrouves connecté avec l'identité de l'autre. L'admin signale le partage en rouge et
> propose **« Lui donner un profil séparé »**, avec ou sans copie de la session en cours.
> Un profil vierge force le site à redemander quel compte utiliser — c'est ce qu'on veut
> quand deux services doivent utiliser deux comptes Google distincts.

Dans **Admin → Mes comptes** :

* **Ajouter un compte** par service, avec un nom et une adresse e-mail ;
* **Ouvrir et me connecter** ouvre le site dans le profil de *ce* compte ;
* les flèches ▲▼ fixent la priorité : **le premier compte disponible travaille** ;
* une case « dans la rotation » met un compte de côté sans le supprimer ;
* **Déconnecter** efface le profil de ce compte seul — refusé si le profil est partagé,
  pour ne pas déconnecter les autres ; **Supprimer** le retire de la liste.

**Quel compte travaille en premier.** Par défaut, celui dont le quota **se recharge le
plus tôt** : c'est le plus périssable. Un quota qui se refait ce soir à 23:55 ne vaut pas
un quota qui attend mardi — autant dépenser le premier. L'ordre réel est affiché en clair
au-dessus de chaque liste, avec le délai restant, et il change tout seul au fil de la
semaine. Les flèches ▲▼ ne servent alors qu'à départager deux comptes à égalité ; pour
décider toi-même, bascule sur « l'ordre de la liste » dans Réglages → Comptes.

**Horaire de recharge par compte.** Chaque abonnement a sa propre réinitialisation
hebdomadaire — un jour et une heure, réglés sur sa ligne (par exemple : tous les vendredi
à 23:55). Un compte épuisé redevient disponible exactement à ce moment-là, pas avant. Sans
horaire renseigné, c'est le délai global de Réglages → Comptes qui s'applique.

**La bascule.** Quand un service annonce une limite d'usage, le compte est marqué
« épuisé » et la production passe au suivant. Point important pour tes quotas : la bascule
n'a lieu que si la limite est détectée **avant le moindre envoi** — là, rien n'a été
consommé, donc réessayer ailleurs ne coûte rien. Si la limite apparaît après l'envoi, la
tâche passe en « à vérifier » et rien n'est relancé. Chaque compte n'est essayé **qu'une
fois** par génération.

Le bouton **Réarmer** remet un compte dans la rotation tout de suite, sans attendre son
horaire. Quand tous les comptes sont à court, le projet est mis en pause avec le motif et
la date de la prochaine recharge.

Le journal indique à chaque fois quel compte a travaillé.

### Travailler sans fenêtres à l'écran

Réglage **Admin → Comptes → « Comment le navigateur travaille pendant les générations »** :

| Mode | Ce qui se passe |
|---|---|
| **écran à part** *(par défaut)* | Chrome, un vrai Chrome complet, tourne sur un écran X qui n'est pas le tien (Xvfb). **Aucune fenêtre n'apparaît jamais**, et les protections anti-robot le voient comme un navigateur ordinaire. |
| **hors écran** | la fenêtre est placée très loin de l'écran. Suffit sous X11. **Sous GNOME en session Wayland, le compositeur la ramène sur ton bureau** : c'est la raison des fenêtres qui surgissaient. |
| **sans fenêtre** | le « headless » intégré de Chrome, le plus léger. **Mesuré : Cloudflare bloque ChatGPT dans ce mode** (page « Un instant… »). L'application retombe alors automatiquement sur « hors écran ». |
| **fenêtre visible** | pour regarder l'automatisation travailler. |

**L'écran à part demande Xvfb**, absent d'Ubuntu par défaut. Il est déjà installé ici
**sans droits administrateur** : le paquet officiel Ubuntu a été dépaqueté dans
`~/.local/opt/xvfb`, et l'application l'y trouve toute seule. Pour l'installer un jour
proprement à l'échelle du système :

```bash
sudo apt install xvfb
```

Si Xvfb venait à manquer, l'Atelier et l'Admin l'écrivent en toutes lettres et
l'application retombe sur « hors écran » — donc des fenêtres peuvent réapparaître.

**Il ne suffit pas de poser `DISPLAY` : il faut l'imposer.** Chrome choisit son
affichage tout seul (« ozone auto ») et **préfère Wayland**. Il retrouve la session de
GNOME par `XDG_RUNTIME_DIR/wayland-0` même quand `WAYLAND_DISPLAY` a été retiré de son
environnement, ignore `DISPLAY`, et la fenêtre réapparaît sur le bureau. L'application
lui passe donc `--ozone-platform=x11`.

Comment on l'a vu : l'écran virtuel était **vide** — `xwininfo -display :99` ne montrait
aucune fenêtre — pendant que la fenêtre s'affichait. Après correction :

```
écran :99 → "about:blank - Google Chrome"  (google-chrome .../Videroo/donnees/profils/…)
écran :0  → aucun Chrome de Videroo
```

Un garde-fou reste en place : après chaque ouverture sur l'écran virtuel, l'application
compte les fenêtres qui s'y trouvent. S'il n'y en a aucune, elle l'écrit dans le journal
en alerte, au lieu de te laisser croire que tout va bien.

Quel que soit le choix, **la fenêtre s'ouvre toujours pour te connecter à un compte** :
une connexion se fait à la main, pas en arrière-plan.

### Une conversation par histoire

Toutes les planches et toutes les images d'une même histoire sortent de la **même
conversation ChatGPT**. L'adresse est mémorisée à la première génération et n'est plus
jamais écrasée ; une nouvelle histoire repart d'un chat neuf, automatiquement. L'onglet
Générations affiche la conversation en cours, avec un bouton **« Nouveau chat ChatGPT »**
si tu veux repartir de zéro.

Pour Grok, ta méthode garde **un seul chat pour toutes les histoires**, avec les règles
installées une fois et une annonce « Nouvelle série » par histoire : renseigne alors son
adresse dans **Admin → Réglages → « Adresse de Grok »**.

### Vitesse : pourquoi c'était lent, et ce qui a changé

Une préparation de planche prenait **70 secondes** avant même d'envoyer le prompt. Mesuré :
la page se chargeait en 1,4 s, mais la recherche de la zone de saisie prenait **62 s**.
Chaque sélecteur obsolète de la liste attendait son délai complet avant de passer au
suivant ; trois sélecteurs morts coûtaient trois fois quinze secondes.

La recherche balaie désormais **toute la liste par passes rapides** jusqu'à une limite
globale : le bon sélecteur est trouvé en une seconde, où qu'il soit. Même mesure après
correction : **4,8 s**. Les sélecteurs vérifiés sur le site sont aussi placés en tête.

Le journal d'un projet affiche maintenant un chronométrage (`⏱ page prête à 2 s`,
`⏱ prompt envoyé à 5 s`…) : si une étape traîne, on voit immédiatement laquelle.

**La détection du résultat a été resserrée** : la page est inspectée toutes les 1,2 s au
lieu de 3 s pour une image, et toutes les 1,5 s au lieu de 5 s pour une vidéo. L'image
attendue est cherchée **avant** les signes de quota, qui sont la partie lente du balayage.
Sur un épisode de dix scènes, c'est près d'une minute récupérée — du temps d'attente pur,
jamais du quota.

### ChatGPT et Grok travaillent en parallèle

Avant, la chaîne attendait : image de la scène 1, puis sa vidéo, puis image de la scène 2…
Chaque attente de Grok — souvent plusieurs minutes — laissait ChatGPT inactif.

Maintenant, **un worker par fournisseur**. Dès qu'une image de scène existe, sa vidéo part
chez Grok, et pendant que Grok rend, ChatGPT prépare l'image de la scène suivante. Sur dix
scènes, c'est presque la moitié du temps d'attente en moins.

Ce qui rend la chose sûre : les deux fournisseurs **ne partagent ni compte, ni quota, ni
navigateur**. Chacun a sa propre session Chrome, sur son propre thread — Playwright
interdit qu'un objet traverse une frontière de thread, d'où `session()` qui rend toujours
celle du thread courant.

La règle d'origine « une seule génération active à la fois » devient donc **« une seule par
fournisseur »**. La garantie qui protégeait réellement tes quotas — *une seule génération
automatique par élément* — est inchangée, portée par les index uniques de la base, et
vérifiée par les tests.

### L'image récupérée est la dernière, pas la « nouvelle »

Une planche CASSISANDRO a reçu une image de GOYAVELLA. Cause : l'application mémorisait les
adresses des images présentes avant l'envoi, puis cherchait une adresse inconnue. Or
**ChatGPT recrée une adresse `blob:` à chaque chargement de page** : une vieille image
prend une adresse « neuve » et se fait prendre pour le résultat.

L'application **compte** désormais les images avant l'envoi, attend qu'il y en ait une de
plus, et prend **la dernière de la page**. La position est fiable, l'adresse ne l'est pas.

### Sobriété : un seul Chrome à la fois

Mesuré le 6 novembre 2026 : chaque profil ouvert pèse environ **270 Mo**, et
l'application en ouvrait jusqu'à deux pour une seule génération — un pour le compte
utilisé, plus un profil « principal » ouvert pour rien au lancement de la chaîne.

Trois corrections :

- **le lancement de la chaîne n'ouvre plus aucun navigateur** : il vérifie seulement
  que Playwright est là. C'est la tâche qui ouvre le profil de son compte ;
- **ouvrir un profil ferme l'autre, au sein d'une même session.** Avec le travail en
  parallèle, il y a donc au plus deux Chrome : celui de ChatGPT et celui de Grok ;
- **arguments sobres** : `--disable-gpu` (il n'y a pas de carte graphique sur un écran
  virtuel), `--disable-extensions`, `--mute-audio`, `--disable-dev-shm-usage`,
  `--renderer-process-limit=2`.

« Arrêter » ferme aussi le navigateur et rend la mémoire.

La barre de pilotage de l'Atelier affiche en permanence **où tourne Chrome et ce qu'il
pèse** : « 1 Chrome sur l'écran :99, hors de ton bureau · 270 Mo ». Si une fenêtre
apparaissait malgré tout sur ton bureau, la ligne passerait en rouge et le dirait.

**Attention au reste de la machine.** Sur 16 Go, relevé pendant une génération :
l'application ChatGPT de bureau occupait 4 Go, Chrome 2,8 Go, codex 2,5 Go — soit près
de 14 Go pris avant même que Videroo ne démarre. Aucune optimisation de l'application ne
compense ça : il faut fermer ce qui ne sert pas pendant une production.

### Les images de ChatGPT : ce qui bloquait vraiment

Trois planches sont restées « à vérifier » après 420 s d'attente chacune. L'image avait
pourtant bien été générée, et le quota bien consommé : elle était simplement invisible
pour l'application. Deux causes, relevées sur le site réel le 6 novembre 2026 :

- **le conteneur a changé.** `div[data-message-author-role='assistant']` ne correspond
  plus à rien. L'image vit maintenant dans `[data-testid='generated-image-gallery']` et
  porte l'alt `Image générée 1` ;
- **l'adresse est un `blob:`**, pas une URL `oaiusercontent`. Un `blob:` n'existe que dans
  l'onglet qui l'a créé : `page.request.get()` ne peut pas le lire. L'application demande
  donc les octets au navigateur lui-même (`navigateur.octets_de_image`).

L'avatar du compte (`cdn.auth0.com`) est écarté explicitement : sans ça, il serait pris
pour une image générée dès l'ouverture de la page.

Les deux planches déjà produites — GOYAVELLA et CÉLERANDRO — ont été récupérées depuis la
conversation et importées dans le projet : elles n'ont pas été regénérées, donc pas
repayées.

### Joindre les planches : un champ caché

Les scènes ne partent pas sans leurs planches jointes — « un code comme R1 ne remplace
pas une image ». Or les deux `input[type=file]` de ChatGPT portent la classe `hidden`,
et un *locator* Playwright attend qu'un élément soit visible : il expirait sans rien
joindre, et la scène échouait avec « sélecteur de fichier introuvable ».

L'application passe maintenant par le **handle DOM**, qui ne contrôle pas la visibilité,
et vise d'abord le champ qui accepte les images (`input[type='file'][accept*='image']`) :
le second champ, générique, les refuse. Le locator reste en secours.

Quand ça échoue, le message **nomme chaque sélecteur essayé et l'erreur exacte**, au lieu
d'un « introuvable » qui n'apprend rien.

Vérifié le 6 novembre 2026 sur le site réel : une planche jointe, une vignette apparue
dans le composeur, page rechargée derrière — sans rien envoyer, donc sans quota.

### La miniature TikTok

Dernière étape de la chaîne, dans la même conversation ChatGPT, **avec les planches des
personnages jointes** pour qu'ils soient identiques à ceux des scènes.

Le prompt est celui de Yacine, conservé mot pour mot dans
`videroo/ressources/prompt_miniature.txt` ; l'application n'y remplace que deux repères :
`{titre}` par le titre de l'histoire, `{episode}` par le numéro de partie. Un prompt écrit
à la main dans l'onglet Export reste prioritaire.

### L'envoi est vérifié, pas supposé

Cliquer le bouton d'envoi ne prouve pas que le message est parti : le clic peut tomber à
côté, ou le bouton être encore désactivé. L'application **confirme que la zone de saisie
s'est vidée**. Sinon elle presse Entrée et revérifie. Et si rien ne part, elle le dit
**tout de suite** au lieu d'attendre sept minutes une image qui n'arrivera jamais — avec
le prompt laissé prêt dans la page pour que tu l'envoies toi-même.

### Fenêtres restées ouvertes

Si l'application est arrêtée brutalement, les fenêtres Chrome qu'elle avait ouvertes
survivent et **gardent les profils verrouillés** : plus aucune génération ne peut démarrer
sur ces comptes. L'admin les détecte et propose **« Fermer les fenêtres restées
ouvertes »**, qui ne touche que les Chrome lancés sur les profils de Videroo — jamais ton
navigateur personnel. Tes connexions sont enregistrées sur le disque : les fermer ne te
déconnecte pas.

### Si Google refuse la connexion (erreur 400)

Google bloque souvent la connexion depuis une fenêtre pilotée. Si ton compte ChatGPT passe
par « Se connecter avec Google », bascule sur le second mode, dans **Admin → Accès au
navigateur** :

1. ferme **complètement** Chrome (toutes les fenêtres) ;
2. relance-le avec `google-chrome --remote-debugging-port=9222` ;
3. passe « Comment Videroo accède au navigateur » sur **Chrome existant** et enregistre ;
4. clique sur **Tester le branchement**.

Videroo se branche alors sur **ton** Chrome, là où tu es déjà connecté : il n'y a plus
aucune connexion à faire, donc plus de blocage. Dans ce mode, l'application agit dans ton
navigateur personnel — elle ne pilote que ChatGPT et Grok, et ne le ferme jamais.

### Pourquoi l'état d'un compte peut rester « à confirmer »

Mesuré sur grok.com : la page est **identique** connectée ou non — même zone de saisie,
mêmes cookies, même lien de connexion. Aucune détection automatique ne peut trancher.
Dans ce cas l'application affiche **« à confirmer »** au lieu d'inventer une certitude, et
te laisse deux boutons pour trancher toi-même. La preuve définitive reste la première
génération réussie.

---

## 4. Le flux de travail

**Projets → Histoire → Personnages → Scènes → Générations → Montage → Export**

### Le type d'histoire, choisi à la création

Un projet appartient à une des trois familles, et ce choix se fait **une fois, au moment
de créer le projet** :

| Type | Métaprompt | Forme du pack |
|---|---|---|
| **Histoire de fruits** | le tien, collé dans l'onglet Métaprompt | V6 : blocs `PLANCHE DE RÉFÉRENCE — X`, `SCÈNE 1 — …` |
| **Chronique** | livré avec l'application | étiquettes : `RÉFÉRENCE X :`, `IMAGE (…) :`, `VIDÉO (type · durée · …) :` |
| **Bébé IA** | livré avec l'application | blocs `R1 — ROSE, l'héroïne`, `O1 — Le doudou`, `SCÈNE 01 — …`, `Prompt image — ChatGPT`, `Prompt vidéo — Grok` |

Le type décide de deux choses, et de deux choses seulement : **quel métaprompt tu copies**
et **quel lecteur lit ton pack**. Tout le reste est identique — planches de référence, puis
pour chaque scène son image dans ChatGPT et sa vidéo dans Grok, puis la miniature, puis
CapCut. Les projets créés avant ce choix restent en « Histoire de fruits » et ne changent
pas de comportement ; le métaprompt V6 déjà enregistré garde son fichier, rien n'est à
recoller.

Les deux métaprompts livrés sont conservés **mot pour mot** dans `videroo/ressources/`.
Dès que tu enregistres le tien pour un type, c'est le tien qui est utilisé.

Un pack bébé a une particularité qui change quelque chose pour la chaîne : **les objets
(`O1`, `O2`…) sont de vraies planches**, avec leur propre prompt, générées et jointes
exactement comme les personnages. Un code `O1` cité dans « Références ChatGPT » ne
remplace pas une image jointe, pas plus qu'un `R1`.

Si tu colles un pack dans un projet du mauvais type, il est **lu quand même** et l'écart de
format est signalé : un copier-coller valable ne doit pas se heurter à un refus sec.

### Histoire
Colle le package complet produit par ton V6 (texte brut, Markdown ou JSON), ou importe un
fichier. L'application :

* distingue le **métaprompt** d'un **pack d'histoire déjà généré** et refuse de les confondre ;
* extrait titre, moteur, univers, règle du méchant, menace évolutive, prix final,
  cliffhanger, réplique son TikTok, instructions de miniature ;
* extrait les personnages (identifiants `R1`, `R2`… attribués dans l'ordre des planches),
  leur espèce, âge, genre, verrou de voix, étiquette Grok et prompt de planche ;
* extrait les scènes dans l'ordre avec durée, inventaire, « ce qui change », prompt image,
  prompt vidéo, version recommandée, répliques et nombre de mots ;
* **signale ce qui manque ou paraît douteux sans rien inventer** (champ absent, durée hors
  6/10/15 s, référence citée qui ne correspond à aucune planche, dialogue hors calibrage
  V6, somme des durées hors 70–110 s).

Le parseur **tolère les variantes de format** que ChatGPT produit en pratique :

* libellés complets ou abrégés (`🧾 Inventaire de l'image de départ :` ou `🧾 Inventaire :`,
  `🎭 Références à charger dans ChatGPT :` ou `🎭 Références :`, `⏱️ Durée Grok :`) ;
* identifiants explicites (`R1 — PHYSALINO`) repris tels quels, ou attribués dans l'ordre
  des planches et signalés comme tels ;
* références préfixées par leur code (`R1 Physalino, R2 Brocolandro`) ;
* **planches d'objet** (`🔧 Objet de référence — P1 : …` suivi de
  `🖼️ PROMPT PLANCHE OBJET — …`), traitées comme des références à joindre au même titre
  qu'un personnage, sans réclamer de verrou de voix ni d'étiquette Grok ;
* pack sans ligne `🎬 HISTOIRE :` — le titre est repris de la première ligne, et l'info
  est affichée ;
* synopsis sous un titre sans deux-points (`📌 L'histoire`) ;
* **étiquettes posées seules sur leur ligne** — le canevas de ChatGPT rend souvent
  `Durée` puis `1 min 17 s` sur deux lignes au lieu de `Durée : 1 min 17 s`. La durée est
  lue en minutes et secondes, pas seulement en secondes ;
* `PARTIE 1 SUR 2` écrit sans deux-points ;
* bloc `Identité du film` : `Enjeu` tient lieu de phrase de résumé, `Prix du sacrifice`
  de prix final ;
* **histoire sans méchant.** Si le pack écrit noir sur blanc qu'il n'y a pas
  d'antagoniste — « La vidéo d'origine ne contient pas de méchant : le véritable obstacle
  est la pauvreté des deux enfants » — l'absence de règle du méchant devient une simple
  note et la phrase du pack est citée telle quelle. Réclamer cette règle reviendrait à
  réclamer une invention. Le manque reste signalé quand le pack ne dit rien.

L'étape de validation compte **les points à corriger**, pas les notes : un pack lisible de
bout en bout affiche « rien à corriger » même s'il porte des lignes d'information.

Un réimport **conserve les images et vidéos déjà obtenues**.

### Personnages
Génère les planches de référence, ou importe des références que tu possèdes déjà.
Chaque champ est éditable avant toute génération.

### Scènes
Chaque scène montre ses prompts, ses références, son image, sa vidéo, sa durée et son
état. Tu corriges les champs, tu règles les coupes de début et de fin, puis tu **valides**.
C'est la validation qui autorise une génération.

### Atelier — la page où tout se passe

C'est la vue principale, et elle se lit de haut en bas, dans l'ordre où l'application
travaille :

1. **un bouton** — « Lancer la génération », avec en permanence ce qui est en cours, chez
   quel fournisseur, et ce qui reste à faire ;
2. **le suivi en quatre étages**, empilés de haut en bas et reliés par des flèches :

   | Étage | Ce qu'il montre |
   |---|---|
   | **01 · Prompts** | une ligne par scène, dont la barre se remplit à mesure que son image puis sa vidéo arrivent |
   | **02 · ChatGPT** | une vignette par planche et par image de départ — la vraie miniature dès qu'elle existe, avec sa coche verte |
   | **03 · Grok** | une tuile par vidéo, celle en cours de génération pulse |
   | **04 · Projet** | le compte des vidéos obtenues, et le bouton qui les télécharge toutes |

   L'étage où l'application travaille s'allume. Cliquer une vignette descend sur la carte
   détaillée correspondante. **Aucun pourcentage inventé** : Grok n'annonce pas sa
   progression, donc l'application affiche un état (`en cours`, `terminé`) et des
   fractions réelles (`1 / 3`), jamais un « 78 % » imaginaire ;
3. **le détail, élément par élément** — les planches en vignettes, puis les scènes, une
   ligne chacune : à **gauche** la carte *ChatGPT — image de départ*, à **droite** la
   carte *Grok — vidéo animée*, que tu peux lire sur place ;
4. **la récupération des vidéos** pour CapCut.

Chaque carte dit son état (`à faire`, `en cours`, `terminé`, `à vérifier`, `à toi de
jouer`) et porte le bouton qui va avec. Sur une carte terminée : **« Je n'aime pas —
regénérer »**, qui demande confirmation parce que c'est du quota.

**La page descend toute seule** sur la génération qui démarre. Dès que tu fais défiler
toi-même, elle te laisse la main et un bouton « Suivre la génération ↓ » apparaît pour y
revenir quand tu veux. La page se met à jour en continu sans jamais se recharger : une
vidéo que tu es en train de regarder ne repart pas de zéro.

**Récupérer les vidéos.** Le montage final se fait dans CapCut, pas ici. En bas de
l'Atelier : **« Télécharger toutes les vidéos (.zip) »**, et un lien par scène. Ce sont les
fichiers **bruts rendus par Grok, sans ré-encodage** — octet pour octet ce que Grok a
produit —, nommés `episode-scene-03-titre.mp4` pour arriver rangés dans CapCut.

Les onglets Montage et Export ont quitté la navigation ; les pages restent accessibles
depuis le bas de l'Atelier si tu en as besoin un jour.

### Générations (page avancée)

**Production complète — un seul bouton.** L'application enchaîne toute seule, dans l'ordre :

1. les planches de référence (personnages et objets) dans ChatGPT ;
2. pour chaque scène, son image de départ dans ChatGPT ;
3. puis sa vidéo dans Grok, à partir de cette image ;
4. la miniature, si le pack contient son prompt.

Une génération à la fois. La chaîne avance d'un élément **à chaque réussite** et s'arrête
d'elle-même dès qu'un élément n'aboutit pas : elle ne relance jamais rien. En mode assisté,
elle attend ton import et repart dès que tu l'as fait. Le clic sur « Lancer la production
complète » vaut validation des scènes complètes ; celles qui posent problème restent de
côté, avec leur motif.

Les compteurs affichent ce qui reste (planches, images, vidéos) et la prochaine étape.

À côté : Pause, Reprendre, Arrêter, Réessayer l'élément sélectionné, import manuel d'un
résultat, suivi de chaque élément (terminé / en cours / en attente / à vérifier), lancement
d'un lot ou d'un élément isolé, et journal complet.

### Montage et Export
Assemblage dans l'ordre du scénario, coupes, vitesse avec aperçu de la durée finale,
musique facultative, sous-titres facultatifs éditables et exportables en SRT,
export MP4 vertical 1080 × 1920 avec audio synchronisé, puis contrôle final.

**La bonne scène avec le bon temps.** L'adaptateur Grok **règle la durée dans l'interface**
(menu, boutons `6s / 10s / 15s`, ou panneau de réglages) et ne se contente pas de la
demander dans le texte du prompt ; il choisit aussi le format 9:16 s'il est proposé. Si le
contrôle reste introuvable, c'est écrit noir sur blanc dans la tâche — et une option
d'Admin permet même de **refuser d'envoyer** plutôt que de risquer une mauvaise durée.

Après téléchargement, la durée réelle est comparée à celle voulue. Au-delà de l'écart
toléré (1 s par défaut, réglable), la scène est marquée **« durée non conforme »** : la
vidéo est conservée — elle t'a coûté un quota — et un bouton **« Ramener à N s en coupant
la fin »** règle les coupes pour que l'export tombe juste, sans toucher au fichier
d'origine. Une vidéo plus courte que demandé n'est jamais « rallongée » : l'application te
le dit. Les écarts non rattrapés apparaissent dans le contrôle final.

**Export scène par scène.** Chaque scène peut aussi sortir seule en MP4, au même format que
l'épisode : coupes et vitesse appliquées, audio conservé, sous-titres de la scène
incrustables, musique au choix. Bouton par scène, bouton « Exporter toutes les scènes », et
archive ZIP de l'ensemble. Les fichiers portent un nom stable
(`export/scenes/scene-07_titre.mp4`) et se téléchargent depuis la page Montage.

---

## 5. Protection de tes quotas

C'est le cœur de l'application. Les règles sont appliquées dans `videroo/runner.py` et
garanties jusque dans le schéma SQLite (index uniques partiels).

* **Une seule génération automatique par élément validé.** Une deuxième demande
  automatique est refusée avec son motif, dès que la première a pu coûter quelque chose.
  La seule exception est un échec survenu **avant le moindre envoi** : la pièce jointe n'a
  pas pu être déposée, la zone de saisie ne s'est pas vidée. Rien n'est parti, rien n'a été
  consommé, et la chaîne a le droit de réessayer **le même élément** — trois fois au plus,
  puis elle s'arrête et te dit quoi réparer. C'est l'adaptateur lui-même qui note le moment
  exact où le prompt part (`taches.envoye`), pas une lecture du message d'erreur.
* **Une seule génération active à la fois.** Le worker ne prend rien tant qu'une tâche est
  `en_cours`.
* **Aucun clic automatique sur « Régénérer ».** Jamais, dans aucun adaptateur.
* **Un délai dépassé ne relance rien.** La tâche passe en « à vérifier » avec l'instruction
  d'aller regarder chez le fournisseur.
* **L'état est enregistré avant et après chaque lancement** (`ÉTAT AVANT` / `ÉTAT APRÈS`
  dans le journal, horodatages dans la table des tâches).
* **Après un plantage**, au démarrage suivant, l'application cherche d'abord si un résultat
  existe déjà sur le disque : si oui elle le rattache sans rien relancer, sinon la tâche
  passe en « à vérifier ».
* **Une régénération vient toujours d'un clic explicite** de ta part, avec confirmation.
  L'ancien fichier est conservé à côté, horodaté.
* **Rien ne part si l'élément n'est pas prêt** : scène non validée, prompt vide, planche de
  référence réellement absente, image de départ manquante, ou durée non prise en charge
  par Grok. Ces refus ont lieu **avant** le moindre envoi, donc sans consommer de quota.
* **La chaîne s'arrête sur l'élément qui coince, elle ne le saute pas.** Chaque
  fournisseur suit son propre ordre — ChatGPT fait les planches puis l'image de la scène 1,
  de la 2, de la 3… ; Grok anime la 1, puis la 2. Quand un élément demande ta décision
  (résultat « à vérifier », échec survenu après l'envoi, trois essais à blanc), la chaîne
  s'arrête **dessus** et l'écrit en clair, avec son motif. Elle ne passe pas à la scène
  suivante : sinon l'épisode se fabrique avec des trous et plus personne ne sait où on en
  est. Dès que l'élément est réglé — par « Récupérer le résultat », « Importer » ou
  « Réessayer » —, elle repart toute seule de là.
* **« Récupérer le résultat » ne rattache jamais un fichier que tu possèdes déjà.**
  Le contenu obtenu est comparé — empreinte SHA-256, pas le nom de fichier — à tous les
  médias déjà rattachés au projet. Si c'est la planche de référence ou la vidéo de la
  scène précédente, rien n'est rattaché, le fichier en trop est effacé (son original est
  intact) et le compte est noté sur la tâche. À partir de la première récupération à
  vide, l'application cesse de te réclamer une récupération et dit la vérité : **rien
  n'a été produit chez le fournisseur**, seule une régénération explicite de ta part
  peut produire cet élément. Ces vérifications ne coûtent aucun crédit.
* **Arrêter** un projet annule les tâches en attente et empêche les suivantes **sans
  effacer un seul résultat déjà obtenu**.
* L'application **n'affiche jamais un nombre de crédits restants** : les fournisseurs ne le
  publient pas de façon fiable.

### Anti-mélange entre projets
Chaque dossier de projet contient un `projet.json` portant son identifiant. Tout chemin
situé hors du dossier du projet attendu, ou portant l'empreinte d'un autre projet, est
refusé — à la génération comme à l'import. Les noms de fichiers sont stables :
`references/R1_articho.png`, `scenes/scene-07_image.png`, `scenes/scene-07_video.mp4`.

### Chroniques : les planches citées par le prompt vidéo partent vraiment
Un prompt vidéo de chronique écrit « @image2 is KENZA, @image3 is ILYES ». Grok numérote
les pièces jointes **dans l'ordre où on les dépose** : `@image1` est l'image de départ,
`@image2` et les suivantes sont les planches des personnages. L'application relit l'ordre
exact du prompt, joint les planches dans cet ordre, et **refuse d'envoyer** — avant le
moindre clic, donc sans quota — si l'une d'elles n'est pas sur le disque. Un repère comme
`@image3` ne remplace pas une image réellement jointe.

### Les références sont de vraies images
Avant de générer l'image d'une scène, l'application vérifie que **chaque planche requise
existe réellement sur le disque** et appartient bien au projet. Un identifiant comme `R1`
ne remplace jamais une image réellement jointe : si une planche manque, la génération est
refusée avec la liste précise de ce qu'il faut produire d'abord.

---

## 6. Les trois modes de génération

Réglable globalement dans **Réglages**, et par élément au moment du lancement.

| Mode | Ce qui se passe |
|---|---|
| **assisté** (recommandé au départ) | l'application ouvre la bonne page, écrit le prompt dans `imports/tache-XXXX_prompt.txt`, liste les fichiers à joindre et la durée à régler. **Elle ne lance aucune génération.** Tu lances, puis tu cliques « Importer le résultat ». |
| **auto** | l'application pilote le site : joint les vraies images, saisit le prompt, envoie **une fois**, attend le résultat réel, le télécharge et le vérifie (lisibilité, durée, présence d'audio). |
| **démo** | fabrique des fichiers factices **marqués SIMULATION**, placés dans `_simulation/`, renvoyés en « à vérifier », non rattachés aux scènes et absents de l'export. Sert à tester le pipeline sans toucher à tes quotas. Ce n'est jamais présenté comme une génération réussie. |

Si une connexion expire, qu'une vérification apparaît ou qu'un quota est atteint, le projet
est **mis en pause** avec une instruction claire, et tu reprends après ton intervention.

---

## 7. La méthode Grok

Ton problème : le chat Grok produit deux vidéos pour une scène, avec des voix différentes.
L'application applique ta méthode telle quelle.

1. Onglet **Générations** → renseigne **Série Grok** et **Épisode**.
2. Bouton **Copier le prompt de règles** : c'est ton prompt d'installation, conservé mot
   pour mot dans `videroo/ressources/prompt_regles_grok.txt`. En mode auto, l'application
   l'envoie elle-même, **une seule fois par chat**, puis coche « Règles installées ».
3. Première scène d'une série, le message envoyé est :
   `Nouvelle série "<nom>" :` + le prompt vidéo + **l'image de la scène en pièce jointe**.
   À partir de la partie 2, la série existe déjà : la première scène de la partie dit
   `"<nom>" - Épisode N :` à la place, jamais « Nouvelle série ».
4. Scènes suivantes : `Série "<nom>" — Épisode N — scène K :` + le prompt + l'image, avec
   le rappel « UNE SEULE génération (one-shot, voix + lèvres ensemble, Choix A) » et le
   chemin `Video/<serie>/Episode N/`.
5. L'application télécharge **une seule** vidéo. Si Grok en a produit plusieurs, elle te le
   dit et te laisse importer l'autre prise à la main — sans jamais relancer de génération.

### Séries : achever, ou passer à la partie suivante

En bas de l'**Atelier**, la section **Fin de la partie N** propose deux choix :

* **Achever : monter la vidéo finale** ouvre l'onglet Export de cet épisode ;
* **Passer à la partie N+1** prépare un nouveau projet « <nom> — Partie N+1 », **sans rien
  générer**, qui reprend de la partie N :
  * les **mêmes conversations ChatGPT et Grok** (tes personnages y ont déjà leur visage) ;
  * les règles Grok déjà installées dans ce chat (elles ne sont pas renvoyées) ;
  * le nom de série Grok, l'épisode N+1, le type d'histoire et le format d'export ;
  * les **planches** des personnages, copiées dans son propre dossier : effacer la
    partie 1 ne casse pas la 2, et quand tu colles le pack de la suite, chaque code déjà
    connu (R1, O2…) garde son image au lieu de coûter une nouvelle planche.

Tu arrives sur la page du nouveau projet : colle le pack de la partie N+1, valide, lance.
Cliquer deux fois ne crée pas deux « partie 2 » : le bouton devient « Ouvrir la partie 2 ».
Un compte qui a son chat dédié (ci-dessous) reste prioritaire, comme pour la partie 1.

### Le chat dédié de chaque compte

Le chat Grok ordinaire **ne fabrique pas de vidéo**. Interrogé, il répond poliment qu'« aucun
outil de génération Imagine n'est disponible dans les outils de cette session », coche une
liste de cases et ne produit aucun fichier. Chez toi, la vidéo sort de **la conversation du
projet « Video »** — c'est elle, et elle seule, qu'il faut viser.

Chaque compte Grok a donc sa propre adresse de chat, à coller dans **Admin → Mes comptes**,
sous le nom du compte. Un compte de secours a sa propre conversation : l'adresse se règle
compte par compte, pas une fois pour toutes.

Quand un compte a son chat dédié :

* l'application ouvre **exactement** cette conversation, et n'en crée jamais de nouvelle ;
* si elle n'y arrive pas (mauvais compte connecté, adresse d'un autre compte), elle
  **n'envoie rien** et te le dit — un prompt envoyé ailleurs produirait une vidéo hors série ;
* elle **ne renvoie pas** le prompt de règles : elles sont les tiennes, déjà posées là ;
* elle annonce `Nouvelle série "<nom>" :` dans **chaque conversation qui ignore encore cette
  série**. Une conversation ne sait rien de ce qui a été annoncé dans une autre, et un compte
  de secours ouvre forcément une autre conversation. Le `?rid=…` que Grok ajoute à chaque
  rechargement est ignoré dans la comparaison, sinon la série serait réannoncée à chaque scène.

Champ laissé vide : on retombe sur le comportement décrit juste en dessous.

#### La forme réelle de ces adresses

Relevé le 7 novembre 2026 dans tes trois comptes : la conversation du projet « Video » n'a
pas une adresse en `/c/…`, mais celle du mode Agent Imagine, où **l'identifiant est dans la
question de l'adresse** :

```
https://grok.com/imagine/agent/<agent>?conversation=<identifiant>
```

Ça change deux choses, et toutes les deux sont des pièges. Comparer les chemins seuls
prendrait deux conversations différentes du même agent pour une seule : la série ne serait
pas annoncée, et surtout le garde-fou « ce n'est pas mon chat » laisserait passer un prompt
envoyé au mauvais endroit. Et attendre un `/c/…` après l'envoi serait attendre pour rien.
`videroo/adresses.py` reconnaît donc les deux formes et compare les adresses en gardant
le `conversation=`, en n'ignorant que le `rid=` que Grok renouvelle à chaque rechargement.

Correspondance posée, vérifiée en ouvrant chaque conversation dans son profil :

| Compte dans Videroo | Profil Chrome | Compte Grok |
|---|---|---|
| Yacine 92i | `principal` | lukifofo |
| Yacine | `grok-yacine` | yacine |
| Dariya | `grok-dariya` | dariya |

#### Ce que la page Imagine a de particulier

* **Deux champs de fichier cachés** y coexistent : celui de la barre de saisie (« Joindre des
  fichiers ») et celui du panneau latéral (« Importer une image »). Prendre le second poserait
  l'image de départ sur le plan de travail au lieu de l'attacher au message : la scène
  s'animerait sans sa référence, sans que rien ne s'en plaigne. Le sélecteur écarte donc le
  panneau explicitement.
* **Aucun réglage de durée** dans cette interface : pas de bouton « Réglages » ni de menu de
  durée. La durée reste demandée dans le texte du prompt, et le journal le dit au lieu de
  laisser croire qu'elle a été réglée.
* **La conversation garde tous les plans passés** — 226 vidéos chez « Yacine », 177 chez
  « Dariya ». Deux conséquences, traitées toutes les deux :
  * le repère est posé **une fois la conversation chargée**, pas à l'ouverture : les anciennes
    vidéos arrivent par vagues, et un repère posé trop tôt ferait passer une vidéo ancienne
    pour celle de la scène ;
  * seule une vidéo **apparue après l'envoi** est enregistrée, et par son adresse directe.
    Le bouton de téléchargement n'est rattaché à aucune vidéo en particulier : il ne sert
    plus qu'aux pages où la vidéo n'a pas d'adresse utilisable.

  Dans les deux cas, le risque évité est le même : rendre un plan d'une autre scène **en
  l'annonçant comme réussi**. Un faux succès est le pire des résultats, parce qu'il ne se
  signale pas.

### Les règles vivent dans un chat, donc dans un compte

« Règles installées » n'est pas une case qu'on coche et qu'on oublie. Pour que l'application
les considère comme présentes, il lui faut les trois à la fois : la case, **l'adresse de la
conversation** (`grok.com/c/…`), et **le profil Chrome** où cette conversation a été ouverte.

* Sans adresse retenue, on retombe sur un chat neuf : les règles n'y sont pas.
* Avec un autre compte Grok, la conversation du premier compte n'existe pas dans cette
  session : les règles n'y sont pas non plus.

Dans ces deux cas l'application **réinstalle les règles** et le dit dans le journal. C'est un
message texte, **aucune vidéo, donc aucun quota vidéo consommé**. L'adresse n'est retenue que
lorsque Grok l'a réellement posée : juste après l'envoi elle vaut encore `grok.com/`, et la
garder ainsi ferait croire à un chat dédié qui n'existe pas.

### Le ménage qui tuait le travail en cours

Le 7 novembre 2026 à 06 h 25, la chaîne de production a fermé « 2 fenêtres Chrome oubliées »
avant de démarrer. L'une n'était pas oubliée du tout : elle attendait une image chez ChatGPT,
demandée onze minutes plus tôt. La suite était écrite d'avance :

```
06:24:14  prompt envoyé à ChatGPT, attente de l'image
06:25:37  2 fenêtre(s) Chrome oubliée(s) fermée(s) avant de lancer   ← SIGKILL
06:31:15  Aucune image recuperee après 420 s                        ← contre une page morte
06:32:33  Navigateur dédié indisponible … Installe le navigateur    ← et ça recommence
```

Trois fautes en une, réparées séparément :

1. **Le ménage ne distinguait pas « oubliée » de « en cours d'utilisation ».** Il épargne
   maintenant les profils que l'application pilote à cet instant — y compris au second
   passage, qui reprenait d'un `SIGKILL` ce que la protection venait d'accorder. Attention au
   piège : le profil `principal` vit dans le dossier `profil_navigateur`, et comparer les noms
   de profil aux noms de dossier ne protégeait jamais le principal, justement celui qui
   travaillait.
2. **Un contexte fermé de l'extérieur restait en mémoire pour toujours.** Toutes les scènes
   suivantes échouaient sur un objet fantôme. La fenêtre est maintenant rouverte une fois, en
   le disant au journal — rouvrir une fenêtre ne relance aucune génération.
3. **Le message accusait l'installation.** Playwright tournait depuis des heures. Un
   navigateur fermé et un navigateur absent produisent la même panne à l'écran, mais pas la
   même réparation : `raison_navigateur()` regarde avant d'accuser.

### Le champ de fichier de ChatGPT n'en prend qu'un à la fois

La miniature est la seule étape qui joint **plusieurs** images d'un coup : les quatre planches
de référence, pour que les personnages soient identiques. Les scènes n'en joignent qu'une.
C'est pourquoi la panne n'est apparue qu'à la dernière étape de l'épisode, le 7 novembre 2026
à 14 h 58 :

```
Non-multiple file input can only accept single file
```

Le champ de ChatGPT ne porte pas l'attribut `multiple`. L'application le **demande au DOM**
plutôt que de le deviner, et dépose les fichiers un par un quand c'est nécessaire : chaque
dépôt déclenche son propre envoi, et le site les empile dans la zone de saisie. Déposer un
fichier n'envoie aucun prompt — l'échec n'avait d'ailleurs rien coûté, le journal le dit :
« Aucun prompt envoyé, aucun quota consommé. »

### Le titre arrivait avec des guillemets doublés

Le pack V6 livre le titre guillemets compris — `« IL A TOUT DONNÉ À SA PETITE SŒUR »` — et le
modèle de miniature ajoute les siens. Le prompt demandait donc d'écrire
`« « IL A TOUT DONNÉ À SA PETITE SŒUR » »`, et comme il précise « écris le titre exactement
ainsi », ChatGPT les aurait dessinés dans l'image. Le titre est désormais déballé de ses
guillemets avant d'entrer dans le modèle.

### Chez ChatGPT, compter les images ne veut rien dire

Deux fois le 7 novembre 2026, l'application a attendu 420 s l'image de la scène 3, l'a
déclarée introuvable — et « Récupérer le résultat » l'a ramenée en neuf secondes, la bonne,
sans rien générer. L'image était là depuis le début.

La sonde en lecture seule a montré pourquoi, et ce n'était pas ce qu'on croyait :

```
[data-testid='generated-image-gallery'] img   n=4
main img[src^='blob:']                        n=9
data-message-author-role                      n=0     ← aucun fil de messages
hauteur de page                               900 px  ← la hauteur de la fenêtre
```

Cette conversation n'est pas un fil de discussion : c'est une **galerie** qui ne garde montées
que les quatre dernières images, la plus récente au bout. D'où deux pièges, qui ont fait
tomber les deux garde-fous d'un coup :

* **Le compte plafonne.** Le journal annonce « 5 image(s) déjà dans la conversation » à
  13 h 56, puis « 4 image(s) » à 14 h 04, sans que rien n'ait été supprimé : quand une image
  neuve arrive, une ancienne sort. Exiger « plus d'images qu'avant » était donc une condition
  qui **ne pouvait pas devenir vraie**.
* **Les adresses `blob:` sont refabriquées à chaque rendu.** Une image qui revient à l'écran
  change d'adresse sans avoir changé d'image : « adresse inconnue » ne prouve rien non plus.

Ce qui ne ment pas, c'est l'image elle-même. L'application relève l'**empreinte du contenu**
des images présentes **avant d'envoyer** — joindre un fichier et envoyer un prompt font
re-rendre la galerie, et une adresse `blob:` périmée ne se lit plus. Elle ne retient ensuite
la dernière image que si son contenu ne figure pas dans ce repère, et qu'il ne bouge plus
d'une lecture à l'autre. Une même adresse n'est relue qu'une fois : relire 2 Mo toutes les
1,2 s pendant sept minutes encombrerait le navigateur qu'on surveille.

Et si aucune empreinte de départ n'a pu être relevée, l'application **s'arrête et le dit**
plutôt que de deviner : sans repère, la dernière image de la galerie ressemble trait pour
trait à une image neuve, et la rattacher à la scène serait une erreur silencieuse. Lire une
image déjà affichée ne renvoie rien à ChatGPT et ne coûte aucun quota.

### Mourir prend du temps : la seconde fausse réparation

Le même jour à 13 h 55, la panne est revenue sous un autre visage. Le ménage a bien épargné
ce qu'il fallait, mais il restait une fenêtre vraiment oubliée, et il l'a fermée :

```
13:55:36  1 fenêtre(s) Chrome oubliée(s) fermée(s) avant de lancer
13:55:38  ⏱ page ChatGPT prête à 0 s                      ← la page morte répond encore
13:55:41  Impossible de joindre les images de référence   ← échec de la scène 5
13:55:55  Le navigateur … avait été fermé : il est rouvert ← la scène 3 passe, elle
```

Deux secondes ne suffisent pas à tuer un Chrome. Entre le `SIGTERM` et la mort réelle, le
navigateur répond encore à certaines demandes, et c'est ce qui rend la panne si trompeuse :

1. **`is_closed()` est un drapeau local**, pas une question posée au navigateur. Il répondait
   « ouverte » d'une page dont le Chrome agonisait, donc la page était rendue telle quelle,
   « prête à 0 s », et ne tombait sur le cadavre qu'au premier vrai appel — trop tard pour la
   réouverture, qui ne gardait que la création d'une page neuve. L'application fait maintenant
   un **aller-retour réel** (`page.evaluate("1")`) avant de rendre une page gardée en mémoire,
   et la réouverture couvre toute l'obtention de la page, pas seulement sa création.
2. **On rouvrait un profil que son Chrome tenait encore.** Un délai fixe de deux secondes ne
   sait pas si le ménage est fini ; regarder, si. L'ouverture attend désormais que plus aucun
   processus ne tienne le dossier, et le dit au journal si un Chrome s'entête.
3. **Le journal ne nommait personne.** « 1 fenêtre fermée » ne dit pas laquelle, et retrouver
   le coupable a demandé de relire toute la soirée. La ligne nomme maintenant les profils
   fermés **et** les profils épargnés.

Aucune de ces trois réparations n'envoie quoi que ce soit : rouvrir une fenêtre, attendre un
processus et écrire au journal ne coûtent pas un crédit.

### Un menu ouvert bloque toute la page

Les menus de Grok coupent les clics du **reste** de la page tant qu'ils sont ouverts. La zone
de saisie reste parfaitement visible, mais plus rien ne l'atteint. C'est ce qui a fait échouer
la scène 1 du 7 novembre 2026 sur un message trompeur — « zone de saisie introuvable » — alors
que la zone était là : la recherche du réglage de durée avait ouvert un menu de la barre
latérale et l'avait laissé ouvert.

Trois garde-fous depuis :

* les sélecteurs de réglage ne visent plus que **la barre de saisie**, jamais la barre latérale ;
* avant d'écrire, l'application referme ce qui traîne ouvert, par **Échap** — qui ne valide
  rien, n'accepte rien et n'envoie rien, et n'est pressé que si une surcouche est bien ouverte ;
* en cas d'échec, le message dit **ce qui s'est réellement passé** : une zone recouverte n'est
  pas une zone absente, et l'annoncer ainsi enverrait réparer un sélecteur qui marche.

---

## 8. Montage et export

* Assemblage dans l'ordre du scénario, dialogues des clips conservés.
* Coupes de début et de fin réglables scène par scène.
* Export vertical **1080 × 1920** (modifiable), H.264 + AAC, `faststart`.
* Vitesse réglable, avec **aperçu de la durée finale** avant d'encoder.
* **Avertissement** si tes réglages rendent l'épisode plus court que ta durée souhaitée.
* Musique facultative avec réglage du volume, mixée sans écraser les dialogues.
* Sous-titres facultatifs : timings proposés depuis les dialogues du pack, éditables ligne
  par ligne, export SRT, import SRT, incrustation optionnelle dans l'image.
* Un clip sans piste audio est signalé puis comblé par du silence (le reste reste synchrone).
* **Un seul encodage FFmpeg** pour tout l'épisode : coupes, format, vitesse, musique et
  sous-titres sont appliqués en une passe, sans conversion intermédiaire.
* Un agrandissement de résolution ou une montée de cadence sont **signalés comme tels** :
  ils ne créent aucun détail et n'améliorent pas la fluidité réelle.
* **Chaque scène exportable seule** en MP4, avec le même traitement que l'épisode, plus une
  archive ZIP de toutes les scènes.

Le contrôle final affiche la vidéo assemblée, sa durée, les scènes utilisées, la miniature
et la liste des fichiers manquants ou des problèmes détectés.

L'application **ne modifie jamais ton scénario validé** et **ne publie rien** : aucune
diffusion TikTok, aucun envoi automatique.

---

## 9. Fonctions complémentaires

* **Prompts image en série**, **prompts vidéo en série**, **prompts de planches** : tous les
  prompts à la suite, prêts à copier, avec la liste des fichiers à joindre. Rien n'est
  envoyé depuis ces pages.
* **Extraction d'images clés** d'une vidéo de scène avec FFmpeg.
* **Bibliothèque de vidéos de référence** : liens, notes de structure narrative, indicateurs
  publics que tu relèves toi-même (l'application ne les récupère pas automatiquement).
* **Transcription locale** : l'application extrait l'audio de l'export en WAV 16 kHz mono et
  détecte un moteur local s'il est installé. Pour cette version, le SRT est importé ou
  corrigé à la main.
* **Assistant d'histoires** : la marche à suivre avec ton V6 d'origine (options 1, 2, 3),
  qui attend ton choix avant de produire le pack.

---

## 10. Où sont tes fichiers

```
donnees/
├── videroo.db                  base SQLite (projets, scènes, tâches, journal)
├── metaprompt_v6.md            ton métaprompt, conservé tel quel
├── selecteurs.json             surcharge des sélecteurs (créé si tu en enregistres)
├── profil_navigateur/          profil du compte « principal »
├── profils/                    un dossier par compte supplémentaire (grok-dariya…)
├── journaux/videroo.log        journal texte
└── projets/0001-<nom>/
    ├── projet.json             empreinte anti-mélange
    ├── pack_importe.md         le pack tel que tu l'as collé
    ├── references/             R1_*.png, R2_*.png…
    ├── scenes/                 scene-01_image.png, scene-01_video.mp4…
    ├── imports/                prompts préparés en mode assisté
    ├── miniature/              miniature.png
    ├── musique/                musique importée
    ├── export/                 episode.mp4, episode.srt, episode-scenes.zip
    │   └── scenes/             scene-01_titre.mp4, scene-02_titre.mp4…
    └── _simulation/            fichiers de démonstration (jamais exportés)
```

Déplacer le dossier de données : `VIDEROO_DONNEES=/un/autre/chemin ./lancer.sh`

---

## 11. L'espace Admin

Tout se règle depuis **Admin**, sans toucher au code :

* **Mes comptes** — état de connexion de ChatGPT et de Grok, bouton pour ouvrir chaque
  site dans le navigateur dédié et s'y connecter, bouton pour vérifier. La date de la
  dernière vérification est affichée.
* **Navigateur dédié** — ouvrir, fermer, voir le profil utilisé et les pages ouvertes, et
  **déconnecter tous les comptes** en effaçant le profil local (confirmation obligatoire ;
  aucun projet ni média n'est touché).
* **Réglages** — mode par défaut, délais d'attente image et vidéo, durées acceptées par
  Grok, adresses de ChatGPT et de Grok, affichage de la fenêtre, format d'export, qualité
  H.264, vitesse d'encodage, durées souhaitées, volume de la musique. Chaque champ affiche
  sa valeur par défaut, et une valeur invalide est refusée avec son motif.
* **Sélecteurs des sites** — voir la section suivante.
* **Machine** et **Journal général**.

Un seul réglage reste volontairement figé : **une génération à la fois**. C'est ce qui
protège tes quotas.

**Un seul thread pilote le navigateur.** Playwright en mode synchrone refuse qu'un objet
créé dans un thread soit utilisé depuis un autre ; or les routes web et le worker tournent
dans des threads différents. Toutes les opérations passent donc par un thread dédié, et une
vérification de compte demandée pendant une génération est refusée proprement plutôt que de
planter.

---

### Un pack écrit dans un format jamais vu

Les packs peuvent changer de forme d'un épisode à l'autre. Videroo s'adapte sans
modification du code :

- les scènes sont repérées par leur numérotation (« Scène 1 », « PLAN 2 »,
  « Séquence 3 »…), même si aucun lecteur ne reconnaît le reste ;
- si une scène reste sans prompt image, prompt vidéo ou durée, l'onglet
  **Histoire** affiche « Apprends-moi ce nouveau format » : chaque étiquette
  inconnue y est listée avec un extrait. Tu choisis ce qu'elle est (prompt
  image, prompt vidéo, durée, références, inventaire, ce qui change, ou à
  ignorer), et le pack est relu aussitôt, médias conservés ;
- le choix est retenu pour tous les packs suivants, tous types d'histoire
  confondus. La liste est dans **Admin > Étiquettes de pack apprises**, avec un
  bouton « Oublier » en cas d'erreur.

Rien n'est reformulé : un champ n'est rempli qu'avec le texte du pack, recopié
tel quel, et seulement s'il était vide.

### Utiliser Videroo depuis ton téléphone (Tailscale)

L'interface s'adapte aux petits écrans. Videroo reste à l'écoute de
`127.0.0.1` uniquement : il pilote tes comptes ChatGPT et Grok, il ne doit
jamais être ouvert à ton Wi-Fi ni à Internet. Tailscale crée un réseau privé
chiffré entre tes seuls appareils, et `tailscale serve` y publie Videroo sans
rien changer à son adresse locale.

Une seule fois, sur le PC :

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale serve --bg 8077
```

Sur le téléphone : installe l'app Tailscale, connecte-toi avec le même compte,
puis ouvre l'adresse affichée par `tailscale serve status`
(du type `https://<nom-du-pc>.<ton-reseau>.ts.net`). Elle n'est joignable que
depuis tes appareils connectés à Tailscale. Pour couper l'accès :
`sudo tailscale serve reset`.

## 12. Quand ChatGPT ou Grok changent leur interface

Les sélecteurs sont isolés dans `videroo/adapters/selecteurs.py` et surchargeables depuis
**Réglages → Sélecteurs des sites** (enregistrés dans `donnees/selecteurs.json`, sans
toucher au code). Chaque entrée est une **liste essayée dans l'ordre** jusqu'à ce qu'un
sélecteur fonctionne. Si un sélecteur manque, l'adaptateur s'arrête **avant** d'envoyer
quoi que ce soit et te dit lequel réparer.

Les adaptateurs sont séparés : `adapters/chatgpt.py`, `adapters/grok.py`,
`adapters/assiste.py`, `adapters/demo.py`.

---

## 13. Vérifications locales

```bash
./verifier.sh
```

447 tests, sans consommer un seul quota IA. Ils couvrent :

* **le parseur** — pack V6 réel (3 planches, 10 scènes, 77 s), distinction métaprompt / pack,
  champs manquants signalés sans invention, références inconnues, import JSON, blocs code ;
* **la persistance** — création, import, édition, validation, conservation des médias au
  réimport, refus des fichiers illisibles et des extensions inattendues, disparition de
  l'avertissement « aucun prompt de planche » dès que tu écris le prompt manquant ;
* **la prévention des doubles générations** — une seule génération automatique, une seule
  tâche active, pause, arrêt, refus avant dépense, anti-mélange entre projets, reprise après
  plantage, réussite annoncée sans fichier, délai dépassé ;
* **l'export vidéo** — assemblage FFmpeg réel, coupes, vitesse, silence comblé, musique,
  sous-titres incrustés, aller-retour SRT, contrôle final ;
* **la chaîne de production** — ordre planches → image → vidéo, prérequis respectés, une
  seule tâche à la fois, arrêt propre sur un élément non abouti, pause, arrêt, reprise par
  import manuel ;
* **l'espace admin** — persistance et validation des réglages, refus des valeurs
  invalides, état des comptes, et un seul thread pour le navigateur ;
* **l'export scène par scène** — MP4 vertical lisible, coupes et vitesse appliquées,
  sous-titres incrustés, archive ZIP, boutons réellement présents dans les pages ;
* **les durées** — conformité de la vidéo obtenue, rattrapage par les coupes, réglage de la
  durée dans l'interface Grok, et détection de connexion qui ne se laisse pas tromper par
  un « Sign in » résiduel, et qui répond « à confirmer » quand la page est ambiguë ;
* **les deux modes d'accès au navigateur** — profil dédié ou branchement sur un Chrome
  existant, avec un message qui explique quoi faire quand le branchement échoue ;
* **le multi-comptes** — un profil par compte, bascule sur quota sans jamais relancer une
  génération déjà envoyée, mise en pause quand tous les comptes sont à court ;
* **les horaires de recharge** — réinitialisation hebdomadaire par compte, disponibilité
  calculée à la minute près, et ordre d'utilisation qui place en tête le quota le plus
  proche de sa recharge ;
* **l'isolation des profils** — détection d'un profil partagé, séparation en un clic avec
  ou sans copie de session, et refus d'effacer un profil utilisé par plusieurs comptes ;
* **les fenêtres restées ouvertes** — repérées par leur dossier de profil, et fermées sans
  jamais toucher au Chrome personnel ;
* **les trois modes d'affichage** — arguments de lancement vérifiés pour chacun, et repli
  de « sans fenêtre » vers « hors écran » ;
* **la conversation par histoire** — mémorisée une fois, jamais écrasée, distincte d'une
  histoire à l'autre, et transmise à l'adaptateur ;
* **l'envoi vérifié** — confirmation par le vidage de la zone de saisie, repli sur la
  touche Entrée, et échec franc quand rien ne part ;
* **la suppression d'un projet** — refusée sans le titre exact recopié, complète
  quand il est bon (base et dossier), et sans effet sur les autres projets ;
* **les chroniques** — titre d'épisode suivi d'un sous-titre, ordre des planches du
  prompt vidéo conservé jusqu'à l'envoi à Grok ;
* **la récupération qui ne ramène rien de neuf** — la planche prise pour l'image d'une
  scène et la vidéo de la scène précédente prise pour la suivante sont reconnues par leur
  contenu, le fichier en trop est effacé sans toucher à l'original, et la chaîne cesse de
  réclamer une récupération dont on sait déjà qu'elle ne produira rien ;
* **la rapidité de recherche** — un sélecteur obsolète ne coûte plus son délai complet ;
* **le navigateur qui meurt sous les pieds** — une page gardée en mémoire est prouvée
  vivante par un aller-retour réel avant d'être rendue, le ménage nomme ce qu'il ferme et
  ce qu'il épargne, et l'ouverture attend que le profil soit vraiment rendu ;
* **la détection d'image chez ChatGPT** — galerie qui plafonne, adresses `blob:`
  refabriquées, image à moitié arrivée, et une seule lecture par adresse ;
* **les pièces jointes** — plusieurs fichiers déposés un par un quand le champ n'accepte pas
  `multiple`, d'un seul coup quand il l'accepte, et un titre qui ne double pas ses guillemets ;
* **la clôture de l'épisode** — miniature téléchargeable sous un nom lisible et refusée avec
  son motif quand elle n'existe pas, bouton « Tout est validé » fermé tant qu'il manque
  quelque chose, et page de retour prise dans une liste fermée ;
* **la récupération qui rend la planche** — un fichier identique à un média déjà rattaché
  n'est jamais pris pour un résultat neuf : il est mis de côté, nommé, et rien n'est
  relancé ; récupérer une planche pour elle-même reste possible ;
* **les trois types d'histoires** — choix à la création, un métaprompt par type sans
  écraser les autres, lecteur de chronique (planches, durées, personnage qui arrive en
  cours de scène, type de scène qui n'est pas pris pour un prénom), lecteur bébé (planches
  des bébés et des objets, durées, dialogues, bouton « Copier » de ChatGPT et titres de
  section qui ne finissent pas dans un prompt, référence citée sans planche signalée),
  titre de miniature venu du pack, et pack collé dans le mauvais type lu avec une remarque ;
* **la reprise après un échec** — un échec qui s'est arrêté avant le moindre envoi est
  retenté sur le même élément puis abandonné au bout de trois essais à blanc, un échec
  survenu après l'envoi n'est jamais relancé tout seul, la chaîne s'arrête sur l'élément
  qui demande ta décision au lieu de fabriquer la scène suivante, et le motif est
  affichable, pas seulement écrit dans le journal ; le message de lancement nomme
  l'élément qui bloque au lieu de dire « rien à produire », et le bandeau d'arrêt vit
  dans l'en-tête collant, donc il reste visible quand tu descends voir les scènes.

---

## 14. Ce qui demande ton intervention

* **Connexion** à ChatGPT et à Grok, à la main, une fois, depuis **Admin → Mes comptes**.
* **Les sélecteurs du mode auto ont tenu sur les vrais sites, le 7 novembre 2026** :
  l'épisode « IL A TOUT DONNÉ À SA PETITE SŒUR » est sorti entier en mode auto — 4 planches
  et 10 images chez ChatGPT, 10 vidéos chez Grok, plus la miniature, 25 générations en tout,
  la plupart enchaînées sans intervention. Ce n'est pas une garantie : ChatGPT et Grok
  changent leur interface quand ils veulent, et un sélecteur qui marchait ce jour-là peut
  tomber demain. Quand ça arrive, le mode auto s'arrête **avant d'envoyer** et te nomme le
  sélecteur à réparer ; le **mode assisté**, qui ne dépend d'aucun sélecteur, reste là comme
  repli.
* **Transcription locale automatique** : non incluse. L'audio est extrait, le SRT est importé
  ou corrigé à la main.
* **Publication** : jamais automatique, par choix.
