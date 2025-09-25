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

Copy `bot-config.example.yaml` to a secure location and populate it with your
API keys for two hedge-mode enabled accounts.

```bash
cp bot-config.example.yaml my-config.yaml
```

Update the `bot` section with your preferred symbol, leverage, target volume,
and other runtime parameters.

### Running the Bot

Use the CLI entrypoint to start generating volume:

```bash
python volume_bot.py run --config my-config.yaml -vv
```

Add `--dry-run` to simulate behaviour without submitting orders. The bot
writes live metrics to the status file defined in the configuration. You can
inspect it with:

```bash
python volume_bot.py status --status-file bot_status.json
```

### Testing

Run the automated tests with `pytest`:

```bash
pytest
```
