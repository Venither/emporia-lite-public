import os, json
from unittest.mock import patch, MagicMock
from shared import secrets


def test_get_emporia_credentials_fetches_and_parses_secret():
    secrets._cache.clear()
    fake_client = MagicMock()
    fake_client.get_secret_value.return_value = {
        "SecretString": json.dumps({"EMPORIA_EMAIL": "a@b.com", "EMPORIA_PASSWORD": "hunter2"})
    }
    with patch.dict(os.environ, {"EMPORIA_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:1:secret:x"}), \
         patch("shared.secrets.boto3.client", return_value=fake_client):
        email, password = secrets.get_emporia_credentials()

    assert (email, password) == ("a@b.com", "hunter2")
    fake_client.get_secret_value.assert_called_once_with(
        SecretId="arn:aws:secretsmanager:us-east-1:1:secret:x"
    )


def test_get_emporia_credentials_caches_after_first_call():
    secrets._cache.clear()
    fake_client = MagicMock()
    fake_client.get_secret_value.return_value = {
        "SecretString": json.dumps({"EMPORIA_EMAIL": "a@b.com", "EMPORIA_PASSWORD": "hunter2"})
    }
    with patch.dict(os.environ, {"EMPORIA_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:1:secret:x"}), \
         patch("shared.secrets.boto3.client", return_value=fake_client):
        secrets.get_emporia_credentials()
        secrets.get_emporia_credentials()

    assert fake_client.get_secret_value.call_count == 1
