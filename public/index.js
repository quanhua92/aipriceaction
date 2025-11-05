// API Base URL - automatically detects current host
const API_BASE = window.location.origin;

// Utility function to build API URL
function buildApiUrl(endpoint, params = {}) {
  const url = new URL(endpoint, API_BASE);
  Object.entries(params).forEach(([key, value]) => {
    if (value) url.searchParams.append(key, value);
  });
  return url.toString();
}

// Utility function to format JSON
function formatJson(data) {
  return JSON.stringify(data, null, 2);
}

// Utility function to show/hide elements
function showElement(id) {
  document.getElementById(id).classList.remove('hidden');
}

function hideElement(id) {
  document.getElementById(id).classList.add('hidden');
}

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

// Market data cache
let marketData = [];
let sectorMapping = {};

// Tab switching
function showMarketTab(tabName) {
  // Update tab buttons
  document.querySelectorAll('.market-tab').forEach(btn => {
    btn.classList.remove('active-tab', 'border-indigo-600', 'text-indigo-600');
    btn.classList.add('border-transparent', 'text-gray-500', 'hover:text-gray-700', 'hover:border-gray-300');
  });

  event.target.classList.add('active-tab', 'border-indigo-600', 'text-indigo-600');
  event.target.classList.remove('border-transparent', 'text-gray-500', 'hover:text-gray-700', 'hover:border-gray-300');

  // Update content
  document.querySelectorAll('.market-tab-content').forEach(content => {
    content.classList.add('hidden');
  });

  document.getElementById(`tab-${tabName}`).classList.remove('hidden');
}

// Get MA Score from API data (already calculated server-side)
function getMAScore(candle) {
  // Use ma20_score from API, formatted to 3 decimal places
  return candle.ma20_score ? parseFloat(candle.ma20_score.toFixed(3)) : 0;
}

