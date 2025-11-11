// Analysis API Tab Management
function showAnalysisTab(tabName) {
  // Hide all tabs
  document.querySelectorAll('.analysis-tab-content').forEach(tab => {
    tab.classList.add('hidden');
  });

  // Remove active styling from all buttons
  document.querySelectorAll('.analysis-tab-btn').forEach(btn => {
    btn.classList.remove('border-blue-500', 'text-blue-600');
    btn.classList.add('border-transparent', 'text-gray-600');
  });

  // Show selected tab
  document.getElementById(`analysis-tab-${tabName}`).classList.remove('hidden');

  // Activate selected button
  const activeBtn = document.getElementById(`tab-btn-${tabName}`);
  activeBtn.classList.add('border-blue-500', 'text-blue-600');
  activeBtn.classList.remove('border-transparent', 'text-gray-600');
}

// Build Top Performers API URL
function buildTopPerformersUrl() {
  const params = {};

  const date = document.getElementById('analysis-date').value;
  const sortBy = document.getElementById('analysis-sort-by').value;
  const direction = document.getElementById('analysis-direction').value;
  const limit = document.getElementById('analysis-limit').value;
  const sector = document.getElementById('analysis-sector').value;
  const minVolume = document.getElementById('analysis-min-volume').value;
  const withHourEl = document.getElementById('analysis-with-hour');
  const withHour = withHourEl && withHourEl.value === 'true';

  if (date) params.date = date;
  if (sortBy) params.sort_by = sortBy;
  if (direction) params.direction = direction;
  if (limit) params.limit = limit;
  if (sector) params.sector = sector;
  if (minVolume) params.min_volume = minVolume;
  if (withHour) params.with_hour = 'true';

  return buildApiUrl('/analysis/top-performers', params);
}

// Build MA Scores API URL
function buildMAScoresUrl() {
  const params = {};

  const date = document.getElementById('ma-analysis-date').value;
  const maPeriod = document.getElementById('ma-period').value;
  const minScore = document.getElementById('ma-min-score').value;
  const aboveThreshold = document.getElementById('ma-above-threshold').checked;
  const topPerSector = document.getElementById('ma-top-per-sector').value;

  if (date) params.date = date;
  if (maPeriod) params.ma_period = maPeriod;
  if (minScore) params.min_score = minScore;
  if (aboveThreshold) params.above_threshold_only = 'true';
  if (topPerSector) params.top_per_sector = topPerSector;

  return buildApiUrl('/analysis/ma-scores-by-sector', params);
}

// Fetch Top Performers
async function fetchTopPerformers() {
  const url = buildTopPerformersUrl();
  const resultDiv = document.getElementById('analysis-top-performers-result');
  const countSpan = document.getElementById('analysis-top-performers-count');
  const contentDiv = document.getElementById('analysis-top-performers-content');

  try {
    showElement('analysis-top-performers-result');
    countSpan.textContent = 'Loading...';
    if (contentDiv) contentDiv.innerHTML = '';

    const response = await fetch(url);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const data = await response.json();

    // Display results using enhanced UI
    displayTopPerformers(data);

  } catch (error) {
    countSpan.textContent = 'Error';
    if (contentDiv) {
      contentDiv.innerHTML = `<div class="text-red-600 text-sm p-4 bg-red-50 rounded border border-red-200"><strong>Error:</strong> ${error.message}</div>`;
    }
  }
}

