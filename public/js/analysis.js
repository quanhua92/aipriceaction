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

  if (date) params.date = date;
  if (sortBy) params.sort_by = sortBy;
  if (direction) params.direction = direction;
  if (limit) params.limit = limit;
  if (sector) params.sector = sector;
  if (minVolume) params.min_volume = minVolume;

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
  const pre = resultDiv.querySelector('pre');
  const countSpan = document.getElementById('analysis-top-performers-count');

  try {
    showElement('analysis-top-performers-result');
    pre.textContent = 'Loading...';
    countSpan.textContent = '';

    const response = await fetch(url);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const data = await response.json();

    // Count performers
    const count = data.data?.performers?.length || 0;
    countSpan.textContent = `${count} performers | Total analyzed: ${data.total_analyzed || 0}`;

    pre.textContent = formatJson(data);
    pre.className = 'text-sm overflow-x-auto max-h-96 overflow-y-auto text-green-800';
  } catch (error) {
    pre.textContent = `Error: ${error.message}`;
    pre.className = 'text-sm overflow-x-auto max-h-96 overflow-y-auto text-red-600';
    countSpan.textContent = '';
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
