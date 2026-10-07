# Publishing the lens

Going from the development build, which talks to FreeCAD on your own network,
to a build in Lens Explorer that anyone can install.

The difference between them is one thing: a published lens may only use
`wss://` and `https://`, so it cannot reach a plain local address. FreeCAD's
own server gets a real certificate by running a cloudflared tunnel in front
of itself.

What a user ends up doing: install the FreeCAD addon and cloudflared, install
the lens from Lens Explorer, press Share over the internet, and type the four
words FreeCAD shows into the glasses. No Lens Studio, no firewall rule, and
nobody hosting a server.

## 1. Nothing to host

The addon runs **cloudflared** itself and FreeCAD's own server ends up behind
`https://<four-words>.trycloudflare.com` with a certificate the glasses
already trust. No account, no domain, no server, and no model passing through
anyone else's machine.

Users install cloudflared alongside the addon:

```powershell
winget install Cloudflare.cloudflared
```

Then **Share over the internet** on the Holo-CAD toolbar, and the Report view
prints the four words to type into the lens.

The hostname is random, because a quick tunnel needs no account. It survives
FreeCAD restarting, since the tunnel is deliberately left running and the next
session adopts it, so the words are typed once per tunnel rather than once per
session. Stop sharing from the same toolbar button when you are done.

Wanting the words typed once and never again means a **stable** hostname, and
that needs an account somewhere: Tailscale Funnel is free and needs no domain,
Cloudflare named tunnels need a domain. Both work through the same setting;
see `docs/RELAY.md`.

A relay also still exists in `relay/` for the case where you would rather host
one server for everybody than have each user run a tunnel.

## 2. The lens asks for the words itself

`TunnelPairing` opens the keyboard when it has no hostname stored, accepts
the words however they are typed (pasted whole url, spaces instead of
hyphens, suffix included), and remembers them. Nothing to configure.

`bridgeUrl` is then set at runtime to
`wss://<words>.trycloudflare.com/ws`, so the value left in the inspector only
matters for a development build on the local network.

## 3. Project Settings

- **Experimental APIs: off.** `lensDescriptors` in the `.esproj` goes back to
  `[]`. With the tunnel in place nothing needs the insecure schemes.
- **Lens Icon** set under Distribution Settings. `py tools\make_lens_icon.py`
  writes a 320 x 320 PNG at `tools/lens_icon.png`.

## 4. Check against Snap's list

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

## 5. Test the published path before submitting

Worth doing with Experimental APIs already off, because that is the build
being reviewed.

1. In FreeCAD, press **Share over the internet** and wait for the words.
2. Send the lens to your own glasses from Lens Studio.
3. The lens asks for the words. Type them, without the suffix.
4. Press **Send to Spectacles**. The model should appear.

If it does not, the Report view in FreeCAD and the Logger in Lens Studio
between them say why. The usual causes are a word typed wrong and cloudflared
not being installed.

## What stays available afterwards

The addon serves on the local network at the same time, so a development lens
on the same Wi-Fi keeps working while a published one comes in through the
tunnel. The two builds differ only in the Experimental APIs checkbox and where
`bridgeUrl` points, and `wire_lens_scene.py` switches between them.
