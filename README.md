# AllYouNeedIsWheel

AllYouNeedIsWheel is a financial options trading assistant that connects to Interactive Brokers (IB) to help analyze, visualize, and recommend option trading strategies. It retrieves portfolio data, analyzes options chains, and presents recommendations through a user-friendly web interface.

## Features

- **Portfolio Dashboard**: View your current portfolio positions, value, and performance metrics
- **Options Analysis**: Analyze option chains for any stock ticker
- **Trading Recommendations**: Get option trade recommendations
- **Interactive Web Interface**: Modern, responsive web application with data visualizations
- **API Integration**: Backend API to interact with Interactive Brokers
- **Order Management**: Create, cancel, and execute option orders through the dashboard

## Prerequisites

- Python 3.7+
- Interactive Brokers TWS (Trader Workstation) or IB Gateway
- IB account with market data subscriptions for options

## Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/yourusername/AllYouNeedIsWheel.git
   cd AllYouNeedIsWheel
   ```

2. Install required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Create your connection configuration file:
   ```bash
   cp connection.json.example connection.json
   ```

4. Edit `connection.json` with your Interactive Brokers connection details:
   ```json
   {
       "host": "127.0.0.1",
       "port": 7497,
       "client_id": 1,
       "readonly": true,
       "account_id": "YOUR_ACCOUNT_ID",
       "db_path": "options_dev.db",
       "comment": "Port 7496 for TWS, 7497 for IB Gateway. Set readonly to true for safety during testing."
   }
   ```

## Configuration

Two connection files can be maintained:
- `connection.json` - For paper trading (default)
- `connection_real.json` - For real-money trading

The key configuration parameters are:
- `host`: Usually "127.0.0.1" for local TWS/IB Gateway
- `port`: 7497 for IB Gateway paper trading, 7496 for TWS live trading
- `client_id`: Unique client ID (important if you have multiple connections)
- `readonly`: Set to `true` to prevent actual order execution (safer for testing)
- `db_path`: Path to the SQLite database file

## Usage

### Starting the Development Server

Run the Flask development server:

```bash
python app.py
```

This will start the application on http://localhost:5000

### Starting the Production API Server

For production use with Gunicorn (recommended for deployment):

```bash
# For paper trading (default)
python run_api.py

# For real money trading
python run_api.py --realmoney
```

By default, the server will run on port 5000 with 4 workers. You can change these settings with environment variables:

```bash
# Change port and worker count
PORT=8080 WORKERS=2 python run_api.py
```

### API Endpoints

- **Portfolio**: 
  - GET `/api/portfolio/` - Get current portfolio positions and account data

- **Options**:
  - GET `/api/options/<ticker>` - Get option chain for ticker
  - GET `/api/options/<ticker>/<expiration>` - Get option chain for specific expiration date

- **Orders**:
  - GET `/api/options/orders` - Get orders with optional filters
  - POST `/api/options/order` - Create a new order
  - DELETE `/api/options/order/<order_id>` - Cancel an order
  - PUT `/api/options/order/<order_id>` - Update an order status
  - POST `/api/options/execute/<order_id>` - Execute an order through TWS

- **Stock Data**:
  - GET `/api/stock/<ticker>` - Get stock price and basic data

### Web Interface

The web interface consists of four main pages:

1. **Dashboard** (http://localhost:5000/): Overview of your portfolio and key metrics
2. **Portfolio** (http://localhost:5000/portfolio): Detailed view of all positions
3. **Options** (http://localhost:5000/options?ticker=SYMBOL): Option chain analysis
4. **Recommendations** (http://localhost:5000/recommendations): Trade recommendations

### Mock Data

The application automatically uses mock data in the following scenarios:
- When the market is closed (outside of 9:30 AM - 4:00 PM ET, Monday-Friday)
- On weekends and market holidays
- When no connection to Interactive Brokers TWS/IB Gateway is available
- When API requests fail for any reason

This ensures that the application remains functional for development, testing, and demonstration purposes even when real-time market data is unavailable. Mock data includes:
- Portfolio summary with realistic account values
- Stock positions (primarily NVDA)
- Option positions with appropriate strikes and expiration dates
- Weekly option income calculations

Mock data is clearly labeled in the UI with an indicator showing "Mock Data" to avoid confusion with real trading data.

## Project Structure

```
AllYouNeedIsWheel/
├── api/                      # Flask API backend
│   ├── __init__.py           # API initialization and factory function
│   ├── routes/               # API route modules
│   ├── services/             # Business logic for API
│   └── models/               # Data models
├── core/                     # Core trading functionality
│   ├── __init__.py
│   ├── connection.py         # Interactive Brokers connection handling
│   ├── logging_config.py     # Logging configuration
│   └── utils.py              # Utility functions
├── db/                       # Database operations
│   ├── __init__.py
│   └── database.py           # SQLite database wrapper
├── frontend/                 # Frontend web application
│   ├── static/               # Static assets (CSS, JS)
│   └── templates/            # Jinja2 HTML templates
├── logs/                     # Log files directory
├── app.py                    # Main Flask application entry point
├── run_api.py                # Production API server runner with Gunicorn
├── config.py                 # Configuration handling
├── connection.json           # IB connection configuration (paper trading)
├── connection_real.json      # IB connection configuration (real money)
├── connection.json.example   # Example configuration template
├── options_dev.db            # Development database (SQLite)
├── requirements.txt          # Python dependencies
└── .gitignore                # Git ignore rules
```

## Development

### Adding New Features

1. For backend changes, add routes in `api/routes/` and implement business logic in `api/services/`
2. For frontend changes, modify the templates in `frontend/templates/` and static assets in `frontend/static/`
3. For database changes, update the schema and queries in `db/database.py`

### Database

The application uses SQLite for storage. Two database files are maintained:
- `options_dev.db` - For development/testing
- `options_prod.db` - For production use

## Troubleshooting

### Connection Issues

- Ensure TWS or IB Gateway is running and API connections are enabled
- Verify the correct port (7496 for TWS, 7497 for IB Gateway)
- Check that the client ID is not already in use
- Confirm you have the right market data subscriptions for options

### Common Errors

- "Socket Connection Broken": TWS/IB Gateway is not running
- "Client ID already in use": Another application is using the same client ID
- "No market data permissions": You need to subscribe to market data for the securities you're requesting

## Security Notes

- Never commit `connection_real.json` to version control (it's in `.gitignore`)
- Always use `readonly: true` during development to prevent accidental order execution
- Use caution when running with the `--realmoney` flag as real trades can be executed

## License

[Apache License 2.0](LICENSE)

## Acknowledgments

- [IB Insync](https://github.com/erdewit/ib_insync) for Interactive Brokers API integration
- [Flask](https://flask.palletsprojects.com/) for the web framework
- [Gunicorn](https://gunicorn.org/) for WSGI HTTP server
