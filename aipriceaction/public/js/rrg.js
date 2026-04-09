// RRG (Relative Rotation Graph) Analysis

// Build RRG API URL
function buildRrgUrl() {
  const params = {};

  const benchmark = document.getElementById('rrg-benchmark').value;
  const algorithm = document.getElementById('rrg-algorithm').value;
  const period = document.getElementById('rrg-period').value;
  const trails = document.getElementById('rrg-trails').value;
  const trailLength = document.getElementById('rrg-trail-length').value;
  const mode = document.getElementById('rrg-mode').value;

  if (benchmark) params.benchmark = benchmark;
  if (algorithm) params.algorithm = algorithm;
  if (period) params.period = period;
  if (trails === 'true') params.trails = 'true';
  if (trails === 'true' && trailLength) params.trail_length = trailLength;
  if (mode && mode !== 'vn') params.mode = mode;

  return buildApiUrl('/analysis/rrg', params);
}

// Fetch RRG data
async function fetchRRG() {
  const url = buildRrgUrl();
  const resultDiv = document.getElementById('rrg-result');
  const countSpan = document.getElementById('rrg-count');
  const contentDiv = document.getElementById('rrg-content');

  try {
    showElement('rrg-result');
    countSpan.textContent = 'Loading...';
    if (contentDiv) contentDiv.innerHTML = '<div class="text-center py-8"><div class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div><p class="text-sm text-gray-600 mt-2">Computing RRG...</p></div>';

    const response = await fetch(url);

    if (!response.ok) {
      const errBody = await response.json().catch(() => ({}));
      throw new Error(errBody.error || `HTTP ${response.status}: ${response.statusText}`);
    }

    const data = await response.json();
    displayRRG(data);
  } catch (error) {
    countSpan.textContent = 'Error';
    if (contentDiv) {
      contentDiv.innerHTML = '<div class="text-red-600 text-sm p-4 bg-red-50 rounded border border-red-200"><strong>Error:</strong> ' + error.message + '</div>';
    }
  }
}

