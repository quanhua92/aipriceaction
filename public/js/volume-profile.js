/**
 * Volume Profile Analysis Module
 * Provides volume-by-price analysis with visual charts and trading insights
 */

// ========================================
// 1. Main Fetch Function
// ========================================

async function fetchVolumeProfile() {
  const url = buildVolumeProfileUrl();
  const resultDiv = document.getElementById('volume-profile-result');
  const contentDiv = document.getElementById('volume-profile-content');
  const countSpan = document.getElementById('volume-profile-count');

  try {
    showElement('volume-profile-result');
    contentDiv.innerHTML = '<div class="text-center py-8"><div class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div><p class="text-sm text-gray-600 mt-2">Analyzing volume profile...</p></div>';
    countSpan.textContent = '';

    const response = await fetch(url);

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`HTTP ${response.status}: ${errorText}`);
    }

    const data = await response.json();

    // Display the volume profile analysis
    displayVolumeProfile(data);

  } catch (error) {
    contentDiv.innerHTML = `<div class="text-sm text-red-600 p-4">${error.message}</div>`;
    contentDiv.className = 'p-4';
    countSpan.textContent = '';
  }
}

// ========================================
// 2. URL Builder
// ========================================

function buildVolumeProfileUrl() {
  const params = {};

  // Required parameters
  const symbol = document.getElementById('vp-symbol').value.trim();
  if (symbol) params.symbol = symbol;

  // Date is required - use input value or default to today
  const dateInput = document.getElementById('vp-date').value;
  const date = dateInput || new Date().toISOString().split('T')[0];
  params.date = date;

  const bins = document.getElementById('vp-bins').value;
  if (bins) params.bins = bins;

  const valueAreaPct = document.getElementById('vp-value-area-pct').value;
  if (valueAreaPct) params.value_area_pct = valueAreaPct;

  const mode = document.getElementById('vp-mode').value;
  if (mode && mode !== 'vn') params.mode = mode;

  return buildApiUrl('/analysis/volume-profile', params);
}

// ========================================
// 3. Display Orchestrator
// ========================================

function displayVolumeProfile(apiData) {
  const contentDiv = document.getElementById('volume-profile-content');
  const countSpan = document.getElementById('volume-profile-count');
  const data = apiData.data;

  // Update count
  countSpan.textContent = `${data.symbol} • ${data.analysis_date || 'Latest'} • ${data.profile.length} price levels`;

  // Build tab navigation and content
  let html = buildVolumeProfileTabs();
  html += renderVolumeChart(data);
  html += renderPriceLevels(data);
  html += renderVolumeStats(data);
  html += renderTradingInsights(data);

  contentDiv.innerHTML = html;
  contentDiv.className = 'p-0';

  // Initialize to first tab
  showVolumeTab('chart');
}

// ========================================
// 4. Tab Navigation HTML
// ========================================

function buildVolumeProfileTabs() {
  return `
    <div class="border-b border-gray-200 px-4 overflow-x-auto">
      <nav class="flex space-x-2 md:space-x-4 min-w-max md:min-w-0 -mb-px" aria-label="Volume Profile Tabs">
        <button onclick="showVolumeTab('chart')" id="vp-tab-btn-chart" class="vp-tab-btn border-b-2 border-indigo-500 px-3 py-3 text-xs md:text-sm font-medium text-indigo-600 whitespace-nowrap">
          📊 Volume Distribution
        </button>
        <button onclick="showVolumeTab('levels')" id="vp-tab-btn-levels" class="vp-tab-btn border-b-2 border-transparent px-3 py-3 text-xs md:text-sm font-medium text-gray-600 hover:text-gray-800 whitespace-nowrap">
          📍 Price Levels
        </button>
        <button onclick="showVolumeTab('stats')" id="vp-tab-btn-stats" class="vp-tab-btn border-b-2 border-transparent px-3 py-3 text-xs md:text-sm font-medium text-gray-600 hover:text-gray-800 whitespace-nowrap">
          📈 Statistics
        </button>
        <button onclick="showVolumeTab('insights')" id="vp-tab-btn-insights" class="vp-tab-btn border-b-2 border-transparent px-3 py-3 text-xs md:text-sm font-medium text-gray-600 hover:text-gray-800 whitespace-nowrap">
          💡 Trading Insights
        </button>
      </nav>
    </div>
  `;
}

