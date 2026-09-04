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
    <thead><tr><th>Circuit</th><th class="amps" id="ampsHeader">Amps (last hour peak)</th><th class="amps">All-Time Peak</th></tr></thead>
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
    // Bumped ONLY when the live toggle's checked state changes -- never per
    // fetch. A pollLive() captures the epoch before awaiting and discards its
    // result if the toggle flipped in the meantime, so a reading issued
    // before a toggle-off can't overwrite the historical table afterwards.
    // Deliberately NOT bumped per request: doing that made every poll
    // supersede the previous one whenever /api/live took longer than the 5s
    // interval, which froze the table with no error shown.
    let liveEpoch = 0;

    function isLive() {
      return document.getElementById("liveToggle").checked;
    }

    function renderTable() {
      const body = document.getElementById("circuitsBody");
      body.innerHTML = "";
      // The LATEST rows the poller writes hold last hour's PEAK draw, while
      // /api/live is an instantaneous reading -- same column, two different
      // meanings, so label which one is on screen.
      document.getElementById("ampsHeader").textContent =
        isLive() ? "Amps (live)" : "Amps (last hour peak)";
      const all = Object.values(circuits);
      const main = all.filter(c => c.name === "Main");
      const rest = all.filter(c => c.name !== "Main").sort((a, b) => a.name.localeCompare(b.name));
      [...main, ...rest].forEach(c => {
        const row = document.createElement("tr");
        if (c.name === "Main") row.className = "main-row";
        // textContent, not innerHTML -- circuit names come from Emporia and
        // are never treated as markup.
        const nameCell = document.createElement("td");
        nameCell.textContent = c.name;
        const ampsCell = document.createElement("td");
        ampsCell.className = "amps";
        ampsCell.textContent = c.amps.toFixed(1) + " A";
        const peakCell = document.createElement("td");
        peakCell.className = "amps";
        // Live polls don't carry all_time_max (it's not written on that path
        // -- see handle_live_request); the merge in pollLive() preserves
        // whatever loadHistory() last populated here, so this can still be
        // undefined only before the very first history load ever resolves.
        peakCell.textContent = c.all_time_max != null ? c.all_time_max.toFixed(1) + " A" : "—";
        row.appendChild(nameCell);
        row.appendChild(ampsCell);
        row.appendChild(peakCell);
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
      // The chart is written here and nowhere else, so it always renders --
      // even if the toggle was flipped on while this request was in flight.
      renderChart(data.history);
      if (isLive()) return; // live mode owns the table; don't overwrite it
      circuits = {};
      data.circuits.forEach(c => { circuits[c.circuit_id] = c; });
      renderTable();
      document.getElementById("status").textContent = "Loaded " + new Date().toLocaleTimeString();
    }

    async function pollLive() {
      const epoch = liveEpoch;
      try {
        const res = await fetch("/api/live");
        if (!res.ok) throw new Error("live request failed");
        const data = await res.json();
        if (epoch !== liveEpoch) return; // toggle changed since this was issued; discard
        // Merge, don't replace -- the live payload has no all_time_max (that
        // field is only ever written by the poller, not the live pass-through
        // path), so a wholesale replace here would blank out the column that
        // loadHistory() already populated.
        data.circuits.forEach(c => {
          circuits[c.circuit_id] = { ...circuits[c.circuit_id], ...c };
        });
        renderTable();
        document.getElementById("status").textContent = "Live " + new Date().toLocaleTimeString();
      } catch (e) {
        if (epoch !== liveEpoch) return; // toggle changed since this was issued; discard
        // Fall back to whatever's already rendered (last known reading)
        // instead of breaking the page on a transient Emporia/API failure.
        document.getElementById("status").textContent = "Live update failed, showing last known reading";
      }
    }

    document.getElementById("liveToggle").addEventListener("change", (e) => {
      liveEpoch++; // invalidate any pollLive issued under the previous state
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
