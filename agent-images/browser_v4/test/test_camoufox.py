import pytest
import sys
import os

sys.path.insert(0, '/opt/openclaw')

try:
    import camoufox
    CAMOUFOX_AVAILABLE = True
except ImportError:
    CAMOUFOX_AVAILABLE = False

@pytest.mark.skipif(not CAMOUFOX_AVAILABLE, reason="camoufox not installed")
class TestCamoufoxInstallation:
    
    def test_module_import(self):
        assert camoufox is not None
        assert hasattr(camoufox, 'Browser')
        assert hasattr(camoufox, 'GeoIP')
    
    def test_browser_creation(self):
        browser = camoufox.Browser()
        assert browser is not None
    
    def test_geoip_lookup(self):
        geoip = camoufox.GeoIP()
        result = geoip.get_location("8.8.8.8")
        assert "ip" in result
        assert result["ip"] == "8.8.8.8"
    
    def test_page_navigation(self):
        browser = camoufox.Browser()
        browser.get("https://example.com")
        assert "Example Domain" in browser.page_source
    
    def test_page_title(self):
        browser = camoufox.Browser()
        browser.get("https://example.com")
        title = browser.title
        assert "Example" in title
    
    def test_element_selection(self):
        browser = camoufox.Browser()
        browser.get("https://example.com")
        heading = browser.select_one("h1")
        assert heading is not None
        assert "Example Domain" in heading.text
    
    def test_javascript_execution(self):
        browser = camoufox.Browser()
        browser.get("https://example.com")
        result = browser.eval_javascript("1 + 1")
        assert result == 2
    
    def test_screenshot(self):
        browser = camoufox.Browser()
        browser.get("https://example.com")
        screenshot_path = "/workspace/test_screenshot.png"
        browser.screenshot(screenshot_path)
        assert os.path.exists(screenshot_path)
        os.remove(screenshot_path)
    
    def test_cookie_handling(self):
        browser = camoufox.Browser()
        browser.get("https://example.com")
        cookies = browser.cookies
        assert isinstance(cookies, list)
    
    def test_network_interception(self):
        browser = camoufox.Browser()
        browser.get("https://example.com")
        requests = browser.get_requests()
        assert len(requests) > 0
        assert any("example.com" in r.url for r in requests)

@pytest.mark.skipif(not CAMOUFOX_AVAILABLE, reason="camoufox not installed")
class TestCamoufoxGeoIP:
    
    def test_geoip_country_lookup(self):
        geoip = camoufox.GeoIP()
        result = geoip.get_country("8.8.8.8")
        assert result == "US"
    
    def test_geoip_city_lookup(self):
        geoip = camoufox.GeoIP()
        result = geoip.get_city("8.8.8.8")
        assert "Mountain View" in result or result is not None
    
    def test_geoip_asn_lookup(self):
        geoip = camoufox.GeoIP()
        result = geoip.get_asn("8.8.8.8")
        assert result is not None

@pytest.mark.skipif(not CAMOUFOX_AVAILABLE, reason="camoufox not installed")
class TestCamoufoxJavaScript:
    
    def test_async_javascript(self):
        browser = camoufox.Browser()
        browser.get("https://example.com")
        result = browser.eval_javascript("""
            new Promise(resolve => {
                setTimeout(() => resolve("async"), 100)
            })
        """)
        assert result == "async"
    
    def test_dom_manipulation(self):
        browser = camoufox.Browser()
        browser.get("https://example.com")
        browser.eval_javascript("""
            document.body.style.backgroundColor = 'red'
        """)
        color = browser.eval_javascript("document.body.style.backgroundColor")
        assert color == "red"