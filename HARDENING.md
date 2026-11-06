# Hardening

This fork runs upstream's app with tighter defaults, for one owner on one machine. It
branched from upstream commit `69928d0`.

## What differs from upstream

| Change | Why | Where |
|---|---|---|
| This project's images are only built here and named `localhost/celine/*`. The registry settings are gone | Upstream's `docker compose up -d` asks Docker Hub for `celine/celine-*` before it builds. That namespace belongs to an unrelated account, so an image published there would run in place of the build, and one of them holds the Docker socket | `pull_policy: never` and the image names in `docker-compose.yml`, `--pull never` in `shellbox-manager/manager.sh`; `CELINE_IMAGES`, `CELINE_TAG` and `images.yml` removed |
| A firewall in front of the shellbox and the browser | Upstream leaves the LAN and the Docker host open to both. They now reach the app and the public internet only, and root in the box cannot change that | `egress/`, `shellbox-net` and `browser-net` in `docker-compose.yml` |
| The manager recreates a box that lost its firewall | Docker does not retry a box whose firewall was not up, and a box whose firewall restarted keeps a dead network with a green health check | `repair` in `shellbox-manager/manager.sh`, every minute |
| The shellbox loses `NET_RAW` and `MKNOD` | A packet socket would skip the firewall | `docker-compose.yml` |
| The browser has no capabilities, `no-new-privileges` and a seccomp filter | Upstream runs it with no filter at all. This is Docker's default profile for engine 29.7.2 plus `clone`, `unshare`, `setns` and `chroot`, which Brave's sandbox needs. `io_uring`, `bpf`, `keyctl` and `perf_event_open` stay blocked | `browser/seccomp.json`, `docker-compose.yml` |
| The browser container takes no code from the shared home | The shellbox writes that home. A planted native messaging host started outside Brave's sandbox, and a planted Python `.pth` ran inside `browserd`. Both were reproduced and both are now blocked | `browser/policies.json`, `PYTHONNOUSERSITE` in `browser/Dockerfile` |
| The model proxy refuses media addresses | A model server downloads an `image_url` itself, which would carry the box past its firewall. Media goes in as a `data:` URL | `_media_address` in `server/celine/api/llm_proxy.py` |
| The web tool connects only to the address it checked | httpx resolved the name a second time, so DNS rebinding could land a GET on the LAN | `_PublicOnly` in `server/celine/skills/web.py` |
| The guard starts on, in On mode | Upstream starts it off, and turning it on gives Watch, which only logs while every step runs. In On, a risky step waits for the judge and is refused if the judge breaks | `settings/features.py`, `settings/general.py`, `guard/rules.py` |
| A routine prompt the agent writes waits for your approval | It runs later with nobody watching, so it is how a hostile page would make itself permanent | `confirm` in `guard/rules.py` |
| The Discord and WhatsApp connectors do not start | Each logs in as your own account through an unofficial client, which can cost you the account. Logging out of Discord here leaves its token valid until you change your password | `.env.example` |
| The SearXNG image and the Docker CLI image are pinned by digest | SearXNG is the one service pulled instead of built by default, and the CLI image holds the Docker socket | `docker-compose.yml`, `shellbox-manager/Dockerfile` |
| Line endings are LF everywhere | The containers run these files, and Git for Windows would otherwise check them out with CRLF | `.gitattributes` |

## What this gives up

- The agent's shell and browser cannot reach your LAN or your PC directly. `EGRESS_ALLOW`
  in `.env` opens what you name, one port (`ADDRESS:PORT`) or a whole machine (`ADDRESS`).
  Models, ComfyUI, the vault and web search go through the app and are unaffected.
- No live packet capture or raw-socket tools in the shellbox: `tcpdump`, `tshark` capture,
  `nmap` SYN scans, `traceroute -I`. Reading a capture file and `ping` still work. This
  cannot be opened with an allowlist, because those sockets are what skip the firewall.
