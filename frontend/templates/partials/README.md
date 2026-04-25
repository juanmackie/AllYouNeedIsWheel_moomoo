# Template Partials Structure

This directory contains reusable Jinja2 template components organized by function.

## Directory Structure

- `common/` — Components used across multiple pages
  - `navigation.html` — Main navigation bar
  - `footer.html` — Site footer
  - `positions_table.html` — Positions table (stock and option positions)

- `dashboard/` — Components specific to the dashboard page
  - `account_summary.html` — Account value, cash balance, and summary cards
  - `options_table.html` — Option opportunities table (calls/puts with OTM analysis)
  - `pending_orders.html` — Pending and filled orders tables

- `portfolio/` — Components specific to the portfolio page (currently empty, uses routes directly)

- `components/` — Smaller reusable UI components included in multiple partials

## Usage

Include components using Jinja2 syntax:

```jinja2
{% include "partials/path/to/component.html" %}
```

## Notes

- Dashboard partials rely on JavaScript files in `frontend/static/js/dashboard/`
- The positions table is shared between dashboard and portfolio pages via `common/`
- All partials inherit the base template `frontend/templates/base.html`