// Display Top Performers with enhanced UI
function displayTopPerformers(data) {
  var countSpan = document.getElementById('analysis-top-performers-count');
  var contentDiv = document.getElementById('analysis-top-performers-content');

  if (!contentDiv) {
    return;
  }

  // Update count information
  var performersCount = (data.data && data.data.performers) ? data.data.performers.length : 0;
  var worstPerformersCount = (data.data && data.data.worst_performers) ? data.data.worst_performers.length : 0;
  var hasHourly = data.data && data.data.hourly && data.data.hourly.length > 0;

  countSpan.textContent = 'Analysis Date: ' + (data.analysis_date || 'N/A') + ' | Total Analyzed: ' + (data.total_analyzed || 0) + ' stocks | Top: ' + performersCount + ' | Worst: ' + worstPerformersCount + (hasHourly ? ' | Hourly: ' + data.data.hourly.length + 'h' : '');

  // Generate HTML content
  var html = '';

  // Tab navigation for daily vs hourly view
  if (hasHourly) {
    html += '<div class="border-b border-gray-200 mb-4">';
    html += '<nav class="flex space-x-4" aria-label="Top Performers Tabs">';
    html += '<button onclick="showPerformersTab(\'daily\')" id="performers-tab-daily" class="performers-tab active-tab px-3 py-2 text-sm font-medium border-b-2 border-blue-500 text-blue-600 transition-all duration-200 hover:bg-blue-50">📊 Daily Summary</button>';
    html += '<button onclick="showPerformersTab(\'hourly\')" id="performers-tab-hourly" class="performers-tab px-3 py-2 text-sm font-medium border-b-2 border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300 transition-all duration-200 hover:bg-gray-50">⏰ Hourly Breakdown (' + data.data.hourly.length + ' hours)</button>';
    html += '</nav></div>';
  }

  // Daily Summary Content
  html += '<div id="performers-content-daily" class="performers-content">';
  try {
    html += renderDailySummary(data.data);
  } catch (error) {
    html += '<div class="text-red-600">Error rendering daily summary</div>';
  }
  html += '</div>';

  // Hourly Breakdown Content
  if (hasHourly) {
    html += '<div id="performers-content-hourly" class="performers-content hidden">';
    try {
      html += renderHourlyBreakdown(data.data.hourly);
    } catch (error) {
      html += '<div class="text-red-600">Error rendering hourly breakdown</div>';
    }
    html += '</div>';
  }

  contentDiv.innerHTML = html;
}

