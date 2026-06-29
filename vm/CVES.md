# arenabench — pinned Debian point-release CVE inventory

This document records the known CVE state for the Debian 12 point release
that the arenabench golden image is pinned to. Per plan §3.B11, the image
is pinned with `sha256` + `apt list --installed` snapshot in
`vm/images/MANIFEST.json`. This file is the **human-readable** counterpart
that records the CVE landscape at pin time.

## Pinned release

- **Distro**: Debian 12 (bookworm)
- **Build-ID**: 20260615-2510 (set via `ARENABENCH_DEBIAN_RELEASE` env at build time;
  env name kept for backward compat — the value is a build-ID, not semver)
- **Pin date**: 2026-06-29
- **Image source**: `https://cloud.debian.org/images/cloud/bookworm/20260615-2510/`

> Debian's cloud image distribution channel publishes under date-stamped
> build-ID directories (`YYYYMMDD-NNNN/`), NOT semver point releases. Browse
> https://cloud.debian.org/images/cloud/bookworm/ to pick a newer pin. The
> SHA256 captured in `vm/images/MANIFEST.json` is the actual immutable
> source-of-truth for the pin per plan §3.B11.

## CVE snapshot procedure

The Debian Security Tracker (https://security-tracker.debian.org/tracker/)
publishes per-package CVE data. As of build.sh + customize.sh, the installed
package list is captured automatically:

1. `customize.sh` writes `/etc/arenabench/installed-packages.txt` inside the
   VM at golden-build time (the locale-stable `apt list --installed` output
   with the "Listing..." header stripped and lines sorted).
2. `build.sh` extracts that file to `vm/images/installed-packages-<arch>.txt`
   via `virt-cat` (libguestfs) AFTER the qcow2 is finalized. `MANIFEST.json`
   records the snapshot location in the `installed_packages_source` field;
   on hosts without libguestfs the field records the deferral.

To refresh this file after re-pinning to a new point release:

```bash
# Step 1 — rebuild the golden image (writes installed-packages-<arch>.txt
# alongside the qcow2 when virt-cat is available).
./vm/golden/build.sh

# Step 2 — for each package in the snapshot, query the security tracker.
while read -r pkg _; do
    pkg=${pkg%/*}  # strip /bookworm
    curl -fsS "https://security-tracker.debian.org/tracker/source-package/$pkg.json" \
      | jq '{src:.source, debian_release:."debian/12.0".'\''"$pkg"'\''.releases.bookworm}' \
      >> vm/CVES.json 2>/dev/null || true
done < vm/images/installed-packages-aarch64.txt
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
