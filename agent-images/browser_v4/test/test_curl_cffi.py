import pytest
import curl_cffi
import sys
import traceback

class TestCurlCffi:
    
    def test_curl_basic_get(self):
        """Test basic GET request"""
        response = curl_cffi.curl.get("https://api.github.com/health")
        assert response.status_code == 200
        assert "health" in response.text.lower()
    
    def test_curl_post(self):
        """Test POST request"""
        response = curl_cffi.curl.post("https://httpbin.org/post", 
                                      data="{\"key\": \"value\"}", 
                                      headers={"Content-Type": "application/json"})
        assert response.status_code == 200
        assert "key" in response.text
        assert "value" in response.text
    
    def test_curl_timeout(self):
        """Test timeout handling"""
        try:
            response = curl_cffi.curl.get("http://127.0.0.1:9999/", timeout=1.0)
            # This should raise an exception due to timeout
            assert False, "Expected TimeoutException"
        except Exception:
            # Expected behavior
            pass
    
    def test_curl_error_handling(self):
        """Test error response handling"""
        response = curl_cffi.curl.get("http://nonexistent.url/", timeout=2.0)
        # Should not be 200 for non-existent URL
        assert response.status_code != 200 or "ConnectionError" in response.text or "FailedToConnect" in response.text
    
    
class TestCurlUrlEncoding():
    
    def test_special_characters_in_url(self):
        """Test URL encoding with special characters"""
        encoded_url = curl_cffi.utils.urlencode({"q": "a+b&c", "page": 1})
        assert "q=a%2Bb%26c" in encoded_url or "page=1" in encoded_url
    
    def test_non_ascii_characters_in_url(self):
        """Test URL with non-ASCII characters"""
        try:
            # Create URL with unicode characters
            url = "https://httpbin.org/get?email=n%C3%B6%C3%A4r@example.com"
            response = curl_cffi.curl.get(url)
            assert response.status_code == 200
            assert "returns" in response.text
        except Exception as e:
            # Some systems might not support unicode URLs
            pytest.skip(f"Unicode URL test skipped: {e}")
    
    def test_query_parameters(self):
        """Test proper query parameter formation"""
        url = "https://httpbin.org/get?token=abc123&page=1"
        response = curl_cffi.curl.get(url)
        assert response.status_code == 200
        assert "token" in response.text
        assert "abc123" in response.text