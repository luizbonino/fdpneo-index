# Running the index next to FDPneo

The FDPneo Index is FDPneo's FAIR Discovery (FDPneo ADR-0021, as amended):
a separate service that FDPneo servers announce themselves to. Nothing in
FDPneo changes to use it, and FDPneo's own evaluation stack stays as it is.

## How an FDPneo server announces itself

An FDPneo server pings its index targets (FDPneo ADR-0025) at start-up,
weekly, and when its metadata changes: `POST <index>/` with
`{"clientUrl": "<its root>"}`. Targets come from two places:

- `FDP_INDEX_PING_TARGETS`, comma-separated, read at start-up: for
  targets fixed by the deployment, such as a national index.
- The admin API `POST /fdp-api/index/targets` (and FDPneo's admin screen
  for it), effective at once and without a restart.
  `POST /fdp-api/index/ping` pings every target now and reports each
  result.

The announced `clientUrl` is the FDP's canonical root; set
`FDP_INDEX_PING_CLIENT_URL` only when the index must use another address.

## In production

Each runs on its own host or stack, with public addresses:

```sh
# on the FDPneo server
FDP_INDEX_PING_TARGETS=https://index.example.org
```

The index receives the ping, retrieves `https://fdp.example.org`, and
lists and harvests it according to its approval setting. The index's
**Register** page shows the same ping endpoint, and the entry's page shows
each check.

## On one machine, for a trial

Both stacks can run side by side on a laptop. Two things differ from
production, because the containers cannot reach each other at
`localhost`:

1. FDPneo pings the index through the host: Docker Desktop names it
   `host.docker.internal`.
   ```sh
   # FDPneo's environment
   FDP_INDEX_PING_TARGETS=http://host.docker.internal:8090
   FDP_INDEX_PING_CLIENT_URL=http://host.docker.internal:8000
   ```
   (`8000` is FDPneo's published port; use yours.)
2. The index must be allowed to fetch a private address, and the default
   deny list refuses `localhost` addresses only, so `host.docker.internal`
   passes:
   ```sh
   # deploy/.env of the index
   FDPINDEX_OUTBOUND_ALLOW_PRIVATE=true
   ```

Never set `FDPINDEX_OUTBOUND_ALLOW_PRIVATE` on a public index: anyone could
make it fetch addresses inside your network.

On Linux, add `extra_hosts: ["host.docker.internal:host-gateway"]` to the
FDPneo server and the index server, or put both on one Docker network and
use the containers' names.

## FDPneo's index mode

FDPneo servers do not run an index themselves; there is nothing to switch
off. A Java FAIR Data Point with `FDP_INDEX=true` does: see
[java-fdp.md](java-fdp.md).
