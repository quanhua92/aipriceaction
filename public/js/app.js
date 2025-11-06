// Main application initialization
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
