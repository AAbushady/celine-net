# The egress firewall for the shellbox and the browser (firewall.sh, docker-compose.yml).
FROM alpine:3.24.2@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
RUN apk add --no-cache iptables
COPY --chmod=755 egress/firewall.sh /usr/local/bin/firewall
ENTRYPOINT ["/usr/local/bin/firewall"]
