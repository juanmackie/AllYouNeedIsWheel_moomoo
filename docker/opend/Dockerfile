FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    tar \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Download OpenD for Ubuntu
ARG OPEND_VERSION=10.1.6108
ARG OPEND_URL=https://softwaredownload.futunn.com/Futu_OpenD_${OPEND_VERSION}_Ubuntu18.04.tar.gz

RUN mkdir -p /opt/opend && \
    wget -qO /tmp/opend.tar.gz "${OPEND_URL}" && \
    tar -xzf /tmp/opend.tar.gz -C /opt/opend && \
    rm /tmp/opend.tar.gz && \
    find /opt/opend -type f -name "*OpenD*" -exec chmod +x {} \; && \
    find /opt/opend -type f -name "*opend*" -exec chmod +x {} \; && \
    ls -la /opt/opend/ && \
    find /opt/opend -type f -executable

# Create data directory
RUN mkdir -p /app/data

# Copy entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /opt/opend

EXPOSE 11111

ENTRYPOINT ["/entrypoint.sh"]