- A risky step now waits for the guard's judge, costs a model call, and can be refused. A
  wrong refusal is lifted with a pass from the owner, and Settings, Guard can set Watch.
- A routine the agent writes or edits waits for your approval, and with nobody there to
  answer it does not change.
- A program in the shellbox sends images to the models as `data:` URLs, not as addresses.
- Browser extensions that talk to a native program do not work.
- No prebuilt images: updates build here with `docker compose up -d --build`.

## First run

```sh
cp .env.example .env
docker compose up -d --build
docker compose exec -u celine celine cat /data/app/setup-code.txt
```

Open http://localhost:8766 and follow the setup, then:

1. Connect a local model, so chats and notes stay on this machine.
2. Check the decision model in the Guard step. With one model the setup picks it, and each
   risky step then costs a call on that model. Until one is picked, risky actions wait.
3. Give it accounts made for it, never your own email or vault.
4. Leave `CELINE_PUBLISH` unset, which keeps the app on loopback.

## From your phone

The app installs to a phone's home screen and sends push notifications, and it needs an
https address for both. Tailscale gives it one that only your tailnet can reach, and the
app stays on loopback:

```sh
tailscale serve --bg --https=443 http://127.0.0.1:8766
```

Then, in this order:

1. Settings, Security: add a password or a PIN. A passkey belongs to the app's address, so
   one made on localhost stops working when the address changes.
2. Settings, General, Network: set the address to `https://<machine>.<tailnet>.ts.net`.
3. On the phone, with Tailscale connected, open that address and log in with the password
   or the PIN. Add it to the home screen before turning on notifications.

This machine has to be on and running Docker. The firewalls drop tailnet addresses, so the
shellbox and the browser cannot use this path. The app logs one warning about
`x-forwarded-for`, and every device on this path counts as one client for the login limit.
`tailscale serve --https=443 off` takes the address down.

## If a box has no network

A box shares its firewall's network namespace, so if the firewall container restarts on
its own, the box loses its network. That is the safe direction. The manager recreates the
box within about a minute, for the shellbox and the browser alike. It runs with the
`shellbox` profile, so without that profile, or to do it at once:

```sh
docker compose up -d --force-recreate --no-deps shellbox browser
```

## What this does not stop

- The shellbox and the browser reach the public internet, so anything they can see can
  leave. That includes your router's own public address. `internal: true` on their networks
  closes all of it, along with web pages and package installs from those containers.
- The two share one home folder. Code in the shellbox can read the browser's cookies and
  load extensions into it. Two routes from there to running code in the browser container
  are closed, but there may be others, so treat the two containers as one zone of trust.
- The routines stay as upstream ships them, so `radar`, `world` and `daydream` read the web
  or follow her own interests with nobody watching. A hostile page can steer her there. To
  run without them, switch them off in the setup's Routines step.
- Shares are off by default. A shared page runs in your browser, not in the box, so its
  scripts can send requests to your LAN from your PC.
- The guard is a model, and a model can be fooled. The firewall, the capabilities and the
  seccomp filter are the boundaries that do not depend on one.
- A kernel bug reached from a box would land in Docker Desktop's VM. The browser's filter
  still allows user namespaces, because Brave's sandbox needs them. It is fixed to engine
  29.7.2's defaults and does not follow Docker updates.
- The app itself can reach the LAN, because model servers may live there. Its web tools
  refuse private addresses (`skills/web.py`).
- Third-party code still runs inside the shellbox: REA is pinned by version, Anatomy by
  commit, Node and opencode by hash. Base images are pinned by tag, and apt packages float.
- The audit behind this was mostly a pattern scan with the key files read by hand, so it
  can miss a subtle backdoor.

## Taking upstream changes

Nothing here updates itself. Read the changes before merging them:

```sh
git remote add upstream https://github.com/LumiWasTaken/celine-net.git
git fetch upstream
git diff 69928d0 upstream/master
```

Read the Dockerfiles, the compose files, the lockfiles, `config/` and `server/celine/guard/`
most closely, and update the commit named at the top of this file after a merge.
