document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('input:not([type=radio]):not([type=checkbox]):not([type=hidden]), textarea')
    .forEach(i => i.setAttribute('autocomplete', 'off'));

  // Double-submit prevention: desabilita o botão de submit após o primeiro clique
  document.addEventListener('submit', function (e) {
    var btn = e.target.querySelector('button[type="submit"]');
    if (btn && !btn.disabled) {
      btn.disabled = true;
      btn.textContent = 'Processando...';
    }
  });
});
