// Main application initialization
document.addEventListener('DOMContentLoaded', () => {
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
