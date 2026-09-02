# Emporia Lite

Single-household Emporia power dashboard. Amps only (no watts), real per-circuit
current draw, true hourly max (not average). Serverless: 2 Lambda functions,
1 DynamoDB table, no EC2/ALB/NAT/S3. See
`docs/superpowers/specs/2026-08-20-emporia-lite-remodel-design.md` for the
full design.

## Local development

```bash
pip install -r requirements-dev.txt
pytest
```

## Deploy

```bash
sam build
sam deploy --guided   # first time only; then just `sam deploy`
```

Requires a Secrets Manager secret (ARN passed as the `EmporiaSecretArn`
parameter) containing:

```json
{"EMPORIA_EMAIL": "...", "EMPORIA_PASSWORD": "..."}
```
