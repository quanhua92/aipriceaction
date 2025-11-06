// Sector analysis data
let sectorAnalysisData = null;

// Load sector analysis
async function loadSectorAnalysis() {
  const loadingDiv = document.getElementById('sector-loading');
  const summaryDiv = document.getElementById('sector-summary');
  const tabsDiv = document.getElementById('sector-tabs');

  try {
    showElement('sector-loading');
    hideElement('sector-summary');
    hideElement('sector-tabs');

    // Fetch groups and all ticker data
    const groupsResponse = await fetch(buildApiUrl('/tickers/group'));
    const groups = await groupsResponse.json();

    const tickersResponse = await fetch(buildApiUrl('/tickers', { interval: '1D' }));
    const tickersData = await tickersResponse.json();

    // Analyze each sector
    const sectors = {};
    for (const [sectorName, tickers] of Object.entries(groups)) {
      const sectorScores = [];

      for (const ticker of tickers) {
        if (tickersData[ticker] && tickersData[ticker].length > 0) {
          const latest = tickersData[ticker][tickersData[ticker].length - 1];
          const ma20 = latest.ma20_score || 0;
          const ma50 = latest.ma50_score || 0;

          if (ma20 !== null && ma50 !== null) {
            sectorScores.push({
              ticker,
              close: latest.close,
              ma20,
              ma50,
              combined: ma20 + ma50,
              volume: latest.volume || 0
            });
          }
        }
      }

      if (sectorScores.length > 0) {
        const avgMa20 = sectorScores.reduce((sum, s) => sum + s.ma20, 0) / sectorScores.length;
        const avgMa50 = sectorScores.reduce((sum, s) => sum + s.ma50, 0) / sectorScores.length;
        const aboveMa20 = sectorScores.filter(s => s.ma20 > 0).length;
        const aboveMa50 = sectorScores.filter(s => s.ma50 > 0).length;

        sectors[sectorName] = {
          avgMa20,
          avgMa50,
          avgCombined: avgMa20 + avgMa50,
          count: sectorScores.length,
          aboveMa20,
          aboveMa50,
          breadthMa20: (aboveMa20 / sectorScores.length) * 100,
          breadthMa50: (aboveMa50 / sectorScores.length) * 100,
          scores: sectorScores.sort((a, b) => b.combined - a.combined)
        };
      }
    }

    sectorAnalysisData = Object.entries(sectors)
      .sort((a, b) => b[1].avgCombined - a[1].avgCombined);

    // Update summary stats
    const strongSectors = sectorAnalysisData.filter(([, s]) => s.avgCombined > 0).length;
    const weakSectors = sectorAnalysisData.filter(([, s]) => s.avgCombined < 0).length;
    const bestSector = sectorAnalysisData[0];
    const worstSector = sectorAnalysisData[sectorAnalysisData.length - 1];

    document.getElementById('strong-sectors-count').textContent = strongSectors;
    document.getElementById('weak-sectors-count').textContent = weakSectors;
    document.getElementById('best-sector').textContent = bestSector[0];
    document.getElementById('best-sector-score').textContent = `+${bestSector[1].avgCombined.toFixed(2)}%`;
    document.getElementById('worst-sector').textContent = worstSector[0];
    document.getElementById('worst-sector-score').textContent = `${worstSector[1].avgCombined.toFixed(2)}%`;

    // Show UI elements
    hideElement('sector-loading');
    showElement('sector-summary');
    showElement('sector-tabs');

    // Populate all tabs
    populateSectorHeatmap();
    populateSectorQuadrant();
    populateSectorBreadth();
    populateSectorDetails();

  } catch (error) {
    console.error('Error loading sector analysis:', error);
    document.getElementById('sector-loading').innerHTML = `
      <div class="text-center py-8">
        <p class="text-red-600 font-semibold">Error loading sector analysis</p>
        <p class="text-sm text-gray-600 mt-2">${error.message}</p>
      </div>
    `;
  }
}

// Show sector tab
function showSectorTab(tabName) {
  // Update tab buttons
  document.querySelectorAll('.sector-tab').forEach(btn => {
    btn.classList.remove('active-tab', 'border-purple-600', 'text-purple-600');
    btn.classList.add('border-transparent', 'text-gray-500');
  });

  event.target.classList.add('active-tab', 'border-purple-600', 'text-purple-600');
  event.target.classList.remove('border-transparent', 'text-gray-500');

  // Update content
  document.querySelectorAll('.sector-tab-content').forEach(content => {
    content.classList.add('hidden');
  });

  document.getElementById(`sector-tab-${tabName}`).classList.remove('hidden');
}

