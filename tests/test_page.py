from web.page import PAGE_HTML


def test_page_has_no_watts_references():
    assert "watt" not in PAGE_HTML.lower()


def test_page_has_expected_elements():
    for expected in ("circuitsTable", "circuitsBody", "liveToggle", "historyChart"):
        assert expected in PAGE_HTML


def test_page_fetches_the_real_api_routes():
    assert '"/api/history"' in PAGE_HTML
    assert '"/api/live"' in PAGE_HTML
