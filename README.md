# AllYouNeedIsWheel (Moomoo Edition)

AllYouNeedIsWheel is a financial options trading assistant specifically designed for the "Wheel Strategy" that connects to the [Moomoo OpenAPI](https://openapi.moomoo.com/moomoo-api-doc/en/intro/intro.html). It helps traders analyze, visualize, and execute the wheel strategy effectively by retrieving portfolio data, analyzing options chains for cash-secured puts and covered calls, and presenting recommendations through a user-friendly web interface.

<img width="1680" alt="Screen Shot 2025-04-26 at 00 32 08" src="https://github.com/user-attachments/assets/d27d525e-1fb4-4494-b5be-eba17e774322" />
<img width="1321" alt="Screen Shot 2025-04-26 at 00 33 00" src="https://github.com/user-attachments/assets/24634bbf-3110-46fa-85c4-b05301e11a88" />
<img width="1311" alt="Screen Shot 2025-04-26 at 00 33 21" src="https://github.com/user-attachments/assets/0688ca0a-7fca-41fc-83b4-91881a2e9848" />
<img width="1309" alt="Screen Shot 2025-04-26 at 00 33 41" src="https://github.com/user-attachments/assets/3e029e78-406c-44d4-b557-39b55c691f8a" />
<img width="1500" alt="Screen Shot 2025-04-26 at 00 34 06" src="https://github.com/user-attachments/assets/12a6539c-f74a-4d18-b868-ac7bef766dc8" />
<img width="1357" alt="Screen Shot 2025-04-26 at 00 34 38" src="https://github.com/user-attachments/assets/d9b2f57f-606d-4f4f-9d83-08b933ba71da" />

## Features

- **Portfolio Dashboard**: View your current portfolio positions, value, and performance metrics
- **Wheel Strategy Focus**: Specialized tools for implementing the wheel strategy (selling cash-secured puts and covered calls)
- **Options Analysis**: Analyze option chains to find the best cash-secured puts and covered calls for any stock ticker
- **Option Rollover Management**: Tool for rolling option positions approaching strike price to later expirations
- **Interactive Web Interface**: Modern, responsive web application with data visualizations
- **Moomoo OpenAPI Integration**: Backend API connected to moomoo via OpenD gateway
- **Order Management**: Create, cancel, and execute wheel strategy option orders through the dashboard

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/)
- Moomoo account with market data subscriptions for US options
- OPRA Options Real-time Quote card (free if total assets > $3,000)

## Quick Start (Docker Compose)

The easiest way to run everything — Moomoo OpenD + the web app — in one command.

### 1. Clone and configure

