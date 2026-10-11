# syntax=docker/dockerfile:1

# Event Listener: a long-lived poller, not a web server. It follows the mock
# chain, pairs TaskCreated with TaskFunded, and dispatches complete tasks.
#
# It reads the compiled contract ABIs from mocks/contracts/artifacts/ at
# startup; docker-compose.yml mounts that directory read-only because the
# artifacts are git-ignored build output produced by the deploy step.
FROM accion-base:latest

COPY common ./common
COPY event_listener ./event_listener

CMD ["python", "-m", "event_listener.runner"]
