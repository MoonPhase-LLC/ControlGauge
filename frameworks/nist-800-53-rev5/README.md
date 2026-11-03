# NIST SP 800-53 Rev. 5 — Framework Data

This directory holds the NIST SP 800-53 Rev. 5 control catalog that ControlGauge imports at
startup (Sprint 3). The source is fixed by owner decision PD-012.

## Contents

| File | What it is |
|---|---|
| `framework.json` | Manifest: framework identity, plus the pinned catalog and profile files with their SHA-256, release version, upstream tag/commit/URLs, license notice and attribution |
| `oscal/NIST_SP-800-53_rev5_catalog-min.json` | The full catalog, NIST SP 800-53 Release 5.2.0 in OSCAL JSON |
| `oscal/NIST_SP-800-53_rev5_{LOW,MODERATE,HIGH,PRIVACY}-baseline_profile-min.json` | The four published baselines (OSCAL profiles) |
| `oscal/UPSTREAM_LICENSE.md` | The upstream repository's license, unmodified |

The `oscal/` files are **byte-for-byte copies** of the files in
[`usnistgov/oscal-content`](https://github.com/usnistgov/oscal-content) at tag
[`v1.5.0`](https://github.com/usnistgov/oscal-content/releases/tag/v1.5.0) (commit
`78650f02ad9321bb7b817846f8fbd4f2bcd620de`), path `nist.gov/SP800-53/rev5/json/`. Nothing in them
was edited. `.gitattributes` stops Git from converting their line endings, so their hashes stay
valid on every checkout.

## License and attribution

The content is a work of the United States Government: public domain in the United States, with
copyright waived worldwide under CC0 1.0 (see `oscal/UPSTREAM_LICENSE.md`). The app shows the
attribution from `framework.json` next to all control text, keeps the text verbatim, and states
that NIST does not endorse the product.

## How it is used

At startup the service checks each file's SHA-256 against `framework.json` before parsing it,
validates it, and imports it as an immutable catalog release (`service/README.md` "Framework
catalogs"; schema in `docs/DATABASE.md` §8). The importer is generic OSCAL: nothing in the code is
specific to this framework (`docs/ARCHITECTURE.md` §8). The baselines are reference data for
browsing; assessments are not scoped by them (`docs/OPEN_QUESTIONS.md` P-1).

## Adding a newer release

1. Copy the new upstream files into `oscal/`, unmodified (keep the old ones only if still needed).
2. Update `framework.json`: path, `sha256` (`sha256sum <file>`), `release_version` (must equal the
   catalog's own `metadata.version`), tag, commit and URLs.
3. On the next start the service imports the new release **beside** the old one and makes it
   current for new assessments. Existing assessments keep the release they were pinned to.

Never change a file in place while keeping the same `release_version`: the service refuses a
source whose hash differs from an already-imported release of that version.

## Do not

- Do not edit, reformat or "fix" the upstream files. Their text is official framework language.
- Do not add hand-written or product-generated text here, and never present it as NIST wording.
