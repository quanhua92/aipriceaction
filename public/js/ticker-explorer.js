// Get current form values
function getFormValues() {
  return {
    symbol: document.getElementById('symbol').value,
    interval: document.getElementById('interval').value,
    start_date: document.getElementById('start_date').value,
    format: document.getElementById('format').value
  };
}

// Build ticker API URL from form
function buildTickerUrl() {
  const { symbol, interval, start_date, format } = getFormValues();

  const params = {
    symbol,
    interval,
    format
  };

  if (start_date) {
    params.start_date = start_date;
  }

  return buildApiUrl('/tickers', params);
}

// Display API URL
function displayApiUrl(url) {
  const urlDiv = document.getElementById('api-url');
  const code = urlDiv.querySelector('code');

  code.textContent = url;
  showElement('api-url');
}

// Copy API URL to clipboard
async function copyApiUrl() {
  const url = buildTickerUrl();

  try {
    await navigator.clipboard.writeText(url);
    alert('API URL copied to clipboard!');
  } catch (error) {
    // Fallback for older browsers
    const textarea = document.createElement('textarea');
    textarea.value = url;
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    document.body.removeChild(textarea);
    alert('API URL copied to clipboard!');
  }
}

// Fetch Ticker Data
async function fetchTicker() {
  const { format } = getFormValues();
  const url = buildTickerUrl();

  const resultDiv = document.getElementById('ticker-result');
  const pre = resultDiv.querySelector('pre');
  const countSpan = document.getElementById('result-count');

  try {
    displayApiUrl(url);
    showElement('ticker-result');
    pre.textContent = 'Loading...';
    countSpan.textContent = '';

    const response = await fetch(url);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    if (format === 'csv') {
      const text = await response.text();
      const lines = text.trim().split('\n');
      countSpan.textContent = `${lines.length - 1} records`;
      pre.textContent = text;
      pre.className = 'text-sm overflow-x-auto max-h-96 overflow-y-auto text-gray-800';
    } else {
      const data = await response.json();
      const count = data.tickers ? data.tickers.length : 0;
      countSpan.textContent = `${count} records`;
      pre.textContent = formatJson(data);
      pre.className = 'text-sm overflow-x-auto max-h-96 overflow-y-auto text-green-800';
    }
  } catch (error) {
    pre.textContent = `Error: ${error.message}`;
    pre.className = 'text-sm overflow-x-auto max-h-96 overflow-y-auto text-red-600';
    countSpan.textContent = '';
  }
}

// Quick fetch with preset values
function quickFetch(symbol, interval, startDate) {
  document.getElementById('symbol').value = symbol;
  document.getElementById('interval').value = interval;
  document.getElementById('start_date').value = startDate;
  document.getElementById('format').value = 'json';

  fetchTicker();

  // Scroll to results
  setTimeout(() => {
    document.getElementById('ticker-result').scrollIntoView({
      behavior: 'smooth',
      block: 'nearest'
    });
  }, 100);
}
