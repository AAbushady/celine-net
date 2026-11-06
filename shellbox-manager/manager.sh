#!/bin/sh
# shellbox-manager: the shellbox's morning reset (docs/DEPLOY.md "Shellbox").
#
# At SHELLBOX_RESET_AT (HH:MM in TZ, docker-compose.yml) it recreates the shellbox
# service, which throws away everything outside its home. The reset is announced on the
# intercom with the nightly restart (docs/DEPLOY.md "Announcing a restart"). It drives
# docker compose through the Docker socket, on the stack folder (read-only at /stack),
# under the stack's own project name.
#
# Before the reset it keeps a snapshot of its home, one folder per day
# (shellbox-snapshots/<date>, out of its reach): a hardlink copy of the previous
# snapshot (cp -al), then rsync -a --delete from its home, so a file that did not change
# costs nothing. A snapshot older than SHELLBOX_SNAPSHOT_KEEP_DAYS (default 7; 0 keeps
# every one) goes after the new one is made; a restore is by hand. The folder is beside its
# home: in the same volume when its home is a named volume (docker-compose.yml), next
# to the host folder when it is one (DEPLOY.md "Shellbox", the home disk, so the
# snapshots are not on it). SHELLBOX_SNAPSHOTS, a host folder, puts them there instead.
set -eu
if [ "${1:-}" != reset ] && [ "${1:-}" != repair ]; then
    at=${SHELLBOX_RESET_AT:?docker-compose.yml sets it}
    {
        echo "$((10#${at#*:})) $((10#${at%:*})) * * * shellbox-manager reset > /proc/1/fd/1 2>&1"
        echo "* * * * * shellbox-manager repair > /proc/1/fd/1 2>&1"
    } > /etc/crontabs/root
    echo "shellbox-manager: the shellbox resets at $at ($(date +%Z))"
    exec crond -f -d 8
fi
project=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$(hostname)")
compose() { docker compose -p "$project" --project-directory /stack "$@"; }
container_of() {
    docker ps -aq --filter "label=com.docker.compose.project=$project" \
        --filter "label=com.docker.compose.service=$1"
}

# A box joins its firewall's network namespace. If the firewall was not up when the box
# started, Docker does not retry the box. If the firewall restarted, the box keeps running
# with no network and its health check stays green. Either way the box is recreated once
# its firewall is healthy. This reads only what Docker reports, nothing from inside the
# box. A box stopped on purpose has no start error and stays stopped.
repair() {
    for service in shellbox browser; do
        box=$(container_of "$service")
        net=$(container_of "$service-net")
        if [ -z "$box" ] || [ -z "$net" ]; then continue; fi
        health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$net")
        if [ "$health" != healthy ]; then continue; fi
        running=$(docker inspect -f '{{.State.Running}}' "$box")
        failed=$(docker inspect -f '{{.State.Error}}' "$box")
        box_at=$(docker inspect -f '{{.State.StartedAt}}' "$box")
        net_at=$(docker inspect -f '{{.State.StartedAt}}' "$net")
        first=$(printf '%s\n%s\n' "$box_at" "$net_at" | sort | head -n 1)
        if { [ "$running" != true ] && [ -n "$failed" ]; } ||
           { [ "$running" = true ] && [ "$first" = "$box_at" ] && [ "$box_at" != "$net_at" ]; }; then
            echo "$(date '+%F %R') $service lost its firewall's network, recreating it"
            compose up -d --force-recreate --no-deps "$service"
        fi
    done
}
if [ "${1:-}" = repair ]; then
    repair
    exit 0
fi

snapshot() {
    box=$(compose ps -q shellbox)
    [ -n "$box" ] || return 0
    image=$(docker inspect -f '{{.Config.Image}}' "$box")
    home='{{range .Mounts}}{{if eq .Destination "/home/celine"}}{{.Name}}|{{.Source}}{{end}}{{end}}'
    mount=$(docker inspect -f "$home" "$box")
    name=${mount%%|*} source=${mount#*|}
    snaps=${SHELLBOX_SNAPSHOTS:-}
    if [ -n "$name" ]; then
        # a named volume: its home is its shellbox/ (docker-compose.yml)
        set -- -v "$name:/data"
    elif [ -n "$source" ]; then
        # a host folder (no name): the snapshots go next to it
        set -- -v "$source:/data/shellbox:ro"
        snaps=${snaps:-$(dirname "$source")/shellbox-snapshots}
    else
        echo "no mount on /home/celine"
        return 1
    fi
    [ -z "$snaps" ] || set -- "$@" -v "$snaps:/data/shellbox-snapshots"
    # the oldest day kept, by name; empty keeps every one
    keep=${SHELLBOX_SNAPSHOT_KEEP_DAYS:-7} oldest=
    [ "$keep" -eq 0 ] || oldest=$(date -d "@$(($(date +%s) - keep * 86400))" +%F)
    # the shellbox image has rsync
    docker run --rm --pull never --network none "$@" --entrypoint sh "$image" -c '
        set -eu
        day=$1 s=/data/shellbox-snapshots
        mkdir -p "$s"
        last=$(cd "$s" && ls -1d [0-9]* 2>/dev/null | tail -n 1)
        if [ -n "$last" ] && [ "$last" != "$day" ]; then cp -al "$s/$last" "$s/$day"; fi
        rsync -a --delete /data/shellbox/ "$s/$day/"
        [ -n "$2" ] || exit 0
        for old in $(cd "$s" && ls -1d [0-9]*); do
            if [ "$old" \< "$2" ]; then rm -rf "${s:?}/$old"; fi
        done' sh "$(date +%F)" "$oldest"
}

if snapshot; then
    echo "$(date '+%F %R') home snapshot kept"
else
    echo "$(date '+%F %R') home snapshot failed, the reset goes on"
fi
compose up -d --force-recreate --no-deps shellbox
echo "$(date '+%F %R') shellbox reset"
