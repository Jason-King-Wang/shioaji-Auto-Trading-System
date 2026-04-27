# Auto Trading Framework

Python package for a Shioaji-compatible auto-trading execution layer.

## Core Capabilities

- Broker adapter boundary for dry-run and live-order implementations.
- Selection-provider interface for manual CSV, generic CSV, or private daily signal exports.
- Safety gates for live order submission, guarded schedules, and no-backfill windows.
- Buy/sell loop orchestration with workflow status reporting.
- HTML/JSON report rendering for daily and weekly trading state.

## Safety Defaults

This repository does not include real credentials or production account data. Use `.env.example` as a template and keep the real `.env` file outside git.

Important environment flags:

```text
SINOPAC_DEFAULT_SIMULATION=1
SINOPAC_ALLOW_LIVE_SUBMIT=0
```

## Commands

```powershell
python run.py --help
python -m sinopac_auto_trading.cli --help
python -m unittest discover tests
```

## Public Boundary

The package can consume private signal exports, but the private signal rules are not part of this repository. Examples under `examples/` use mock data only.
