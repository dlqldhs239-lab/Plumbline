/* The review form, for a judge with thirty projects ahead.
   Without this script the form still works: choose, Save draft or Submit.
   With it:
   - a digit scores the focused row and moves to the next one;
   - the weighted score is shown as it changes;
   - every change is saved as a draft through the API, so a closed tab
     loses nothing. Submitting is never done for the judge. */
(function () {
  'use strict';
  var form = document.getElementById('scoreform');
  if (!form) return;
  var rows = Array.prototype.slice.call(form.querySelectorAll('.score-row'));
  var groups = rows.map(function (r) { return r.querySelector('.scale'); });
  var comment = document.getElementById('comment');
  var saved = document.getElementById('saved');
  var own = document.getElementById('own');
  var status = document.getElementById('status');
  var url = form.getAttribute('data-save');
  var min = parseInt(form.getAttribute('data-min'), 10);
  var max = parseInt(form.getAttribute('data-max'), 10);
  var token = (form.querySelector('input[name=csrfmiddlewaretoken]') || {}).value;
  var timer = 0, pending = false, leaving = false;

  function chosen(group) { return group.querySelector('input:checked'); }

  function focusRow(i, quietly) {
    var target = i >= groups.length ? comment : (chosen(groups[i]) || groups[i].querySelector('input'));
    if (target) target.focus({ preventScroll: !!quietly });
  }

  function weighted() {
    var sum = 0, weight = 0;
    rows.forEach(function (row, i) {
      var c = chosen(groups[i]);
      var w = parseFloat(row.getAttribute('data-weight')) || 0;
      if (c) { sum += parseInt(c.value, 10) * w; weight += w; }
    });
    if (own) own.textContent = weight ? (sum / weight).toFixed(2) : 'n/a';
  }

  function say(text, tone) {
    if (!saved) return;
    saved.textContent = text;
    saved.className = 'label' + (tone ? ' ' + tone : '');
  }

  function payload() {
    var scores = {};
    groups.forEach(function (g) {
      var c = chosen(g);
      if (c) scores[c.name.replace(/^score_/, '')] = parseInt(c.value, 10);
    });
    return { scores: scores, comment: comment ? comment.value : '', submit: false };
  }

  function save() {
    if (!url || !window.fetch || leaving) return;
    pending = false;
    say('Saving');
    fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
      body: JSON.stringify(payload())
    }).then(function (r) {
      if (!r.ok) return r.json().then(function (b) { throw new Error(b.detail || 'not saved'); });
      var now = new Date();
      say('Draft saved ' + ('0' + now.getHours()).slice(-2) + ':' + ('0' + now.getMinutes()).slice(-2) + ':' + ('0' + now.getSeconds()).slice(-2));
      if (status && /not started/i.test(status.textContent)) { status.textContent = 'In progress'; status.className = 'badge warn'; }
    }).catch(function (e) {
      pending = true;
      say('Not saved: ' + e.message + '. Use Save draft.', 'bad');
    });
  }

  function changed() {
    weighted();
    pending = true;
    say('Unsaved');
    clearTimeout(timer);
    timer = setTimeout(save, 700);
  }

  groups.forEach(function (group, i) {
    group.addEventListener('keydown', function (e) {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      var v = parseInt(e.key, 10);
      if (isNaN(v) || v < min || v > max) return;
      var input = group.querySelector('input[value="' + v + '"]');
      if (!input || input.disabled) return;
      e.preventDefault();
      input.checked = true;
      changed();
      focusRow(i + 1);
    });
    group.addEventListener('change', changed);
  });
  if (comment) comment.addEventListener('input', changed);

  form.addEventListener('submit', function () { leaving = true; clearTimeout(timer); });
  window.addEventListener('beforeunload', function (e) {
    if (pending && !leaving) { e.preventDefault(); e.returnValue = ''; }
  });

  weighted();
  // Start where the work is: the first row without a score. Only where the
  // form is beside the project, and never by moving the page.
  if (!document.querySelector('.messages .error') && window.matchMedia('(min-width: 1101px)').matches) {
    for (var i = 0; i < groups.length; i++) { if (!chosen(groups[i])) { focusRow(i, true); break; } }
  }
})();
