# Privacy Policy

## Data Collected

The El Monte Research Atlas collects the following categories of personal data about researchers:

- **Names** (first, middle, last)
- **Affiliations** (institutional roles, departments, positions)
- **Titles** (academic ranks, job titles)
- **Biographies** (professional biographical text)
- **Publication records** (titles, years, citation counts, author positions)
- **Research topics** (topic and concept classifications derived from publications)
- **Curriculum vitae documents** (PDF/HTML snapshots of researchers' own
  public CV pages, when such a page exists and was fetched; served through
  the person profile and retained for 90 days — see Retention below)

## Sources

All data is collected from **publicly available sources**:

- University and departmental faculty directories (public web pages)
- The [OpenAlex](https://openalex.org) open research database API
- The [ORCID](https://orcid.org) public API

## Purpose

The purpose of processing this data is to provide a **research landscape visualization** — an institutional directory that helps users discover researchers, explore organizational charts, and understand research collaboration networks.

## Legal Basis

This processing is conducted under two legal bases:

1. **Public-record data** — the information is obtained from publicly accessible institutional directories and open bibliographic databases.
2. **Legitimate interest** — producing an institutional research directory serves the legitimate interests of the academic community.

## Data Retention

Snapshots of externally-fetched resources expire according to their kind:

| Source Kind | Retention |
|---|---|
| CVs (PDF/HTML snapshots) | 90 days |
| Directory profiles | 365 days |

Person records are retained while the research directory remains live.

## CV Opt-Out

If you are a researcher whose CV page is displayed here and you would prefer
it not to be cached or served, contact the address below (or request deletion
of your full record — the CV snapshot is removed with it). No CV is fetched
from behind authentication or login walls; only publicly posted faculty pages
are snapshotted, and the snapshot expires automatically after 90 days.

## Deletion

Data subjects may request deletion of their records. The deletion process is implemented via `scripts/admin/delete_person.py`. Run:

```
python3 -m scripts.admin.delete_person <person_id_or_orcid> [--dry-run]
```

to remove a person and all associated data from the database.

## Data Subject Rights

- **Access** — you may export all data held about you using the `personExport` GraphQL query (GDPR Art. 20 portability).
- **Erasure** — you may request deletion of your records via the admin deletion script described above.
- **Contact** — for privacy inquiries, contact: [chendyu@berkeley.edu](mailto:chendyu@berkeley.edu)

## Third-Party Attribution

This project integrates data from:

- [OpenAlex](https://openalex.org) — publication metadata and research topics. See [OpenAlex Terms of Service](https://openalex.org/terms).
- [ORCID](https://orcid.org) — researcher identifiers. See [ORCID Terms of Use](https://info.orcid.org/terms-of-use/).

## Tracking, Analytics, and Cookies

This application does **not** use tracking, analytics, or cookies of any kind.
