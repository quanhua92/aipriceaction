// Get current form values
function getFormValues() {
  return {
    symbol: document.getElementById('symbol').value,
    interval: document.getElementById('interval').value,
    start_date: document.getElementById('start_date').value,
    end_date: document.getElementById('end_date').value,
    limit: document.getElementById('limit').value,
    mode: document.getElementById('mode').value,
    format: document.getElementById('format').value
  };
}

// Build ticker API URL from form
function buildTickerUrl() {
  const { symbol, interval, start_date, end_date, limit, mode, format } = getFormValues();

  const params = {};

  // Always add symbol, interval, mode, and format
  if (symbol) params.symbol = symbol;
  if (interval) params.interval = interval;
  if (mode) params.mode = mode;
  if (format) params.format = format;

  // Add start_date if provided
  if (start_date) {
    params.start_date = start_date;
  }

  // Add end_date if provided
  if (end_date) {
    params.end_date = end_date;
  }

  // Add limit if provided and start_date is not provided
  // (limit is ignored when start_date is present per API spec)
  if (limit && !start_date) {
    params.limit = limit;
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

// Quick fetch with preset values (backward compatible)
function quickFetch(symbol, interval, startDate) {
  document.getElementById('symbol').value = symbol;
  document.getElementById('interval').value = interval;
  document.getElementById('start_date').value = startDate || '';
  document.getElementById('end_date').value = '';
  document.getElementById('limit').value = '';
  document.getElementById('mode').value = 'vn'; // Default to VN for backward compatibility
  document.getElementById('format').value = 'csv';

  fetchTicker();

  // Scroll to results
  setTimeout(() => {
    document.getElementById('ticker-result').scrollIntoView({
      behavior: 'smooth',
      block: 'nearest'
    });
  }, 100);
}

// Quick fetch with all parameters (new function)
function quickFetchWithParams(symbol, interval, startDate, endDate, limit, mode = 'vn') {
  document.getElementById('symbol').value = symbol;
  document.getElementById('interval').value = interval;
  document.getElementById('start_date').value = startDate || '';
  document.getElementById('end_date').value = endDate || '';
  document.getElementById('limit').value = limit || '';
  document.getElementById('mode').value = mode;
  document.getElementById('format').value = 'csv';

  fetchTicker();

  // Scroll to results
  setTimeout(() => {
    document.getElementById('ticker-result').scrollIntoView({
      behavior: 'smooth',
      block: 'nearest'
    });
  }, 100);
}

// Clear date field
function clearDate(fieldId) {
  document.getElementById(fieldId).value = '';
}
