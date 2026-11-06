# shellbox-manager: the shellbox's morning reset, inside the stack (manager.sh)
# this image holds the Docker socket, so it is pinned by digest
FROM docker:29.8.2-cli@sha256:35cfcf893c20f2931f4909151b5407aaaeab711277de7ce8b8f521f095d54c32
RUN apk add --no-cache tzdata
COPY --link --chmod=755 shellbox-manager/manager.sh /usr/local/bin/shellbox-manager
ENTRYPOINT ["shellbox-manager"]
