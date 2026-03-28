# AllYouNeedIsWheel (Moomoo Edition)

A financial options trading assistant for the "Wheel Strategy" powered by the [Moomoo OpenAPI](https://openapi.moomoo.com/moomoo-api-doc/en/intro/intro.html). View your portfolio, analyze options chains for cash-secured puts and covered calls, and manage orders through a local web dashboard.

<img width="1680" alt="Dashboard screenshot" src="https://github.com/user-attachments/assets/d27d525e-1fb4-4494-b5be-eba17e774322" />
<img width="1321" alt="Portfolio screenshot" src="https://github.com/user-attachments/assets/24634bbf-3110-46fa-85c4-b05301e11a88" />
<img width="1311" alt="Options screenshot" src="https://github.com/user-attachments/assets/0688ca0a-7fca-41fc-83b4-91881a2e9848" />
<img width="1309" alt="Rollover screenshot" src="https://github.com/user-attachments/assets/3e029e78-406c-44d4-b557-39b55c691f8a" />
<img width="1500" alt="Dashboard screenshot 2" src="https://github.com/user-attachments/assets/12a6539c-f74a-4d18-b868-ac7bef766dc8" />
<img width="1357" alt="Dashboard screenshot 3" src="https://github.com/user-attachments/assets/d9b2f57f-606d-4f4f-9d83-08b933ba71da" />

## Features

- **Portfolio Dashboard** — positions, cash balance, margin metrics, and weekly option income
- **Wheel Strategy Focus** — cash-secured puts and covered calls with OTM analysis
- **Options Rollover** — roll positions approaching strike price to later expirations
- **Order Management** — create, execute, and cancel option orders from the browser
- **OpenD Connection Status** — the web UI shows real-time OpenD connection and login state
- **Auto Launch** — optional one-click start that can open OpenD for you on Windows

## Prerequisites

| Requirement | Notes |
|---|---|
| Windows 10/11 | Required for the one-click launcher (`start_local.cmd`) |
| Python 3.10+ | The launcher creates a venv automatically |
| [Moomoo OpenD](https://www.moomoo.com/download/OpenAPI) | Runs locally alongside the app |
| Moomoo account | With US options market data subscriptions |
| OPRA Options Real-time Quote card | Free if total assets > $3,000 |

## Quick Start (Windows)

This is the recommended daily-use flow.

### 1. Clone

```bash
git clone https://github.com/juanmackie/AllYouNeedIsWheel_moomoo.git
cd AllYouNeedIsWheel_moomoo
```

### 2. Create connection.json

```bash
copy connection.json.example connection.json
```

The launcher will also create this file for you if it is missing.

### 3. Create .env

```bash
copy .env.example .env
```

Edit `.env` with your Moomoo credentials:

```env
MOOMOO_LOGIN=your-email@example.com
MOOMOO_PASSWORD=your-moomoo-password
MOOMOO_TRADING_PASSWORD=your-trading-password
MOOMOO_LANG=en
```

### 4. Start the app

Double-click `start_local.cmd`.

The launcher will:

1. Create a Python virtual environment (`.venv`) if it does not exist
2. Install Python dependencies when requirements change
3. Create `connection.json` from the example if it is missing
4. Optionally open OpenD (see below)
5. Start the Flask app on `http://127.0.0.1:8000/`
6. Open your browser automatically

### 5. Log in to OpenD

If OpenD is not already signed in, sign in there manually and complete any verification or captcha step. The web app stays running and shows a banner explaining the current OpenD state.

### 6. Open the app

If the launcher did not open a browser, visit `http://127.0.0.1:8000/`.

## OpenD Auto Launch

To have the launcher open OpenD for you, edit `connection.json`:

```json
{
  "host": "127.0.0.1",
  "port": 11111,
  "readonly": true,
  "auto_launch_opend": true,
  "opend_path": "C:\\Path\\To\\OpenD.exe",
  "db_path": "options.db"
}
```

- `auto_launch_opend` — set to `true` to start OpenD when you run the launcher
- `opend_path` — path to `OpenD.exe` or `FutuOpenD.exe` on your machine
- If `opend_path` is empty the launcher searches common install locations automatically

## OpenD Login

OpenD requires manual login and may show a graphic verification step (captcha). The app cannot automate this. When OpenD is running but not logged in, the dashboard shows a "LOGIN REQUIRED" banner and disables execute buttons until you complete the step in the OpenD window.

## Configuration

### connection.json fields

| Field | Default | Description |
|---|---|---|
| `host` | `127.0.0.1` | OpenD hostname |
| `port` | `11111` | OpenD port |
| `readonly` | `true` | Use `true` for paper trading (SIMULATE), `false` for live |
| `db_path` | `options.db` | Path to SQLite database |
| `auto_launch_opend` | `false` | Open OpenD when the launcher starts |
| `opend_path` | `""` | Path to the OpenD executable |

### connection_docker.json

Used only by `docker-compose.yml`. Points to `opend:11111` via the Docker network.

### connection_real.json (gitignored)

Live trading config. Not committed to version control.

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `MOOMOO_LOGIN` | Yes | Your Moomoo email or phone number |
| `MOOMOO_PASSWORD` | Yes | Your Moomoo login password |
| `MOOMOO_TRADING_PASSWORD` | For live | Your trading password |
| `MOOMOO_LANG` | No | Language: `en` (default) or `ch` |
| `CONNECTION_CONFIG` | No | Override config file (default: `connection.json`) |
| `PORT` | No | App port (default: `8000`) |

## API Endpoints

### System

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | App health check |
| `GET` | `/api/system/opend-status` | Live OpenD connection probe |

### Portfolio

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/portfolio/` | Portfolio summary |
| `GET` | `/api/portfolio/positions` | Positions (filter: `?type=STK` or `?type=OPT`) |
| `GET` | `/api/portfolio/weekly-income` | Weekly option income from short options expiring this Friday |

### Options

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/options/otm?tickers=<t>&otm=<pct>` | OTM options analysis |
| `GET` | `/api/options/stock-price?tickers=<t>` | Current stock prices |
| `GET` | `/api/options/expirations?ticker=<t>` | Available option expirations |

### Orders

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/options/pending-orders` | Pending/processing orders |
| `POST` | `/api/options/order` | Create an order |
| `DELETE` | `/api/options/order/<id>` | Delete an order |
| `PUT` | `/api/options/order/<id>/quantity` | Update order quantity |
| `POST` | `/api/options/execute/<id>` | Execute an order via moomoo |
| `POST` | `/api/options/cancel/<id>` | Cancel a processing order |
| `POST` | `/api/options/check-orders` | Sync order statuses |
| `POST` | `/api/options/rollover` | Create rollover orders |

## Web Pages

| URL | Description |
|---|---|
| `http://127.0.0.1:8000/` | Dashboard |
| `http://127.0.0.1:8000/portfolio` | Detailed portfolio view |
| `http://127.0.0.1:8000/rollover` | Option rollover manager |

## Project Structure

```
AllYouNeedIsWheel_moomoo/
├── api/                         # Flask API
│   ├── routes/                  #   route modules
│   └── services/                #   business logic
├── core/                        # Moomoo connection + helpers
│   ├── connection.py            #   OpenD probe and connection
│   ├── currency.py              #   currency conversion
│   ├── logging_config.py        #   logging setup
│   └── utils.py                 #   utility functions
├── db/                          # SQLite database
│   └── database.py
├── docker/opend/                # OpenD container (optional)
│   ├── Dockerfile
│   └── entrypoint.sh
├── frontend/                    # Web UI
│   ├── static/                  #   CSS + JS
│   └── templates/               #   Jinja2 templates
├── app.py                       # Flask app factory
├── run_api.py                   # WSGI server launcher
├── config.py                    # Config loader
├── start_local.cmd              # Windows one-click launcher
├── start_local.ps1              # PowerShell launcher logic
├── connection.json.example      # Example local config
├── connection_docker.json       # Docker Compose config
├── docker-compose.yml           # Optional containerized setup
├── Dockerfile                   # Web app container image
├── requirements.txt             # Python dependencies
└── .env.example                 # Example env file
```

## Docker (Optional)

The Docker Compose setup runs everything in containers. It is optional and best suited for experimentation rather than daily use because OpenD requires interactive login that is difficult in containers.

```bash
docker-compose up -d
```

This starts:
- `moomoo-opend` — OpenD gateway on port 11111
- `all-you-need-is-wheel` — web app on port 8000

## Troubleshooting

### OpenD not running

The dashboard shows an "OPEN OPEND" or "OpenD is not running" banner. Open the OpenD application and log in.

### OpenD login required

The dashboard shows "LOGIN REQUIRED". Complete the login or captcha step inside the OpenD window.

### Port 8000 already in use

Stop the existing process or start the app on a different port:

```bash
PORT=8001 python run_api.py
```

### No market data permissions

You need the OPRA Options Real-time Quote card (free if total assets > $3,000).

### Failed to unlock trade

Set `MOOMOO_TRADING_PASSWORD` in your `.env` file for live trading.

### App starts but pages show no data

Check the OpenD connection banner at the top of every page. Data only loads when OpenD is connected and logged in.

## Docker Commands (Reference)

```bash
docker-compose logs -f
docker-compose restart
docker-compose down
docker-compose up -d --build
docker-compose logs opend
```

## Security Notes

- Never commit `connection_real.json` to version control (it is in `.gitignore`)
- `.env` credentials are visible to anyone with access to the machine
- Store `MOOMOO_TRADING_PASSWORD` in your environment, not in config files
- Use `readonly: true` in `connection.json` unless you intentionally want live trading

## License

[Apache License 2.0](LICENSE)

## Acknowledgments

- [Moomoo OpenAPI](https://openapi.moomoo.com/moomoo-api-doc/en/intro/intro.html) — market data and trading API
- [Flask](https://flask.palletsprojects.com/) — web framework