// Display RRG data
function displayRRG(data) {
  const countSpan = document.getElementById('rrg-count');
  const contentDiv = document.getElementById('rrg-content');
  if (!contentDiv) return;

  const rrgData = data.data;
  const tickers = rrgData.tickers || [];
  const benchmark = rrgData.benchmark || 'N/A';
  const algorithm = rrgData.algorithm || 'N/A';
  const period = rrgData.period || 'N/A';

  // Classify into quadrants
  const leading = [];
  const weakening = [];
  const lagging = [];
  const improving = [];

  tickers.forEach(t => {
    const x = t.rs_ratio;
    const y = t.rs_momentum;
    const entry = t;
    if (x >= 100 && y >= 100) leading.push(entry);
    else if (x >= 100 && y < 100) weakening.push(entry);
    else if (x < 100 && y < 100) lagging.push(entry);
    else improving.push(entry);
  });

  countSpan.textContent = 'Benchmark: ' + benchmark + ' | Algorithm: ' + algorithm + ' | Period: ' + period + ' | Total: ' + tickers.length;

  let html = '';

  // Quadrant summary
  html += '<div class="grid grid-cols-2 md:grid-cols-4 gap-2 md:gap-3 mb-4">';
  html += '<div class="bg-gradient-to-br from-green-50 to-green-100 rounded-lg p-3 border-2 border-green-300">';
  html += '<div class="text-sm font-bold text-green-800">Leading</div>';
  html += '<div class="text-xs text-gray-600">RS-Ratio &ge; 100, Momentum &ge; 100</div>';
  html += '<div class="text-xl font-bold text-green-700 mt-1">' + leading.length + '</div>';
  html += '</div>';
  html += '<div class="bg-gradient-to-br from-yellow-50 to-yellow-100 rounded-lg p-3 border-2 border-yellow-300">';
  html += '<div class="text-sm font-bold text-yellow-800">Weakening</div>';
  html += '<div class="text-xs text-gray-600">RS-Ratio &ge; 100, Momentum &lt; 100</div>';
  html += '<div class="text-xl font-bold text-yellow-700 mt-1">' + weakening.length + '</div>';
  html += '</div>';
  html += '<div class="bg-gradient-to-br from-red-50 to-red-100 rounded-lg p-3 border-2 border-red-300">';
  html += '<div class="text-sm font-bold text-red-800">Lagging</div>';
  html += '<div class="text-xs text-gray-600">RS-Ratio &lt; 100, Momentum &lt; 100</div>';
  html += '<div class="text-xl font-bold text-red-700 mt-1">' + lagging.length + '</div>';
  html += '</div>';
  html += '<div class="bg-gradient-to-br from-blue-50 to-blue-100 rounded-lg p-3 border-2 border-blue-300">';
  html += '<div class="text-sm font-bold text-blue-800">Improving</div>';
  html += '<div class="text-xs text-gray-600">RS-Ratio &lt; 100, Momentum &ge; 100</div>';
  html += '<div class="text-xl font-bold text-blue-700 mt-1">' + improving.length + '</div>';
  html += '</div>';
  html += '</div>';

  // Tab navigation
  html += '<div class="border-b border-gray-200 mb-4">';
  html += '<nav class="flex space-x-2 md:space-x-4 overflow-x-auto">';
  html += '<button onclick="showRrgTab(\'table\')" id="rrg-tab-btn-table" class="rrg-tab-btn border-b-2 border-indigo-500 px-3 py-2 text-xs md:text-sm font-medium text-indigo-600 whitespace-nowrap">Table</button>';
  html += '<button onclick="showRrgTab(\'leading\')" id="rrg-tab-btn-leading" class="rrg-tab-btn border-b-2 border-transparent px-3 py-2 text-xs md:text-sm font-medium text-gray-500 hover:text-gray-700 whitespace-nowrap">Leading (' + leading.length + ')</button>';
  html += '<button onclick="showRrgTab(\'weakening\')" id="rrg-tab-btn-weakening" class="rrg-tab-btn border-b-2 border-transparent px-3 py-2 text-xs md:text-sm font-medium text-gray-500 hover:text-gray-700 whitespace-nowrap">Weakening (' + weakening.length + ')</button>';
  html += '<button onclick="showRrgTab(\'lagging\')" id="rrg-tab-btn-lagging" class="rrg-tab-btn border-b-2 border-transparent px-3 py-2 text-xs md:text-sm font-medium text-gray-500 hover:text-gray-700 whitespace-nowrap">Lagging (' + lagging.length + ')</button>';
  html += '<button onclick="showRrgTab(\'improving\')" id="rrg-tab-btn-improving" class="rrg-tab-btn border-b-2 border-transparent px-3 py-2 text-xs md:text-sm font-medium text-gray-500 hover:text-gray-700 whitespace-nowrap">Improving (' + improving.length + ')</button>';
  html += '<button onclick="showRrgTab(\'raw\')" id="rrg-tab-btn-raw" class="rrg-tab-btn border-b-2 border-transparent px-3 py-2 text-xs md:text-sm font-medium text-gray-500 hover:text-gray-700 whitespace-nowrap">Raw JSON</button>';
  html += '</nav></div>';

  // Table tab (all tickers)
  html += '<div id="rrg-tab-table" class="rrg-tab-content">';
  html += renderRrgTable(tickers);
  html += '</div>';

  // Quadrant tabs
  html += '<div id="rrg-tab-leading" class="rrg-tab-content hidden">';
  html += renderRrgQuadrantTable(leading, 'Leading', 'green');
  html += '</div>';

  html += '<div id="rrg-tab-weakening" class="rrg-tab-content hidden">';
  html += renderRrgQuadrantTable(weakening, 'Weakening', 'yellow');
  html += '</div>';

  html += '<div id="rrg-tab-lagging" class="rrg-tab-content hidden">';
  html += renderRrgQuadrantTable(lagging, 'Lagging', 'red');
  html += '</div>';

  html += '<div id="rrg-tab-improving" class="rrg-tab-content hidden">';
  html += renderRrgQuadrantTable(improving, 'Improving', 'blue');
  html += '</div>';

  // Raw JSON tab
  html += '<div id="rrg-tab-raw" class="rrg-tab-content hidden">';
  html += '<pre class="text-xs overflow-x-auto max-h-96 overflow-y-auto text-green-800">' + formatJson(data) + '</pre>';
  html += '</div>';

  contentDiv.innerHTML = html;
}

