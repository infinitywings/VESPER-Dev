# Safe operation and vulnerability reporting

VESPER includes deliberately weak device-software targets and network attack mechanisms for authorized research. They are not suitable for household or production deployment. Run them only against synthetic fixtures you control, in an isolated Linux VM/container network; do not bridge them to a home LAN or expose prototype APIs to the Internet.

The offline quick start needs no network privileges or credentials. Dashboard defaults bind to loopback. Optional Docker/router scripts can request privileged mode, host networking, kernel modules and firewall changes; inspect them before running. They are not validated production hardening controls, and this repository does not assert calibrated physical RF behavior.

Keep tokens in local ignored environment files. Before sharing results, remove cloud device/location identifiers, callback URLs, credentials, private household activity, and personal network information. `.gitignore` is a safeguard, not a privacy audit.

For a suspected vulnerability or accidentally committed credential, contact Chenglong Fu at chenglong.fu@charlotte.edu. Do not post the credential value in a public issue. Revoke/rotate exposed credentials first; deleting a file in a later commit does not remove it from existing history.
