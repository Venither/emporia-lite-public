PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Emporia Lite</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #111; }
  h1 { font-size: 1.25rem; margin-bottom: 0.25rem; }
  table { width: 100%; border-collapse: collapse; margin: 1rem 0; }
  th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #ddd; }
  th { font-weight: 600; }
  .amps { font-variant-numeric: tabular-nums; text-align: right; }
  tr.main-row { font-weight: 700; }
  #liveToggleRow { margin: 1rem 0; font-size: 0.9rem; color: #555; }
  #status { font-size: 0.8rem; color: #888; margin-left: 0.5rem; }
  canvas { max-width: 100%; }
</style>
</head>
<body>
  <h1>Emporia Lite</h1>
  <table id="circuitsTable">
    <thead><tr><th>Circuit</th><th class="amps">Amps</th></tr></thead>
    <tbody id="circuitsBody"></tbody>
  </table>
  <div id="liveToggleRow">
    <label><input type="checkbox" id="liveToggle"> Live reading (updates every 5s)</label>
    <span id="status"></span>
  </div>
  <canvas id="historyChart" height="80"></canvas>

  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2"></script>
  <script>
    let circuits = {}; // circuit_id -> {name, amps}
    let chart = null;
    let liveTimer = null;

    function renderTable() {
      const body = document.getElementById("circuitsBody");
      body.innerHTML = "";
      const all = Object.values(circuits);
      const main = all.filter(c => c.name === "Main");
      const rest = all.filter(c => c.name !== "Main").sort((a, b) => a.name.localeCompare(b.name));
      [...main, ...rest].forEach(c => {
        const row = document.createElement("tr");
        if (c.name === "Main") row.className = "main-row";
        row.innerHTML = `<td>${c.name}</td><td class="amps">${c.amps.toFixed(1)} A</td>`;
        body.appendChild(row);
      });
    }

    function renderChart(history) {
      const ctx = document.getElementById("historyChart");
      const labels = history.map(h => h.hour);
      const data = history.map(h => h.amps);
      if (chart) { chart.destroy(); }
      chart = new Chart(ctx, {
        type: "line",
        data: { labels, datasets: [{ label: "Main (amps)", data, borderWidth: 1.5, pointRadius: 0 }] },
        options: {
          animation: false,
          scales: { y: { beginAtZero: true, title: { display: true, text: "Amps" } } },
          plugins: { zoom: { pan: { enabled: true, mode: "x" }, zoom: { wheel: { enabled: true }, mode: "x" } } }
        }
      });
    }

    async function loadHistory() {
      const res = await fetch("/api/history");
      const data = await res.json();
      circuits = {};
      data.circuits.forEach(c => { circuits[c.circuit_id] = c; });
      renderTable();
      renderChart(data.history);
      document.getElementById("status").textContent = "Loaded " + new Date().toLocaleTimeString();
    }

    async function pollLive() {
      try {
        const res = await fetch("/api/live");
        if (!res.ok) throw new Error("live request failed");
        const data = await res.json();
        // The toggle may have been switched off while this request was in
        // flight (loadHistory() already repopulated `circuits` with the
        // correct cached data in that case) -- discard a late response
        // rather than clobbering it with a stale live reading.
        if (!document.getElementById("liveToggle").checked) return;
        data.circuits.forEach(c => { circuits[c.circuit_id] = c; });
        renderTable();
        document.getElementById("status").textContent = "Live " + new Date().toLocaleTimeString();
      } catch (e) {
        // Fall back to whatever's already rendered (last known LATEST reading)
        // instead of breaking the page on a transient Emporia/API failure.
        document.getElementById("status").textContent = "Live update failed, showing last known reading";
      }
    }

    document.getElementById("liveToggle").addEventListener("change", (e) => {
      if (e.target.checked) {
        pollLive();
        liveTimer = setInterval(pollLive, 5000);
      } else {
        clearInterval(liveTimer);
        loadHistory();
      }
    });

    loadHistory();
  </script>
</body>
</html>
"""