// ========================================
// 5. Tab: Volume Distribution Chart
// ========================================

function renderVolumeChart(data) {
  const profile = data.profile;
  const poc = data.poc;
  const valueArea = data.value_area;
  const maxVolume = Math.max(...profile.map(p => p.volume));

  let html = `
    <div id="vp-tab-chart" class="vp-tab-content p-4">
      <div class="bg-gradient-to-r from-indigo-50 to-purple-50 rounded-lg p-4 mb-4 border border-indigo-200">
        <h3 class="text-sm font-semibold text-gray-800 mb-2">Volume by Price Level</h3>
        <p class="text-xs text-gray-600">
          <strong>How to read:</strong> Longer bars = More trading volume at that price.
          <span class="inline-block ml-2 px-2 py-0.5 bg-yellow-100 text-yellow-800 rounded text-xs font-semibold">⭐ POC</span> marks highest volume.
          <span class="inline-block ml-1 px-2 py-0.5 bg-blue-100 text-blue-800 rounded text-xs">📦 VA</span> shows 70% of trading.
        </p>
      </div>

      <div class="space-y-1">
  `;

  // Render each price level as a horizontal bar
  profile.forEach(level => {
    const isPOC = Math.abs(level.price - poc.price) < 0.01;
    const inVA = level.price >= valueArea.low && level.price <= valueArea.high;
    const barWidth = (level.volume / maxVolume * 100).toFixed(1);
    const percentage = level.percentage || 0;
    const isHVN = percentage >= 3.0;
    const isLVN = percentage < 1.0;

    // Determine bar color
    let barColor = 'bg-indigo-500';
    if (isPOC) barColor = 'bg-yellow-500';
    else if (isHVN) barColor = 'bg-green-500';
    else if (isLVN) barColor = 'bg-orange-400';

    // Background color for value area
    const bgClass = inVA ? 'bg-blue-50' : '';

    // Add minimum width for visibility (at least 2% for non-zero volumes)
    const minWidth = level.volume > 0 ? 2 : 0;
    const displayWidth = Math.max(minWidth, parseFloat(barWidth));

    html += `
      <div class="py-1.5 px-2 rounded ${bgClass} hover:bg-gray-100 transition-colors">
        <div class="flex items-center gap-2">
          <div class="w-20 text-xs font-mono text-right font-semibold ${isPOC ? 'text-yellow-700' : 'text-gray-700'}">
            ${formatNumber(level.price)}
          </div>
          <div class="flex-1 relative h-5">
            <div class="${barColor} h-full rounded transition-all" style="width: ${displayWidth}%;"></div>
          </div>
        </div>
        <div class="flex items-center justify-between mt-1 text-xs text-gray-500 pl-22">
          <span>${formatVolume(level.volume)} (${percentage.toFixed(1)}%)</span>
          <span>
            ${isPOC ? '<span class="text-yellow-600 mr-1">⭐</span><span class="px-1.5 py-0.5 bg-yellow-100 text-yellow-700 rounded font-semibold">POC</span>' : ''}
            ${isHVN && !isPOC ? '<span class="px-1.5 py-0.5 bg-green-100 text-green-700 rounded font-semibold">HVN</span>' : ''}
            ${isLVN ? '<span class="px-1.5 py-0.5 bg-orange-100 text-orange-700 rounded font-semibold">LVN</span>' : ''}
          </span>
        </div>
      </div>
    `;
  });

  html += `
      </div>

      <div class="mt-4 p-3 bg-blue-50 rounded text-xs text-gray-600 border border-blue-200">
        <strong>Legend:</strong>
        <span class="ml-2">⭐ POC = Point of Control (highest volume)</span> •
        <span class="ml-1">📦 VA = Value Area (70% of volume)</span> •
        <span class="ml-1">HVN = High Volume Node (&gt;3%)</span> •
        <span class="ml-1">LVN = Low Volume Node (&lt;1%)</span>
      </div>
    </div>
  `;

  return html;
}

