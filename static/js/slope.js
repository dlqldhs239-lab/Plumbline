/* Results slopegraph: follow one project across.
   The graph itself is drawn on the server. This only links the two name
   columns and the lines, for a pointer or for the keyboard. */
(function () {
  'use strict';
  var root = document.querySelector('.slope');
  if (!root) return;
  var marked = [];

  function mark(id) {
    marked.forEach(function (el) { el.classList.remove('hot'); });
    marked = [];
    root.classList.toggle('following', !!id);
    if (!id) return;
    marked = Array.prototype.slice.call(root.querySelectorAll('[data-p="' + id + '"]'));
    marked.forEach(function (el) {
      el.classList.add('hot');
      // Painted last, so the followed line lies over the others.
      if (el.tagName.toLowerCase() === 'path') el.parentNode.appendChild(el);
    });
  }

  function of(target) {
    var el = target.closest ? target.closest('[data-p]') : null;
    return el && root.contains(el) ? el.getAttribute('data-p') : null;
  }

  root.addEventListener('mouseover', function (e) { mark(of(e.target)); });
  root.addEventListener('mouseleave', function () { mark(null); });
  root.addEventListener('focusin', function (e) { mark(of(e.target)); });
  root.addEventListener('focusout', function () { mark(null); });
})();
