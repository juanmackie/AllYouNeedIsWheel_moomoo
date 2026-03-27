FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    tar \
    ca-certificates \
    libssl-dev \
    netcat-openbsd \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Download OpenD for Ubuntu
ARG OPEND_VERSION=10.1.6108
ARG OPEND_URL=https://softwaredownload.futunn.com/Futu_OpenD_${OPEND_VERSION}_Ubuntu18.04.tar.gz

# Extract and flatten the double-nested directory structure
RUN mkdir -p /opt/opend && \
    wget -qO /tmp/opend.tar.gz "${OPEND_URL}" && \
    tar -xzf /tmp/opend.tar.gz -C /tmp && \
    rm /tmp/opend.tar.gz && \
    INNER="/tmp/Futu_OpenD_${OPEND_VERSION}_Ubuntu18.04/Futu_OpenD_${OPEND_VERSION}_Ubuntu18.04" && \
    if [ ! -d "$INNER" ]; then \
        INNER=$(find "/tmp/Futu_OpenD_${OPEND_VERSION}_Ubuntu18.04" -mindepth 1 -maxdepth 1 -type d -name "Futu_OpenD_${OPEND_VERSION}_Ubuntu18.04" | head -1); \
    fi && \
    if [ -z "$INNER" ] || [ ! -d "$INNER" ]; then \
        echo "ERROR: Could not locate extracted OpenD directory." && exit 1; \
    fi && \
    cp -r "$INNER"/. /opt/opend/ && \
    rm -rf /tmp/Futu_OpenD_* && \
    chmod +x /opt/opend/FutuOpenD /opt/opend/FTWebSocket /opt/opend/FTUpdate 2>/dev/null || true && \
    echo "=== OpenD contents ===" && ls -la /opt/opend/

# Create data directory
RUN mkdir -p /app/data

# Copy entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /opt/opend

EXPOSE 11111

ENTRYPOINT ["/entrypoint.sh"]
