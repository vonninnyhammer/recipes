/* Recipe section filtering: ingredient match-ALL + category chips. */
(function () {
  var input = document.querySelector('input[name="ing"]');
  var chipsHost = document.getElementById('chips');
  var list = document.getElementById('list');
  var count = document.getElementById('count');
  var empty = document.getElementById('empty');
  var cards = Array.prototype.slice.call(list.querySelectorAll('.card'));
  var activeCats = {};

  chipsHost.addEventListener('click', function (e) {
    var c = e.target.closest('.chip');
    if (!c) return;
    var k = c.dataset.cat;
    if (activeCats[k]) delete activeCats[k]; else activeCats[k] = true;
    c.classList.toggle('on', !!activeCats[k]);
    apply();
  });

  function tokens(s) {
    return (s || '').toLowerCase().split(/[\s,]+/).map(function (t) {
      return t.trim().replace(/\.$/, '');
    }).filter(function (t) { return t.length >= 2; });
  }

  function apply() {
    var want = tokens(input.value);
    var catKeys = Object.keys(activeCats);
    var shown = 0;
    cards.forEach(function (card) {
      var ok = true;
      if (catKeys.length) {
        var cats = (card.dataset.cats || '').split(',');
        ok = catKeys.some(function (k) { return cats.indexOf(k) !== -1; });
      }
      if (ok && want.length) {
        var hay = (card.dataset.ing || '').toLowerCase() + ' ' +
                  (card.dataset.cats || '').toLowerCase() + ' ' +
                  (card.dataset.tags || '').toLowerCase() + ' ' +
                  card.dataset.title.toLowerCase();
        ok = want.every(function (t) { return hay.indexOf(t) !== -1; });
      }
      card.style.display = ok ? '' : 'none';
      if (ok) shown++;
    });
    count.textContent = shown + ' of ' + cards.length + ' recipe(s)';
    empty.style.display = shown ? 'none' : 'block';
  }

  if (input) input.addEventListener('input', apply);
  apply();
})();
