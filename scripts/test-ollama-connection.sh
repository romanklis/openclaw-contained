#!/bin/bash
# Test connection to Windows host Ollama from WSL

echo "Testing connection to Windows host Ollama..."
echo ""

# Try Docker's host.docker.internal
echo "1. Testing host.docker.internal:11434..."
curl -s http://host.docker.internal:11434/api/tags 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✓ Connection successful via host.docker.internal"
    echo ""
    echo "Available models:"
    curl -s http://host.docker.internal:11434/api/tags | python3 -m json.tool 2>/dev/null || echo "Could not parse JSON"
    exit 0
fi

# Try WSL gateway IP
GATEWAY_IP=$(ip route | grep default | awk '{print $3}')
echo ""
echo "2. Testing WSL gateway ${GATEWAY_IP}:11434..."
curl -s http://${GATEWAY_IP}:11434/api/tags 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✓ Connection successful via WSL gateway"
    echo ""
    echo "Available models:"
    curl -s http://${GATEWAY_IP}:11434/api/tags | python3 -m json.tool 2>/dev/null || echo "Could not parse JSON"
    echo ""
    echo "⚠️  Update .env to use: OLLAMA_BASE_URL=http://${GATEWAY_IP}:11434"
    exit 0
fi

echo ""
echo "✗ Could not connect to Windows Ollama"
echo ""
echo "Troubleshooting:"
echo "1. Ensure Windows Ollama is running"
echo "2. Enable 'Expose Ollama to the network' in Ollama settings"
echo "3. Check Windows Firewall allows port 11434"
echo "4. Verify Ollama is accessible from Windows: http://localhost:11434/api/tags"