// Render full RRG table sorted by RS-Ratio desc
function renderRrgTable(tickers) {
  const sorted = [...tickers].sort((a, b) => b.rs_ratio - a.rs_ratio);
  return renderRrgTickerRows(sorted);
}

// Render a quadrant's table
function renderRrgQuadrantTable(tickers, label, color) {
  if (tickers.length === 0) {
    return '<div class="text-center py-8 text-gray-500">No tickers in this quadrant</div>';
  }
  // Sort by RS-Momentum desc within quadrant
  const sorted = [...tickers].sort((a, b) => b.rs_momentum - a.rs_momentum);
  return renderRrgTickerRows(sorted);
}

// Render rows shared by table and quadrant views
function renderRrgTickerRows(tickers) {
  let html = '<div class="overflow-x-auto border border-gray-200 rounded-lg">';
  html += '<table class="min-w-full divide-y divide-gray-200">';
  html += '<thead class="bg-gray-50">';
  html += '<tr>';
  html += '<th class="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Symbol</th>';
  html += '<th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase">RS-Ratio</th>';
  html += '<th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase">RS-Momentum</th>';
  html += '<th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase">Raw RS</th>';
  html += '<th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase">Close</th>';
  html += '<th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase">Volume</th>';
  html += '<th class="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Sector</th>';
  html += '</tr>';
  html += '</thead>';
  html += '<tbody class="bg-white divide-y divide-gray-200">';

  tickers.forEach(t => {
    const ratioClass = t.rs_ratio >= 100 ? 'text-green-600' : 'text-red-600';
    const momClass = t.rs_momentum >= 100 ? 'text-green-600' : 'text-red-600';

    html += '<tr class="hover:bg-gray-50">';
    html += '<td class="px-3 py-2 text-sm font-mono font-semibold text-gray-900">' + escHtml(t.symbol) + '</td>';
    html += '<td class="px-3 py-2 text-sm text-right font-medium ' + ratioClass + '">' + t.rs_ratio.toFixed(2) + '</td>';
    html += '<td class="px-3 py-2 text-sm text-right font-medium ' + momClass + '">' + t.rs_momentum.toFixed(2) + '</td>';
    html += '<td class="px-3 py-2 text-sm text-right text-gray-700">' + t.raw_rs.toFixed(4) + '</td>';
    html += '<td class="px-3 py-2 text-sm text-right text-gray-700">' + formatNumber(t.close) + '</td>';
    html += '<td class="px-3 py-2 text-sm text-right text-gray-700">' + formatVolume(t.volume) + '</td>';
    html += '<td class="px-3 py-2 text-sm text-gray-500">' + escHtml(t.sector || '-') + '</td>';
    html += '</tr>';
  });

  html += '</tbody></table></div>';
  return html;
}

// RRG tab switching
function showRrgTab(tabName) {
  document.querySelectorAll('.rrg-tab-content').forEach(el => el.classList.add('hidden'));
  document.querySelectorAll('.rrg-tab-btn').forEach(btn => {
    btn.classList.remove('border-indigo-500', 'text-indigo-600');
    btn.classList.add('border-transparent', 'text-gray-500');
  });
  const content = document.getElementById('rrg-tab-' + tabName);
  if (content) content.classList.remove('hidden');
  const btn = document.getElementById('rrg-tab-btn-' + tabName);
  if (btn) {
    btn.classList.add('border-indigo-500', 'text-indigo-600');
    btn.classList.remove('border-transparent', 'text-gray-500');
  }
}

// Toggle trail length visibility
function toggleRrgTrailLength() {
  const trails = document.getElementById('rrg-trails').value;
  const container = document.getElementById('rrg-trail-length-container');
  if (trails === 'true') {
    container.classList.remove('hidden');
  } else {
    container.classList.add('hidden');
  }
}

// Copy RRG API URL
async function copyRrgUrl() {
  const url = buildRrgUrl();
  try {
    await navigator.clipboard.writeText(url);
    alert('API URL copied to clipboard!');
  } catch (error) {
    const textarea = document.createElement('textarea');
    textarea.value = url;
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    document.body.removeChild(textarea);
    alert('API URL copied to clipboard!');
  }
}

// Escape HTML to prevent XSS
function escHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}
