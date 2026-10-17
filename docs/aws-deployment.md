# Deploy FloatChat on AWS with $200 of credits

Reviewed against official AWS documentation on 18 October 2026. No AWS resources
have been created by this repository or by the local verification commands.

FloatChat runs on **one Amazon Lightsail instance**. Docker Compose starts three
containers on it: Caddy (HTTPS on ports 80/443), the Dash dashboard (gunicorn) and
PostgreSQL. Only Caddy is reachable from the internet. The AI chat is optional and
calls Amazon Bedrock. This is a small portfolio deployment, not a high-availability
production service.

## What it costs, and how long $200 lasts

| Item | Monthly |
| --- | ---: |
| Lightsail Linux 2 GB bundle with public IPv4 (2 vCPU, 60 GB SSD, 3 TB transfer, half that in Mumbai) | $12 |
| Static IP, while attached to the instance | $0 |
| Hostname: free `<ip>.nip.io`, or your own domain | $0 + domain |
| Optional Bedrock inference, capped by the app's request limits (a spending target, not a price) | ≤ $5 |
| **Total** | **$12–17** |

Taxes, snapshots, excess transfer and any other AWS service you try add to this.
[Lightsail pricing](https://aws.amazon.com/lightsail/pricing/).

**Time is the real limit.** $200 covers $12/month for more than a year, but:

- New customers get **$100 at sign-up plus up to $100 more** for completing the
  activities in the *Explore AWS* widget on the console home page. If an activity
  creates resources, such as an EC2 instance or database, delete them afterwards.
- On the **Free plan**, which new accounts start on, the plan ends after **6 months**
  or when the credits run out, whichever comes first. AWS then suspends the account:
  the site goes offline, AWS keeps the data for 90 days, and erases it unless you
  upgrade.
- If you **upgrade to the Paid plan**, your remaining credits keep applying until
  **12 months after sign-up**. After that, or once the credits are used up, your card
  is billed normally.

In practice, the Free plan gives up to 6 months of hosting with no risk of a bill.
Upgrade before month 6 if you want about 12 months, and keep budget alerts on.
When you pick the bundle, the Lightsail console may show a *first 3 months free* badge
(one bundle per account, 750 hours/month). AWS's pages disagree on whether new
accounts still get it, so go by the console and your Free Tier usage page.
[Free Tier FAQ](https://aws.amazon.com/free/free-tier-faqs/),
[account plans](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html),
[Lightsail billing FAQ](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-frequently-asked-questions-faq-billing-and-account-management.html).

Don't use a GPU instance. The 2 GB host runs the app and the database. It can't run
a language model, and a GPU instance would use up the credits within days.

## Before you start

1. **Push this repository to GitHub.** The server clones
   `https://github.com/kartiks15/FloatChat.git`, so the Dockerfile, `compose.yaml`
   and `deploy/` must be committed and pushed. `.env` and `data/` are git-ignored
   and never go to GitHub. You upload the data separately in step 7.
2. Decide on a hostname. You don't need to buy a domain. Without one, the site gets
   `https://<static-ip-with-dashes>.nip.io`, a free wildcard DNS name that points at
   your IP, and Caddy still gets a real HTTPS certificate for it.

## Step-by-step deployment

### 1. Check credits and set a budget alert

1. In the AWS console, open **Billing and Cost Management → Credits**. Note your
   balance and expiry date, and which plan you're on (Free or Paid).
2. Open **Budgets → Create budget → Customize (advanced) → Cost budget**. Choose
   monthly, $15, and alert emails at 50%, 80% and 100%. Under **Advanced options**,
   untick **Credits** so the alerts track real usage, not usage after credits.
   Budget alerts arrive with a delay and don't cap spending.
   [AWS Budgets](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html).

### 2. Create the Lightsail instance

In the [Lightsail console](https://lightsail.aws.amazon.com/), choose **Create instance**:

- **Region:** the one closest to your visitors, for example Mumbai (`ap-south-1`)
  for India. Bedrock can be in a different region later.
- **Platform:** Linux/Unix → **OS Only** → **Ubuntu 24.04 LTS**.
- **Launch script:** click **+ Add launch script** and paste the whole of
  [`deploy/lightsail-launch.sh`](../deploy/lightsail-launch.sh). If your repository
  URL differs, edit `REPO_URL` first. On first boot the script adds 2 GB of swap,
  installs Docker Engine and Compose, and clones the repository to `~/FloatChat`.
- **SSH key:** keep the default key.
- **Networking:** **Dual-stack** (IPv4 + IPv6). The cheaper IPv6-only bundles can't
  clone from GitHub or reach most visitors.
- **Plan:** General purpose, **$12 USD** (2 GB RAM, 2 vCPU, 60 GB SSD).
- **Name:** `floatchat`, then **Create instance**.

New accounts are sometimes refused with *"your account can not create an instance
using this Lightsail plan size"*. The limit can differ by region: one new account
refused in Mumbai created the $12 plan in N. Virginia (`us-east-1`) straight away.
Otherwise pick the **$7 USD** plan (1 GB RAM, 2 vCPU, 40 GB SSD), and check the
launch script is still in its box after changing region or plan. Each Dash
worker measured about 60 MB after import, so the stack fits in 1 GB, and the launch
script's 2 GB of swap covers image builds and imports. Avoid the 512 MB plan. AWS
lifts the restriction on its own over time; you can upsize later from a snapshot.

The launch script needs about 3–5 minutes after the instance shows *Running*.

### 3. Attach a static IP

Open the instance → **Networking** → **Attach static IP**. Create one and attach it.
A static IP is free while attached and is billed when left unattached. Do this before
step 6, because attaching it changes the instance's public address.

### 4. Open HTTPS in the firewall

On the same **Networking** tab, under **IPv4 Firewall** → **Add rule** → **HTTPS (TCP 443)**.
Repeat under **IPv6 Firewall**. Keep HTTP (80), because Caddy needs it to obtain
certificates and to redirect to HTTPS. You can restrict SSH (22) to your own IP;
if you do, tick **Allow Lightsail browser SSH** so you can still get in when your
home IP changes. Never open PostgreSQL 5432, Dash 8050 or Ollama 11434.

### 5. Connect over SSH

**Easiest:** click the terminal icon on the instance card. This opens a browser SSH
session, which is enough for steps 6 and 8.

**From Windows**, which you need for uploading data in step 7: in Lightsail,
open **Account → SSH keys** and download the default key for your region. Save it as
`%USERPROFILE%\.ssh\lightsail-floatchat.pem`. Then in PowerShell:

```powershell
icacls "$HOME\.ssh\lightsail-floatchat.pem" /inheritance:r /grant:r "$($env:USERNAME):R"
ssh -i "$HOME\.ssh\lightsail-floatchat.pem" ubuntu@YOUR_STATIC_IP
```

The `icacls` line stops OpenSSH from rejecting the key with an
*unprotected private key file* error.

On the server, wait for the launch script to finish, then confirm Docker works:

```bash
cloud-init status --wait
docker compose version
```

If `cloud-init` reports `error`, read `sudo tail -50 /var/log/cloud-init-output.log`.
If `docker` says *permission denied*, you connected before the script finished.
Log out, connect again, and retry.

### 6. Start FloatChat

```bash
cd ~/FloatChat
bash deploy/make-env.sh
docker compose config --quiet
docker compose up --build -d --wait
```

`make-env.sh` writes `.env` with two new random database passwords, sets `DOMAIN`
and sets `COMPOSE_FILE`, so every plain `docker compose` command in this repo uses
the HTTPS setup. Without an argument it uses `<static-ip>.nip.io`. With a domain of
your own, first create a DNS **A record** pointing at the static IP, then run
`bash deploy/make-env.sh floatchat.example.com`. The passwords exist only in this
`.env`. Keep the file, and don't run `make-env.sh` again on a server that already has
a database. The first image build takes a few minutes.

`grep DOMAIN .env` shows your address. `docker compose logs proxy` should report
*certificate obtained successfully* within a minute or two.

### 7. Upload and import your Argo data

On your PC, in PowerShell from the repository folder:

```powershell
tar -czf "$env:TEMP\argo.tgz" --exclude "SD*" -C data argo
scp -i "$HOME\.ssh\lightsail-floatchat.pem" "$env:TEMP\argo.tgz" ubuntu@YOUR_STATIC_IP:~/FloatChat/
```

The `--exclude "SD*"` skips the synthetic profiles. Float 2902115 has synthetic
(`SD`) and core + BGC (`D`/`BD`) files for the same cycles, and importing both would
duplicate those observations. The current local dataset becomes a ~5 MB archive.

Back on the server:

```bash
cd ~/FloatChat
mkdir -p data && tar -xzf argo.tgz -C data && rm argo.tgz
docker compose run --rm etl --dir /data/argo --limit 20
docker compose run --rm etl --dir /data/argo
```

Look over the first 20 files in the dashboard before importing the rest. Re-running
the import replaces rows from the same source files, so it doesn't duplicate them.

### 8. Check it

```bash
curl --fail https://YOUR_DOMAIN/healthz
curl --fail https://YOUR_DOMAIN/readyz
free -h
docker stats --no-stream
```

Then open the site. Check that floats appear, that selecting one shows its trajectory,
and that clicking a marker opens its measurements. The dashboard's date ranges are
relative to the newest observation in your data, so historical files still show up.

## Updating the site

Commit and push from your PC, then on the server:

```bash
cd ~/FloatChat
git pull
docker compose up --build -d --wait
docker image prune -f
```

If `schema.sql` changed, back up the database first (see Maintenance below).

## Switching to your own domain later

Create a DNS A record pointing at the static IP. Change `DOMAIN=` in `~/FloatChat/.env`,
then run `docker compose up -d proxy`. Caddy gets the new certificate automatically.

## Open-weight inference

### Recommended hosted option: Qwen on Bedrock

The implementation uses boto3's **Converse** API and the open-weight Qwen model,
without the OpenAI SDK or OpenAI endpoints. Qwen3 32B is currently listed as
active; model ID `qwen.qwen3-32b-v1:0` supports Converse. Confirm access, pricing
and region in your account before enabling it. The Bedrock region doesn't have to
match the Lightsail region. If the console asks you to upgrade to the Paid plan to
use the model, decide that first (see the credits timeline above).
[AWS Qwen model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-qwen-qwen3-32b.html),
[Bedrock pricing](https://aws.amazon.com/bedrock/pricing/).

In the Bedrock console, configure access to this model and obtain a Bedrock API
key using AWS's documented process. On Lightsail, do not assume EC2 instance
roles are available. This Compose setup supports a Bedrock bearer token via the
environment. Restrict the issuing IAM identity to required model invocation,
set an expiry, rotate it, and keep it in the server's protected `.env`.
[Bedrock API key authentication](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html).

`make-env.sh` leaves these lines commented out at the end of `.env`. Uncomment
them and fill in the token:

```dotenv
AI_ENABLED=true
AI_PROVIDER=bedrock
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=qwen.qwen3-32b-v1:0
AWS_BEARER_TOKEN_BEDROCK=your-bedrock-token
AI_DAILY_REQUEST_LIMIT=20
AI_HOURLY_CLIENT_LIMIT=5
```

Recreate the app container after changing configuration:

```bash
docker compose up -d app
```

Each accepted question reserves **at most two inference calls**, with bounded
prompts and 700 output tokens per call, without SDK retries. Failed requests
still consume the local quota. Daily limits apply across both workers and all
visitors; hourly limits also apply to each IP. These are request caps, not
dollar caps. Keep the persistent quota volume and monitor actual Bedrock usage.

The first model call chooses one validated retrieval operation. Python executes
fixed, parameterized SQL with a read-only database account. The second call
explains the result. Unsupported questions are explicitly limited by these
operations; there is no arbitrary SQL agent or unrestricted statistical engine.

### Local option: Ollama

Install [Ollama](https://ollama.com/download), then:

```bash
ollama pull qwen3:4b
```

Set `AI_ENABLED=true`, `AI_PROVIDER=ollama`, `OLLAMA_MODEL=qwen3:4b`, and
`OLLAMA_BASE_URL=http://localhost:11434` for native Python. For Docker Desktop,
use `http://host.docker.internal:11434`. On Linux, the host's Ollama listener must
be reachable from the container network; keep it firewalled to that private
network. Never expose an unauthenticated Ollama endpoint publicly. The 4B model
is about 2.5 GB of weights before runtime/context memory; it will not fit beside
the application on the 2 GB Lightsail instance. For local experimentation,
start on a machine with at least 8 GB available RAM and measure latency.
[Ollama model](https://ollama.com/library/qwen3:4b),
[native chat API](https://docs.ollama.com/api/chat).

## Local Docker rehearsal (optional)

Install Docker with Compose **2.24.4 or later**. On a new checkout:

```bash
cp env.example .env
# Edit .env: set DIFFERENT long random POSTGRES_PASSWORD and APP_DB_PASSWORD.
# Leave COMPOSE_FILE commented out locally.
docker compose config --quiet
docker compose up --build -d --wait
curl --fail http://localhost:8080/healthz
curl --fail http://localhost:8080/readyz
```

Open `http://localhost:8080`. The initial database is empty; load actual data:

```bash
# Download using your native Python environment, if you do not already have data.
python download_argo.py --wmo 2902115 --type Sprof --out-dir data/argo
# Existing local data/ is also supported. Choose Sprof OR core+BGC per float.
docker compose run --rm etl --dir /data/argo --limit 20
# Remove --limit only after checking the first import.
docker compose run --rm etl --dir /data/argo
```

## Maintenance, backups and stopping charges

Create a database backup before updates, on the server in `~/FloatChat`:

```bash
mkdir -p backups
chmod 700 backups
docker compose exec -T db pg_dump -U floatchat_admin -d floatchat -Fc > backups/floatchat.dump
```

Keep a protected copy off the instance; a file on the same disk is not disaster
recovery. From Windows:

```powershell
scp -i "$HOME\.ssh\lightsail-floatchat.pem" ubuntu@YOUR_STATIC_IP:~/FloatChat/backups/floatchat.dump .
```

Test restores into a separate disposable database with `pg_restore`.
Automate backups only after choosing retention and storage costs.

Schema initialization runs only on an empty Postgres volume. For an existing
installation, back up first, then apply the non-destructive migration:

```bash
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U floatchat_admin -d floatchat < schema.sql
```

Rows imported by the old importer remain marked `legacy`. Re-importing the same
files does not identify and remove those old rows automatically. For a clean
portfolio dataset, import into a new database/volume and retain the old one
until you have verified the new results. Do not delete old data to migrate.

`docker compose down` preserves volumes. `down -v` destroys the database and
quota history: never use it as an update step. Changing a password in `.env`
does not rotate an existing Postgres role; rotate it in PostgreSQL too.

Stopping containers or stopping a Lightsail instance does not stop instance or
storage billing. To retire the deployment: export the data, then delete the
instance, any snapshots, and the static IP (an unattached static IP is billed), and
check the next bill. Do this before credits expire if you do not want paid usage.

## Verification status

Local unit tests cover dashboard startup, date filters, chart grid separation,
NetCDF QC/adjusted values, rollback handling, SQL operation validation, quota
concurrency and mocked Ollama/Bedrock requests. `requirements.lock` records the
resolved Python 3.12 environment. GitHub Actions is configured to test real
PostgreSQL ingestion/read-only access and build/start the Docker stack.

`deploy/lightsail-launch.sh` and `deploy/make-env.sh` pass `bash -n`, and
`make-env.sh` was exercised locally (nip.io and custom-domain modes, refusal to
overwrite). The data packaging command was run with Windows `tar` against the
local dataset. Docker was unavailable on the development machine, so the launch
script, Docker/Linux startup, HTTPS issuance, real model inference and the AWS
deployment remain release checks to run on the instance. No successful cloud
deployment or live model answer is claimed.
