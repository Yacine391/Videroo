// Glisser-déposer d'une image sur SA case (planche d'un personnage, miniature).
// La case porte data-depot="planche:<id>" ou "miniature:0" et data-projet.
// Rien n'est généré : l'image est importée telle quelle, comme « Importer ».
(function () {
  function caseDe(cible) { return cible && cible.closest ? cible.closest('[data-depot]') : null; }

  function annoncer(zone, texte, bon) {
    let ligne = zone.querySelector('.depot-etat');
    if (!ligne) {
      ligne = document.createElement('p');
      ligne.className = 'depot-etat mono';
      zone.appendChild(ligne);
    }
    ligne.textContent = texte;
    ligne.classList.toggle('ko', !bon);
  }

  async function envoyer(zone, fichier) {
    if (!fichier || !/^image\//.test(fichier.type)) {
      annoncer(zone, 'Ce n’est pas une image (.png .jpg .webp).', false);
      return;
    }
    const donnees = new FormData();
    donnees.append('fichier', fichier, fichier.name);
    donnees.append('cible', zone.dataset.depot);
    annoncer(zone, 'Import de ' + fichier.name + '…', true);
    try {
      const reponse = await fetch('/projets/' + zone.dataset.projet + '/deposer', {
        method: 'POST', body: donnees
      });
      const bilan = await reponse.json();
      annoncer(zone, bilan.message || (bilan.ok ? 'Image rangée.' : 'Échec.'), !!bilan.ok);
      if (bilan.ok) {
        if (typeof window.rafraichir === 'function') { window.rafraichir(); }
        else { setTimeout(function () { location.reload(); }, 600); }
      }
    } catch (e) {
      annoncer(zone, 'Envoi impossible : ' + e, false);
    }
  }

  document.addEventListener('dragover', function (ev) {
    const zone = caseDe(ev.target);
    if (!zone) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'copy';
    zone.classList.add('depot-survol');
  });
  document.addEventListener('dragleave', function (ev) {
    const zone = caseDe(ev.target);
    if (zone && !zone.contains(ev.relatedTarget)) zone.classList.remove('depot-survol');
  });
  document.addEventListener('drop', function (ev) {
    const zone = caseDe(ev.target);
    if (!zone) return;
    ev.preventDefault();
    zone.classList.remove('depot-survol');
    const fichiers = ev.dataTransfer && ev.dataTransfer.files;
    if (fichiers && fichiers.length) envoyer(zone, fichiers[0]);
  });
  // Une image lâchée à côté d'une case ne doit pas ouvrir l'image dans l'onglet.
  window.addEventListener('dragover', function (ev) { ev.preventDefault(); });
  window.addEventListener('drop', function (ev) { if (!caseDe(ev.target)) ev.preventDefault(); });
})();
