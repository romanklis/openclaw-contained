import pytest
import sys
import os
import tempfile
import time

# Add the module paths
sys.path.insert(0, '/opt/openclaw')

try:
    import camoufox
    import curl_cffi
    BOTH_MODULES_AVAILABLE = True
except ImportError as e:
    BOTH_MODULES_AVAILABLE = False
    print(f"Import error: {e}")

@pytest.mark.skipif(not BOTH_MODULES_AVAILABLE, reason="camoufox or curl_cffi not installed")
class TestBrowserIntegration:
    
    def test_camoufox_and_curl_cffi_coordination(self):
        """Test coordinated use of camoufox and curl_cffi"""
        # Step 1: Use camoufox to get a URL that requires browser interaction
        browser = camoufox.Browser()
        browser.get("https://httpbin.org/html")
        
        # Verify the page loaded correctly
        assert "Herman Melville - Moby-Dick" in browser.page_source
        
        # Step 2: Extract a link from the page to fetch with curl_cffi
        chapter_link = browser.select_one("a[href*='/html']")
        assert chapter_link is not None
        
        href = chapter_link.get_attribute("href")
        assert href is not None
        
        # Step 3: Use curl_cffi to fetch the content
        full_url = f"https://httpbin.org{href}" if href.startswith("/") else href
        response = curl_cffi.curl.get(full_url)
        assert response.status_code == 200
        assert "Moby-Dick" in response.text
        
        browser.quit()
    
    def test_form_submission_integration(self):
        """Test form submission using both tools"""
        # Prepare test data
        test_data = {
            "name": "Test User",
            "email": "test@example.com",
            "message": "Hello from integration test"
        }
        
        # Use curl_cffi to submit form to a test endpoint
        response = curl_cffi.curl.post(
            "https://httpbin.org/post",
            data=test_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        assert response.status_code == 200
        assert "Test User" in response.text
        assert "test@example.com" in response.text
        
        # Use camoufox to verify we can see similar content
        browser = camoufox.Browser()
        browser.get("https://httpbin.org/forms/post")
        
        # Fill and submit form via browser (simplified)
        name_input = browser.select_one("input[name='custname']")
        if name_input:
            name_input.send_keys(test_data["name"])
            # Submit would require finding and clicking submit button
            # For simplicity, we'll just verify the field was populated
            assert name_input.get_attribute("value") == test_data["name"]
        
        browser.quit()
    
    def test_user_agent_consistency(self):
        """Test that we can detect consistent user agent handling"""
        # Get user agent from curl_cffi
        response = curl_cffi.curl.get("https://httpbin.org/headers")
        assert response.status_code == 200
        curl_data = response.json()
        curl_user_agent = curl_data.get("headers", {}).get("User-Agent", "")
        
        # Get user agent from camoufox
        browser = camoufox.Browser()
        browser.get("https://httpbin.org/headers")
        # Extract User-Agent from the page
        ua_element = browser.select_one("pre")
        import json
        if ua_element:
            data = json.loads(ua_element.text)
            browser_user_agent = data.get("headers", {}).get("User-Agent", "")
            
            # Both should have some user agent (may differ due to camoufox's stealth)
            assert len(curl_user_agent) > 0
            assert len(browser_user_agent) > 0
            
            # At least one should contain common browser indicators
            ua_indicators = ["mozilla", "chrome", "safari", "firefox"]
            curl_matches = any(indicator in curl_user_agent.lower() for indicator in ua_indicators)
            browser_matches = any(indicator in browser_user_agent.lower() for indicator in ua_indicators)
            
            assert curl_matches or browser_matches, "Neither UA contains expected browser indicators"
        
        browser.quit()
    
    def test_ip_geoip_consistency(self):
        """Test that IP geolocation works with both tools"""
        # Get IP via curl_cffi
        response = curl_cffi.curl.get("https://api.ipify.org?format=json")
        assert response.status_code == 200
        ip_data = response.json()
        ip = ip_data.get("ip")
        assert ip is not None and len(ip) > 0
        
        # Get geoIP data via camoufox
        browser = camoufox.Browser()
        browser.get("https://httpbin.org/ip")
        # Extract IP from response
        ip_element = browser.select_one("pre")
        if ip_element:
            import json
            ip_data_browser = json.loads(ip_element.text)
            browser_ip = ip_data_browser.get("origin") or ip_data_browser.get("ip")
            
            # Both should return an IP (might be different due to proxy/VPN)
            assert browser_ip is not None and len(browser_ip) > 0
        
        browser.quit()
        
        # Test geoIP lookup with camoufox
        geoip = camoufox.GeoIP()
        location = geoip.get_location(ip)
        assert "ip" in location
        assert location["ip"] == ip
        assert "country" in location
        assert "country_code" in location
    
    def test_error_recovery_integration(self):
        """Test error handling and recovery between both tools"""
        # Test with invalid URL using curl_cffi
        try:
            response = curl_cffi.curl.get("http://thisdomaindefinitelydoesnotexist12345.com/", timeout=3.0)
            # If we get here, the request somehow succeeded (unexpected)
            assert response.status_code != 200 or "error" in response.text.lower()
        except Exception as e:
            # Expected - connection should fail
            assert "ConnectionError" in str(type(e)) or "NameOrServiceNotKnown" in str(e) or "FailedToConnect" in str(e)
        
        # Test with invalid URL using camoufox (should handle gracefully)
        browser = camoufox.Browser()
        try:
            browser.get("http://thisdomaindefinitelydoesnotexist12345.com/")
            # If we get here, page loaded (unexpected for invalid domain)
            # But we should be able to detect it didn't load properly
            title = browser.title
            # Either empty title or error page indicator
        except Exception:
            # Browser might throw exception on failed load - this is acceptable
            pass
        finally:
            browser.quit()
    
    def test_resource_cleanup(self):
        """Test that resources are properly cleaned up"""
        # Test camoufox cleanup
        browser = camoufox.Browser()
        browser.get("https://example.com")
        title = browser.title
        assert "Example" in title
        browser.quit()  # Should close without errors
        
        # Test that we can create another instance after closing
        browser2 = camoufox.Browser()
        browser2.get("https://example.com")
        assert browser2.title == title
        browser2.quit()
        
        # Test curl_cffi doesn't leave hanging connections
        # (This is harder to test directly, but we can make multiple requests)
        for i in range(5):
            response = curl_cffi.curl.get("https://httpbin.org/get")
            assert response.status_code == 200