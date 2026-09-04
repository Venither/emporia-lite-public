from web.page import PAGE_HTML


def test_page_has_no_watts_references():
    assert "watt" not in PAGE_HTML.lower()


def test_page_has_expected_elements():
    for expected in ("circuitsTable", "circuitsBody", "liveToggle", "historyChart"):
        assert expected in PAGE_HTML


def test_page_fetches_the_real_api_routes():
    assert '"/api/history"' in PAGE_HTML
    assert '"/api/live"' in PAGE_HTML


def test_live_epoch_changes_only_on_toggle_not_per_request():
    # A per-request generation counter made every poll supersede the one
    # before it whenever /api/live was slower than the 5s interval, freezing
    # the table silently. The epoch must move only on a toggle state change.
    assert "liveEpoch++" in PAGE_HTML
    assert PAGE_HTML.count("liveEpoch++") == 1
    assert "++requestGeneration" not in PAGE_HTML
    assert "const epoch = liveEpoch;" in PAGE_HTML


def test_chart_is_rendered_before_the_live_ownership_check():
    # renderChart must not sit behind the early return, or toggling live on
    # during the initial load leaves the chart permanently blank.
    body = PAGE_HTML[PAGE_HTML.index("async function loadHistory()"):]
    body = body[: body.index("async function pollLive()")]
    assert body.index("renderChart(data.history)") < body.index("if (isLive()) return")
    assert "renderChart" not in PAGE_HTML[PAGE_HTML.index("async function pollLive()"):]


def test_amps_column_is_labelled_per_mode():
    assert "Amps (last hour peak)" in PAGE_HTML
    assert "Amps (live)" in PAGE_HTML
    assert 'id="ampsHeader"' in PAGE_HTML


def test_circuit_names_are_not_interpolated_into_innerhtml():
    assert "nameCell.textContent = c.name" in PAGE_HTML
    assert "${c.name}" not in PAGE_HTML


def test_all_time_peak_column_present():
    assert "All-Time Peak" in PAGE_HTML
    assert "c.all_time_max" in PAGE_HTML


def test_live_poll_merges_rather_than_replaces_circuit_data():
    # The live payload never carries all_time_max (only the poller writes
    # it). A wholesale replace in pollLive() would blank that column out
    # the moment the toggle is switched on.
    start = PAGE_HTML.index("async function pollLive()")
    end = PAGE_HTML.index('document.getElementById("liveToggle").addEventListener')
    body = PAGE_HTML[start:end]
    assert "circuits[c.circuit_id] = { ...circuits[c.circuit_id], ...c }" in body
    assert "circuits[c.circuit_id] = c;" not in body
