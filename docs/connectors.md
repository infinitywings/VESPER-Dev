# Optional platform connectors

The TAP engine provides internal trigger-action evaluation. SmartThings and Matter/Home Assistant are separate integration paths; an internal rule result should not be attributed to an external platform unless that platform actually participates in the experiment.

Install optional service dependencies with `python -m pip install -e '.[network]'`. The core demo does not start these services. To prepare local environment variables:

```bash
cp .env.example .env
# Edit .env locally; leave unused credentials empty.
set -a
source .env
set +a
```

Use test accounts and synthetic devices only. `.env`, logs, databases and captured traffic are ignored by Git. Do not put production credentials in YAML, terminal transcripts, issues or screenshots.

## SmartThings

`vesper/integrations/smartthings.py` supports a personal-access-token path using `SMARTTHINGS_TOKEN`. `schema_connector.py` and `scripts/smartthings_server.py` provide the distinct Schema webhook path using `SMARTTHINGS_CLIENT_ID` and `SMARTTHINGS_CLIENT_SECRET`, plus your own OAuth client configuration. An HTTPS callback origin must be registered with your developer application. A tunnel is optional; its credentials and URL are local configuration, not a repository default.

See the [official SmartThings developer documentation](https://developer.smartthings.com/docs/) for current account, scope, token and Schema registration requirements. The included webhook/server is a research prototype, not a production identity service. Do not expose it publicly or connect real household devices without a separate security review. Use the least privileges required for a disposable test environment.

## Matter / Home Assistant

`vesper/matter/bridge_client.py` addresses the VESPER REST bridge, while `vesper/matter/client.py` provides a separate WebSocket client path to `python-matter-server`. The Matter SDK is optional; install it according to [python-matter-server's upstream instructions](https://github.com/home-assistant-libs/python-matter-server). Home Assistant's integration is documented [here](https://www.home-assistant.io/integrations/matter/).

The upstream Python Matter Server repository now redirects to `matter-js/python-matter-server` and is archived. Its [migration notice](https://github.com/matter-js/python-matter-server) identifies 8.1.2 as the final Python release and directs users to a rewritten server. VESPER's inherited Python-client path is therefore a **legacy integration**, not a claim of compatibility with the current replacement. Migrating and validating that path is separate work.

The example configuration uses local endpoints: Home Assistant at `http://localhost:8123`, the REST bridge at `http://localhost:8484`, and the Matter server at `ws://localhost:5580/ws`. For the hub's Home Assistant REST path, `HA_TOKEN` is read from the process environment. These endpoints must be backed by your separately configured services; they are not started by `--demo`.

The Docker examples include privileged Linux networking and host-network services. Merely parsing the compose file is not proof that all services build or commission successfully. Connector latency, synchronization reliability and live platform automation were not evaluated in this release check.
