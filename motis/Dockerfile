# MOTIS 2 routing server with web UI – build context is the repository root.
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl bzip2 osmium-tool python3 \
 && rm -rf /var/lib/apt/lists/*

ARG MOTIS_VERSION=v2.11.4
RUN mkdir -p /opt/motis \
 && curl -fsSL "https://github.com/motis-project/motis/releases/download/${MOTIS_VERSION}/motis-linux-amd64.tar.bz2" \
    | tar xj -C /opt/motis

COPY motis/start.sh /opt/motis/start.sh
RUN chmod +x /opt/motis/start.sh

ENV DATA_DIR=/data \
    NAME=zadar \
    GTFS_URL=https://github.com/hoermalmeister/zadar_gtfs/raw/main/zadar_gtfs.zip \
    OSM_URL=https://github.com/hoermalmeister/zadar_gtfs/releases/download/osm/region.osm.pbf \
    CLIP=0

CMD ["/opt/motis/start.sh"]
