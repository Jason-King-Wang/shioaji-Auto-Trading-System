# Public Framework Boundary

Public framework:

- selection-provider interface
- broker-adapter interface
- quote-provider interface
- sizing, tick and calendar helpers
- dry-run order engine
- markdown / HTML reporting
- SQLite state model
- fake broker and mock providers

Private data that must stay out of version control:

- `.env`
- live broker credentials
- CA certificates
- real account identifiers
- real private trade history
- private `StockModelVault` notes and rules