// ========================================
// 6. Tab: Price Levels Table
// ========================================

function renderPriceLevels(data) {
  const profile = data.profile;
  const poc = data.poc;
  const valueArea = data.value_area;

  let html = `
    <div id="vp-tab-levels" class="vp-tab-content hidden p-4">
      <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
        <!-- POC Card -->
        <div class="bg-gradient-to-br from-yellow-50 to-yellow-100 rounded-lg p-4 border-2 border-yellow-300">
          <div class="text-xs text-gray-600 mb-1">Point of Control (POC)</div>
          <div class="text-2xl font-bold text-yellow-700">${formatNumber(poc.price)}</div>
          <div class="text-xs text-gray-600 mt-2">
            Volume: ${formatVolume(poc.volume)} (${(poc.percentage || 0).toFixed(1)}%)
          </div>
          <div class="text-xs text-yellow-700 mt-1 font-semibold">⭐ Highest volume level</div>
        </div>

        <!-- Value Area Low Card -->
        <div class="bg-gradient-to-br from-blue-50 to-blue-100 rounded-lg p-4 border-2 border-blue-300">
          <div class="text-xs text-gray-600 mb-1">Value Area Low</div>
          <div class="text-2xl font-bold text-blue-700">${formatNumber(valueArea.low)}</div>
          <div class="text-xs text-gray-600 mt-2">
            Support level at ${(valueArea.percentage || 70).toFixed(1)}% volume
          </div>
          <div class="text-xs text-blue-700 mt-1 font-semibold">📦 Bottom of fair value</div>
        </div>

        <!-- Value Area High Card -->
        <div class="bg-gradient-to-br from-purple-50 to-purple-100 rounded-lg p-4 border-2 border-purple-300">
          <div class="text-xs text-gray-600 mb-1">Value Area High</div>
          <div class="text-2xl font-bold text-purple-700">${formatNumber(valueArea.high)}</div>
          <div class="text-xs text-gray-600 mt-2">
            Resistance level at ${(valueArea.percentage || 70).toFixed(1)}% volume
          </div>
          <div class="text-xs text-purple-700 mt-1 font-semibold">📦 Top of fair value</div>
        </div>
      </div>

      <!-- Price Levels Table -->
      <div class="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div class="overflow-x-auto -mx-4 md:mx-0">
          <table class="min-w-full divide-y divide-gray-200 text-xs">
            <thead class="bg-gray-50">
              <tr>
                <th class="px-3 py-2 text-left font-semibold text-gray-700">Price</th>
                <th class="px-3 py-2 text-right font-semibold text-gray-700">Volume</th>
                <th class="px-3 py-2 text-right font-semibold text-gray-700">% of Total</th>
                <th class="px-3 py-2 text-center font-semibold text-gray-700">Type</th>
                <th class="px-3 py-2 text-center font-semibold text-gray-700">Zone</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-200 bg-white">
  `;

  profile.forEach(level => {
    const isPOC = Math.abs(level.price - poc.price) < 0.01;
    const inVA = level.price >= valueArea.low && level.price <= valueArea.high;
    const percentage = level.percentage || 0;
    const isHVN = percentage >= 3.0;
    const isLVN = percentage < 1.0;

    let typeLabel = 'Normal';
    let typeClass = 'bg-gray-100 text-gray-700';
    if (isPOC) {
      typeLabel = 'POC';
      typeClass = 'bg-yellow-100 text-yellow-700 font-bold';
    } else if (isHVN) {
      typeLabel = 'HVN';
      typeClass = 'bg-green-100 text-green-700 font-semibold';
    } else if (isLVN) {
      typeLabel = 'LVN';
      typeClass = 'bg-orange-100 text-orange-700';
    }

    const zoneLabel = inVA ? 'Value Area' : 'Outside VA';
    const zoneClass = inVA ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-600';

    html += `
      <tr class="hover:bg-gray-50">
        <td class="px-3 py-2 font-mono font-semibold text-gray-900">${formatNumber(level.price)}</td>
        <td class="px-3 py-2 text-right text-gray-700">${formatVolume(level.volume)}</td>
        <td class="px-3 py-2 text-right text-gray-700">${percentage.toFixed(2)}%</td>
        <td class="px-3 py-2 text-center">
          <span class="px-2 py-0.5 rounded text-xs ${typeClass}">${typeLabel}</span>
        </td>
        <td class="px-3 py-2 text-center">
          <span class="px-2 py-0.5 rounded text-xs ${zoneClass}">${zoneLabel}</span>
        </td>
      </tr>
    `;
  });

  html += `
            </tbody>
          </table>
        </div>
      </div>
    </div>
  `;

  return html;
}

