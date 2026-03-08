// Auto-dismiss flash messages
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => {
    document.querySelectorAll('.flash').forEach(el => {
      el.style.transition = 'opacity .5s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 500);
    });
  }, 4000);

  // Load dashboard if on index page
  if (document.getElementById('chart-monthly')) {
    loadDashboard();
  }
});

function fmt(n) {
  return '$' + Number(n).toFixed(2);
}

let monthlyChart = null;
let expensesChart = null;

async function loadDashboard() {
  let data;
  try {
    data = await fetch('/api/dashboard-stats').then(r => r.json());
  } catch (e) {
    console.error('Failed to load dashboard stats', e);
    return;
  }

  // Stat numbers
  document.getElementById('s-rev-year').textContent  = fmt(data.total_revenue_year);
  document.getElementById('s-exp-year').textContent  = fmt(data.total_expenses_year);
  const netEl = document.getElementById('s-net-year');
  netEl.textContent = fmt(data.net_profit_year);
  netEl.style.color = data.net_profit_year >= 0 ? 'var(--success)' : 'var(--danger)';
  document.getElementById('s-royalties').textContent = fmt(data.royalties_pending);
  document.getElementById('s-books').textContent     = data.total_books;
  document.getElementById('s-stock').textContent     = fmt(data.physical_stock_value);

  // Monthly revenue vs expenses chart
  const monthlyRev = data.monthly_revenue.slice().reverse();
  const monthlyExp = data.monthly_expenses.slice().reverse();

  // Build a unified label set
  const labelSet = new Set();
  monthlyRev.forEach(r => labelSet.add(`${r.year}-${String(r.month).padStart(2,'0')}`));
  monthlyExp.forEach(r => labelSet.add(`${r.year}-${String(r.month).padStart(2,'0')}`));
  const labels = [...labelSet].sort();

  const revMap = {};
  monthlyRev.forEach(r => { revMap[`${r.year}-${String(r.month).padStart(2,'0')}`] = r.revenue; });
  const expMap = {};
  monthlyExp.forEach(r => { expMap[`${r.year}-${String(r.month).padStart(2,'0')}`] = r.expenses; });

  if (monthlyChart) monthlyChart.destroy();
  monthlyChart = new Chart(document.getElementById('chart-monthly'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: 'Ingresos',
          data: labels.map(l => revMap[l] || 0),
          backgroundColor: 'rgba(37,99,235,.75)',
          borderRadius: 4,
        },
        {
          label: 'Gastos',
          data: labels.map(l => expMap[l] || 0),
          backgroundColor: 'rgba(220,38,38,.5)',
          borderRadius: 4,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: 'top' } },
      scales: { y: { beginAtZero: true } }
    }
  });

  // Expense by category pie chart
  const cats = data.expense_by_category.filter(c => c.total > 0);
  if (expensesChart) expensesChart.destroy();
  if (cats.length > 0) {
    expensesChart = new Chart(document.getElementById('chart-expenses'), {
      type: 'doughnut',
      data: {
        labels: cats.map(c => c.category),
        datasets: [{
          data: cats.map(c => c.total),
          backgroundColor: [
            'rgba(37,99,235,.7)',
            'rgba(220,38,38,.7)',
            'rgba(22,163,74,.7)',
            'rgba(217,119,6,.7)',
            'rgba(139,92,246,.7)',
          ]
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom', labels: { font: { size: 11 } } } }
      }
    });
  } else {
    document.getElementById('chart-expenses').closest('.card').querySelector('.card-title').insertAdjacentHTML(
      'afterend', '<p class="empty-state" style="height:250px;display:flex;align-items:center;justify-content:center">Sin gastos registrados</p>'
    );
    document.getElementById('chart-expenses').style.display = 'none';
  }

  // Top books table
  const container = document.getElementById('top-books-container');
  if (data.top_books.length === 0) {
    container.innerHTML = '<p class="empty-state">No hay datos de ventas todavía.</p>';
  } else {
    container.innerHTML = `<div class="table-wrapper"><table>
      <thead><tr><th>#</th><th>Libro</th><th>Ingresos</th></tr></thead>
      <tbody>
        ${data.top_books.map((b, i) => `<tr>
          <td>${i+1}</td>
          <td>${escHtml(b.title)}</td>
          <td>${fmt(b.revenue)}</td>
        </tr>`).join('')}
      </tbody>
    </table></div>`;
  }

  document.getElementById('last-updated').textContent =
    'Actualizado: ' + new Date().toLocaleTimeString();
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
