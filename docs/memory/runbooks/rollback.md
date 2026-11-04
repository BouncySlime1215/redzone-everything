# Rollback

1. `.venv/bin/python scripts/deploy.py rollback`: back to the last good commit with its config and models.
2. Only config went bad: `.venv/bin/python scripts/deploy.py status` lists snapshots, then `.venv/bin/python scripts/deploy.py restore <id>`.
3. Check it came back: `.venv/bin/python scripts/deploy.py status` and `curl -s localhost:8778/api/state | head -c 300`.
4. Exit 5 (rollback failed) needs a person: `launchctl kickstart -k gui/$(id -u)/com.redzone.everything`, then tell the coordinator.
5. Write down what broke in [lessons.md](../lessons.md) (by PR).
