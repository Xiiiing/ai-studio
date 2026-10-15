# Systemd model authentication, without inline secrets

The optional `api_key_credential` model profile field names a credential loaded by **systemd** (for example `qwen38_primary`). It never contains a token or a filesystem path. Model profiles may use **either** `api_key_env` **or** `api_key_credential`, not both.

A system administrator can declare `LoadCredential=qwen38_primary:/path/to/preexisting/protected/token-file` on a service that consumes the model. Systemd mounts a read-only private credential for that service and provides `CREDENTIALS_DIRECTORY`. The provider reads only the approved credential name from that directory using `O_NOFOLLOW` and a bounded regular-file check, uses it in the model request header, then redacts the exact credential from logged response fields. The credential body must never appear in JSON config, command arguments, logs or API inventory.

The same credential declaration must be applied to both the web service (used for profile testing and UI model availability) and the worker (used for generation). Changing a model's authentication reference requires a fresh worker invocation; do not mutate a model service owned by another user.

The model transport preserves the existing `api_key_env` implementation. Credential names cannot contain slashes or relative path segments. Remote/LAN authenticated HTTP still requires HTTPS. An absent or unreadable credential fails closed; inventory exposes only whether it is available, not its contents.

This backend capability does **not** imply a user's primary model endpoint is reachable or compatible: check that the protected endpoint serves the requested exact model alias and validate a real schema-constrained request before activating the role binding. A text-only model must not be configured with `vision: true`.
