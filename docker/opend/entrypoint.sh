#!/bin/bash
set -e

# Validate required credentials
if [ -z "$MOOMOO_LOGIN" ] || [ -z "$MOOMOO_PASSWORD" ]; then
    echo "============================================"
    echo "  ERROR: Missing Moomoo credentials!"
    echo "  Set MOOMOO_LOGIN and MOOMOO_PASSWORD"
    echo "  in Coolify environment variables."
    echo "============================================"
    echo ""
    echo "Container will wait for credentials..."
    echo "Update env vars and redeploy."
    # Keep container alive so web can still start
    tail -f /dev/null
    exit 1
fi

# Generate OpenD.xml from environment variables
cat > /opt/opend/OpenD.xml << XMLEOF
<?xml version="1.0" encoding="UTF-8"?>
<FutuOpenD>
    <Global>
        <lang>${MOOMOO_LANG:-en}</lang>
        <log_level>${MOOMOO_LOG_LEVEL:-info}</log_level>
        <ip>0.0.0.0</ip>
        <api_port>11111</api_port>
        <login_account>${MOOMOO_LOGIN}</login_account>
        <login_pwd>${MOOMOO_PASSWORD}</login_pwd>
        <push_proto_type>0</push_proto_type>
        <auto_hold_quote_right>1</auto_hold_quote_right>
    </Global>
</FutuOpenD>
XMLEOF

echo "============================================"
echo "  Moomoo OpenD Container"
echo "============================================"
echo "  Login: ${MOOMOO_LOGIN}"
echo "  Port:  11111"
echo "  Lang:  ${MOOMOO_LANG:-en}"
echo "============================================"

# Symlink Appdata.dat if it exists in data volume
if [ -f /app/data/Appdata.dat ]; then
    ln -sf /app/data/Appdata.dat /opt/opend/Appdata.dat
fi

# Copy config to data volume for persistence
cp /opt/opend/OpenD.xml /app/data/OpenD.xml

echo "Starting OpenD..."

# Find the OpenD binary
OPEND_BIN="/opt/opend/FutuOpenD"
if [ ! -f "$OPEND_BIN" ]; then
    OPEND_BIN=$(find /opt/opend -maxdepth 3 -type f -name "FutuOpenD" | head -1)
fi
if [ ! -f "$OPEND_BIN" ]; then
    OPEND_BIN=$(find /opt/opend -maxdepth 3 -type f -executable | head -1)
fi

if [ -n "$OPEND_BIN" ]; then
    echo "Found OpenD binary: $OPEND_BIN"
    chmod +x "$OPEND_BIN"
    exec "$OPEND_BIN" -cfg_file=/opt/opend/OpenD.xml
else
    echo "ERROR: Could not find OpenD binary!"
    echo "Contents of /opt/opend/:"
    find /opt/opend -type f | head -20
    exit 1
fi
