# API Documentation for Aster Finance

[Aster Finance API Document](./aster-finance-api.md)

# Aster Finance API 文档

[Aster Finance API 文档](./aster-finance-api_CN.md)

## Delta-Neutral Volume Bot

This repository now includes a reference implementation of a delta-neutral volume
bot that interacts with the [Aster futures API](./aster-finance-api.md).
The bot opens offsetting positions across two accounts to safely generate
trading volume while keeping net exposure close to zero.

### Features

- REST client implementing the authentication workflow described in the
  official documentation, including leverage and margin management endpoints.
- Hedged trading cycles that open and close positions on paired accounts,
  tracking filled trades and commissions for each cycle.
- Persistent status file for terminal monitoring of cumulative volume and fees.
- Dry-run mode for safely testing the strategy without hitting the live API.

### Installation

1. Create and activate a Python 3.10+ environment.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

### Configuration

All runtime settings live in a single configuration file—no command-line
overrides are required. Copy the provided example and fill in the credentials
for your paired accounts:

```bash
cp bot-config.example.yaml my-config.yaml
```

Update the new file with your API keys and desired bot parameters. The
`bot-config.example.yaml` file is heavily documented and lists every supported
option, including:

- `symbol`, `order_quantity`, `leverage`, and `margin_type` for each cycle.
- Risk controls such as `target_volume`, `hold_seconds`, and `max_cycles`.
- Output settings such as `status_file` and `status_update_interval_minutes`
  (defaults to 60 minutes).

### Running the Bot

Start the bot from the terminal and point it at your configuration file. Logs
remain in the console so you can watch each hedge cycle as it completes.

```bash
python volume_bot.py run --config my-config.yaml -vv
```

The bot only writes to the status file at the configured interval (e.g. once
per hour by default). To inspect the most recent snapshot, use the status
helper:

```bash
python volume_bot.py status --status-file bot_status.json
```

### Testing

Run the automated tests with `pytest`:

```bash
pytest
```