```bash
git clone https://github.com/juanmackie/AllYouNeedIsWheel_moomoo.git
cd AllYouNeedIsWheel_moomoo
```

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` with your Moomoo credentials:

```env
MOOMOO_LOGIN=your-email@example.com
MOOMOO_PASSWORD=your-moomoo-password
MOOMOO_TRADING_PASSWORD=your-trading-password
MOOMOO_LANG=en
```

### 3. Start everything

```bash
docker-compose up -d
```

This starts two containers:
- **moomoo-opend** — Moomoo OpenD gateway on port `11111`
- **all-you-need-is-wheel** — The web app on port `8000`

### 4. Open the app

Visit http://localhost:8000

### 5. First-time setup

Before trading, log into your Moomoo account via the [moomoo app](https://www.moomoo.com/) or web to complete the API questionnaire and agreements. OpenD cannot do this step.

### Coolify Deployment

1. Go to Coolify → New Resource → **Docker Compose** (not Public Repository)
2. Point to this GitHub repo
3. Set environment variables in Coolify:
   - `MOOMOO_LOGIN` = your email/phone
   - `MOOMOO_PASSWORD` = your password
   - `MOOMOO_TRADING_PASSWORD` = your trading password
4. Deploy

The docker-compose.yml handles everything automatically.

## Manual Installation (without Docker)

If you prefer to run OpenD separately:

### 1. Prerequisites

- Python 3.10+
- [Moomoo OpenD](https://www.moomoo.com/download/OpenAPI) running locally or on a server

### 2. Install

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure

```bash
cp connection.json.example connection.json
```

Edit `connection.json`:

```json
{
    "host": "127.0.0.1",
    "port": 11111,
    "readonly": true,
    "db_path": "options.db"
}
```

### 4. Run

```bash
python3 run_api.py
```

Visit http://localhost:8000

## Moomoo OpenD Setup

If running without Docker, you must have Moomoo OpenD running:

1. **Download OpenD** from [moomoo.com/download/OpenAPI](https://www.moomoo.com/download/OpenAPI)
2. **Start OpenD** and log in with your Moomoo ID and password
3. **Configure OpenD**:
   - IP: `0.0.0.0` (for remote access) or `127.0.0.1` (local only)
   - Port: `11111` (default)
4. **First-time setup**: Complete the API questionnaire and agreements in the moomoo app
5. **Market data**: Ensure you have US options market data access (free if assets > $3,000)

## Configuration

### Connection files

- `connection.json` — Local development (connects to `127.0.0.1:11111`)
- `connection_docker.json` — Docker Compose (connects to `opend:11111` via Docker network)
- `connection_real.json` — Live trading config (not committed to git)

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `MOOMOO_LOGIN` | Yes (Docker) | Your Moomoo email or phone number |
| `MOOMOO_PASSWORD` | Yes (Docker) | Your Moomoo login password |
| `MOOMOO_TRADING_PASSWORD` | Yes (live) | Your trading password for live orders |
| `MOOMOO_LANG` | No | Language: `en` (default) or `ch` |
| `CONNECTION_CONFIG` | No | Connection config file (default: `connection.json`) |
| `PORT` | No | App port (default: `8000`) |

## API Endpoints

- **Portfolio**:
  - `GET /api/portfolio/` — Get current portfolio positions and account data
  - `GET /api/portfolio/positions` — Get positions (filter with `?type=STK` or `?type=OPT`)
  - `GET /api/portfolio/weekly-income` — Get weekly option income from short options expiring this Friday

- **Options**:
  - `GET /api/options/otm?tickers=<ticker>&otm=<pct>` — Get OTM options by percentage
  - `GET /api/options/stock-price?tickers=<ticker>` — Get stock price(s)
  - `GET /api/options/expirations?ticker=<ticker>` — Get available expiration dates

- **Orders**:
  - `GET /api/options/pending-orders` — Get pending/processing orders
  - `POST /api/options/order` — Create a new order
  - `DELETE /api/options/order/<order_id>` — Delete an order
  - `PUT /api/options/order/<order_id>/quantity` — Update order quantity
  - `POST /api/options/execute/<order_id>` — Execute an order through moomoo
  - `POST /api/options/cancel/<order_id>` — Cancel a processing order
  - `POST /api/options/check-orders` — Check status of pending orders
  - `POST /api/options/rollover` — Create rollover orders

## Web Interface

1. **Dashboard** (http://localhost:8000/): Overview of your portfolio and key metrics
2. **Portfolio** (http://localhost:8000/portfolio): Detailed view of all positions
3. **Rollover** (http://localhost:8000/rollover): Interface for managing option positions approaching strike price

## Project Structure

```
AllYouNeedIsWheel_moomoo/
├── api/                      # Flask API backend
│   ├── routes/               # API route modules
│   └── services/             # Business logic for API
├── core/                     # Core trading functionality
│   ├── connection.py         # Moomoo OpenD connection handling
│   ├── currency.py           # Currency conversion utilities
│   ├── logging_config.py     # Logging configuration
│   └── utils.py              # Utility functions
├── db/                       # Database operations
│   └── database.py           # SQLite database wrapper
├── docker/                   # Docker support
│   └── opend/                # Moomoo OpenD container
│       ├── Dockerfile
│       └── entrypoint.sh
├── frontend/                 # Frontend web application
│   ├── static/               # Static assets (CSS, JS)
│   └── templates/            # Jinja2 HTML templates
├── app.py                    # Main Flask application entry point
├── run_api.py                # Production API server runner
├── config.py                 # Configuration handling
├── docker-compose.yml        # Docker Compose orchestration
├── Dockerfile                # Web app container
├── connection_docker.json    # Docker networking config
├── connection.json.example   # Example local config
├── requirements.txt          # Python dependencies
└── README.md                 # This file
```

## Troubleshooting

### Connection Issues

- Ensure Moomoo OpenD is running and logged in (`docker-compose logs opend`)
- Verify the correct port (default: 11111)
- Confirm you have the right market data subscriptions for US options

### Common Errors

- "Failed to connect to moomoo OpenD": OpenD is not running or not logged in
- "No market data permissions": You need OPRA options data subscription (free if assets > $3,000)
- "Failed to unlock trade": Set `MOOMOO_TRADING_PASSWORD` environment variable for live trading

### Docker Commands

```bash
# View logs
docker-compose logs -f

# Restart everything
docker-compose restart

# Stop everything
docker-compose down

# Rebuild after code changes
docker-compose up -d --build

# View OpenD logs specifically
docker-compose logs opend
```

## Security Notes

- Never commit `connection_real.json` to version control (it's in `.gitignore`)
- Moomoo credentials in `.env` are visible to anyone with server access
- Store `MOOMOO_TRADING_PASSWORD` as an environment variable, never in config files
- Use caution when trading with real money

## License

[Apache License 2.0](LICENSE)

## Acknowledgments

- [Moomoo OpenAPI](https://openapi.moomoo.com/moomoo-api-doc/en/intro/intro.html) for market data and trading API
- [Flask](https://flask.palletsprojects.com/) for the web framework
- [Docker](https://www.docker.com/) for containerization
