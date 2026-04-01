// Health Check
async function fetchHealth() {
  const resultDiv = document.getElementById('health-result');
  const pre = resultDiv.querySelector('pre');

  try {
    showElement('health-result');
    pre.textContent = 'Loading...';

    const response = await fetch(buildApiUrl('/health'));
    const data = await response.json();

    pre.textContent = formatJson(data);
    pre.className = 'text-sm overflow-x-auto text-green-800';
  } catch (error) {
    pre.textContent = `Error: ${error.message}`;
    pre.className = 'text-sm overflow-x-auto text-red-600';
  }
}