// ========================================
// 7. Tab: Volume Statistics
// ========================================

function renderVolumeStats(data) {
  const stats = data.statistics;
  const priceRange = data.price_range;
  const poc = data.poc;
  const valueArea = data.value_area;

  return `
    <div id="vp-tab-stats" class="vp-tab-content hidden p-4">
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <!-- Volume Statistics -->
        <div class="bg-white rounded-lg border border-gray-200 p-4">
          <h3 class="text-sm font-semibold text-gray-800 mb-3">📊 Volume Statistics</h3>
          <div class="space-y-2 text-xs">
            <div class="flex justify-between py-1 border-b border-gray-100">
              <span class="text-gray-600">Total Volume:</span>
              <span class="font-semibold text-gray-900">${formatVolume(data.total_volume)}</span>
            </div>
            <div class="flex justify-between py-1 border-b border-gray-100">
              <span class="text-gray-600">Value Area Volume:</span>
              <span class="font-semibold text-gray-900">${formatVolume(valueArea.volume)}</span>
            </div>
            <div class="flex justify-between py-1 border-b border-gray-100">
              <span class="text-gray-600">Value Area %:</span>
              <span class="font-semibold text-blue-700">${(valueArea.percentage || 70).toFixed(1)}%</span>
            </div>
            <div class="flex justify-between py-1 border-b border-gray-100">
              <span class="text-gray-600">POC Volume:</span>
              <span class="font-semibold text-yellow-700">${formatVolume(poc.volume)} (${(poc.percentage || 0).toFixed(1)}%)</span>
            </div>
          </div>
        </div>

        <!-- Price Statistics -->
        <div class="bg-white rounded-lg border border-gray-200 p-4">
          <h3 class="text-sm font-semibold text-gray-800 mb-3">💰 Price Statistics</h3>
          <div class="space-y-2 text-xs">
            <div class="flex justify-between py-1 border-b border-gray-100">
              <span class="text-gray-600">Low Price:</span>
              <span class="font-semibold text-gray-900">${formatNumber(priceRange.low)}</span>
            </div>
            <div class="flex justify-between py-1 border-b border-gray-100">
              <span class="text-gray-600">High Price:</span>
              <span class="font-semibold text-gray-900">${formatNumber(priceRange.high)}</span>
            </div>
            <div class="flex justify-between py-1 border-b border-gray-100">
              <span class="text-gray-600">Price Spread:</span>
              <span class="font-semibold text-gray-900">${formatNumber(priceRange.spread)}</span>
            </div>
            <div class="flex justify-between py-1 border-b border-gray-100">
              <span class="text-gray-600">POC Price:</span>
              <span class="font-semibold text-yellow-700">${formatNumber(poc.price)}</span>
            </div>
          </div>
        </div>

        <!-- Distribution Statistics -->
        <div class="bg-white rounded-lg border border-gray-200 p-4">
          <h3 class="text-sm font-semibold text-gray-800 mb-3">📈 Distribution</h3>
          <div class="space-y-2 text-xs">
            <div class="flex justify-between py-1 border-b border-gray-100">
              <span class="text-gray-600">Volume at/above POC:</span>
              <span class="font-semibold text-gray-900">${(stats.volume_above_poc_percent || 50).toFixed(1)}%</span>
            </div>
            <div class="flex justify-between py-1 border-b border-gray-100">
              <span class="text-gray-600">Volume below POC:</span>
              <span class="font-semibold text-gray-900">${(stats.volume_below_poc_percent || 50).toFixed(1)}%</span>
            </div>
            <div class="flex justify-between py-1 border-b border-gray-100">
              <span class="text-gray-600">Balance:</span>
              <span class="font-semibold ${Math.abs((stats.volume_above_poc_percent || 50) - 50) < 10 ? 'text-green-700' : 'text-orange-700'}">
                ${Math.abs((stats.volume_above_poc_percent || 50) - 50) < 10 ? '✓ Balanced' : '⚠ Skewed'}
              </span>
            </div>
          </div>
        </div>

        <!-- Price Levels -->
        <div class="bg-white rounded-lg border border-gray-200 p-4">
          <h3 class="text-sm font-semibold text-gray-800 mb-3">🎯 Key Levels</h3>
          <div class="space-y-2 text-xs">
            <div class="flex justify-between py-1 border-b border-gray-100">
              <span class="text-gray-600">VA Range:</span>
              <span class="font-semibold text-blue-700">${formatNumber(valueArea.high - valueArea.low)}</span>
            </div>
            <div class="flex justify-between py-1 border-b border-gray-100">
              <span class="text-gray-600">VA % of Spread:</span>
              <span class="font-semibold text-gray-900">${((valueArea.high - valueArea.low) / priceRange.spread * 100).toFixed(1)}%</span>
            </div>
            <div class="flex justify-between py-1 border-b border-gray-100">
              <span class="text-gray-600">POC Position:</span>
              <span class="font-semibold text-gray-900">
                ${poc.price < (priceRange.low + priceRange.spread * 0.4) ? '📍 Lower third' :
                  poc.price > (priceRange.low + priceRange.spread * 0.6) ? '📍 Upper third' : '📍 Middle'}
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- Summary Card -->
      <div class="mt-4 bg-gradient-to-r from-indigo-50 to-purple-50 rounded-lg p-4 border border-indigo-200">
        <h3 class="text-sm font-semibold text-gray-800 mb-2">📋 Profile Summary</h3>
        <p class="text-xs text-gray-700 leading-relaxed">
          <strong>${data.symbol}</strong> traded <strong>${formatVolume(data.total_volume)}</strong> total volume on ${data.analysis_date || 'latest trading day'}.
          The Point of Control (POC) at <strong class="text-yellow-700">${formatNumber(poc.price)}</strong> represents the price with highest acceptance (${(poc.percentage || 0).toFixed(1)}% of volume).
          The Value Area ranges from <strong class="text-blue-700">${formatNumber(valueArea.low)}</strong> to <strong class="text-purple-700">${formatNumber(valueArea.high)}</strong>,
          containing ${(valueArea.percentage || 70).toFixed(1)}% of all trading activity.
          Price distribution is ${Math.abs((stats.volume_above_poc_percent || 50) - 50) < 10 ? 'balanced' : 'skewed'} around the POC.
        </p>
      </div>
    </div>
  `;
}