// Render Daily Summary
function renderDailySummary(data) {
  let html = '';

  // Top Performers
  if (data.performers && data.performers.length > 0) {
    html += `
      <div class="mb-6">
        <h4 class="font-semibold text-green-700 mb-3 flex items-center">
          🚀 Top Performers
          <span class="ml-2 text-xs bg-green-100 text-green-800 px-2 py-1 rounded">${data.performers.length} stocks</span>
        </h4>
        <div class="overflow-x-auto border border-gray-200 rounded-lg">
          <table class="min-w-full divide-y divide-gray-200">
            <thead class="bg-gray-50">
              <tr>
                <th class="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">#</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Symbol</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Price</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Change %</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Volume</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Total Money Changed</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">MA20</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">MA20 Score</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">MA50</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">MA50 Score</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">MA100</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">MA100 Score</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">MA200</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">MA200 Score</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Sector</th>
              </tr>
            </thead>
            <tbody class="bg-white divide-y divide-gray-200">
    `;

    data.performers.forEach((stock, index) => {
      html += renderStockRow(stock, index + 1, 'top');
    });

    html += `
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  // Worst Performers
  if (data.worst_performers && data.worst_performers.length > 0) {
    html += `
      <div class="mb-6">
        <h4 class="font-semibold text-red-700 mb-3 flex items-center">
          📉 Worst Performers
          <span class="ml-2 text-xs bg-red-100 text-red-800 px-2 py-1 rounded">${data.worst_performers.length} stocks</span>
        </h4>
        <div class="overflow-x-auto border border-gray-200 rounded-lg">
          <table class="min-w-full divide-y divide-gray-200">
            <thead class="bg-gray-50">
              <tr>
                <th class="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">#</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Symbol</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Price</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Change %</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Volume</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Total Money Changed</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">MA20</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">MA20 Score</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">MA50</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">MA50 Score</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">MA100</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">MA100 Score</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">MA200</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">MA200 Score</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Sector</th>
              </tr>
            </thead>
            <tbody class="bg-white divide-y divide-gray-200">
    `;

    data.worst_performers.forEach((stock, index) => {
      html += renderStockRow(stock, index + 1, 'worst');
    });

    html += `
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  return html;
}

// Render Hourly Breakdown
function renderHourlyBreakdown(hourlyData) {
  let html = '';

  // Hour navigation
  html += `
    <div class="mb-4">
      <div class="flex items-center justify-between mb-3">
        <h4 class="font-semibold text-blue-700">⏰ Trading Hour Performance</h4>
        <div class="text-xs text-gray-500">${hourlyData.length} trading hours</div>
      </div>
      <div class="flex flex-wrap gap-2 mb-4">
  `;

  hourlyData.forEach((hourData, index) => {
    const hourLabel = `Hour ${index + 1}`;
    const timeString = hourData.hour ? extractTime(hourData.hour) : '';
    const isActive = index === 0; // Show first hour by default

    html += `
      <button
        onclick="showHour(${index})"
        id="hour-btn-${index}"
        class="hour-nav-btn px-3 py-2 text-xs font-medium rounded-lg border transition-all duration-200 hover:scale-105 sm:px-4 sm:py-2 ${
          isActive
            ? 'bg-blue-600 text-white border-blue-600'
            : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
        }"
        data-hour="${index}">
        ${hourLabel}
        <div class="text-xs opacity-75">${timeString}</div>
      </button>
    `;
  });

  html += `
      </div>
    </div>
  `;

  // Hour content containers
  hourlyData.forEach((hourData, index) => {
    const isActive = index === 0;
    html += `<div id="hour-content-${index}" class="hour-content ${isActive ? '' : 'hidden'}">`;
    html += renderHourContent(hourData, index);
    html += `</div>`;
  });

  return html;
}

// Render content for a specific hour
function renderHourContent(hourData, hourIndex) {
  const timeString = hourData.hour ? extractTime(hourData.hour) : '';
  let html = '';

  html += `
    <div class="bg-gray-50 rounded-lg p-4 border border-gray-200 mb-4">
      <div class="flex items-center justify-between mb-3">
        <h5 class="font-semibold text-gray-700">
          🕐 Hour ${hourIndex + 1}: ${timeString}
        </h5>
        <div class="flex gap-4 text-xs text-gray-600">
          ${hourData.performers ? `<span>Top: ${hourData.performers.length}</span>` : ''}
          ${hourData.worst_performers ? `<span>Worst: ${hourData.worst_performers.length}</span>` : ''}
        </div>
      </div>
  `;

  // Top performers for this hour
  if (hourData.performers && hourData.performers.length > 0) {
    html += `
      <div class="mb-3">
        <h6 class="text-sm font-medium text-green-700 mb-2">🚀 Top Performers</h6>
        <div class="overflow-x-auto">
          <table class="min-w-full text-sm">
            <thead class="bg-green-50 divide-y divide-green-200">
              <tr>
                <th class="px-2 py-1 text-left text-xs font-medium text-green-700">#</th>
                <th class="px-2 py-1 text-left text-xs font-medium text-green-700">Symbol</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-green-700">Price</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-green-700">Change %</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-green-700">Volume</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-green-700">Total Money Changed</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-green-700">MA20</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-green-700">MA20 Score</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-green-700">MA50</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-green-700">MA50 Score</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-green-700">MA100</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-green-700">MA100 Score</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-green-700">MA200</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-green-700">MA200 Score</th>
              </tr>
            </thead>
            <tbody class="bg-white divide-y divide-gray-200">
    `;

    hourData.performers.forEach((stock, index) => {
      html += renderHourlyStockRow(stock, index + 1, 'top');
    });

    html += `
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  // Worst performers for this hour
  if (hourData.worst_performers && hourData.worst_performers.length > 0) {
    html += `
      <div>
        <h6 class="text-sm font-medium text-red-700 mb-2">📉 Worst Performers</h6>
        <div class="overflow-x-auto">
          <table class="min-w-full text-sm">
            <thead class="bg-red-50 divide-y divide-red-200">
              <tr>
                <th class="px-2 py-1 text-left text-xs font-medium text-red-700">#</th>
                <th class="px-2 py-1 text-left text-xs font-medium text-red-700">Symbol</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-red-700">Price</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-red-700">Change %</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-red-700">Volume</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-red-700">Total Money Changed</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-red-700">MA20</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-red-700">MA20 Score</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-red-700">MA50</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-red-700">MA50 Score</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-red-700">MA100</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-red-700">MA100 Score</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-red-700">MA200</th>
                <th class="px-2 py-1 text-right text-xs font-medium text-red-700">MA200 Score</th>
              </tr>
            </thead>
            <tbody class="bg-white divide-y divide-gray-200">
    `;

    hourData.worst_performers.forEach((stock, index) => {
      html += renderHourlyStockRow(stock, index + 1, 'worst');
    });

    html += `
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  html += `</div>`;
  return html;
}

// Render stock row for daily tables
function renderStockRow(stock, index, type) {
  const changeClass = (stock.close_changed >= 0) ? 'text-green-600' : 'text-red-600';
  const changeSign = (stock.close_changed >= 0) ? '+' : '';
  const volume = stock.volume ? formatVolume(stock.volume) : 'N/A';
  const totalMoney = stock.total_money_changed ? formatVolume(Math.abs(stock.total_money_changed)) : 'N/A';
  const totalMoneySign = stock.total_money_changed >= 0 ? '' : '-';
  const totalMoneyClass = stock.total_money_changed >= 0 ? 'text-green-600' : 'text-red-600';

  // MA values and scores
  const ma20 = stock.ma20 ? formatNumber(stock.ma20) : 'N/A';
  const ma20Score = stock.ma20_score ? stock.ma20_score.toFixed(1) + '%' : 'N/A';
  const ma50 = stock.ma50 ? formatNumber(stock.ma50) : 'N/A';
  const ma50Score = stock.ma50_score ? stock.ma50_score.toFixed(1) + '%' : 'N/A';
  const ma100 = stock.ma100 ? formatNumber(stock.ma100) : 'N/A';
  const ma100Score = stock.ma100_score ? stock.ma100_score.toFixed(1) + '%' : 'N/A';
  const ma200 = stock.ma200 ? formatNumber(stock.ma200) : 'N/A';
  const ma200Score = stock.ma200_score ? stock.ma200_score.toFixed(1) + '%' : 'N/A';

  return '<tr class="hover:bg-gray-50">' +
    '<td class="px-3 py-2 text-sm text-gray-900">' + index + '</td>' +
    '<td class="px-3 py-2 text-sm font-mono font-semibold text-gray-900">' + stock.symbol + '</td>' +
    '<td class="px-3 py-2 text-sm text-gray-900 text-right">' + (stock.close ? formatNumber(stock.close) : 'N/A') + '</td>' +
    '<td class="px-3 py-2 text-sm text-right font-medium ' + changeClass + '">' + changeSign + (stock.close_changed || 0).toFixed(2) + '%</td>' +
    '<td class="px-3 py-2 text-sm text-gray-900 text-right">' + volume + '</td>' +
    '<td class="px-3 py-2 text-sm text-right font-medium ' + totalMoneyClass + '">' + totalMoneySign + totalMoney + '</td>' +
    '<td class="px-3 py-2 text-sm text-gray-900 text-right">' + ma20 + '</td>' +
    '<td class="px-3 py-2 text-sm text-gray-900 text-right">' + ma20Score + '</td>' +
    '<td class="px-3 py-2 text-sm text-gray-900 text-right">' + ma50 + '</td>' +
    '<td class="px-3 py-2 text-sm text-gray-900 text-right">' + ma50Score + '</td>' +
    '<td class="px-3 py-2 text-sm text-gray-900 text-right">' + ma100 + '</td>' +
    '<td class="px-3 py-2 text-sm text-gray-900 text-right">' + ma100Score + '</td>' +
    '<td class="px-3 py-2 text-sm text-gray-900 text-right">' + ma200 + '</td>' +
    '<td class="px-3 py-2 text-sm text-gray-900 text-right">' + ma200Score + '</td>' +
    '<td class="px-3 py-2 text-sm text-gray-500">' + (stock.sector || 'N/A') + '</td>' +
    '</tr>';
}

// Render stock row for hourly tables (compact)
function renderHourlyStockRow(stock, index, type) {
  const changeClass = (stock.close_changed ?? 0) >= 0 ? 'text-green-600' : 'text-red-600';
  const changeSign = (stock.close_changed ?? 0) >= 0 ? '+' : '';
  const volume = stock.volume ? formatVolume(stock.volume) : 'N/A';
  const totalMoney = stock.total_money_changed ? formatVolume(Math.abs(stock.total_money_changed)) : 'N/A';
  const totalMoneySign = (stock.total_money_changed ?? 0) >= 0 ? '' : '-';
  const totalMoneyClass = (stock.total_money_changed ?? 0) >= 0 ? 'text-green-600' : 'text-red-600';

  // MA values and scores
  const ma20 = stock.ma20 ? formatNumber(stock.ma20) : 'N/A';
  const ma20Score = stock.ma20_score ? `${stock.ma20_score.toFixed(1)}%` : 'N/A';
  const ma50 = stock.ma50 ? formatNumber(stock.ma50) : 'N/A';
  const ma50Score = stock.ma50_score ? `${stock.ma50_score.toFixed(1)}%` : 'N/A';
  const ma100 = stock.ma100 ? formatNumber(stock.ma100) : 'N/A';
  const ma100Score = stock.ma100_score ? `${stock.ma100_score.toFixed(1)}%` : 'N/A';
  const ma200 = stock.ma200 ? formatNumber(stock.ma200) : 'N/A';
  const ma200Score = stock.ma200_score ? `${stock.ma200_score.toFixed(1)}%` : 'N/A';

  return `
    <tr class="hover:bg-gray-50">
      <td class="px-2 py-1 text-xs text-gray-900">${index}</td>
      <td class="px-2 py-1 text-xs font-mono font-semibold text-gray-900">${stock.symbol}</td>
      <td class="px-2 py-1 text-xs text-gray-900 text-right">${stock.close ? formatNumber(stock.close) : 'N/A'}</td>
      <td class="px-2 py-1 text-xs text-right font-medium ${changeClass}">${changeSign}${(stock.close_changed ?? 0).toFixed(2)}%</td>
      <td class="px-2 py-1 text-xs text-gray-900 text-right">${volume}</td>
      <td class="px-2 py-1 text-xs text-right font-medium ${totalMoneyClass}">${totalMoneySign}${totalMoney}</td>
      <td class="px-2 py-1 text-xs text-gray-900 text-right">${ma20}</td>
      <td class="px-2 py-1 text-xs text-gray-900 text-right">${ma20Score}</td>
      <td class="px-2 py-1 text-xs text-gray-900 text-right">${ma50}</td>
      <td class="px-2 py-1 text-xs text-gray-900 text-right">${ma50Score}</td>
      <td class="px-2 py-1 text-xs text-gray-900 text-right">${ma100}</td>
      <td class="px-2 py-1 text-xs text-gray-900 text-right">${ma100Score}</td>
      <td class="px-2 py-1 text-xs text-gray-900 text-right">${ma200}</td>
      <td class="px-2 py-1 text-xs text-gray-900 text-right">${ma200Score}</td>
    </tr>
  `;
}

// Show performers tab (daily vs hourly)
function showPerformersTab(tabName) {
  // Hide all content
  document.querySelectorAll('.performers-content').forEach(content => {
    content.classList.add('hidden');
  });

  // Remove active styling from all tabs
  document.querySelectorAll('.performers-tab').forEach(tab => {
    tab.classList.remove('active-tab', 'border-blue-500', 'text-blue-600');
    tab.classList.add('border-transparent', 'text-gray-500');
  });

  // Show selected content
  document.getElementById(`performers-content-${tabName}`).classList.remove('hidden');

  // Activate selected tab
  const activeTab = document.getElementById(`performers-tab-${tabName}`);
  activeTab.classList.add('active-tab', 'border-blue-500', 'text-blue-600');
  activeTab.classList.remove('border-transparent', 'text-gray-500');
}

// Show specific hour content
function showHour(hourIndex) {
  // Hide all hour content
  document.querySelectorAll('.hour-content').forEach(content => {
    content.classList.add('hidden');
  });

  // Remove active styling from all hour buttons
  document.querySelectorAll('.hour-nav-btn').forEach(btn => {
    btn.classList.remove('bg-blue-600', 'text-white', 'border-blue-600');
    btn.classList.add('bg-white', 'text-gray-700', 'border-gray-300');
  });

  // Show selected hour content
  document.getElementById(`hour-content-${hourIndex}`).classList.remove('hidden');

  // Activate selected hour button
  const activeBtn = document.getElementById(`hour-btn-${hourIndex}`);
  activeBtn.classList.add('bg-blue-600', 'text-white', 'border-blue-600');
  activeBtn.classList.remove('bg-white', 'text-gray-700', 'border-gray-300');
}

// Extract time from datetime string
function extractTime(datetimeStr) {
  if (!datetimeStr) return '';
  try {
    return datetimeStr.split(' ')[1] || datetimeStr;
  } catch (e) {
    return datetimeStr;
  }
}

// Fetch MA Scores
async function fetchMAScores() {
  const url = buildMAScoresUrl();
  const resultDiv = document.getElementById('analysis-ma-scores-result');
  const pre = resultDiv.querySelector('pre');
  const countSpan = document.getElementById('analysis-ma-scores-count');

  try {
    showElement('analysis-ma-scores-result');
    pre.textContent = 'Loading...';
    countSpan.textContent = '';

    const response = await fetch(url);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const data = await response.json();

    // Count sectors
    const sectorCount = data.data?.sectors?.length || 0;
    countSpan.textContent = `${sectorCount} sectors | Total analyzed: ${data.total_analyzed || 0}`;

    pre.textContent = formatJson(data);
    pre.className = 'text-sm overflow-x-auto max-h-96 overflow-y-auto text-green-800';
  } catch (error) {
    pre.textContent = `Error: ${error.message}`;
    pre.className = 'text-sm overflow-x-auto max-h-96 overflow-y-auto text-red-600';
    countSpan.textContent = '';
  }
}

// Copy Analysis API URL to clipboard
async function copyAnalysisUrl(type) {
  let url;

  if (type === 'top-performers') {
    url = buildTopPerformersUrl();
  } else if (type === 'ma-scores') {
    url = buildMAScoresUrl();
  }

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
