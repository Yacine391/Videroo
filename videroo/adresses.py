"""Adresses de conversation : les reconnaitre, et les comparer.

Une adresse de conversation decide de tout le reste : si le prompt part dans TA
conversation ou dans un chat neuf, et si la serie a deja ete annoncee la ou on
ecrit. Grok en a deux formes, et elles ne se ressemblent pas :

    https://grok.com/c/<identifiant>
    https://grok.com/imagine/agent/<agent>?conversation=<identifiant>

Dans la seconde, ce qui identifie la conversation est dans la QUESTION de
l'adresse, pas dans son chemin. Comparer les chemins seuls y confondrait deux
conversations differentes du meme agent, et un prompt partirait au mauvais
endroit sans que rien ne s'en plaigne.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

# Grok rajoute « ?rid=… » a chaque rechargement de la MEME page. C'est le seul
# parametre dont on sait qu'il ne designe rien. Tout le reste est garde : prendre
# deux conversations pour une seule enverrait un prompt au mauvais endroit, alors
# que les distinguer a tort ne coute qu'une annonce de serie repetee une fois.
PARAMETRES_VOLATILS = {"rid"}

# Le parametre qui porte l'identifiant dans les adresses « /imagine/agent/… ».
PARAMETRE_CONVERSATION = "conversation"


def _cle(adresse: str) -> tuple:
    decoupe = urlsplit((adresse or "").strip())
    parametres = tuple(
        sorted(
            (nom, valeur)
            for nom, valeur in parse_qsl(decoupe.query, keep_blank_values=True)
            if nom not in PARAMETRES_VOLATILS
        )
    )
    return decoupe.netloc.lower(), decoupe.path.rstrip("/"), parametres


def meme_conversation(a: str, b: str) -> bool:
    """Deux adresses designent-elles la meme conversation ?

    Une adresse vide ne designe rien : elle n'est jamais egale a quoi que ce
    soit, sinon un projet sans serie annoncee passerait pour deja annonce.
    """
    return bool((a or "").strip()) and _cle(a) == _cle(b)


def est_une_conversation(adresse: str) -> bool:
    """L'adresse nomme-t-elle une conversation precise ?

    « https://grok.com/ » ouvre une conversation differente a chaque fois : la
    retenir comme chat dedie reviendrait a n'en avoir aucun, et les regles
    seraient comptees comme installees dans un chat introuvable. On exige donc
    une adresse qui nomme la conversation, sous l'une des deux formes connues.
    """
    decoupe = urlsplit((adresse or "").strip())
    chemin = decoupe.path
    if "/c/" in chemin and chemin.split("/c/", 1)[1].strip("/"):
        return True
    for nom, valeur in parse_qsl(decoupe.query, keep_blank_values=True):
        if nom == PARAMETRE_CONVERSATION and valeur.strip():
            return True
    return False
