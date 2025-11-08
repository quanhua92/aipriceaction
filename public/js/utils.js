// API Base URL - automatically detects current host
const API_BASE = window.location.origin;

// Utility function to build API URL
function buildApiUrl(endpoint, params = {}) {
  const url = new URL(endpoint, API_BASE);
  Object.entries(params).forEach(([key, value]) => {
    // Only skip null, undefined, and empty strings
    if (value !== null && value !== undefined && value !== '') {
      url.searchParams.append(key, value);
    }
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

// Get MA Score from API data (already calculated server-side)
function getMAScore(candle) {
  // Use ma20_score from API, formatted to 3 decimal places
  return candle.ma20_score ? parseFloat(candle.ma20_score.toFixed(3)) : 0;
}
