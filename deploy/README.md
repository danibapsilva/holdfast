# Deploying the demo

Holdfast runs as two systemd services on one small Linux server: the API (`holdfast-api`, the web
playground's backend, on `127.0.0.1:8710`) and the watcher (`holdfast-worker`). Settings live in one file
that only the service user can read. Everything here is for the Sepolia test network; the programs refuse
to start anywhere else.

## 1. Settings

Make the settings file and fill it in from [`.env.example`](../.env.example). The seed phrase and the
MongoDB URI are secrets: type them into the file on the server, never into a chat, a commit, or a shell
history.

```bash
mkdir -p ~/.config/holdfast && chmod 700 ~/.config/holdfast
cp .env.example ~/.config/holdfast/holdfast.env
chmod 600 ~/.config/holdfast/holdfast.env
nano ~/.config/holdfast/holdfast.env
```

In MongoDB Atlas, allow the server's public IP under Network Access, and use the "Connect, Drivers"
connection string as `HOLDFAST_MONGO_URI`.

## 2. Install

```bash
git clone https://github.com/danibapsilva/holdfast.git ~/holdfast
cd ~/holdfast
python3.12 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install py-solc-x   # only needed once, to compile tUSD for the deploy
```

## 3. Fund the treasury and deploy tUSD

The treasury is the seed's `m/44'/60'/1'/0/0` address: it deploys tUSD, mints it for the demo bot, and
pays gas.

```bash
.venv/bin/python scripts/show_treasury.py --env-file ~/.config/holdfast/holdfast.env
```

Paste the printed address into a Sepolia faucet (Google Cloud's Web3 faucet gives 0.05 SepoliaETH a day),
wait for it to arrive, then:

```bash
.venv/bin/python scripts/deploy_tusd.py --env-file ~/.config/holdfast/holdfast.env
```

Add the `HOLDFAST_TUSD_ADDRESS=...` line it prints to the settings file.

## 4. Run the services

```bash
sudo cp deploy/holdfast-api.service deploy/holdfast-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now holdfast-api holdfast-worker
curl -s http://127.0.0.1:8710/health
journalctl -u holdfast-api -u holdfast-worker -f
```

Both units cap their memory and CPU and run with a read-only system (`ProtectSystem=strict`), so a bug
can't disturb anything else on the machine.

## 5. Make the API public

The API only listens on `127.0.0.1`. Tailscale Funnel puts it on the internet over HTTPS:

```bash
sudo tailscale funnel --bg --https=8443 http://127.0.0.1:8710
tailscale funnel status
```

Use port **8443**, not 443. On the demo server, port 443 serves a private app to the tailnet only, and
it must stay that way. Funnel has to be allowed for the machine once in the Tailscale admin console (the
command prints the link if it isn't). The public address is `https://<machine>.<tailnet>.ts.net:8443`;
set it as the playground's API URL, and keep the site's origin in `HOLDFAST_ALLOWED_ORIGINS`.

## Updating

```bash
cd ~/holdfast && git pull && .venv/bin/pip install -e . && sudo systemctl restart holdfast-api holdfast-worker
```
