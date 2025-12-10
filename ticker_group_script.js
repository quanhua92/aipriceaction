// 1. Select all cells in the 'nmTotalTradedQty' column
const volCells = document.querySelectorAll('div[col-id="nmTotalTradedQty"][role="gridcell"]');

let stockData = [];

volCells.forEach(cell => {
    const symbol = cell.closest('.ag-row').getAttribute('row-id');
    const quantity = parseInt(cell.innerText.replace(/,/g, ''), 10);

    // Filter: Only keep if valid number AND > 100,000
    if (!isNaN(quantity) && quantity > 100000) {
        stockData.push({ symbol: symbol, quantity: quantity });
    }
});

// 2. Sort by Quantity (Descending: Highest to Lowest)
// Change to (a.quantity - b.quantity) if you want Lowest to Highest
stockData.sort((a, b) => b.quantity - a.quantity);

// 3. Extract just the symbols for the final result
const filteredStocks = stockData.map(item => item.symbol);

// --- LOGGING FOR VERIFICATION ---
console.log("--- SORTED RESULT ---");
console.table(stockData); // See the volumes to verify sort order

// --- FINAL JSON OUTPUT ---
const result = JSON.stringify(filteredStocks);
console.log("--- JSON TO COPY ---");
console.log(result);
copy(result);