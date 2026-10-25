# Security and data handling

Do not publish credentials or personal conversations in issues. For a suspected vulnerability, use GitHub's private vulnerability reporting if enabled; otherwise ask the maintainer for a private channel without sharing exploit details publicly.

Current deployment targets a local/self-hosted environment, with loopback-only published ports. It has not undergone an independent security audit. Model output and MCP content do not grant tool permissions. Approval, identity and conversation access must be rechecked by the server.

If a key is exposed, revoke/rotate it at the provider; removing a Git commit alone does not invalidate a credential. Database backups can contain conversations and password hashes; protect them like the original database. See [database upgrades](docs/07-database.md) for coordinated migration and recovery requirements.
