FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    tar \
    ca-certificates \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Download OpenD for Ubuntu
ARG OPEND_VERSION=10.1.6108
ARG OPEND_URL=https://softwaredownload.futunn.com/Futu_OpenD_${OPEND_VERSION}_Ubuntu18.04.tar.gz

# Extract and flatten the double-nested directory structure
RUN mkdir -p /tmp/opend-extract && \
    wget -qO /tmp/opend.tar.gz "${OPEND_URL}" && \
    tar -xzf /tmp/opend.tar.gz -C /tmp/opend-extract && \
    rm /tmp/opend.tar.gz && \
    # Move binary files to /opt/opend (flatten double nesting)
    cp -r /tmp/opend-extract/Futu_OpenD_*/Futu_OpenD_*/. /opt/opend/ 2>/dev/null || \
    cp -r /tmp/opend-extract/Futu_OpenD_*/. /opt/opend/ 2>/dev/null || \
    cp -r /tmp/opend-extract/. /opt/opend/ && \
    rm -rf /tmp/opend-extract && \
    chmod +x /opt/opend/FutuOpenD /opt/opend/FTWebSocket /opt/opend/FTUpdate 2>/dev/null || true && \
    ls -la /opt/opend/

# Create data directory
RUN mkdir -p /app/data

# Copy entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /opt/opend

EXPOSE 11111

ENTRYPOINT ["/entrypoint.sh"]
