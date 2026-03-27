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

APPDATA_TARGET="$OPEND_DIR/AppData.dat"
APPDATA_LEGACY_TARGET="$OPEND_DIR/Appdata.dat"
APPDATA_SOURCE=""

for candidate in /app/data/AppData.dat /app/data/Appdata.dat; do
    if [ -f "$candidate" ]; then
        APPDATA_SOURCE="$candidate"
        break
    fi
done

if [ -z "$APPDATA_SOURCE" ] && [ -f "$APPDATA_TARGET" ]; then
    APPDATA_SOURCE="$APPDATA_TARGET"
fi

if [ -z "$APPDATA_SOURCE" ]; then
    APPDATA_SOURCE=$(find /opt/opend -maxdepth 4 -type f \( -name "AppData.dat" -o -name "Appdata.dat" \) | head -1)
fi

if [ -n "$APPDATA_SOURCE" ] && [ "$APPDATA_SOURCE" != "$APPDATA_TARGET" ]; then
    ln -sf "$APPDATA_SOURCE" "$APPDATA_TARGET"
fi

if [ -f "$APPDATA_TARGET" ] && [ ! -e "$APPDATA_LEGACY_TARGET" ]; then
    ln -sf "$APPDATA_TARGET" "$APPDATA_LEGACY_TARGET"
fi

if [ ! -f "$APPDATA_TARGET" ]; then
    echo "ERROR: AppData.dat not found next to FutuOpenD."
    find /opt/opend -maxdepth 4 -type f | head -30
    tail -f /dev/null
fi

CONFIG_PATH="$OPEND_DIR/FutuOpenD.xml"

cat > "$CONFIG_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<futu_opend>
    <ip>0.0.0.0</ip>
    <api_port>11111</api_port>
    <login_account>${MOOMOO_LOGIN}</login_account>
    <login_pwd>${MOOMOO_PASSWORD}</login_pwd>
    <lang>${MOOMOO_LANG:-en}</lang>
    <log_level>${MOOMOO_LOG_LEVEL:-info}</log_level>
    <push_proto_type>0</push_proto_type>
    <price_reminder_push>1</price_reminder_push>
    <auto_hold_quote_right>1</auto_hold_quote_right>
    <future_trade_api_time_zone>UTC+8</future_trade_api_time_zone>
</futu_opend>
EOF

cp "$CONFIG_PATH" /app/data/FutuOpenD.xml
ln -sf "$CONFIG_PATH" "$OPEND_DIR/OpenD.xml"
ln -sf "$CONFIG_PATH" /app/data/OpenD.xml

echo "Using binary: $OPEND_BIN"
echo "Using config: $CONFIG_PATH"
echo "Using appdata: $APPDATA_TARGET"

cd "$OPEND_DIR"

LOG_FILE="/app/data/opend-runtime.log"
rm -f "$LOG_FILE"
touch "$LOG_FILE"

"$OPEND_BIN" -cfg_file="$CONFIG_PATH" >> "$LOG_FILE" 2>&1 &
OPEND_PID=$!

for _ in $(seq 1 60); do
    if nc -z 127.0.0.1 11111; then
        echo "OpenD port is ready on 11111."
        wait "$OPEND_PID"
        exit $?
    fi

    if ! kill -0 "$OPEND_PID" 2>/dev/null; then
        wait "$OPEND_PID"
        EXIT_CODE=$?
        echo "OpenD exited with code $EXIT_CODE before port 11111 became ready."
        cat "$LOG_FILE"
        tail -f /dev/null
    fi

    sleep 2
done

echo "OpenD did not open port 11111 within the expected time."
cat "$LOG_FILE"
tail -f /dev/null