// ========================================
// 8. Tab: Trading Insights
// ========================================

function renderTradingInsights(data) {
  const poc = data.poc;
  const valueArea = data.value_area;
  const priceRange = data.price_range;
  const profile = data.profile;

  // Calculate HVN and LVN levels
  const hvnLevels = profile.filter(p => (p.percentage || 0) >= 3.0);
  const lvnLevels = profile.filter(p => (p.percentage || 0) < 1.0);

  return `
    <div id="vp-tab-insights" class="vp-tab-content hidden p-4">
      <div class="space-y-4">
        <!-- POC Analysis -->
        <div class="bg-gradient-to-r from-yellow-50 to-yellow-100 rounded-lg p-4 border border-yellow-200">
          <h3 class="text-sm font-semibold text-yellow-800 mb-2">⭐ Point of Control Strategy</h3>
          <div class="text-xs text-gray-700 space-y-2">
            <p>
              <strong>POC at ${formatNumber(poc.price)}</strong> represents "fair value" - the price where buyers and sellers agreed the most (${(poc.percentage || 0).toFixed(1)}% of volume).
            </p>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
              <div class="bg-white rounded p-3 border border-yellow-300">
                <div class="font-semibold text-yellow-800 mb-1">📈 If price moves away from POC:</div>
                <ul class="list-disc list-inside space-y-1 text-gray-600">
                  <li>5%+ above POC → Consider selling (potential reversion)</li>
                  <li>5%+ below POC → Consider buying (potential bounce)</li>
                  <li>Target: Return to POC at ${formatNumber(poc.price)}</li>
                </ul>
              </div>
              <div class="bg-white rounded p-3 border border-yellow-300">
                <div class="font-semibold text-yellow-800 mb-1">🎯 Tomorrow's reference:</div>
                <ul class="list-disc list-inside space-y-1 text-gray-600">
                  <li>Today's POC often becomes tomorrow's support/resistance</li>
                  <li>Watch for price reaction when testing ${formatNumber(poc.price)}</li>
                  <li>Strong POC = High probability reversal zone</li>
                </ul>
              </div>
            </div>
          </div>
        </div>

        <!-- Value Area Strategy -->
        <div class="bg-gradient-to-r from-blue-50 to-purple-50 rounded-lg p-4 border border-blue-200">
          <h3 class="text-sm font-semibold text-blue-800 mb-2">📦 Value Area Trading</h3>
          <div class="text-xs text-gray-700 space-y-2">
            <p>
              <strong>Value Area: ${formatNumber(valueArea.low)} - ${formatNumber(valueArea.high)}</strong> contains ${(valueArea.percentage || 70).toFixed(1)}% of trading activity.
            </p>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
              <div class="bg-white rounded p-3 border border-blue-300">
                <div class="font-semibold text-blue-800 mb-1">💰 Range Trading:</div>
                <ul class="list-disc list-inside space-y-1 text-gray-600">
                  <li>Buy near VA Low: ${formatNumber(valueArea.low)} (support)</li>
                  <li>Sell near VA High: ${formatNumber(valueArea.high)} (resistance)</li>
                  <li>Stop trading if price breaks outside VA</li>
                </ul>
              </div>
              <div class="bg-white rounded p-3 border border-purple-300">
                <div class="font-semibold text-purple-800 mb-1">🚀 Breakout Signals:</div>
                <ul class="list-disc list-inside space-y-1 text-gray-600">
                  <li>Price above VA High = Potential overbought</li>
                  <li>Price below VA Low = Potential oversold</li>
                  <li>Outside VA = Look for mean reversion</li>
                </ul>
              </div>
            </div>
          </div>
        </div>

        <!-- HVN/LVN Strategy -->
        <div class="bg-gradient-to-r from-green-50 to-orange-50 rounded-lg p-4 border border-green-200">
          <h3 class="text-sm font-semibold text-gray-800 mb-2">🎯 High/Low Volume Nodes</h3>
          <div class="text-xs text-gray-700">
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
              <!-- HVN Levels -->
              <div class="bg-white rounded p-3 border border-green-300">
                <div class="font-semibold text-green-800 mb-2">✅ High Volume Nodes (${hvnLevels.length} levels &gt;3%)</div>
                ${hvnLevels.length > 0 ? `
                  <div class="space-y-1 mb-2">
                    ${hvnLevels.slice(0, 5).map(level => `
                      <div class="flex justify-between text-gray-600">
                        <span class="font-mono">${formatNumber(level.price)}</span>
                        <span>${(level.percentage || 0).toFixed(1)}%</span>
                      </div>
                    `).join('')}
                  </div>
                  <div class="text-gray-600 mt-2 pt-2 border-t border-green-200">
                    <strong>Strategy:</strong>
                    <ul class="list-disc list-inside mt-1 space-y-0.5">
                      <li>Strong support when price falls to HVN</li>
                      <li>Strong resistance when price rises to HVN</li>
                      <li>Place stop-loss just below HVN for support trades</li>
                    </ul>
                  </div>
                ` : '<div class="text-gray-500 italic">No significant HVN levels found</div>'}
              </div>

              <!-- LVN Levels -->
              <div class="bg-white rounded p-3 border border-orange-300">
                <div class="font-semibold text-orange-800 mb-2">⚠️ Low Volume Nodes (${lvnLevels.length} levels &lt;1%)</div>
                ${lvnLevels.length > 0 ? `
                  <div class="space-y-1 mb-2">
                    ${lvnLevels.slice(0, 5).map(level => `
                      <div class="flex justify-between text-gray-600">
                        <span class="font-mono">${formatNumber(level.price)}</span>
                        <span>${(level.percentage || 0).toFixed(1)}%</span>
                      </div>
                    `).join('')}
                  </div>
                  <div class="text-gray-600 mt-2 pt-2 border-t border-orange-200">
                    <strong>Strategy:</strong>
                    <ul class="list-disc list-inside mt-1 space-y-0.5">
                      <li>Price moves FAST through LVN (no support/resistance)</li>
                      <li>Don't place stop-loss in LVN areas</li>
                      <li>LVN gaps likely to fill quickly</li>
                    </ul>
                  </div>
                ` : '<div class="text-gray-500 italic">No significant LVN levels found</div>'}
              </div>
            </div>
          </div>
        </div>

        <!-- Quick Reference Card -->
        <div class="bg-white rounded-lg border-2 border-indigo-300 p-4">
          <h3 class="text-sm font-semibold text-indigo-800 mb-3">📋 Quick Trading Checklist</h3>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
            <div>
              <div class="font-semibold text-gray-800 mb-1">Before Every Trade:</div>
              <ul class="space-y-1 text-gray-600">
                <li>✓ Where is POC? (${formatNumber(poc.price)})</li>
                <li>✓ Am I inside or outside Value Area?</li>
                <li>✓ What's the nearest HVN for support/resistance?</li>
                <li>✓ Any LVN between entry and target?</li>
              </ul>
            </div>
            <div>
              <div class="font-semibold text-gray-800 mb-1">Best Setups:</div>
              <ul class="space-y-1 text-gray-600">
                <li>✓ Price at Value Area boundaries</li>
                <li>✓ Price testing yesterday's POC</li>
                <li>✓ HVN coincides with MA20/MA50</li>
                <li>✓ Breakout from VA with volume confirmation</li>
              </ul>
            </div>
          </div>
        </div>

        <!-- Disclaimer -->
        <div class="bg-gray-50 rounded p-3 border border-gray-200 text-xs text-gray-600">
          <strong>⚠️ Important:</strong> Volume Profile is a tool, not a crystal ball. Always combine with:
          trend analysis, moving averages, market sentiment, and risk management. Past volume doesn't guarantee future results.
        </div>
      </div>
    </div>
  `;
}

