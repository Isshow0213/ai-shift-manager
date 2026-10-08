document.querySelectorAll('[data-copy-invitation]').forEach((button) => {
  button.addEventListener('click', async () => {
    const input = document.getElementById(button.dataset.copyInvitation);
    const status = document.getElementById('staff-copy-status');
    if (!input || !status) return;

    let copied = false;
    try {
      await navigator.clipboard.writeText(input.value);
      copied = true;
    } catch {
      input.focus();
      input.select();
      try {
        copied = document.execCommand('copy');
      } catch {
        copied = false;
      }
    }

    status.textContent = copied
      ? '招待リンクをコピーしました。従業員に送ってください。'
      : '招待リンクを選択しました。コピーして従業員に送ってください。';
    if (copied) button.querySelector('[data-copy-label]').textContent = 'コピー済み';
  });
});
