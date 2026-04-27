# Shioaji Auto Trading System

Interview-safe portfolio version of a SinoPac / Shioaji auto-trading execution framework.

This repository focuses on the execution layer: broker adapters, dry-run/live-order safety gates, order sizing, workflow state, reporting, and test coverage. The private stock-selection model and its rules are intentionally excluded.

## What is included

- `auto-trading/src`: Python execution framework and broker/provider abstractions.
- `auto-trading/tests`: Unit tests for workflow safety, selection providers, report rendering, and order loops.
- `auto-trading/docs`: Public architecture, safety, and adapter contracts.
- `auto-trading/examples`: Small mock CSV inputs for local experimentation.
- `obsidian-showcase`: A curated Obsidian-style documentation excerpt for model/execution boundaries.
- `obsidian-shioaji-vault`: Sanitized full Obsidian note package for the Shioaji execution system.

## What is excluded

- Real `.env` files, API keys, certificate files, and broker credentials.
- Real live-trading databases, SQLite state, private reports, and order logs.
- Private upstream stock-selection rules, AB model notes, candidate-pool logic, and daily private model outputs.
- Raw Obsidian app state such as `.obsidian/workspace.json`.

## Quick Start

```powershell
cd auto-trading
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python run.py --help
python -m unittest discover tests
```

Live submission is guarded by configuration and environment flags. This interview version is meant for architecture review and dry-run demonstrations, not for direct production trading.

## Architecture Boundary

The system is deliberately split into two sides:

- Private signal generation: outside this repository.
- Public execution framework: consumes sanitized final lists or mock inputs and turns them into validated trading workflows.

That separation is the main design point: it allows the execution framework to be reviewed without exposing proprietary selection rules or sensitive account data.
