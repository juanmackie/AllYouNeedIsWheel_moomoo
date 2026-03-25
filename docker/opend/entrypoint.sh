#!/bin/bash
set -e

if [ -z "$MOOMOO_LOGIN" ] || [ -z "$MOOMOO_PASSWORD" ]; then
    echo "Missing MOOMOO_LOGIN or MOOMOO_PASSWORD."
    tail -f /dev/null
fi

echo "============================================"
echo "  Moomoo OpenD Container"
echo "============================================"
echo "  Port: 11111"
echo "  Lang: ${MOOMOO_LANG:-en}"
echo "============================================"

OPEND_BIN="/opt/opend/FutuOpenD"
if [ ! -f "$OPEND_BIN" ]; then
    OPEND_BIN=$(find /opt/opend -maxdepth 4 -type f -name "FutuOpenD" | head -1)
fi
if [ ! -f "$OPEND_BIN" ]; then
    echo "ERROR: Could not find FutuOpenD binary."
    find /opt/opend -maxdepth 4 -type f | head -20
    tail -f /dev/null
fi

chmod +x "$OPEND_BIN"
OPEND_DIR=$(dirname "$OPEND_BIN")
mkdir -p "$OPEND_DIR/log"

if [ -f /app/data/Appdata.dat ]; then
    ln -sf /app/data/Appdata.dat "$OPEND_DIR/Appdata.dat"
elif [ ! -f "$OPEND_DIR/Appdata.dat" ]; then
    PACKAGE_APPDATA=$(find /opt/opend -maxdepth 4 -type f -name "Appdata.dat" | head -1)
    if [ -n "$PACKAGE_APPDATA" ]; then
        ln -sf "$PACKAGE_APPDATA" "$OPEND_DIR/Appdata.dat"
    fi
fi

if [ ! -f "$OPEND_DIR/Appdata.dat" ]; then
    echo "ERROR: Appdata.dat not found next to FutuOpenD."
    find /opt/opend -maxdepth 4 -type f | head -30
    tail -f /dev/null
fi

cat > "$OPEND_DIR/OpenD.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<FutuOpenD>
    <ip>0.0.0.0</ip>
    <api_port>11111</api_port>
    <login_account>${MOOMOO_LOGIN}</login_account>
    <login_pwd>${MOOMOO_PASSWORD}</login_pwd>
    <lang>${MOOMOO_LANG:-en}</lang>
    <log_level>${MOOMOO_LOG_LEVEL:-info}</log_level>
    <push_proto_type>0</push_proto_type>
    <auto_hold_quote_right>1</auto_hold_quote_right>
</FutuOpenD>
EOF

cp "$OPEND_DIR/OpenD.xml" /app/data/OpenD.xml

echo "Using binary: $OPEND_BIN"
echo "Using config: $OPEND_DIR/OpenD.xml"
echo "Using appdata: $OPEND_DIR/Appdata.dat"

cd "$OPEND_DIR"
exec "$OPEND_BIN" -cfg_file="$OPEND_DIR/OpenD.xml"
