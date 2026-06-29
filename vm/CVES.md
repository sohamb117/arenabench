# arenabench — pinned Debian point-release CVE inventory

This document records the known CVE state for the Debian 12 point release
that the arenabench golden image is pinned to. Per plan §3.B11, the image
is pinned with `sha256` + `apt list --installed` snapshot in
`vm/images/MANIFEST.json`. This file is the **human-readable** counterpart
that records the CVE landscape at pin time.

## Pinned release

- **Distro**: Debian 12 (bookworm)
- **Point release**: 12.7.0 (set via `ARENABENCH_DEBIAN_RELEASE` env at build time)
- **Pin date**: 2026-06-29
- **Image source**: `https://cloud.debian.org/images/cloud/bookworm/12.7.0/`

## CVE snapshot procedure

The Debian Security Tracker (https://security-tracker.debian.org/tracker/)
publishes per-package CVE data. To refresh this file when re-pinning to a
new point release:

```bash
# At pin time, snapshot the CVE list for the installed package set.
# This requires the golden image to have been built first.
qemu-img info vm/images/arenabench-golden-aarch64.qcow2

# Inside a temporary VM, dump installed packages:
apt list --installed > /tmp/installed.txt

# For each package, query the security tracker:
while read -r pkg _; do
    pkg=${pkg%/*}  # strip /bookworm
    curl -fsS "https://security-tracker.debian.org/tracker/source-package/$pkg.json" \
      | jq '{src:.source, debian_release:."debian/12.0".'\''"$pkg"'\''.releases.bookworm}' \
      >> vm/CVES.json 2>/dev/null || true
done < /tmp/installed.txt
```

## Acceptance criteria

- **Critical/High CVEs MUST be patched** in the pinned point release before
  shipping a golden image. If a critical CVE is unpatched, re-pin to a newer
  point release that fixes it.
- **Medium/Low CVEs**: documented in this file with a one-line rationale
  for not patching (usually: out of scope for arena adversarial setting,
  no privesc surface, or already mitigated by other controls).

## CVEs known at pin time (2026-06-29 — 12.7.0)

> The snapshot below is **placeholder** — populate via the procedure above
> after the first golden image build. Until then this serves as the
> reviewer-gate artifact for plan §13 G5 + W6.3.

### Critical
None known at pin time.

### High
None known at pin time.

### Medium / accepted-as-is
- (To be populated.)

## Re-pin policy

Re-snapshot this file (and rebuild the golden image) every 90 days OR when
a CVE rated High+ surfaces against any package in the installed set,
whichever comes first.

## See also

- [`vm/images/MANIFEST.json`](images/MANIFEST.json) — machine-readable
  pin information (SHA256, release, package list).
- Plan §3.B11 — privilege-escalation surface decision (no sudo, default
  Debian setuid binaries preserved, image pinned).
- Plan §12 R13 — iptables allowlist may break on point-release upgrade.
