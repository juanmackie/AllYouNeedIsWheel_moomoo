#!/bin/bash

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
    tail -f /dev/null
    exit 1
fi

echo "============================================"
echo "  Moomoo OpenD Container"
echo "============================================"
echo "  Login: ${MOOMOO_LOGIN}"
echo "  Port:  11111"
echo "  Lang:  ${MOOMOO_LANG:-en}"
echo "============================================"

# Find the OpenD binary
OPEND_BIN="/opt/opend/FutuOpenD"
if [ ! -f "$OPEND_BIN" ]; then
    OPEND_BIN=$(find /opt/opend -maxdepth 3 -type f -name "FutuOpenD" | head -1)
fi
if [ ! -f "$OPEND_BIN" ]; then
    OPEND_BIN=$(find /opt/opend -maxdepth 3 -type f -executable | head -1)
fi

if [ -z "$OPEND_BIN" ]; then
    echo "ERROR: Could not find OpenD binary!"
    echo "Contents of /opt/opend/:"
    find /opt/opend -type f | head -20
    tail -f /dev/null
    exit 1
fi

echo "Found OpenD binary: $OPEND_BIN"
chmod +x "$OPEND_BIN"

# Get the directory where the binary lives
OPEND_DIR=$(dirname "$OPEND_BIN")
echo "OpenD directory: $OPEND_DIR"

# Create log directory
mkdir -p "$OPEND_DIR/log"

# Generate OpenD.xml with CORRECT Futu format
cat > "$OPEND_DIR/OpenD.xml" << XMLEOF
<?xml version="1.0" encoding="UTF-8"?>
<FutuOpenD>
    <Global>
        <log_level>${MOOMOO_LOG_LEVEL:-info}</log_level>
        <log_path>./log</log_path>
        <cmd_push_protobuf_ver>0</cmd_push_protobuf_ver>
        <lang>chs</lang>
    </Global>
    <Login>
        <login_account>
            <account>${MOOMOO_LOGIN}</account>
            <pwd>${MOOMOO_PASSWORD}</pwd>
            <login_env>0</login_env>
            <login_region>2</login_region>
        </login_account>
    </Login>
    <Global>
        <ip>0.0.0.0</ip>
        <port>11111</port>
    </Global>
</FutuOpenD>
XMLEOF

echo "Config written to: $OPEND_DIR/OpenD.xml"
cat "$OPEND_DIR/OpenD.xml"

# Symlink Appdata.dat if it exists in data volume
if [ -f /app/data/Appdata.dat ]; then
    ln -sf /app/data/Appdata.dat "$OPEND_DIR/Appdata.dat"
fi

# Copy config to data volume for persistence
cp "$OPEND_DIR/OpenD.xml" /app/data/OpenD.xml

echo "Starting OpenD from $OPEND_DIR ..."

# cd to the binary's directory so OpenD finds its relative files
cd "$OPEND_DIR"
exec "$OPEND_BIN" -cfg_file="$OPEND_DIR/OpenD.xml"
