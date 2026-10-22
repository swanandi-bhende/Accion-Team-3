# syntax=docker/dockerfile:1

# Event Listener on its own: a long-lived poller that follows the chain, pairs
# TaskCreated with TaskFunded, and records what it has seen. Compose does not run
# it. The `pipeline` service runs the same listener in-process and dispatches the
# tasks to inference, proving and settlement. This image stays for running the
# listener alone, for example as a monitor against another chain.
#
# It reads Team A's contract ABIs from team_a_assets/, which the run mounts read-only.
FROM base

COPY common ./common
COPY event_listener ./event_listener

CMD ["python", "-m", "event_listener.runner"]
