# Simple SinoPac Setup Flow

This project is SinoPac / Shioaji only.

1. Install Python dependencies.
2. Run `python run.py app`.
3. On first launch, fill in:
   - SinoPac API Key
   - SinoPac Secret Key
   - Person ID
   - CA certificate path
   - CA certificate password
4. Click `跑模擬審核測試` to run a Shioaji simulation login and simulation place-order test.
5. Use the top panel for normal buy/sell previews.
6. Use the model panel to apply a local model code such as `DEMO`.

The app defaults to simulation-only behavior and prints `submitted_to_broker: false`.
