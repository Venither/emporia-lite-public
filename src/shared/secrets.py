"""Fetches the Emporia login (email + password) from AWS Secrets Manager
at runtime — credentials are never hardcoded or stored in this repo."""

import json
import os

import boto3

_cache = {}


def get_emporia_credentials():
    """Fetch EMPORIA_EMAIL/EMPORIA_PASSWORD from the Secrets Manager secret
    named by the EMPORIA_SECRET_ARN env var. Cached at module scope so a warm
    Lambda execution environment only pays for one Secrets Manager call."""
    if "email" in _cache and "password" in _cache:
        return _cache["email"], _cache["password"]

    secret_arn = os.environ["EMPORIA_SECRET_ARN"]
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_arn)
    secret = json.loads(response["SecretString"])

    _cache["email"] = secret["EMPORIA_EMAIL"]
    _cache["password"] = secret["EMPORIA_PASSWORD"]
    return _cache["email"], _cache["password"]
