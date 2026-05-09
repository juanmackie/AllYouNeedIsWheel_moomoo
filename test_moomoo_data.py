"""
Test Moomoo data availability for non-portfolio tickers.
Run this to verify whether Moomoo returns full option data (Greeks, IV)
for tickers NOT in your portfolio.
"""
import sys
import logging
import traceback

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Test tickers that are likely NOT in a typical portfolio
TEST_TICKERS = ['KO']  # Single ticker for faster test

def run_test():
    from api import create_app, get_service

    app = create_app()
    with app.app_context():
        svc = get_service('options')
        conn = svc._ensure_connection()

        if not conn:
            print("\n[FAIL] Could not establish Moomoo connection")
            print("   Make sure OpenD is running and connection is configured.")
            return

        print("\n[OK] Moomoo connection established\n")

        for ticker in TEST_TICKERS:
            print('=' * 60)
            print("  " + ticker)
            print('=' * 60)

            # 1) Stock price via Moomoo
            print("\n  [1] Stock price from Moomoo...")
            price = conn.get_stock_price(ticker)
            print("      Result: " + str(price))

            if price is None or price <= 0:
                print("      [FAIL] Moomoo failed - trying yfinance fallback...")
                price = svc.options_data._get_yfinance_price(ticker)
                print("      yfinance result: " + str(price))

            if price is None or price <= 0:
                print("      [FAIL] Both sources failed - skipping " + ticker + "\n")
                continue

            print("      [OK] Stock price: $" + str(round(price, 2)))

            # 2) Expiration dates
            print("\n  [2] Expiration dates from Moomoo...")
            ret, exps = conn.get_option_expiration_dates(ticker)
            if ret != 0:  # RET_OK is 0
                print("      [FAIL] Failed to get expirations: ret=" + str(ret))
                continue

            if exps is None or exps.empty:
                print("      [FAIL] No expirations returned")
                continue

            print("      [OK] Got " + str(len(exps)) + " expiration dates")
            print("      Columns: " + str(list(exps.columns)))
            # Find the expiration column
            if 'expiration_date' in exps.columns:
                exp_col = 'expiration_date'
            elif 'strike_time' in exps.columns:
                exp_col = 'strike_time'
            elif 'option_expiry_date' in exps.columns:
                exp_col = 'option_expiry_date'
            else:
                print("      [FAIL] No expiration column found")
                continue
            first_3 = list(exps[exp_col].head(3))
            print("      First 3: " + str(first_3))

            # 3) Option chain for first expiry (CALLs)
            # Convert to YYYYMMDD format for get_option_chain
            first_exp_raw = str(exps[exp_col].iloc[0])
            if '-' in first_exp_raw:
                first_exp = first_exp_raw.replace('-', '')
            else:
                first_exp = first_exp_raw
            print("      Using expiration: " + first_exp)
            print("\n  [3] Option chain for " + first_exp + " (CALLs)...")

            chain = conn.get_option_chain(ticker, first_exp, 'C')
            if not chain or not chain.get('options'):
                print("      [FAIL] No option chain from Moomoo - trying yfinance...")
                chain = svc.options_data._get_yfinance_option_chain(
                    ticker, first_exp.replace('-', ''), 'C'
                )
                if chain and chain.get('options'):
                    opt = chain['options'][0]
                    print("      [OK] yfinance fallback worked")
                    print("      Greeks: delta=" + str(opt.get('delta')) + ", gamma=" + str(opt.get('gamma')) +
                          ", theta=" + str(opt.get('theta')) + ", vega=" + str(opt.get('vega')))
                    print("      IV: " + str(opt.get('implied_volatility')))
                    print("      Bid/Ask: " + str(opt.get('bid')) + "/" + str(opt.get('ask')))
                else:
                    print("      [FAIL] Both Moomoo and yfinance failed")
                continue

            opt = chain['options'][0]
            print("      [OK] Got " + str(len(chain['options'])) + " options")
            print("      ALL FIELDS: " + str(opt))
            print("      Greeks: delta=" + str(opt.get('delta')) + ", gamma=" + str(opt.get('gamma')) +
                  ", theta=" + str(opt.get('theta')) + ", vega=" + str(opt.get('vega')))
            print("      IV: " + str(opt.get('implied_volatility')))
            print("      Bid/Ask: " + str(opt.get('bid')) + "/" + str(opt.get('ask')))
            print("      Volume: " + str(opt.get('volume')) + ", OI: " + str(opt.get('open_interest')))

            # 4) Test the full pipeline (_process_ticker_for_otm)
            print("\n  [4] Full pipeline test (_process_ticker_for_otm)...")
            try:
                portfolio_context = svc._get_portfolio_context()
                # Ensure cash balance and account value for PUT scoring
                if portfolio_context.get('cash_balance', 0) <= 0:
                    portfolio_context['cash_balance'] = 15000
                if portfolio_context.get('account_value', 0) <= 0:
                    portfolio_context['account_value'] = 100000
                result = svc.options_data._process_ticker_for_otm(
                    conn=conn,
                    ticker=ticker,
                    otm_percentage=10,
                    portfolio_context=portfolio_context,
                    expiration=None,
                    option_type=None
                )

                if 'error' in result:
                    print("      [FAIL] Pipeline error: " + str(result['error']))
                else:
                    n_calls = len(result.get('calls', []))
                    n_puts = len(result.get('puts', []))
                    print("      [OK] Pipeline success!")
                    print("      CALLs: " + str(n_calls) + ", PUTs: " + str(n_puts))
                    if n_calls > 0:
                        call = result['calls'][0]
                        print("      Sample CALL score: " + str(call.get('score', 'N/A')))
                        print("      Sample CALL IV: " + str(call.get('implied_volatility', 'N/A')))
                        print("      Sample CALL delta: " + str(call.get('delta', 'N/A')))
                        print("      Sample CALL theta: " + str(call.get('theta', 'N/A')))
                        print("      Sample CALL IV rank: " + str(call.get('iv_rank', 'N/A')))
                    if n_puts > 0:
                        put = result['puts'][0]
                        print("      Sample PUT score: " + str(put.get('score', 'N/A')))
                        print("      Sample PUT IV: " + str(put.get('implied_volatility', 'N/A')))
                        print("      Sample PUT delta: " + str(put.get('delta', 'N/A')))
                        print("      Sample PUT theta: " + str(put.get('theta', 'N/A')))
                        print("      Sample PUT IV rank: " + str(put.get('iv_rank', 'N/A')))
            except Exception as e:
                print("      [FAIL] Pipeline exception: " + str(e))
                traceback.print_exc()

            print()

        print('=' * 60)
        print("  SUMMARY")
        print('=' * 60)
        print("\nIf Moomoo returned Greeks (delta/gamma/theta/vega) above,")
        print("the fix is straightforward: use _process_ticker_for_otm()")
        print("for watchlist tickers instead of the yfinance-only path.")
        print("\nIf Moomoo failed but yfinance worked, the fix still helps")
        print("because _process_ticker_for_otm() has yfinance fallback built in.")
        print("\nIf both failed, check your Moomoo OpenD setup and market data subscriptions.")

if __name__ == '__main__':
    run_test()
