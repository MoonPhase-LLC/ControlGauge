import type { CatalogRelease, ControlDetail, ReleaseSummary } from "@/service/types";

// A small synthetic catalog for frontend tests. Deliberately not shaped like
// any real framework (identifiers `X-1`, `Y-1`; families `XA`, `YB`), and
// with markup-like source text to prove official text renders inertly.

export const RELEASE_ID = "22222222-2222-4222-8222-000000000001";
const FAMILY_X = "33333333-3333-4333-8333-000000000001";
const FAMILY_Y = "33333333-3333-4333-8333-000000000002";
const BASELINE = "44444444-4444-4444-8444-000000000001";
export const CONTROL_X1 = "55555555-5555-4555-8555-000000000001";
export const CONTROL_X2 = "55555555-5555-4555-8555-000000000002";
export const CONTROL_Y1 = "55555555-5555-4555-8555-000000000003";

export const RELEASE_SUMMARY: ReleaseSummary = { id: RELEASE_ID, version: "2.0", title: "Example Catalog" };

const FRAMEWORK_REF = { id: "fw-test", name: "Test Framework", version: "Revision 1" };

export const RELEASE: CatalogRelease = {
  id: RELEASE_ID,
  framework: FRAMEWORK_REF,
  version: "2.0",
  title: "Example Catalog",
  is_current: true,
  imported_at: "2026-11-03T12:00:00Z",
  source: {
    format: "oscal-catalog-json",
    format_version: "1.1.2",
    last_modified: "2026-01-01T00:00:00Z",
    file_name: "catalog.json",
    sha256: "a".repeat(64),
    repository_url: null,
    release_url: "https://example.invalid/releases/v2",
    file_url: null,
    tag: "v2",
    commit: "b".repeat(40),
  },
  license_notice: "Example license notice.",
  attribution: "Control text: Example Standards Body. Displayed verbatim.",
  control_count: 3,
  enhancement_count: 2,
  ungrouped_control_count: 0,
  families: [
    {
      id: FAMILY_X,
      identifier: "XA",
      name: "Family X",
      control_count: 2,
      parts: [],
    },
    {
      id: FAMILY_Y,
      identifier: "YB",
      name: "Family Y",
      control_count: 1,
      parts: [{ name: "overview", label: null, title: "About Family Y", prose: "Family Y intro.", parts: [] }],
    },
  ],
  baselines: [
    {
      id: BASELINE,
      identifier: "core",
      title: "Core Selection",
      version: "2.0",
      member_count: 2,
      source_file_name: "core.json",
      source_sha256: "c".repeat(64),
      source_file_url: null,
    },
  ],
};

export const CONTROLS: ControlDetail[] = [
  {
    id: CONTROL_X1,
    identifier: "X-1",
    title: "First Requirement",
    status: null,
    release: {
      id: RELEASE_ID,
      version: "2.0",
      title: "Example Catalog",
      is_current: true,
      attribution: RELEASE.attribution,
      framework: FRAMEWORK_REF,
    },
    family: { id: FAMILY_X, identifier: "XA", name: "Family X" },
    baselines: ["core"],
    parts: [
      {
        name: "statement",
        label: null,
        title: null,
        prose: null,
        parts: [
          {
            name: "item",
            label: "a.",
            title: null,
            prose: "Review every {{ insert: param, x-1_p1 }} using *approved* tools.",
            parts: [],
          },
          {
            name: "item",
            label: "b.",
            title: null,
            prose: "Pick {{ insert: param, x-1_p2 }}; see [the guide](#res-1). <img src=x onerror=alert(1)>",
            parts: [],
          },
        ],
      },
      { name: "guidance", label: null, title: null, prose: "Guidance text.", parts: [] },
      { name: "assessment-method", label: null, title: "EXAMINE", prose: "Method text.", parts: [] },
    ],
    params: [
      { source_id: "x-1_p1", label: "review period", guidelines: ["A period is defined;"], select_how_many: null, choices: null },
      {
        source_id: "x-1_p2",
        label: null,
        guidelines: [],
        select_how_many: "one-or-more",
        choices: ["alpha", "beta every {{ insert: param, x-1_p1 }}"],
      },
    ],
    links: [
      {
        rel: "related",
        href: "#x-2",
        target: {
          kind: "control",
          id: CONTROL_X2,
          control_id: CONTROL_X2,
          part_label: null,
          identifier: "X-2",
          title: "Retired Requirement",
          citation: null,
          href: null,
        },
      },
      {
        rel: "reference",
        href: "#res-1",
        target: {
          kind: "resource",
          id: null,
          control_id: null,
          part_label: null,
          identifier: null,
          title: "Example Guide",
          citation: "Example Guide, *2026*.",
          href: "https://example.invalid/guide",
        },
      },
    ],
    enhancements: [
      {
        id: "66666666-6666-4666-8666-000000000001",
        identifier: "X-1(1)",
        title: "Automated Review",
        status: null,
        baselines: ["core"],
        parts: [{ name: "statement", label: null, title: null, prose: "Automate the review.", parts: [] }],
        params: [],
        links: [],
      },
      {
        id: "66666666-6666-4666-8666-000000000002",
        identifier: "X-1(2)",
        title: "Old Extension",
        status: "withdrawn",
        baselines: [],
        parts: [],
        params: [],
        links: [
          {
            rel: "incorporated-into",
            href: "#x-1_smt.a",
            target: {
              kind: "part",
              id: CONTROL_X1,
              control_id: CONTROL_X1,
              part_label: "a.",
              identifier: "X-1",
              title: "First Requirement",
              citation: null,
              href: null,
            },
          },
        ],
      },
    ],
  },
  {
    id: CONTROL_X2,
    identifier: "X-2",
    title: "Retired Requirement",
    status: "withdrawn",
    release: {
      id: RELEASE_ID,
      version: "2.0",
      title: "Example Catalog",
      is_current: true,
      attribution: RELEASE.attribution,
      framework: FRAMEWORK_REF,
    },
    family: { id: FAMILY_X, identifier: "XA", name: "Family X" },
    baselines: [],
    parts: [],
    params: [],
    links: [
      {
        rel: "moved-to",
        href: "#y-1",
        target: {
          kind: "control",
          id: CONTROL_Y1,
          control_id: CONTROL_Y1,
          part_label: null,
          identifier: "Y-1",
          title: "Moved Requirement",
          citation: null,
          href: null,
        },
      },
    ],
    enhancements: [],
  },
  {
    id: CONTROL_Y1,
    identifier: "Y-1",
    title: "Moved Requirement",
    status: null,
    release: {
      id: RELEASE_ID,
      version: "2.0",
      title: "Example Catalog",
      is_current: true,
      attribution: RELEASE.attribution,
      framework: FRAMEWORK_REF,
    },
    family: { id: FAMILY_Y, identifier: "YB", name: "Family Y" },
    baselines: ["core"],
    parts: [{ name: "statement", label: null, title: null, prose: "Y text.", parts: [] }],
    params: [],
    links: [],
    enhancements: [],
  },
];

export const BASELINE_ID = BASELINE;
export const FAMILY_X_ID = FAMILY_X;
export const FAMILY_Y_ID = FAMILY_Y;