// ========================================
// 9. Tab Navigation Function
// ========================================

function showVolumeTab(tabName) {
  // Hide all tab contents
  document.querySelectorAll('.vp-tab-content').forEach(tab => {
    tab.classList.add('hidden');
  });

  // Remove active styling from all buttons
  document.querySelectorAll('.vp-tab-btn').forEach(btn => {
    btn.classList.remove('border-indigo-500', 'text-indigo-600');
    btn.classList.add('border-transparent', 'text-gray-600');
  });

  // Show selected tab
  const selectedTab = document.getElementById(`vp-tab-${tabName}`);
  if (selectedTab) {
    selectedTab.classList.remove('hidden');
  }

  // Activate selected button
  const activeBtn = document.getElementById(`vp-tab-btn-${tabName}`);
  if (activeBtn) {
    activeBtn.classList.add('border-indigo-500', 'text-indigo-600');
    activeBtn.classList.remove('border-transparent', 'text-gray-600');
  }
}

// ========================================
// 10. Copy URL to Clipboard
// ========================================

async function copyVolumeProfileUrl() {
  const url = buildVolumeProfileUrl();

  try {
    await navigator.clipboard.writeText(url);
    alert('✅ Volume Profile API URL copied to clipboard!\n\n' + url);
  } catch (error) {
    // Fallback for older browsers
    const textarea = document.createElement('textarea');
    textarea.value = url;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    document.body.removeChild(textarea);
    alert('✅ Volume Profile API URL copied to clipboard!\n\n' + url);
  }
}