// Format number with commas
function formatNumber(num) {
  if (!num) return '0';
  return num.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

// Format volume (in millions)
function formatVolume(vol) {
  if (!vol) return '0';
  if (vol >= 1000000) {
    return (vol / 1000000).toFixed(1) + 'M';
  }
  return formatNumber(vol);
}

// Refresh market data with current date picker value
function refreshMarketData() {
  loadMarketOverview();
}

// Load market overview data
async function loadMarketOverview() {
  const loadingDiv = document.getElementById('market-loading');
  const statsDiv = document.getElementById('market-stats');
  const tabsDiv = document.getElementById('market-tabs');
  const datePickerDiv = document.getElementById('market-date-picker');

  try {
    // Show loading
    loadingDiv.classList.remove('hidden');
    statsDiv.classList.add('hidden');
    tabsDiv.classList.add('hidden');
    datePickerDiv.classList.add('hidden');

    console.log('Fetching ticker groups...');

    // Fetch ticker groups first
    const groupsResponse = await fetch(buildApiUrl('/tickers/group'));
    if (!groupsResponse.ok) {
      throw new Error(`HTTP ${groupsResponse.status}: ${groupsResponse.statusText}`);
    }
    const groupsData = await groupsResponse.json();

    console.log('Groups data:', groupsData);
    console.log('Number of groups:', Object.keys(groupsData).length);

    // Get all tickers from all groups and build sector mapping
    const allTickers = new Set();
    sectorMapping = {};
    Object.keys(groupsData).forEach(groupName => {
      if (Array.isArray(groupsData[groupName])) {
        groupsData[groupName].forEach(ticker => {
          allTickers.add(ticker);
          sectorMapping[ticker] = groupName;
        });
      }
    });
    const tickerArray = Array.from(allTickers);

    console.log('Total unique tickers:', tickerArray.length);

    // Get analysis date from picker (if set, means user wants historical data)
    let analysisDateStr = document.getElementById('market-analysis-date')?.value;
    let useLatestData = !analysisDateStr;

    // Always fetch 7 days of data for comparison
    let startDateStr;
    if (analysisDateStr) {
      // User selected a specific date - fetch 7 days before it
      const analysisDate = new Date(analysisDateStr + 'T00:00:00');
      const startDate = new Date(analysisDate);
      startDate.setDate(startDate.getDate() - 7);
      startDateStr = startDate.toISOString().split('T')[0];
      console.log(`Fetching market data from ${startDateStr} to ${analysisDateStr}...`);
    } else {
      // No date selected - fetch last 7 days to get latest trading date
      const today = new Date();
      const startDate = new Date(today);
      startDate.setDate(startDate.getDate() - 7);
      startDateStr = startDate.toISOString().split('T')[0];
      console.log(`Fetching latest market data (last 7 days from ${startDateStr})...`);
    }

    const params = {
      interval: '1D',
      start_date: startDateStr,
      format: 'json'
    };

    const tickersResponse = await fetch(buildApiUrl('/tickers', params));

    if (!tickersResponse.ok) {
      throw new Error(`HTTP ${tickersResponse.status}: ${tickersResponse.statusText}`);
    }

    const allTickersData = await tickersResponse.json();
    console.log('Received data for', Object.keys(allTickersData).length, 'tickers');

    // Process each ticker's data
    marketData = [];
    let successCount = 0;
    let failCount = 0;

    Object.entries(allTickersData).forEach(([symbol, tickerData]) => {
      try {
        if (!tickerData || !tickerData.length) {
          failCount++;
          return;
        }

        // Filter data up to analysis date (if specified)
        let filteredData = tickerData;
        if (analysisDateStr) {
          filteredData = tickerData.filter(candle => candle.time <= analysisDateStr);
          if (!filteredData.length) {
            failCount++;
            return;
          }
        }

        const latest = filteredData[filteredData.length - 1];
        const previous = filteredData.length >= 2 ? filteredData[filteredData.length - 2] : latest;

        // Update analysis date to actual latest date if not set
        if (!analysisDateStr && successCount === 0) {
          analysisDateStr = latest.time;
        }

        // Calculate price change (0% if only 1 candle)
        const priceChange = filteredData.length >= 2
          ? ((latest.close - previous.close) / previous.close) * 100
          : 0;
        const maScore = getMAScore(latest);
        const priceVsMA20 = latest.ma20 ? ((latest.close - latest.ma20) / latest.ma20) * 100 : 0;

        successCount++;
        if (successCount <= 3) {
          console.log(`✓ ${symbol}: price=${latest.close}, change=${priceChange.toFixed(2)}%, MA score=${maScore}`);
        }

        marketData.push({
          symbol,
          price: latest.close,
          change: priceChange,
          volume: latest.volume,
          ma_score: maScore,
          price_vs_ma20: priceVsMA20,
          ma_20: latest.ma20,
          ma_50: latest.ma50,
          sector: sectorMapping[symbol] || 'OTHERS',
          latest,
          previous
        });
      } catch (error) {
        console.error(`Error processing ${symbol}:`, error);
        failCount++;
      }
    });

    console.log(`Market data loaded: ${marketData.length} stocks (${successCount} success, ${failCount} failed)`);
    console.log('Sample stock:', marketData[0]);

    if (marketData.length === 0) {
      throw new Error(`No ticker data available. All ${tickerArray.length} tickers failed to load. Check console warnings above.`);
    }

    // Calculate stats
    const gainers = marketData.filter(d => d.change > 0).length;
    const losers = marketData.filter(d => d.change < 0).length;
    const avgVolume = marketData.reduce((sum, d) => sum + (d.volume || 0), 0) / marketData.length;

    console.log('Stats - Gainers:', gainers, 'Losers:', losers, 'Avg Volume:', avgVolume);

    // Update stats
    document.getElementById('stat-total').textContent = marketData.length;
    document.getElementById('stat-gainers').textContent = gainers;
    document.getElementById('stat-losers').textContent = losers;
    document.getElementById('stat-volume').textContent = formatVolume(avgVolume);

    // Update last update time
    const now = new Date();
    document.getElementById('last-update-time').textContent = now.toLocaleTimeString();

    // Show stats, tabs, and date picker
    loadingDiv.classList.add('hidden');
    statsDiv.classList.remove('hidden');
    tabsDiv.classList.remove('hidden');
    datePickerDiv.classList.remove('hidden');

    // Set date picker value if not already set
    const datePickerInput = document.getElementById('market-analysis-date');
    if (!datePickerInput.value) {
      datePickerInput.value = analysisDateStr;
    }

    // Populate tables
    populateGainersTable();
    populateLosersTable();
    populateMATable();
    populateSectorTable();

  } catch (error) {
    console.error('Error loading market data:', error);
    loadingDiv.innerHTML = `
      <div class="text-center py-8">
        <p class="text-red-600 font-semibold">Error loading market data</p>
        <p class="text-sm text-gray-600 mt-2">${error.message}</p>
        <p class="text-xs text-gray-500 mt-2">Check browser console for details</p>
      </div>
    `;
  }
}

// Populate top gainers table
function populateGainersTable() {
  const tbody = document.getElementById('gainers-tbody');
  const topGainers = [...marketData]
    .filter(d => d.change > 0)
    .sort((a, b) => b.change - a.change)
    .slice(0, 10);

  if (topGainers.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="px-4 py-4 text-center text-gray-500">No gainers found</td></tr>';
    return;
  }

  tbody.innerHTML = topGainers.map((stock, index) => `
    <tr class="hover:bg-gray-50">
      <td class="px-4 py-3 text-sm text-gray-500">${index + 1}</td>
      <td class="px-4 py-3 text-sm font-medium text-gray-900">${stock.symbol}</td>
      <td class="px-4 py-3 text-sm text-right text-gray-900">${formatNumber(stock.price)}</td>
      <td class="px-4 py-3 text-sm text-right">
        <span class="text-green-600 font-semibold">+${stock.change.toFixed(2)}%</span>
      </td>
      <td class="px-4 py-3 text-sm text-right text-gray-600">${formatVolume(stock.volume)}</td>
      <td class="px-4 py-3 text-sm text-center">
        <button onclick="quickFetch('${stock.symbol}', '1D', '2024-01-01')"
                class="text-indigo-600 hover:text-indigo-900 text-xs">
          View
        </button>
      </td>
    </tr>
  `).join('');
}

// Populate top losers table
function populateLosersTable() {
  const tbody = document.getElementById('losers-tbody');
  const topLosers = [...marketData]
    .filter(d => d.change < 0)
    .sort((a, b) => a.change - b.change)
    .slice(0, 10);

  if (topLosers.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="px-4 py-4 text-center text-gray-500">No losers found</td></tr>';
    return;
  }

  tbody.innerHTML = topLosers.map((stock, index) => `
    <tr class="hover:bg-gray-50">
      <td class="px-4 py-3 text-sm text-gray-500">${index + 1}</td>
      <td class="px-4 py-3 text-sm font-medium text-gray-900">${stock.symbol}</td>
      <td class="px-4 py-3 text-sm text-right text-gray-900">${formatNumber(stock.price)}</td>
      <td class="px-4 py-3 text-sm text-right">
        <span class="text-red-600 font-semibold">${stock.change.toFixed(2)}%</span>
      </td>
      <td class="px-4 py-3 text-sm text-right text-gray-600">${formatVolume(stock.volume)}</td>
      <td class="px-4 py-3 text-sm text-center">
        <button onclick="quickFetch('${stock.symbol}', '1D', '2024-01-01')"
                class="text-indigo-600 hover:text-indigo-900 text-xs">
          View
        </button>
      </td>
    </tr>
  `).join('');
}

// Populate MA scores table
function populateMATable() {
  const tbody = document.getElementById('ma-tbody');
  const topMA = [...marketData]
    .filter(d => d.ma_score > 0)
    .sort((a, b) => b.ma_score - a.ma_score)
    .slice(0, 15);

  if (topMA.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="px-4 py-4 text-center text-gray-500">No MA data available</td></tr>';
    return;
  }

  tbody.innerHTML = topMA.map((stock, index) => {
    const trend = stock.ma_20 > stock.ma_50 ? '📈 Bullish' : '📉 Bearish';
    const trendClass = stock.ma_20 > stock.ma_50 ? 'text-green-600' : 'text-red-600';

    return `
      <tr class="hover:bg-gray-50">
        <td class="px-4 py-3 text-sm text-gray-500">${index + 1}</td>
        <td class="px-4 py-3 text-sm font-medium text-gray-900">${stock.symbol}</td>
        <td class="px-4 py-3 text-sm text-right">
          <span class="font-semibold ${stock.ma_score > 0 ? 'text-green-600' : stock.ma_score < 0 ? 'text-red-600' : 'text-gray-600'}">${stock.ma_score.toFixed(3)}</span>
        </td>
        <td class="px-4 py-3 text-sm text-right ${trendClass}">${trend}</td>
        <td class="px-4 py-3 text-sm text-center">
          <button onclick="quickFetch('${stock.symbol}', '1D', '2024-01-01')"
                  class="text-indigo-600 hover:text-indigo-900 text-xs">
            View
          </button>
        </td>
      </tr>
    `;
  }).join('');
}

// Populate sector table
function populateSectorTable() {
  const container = document.getElementById('sector-container');

  // Group by sector
  const sectors = {};
  marketData.forEach(stock => {
    if (!sectors[stock.sector]) {
      sectors[stock.sector] = [];
    }
    sectors[stock.sector].push(stock);
  });

  // Sort each sector by MA score
  Object.keys(sectors).forEach(sector => {
    sectors[sector].sort((a, b) => b.ma_score - a.ma_score);
  });

  // Render sectors
  const html = Object.entries(sectors)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([sector, stocks]) => {
      const avgChange = stocks.reduce((sum, s) => sum + s.change, 0) / stocks.length;
      const avgMAScore = stocks.reduce((sum, s) => sum + s.ma_score, 0) / stocks.length;

      return `
        <div class="border border-gray-200 rounded-lg overflow-hidden">
          <div class="bg-gradient-to-r from-gray-50 to-gray-100 px-4 py-3 border-b border-gray-200">
            <div class="flex justify-between items-center">
              <h3 class="font-semibold text-gray-800">${sector}</h3>
              <div class="flex gap-4 text-xs">
                <span class="text-gray-600">Avg Change:
                  <span class="${avgChange > 0 ? 'text-green-600' : 'text-red-600'} font-semibold">
                    ${avgChange > 0 ? '+' : ''}${avgChange.toFixed(2)}%
                  </span>
                </span>
                <span class="text-gray-600">Avg MA Score:
                  <span class="text-indigo-600 font-semibold">${avgMAScore.toFixed(0)}</span>
                </span>
              </div>
            </div>
          </div>
          <div class="overflow-x-auto">
            <table class="min-w-full divide-y divide-gray-200">
              <thead class="bg-gray-50">
                <tr>
                  <th class="px-4 py-2 text-left text-xs font-medium text-gray-500">Symbol</th>
                  <th class="px-4 py-2 text-right text-xs font-medium text-gray-500">Price</th>
                  <th class="px-4 py-2 text-right text-xs font-medium text-gray-500">Change</th>
                  <th class="px-4 py-2 text-right text-xs font-medium text-gray-500">MA Score</th>
                  <th class="px-4 py-2 text-center text-xs font-medium text-gray-500">Action</th>
                </tr>
              </thead>
              <tbody class="bg-white divide-y divide-gray-200">
                ${stocks.map(stock => `
                  <tr class="hover:bg-gray-50">
                    <td class="px-4 py-2 text-sm font-medium text-gray-900">${stock.symbol}</td>
                    <td class="px-4 py-2 text-sm text-right">${formatNumber(stock.price)}</td>
                    <td class="px-4 py-2 text-sm text-right">
                      <span class="${stock.change > 0 ? 'text-green-600' : 'text-red-600'}">
                        ${stock.change > 0 ? '+' : ''}${stock.change.toFixed(2)}%
                      </span>
                    </td>
                    <td class="px-4 py-2 text-sm text-right">
                      <span class="font-semibold ${stock.ma_score > 0 ? 'text-green-600' : stock.ma_score < 0 ? 'text-red-600' : 'text-gray-600'}">${stock.ma_score.toFixed(3)}</span>
                    </td>
                    <td class="px-4 py-2 text-sm text-center">
                      <button onclick="quickFetch('${stock.symbol}', '1D', '2024-01-01')"
                              class="text-indigo-600 hover:text-indigo-900 text-xs">
                        View
                      </button>
                    </td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          </div>
        </div>
      `;
    }).join('');

  container.innerHTML = html;
}

// Initialize page
document.addEventListener('DOMContentLoaded', () => {
  // Set default start date to 30 days ago
  const defaultDate = new Date();
  defaultDate.setDate(defaultDate.getDate() - 30);
  document.getElementById('start_date').value = defaultDate.toISOString().split('T')[0];

  // Update server URL in footer if not localhost
  if (window.location.hostname !== 'localhost') {
    document.getElementById('server-url').textContent = API_BASE;
  }

  // Add enter key support for symbol input
  document.getElementById('symbol').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
      fetchTicker();
    }
  });
});
