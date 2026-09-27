/* Pairwise comparison by keyboard: left arrow, right arrow, down for
   "cannot say". The form works without this script. */
(function () {
  'use strict';
  var form = document.getElementById('versus');
  if (!form) return;
  var buttons = form.querySelectorAll('button[name=preferred]');
  if (buttons.length !== 3) return;
  var sent = false;
  document.addEventListener('keydown', function (e) {
    if (sent || e.ctrlKey || e.metaKey || e.altKey) return;
    var tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
    var pick = e.key === 'ArrowLeft' ? buttons[0] : e.key === 'ArrowRight' ? buttons[1] : e.key === 'ArrowDown' ? buttons[2] : null;
    if (!pick || pick.disabled) return;
    e.preventDefault();
    sent = true;
    pick.focus();
    pick.click();
  });
})();
