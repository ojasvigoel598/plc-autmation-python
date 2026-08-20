# Permanent public deployment

The two HMIs are served by one FastAPI process:

- **3D digital twin** → `/app`
- **2D P&ID HMI** → `/`

When you run `python run_scada.py` locally you get

```
http://127.0.0.1:8000/app
http://127.0.0.1:8000/
```

but **`127.0.0.1` only ever points at the machine you are on** — it is a
loopback address. A random person on the internet, or another device, can
never reach your laptop through it. For anyone to view the plant from
anywhere (even while your laptop is off), the backend must run on an
always-on public host.

The repo ships a `render.yaml` Blueprint and a Docker image (see
`Dockerfile`) so the whole thing deploys to **Render's free tier** in a few
clicks — no credit card required, and it stays up even when your laptop is
closed.

## Option A — Render (recommended, free, permanent)

1. Push this repo to GitHub (it already lives at
   `github.com/ojasvigoel598/plc-autmation-python`).
2. Go to <https://dashboard.render.com> and sign in (GitHub account works).
3. Click **New+ → Blueprint** and select this repository.
4. Render reads `render.yaml` and creates a free web service named
   `plc-scada-sim`. Wait a few minutes for the first build (it compiles the
   React twin with Node, then runs the Python backend).
5. When the deploy shows **Live**, your permanent public URLs are:

   | HMI | URL |
   |-----|-----|
   | 3D digital twin | `https://plc-scada-sim.onrender.com/app` |
   | 2D P&ID HMI | `https://plc-scada-sim.onrender.com/` |
   | REST/health | `https://plc-scada-sim.onrender.com/api/state` |

   (If you pick a different service name than `plc-scada-sim`, substitute it
   in the URLs. You can rename the service in the Render dashboard.)

6. Leak events and SQLite trends persist on a 1 GB disk mounted at
   `/app/data`, so history survives restarts and redeploys.

### Keeping it warm (no cold-start delay)

Render's free plan puts the service to sleep after ~15 minutes without
traffic; the first request after sleep wakes it in ~30–60 s. To make it
respond instantly every time, add a free **UptimeRobot** monitor:

1. Create a free account at <https://uptimerobot.com>.
2. **Add New Monitor** → type **HTTP(s)** → URL
   `https://plc-scada-sim.onrender.com/api/state` → interval **every 5
   minutes**.
3. The ping keeps the service awake, so the page loads immediately whenever
   anyone opens it.

### Updating after code changes

`autoDeploy: true` (set in `render.yaml`) means every push to the `main`
branch of the GitHub repo automatically rebuilds and redeploys — the public
URLs never change.

## Option B — Fly.io (always-on free allowance)

```bash
fly launch       # in the repo root; it will detect the Dockerfile
fly deploy
```

The app already honours the `PORT` env var Fly.io injects. Note Fly.io may
ask for card verification even on its free allowance.

## Option C — your own VPS / container host

```bash
docker build -t plc-scada-sim .
docker run -d -p 8000:8000 -p 502:502 -v scada-data:/app/data --restart unless-stopped plc-scada-sim
```

`--restart unless-stopped` brings the container back after reboots. Point a
domain or reverse proxy (Caddy / nginx / Cloudflare Tunnel) at port 8000 and
you have a permanent URL.

## Local auto-start (when the laptop is on)

The cloud deploy above is the only way to be reachable *while the laptop is
off*. If you also want the local server to come back automatically after a
reboot, run it as a service (e.g. `systemd` on Linux, Task Scheduler on
Windows) with:

```bash
python run_scada.py --host 0.0.0.0 --port 8000
```

Binding `0.0.0.0` lets other devices on your LAN reach it at
`http://<your-laptop-ip>:8000`, but does **not** make it public on the
internet.

## Security note

There is no authentication: anyone with the URL can operate the simulated
plant (it is a simulation, not real hardware). That is fine for a public
demo, but do not attach real hardware or sensitive data to a public
instance. See `SECURITY.md`.
