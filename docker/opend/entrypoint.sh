#!/bin/bash
set -e

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

# Check if Appdata.dat exists, if not copy from install
if [ ! -f /app/data/Appdata.dat ] && [ -f /opt/opend/Appdata.dat ]; then
    echo "Copying initial Appdata.dat to persistent volume..."
    cp /opt/opend/Appdata.dat /app/data/Appdata.dat
fi

# Symlink Appdata.dat if it exists in data volume
if [ -f /app/data/Appdata.dat ]; then
    ln -sf /app/data/Appdata.dat /opt/opend/Appdata.dat
fi

# Copy config to data volume for persistence
cp /opt/opend/OpenD.xml /app/data/OpenD.xml

echo "Starting OpenD..."

# Find and run the OpenD binary
if [ -f /opt/opend/FutuOpenD ]; then
    exec /opt/opend/FutuOpenD -cfg_file=/opt/opend/OpenD.xml
elif [ -f /opt/opend/OpenD ]; then
    exec /opt/opend/OpenD -cfg_file=/opt/opend/OpenD.xml
else
    # Try to find any executable
    OPEND_BIN=$(find /opt/opend -maxdepth 1 -type f -executable -name "*OpenD*" | head -1)
    if [ -n "$OPEND_BIN" ]; then
        exec "$OPEND_BIN" -cfg_file=/opt/opend/OpenD.xml
    else
        echo "ERROR: Could not find OpenD binary!"
        ls -la /opt/opend/
        exit 1
    fi
fi
