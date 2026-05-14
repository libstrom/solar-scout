# Railway MCP Server

MCP-server för Railway. Hanterar projekt, deployments, loggar och miljövariabler.

## Verktyg

| Verktyg | Beskrivning |
|---------|-------------|
| `list_projects` | Lista alla projekt och tjänster |
| `get_deployments` | Senaste deployments för en tjänst |
| `get_logs` | Bygg- och deploy-loggar |
| `get_variables` | Visa miljövariabler |
| `set_variable` | Sätt en miljövariabel |
| `set_variables_bulk` | Sätt flera miljövariabler på en gång |
| `redeploy` | Trigga en omdeployment |
| `get_service_url` | Hämta publik URL för en tjänst |

## Installation

```bash
cd railway-mcp
pip install -r requirements.txt
```

## Konfiguration i Claude Code

Lägg till i `.claude/settings.json`:

```json
{
  "mcpServers": {
    "railway": {
      "command": "python",
      "args": ["/absolut/sökväg/till/railway-mcp/server.py"],
      "env": {
        "RAILWAY_API_TOKEN": "ditt-railway-token"
      }
    }
  }
}
```

## Railway API Token

1. Gå till `railway.app/account/tokens`
2. Skapa ett nytt token
3. Lägg in det i `RAILWAY_API_TOKEN`
