# Publishing the lens

Going from the development build, which talks to FreeCAD on your own network,
to a build in Lens Explorer that anyone can install.

The difference between them is one thing: a published lens may only use
`wss://` and `https://`, so it cannot reach anybody's laptop directly. It
talks to the relay instead, and FreeCAD uploads to the same relay.

What a user ends up doing: install the FreeCAD addon, install the lens from
Lens Explorer, read the pairing code off the glasses, type it into FreeCAD.
No Lens Studio, no firewall rule, no address to enter.

## 1. Stand up the relay, once

[docs/RELAY.md](RELAY.md) has the deployment. You need a host, a domain and a
reverse proxy that obtains the certificate. When `curl https://your-relay/status`
returns JSON, it is ready.

This is a commitment rather than a step. Every model every user sends passes
through it, and while it is down every published lens is dead.

## 2. Bake the relay into the addon

In `freecad_addon/SpecsLink/specslink/settings.py`:

```python
DEFAULT_RELAY_URL = "https://your-relay.example.com"
```

That is the whole change. Relay sending switches itself on, and a user only
has to type the pairing code rather than the address as well.

## 3. Point the lens at the relay

```powershell
py tools\wire_lens_scene.py --delete
py tools\wire_lens_scene.py --relay your-relay.example.com
```

`bridgeUrl` becomes `wss://your-relay.example.com/lens`. Ctrl+S in Lens
Studio afterwards, because the MCP server cannot save.

## 4. Project Settings

- **Experimental APIs: off.** `lensDescriptors` in the `.esproj` goes back to
  `[]`. With the relay url in place nothing needs the insecure schemes.
- **Lens Icon** set under Distribution Settings. `py tools\make_lens_icon.py`
  writes a 320 x 320 PNG at `tools/lens_icon.png`.

## 5. Check against Snap's list

Snap reviews every Spectacles lens before it reaches Lens Explorer, usually
two to three business days.
[Publishing Your Spectacles Lens](https://developers.snap.com/spectacles/get-started/start-building/publishing-lens).

| Requirement | Where it stands |
| ----------- | --------------- |
| 60 FPS on device | not measured yet, use the Lens Performance Overlay |
| Published lens under 25 MB | the lens holds only scripts and two materials, so it should be far under, but check |
| Version visible at launch and logged | done, `LENS_VERSION` in `StatusPanel.ts`, shown until the first model arrives |
| Experimental APIs off | step 4 |
| Lens icon | step 4 |

Bump `LENS_VERSION` on every build you submit. It is what makes a bug report
from a stranger worth anything.

## 6. Test the published path before submitting

Worth doing with Experimental APIs already off, because that is the build
being reviewed.

1. Start the relay.
2. Send the lens to your own glasses from Lens Studio.
3. The status panel shows a pairing code.
4. In FreeCAD, Settings, Relay: type the code. The address is already filled
   in from step 2.
5. Press Send to Spectacles. The model should appear.

If it does not, `docs/RELAY.md` has the failure table. The usual causes are a
mistyped code and a proxy that is not passing WebSocket upgrades through.

## What stays available afterwards

The addon keeps serving locally at the same time, so a development lens on the
same Wi-Fi still works while the published one goes through the relay. The two
builds differ only in `bridgeUrl` and the Experimental APIs checkbox, and
`wire_lens_scene.py` switches between them in one command.
