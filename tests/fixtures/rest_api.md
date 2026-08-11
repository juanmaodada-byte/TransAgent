# REST API Reference — User Management

## Base URL

```
https://api.example.com/v1
```

## Authentication

All requests require an API key in the header:

```
Authorization: Bearer sk-xxxxxxxxxxxxxxxx
```

## Endpoints

### Create User

`POST /users`

Request body:

```json
{
  "username": "john_doe",
  "email": "john@example.com",
  "role": "developer",
  "metadata": {
    "team": "platform",
    "region": "us-east-1"
  }
}
```

Response `201 Created`:

```json
{
  "id": "usr_abc123",
  "username": "john_doe",
  "email": "john@example.com",
  "role": "developer",
  "created_at": "2024-03-15T10:30:00Z"
}
```

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| username | string | yes | - | Unique username (3-32 chars) |
| email | string | yes | - | Valid email address |
| role | string | no | "viewer" | One of: admin, developer, viewer |
| metadata | object | no | {} | Custom key-value pairs |

### Error Codes

| Status | Code | Meaning |
|--------|------|---------|
| 400 | `INVALID_EMAIL` | Email format is invalid |
| 409 | `USERNAME_TAKEN` | Username already exists |
| 429 | `RATE_LIMITED` | Too many requests |

### cURL Example

```bash
$ curl -X POST https://api.example.com/v1/users \
  -H "Authorization: Bearer sk-xxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"username": "jane_dev", "email": "jane@example.com", "role": "developer"}'
```

### Configuration File

The default config is at `~/.config/myapp/config.yaml`:

```yaml
api:
  base_url: https://api.example.com/v1
  timeout: 30s
  retries: 3
```
