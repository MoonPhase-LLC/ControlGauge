//! Sprint 2 (S2-04) and Sprint 3 (S3-05): assessment, framework and
//! catalog requests made on the frontend's behalf, entirely in Rust, over the **same pinned,
//! identity-verified connection** `check_service_health` uses (D-026). The
//! frontend never sees the session token, host, port, or raw HTTP: each
//! `#[tauri::command]` in `lib.rs` takes typed, validated arguments, builds
//! a fixed path here, and returns either a typed, closed response schema or
//! a [`ServiceCallError`].
//!
//! Hardening carried over from `/health` (S1-04 review finding 3):
//!
//! - Response bodies are size-bounded while reading ([`MAX_API_RESPONSE_BYTES`]);
//!   request bodies are bounded before sending ([`MAX_API_REQUEST_BYTES`]).
//! - Every response is parsed into a closed schema (`deny_unknown_fields`).
//! - Path segments are never interpolated from free text: caller-supplied
//!   ids must be canonical lowercase UUIDs ([`is_canonical_uuid`]); paging
//!   values are integers; the one free-text query value (catalog search) is
//!   length/character-checked and percent-encoded ([`encode_query_value`]).
//! - Error responses from the service are accepted only in the service's
//!   documented error shape, with bounded field lengths and a constrained
//!   code alphabet, so nothing arbitrary is relayed to the WebView.
//!
//! A transport failure on the pinned connection is fail-closed exactly as
//! for `/health` (`pinned_http` module docs): the connection is revoked and
//! the supervisor moves to `Failed`; nothing redials.

use super::{debug_log, pinned_http, ReadyConnection, HTTP_REQUEST_TIMEOUT};
use bytes::Bytes;
use http_body_util::Full;
use hyper::{Method, Request, StatusCode};
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};

/// Upper bound on a response body. A full page of the largest assessments
/// (100 x (200-char name + 2000-char description), worst-case UTF-8) is
/// well under 2 MiB.
pub const MAX_API_RESPONSE_BYTES: usize = 2 * 1024 * 1024;
/// Upper bound on a request body this process will send.
pub const MAX_API_REQUEST_BYTES: usize = 32 * 1024;
pub const LIST_LIMIT_MAX: u32 = 100;
pub const CONTROL_LIMIT_MAX: u32 = 500;
pub const SEARCH_MAX_CHARS: usize = 100;
pub const LIST_OFFSET_MAX: u64 = 1_000_000;

const MAX_ERROR_FIELDS: usize = 32;
const MAX_ERROR_TEXT: usize = 300;
const MAX_ERROR_CODE: usize = 64;

