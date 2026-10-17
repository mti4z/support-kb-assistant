---
title: Fixing VPN connection problems
---
## VPN will not connect
First confirm your home internet works by opening any public website. Then fully quit the VPN client and start it again. Make sure the client is on the latest version, because older versions are rejected by the gateway. If it still fails, restart the computer and try once more.

## Connected but cannot reach internal sites
This usually means the DNS settings were not applied. Disconnect, flush the DNS cache (on Windows run ipconfig /flushdns), then reconnect. Also check that you are not running another VPN or a proxy at the same time, since they conflict with the corporate tunnel.

## VPN is very slow
Close bandwidth heavy apps such as video streaming and large cloud syncs. Switch from Wi-Fi to a cable if possible. Choose the gateway closest to your location in the client settings. If speed is still poor, report the time and the gateway name in a ticket so the network team can investigate.
