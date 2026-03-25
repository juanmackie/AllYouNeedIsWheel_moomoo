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

- Python 3.10+
- [Moomoo OpenD](https://www.moomoo.com/download/OpenAPI) gateway running locally or on a server
- Moomoo account with market data subscriptions for US options
- OPRA Options Real-time Quote card (free if total assets > $3,000)

## Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/yourusername/AllYouNeedIsWheel.git
   cd AllYouNeedIsWheel
   ```

2. Set up a virtual environment and install required dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
   *Note: The `run_api.py` script will automatically check and install all required dependencies from requirements.txt when run.*

3. Create your connection configuration file:
   ```bash
   cp connection.json.example connection.json
   ```

4. Edit `connection.json` with your Moomoo OpenD connection details:
   ```json
   {
       "host": "127.0.0.1",
       "port": 11111,
       "readonly": true,
       "account_id": "YOUR_MOOMOO_ACCOUNT_ID",
       "db_path": "options.db",
       "comment": "Default port for moomoo OpenD is 11111. Set readonly to true for safety during testing."
   }
   ```

## Moomoo OpenD Setup

Before running the application, you must have Moomoo OpenD running:

1. **Download OpenD** from [moomoo.com/download/OpenAPI](https://www.moomoo.com/download/OpenAPI)

2. **Start OpenD** and log in with your Moomoo ID and password

3. **Configure OpenD**:
   - IP: `127.0.0.1` (default for local)
   - Port: `11111` (default)
   - Log level as needed

4. **First-time setup**: Complete the API questionnaire and agreements in the OpenD interface

5. **Market data**: Ensure you have US options market data access (free if assets > $3,000, otherwise purchase OPRA quote card)

## Configuration

Two connection files can be maintained:
- `connection.json` - For paper trading (default, uses `TrdEnv.SIMULATE`)
- `connection_real.json` - For real-money trading (uses `TrdEnv.REAL`)

The key configuration parameters are:
- `host`: Usually "127.0.0.1" for local OpenD
- `port`: 11111 (default moomoo OpenD port)
- `readonly`: Set to `true` for simulation mode, `false` for live trading
- `db_path`: Path to the SQLite database file

### Environment Variables

- `MOOMOO_TRADING_PASSWORD`: Required for live trading. Set this environment variable to your trading password to unlock live order placement.

## Usage

### Starting the Development Server

```bash
# For paper trading (default)
python3 run_api.py
```

### Starting the Production API Server

```bash
# For real money trading
python3 run_api.py --realmoney
```

This will start the application on http://localhost:8000

By default, the server will run on port 8000 with 4 workers. You can change these settings with environment variables:

```bash
# Change port and worker count
PORT=8080 WORKERS=2 python3 run_api.py
```

### API Endpoints

- **Portfolio**:
  - `GET /api/portfolio/` - Get current portfolio positions and account data
  - `GET /api/portfolio/positions` - Get positions (filter with `?type=STK` or `?type=OPT`)
  - `GET /api/portfolio/weekly-income` - Get weekly option income from short options expiring this Friday

- **Options**:
  - `GET /api/options/otm?tickers=<ticker>&otm=<pct>` - Get OTM options by percentage
  - `GET /api/options/stock-price?tickers=<ticker>` - Get stock price(s)
  - `GET /api/options/expirations?ticker=<ticker>` - Get available expiration dates

- **Orders**:
  - `GET /api/options/pending-orders` - Get pending/processing orders
  - `POST /api/options/order` - Create a new order
  - `DELETE /api/options/order/<order_id>` - Delete an order
  - `PUT /api/options/order/<order_id>/quantity` - Update order quantity
  - `POST /api/options/execute/<order_id>` - Execute an order through moomoo
  - `POST /api/options/cancel/<order_id>` - Cancel a processing order
  - `POST /api/options/check-orders` - Check status of pending orders
  - `POST /api/options/rollover` - Create rollover orders

### Web Interface

The web interface consists of three main pages:

1. **Dashboard** (http://localhost:8000/): Overview of your portfolio and key metrics
2. **Portfolio** (http://localhost:8000/portfolio): Detailed view of all positions
3. **Rollover** (http://localhost:8000/rollover): Interface for managing option positions approaching strike price

## Project Structure

```
AllYouNeedIsWheel/
├── api/                      # Flask API backend
│   ├── __init__.py           # API initialization and factory function
│   ├── routes/               # API route modules
│   │   ├── portfolio.py      # Portfolio endpoints
│   │   └── options.py        # Options/trading endpoints
│   └── services/             # Business logic for API
│       ├── options_service.py
│       └── portfolio_service.py
├── core/                     # Core trading functionality
│   ├── __init__.py
│   ├── connection.py         # Moomoo OpenD connection handling
│   ├── currency.py           # Currency conversion utilities
│   ├── logging_config.py     # Logging configuration
│   └── utils.py              # Utility functions
├── db/                       # Database operations
│   ├── __init__.py
│   └── database.py           # SQLite database wrapper
├── frontend/                 # Frontend web application
│   ├── static/               # Static assets (CSS, JS)
│   │   ├── css/
│   │   └── js/
│   └── templates/            # Jinja2 HTML templates
├── logs/                     # Log files directory
├── app.py                    # Main Flask application entry point
├── run_api.py                # Production API server runner (cross-platform)
├── config.py                 # Configuration handling
├── connection.json.example   # Example configuration template
├── requirements.txt          # Python dependencies
└── .gitignore                # Git ignore rules
```

## Development

### Adding New Features

1. For backend changes, add routes in `api/routes/` and implement business logic in `api/services/`
2. For frontend changes, modify the templates in `frontend/templates/` and static assets in `frontend/static/`
3. For database changes, update the schema and queries in `db/database.py`

### Database

The application uses SQLite for order storage. The database schema includes:
- `orders` - Tracks option orders with status, execution details, and Greeks
- `recommendations` - Historical recommendations

Migrations run automatically on startup to handle schema updates.

## Troubleshooting

### Connection Issues

- Ensure Moomoo OpenD is running and logged in
- Verify the correct port (default: 11111)
- Check that no other application is using the same OpenD connection
- Confirm you have the right market data subscriptions for US options

### Common Errors

- "Failed to connect to moomoo OpenD": OpenD is not running or not logged in
- "No market data permissions": You need OPRA options data subscription (free if assets > $3,000)
- "Failed to unlock trade": Set `MOOMOO_TRADING_PASSWORD` environment variable for live trading
- "ModuleNotFoundError: No module named 'fcntl'": Windows-specific. The script will automatically install waitress as an alternative to gunicorn, or install manually with `pip install waitress`

## Security Notes

- Never commit `connection_real.json` to version control (it's in `.gitignore`)
- Always use `readonly: true` during development to use simulation mode
- Store `MOOMOO_TRADING_PASSWORD` as an environment variable, never in config files
- Use caution when running with the `--realmoney` flag as real trades can be executed

## License

[Apache License 2.0](LICENSE)

## Acknowledgments

- [Moomoo OpenAPI](https://openapi.moomoo.com/moomoo-api-doc/en/intro/intro.html) for market data and trading API
- [Flask](https://flask.palletsprojects.com/) for the web framework
- [Gunicorn](https://gunicorn.org/) for WSGI HTTP server
- [Waitress](https://docs.pylonsproject.org/projects/waitress/) for Windows-compatible WSGI HTTP server