/// Everything a command can report to the frontend. Serialized with a
/// `kind` tag; every variant but `ApiError` is a fixed value.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ServiceCallError {
    ServiceNotReady,
    ServiceNoLongerAlive,
    /// A caller-supplied argument was rejected in Rust before any request.
    InvalidArgument,
    RequestFailed,
    ResponseTooLarge,
    ResponseMalformed,
    Unauthorized,
    UnexpectedStatus {
        status: u16,
    },
    /// The service's own documented error (validation, not found, ...).
    ApiError {
        status: u16,
        code: String,
        message: String,
        fields: Vec<ApiFieldError>,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ApiFieldError {
    pub field: String,
    pub code: String,
    pub message: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ApiErrorEnvelope {
    error: ApiErrorBody,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ApiErrorBody {
    code: String,
    message: String,
    #[serde(default)]
    fields: Vec<ApiFieldError>,
}

// --- closed response schemas (mirror service/src/controlgauge_service/routes) ---

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ReleaseSummary {
    pub id: String,
    pub version: String,
    pub title: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct FrameworkSummary {
    pub id: String,
    pub name: String,
    pub version: String,
    pub source_reference: Option<String>,
    pub control_count: u64,
    pub current_release: Option<ReleaseSummary>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct FrameworkList {
    pub frameworks: Vec<FrameworkSummary>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct FrameworkRef {
    pub id: String,
    pub name: String,
    pub version: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Assessment {
    pub id: String,
    pub name: String,
    pub description: Option<String>,
    pub framework: FrameworkRef,
    pub catalog_release: Option<ReleaseSummary>,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct AssessmentList {
    pub assessments: Vec<Assessment>,
    pub total: u64,
    pub limit: u32,
    pub offset: u64,
}

// --- catalog (S3-05; mirrors routes/catalog.py) ---

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Part {
    pub name: String,
    pub label: Option<String>,
    pub title: Option<String>,
    pub prose: Option<String>,
    pub parts: Vec<Part>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Param {
    pub source_id: String,
    pub label: Option<String>,
    pub guidelines: Vec<String>,
    pub select_how_many: Option<String>,
    pub choices: Option<Vec<String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct LinkTarget {
    pub kind: String,
    pub id: Option<String>,
    pub control_id: Option<String>,
    pub part_label: Option<String>,
    pub identifier: Option<String>,
    pub title: Option<String>,
    pub citation: Option<String>,
    pub href: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Link {
    pub rel: Option<String>,
    pub href: String,
    pub target: LinkTarget,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct FamilyRef {
    pub id: String,
    pub identifier: String,
    pub name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Family {
    pub id: String,
    pub identifier: String,
    pub name: String,
    pub control_count: u64,
    pub parts: Vec<Part>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct BaselineSummary {
    pub id: String,
    pub identifier: String,
    pub title: String,
    pub version: Option<String>,
    pub member_count: u64,
    pub source_file_name: String,
    pub source_sha256: String,
    pub source_file_url: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ReleaseSource {
    pub format: String,
    pub format_version: Option<String>,
    pub last_modified: Option<String>,
    pub file_name: String,
    pub sha256: String,
    pub repository_url: Option<String>,
    pub release_url: Option<String>,
    pub file_url: Option<String>,
    pub tag: Option<String>,
    pub commit: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CatalogRelease {
    pub id: String,
    pub framework: FrameworkRef,
    pub version: String,
    pub title: String,
    pub is_current: bool,
    pub imported_at: String,
    pub source: ReleaseSource,
    pub license_notice: String,
    pub attribution: String,
    pub control_count: u64,
    pub enhancement_count: u64,
    pub ungrouped_control_count: u64,
    pub families: Vec<Family>,
    pub baselines: Vec<BaselineSummary>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ControlSummary {
    pub id: String,
    pub identifier: String,
    pub title: String,
    pub status: Option<String>,
    pub family_id: Option<String>,
    pub enhancement_count: u64,
    pub baselines: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ControlList {
    pub controls: Vec<ControlSummary>,
    pub total: u64,
    pub limit: u32,
    pub offset: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Enhancement {
    pub id: String,
    pub identifier: String,
    pub title: String,
    pub status: Option<String>,
    pub baselines: Vec<String>,
    pub parts: Vec<Part>,
    pub params: Vec<Param>,
    pub links: Vec<Link>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ReleaseRef {
    pub id: String,
    pub version: String,
    pub title: String,
    pub is_current: bool,
    pub attribution: String,
    pub framework: FrameworkRef,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ControlDetail {
    pub id: String,
    pub identifier: String,
    pub title: String,
    pub status: Option<String>,
    pub release: ReleaseRef,
    pub family: Option<FamilyRef>,
    pub baselines: Vec<String>,
    pub parts: Vec<Part>,
    pub params: Vec<Param>,
    pub links: Vec<Link>,
    pub enhancements: Vec<Enhancement>,
}

/// Command input for listing a release's controls. Ids must be canonical
/// UUIDs; the search text is bounded and percent-encoded.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct ControlQuery {
    pub family_id: Option<String>,
    pub baseline_id: Option<String>,
    pub search: Option<String>,
    pub limit: u32,
    pub offset: u64,
}

/// Command input for creating an assessment. Content validation is the
/// service's job (one source of truth for the rules); Rust bounds only the
/// serialized size.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct NewAssessment {
    pub name: String,
    pub description: Option<String>,
    pub framework_id: String,
}

#[derive(Serialize)]
struct CreateAssessmentBody<'a> {
    name: &'a str,
    description: Option<&'a str>,
    framework_id: &'a str,
}

/// True only for a canonical, lowercase, hyphenated UUID -- the exact form
/// the service issues. Anything else never becomes part of a request path.
pub fn is_canonical_uuid(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.len() == 36
        && bytes.iter().enumerate().all(|(i, b)| match i {
            8 | 13 | 18 | 23 => *b == b'-',
            _ => b.is_ascii_digit() || (b'a'..=b'f').contains(b),
        })
}

fn is_valid_code(code: &str) -> bool {
    !code.is_empty()
        && code.len() <= MAX_ERROR_CODE
        && code.bytes().all(|b| b.is_ascii_lowercase() || b == b'_')
}

fn is_valid_field_name(field: &str) -> bool {
    !field.is_empty()
        && field.len() <= MAX_ERROR_CODE
        && field
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'.')
}

fn parse_api_error(status: StatusCode, body: &[u8]) -> Option<ServiceCallError> {
    let envelope: ApiErrorEnvelope = serde_json::from_slice(body).ok()?;
    let ApiErrorBody {
        code,
        message,
        fields,
    } = envelope.error;
    let bounded = is_valid_code(&code)
        && message.chars().count() <= MAX_ERROR_TEXT
        && fields.len() <= MAX_ERROR_FIELDS
        && fields.iter().all(|f| {
            is_valid_field_name(&f.field)
                && is_valid_code(&f.code)
                && f.message.chars().count() <= MAX_ERROR_TEXT
        });
    bounded.then_some(ServiceCallError::ApiError {
        status: status.as_u16(),
        code,
        message,
        fields,
    })
}

async fn call<T: DeserializeOwned>(
    connection: &ReadyConnection,
    method: Method,
    path: &str,
    body: Option<Vec<u8>>,
    expected: StatusCode,
) -> Result<T, ServiceCallError> {
    if !connection.is_alive() {
        return Err(ServiceCallError::ServiceNoLongerAlive);
    }
    let mut builder = Request::builder()
        .method(method)
        .uri(path)
        .header("Host", format!("{}:{}", connection.host, connection.port))
        .header("Authorization", format!("Bearer {}", connection.token))
        .header("Accept", "application/json");
    let payload = match body {
        Some(bytes) => {
            if bytes.len() > MAX_API_REQUEST_BYTES {
                return Err(ServiceCallError::InvalidArgument);
            }
            builder = builder.header("Content-Type", "application/json");
            Bytes::from(bytes)
        }
        None => Bytes::new(),
    };
    let request = builder
        .body(Full::new(payload))
        .map_err(|_| ServiceCallError::InvalidArgument)?;

    let response = connection
        .pinned
        .send(request, MAX_API_RESPONSE_BYTES, HTTP_REQUEST_TIMEOUT)
        .await
        .map_err(|e| {
            debug_log("service api request failed", &e);
            match e {
                pinned_http::PinnedSendError::BodyTooLarge => ServiceCallError::ResponseTooLarge,
                _ => ServiceCallError::RequestFailed,
            }
        })?;

    if !connection.is_alive() {
        return Err(ServiceCallError::ServiceNoLongerAlive);
    }

    if response.status == expected {
        return serde_json::from_slice(&response.body)
            .map_err(|_| ServiceCallError::ResponseMalformed);
    }
    if response.status == StatusCode::UNAUTHORIZED {
        return Err(ServiceCallError::Unauthorized);
    }
    Err(parse_api_error(response.status, &response.body).unwrap_or(
        ServiceCallError::UnexpectedStatus {
            status: response.status.as_u16(),
        },
    ))
}

pub async fn list_frameworks(
    connection: &ReadyConnection,
) -> Result<FrameworkList, ServiceCallError> {
    call(connection, Method::GET, "/frameworks", None, StatusCode::OK).await
}

pub async fn list_assessments(
    connection: &ReadyConnection,
    limit: u32,
    offset: u64,
) -> Result<AssessmentList, ServiceCallError> {
    if !(1..=LIST_LIMIT_MAX).contains(&limit) || offset > LIST_OFFSET_MAX {
        return Err(ServiceCallError::InvalidArgument);
    }
    let path = format!("/assessments?limit={limit}&offset={offset}");
    call(connection, Method::GET, &path, None, StatusCode::OK).await
}

pub async fn get_assessment(
    connection: &ReadyConnection,
    id: &str,
) -> Result<Assessment, ServiceCallError> {
    if !is_canonical_uuid(id) {
        return Err(ServiceCallError::InvalidArgument);
    }
    let path = format!("/assessments/{id}");
    call(connection, Method::GET, &path, None, StatusCode::OK).await
}

pub async fn create_assessment(
    connection: &ReadyConnection,
    input: &NewAssessment,
) -> Result<Assessment, ServiceCallError> {
    let body = serde_json::to_vec(&CreateAssessmentBody {
        name: &input.name,
        description: input.description.as_deref(),
        framework_id: &input.framework_id,
    })
    .map_err(|_| ServiceCallError::InvalidArgument)?;
    call(
        connection,
        Method::POST,
        "/assessments",
        Some(body),
        StatusCode::CREATED,
    )
    .await
}

pub async fn get_catalog_release(
    connection: &ReadyConnection,
    id: &str,
) -> Result<CatalogRelease, ServiceCallError> {
    if !is_canonical_uuid(id) {
        return Err(ServiceCallError::InvalidArgument);
    }
    let path = format!("/catalog-releases/{id}");
    call(connection, Method::GET, &path, None, StatusCode::OK).await
}

/// Percent-encodes a query value: every byte outside RFC 3986's unreserved
/// set becomes `%XX`, so free text can never add parameters or path parts.
pub fn encode_query_value(value: &str) -> String {
    let mut out = String::with_capacity(value.len() * 3);
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_' | b'~') {
            out.push(char::from(byte));
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

/// Search text accepted for the catalog query: at most
/// [`SEARCH_MAX_CHARS`] characters and no control or line-separator
/// characters (the service applies the same rule).
fn is_valid_search(value: &str) -> bool {
    value.chars().count() <= SEARCH_MAX_CHARS
        && !value
            .chars()
            .any(|c| c.is_control() || c == '\u{2028}' || c == '\u{2029}')
}

/// The request path for a control listing, or `None` if any argument is
/// out of bounds.
pub fn control_list_path(release_id: &str, query: &ControlQuery) -> Option<String> {
    if !is_canonical_uuid(release_id)
        || !(1..=CONTROL_LIMIT_MAX).contains(&query.limit)
        || query.offset > LIST_OFFSET_MAX
    {
        return None;
    }
    let mut path = format!(
        "/catalog-releases/{release_id}/controls?limit={}&offset={}",
        query.limit, query.offset
    );
    for (name, value) in [
        ("family_id", &query.family_id),
        ("baseline_id", &query.baseline_id),
    ] {
        if let Some(id) = value {
            if !is_canonical_uuid(id) {
                return None;
            }
            path.push_str(&format!("&{name}={id}"));
        }
    }
    if let Some(search) = &query.search {
        if !is_valid_search(search) {
            return None;
        }
        if !search.trim().is_empty() {
            path.push_str("&q=");
            path.push_str(&encode_query_value(search));
        }
    }
    Some(path)
}

pub async fn list_catalog_controls(
    connection: &ReadyConnection,
    release_id: &str,
    query: &ControlQuery,
) -> Result<ControlList, ServiceCallError> {
    let path = control_list_path(release_id, query).ok_or(ServiceCallError::InvalidArgument)?;
    call(connection, Method::GET, &path, None, StatusCode::OK).await
}

pub async fn get_control(
    connection: &ReadyConnection,
    id: &str,
) -> Result<ControlDetail, ServiceCallError> {
    if !is_canonical_uuid(id) {
        return Err(ServiceCallError::InvalidArgument);
    }
    let path = format!("/controls/{id}");
    call(connection, Method::GET, &path, None, StatusCode::OK).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_uuid_check() {
        assert!(is_canonical_uuid("0f8fad5b-d9cb-469f-a165-70867728950e"));
        for bad in [
            "",
            "0F8FAD5B-D9CB-469F-A165-70867728950E",
            "0f8fad5bd9cb469fa16570867728950e",
            "0f8fad5b-d9cb-469f-a165-70867728950",
            "0f8fad5b-d9cb-469f-a165-70867728950e0",
            "../../../frameworks?x=0000000000000000",
            "0f8fad5b-d9cb-469f-a165-70867728950g",
            "0f8fad5b/d9cb-469f-a165-70867728950e",
        ] {
            assert!(!is_canonical_uuid(bad), "{bad:?} must be rejected");
        }
    }

    #[test]
    fn documented_service_error_is_relayed() {
        let body = br#"{"error":{"code":"validation_failed","message":"The request is not valid.","fields":[{"field":"name","code":"too_long","message":"This value is too long."}]}}"#;
        assert_eq!(
            parse_api_error(StatusCode::UNPROCESSABLE_ENTITY, body),
            Some(ServiceCallError::ApiError {
                status: 422,
                code: "validation_failed".into(),
                message: "The request is not valid.".into(),
                fields: vec![ApiFieldError {
                    field: "name".into(),
                    code: "too_long".into(),
                    message: "This value is too long.".into(),
                }],
            })
        );
    }

    #[test]
    fn error_without_fields_is_relayed() {
        let body = br#"{"error":{"code":"assessment_not_found","message":"The assessment was not found."}}"#;
        assert!(matches!(
            parse_api_error(StatusCode::NOT_FOUND, body),
            Some(ServiceCallError::ApiError { status: 404, .. })
        ));
    }

    #[test]
    fn undocumented_or_unbounded_errors_are_not_relayed() {
        let long = "x".repeat(MAX_ERROR_TEXT + 1);
        let cases = [
            br#"{"detail":"Internal Server Error"}"#.to_vec(),
            br#"{"error":{"code":"Bad Code!","message":"m"}}"#.to_vec(),
            br#"{"error":{"code":"ok","message":"m","extra":1}}"#.to_vec(),
            format!(r#"{{"error":{{"code":"ok","message":"{long}"}}}}"#).into_bytes(),
            br#"{"error":{"code":"ok","message":"m","fields":[{"field":"a b","code":"c","message":"m"}]}}"#
                .to_vec(),
            b"not json".to_vec(),
        ];
        for body in cases {
            assert_eq!(parse_api_error(StatusCode::BAD_REQUEST, &body), None);
        }
    }

    #[test]
    fn new_assessment_input_is_closed_camel_case() {
        let parsed: NewAssessment =
            serde_json::from_str(r#"{"name":"n","description":null,"frameworkId":"fw"}"#).unwrap();
        assert_eq!(parsed.framework_id, "fw");
        assert!(serde_json::from_str::<NewAssessment>(
            r#"{"name":"n","frameworkId":"fw","status":"compliant"}"#
        )
        .is_err());
    }

    const RELEASE: &str = "0f8fad5b-d9cb-469f-a165-70867728950e";
    const FAMILY: &str = "7c9e6679-7425-40de-944b-e07fc1f90ae7";

    fn query() -> ControlQuery {
        ControlQuery {
            limit: 100,
            ..ControlQuery::default()
        }
    }

    #[test]
    fn query_values_are_percent_encoded() {
        assert_eq!(encode_query_value("AC-2(1)"), "AC-2%281%29");
        assert_eq!(
            encode_query_value("a&limit=500#x/../y"),
            "a%26limit%3D500%23x%2F..%2Fy"
        );
        assert_eq!(encode_query_value("\u{e9} 100%"), "%C3%A9%20100%25");
    }

    #[test]
    fn control_list_path_is_built_only_from_checked_values() {
        assert_eq!(
            control_list_path(RELEASE, &query()).unwrap(),
            format!("/catalog-releases/{RELEASE}/controls?limit=100&offset=0")
        );
        let full = ControlQuery {
            family_id: Some(FAMILY.into()),
            search: Some("account & mgmt".into()),
            offset: 5,
            ..query()
        };
        assert_eq!(
            control_list_path(RELEASE, &full).unwrap(),
            format!(
                "/catalog-releases/{RELEASE}/controls?limit=100&offset=5&family_id={FAMILY}&q=account%20%26%20mgmt"
            )
        );
        let blank = ControlQuery {
            search: Some("   ".into()),
            ..query()
        };
        assert!(!control_list_path(RELEASE, &blank).unwrap().contains("q="));
    }

    #[test]
    fn control_list_path_rejects_out_of_bounds_input() {
        let bad = [
            ControlQuery {
                limit: 0,
                ..query()
            },
            ControlQuery {
                limit: CONTROL_LIMIT_MAX + 1,
                ..query()
            },
            ControlQuery {
                offset: LIST_OFFSET_MAX + 1,
                ..query()
            },
            ControlQuery {
                family_id: Some("AC".into()),
                ..query()
            },
            ControlQuery {
                baseline_id: Some("../x".into()),
                ..query()
            },
            ControlQuery {
                search: Some("x".repeat(SEARCH_MAX_CHARS + 1)),
                ..query()
            },
            ControlQuery {
                search: Some("line\nbreak".into()),
                ..query()
            },
        ];
        for q in bad {
            assert_eq!(control_list_path(RELEASE, &q), None, "{q:?}");
        }
        assert_eq!(control_list_path("not-a-uuid", &query()), None);
        let at_limit = ControlQuery {
            search: Some("x".repeat(SEARCH_MAX_CHARS)),
            ..query()
        };
        assert!(control_list_path(RELEASE, &at_limit).is_some());
    }

    #[test]
    fn control_detail_schema_is_closed_and_recursive() {
        let body = r##"{"id":"c","identifier":"X-1","title":"T","status":null,
            "release":{"id":"r","version":"1","title":"R","is_current":true,"attribution":"A",
                "framework":{"id":"f","name":"F","version":"1"}},
            "family":null,"baselines":["low"],
            "parts":[{"name":"statement","label":null,"title":null,"prose":"p",
                "parts":[{"name":"item","label":"a.","title":null,"prose":"q","parts":[]}]}],
            "params":[],"links":[{"rel":"related","href":"#y","target":{"kind":"control",
                "id":"y","control_id":"y","part_label":null,"identifier":"Y","title":"Y",
                "citation":null,"href":null}}],
            "enhancements":[]}"##;
        let parsed: ControlDetail = serde_json::from_str(body).unwrap();
        assert_eq!(parsed.parts[0].parts[0].label.as_deref(), Some("a."));
        let extra = body.replacen(r#""status":null"#, r#""status":null,"compliant":true"#, 1);
        assert!(serde_json::from_str::<ControlDetail>(&extra).is_err());
    }

    #[test]
    fn control_query_input_is_closed_camel_case() {
        let parsed: ControlQuery = serde_json::from_str(
            r#"{"familyId":null,"baselineId":null,"search":"x","limit":10,"offset":0}"#,
        )
        .unwrap();
        assert_eq!(parsed.search.as_deref(), Some("x"));
        assert!(
            serde_json::from_str::<ControlQuery>(r#"{"limit":10,"offset":0,"path":"/admin"}"#)
                .is_err()
        );
    }

    #[test]
    fn command_errors_serialize_with_a_kind_tag() {
        assert_eq!(
            serde_json::to_value(ServiceCallError::ServiceNotReady).unwrap(),
            serde_json::json!({"kind": "service_not_ready"})
        );
        assert_eq!(
            serde_json::to_value(ServiceCallError::UnexpectedStatus { status: 500 }).unwrap(),
            serde_json::json!({"kind": "unexpected_status", "status": 500})
        );
    }
}
