async function refreshSchedule() {
  const interval = document.getElementById('refresh-interval').value;
  const mode = document.getElementById('refresh-mode').value;
  const resultDiv = document.getElementById('refresh-result');
  const pre = resultDiv.querySelector('pre');

  resultDiv.classList.remove('hidden');
  pre.textContent = 'Refreshing...';

  try {
    const res = await fetch('/tickers/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ interval, mode }),
    });
    const data = await res.json();
    pre.textContent = JSON.stringify(data, null, 2);

    if (res.ok) {
      pre.parentElement.classList.remove('border-red-200');
      pre.parentElement.classList.add('border-green-200', 'bg-green-50');
    } else {
      pre.parentElement.classList.remove('border-green-200', 'bg-green-50');
      pre.parentElement.classList.add('border-red-200');
    }
  } catch (err) {
    pre.textContent = 'Error: ' + err.message;
    pre.parentElement.classList.remove('border-green-200', 'bg-green-50');
    pre.parentElement.classList.add('border-red-200');
  }
}