// Populate heatmap
function populateSectorHeatmap() {
  const container = document.getElementById('sector-heatmap');

  const html = sectorAnalysisData.map(([sector, stats]) => {
    const combined = stats.avgCombined;
    const barLength = Math.min(Math.abs(combined) * 2, 50);

    let barHtml;
    if (combined >= 0) {
      const barColor = combined > 5 ? 'text-green-600' : combined > 2 ? 'text-green-500' : 'text-green-400';
      const bar = '█'.repeat(barLength);
      barHtml = `<span class="${barColor}">${bar}</span>`;
    } else {
      const barColor = combined < -10 ? 'text-red-600' : combined < -5 ? 'text-red-500' : 'text-red-400';
      const spaces = '\xa0'.repeat(Math.max(0, 50 - barLength));
      const bar = '█'.repeat(barLength);
      barHtml = `${spaces}<span class="${barColor}">${bar}</span>`;
    }

    const scoreColor = combined >= 0 ? 'text-green-600' : 'text-red-600';

    return `
      <div class="flex items-center gap-2">
        <span class="w-32 text-gray-700 font-medium text-xs">${sector}</span>
        <span class="w-16 ${scoreColor} text-right">${combined.toFixed(2)}%</span>
        <span class="flex-1">${barHtml}</span>
      </div>
    `;
  }).join('');

  container.innerHTML = html;
}

// Populate quadrant analysis
function populateSectorQuadrant() {
  const q1 = [], q2 = [], q3 = [], q4 = [];

  sectorAnalysisData.forEach(([sector, stats]) => {
    const html = `<div class="text-gray-700">${sector} <span class="text-gray-500">(${stats.avgCombined.toFixed(1)}%)</span></div>`;

    if (stats.avgMa20 >= 0 && stats.avgMa50 >= 0) q1.push(html);
    else if (stats.avgMa20 < 0 && stats.avgMa50 >= 0) q2.push(html);
    else if (stats.avgMa20 < 0 && stats.avgMa50 < 0) q3.push(html);
    else q4.push(html);
  });

  document.getElementById('q1-sectors').innerHTML = q1.length ? q1.join('') : '<div class="text-gray-400">None</div>';
  document.getElementById('q2-sectors').innerHTML = q2.length ? q2.join('') : '<div class="text-gray-400">None</div>';
  document.getElementById('q3-sectors').innerHTML = q3.length ? q3.join('') : '<div class="text-gray-400">None</div>';
  document.getElementById('q4-sectors').innerHTML = q4.length ? q4.join('') : '<div class="text-gray-400">None</div>';
}

// Populate breadth table
function populateSectorBreadth() {
  const tbody = document.getElementById('sector-breadth-table');

  const html = sectorAnalysisData.slice(0, 15).map(([sector, stats]) => {
    const breadthAvg = (stats.breadthMa20 + stats.breadthMa50) / 2;
    const avgScoreColor = stats.avgCombined > 0 ? 'text-green-600' : 'text-red-600';

    return `
      <tr class="hover:bg-gray-50">
        <td class="px-3 py-2 font-medium text-gray-800">${sector}</td>
        <td class="px-3 py-2 text-right text-gray-700">${stats.aboveMa20}/${stats.count} (${stats.breadthMa20.toFixed(0)}%)</td>
        <td class="px-3 py-2 text-right text-gray-700">${stats.aboveMa50}/${stats.count} (${stats.breadthMa50.toFixed(0)}%)</td>
        <td class="px-3 py-2 text-right ${avgScoreColor} font-semibold">${stats.avgCombined.toFixed(2)}%</td>
      </tr>
    `;
  }).join('');

  tbody.innerHTML = html;
}

// Populate detailed view
function populateSectorDetails() {
  const container = document.getElementById('sector-details-container');

  const html = sectorAnalysisData.slice(0, 10).map(([sector, stats]) => `
    <div class="border border-gray-200 rounded-lg overflow-hidden">
      <div class="bg-gradient-to-r from-gray-50 to-gray-100 px-4 py-3 border-b">
        <div class="flex justify-between items-center">
          <h3 class="font-bold text-gray-800">${sector}</h3>
          <div class="flex gap-4 text-xs">
            <span class="text-gray-600">MA20: <span class="${stats.avgMa20 > 0 ? 'text-green-600' : 'text-red-600'} font-semibold">${stats.avgMa20.toFixed(2)}%</span></span>
            <span class="text-gray-600">MA50: <span class="${stats.avgMa50 > 0 ? 'text-green-600' : 'text-red-600'} font-semibold">${stats.avgMa50.toFixed(2)}%</span></span>
            <span class="text-gray-600">Combined: <span class="${stats.avgCombined > 0 ? 'text-green-600' : 'text-red-600'} font-semibold">${stats.avgCombined.toFixed(2)}%</span></span>
          </div>
        </div>
      </div>
      <div class="px-4 py-3">
        <div class="text-xs text-gray-600 mb-2">Top 5 Stocks:</div>
        <div class="grid grid-cols-5 gap-2 text-xs">
          ${stats.scores.slice(0, 5).map(stock => `
            <div class="bg-gray-50 rounded p-2">
              <div class="font-semibold text-gray-800">${stock.ticker}</div>
              <div class="${stock.combined > 0 ? 'text-green-600' : 'text-red-600'}">${stock.combined.toFixed(1)}%</div>
            </div>
          `).join('')}
        </div>
      </div>
    </div>
  `).join('');

  container.innerHTML = html;
}
