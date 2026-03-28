# JavaScript Modules Documentation

This directory contains the modularized JavaScript code for the AI Price Action web interface.

## File Structure

```
public/js/
├── README.md              # This file
├── app.js                 # Main application initialization
├── utils.js               # Shared utility functions
├── health.js              # Health check endpoint
├── groups.js              # Ticker groups endpoint
├── ticker-explorer.js     # Ticker data explorer functionality
├── market-overview.js     # Market overview and performance tracking
└── sector-analysis.js     # Sector analysis and visualization
```

## Module Descriptions

### `utils.js`
**Purpose:** Core utility functions used across all modules

**Exports:**
- `API_BASE` - Base URL for API calls
- `buildApiUrl(endpoint, params)` - Constructs API URLs with query parameters
- `formatJson(data)` - Pretty-prints JSON data
- `showElement(id)` - Shows a hidden element
- `hideElement(id)` - Hides an element
- `formatNumber(num)` - Formats numbers with commas
- `formatVolume(vol)` - Formats volume with M suffix for millions
- `getMAScore(candle)` - Extracts MA20 score from candle data

**Dependencies:** None

---

### `health.js`
**Purpose:** System health monitoring

**Functions:**
- `fetchHealth()` - Fetches and displays system health statistics

**UI Elements:**
- `#health-result` - Container for health check results

**Dependencies:** `utils.js`

---

### `groups.js`
**Purpose:** Ticker group management

**Functions:**
- `fetchGroups()` - Fetches and displays available ticker groups

**UI Elements:**
- `#groups-result` - Container for ticker groups

**Dependencies:** `utils.js`

---

### `ticker-explorer.js`
**Purpose:** Individual ticker data exploration

**Functions:**
- `getFormValues()` - Retrieves form input values
- `buildTickerUrl()` - Constructs ticker query URL from form
- `displayApiUrl(url)` - Shows the generated API URL
- `copyApiUrl()` - Copies API URL to clipboard
- `fetchTicker()` - Fetches and displays ticker data
- `quickFetch(symbol, interval, startDate)` - Quick preset queries

**UI Elements:**
- `#symbol` - Symbol input field
- `#interval` - Interval selector
- `#start_date` - Start date picker
- `#format` - Format selector (JSON/CSV)
- `#ticker-result` - Results container
- `#api-url` - API URL display
- `#result-count` - Record count display

**Dependencies:** `utils.js`

---

### `market-overview.js`
**Purpose:** Market-wide performance tracking and analysis

**Global Variables:**
- `marketData` - Array of processed market data
- `sectorMapping` - Ticker to sector mapping

**Functions:**
- `showMarketTab(tabName)` - Switches between market overview tabs
- `refreshMarketData()` - Reloads market data
- `loadMarketOverview()` - Main data loading function
- `populateGainersTable()` - Top gainers table
- `populateLosersTable()` - Top losers table
- `populateMATable()` - MA scores table
- `populateSectorTable()` - Sector breakdown table

**UI Elements:**
- `#market-loading` - Loading spinner
- `#market-stats` - Summary statistics
- `#market-tabs` - Tab navigation
- `#market-date-picker` - Date selection
- `#tab-by-sector` - Sector view
- `#tab-top-gainers` - Gainers table
- `#tab-top-losers` - Losers table
- `#tab-top-ma` - MA scores table

**Dependencies:** `utils.js`

---

### `sector-analysis.js`
**Purpose:** Sector-level momentum analysis and visualization

**Global Variables:**
- `sectorAnalysisData` - Processed sector analysis results

**Functions:**
- `loadSectorAnalysis()` - Loads and analyzes sector data
- `showSectorTab(tabName)` - Switches between analysis tabs
- `populateSectorHeatmap()` - Generates heatmap visualization
- `populateSectorQuadrant()` - Quadrant analysis view
- `populateSectorBreadth()` - Breadth analysis table
- `populateSectorDetails()` - Detailed sector breakdown

**UI Elements:**
- `#sector-loading` - Loading spinner
- `#sector-summary` - Summary statistics
- `#sector-tabs` - Tab navigation
- `#sector-tab-heatmap` - Heatmap view
- `#sector-tab-quadrant` - Quadrant analysis
- `#sector-tab-breadth` - Breadth table
- `#sector-tab-details` - Detailed view

**Dependencies:** `utils.js`

---

### `app.js`
**Purpose:** Application initialization and global setup

**Functions:**
- DOMContentLoaded event handler
- Default date initialization
- Server URL configuration
- Keyboard shortcuts

**Dependencies:** All other modules (must be loaded last)

---

## Loading Order

The modules must be loaded in this specific order in `index.html`:

1. `utils.js` - Core utilities (no dependencies)
2. `health.js` - Health check (depends on utils)
3. `groups.js` - Groups (depends on utils)
4. `ticker-explorer.js` - Ticker explorer (depends on utils)
5. `market-overview.js` - Market overview (depends on utils)
6. `sector-analysis.js` - Sector analysis (depends on utils)
7. `app.js` - Initialization (depends on all)

## Adding New Features

To add a new feature module:

1. Create `public/js/your-feature.js`
2. Import required utilities at the top (via comments for documentation)
3. Implement your functions
4. Add script tag to `index.html` before `app.js`
5. Document your module in this README

## Best Practices

- **Naming:** Use descriptive function names with camelCase
- **Error Handling:** Always wrap API calls in try/catch
- **Loading States:** Show loading indicators before async operations
- **UI Updates:** Use `showElement()` and `hideElement()` for consistency
- **API Calls:** Always use `buildApiUrl()` for constructing URLs
- **Formatting:** Use utility functions (`formatNumber`, `formatVolume`, etc.)

## Example: Creating a New Module

```javascript
// public/js/my-feature.js

// Description of what this module does
// Dependencies: utils.js

// Global variables (if needed)
let myFeatureData = null;

// Main function
async function loadMyFeature() {
  const container = document.getElementById('my-feature-container');

  try {
    showElement('my-feature-loading');

    const response = await fetch(buildApiUrl('/my-endpoint'));
    const data = await response.json();

    // Process data
    myFeatureData = processData(data);

    // Update UI
    populateMyFeature();

    hideElement('my-feature-loading');
    showElement('my-feature-results');

  } catch (error) {
    console.error('Error loading feature:', error);
    container.innerHTML = `<div class="error">${error.message}</div>`;
  }
}

// Helper functions
function processData(data) {
  // Processing logic
  return data;
}

function populateMyFeature() {
  // UI population logic
}
```

Then add to `index.html`:
```html
<script src="/public/js/my-feature.js"></script>
```

## Troubleshooting

**Issue:** Functions not defined
- **Solution:** Check loading order in `index.html`
- **Solution:** Ensure `utils.js` is loaded first

**Issue:** UI elements not updating
- **Solution:** Verify element IDs match between HTML and JS
- **Solution:** Check browser console for JavaScript errors

**Issue:** API calls failing
- **Solution:** Use browser DevTools Network tab to inspect requests
- **Solution:** Verify API endpoint URLs with `buildApiUrl()`

## Performance Tips

- Minimize DOM manipulations (batch updates when possible)
- Use `documentFragment` for large table populations
- Cache DOM element references in function scope
- Debounce user input handlers for search/filter features

## Future Enhancements

Potential improvements for the codebase:

- [ ] Convert to ES6 modules (import/export)
- [ ] Add TypeScript type definitions
- [ ] Implement state management (Redux/Vuex)
- [ ] Add unit tests for each module
- [ ] Create build process (webpack/vite)
- [ ] Add hot module replacement for development
- [ ] Implement service worker for offline support
