// Ticker Groups
async function fetchGroups() {
  const resultDiv = document.getElementById('groups-result');
  const pre = resultDiv.querySelector('pre');

  try {
    showElement('groups-result');
    pre.textContent = 'Loading...';

    const response = await fetch(buildApiUrl('/tickers/group'));
    const data = await response.json();

    pre.textContent = formatJson(data);
    pre.className = 'text-sm overflow-x-auto text-green-800';
  } catch (error) {
    pre.textContent = `Error: ${error.message}`;
    pre.className = 'text-sm overflow-x-auto text-red-600';
  }
}
