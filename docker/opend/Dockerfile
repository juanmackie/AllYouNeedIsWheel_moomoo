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
    # Flatten: copy from double-nested Futu_OpenD_* dir to /opt/opend
    INNER=$(find /tmp/Futu_OpenD_10.1.6108_Ubuntu18.04 -maxdepth 1 -type d -name "Futu_OpenD_*" | head -1) && \
    cp -r "$INNER"/* /opt/opend/ && \
    rm -rf /tmp/Futu_OpenD_* && \
    chmod +x /opt/opend/FutuOpenD /opt/opend/FTWebSocket /opt/opend/FTUpdate 2>/dev/null; true && \
    echo "=== OpenD contents ===" && ls -la /opt/opend/

# Create data directory
RUN mkdir -p /app/data

# Copy entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /opt/opend

EXPOSE 11111

ENTRYPOINT ["/entrypoint.sh"]
