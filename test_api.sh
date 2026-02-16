#!/bin/bash
# API Integration Test Script for OpenClaw Platform

echo "=========================================="
echo "OpenClaw Platform - API Integration Tests"
echo "=========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASSED=0
FAILED=0

test_endpoint() {
    local name="$1"
    local url="$2"
    local expected="$3"
    
    echo -n "Testing $name... "
    response=$(curl -s "$url")
    
    if echo "$response" | grep -q "$expected"; then
        echo -e "${GREEN}✓ PASSED${NC}"
        ((PASSED++))
    else
        echo -e "${RED}✗ FAILED${NC}"
        echo "  Response: $response"
        ((FAILED++))
    fi
}

test_post_endpoint() {
    local name="$1"
    local url="$2"
    local data="$3"
    local expected="$4"
    
    echo -n "Testing $name... "
    response=$(curl -s -X POST "$url" -H "Content-Type: application/json" -d "$data")
    
    if echo "$response" | grep -q "$expected"; then
        echo -e "${GREEN}✓ PASSED${NC}"
        ((PASSED++))
        echo "$response"
    else
        echo -e "${RED}✗ FAILED${NC}"
        echo "  Response: $response"
        ((FAILED++))
    fi
}

echo "=== Service Health Checks ==="
test_endpoint "Control Plane Health" "http://localhost:8000/health" "healthy"
test_endpoint "Image Builder Health" "http://localhost:8002/health" "healthy"
test_endpoint "Frontend Accessibility" "http://localhost:3000" "OpenClaw"
echo ""

echo "=== API Functionality Tests ==="
test_post_endpoint "Create Task" "http://localhost:8000/api/tasks" \
  '{"name":"API Test Task","description":"Test task creation","llm_model":"gemini-2.0-flash-exp"}' \
  "task-"
echo ""

echo "=== Database Connectivity ==="
test_endpoint "List Tasks" "http://localhost:8000/api/tasks" "[]"
echo ""

echo "=== Temporal Connectivity ==="
docker logs openclaw-contained_temporal-worker_1 2>&1 | tail -5
echo ""

echo "=========================================="
echo "Test Summary"
echo "=========================================="
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo "=========================================="

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed!${NC}"
    exit 1
fi
