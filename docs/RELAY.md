# The relay

**Optional, and nobody runs one by default.** Holo-CAD works with no server
beyond the one inside FreeCAD: on the same Wi-Fi the lens talks to FreeCAD
directly and none of this applies.

This is here for two cases. Reaching your own glasses from outside your own
network, and anyone who wants to publish a lens to Lens Explorer and is
willing to host the server that requires.

Worth knowing before you set one up: **a relay you host only for yourself
buys you very little.** Its whole purpose is to be one fixed address with a
real certificate that a published lens can be built against. If you already
have a domain and a certificate, you could point a lens at your own machine
instead. And because a lens carries a single hostname, one published build
cannot point at each user's own relay, so self-hosting does not make a lens
publishable for anyone but its builder.

```
FreeCAD addon  --https POST /push/CODE-->  RELAY  <--wss /lens--  Lens
                                             |                      |
                                             +--- https GET /m/... -+
```

Both ends dial out, so nobody opens a port and nobody touches a firewall.

## Why it has to exist

A published lens may only use `wss://` and `https://`, which needs a
certificate for a hostname that resolves to a specific machine. A user's
laptop cannot have one, and a lens carries a single hostname anyway, so it
could not point at each user's machine even if they could. The relay is the
one fixed address with a real certificate that every lens can be built
against.

What that costs, stated plainly: **every model every user sends passes
through this server.** It holds them in memory, keeps the last three versions
per model, and forgets a pairing fifteen minutes after the last lens leaves.
Nothing is written to disk. Even so, anyone running it is handling other
people's CAD, and should say so.

## Pairing

The lens connects first and the relay gives it an eight character code, which
the lens displays. The user types that code into FreeCAD, under Settings,
Relay. The code is the room: a push carrying it reaches exactly the lenses
holding it.

The typing happens on the laptop keyboard rather than on the glasses, which is
the only reason this is tolerable. Codes avoid `0`, `O`, `1`, `I`, `L` and
vowels, so nothing is ambiguous to read off a headset and no code is ever an
unfortunate word.

A lens reconnecting sends its code back, so a pairing survives the glasses
sleeping without the user retyping anything.

## Running it

It needs Python 3.11 or newer and `aiohttp`, and it must be reachable over
real HTTPS. It does not terminate TLS itself; put a reverse proxy in front,
which is also the easiest way to get a certificate.

```bash
python3 -m venv .venv
.venv/bin/pip install -r relay/requirements.txt
.venv/bin/python relay/relay.py --public-base https://relay.example.com --port 8080
```

`--public-base` is required and goes into every model url, so it must be the
address the glasses will actually use. Get it wrong and the lens will be told
to fetch from somewhere that does not exist.

With Caddy in front, which obtains and renews the certificate on its own:

```
relay.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

As a service, so it comes back after a reboot:

```ini
[Unit]
Description=Holo-CAD relay
After=network.target

[Service]
ExecStart=/opt/holo-cad/.venv/bin/python /opt/holo-cad/relay/relay.py \
          --public-base https://relay.example.com --port 8080
Restart=always
User=holocad

[Install]
WantedBy=multi-user.target
```

Check it with `curl https://relay.example.com/status`, which reports uptime,
rooms, connected lenses and bytes held.

## Limits it enforces

Deliberately strict, because it is public.

| Limit | Value | Why |
| ----- | ----- | --- |
| GLB size | 64 MB | one model should not fill the host |
| Per room | 192 MB | one pairing should not either |
| Versions kept | 3 per model | the lens only ever wants the newest |
| Push rate | 1 per 0.25 s per room | live mode can push hard; the addon paces itself to match and retries if refused |
| Idle room | dropped after 15 min | a pairing nobody is using is rubbish |

## Pointing the lens at it

In the lens, set `BridgeClient.bridgeUrl` to `wss://relay.example.com/lens`.
The relay replies with the pairing code, which `StatusPanel` displays.

Then, to publish: Experimental APIs **off**, a Lens Icon set
(`py tools\make_lens_icon.py` writes one), and submit. Snap reviews every
Spectacles lens before it reaches Lens Explorer, usually two to three business
days, against a checklist that also wants 60 FPS, under 25 MB, and the version
shown at launch.

## Turning it on in FreeCAD

Settings, Relay, for a published lens:

- **Also send through a relay**, on
- **Relay address**, `https://relay.example.com`
- **Pairing code**, whatever the glasses show

The addon keeps serving locally at the same time, so a lens on the same
network still works while a published lens goes through the relay. Uploads run
on a worker thread, and only the newest version of each model is kept in the
queue, so a burst of recomputes in live mode cannot make FreeCAD wait.

## When it does not work

| What you see | Cause |
| ------------ | ----- |
| `the relay has no lens with code ABC` in the Report view | the code was mistyped, or the lens closed and was given a new one |
| `could not reach the relay at ...` | wrong address, or the relay is down |
| the lens shows a code but nothing arrives | FreeCAD has relay sending off, or the address is empty |
| the lens connects then drops repeatedly | the proxy is not passing WebSocket upgrades through |
| models arrive but will not load | `--public-base` does not match the address the glasses can reach, or it is not https |
