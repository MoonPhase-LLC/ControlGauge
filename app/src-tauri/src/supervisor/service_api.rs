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
/// Sprint 4 (PD-016): the per-file upload limit, checked here before the
/// bytes are sent; the service enforces it again.
pub const MAX_UPLOAD_BYTES: usize = 50 * 1024 * 1024;
/// Display names longer than this are refused (the service's limit).
pub const MAX_FILENAME_CHARS: usize = 255;
pub const ARTIFACT_LIMIT_MAX: u32 = 200;
/// Sections can be up to 16k characters each; 20 per page keeps a worst-
/// case page under [`MAX_API_RESPONSE_BYTES`].
pub const SECTION_LIMIT_MAX: u32 = 20;
/// An upload is hashed and written durably by the service before it
/// answers; allow more than the default request deadline.
pub const UPLOAD_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(120);
const FILENAME_HEADER: &str = "X-ControlGauge-Filename";

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
    let body = body.map(|bytes| RequestBody {
        bytes,
        content_type: "application/json",
        max_bytes: MAX_API_REQUEST_BYTES,
        filename_header: None,
    });
    call_with(
        connection,
        method,
        path,
        body,
        &[expected],
        HTTP_REQUEST_TIMEOUT,
    )
    .await
    .map(|(value, _)| value)
}

/// A request body and how it is sent.
struct RequestBody {
    bytes: Vec<u8>,
    content_type: &'static str,
    max_bytes: usize,
    /// Already percent-encoded (see [`encode_query_value`]).
    filename_header: Option<String>,
}

/// The one request path every call takes. Returns the parsed body and the
/// status that matched one of `expected`.
async fn call_with<T: DeserializeOwned>(
    connection: &ReadyConnection,
    method: Method,
    path: &str,
    body: Option<RequestBody>,
    expected: &[StatusCode],
    timeout: std::time::Duration,
) -> Result<(T, StatusCode), ServiceCallError> {
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
        Some(body) => {
            if body.bytes.len() > body.max_bytes {
                return Err(ServiceCallError::InvalidArgument);
            }
            builder = builder.header("Content-Type", body.content_type);
            if let Some(name) = body.filename_header {
                builder = builder.header(FILENAME_HEADER, name);
            }
            Bytes::from(body.bytes)
        }
        None => Bytes::new(),
    };
    let request = builder
        .body(Full::new(payload))
        .map_err(|_| ServiceCallError::InvalidArgument)?;

    let response = connection
        .pinned
        .send(request, MAX_API_RESPONSE_BYTES, timeout)
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

    if expected.contains(&response.status) {
        return serde_json::from_slice(&response.body)
            .map(|value| (value, response.status))
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

// --- Sprint 4: evidence ------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Artifact {
    pub id: String,
    pub assessment_id: String,
    pub filename: String,
    pub sha256: String,
    pub size_bytes: u64,
    pub format: String,
    pub mime_type: String,
    pub parse_status: String,
    pub parse_detail: Option<String>,
    pub parser_version: Option<String>,
    pub section_count: u64,
    pub uploaded_at: String,
    pub parsed_at: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct UploadResult {
    pub artifact: Artifact,
    pub duplicate: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct UploadLimits {
    pub max_file_bytes: u64,
    pub max_files_per_batch: u32,
    pub max_artifacts_per_assessment: u32,
    pub max_assessment_bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ArtifactList {
    pub artifacts: Vec<Artifact>,
    pub total: u64,
    pub total_bytes: u64,
    pub limit: u32,
    pub offset: u64,
    pub limits: UploadLimits,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Section {
    pub id: String,
    pub sequence_index: u64,
    pub locator: String,
    pub content: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SectionList {
    pub artifact_id: String,
    pub sections: Vec<Section>,
    pub total: u64,
    pub limit: u32,
    pub offset: u64,
}

/// A display name the frontend may send: 1..=255 characters, no control
/// or line-separator characters, no path separators. The service applies
/// its own, stricter cleaning; this only keeps junk from being sent.
pub fn is_valid_filename(name: &str) -> bool {
    let count = name.chars().count();
    (1..=MAX_FILENAME_CHARS).contains(&count)
        && !name.trim().is_empty()
        && !name
            .chars()
            .any(|c| c.is_control() || matches!(c, '/' | '\\' | '\u{2028}' | '\u{2029}'))
}

/// Builds the upload request after validating every caller-supplied part.
fn upload_request(
    assessment_id: &str,
    filename: &str,
    bytes: Vec<u8>,
) -> Result<(String, RequestBody), ServiceCallError> {
    if !is_canonical_uuid(assessment_id)
        || !is_valid_filename(filename)
        || bytes.len() > MAX_UPLOAD_BYTES
    {
        return Err(ServiceCallError::InvalidArgument);
    }
    Ok((
        format!("/assessments/{assessment_id}/artifacts"),
        RequestBody {
            bytes,
            content_type: "application/octet-stream",
            max_bytes: MAX_UPLOAD_BYTES,
            filename_header: Some(encode_query_value(filename)),
        },
    ))
}

/// Uploads one file. A duplicate (same content already in the assessment)
/// is not an error: the service answers 200 with `duplicate: true`.
pub async fn upload_artifact(
    connection: &ReadyConnection,
    assessment_id: &str,
    filename: &str,
    bytes: Vec<u8>,
) -> Result<UploadResult, ServiceCallError> {
    let (path, body) = upload_request(assessment_id, filename, bytes)?;
    call_with(
        connection,
        Method::POST,
        &path,
        Some(body),
        &[StatusCode::CREATED, StatusCode::OK],
        UPLOAD_TIMEOUT,
    )
    .await
    .map(|(result, _)| result)
}

pub async fn list_artifacts(
    connection: &ReadyConnection,
    assessment_id: &str,
    limit: u32,
    offset: u64,
) -> Result<ArtifactList, ServiceCallError> {
    if !is_canonical_uuid(assessment_id)
        || !(1..=ARTIFACT_LIMIT_MAX).contains(&limit)
        || offset > LIST_OFFSET_MAX
    {
        return Err(ServiceCallError::InvalidArgument);
    }
    let path = format!("/assessments/{assessment_id}/artifacts?limit={limit}&offset={offset}");
    call(connection, Method::GET, &path, None, StatusCode::OK).await
}

pub async fn get_artifact(
    connection: &ReadyConnection,
    id: &str,
) -> Result<Artifact, ServiceCallError> {
    if !is_canonical_uuid(id) {
        return Err(ServiceCallError::InvalidArgument);
    }
    let path = format!("/artifacts/{id}");
    call(connection, Method::GET, &path, None, StatusCode::OK).await
}

pub async fn list_artifact_sections(
    connection: &ReadyConnection,
    id: &str,
    limit: u32,
    offset: u64,
) -> Result<SectionList, ServiceCallError> {
    if !is_canonical_uuid(id)
        || !(1..=SECTION_LIMIT_MAX).contains(&limit)
        || offset > LIST_OFFSET_MAX
    {
        return Err(ServiceCallError::InvalidArgument);
    }
    let path = format!("/artifacts/{id}/sections?limit={limit}&offset={offset}");
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

// --- Sprint 5: model management (mirrors routes/models.py) --------------------

/// Longest accepted model path, mirrored from the service
/// (`routes/models.py` MAX_MODEL_PATH_CHARS); longer never becomes a
/// request body.
pub const MODEL_PATH_MAX_CHARS: usize = 4096;
/// A start runs the model load -- the service's own start timeout goes up
/// to `DEFAULT_START_TIMEOUT_GENERATION_S` (600 s) for a 7B model on CPU,
/// plus slack. Pattern: [`UPLOAD_TIMEOUT`].
pub const MODEL_START_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(900);
/// A health check is one bounded real capability call (the service's
/// generation health timeout is 60 s) plus slack.
pub const MODEL_HEALTH_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(120);
/// An import hashes the whole file before answering (seconds for a
/// multi-GB GGUF); the same bound class as [`UPLOAD_TIMEOUT`] with more
/// slack, since model files are an order of magnitude larger.
pub const MODEL_IMPORT_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(300);

/// One imported model file. The machine-local `path` is deliberately
/// absent: it is stored in the service's database only and never travels
/// to the frontend (SECURITY T-10).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ModelFile {
    pub id: String,
    pub role: String,
    pub display_name: String,
    pub sha256: String,
    pub size_bytes: u64,
    pub imported_at: String,
    pub last_verified_at: Option<String>,
    pub embedding_dimensions: Option<u64>,
    pub embedding_normalization: Option<String>,
    pub embedding_similarity_metric: Option<String>,
    /// Registry distinction (S6-05): `local_import` | `catalog_download` |
    /// `url_import`. Curated vs user-managed must be visible without
    /// reading code (PD-026).
    pub origin: String,
    /// `none` | `user_checksum` | `catalog_manifest` — how the content was
    /// verified, if at all.
    pub verification: String,
}

/// The live per-role runtime status. Never a pid, port, path, or
/// credential.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct RuntimeStatus {
    pub state: String,
    pub detail_code: Option<String>,
    pub checked_at: Option<String>,
    pub model_id: Option<String>,
}

/// The `/models/{id}/health` answer (the service's `HealthStatusOut` has
/// the same closed shape as its per-role status).
pub type ModelHealthStatus = RuntimeStatus;

/// The `GET /models/{id}` answer (same closed shape as a list entry).
pub type ModelSummary = ModelFile;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct RuntimeStates {
    pub generation: Option<RuntimeStatus>,
    pub embedding: Option<RuntimeStatus>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ModelInventory {
    pub models: Vec<ModelFile>,
    pub runtime: RuntimeStates,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ModelImportResult {
    pub model: ModelFile,
    pub duplicate: bool,
    pub provenance: String,
    pub warning: String,
}

#[derive(Serialize)]
struct ImportModelBody<'a> {
    path: &'a str,
    role: &'a str,
}

/// Builds the import request after validating every caller-supplied part:
/// an absolute `.gguf` path of bounded length and a known role. The
/// service re-validates everything (one source of truth for the rules);
/// this only keeps junk from being sent.
fn import_request(path: &str, role: &str) -> Result<(String, RequestBody), ServiceCallError> {
    let parsed = std::path::Path::new(path);
    let valid_path = path.len() <= MODEL_PATH_MAX_CHARS
        && parsed.is_absolute()
        && parsed
            .extension()
            .is_some_and(|ext| ext.eq_ignore_ascii_case("gguf"));
    if !valid_path || !matches!(role, "generation" | "embedding") {
        return Err(ServiceCallError::InvalidArgument);
    }
    let bytes = serde_json::to_vec(&ImportModelBody { path, role })
        .map_err(|_| ServiceCallError::InvalidArgument)?;
    Ok((
        "/models/import".to_string(),
        RequestBody {
            bytes,
            content_type: "application/json",
            max_bytes: MAX_API_REQUEST_BYTES,
            filename_header: None,
        },
    ))
}

/// `/models/{id}` for a canonical model id, or `InvalidArgument`.
fn model_path(id: &str) -> Result<String, ServiceCallError> {
    if !is_canonical_uuid(id) {
        return Err(ServiceCallError::InvalidArgument);
    }
    Ok(format!("/models/{id}"))
}

/// `/models/{id}/{action}` for a canonical model id, or `InvalidArgument`.
fn model_action_path(id: &str, action: &str) -> Result<String, ServiceCallError> {
    Ok(format!("{}/{}", model_path(id)?, action))
}

/// The empty `StartRequest` body the service's closed schema expects
/// (start options are a reserved surface with no Sprint 5 UI exposure).
fn empty_start_body() -> RequestBody {
    RequestBody {
        bytes: b"{}".to_vec(),
        content_type: "application/json",
        max_bytes: MAX_API_REQUEST_BYTES,
        filename_header: None,
    }
}

/// Imports a local GGUF file by absolute path. The service sniff-validates
/// the container and hashes the whole file before answering (multi-second
/// for a multi-GB model); a duplicate (same content, same role) answers
/// 200 with `duplicate: true` instead of 201.
pub async fn import_model(
    connection: &ReadyConnection,
    path: &str,
    role: &str,
) -> Result<ModelImportResult, ServiceCallError> {
    let (path, body) = import_request(path, role)?;
    call_with(
        connection,
        Method::POST,
        &path,
        Some(body),
        &[StatusCode::CREATED, StatusCode::OK],
        MODEL_IMPORT_TIMEOUT,
    )
    .await
    .map(|(result, _)| result)
}

/// The inventory plus each role's **last-known** runtime status: the
/// service answers `GET /models` from recorded state without contacting
/// the model children (Sprint 5 review F1), so the default
/// [`HTTP_REQUEST_TIMEOUT`] is the right deadline here -- never a
/// dedicated wide one. Live checks are [`check_model_health`]'s job and
/// carry [`MODEL_HEALTH_TIMEOUT`].
pub async fn list_models(connection: &ReadyConnection) -> Result<ModelInventory, ServiceCallError> {
    call(connection, Method::GET, "/models", None, StatusCode::OK).await
}

pub async fn get_model(
    connection: &ReadyConnection,
    id: &str,
) -> Result<ModelSummary, ServiceCallError> {
    let path = model_path(id)?;
    call(connection, Method::GET, &path, None, StatusCode::OK).await
}

/// Runs the model for its recorded role (supervised stop-then-start). The
/// service answers only after the child is ready, hence the dedicated
/// [`MODEL_START_TIMEOUT`].
pub async fn start_model(
    connection: &ReadyConnection,
    id: &str,
) -> Result<RuntimeStatus, ServiceCallError> {
    let path = model_action_path(id, "start")?;
    call_with(
        connection,
        Method::POST,
        &path,
        Some(empty_start_body()),
        &[StatusCode::OK],
        MODEL_START_TIMEOUT,
    )
    .await
    .map(|(status, _)| status)
}

pub async fn stop_model(
    connection: &ReadyConnection,
    id: &str,
) -> Result<RuntimeStatus, ServiceCallError> {
    let path = model_action_path(id, "stop")?;
    call(connection, Method::POST, &path, None, StatusCode::OK).await
}

/// Bounded real capability check for the model (a real generation or
/// embed), hence the dedicated [`MODEL_HEALTH_TIMEOUT`] -- the service's
/// own generation health budget is 60 s, wider than
/// [`HTTP_REQUEST_TIMEOUT`]. `unhealthy` is a status, not an error.
pub async fn check_model_health(
    connection: &ReadyConnection,
    id: &str,
) -> Result<ModelHealthStatus, ServiceCallError> {
    let path = model_action_path(id, "health")?;
    call_with(
        connection,
        Method::POST,
        &path,
        None,
        &[StatusCode::OK],
        MODEL_HEALTH_TIMEOUT,
    )
    .await
    .map(|(status, _)| status)
}

// --- Sprint 6 (S6-02): hardware detection ---------------------------------------

/// A cold `GET /hardware` may run the platform probes once: the Linux
/// `nvidia-smi` subprocess is capped at 5 s service-side, and 15 s leaves
/// margin for a cold spawn plus the cache write. The default
/// [`HTTP_REQUEST_TIMEOUT`] (10 s) is too tight for that worst case.
pub const HARDWARE_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(15);

/// One detected GPU. Unknown fields are `None` -- never guessed, never 0.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct HardwareGpu {
    pub vendor: String,
    pub name: Option<String>,
    pub vram_bytes: Option<u64>,
}

/// The closed machine-facts snapshot. No path, URL, or credential field
/// exists in this schema (SECURITY T-10): the disk figure is the free
/// bytes *of* the model directory, never the path itself. An empty
/// `gpus` list means "nothing detected", a valid result -- never an
/// error.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct HardwareInfo {
    pub os_name: String,
    pub os_version: Option<String>,
    pub cpu_model: Option<String>,
    pub cpu_cores_logical: Option<u64>,
    pub ram_total_bytes: Option<u64>,
    pub ram_available_bytes: Option<u64>,
    pub gpus: Vec<HardwareGpu>,
    pub model_dir_free_bytes: Option<u64>,
    pub detection_complete: bool,
    /// ISO 8601, UTC: the snapshot time, not a freshness claim.
    pub detected_at: String,
}

/// `GET /hardware`: the service's cached detection when present, else one
/// fresh detection (cached best-effort) -- hence [`HARDWARE_TIMEOUT`],
/// never the default deadline.
pub async fn get_hardware(connection: &ReadyConnection) -> Result<HardwareInfo, ServiceCallError> {
    call_with(
        connection,
        Method::GET,
        "/hardware",
        None,
        &[StatusCode::OK],
        HARDWARE_TIMEOUT,
    )
    .await
    .map(|(info, _)| info)
}

/// `POST /hardware/refresh`: the service's explicit re-detect
/// (USER_FLOWS §2.4); staleness is never fixed by re-GETting.
pub async fn refresh_hardware(
    connection: &ReadyConnection,
) -> Result<HardwareInfo, ServiceCallError> {
    call_with(
        connection,
        Method::POST,
        "/hardware/refresh",
        None,
        &[StatusCode::OK],
        HARDWARE_TIMEOUT,
    )
    .await
    .map(|(info, _)| info)
}

// --- Sprint 6 (S6-06): hardware-aware recommendations ------------------------------

/// The §5 footprint estimate in bytes.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct FootprintEstimate {
    pub weights_bytes: u64,
    pub kv_cache_bytes: u64,
    pub runtime_overhead_bytes: u64,
    pub total_bytes: u64,
}

/// One entry's feasibility for one declared role. `verdict` is the
/// closed `recommended|feasible|infeasible|unknown` vocabulary,
/// `load_mode` the closed `simultaneous|sequential` one; `reasons`
/// members of the fixed reason-code vocabulary.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct EntryVerdict {
    pub model_id: String,
    pub role: String,
    pub verdict: String,
    pub load_mode: Option<String>,
    pub estimated: FootprintEstimate,
    pub reasons: Vec<String>,
}

/// One generation×embedding combination (D-023). `verdict` is the
/// closed `simultaneous|sequential_only|infeasible` vocabulary.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PairVerdict {
    pub generation_model_id: String,
    pub embedding_model_id: String,
    pub verdict: String,
    pub combined_total_bytes: u64,
    pub reasons: Vec<String>,
}

/// The full deterministic report for one hardware source (`detected` or
/// `profile:<id>`).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct RecommendationReport {
    pub hardware_source: String,
    pub entries: Vec<EntryVerdict>,
    pub pairs: Vec<PairVerdict>,
}

/// `GET /recommendations`: the pure §5 computation over the shipped
/// manifest + the service's cached hardware snapshot. Pure computation
/// on the service side: [`HTTP_REQUEST_TIMEOUT`] is the right deadline.
pub async fn get_recommendations(
    connection: &ReadyConnection,
) -> Result<RecommendationReport, ServiceCallError> {
    call_with(
        connection,
        Method::GET,
        "/recommendations",
        None,
        &[StatusCode::OK],
        HTTP_REQUEST_TIMEOUT,
    )
    .await
    .map(|(report, _)| report)
}

/// `GET /recommendations/profile/{profile_id}`: the same math on a
/// synthetic profile (the 32 GB / 288 GB owner targets). Unknown ids
/// are a service 404 (`profile_unknown`); the id is restricted to
/// `[a-z0-9-]` by the service's own profile schema, so path
/// interpolation is safe.
pub async fn get_profile_recommendations(
    connection: &ReadyConnection,
    profile_id: &str,
) -> Result<RecommendationReport, ServiceCallError> {
    call_with(
        connection,
        Method::GET,
        &format!("/recommendations/profile/{profile_id}"),
        None,
        &[StatusCode::OK],
        HTTP_REQUEST_TIMEOUT,
    )
    .await
    .map(|(report, _)| report)
}

// --- Sprint 6 (S6-07): catalog listing + external endpoints ------------------------

/// One downloadable file of a catalog entry. The SHA-256 is a computed
/// content identity (allowed structural metadata, T-10).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CatalogFile {
    pub filename: String,
    pub size_bytes: u64,
    pub sha256: String,
}

/// One curated catalog entry with its installed state. The
/// `repository_url` and `revision` are manifest data the approval
/// dialog shows verbatim; everything else is closed structural shape.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CatalogEntry {
    pub model_id: String,
    pub display_name: String,
    pub format: String,
    pub backend: String,
    pub roles: Vec<String>,
    pub tier: Option<String>,
    pub recommended: bool,
    pub recommendation_withheld_reason: Option<String>,
    pub context_window: u64,
    pub curated_context_tokens: u64,
    pub max_input_tokens: u64,
    pub max_output_tokens: Option<u64>,
    pub total_bytes: u64,
    pub license: String,
    pub revision: String,
    pub repository_url: String,
    pub embedding_dimensions: Option<u64>,
    pub installed: bool,
    pub installed_model_ids: Vec<String>,
    pub evaluation_status: String,
    /// The declared per-file details the approval dialog shows (S6-07):
    /// the user approves exactly these files and digests.
    pub files: Vec<CatalogFile>,
}

/// The closed `GET /model-catalog` answer: the shipped manifest plus
/// one local database read -- structural (S6-03).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ModelCatalog {
    pub catalog_version: String,
    pub entries: Vec<CatalogEntry>,
}

/// `GET /model-catalog`: the curated listing with installed state.
/// Structural on the service side (manifest load + one DB read), hence
/// [`MODEL_CATALOG_TIMEOUT`].
pub async fn get_model_catalog(
    connection: &ReadyConnection,
) -> Result<ModelCatalog, ServiceCallError> {
    call_with(
        connection,
        Method::GET,
        "/model-catalog",
        None,
        &[StatusCode::OK],
        MODEL_CATALOG_TIMEOUT,
    )
    .await
    .map(|(catalog, _)| catalog)
}

/// One registered external endpoint. `base_url_host_only` is the
/// normalized `scheme://host:port` form -- loopback by construction,
/// with no path/userinfo/query remainder to leak (T-10).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Endpoint {
    pub id: String,
    pub display_name: String,
    pub backend: String,
    pub base_url_host_only: String,
    pub capabilities: String,
    /// ISO 8601, UTC.
    pub registered_at: String,
    pub last_health_at: Option<String>,
    pub last_health_state: Option<String>,
    pub last_health_code: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct EndpointList {
    pub endpoints: Vec<Endpoint>,
}

/// The registration POST body. No credential field exists (V0.1
/// registers unauthenticated loopback endpoints only).
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EndpointCreate<'a> {
    pub display_name: &'a str,
    pub backend: &'a str,
    pub base_url: &'a str,
    pub capabilities: &'a str,
}

/// The attach POST body.
#[derive(Debug, Serialize)]
pub struct EndpointAttach<'a> {
    pub role: &'a str,
}

/// The 200 body of a successful attach: the role is now occupied by
/// the endpoint (its registry id is the role's `model_id`).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct AttachResult {
    pub role: String,
    pub state: String,
    pub model_id: String,
}

/// The service mints endpoint ids as uuid4 strings (36 chars, 4 dashes).
fn is_endpoint_id(endpoint_id: &str) -> bool {
    let bytes = endpoint_id.as_bytes();
    if bytes.len() != 36 {
        return false;
    }
    for (index, b) in bytes.iter().enumerate() {
        match (index, b) {
            (8 | 13 | 18 | 23, b'-') => {}
            (_, b'0'..=b'9' | b'a'..=b'f') => {}
            _ => return false,
        }
    }
    true
}

fn endpoint_path(endpoint_id: &str, suffix: Option<&str>) -> Result<String, ServiceCallError> {
    if !is_endpoint_id(endpoint_id) {
        return Err(ServiceCallError::InvalidArgument);
    }
    Ok(match suffix {
        None => format!("/model-endpoints/{endpoint_id}"),
        Some(suffix) => format!("/model-endpoints/{endpoint_id}/{suffix}"),
    })
}

/// `GET /model-endpoints`: the registered endpoints with their RECORDED
/// last health result -- structural, never a live probe.
pub async fn list_endpoints(
    connection: &ReadyConnection,
) -> Result<EndpointList, ServiceCallError> {
    call_with(
        connection,
        Method::GET,
        "/model-endpoints",
        None,
        &[StatusCode::OK],
        HTTP_REQUEST_TIMEOUT,
    )
    .await
    .map(|(list, _)| list)
}

/// The endpoint registration POST runs a bounded REAL health probe
/// server-side (`ENDPOINT_HEALTH_TIMEOUT_S` = 10 s per capability
/// before the 502 refusal), so the client deadline must comfortably
/// outlive it.
pub const ENDPOINT_REGISTER_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(30);

/// The live health-check POST runs the same bounded probe server-side.
pub const ENDPOINT_HEALTH_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(20);

/// The attach POST is bounded local work (provider construction +
/// manager switch), but the switch may stop an occupied role first --
/// the same class as a start's tail, so it keeps a wide deadline.
pub const ENDPOINT_ATTACH_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(20);

/// The registration POST body bytes: closed JSON, camelCase. Shape
/// validation happens here (the service owns the loopback rule and the
/// bounded probe; a malformed shape is refused before any round trip).
fn endpoint_create_request(create: &EndpointCreate<'_>) -> Result<RequestBody, ServiceCallError> {
    let shape_ok = !create.display_name.is_empty()
        && create.display_name.len() <= 255
        && matches!(create.backend, "llama_cpp" | "openai_compatible")
        && matches!(create.capabilities, "generation" | "embedding" | "both")
        && !create.base_url.is_empty()
        && create.base_url.len() <= 500;
    if !shape_ok {
        return Err(ServiceCallError::InvalidArgument);
    }
    serde_json::to_vec(create)
        .map(|bytes| RequestBody {
            bytes,
            content_type: "application/json",
            max_bytes: MAX_API_REQUEST_BYTES,
            filename_header: None,
        })
        .map_err(|_| ServiceCallError::InvalidArgument)
}

/// `POST /model-endpoints`: the explicit registration (validate URL →
/// bounded probe → persist; probe failure is a 502 refusal with NO
/// row). 201 answers with the registered endpoint.
pub async fn register_endpoint(
    connection: &ReadyConnection,
    create: &EndpointCreate<'_>,
) -> Result<Endpoint, ServiceCallError> {
    let body = endpoint_create_request(create)?;
    call_with(
        connection,
        Method::POST,
        "/model-endpoints",
        Some(body),
        &[StatusCode::CREATED],
        ENDPOINT_REGISTER_TIMEOUT,
    )
    .await
    .map(|(endpoint, _)| endpoint)
}

/// `POST /model-endpoints/{id}/health`: the live bounded check; the
/// worst result is recorded on the row.
pub async fn check_endpoint_health(
    connection: &ReadyConnection,
    endpoint_id: &str,
) -> Result<Endpoint, ServiceCallError> {
    let path = endpoint_path(endpoint_id, Some("health"))?;
    call_with(
        connection,
        Method::POST,
        &path,
        None,
        &[StatusCode::OK],
        ENDPOINT_HEALTH_TIMEOUT,
    )
    .await
    .map(|(endpoint, _)| endpoint)
}

/// `POST /model-endpoints/{id}/attach`: make the endpoint a role's
/// occupant (the switch contract applies; `start()` is never called).
pub async fn attach_endpoint(
    connection: &ReadyConnection,
    endpoint_id: &str,
    role: &str,
) -> Result<AttachResult, ServiceCallError> {
    let path = endpoint_path(endpoint_id, Some("attach"))?;
    if !matches!(role, "generation" | "embedding") {
        return Err(ServiceCallError::InvalidArgument);
    }
    let body = serde_json::to_vec(&EndpointAttach { role })
        .map(|bytes| RequestBody {
            bytes,
            content_type: "application/json",
            max_bytes: MAX_API_REQUEST_BYTES,
            filename_header: None,
        })
        .map_err(|_| ServiceCallError::InvalidArgument)?;
    call_with(
        connection,
        Method::POST,
        &path,
        Some(body),
        &[StatusCode::OK],
        ENDPOINT_ATTACH_TIMEOUT,
    )
    .await
    .map(|(result, _)| result)
}

/// `DELETE /model-endpoints/{id}`: unregister; refused 409 while the
/// endpoint occupies a role (detach via the role stop first). In-memory
/// DB work only: the default deadline.
pub async fn delete_endpoint(
    connection: &ReadyConnection,
    endpoint_id: &str,
) -> Result<(), ServiceCallError> {
    let path = endpoint_path(endpoint_id, None)?;
    call_with::<serde_json::Value>(
        connection,
        Method::DELETE,
        &path,
        None,
        &[StatusCode::OK],
        HTTP_REQUEST_TIMEOUT,
    )
    .await
    .map(|_| ())
}

// --- Sprint 6 (S6-04): secure downloads + URL imports -----------------------------

/// The catalog download-start POST does bounded local work before the
/// transfer thread starts (manifest load, URL validation, the disk
/// pre-flight) -- well under the default deadline normally, but 30 s
/// covers a cold first parse on a loaded machine with margin. The
/// default [`HTTP_REQUEST_TIMEOUT`] (10 s) is too tight to guarantee.
pub const MODEL_CATALOG_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(30);

/// The URL-import start POST is the same bounded-local class (URL rule,
/// display-name cleaning, disk pre-flight). Pattern:
/// [`MODEL_CATALOG_TIMEOUT`].
pub const DOWNLOAD_START_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(30);

/// Poll and cancel are in-memory snapshots service-side (no child
/// contact, no network). Tight by design: a wedged service must surface
/// in the UI's poll loop fast instead of hanging each request for the
/// default deadline.
pub const DOWNLOAD_POLL_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(5);

/// The 202 body of an approved download start: the poll id only.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct DownloadStarted {
    pub download_id: String,
}

/// One file's byte counts. The filename is the REMOTE artifact name (a
/// manifest or import-URL datum, like the catalog's own fields) -- never
/// a local path (SECURITY T-10).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct DownloadFileProgress {
    pub filename: String,
    pub bytes_downloaded: u64,
    /// 0 = size not declared up front (URL imports).
    pub bytes_total: u64,
}

/// The structural poll answer: state machine + byte counts. Never a
/// path, URL, or credential. `state` is one of the service's closed set
/// (pending | downloading | verifying | completed | failed | cancelled);
/// `error_code` is set only on `failed`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct DownloadStatus {
    pub download_id: String,
    pub state: String,
    pub files: Vec<DownloadFileProgress>,
    pub error_code: Option<String>,
}

/// The manifest's identifier shape for catalog model ids
/// (`^[a-z0-9][a-z0-9.-]*$`, bounded): the Rust side only keeps junk
/// from being sent; the service re-validates against the loaded
/// manifest.
fn is_catalog_model_id(model_id: &str) -> bool {
    let mut chars = model_id.chars();
    match chars.next() {
        Some(c) if c.is_ascii_lowercase() || c.is_ascii_digit() => {}
        _ => return false,
    }
    model_id.len() <= 100
        && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '.' || c == '-')
}

/// `/model-catalog/{model_id}/download` for a catalog id, or
/// `InvalidArgument`.
fn catalog_download_path(model_id: &str) -> Result<String, ServiceCallError> {
    if !is_catalog_model_id(model_id) {
        return Err(ServiceCallError::InvalidArgument);
    }
    Ok(format!("/model-catalog/{model_id}/download"))
}

/// The service mints download ids as uuid4 hex: 32 lowercase hex chars.
fn is_download_id(download_id: &str) -> bool {
    download_id.len() == 32
        && download_id
            .chars()
            .all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c))
}

/// `/model-catalog/downloads/{download_id}[/{action}]` for an opaque
/// download id, or `InvalidArgument`.
fn download_path(download_id: &str, action: Option<&str>) -> Result<String, ServiceCallError> {
    if !is_download_id(download_id) {
        return Err(ServiceCallError::InvalidArgument);
    }
    let base = format!("/model-catalog/downloads/{download_id}");
    Ok(match action {
        Some(action) => format!("{base}/{action}"),
        None => base,
    })
}

/// The user-URL rule, restated from `download.py::validate_user_url`:
/// https anywhere, or http to a loopback host (the local-test case).
/// The service re-validates; this keeps every non-loopback plaintext
/// URL from ever leaving the app.
fn is_valid_import_url(url: &str) -> bool {
    if url.len() > 2048 {
        return false;
    }
    if let Some(rest) = url.strip_prefix("https://") {
        return !rest.is_empty() && !rest.starts_with('/');
    }
    if let Some(rest) = url.strip_prefix("http://") {
        let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
        let host = authority.rsplit_once('@').map_or(authority, |(_, h)| h);
        let host = if let Some(inner) = host.strip_prefix('[') {
            // Bracketed IPv6: never split on the colons inside.
            inner.split_once(']').map_or("", |(h, _)| h)
        } else {
            host.split_once(':').map_or(host, |(h, _)| h)
        };
        return matches!(
            host.to_ascii_lowercase().as_str(),
            "127.0.0.1" | "::1" | "localhost"
        );
    }
    false
}

#[derive(Serialize)]
struct ImportUrlBody<'a> {
    url: &'a str,
    role: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    display_name: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    expected_sha256: Option<&'a str>,
    acknowledge_unverified: bool,
}

/// Builds the URL-import request after validating every caller-supplied
/// part: the URL rule, a known role, an optional bounded display name,
/// and the PD-026 authorization rule -- either an expected checksum
/// (64 lowercase hex) or the explicit unverified acknowledgment. The
/// service re-validates everything; this only keeps junk from being
/// sent.
fn import_url_request(
    url: &str,
    role: &str,
    display_name: Option<&str>,
    expected_sha256: Option<&str>,
    acknowledge_unverified: bool,
) -> Result<RequestBody, ServiceCallError> {
    let valid_role = matches!(role, "generation" | "embedding");
    let valid_name = match display_name {
        Some(name) => !name.is_empty() && name.len() <= 255,
        None => true,
    };
    let valid_sha = match expected_sha256 {
        Some(sha) => {
            sha.len() == 64
                && sha
                    .chars()
                    .all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c))
        }
        None => true,
    };
    if !is_valid_import_url(url) || !valid_role || !valid_name || !valid_sha {
        return Err(ServiceCallError::InvalidArgument);
    }
    if expected_sha256.is_none() && !acknowledge_unverified {
        // PD-026: an elevated-trust action needs explicit authorization.
        // Refuse locally instead of paying the service round-trip.
        return Err(ServiceCallError::InvalidArgument);
    }
    let body = ImportUrlBody {
        url,
        role,
        display_name,
        expected_sha256,
        acknowledge_unverified,
    };
    serde_json::to_vec(&body)
        .map(|bytes| RequestBody {
            bytes,
            content_type: "application/json",
            max_bytes: MAX_API_REQUEST_BYTES,
            filename_header: None,
        })
        .map_err(|_| ServiceCallError::InvalidArgument)
}

/// Starts a curated-catalog download: the explicit approval POST (the
/// UI confirms first). The transfer itself is polled structurally via
/// [`get_download_status`] -- this answers 202 as soon as the bounded
/// local work is done, hence [`MODEL_CATALOG_TIMEOUT`].
pub async fn start_catalog_download(
    connection: &ReadyConnection,
    model_id: &str,
) -> Result<DownloadStarted, ServiceCallError> {
    let path = catalog_download_path(model_id)?;
    call_with(
        connection,
        Method::POST,
        &path,
        None,
        &[StatusCode::ACCEPTED],
        MODEL_CATALOG_TIMEOUT,
    )
    .await
    .map(|(started, _)| started)
}

/// Structural poll of a download: state + byte counts, never a path
/// (T-10). An in-memory snapshot service-side, hence the tight
/// [`DOWNLOAD_POLL_TIMEOUT`].
pub async fn get_download_status(
    connection: &ReadyConnection,
    download_id: &str,
) -> Result<DownloadStatus, ServiceCallError> {
    let path = download_path(download_id, None)?;
    call_with(
        connection,
        Method::GET,
        &path,
        None,
        &[StatusCode::OK],
        DOWNLOAD_POLL_TIMEOUT,
    )
    .await
    .map(|(status, _)| status)
}

/// Requests cancellation; the worker stops at the next chunk boundary
/// and retains the `.part` (a later attempt resumes). Cancelling a
/// terminal download answers its status (a no-op), an unknown id is 404.
pub async fn cancel_download(
    connection: &ReadyConnection,
    download_id: &str,
) -> Result<DownloadStatus, ServiceCallError> {
    let path = download_path(download_id, Some("cancel"))?;
    call_with(
        connection,
        Method::POST,
        &path,
        None,
        &[StatusCode::OK],
        DOWNLOAD_POLL_TIMEOUT,
    )
    .await
    .map(|(status, _)| status)
}

/// Starts a direct-URL model download (S6-04). The POST is the explicit
/// approval; either an expected checksum (enforced before registration)
/// or the explicit unverified acknowledgment is required (PD-026).
/// Progress is polled via [`get_download_status`].
pub async fn import_model_url(
    connection: &ReadyConnection,
    url: &str,
    role: &str,
    display_name: Option<&str>,
    expected_sha256: Option<&str>,
    acknowledge_unverified: bool,
) -> Result<DownloadStarted, ServiceCallError> {
    let body = import_url_request(
        url,
        role,
        display_name,
        expected_sha256,
        acknowledge_unverified,
    )?;
    call_with(
        connection,
        Method::POST,
        "/models/import-url",
        Some(body),
        &[StatusCode::ACCEPTED],
        DOWNLOAD_START_TIMEOUT,
    )
    .await
    .map(|(started, _)| started)
}

#[cfg(test)]
mod tests {
    use super::*;

    const ASSESSMENT: &str = "0f8fad5b-d9cb-469f-a165-70867728950e";
    const MODEL: &str = "0f8fad5b-d9cb-469f-a165-70867728950e";
    /// A uuid-shaped endpoint id (the service mints `str(uuid4())`).
    const ENDPOINT: &str = "7c9e6679-7425-40de-944b-e07fc1f90ae7";

    #[test]
    fn upload_request_validates_and_encodes() {
        let (path, body) = upload_request(ASSESSMENT, "Política de acceso.pdf", vec![1, 2, 3])
            .expect("valid upload");
        assert_eq!(path, format!("/assessments/{ASSESSMENT}/artifacts"));
        assert_eq!(body.content_type, "application/octet-stream");
        assert_eq!(
            body.filename_header.as_deref(),
            Some("Pol%C3%ADtica%20de%20acceso.pdf")
        );
        assert_eq!(body.bytes, vec![1, 2, 3]);
    }

    #[test]
    fn upload_request_rejects_bad_input() {
        let long = "x".repeat(MAX_FILENAME_CHARS + 1);
        for (assessment, name) in [
            ("not-a-uuid", "a.pdf"),
            (ASSESSMENT, ""),
            (ASSESSMENT, "   "),
            (ASSESSMENT, "a/b.pdf"),
            (ASSESSMENT, "a\\b.pdf"),
            (ASSESSMENT, "a\nb.pdf"),
            (ASSESSMENT, "a\u{2028}.pdf"),
            (ASSESSMENT, long.as_str()),
        ] {
            assert_eq!(
                upload_request(assessment, name, vec![])
                    .err()
                    .map(|e| format!("{e:?}")),
                Some(format!("{:?}", ServiceCallError::InvalidArgument)),
                "{name:?}"
            );
        }
        assert!(upload_request(ASSESSMENT, "a.pdf", vec![0; MAX_UPLOAD_BYTES + 1]).is_err());
        assert!(upload_request(ASSESSMENT, "a.pdf", vec![0; MAX_UPLOAD_BYTES]).is_ok());
    }

    #[test]
    fn artifact_responses_are_closed_schemas() {
        let good = serde_json::json!({
            "id": ASSESSMENT, "assessment_id": ASSESSMENT, "filename": "a.txt",
            "sha256": "0".repeat(64), "size_bytes": 3, "format": "txt",
            "mime_type": "text/plain", "parse_status": "parsed", "parse_detail": null,
            "parser_version": "cg-parsers/1", "section_count": 1,
            "uploaded_at": "2026-11-05T00:00:00", "parsed_at": null
        });
        assert!(serde_json::from_value::<Artifact>(good.clone()).is_ok());
        let mut extra = good;
        extra["unexpected"] = serde_json::json!("x");
        assert!(serde_json::from_value::<Artifact>(extra).is_err());
    }

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

    // --- Sprint 5: model management -------------------------------------------

    fn model_fixture() -> serde_json::Value {
        serde_json::json!({
            "id": MODEL, "role": "generation",
            "display_name": "generation-model.gguf", "sha256": "a".repeat(64),
            "size_bytes": 3_993_201_344u64, "imported_at": "2026-11-17T12:00:00Z",
            "last_verified_at": null, "embedding_dimensions": null,
            "embedding_normalization": null, "embedding_similarity_metric": null,
            "origin": "local_import", "verification": "none"
        })
    }

    #[test]
    fn model_response_schemas_reject_unknown_fields() {
        let inventory = serde_json::json!({
            "models": [model_fixture()],
            "runtime": {
                "generation": {
                    "state": "ready", "detail_code": null,
                    "checked_at": "2026-11-17T12:01:00Z", "model_id": MODEL
                },
                "embedding": null
            }
        });
        assert!(serde_json::from_value::<ModelInventory>(inventory).is_ok());
        assert!(serde_json::from_value::<ModelFile>(model_fixture()).is_ok());

        // T-10: the machine-local `path` must never appear on a response.
        let mut with_path = model_fixture();
        with_path["path"] = serde_json::json!("/home/user/models/x.gguf");
        assert!(serde_json::from_value::<ModelFile>(with_path).is_err());

        let mut extra_inventory = serde_json::json!({
            "models": [], "runtime": {"generation": null, "embedding": null}
        });
        extra_inventory["unexpected"] = serde_json::json!("x");
        assert!(serde_json::from_value::<ModelInventory>(extra_inventory).is_err());

        let status = serde_json::json!({
            "state": "unhealthy", "detail_code": "request_timeout",
            "checked_at": "2026-11-17T12:02:00Z", "model_id": MODEL
        });
        assert!(serde_json::from_value::<ModelHealthStatus>(status).is_ok());
        let mut extra_status = serde_json::json!({
            "state": "ready", "detail_code": null, "checked_at": null, "model_id": null
        });
        extra_status["pid"] = serde_json::json!(1234);
        assert!(serde_json::from_value::<ModelHealthStatus>(extra_status).is_err());

        let import = serde_json::json!({
            "model": model_fixture(), "duplicate": false,
            "provenance": "unverified_import", "warning": "w"
        });
        assert!(serde_json::from_value::<ModelImportResult>(import).is_ok());
        let mut extra_import = serde_json::json!({
            "model": model_fixture(), "duplicate": true,
            "provenance": "unverified_import", "warning": "w"
        });
        extra_import["path"] = serde_json::json!("/home/user/models/x.gguf");
        assert!(serde_json::from_value::<ModelImportResult>(extra_import).is_err());
    }

    #[test]
    fn recommendation_report_schema_is_closed() {
        // --- Sprint 6 (S6-06): the recommendation report mirror ----------
        let report = serde_json::json!({
            "hardware_source": "profile:workstation-32gb",
            "entries": [{
                "model_id": "m", "role": "generation", "verdict": "recommended",
                "load_mode": "simultaneous",
                "estimated": {
                    "weights_bytes": 1u64, "kv_cache_bytes": 2u64,
                    "runtime_overhead_bytes": 3u64, "total_bytes": 6u64
                },
                "reasons": []
            }],
            "pairs": [{
                "generation_model_id": "m", "embedding_model_id": "e",
                "verdict": "simultaneous", "combined_total_bytes": 7u64,
                "reasons": ["insufficient_vram"]
            }]
        });
        assert!(serde_json::from_value::<RecommendationReport>(report.clone()).is_ok());
        assert_eq!(
            serde_json::from_value::<RecommendationReport>(report).unwrap(),
            RecommendationReport {
                hardware_source: "profile:workstation-32gb".into(),
                entries: vec![EntryVerdict {
                    model_id: "m".into(),
                    role: "generation".into(),
                    verdict: "recommended".into(),
                    load_mode: Some("simultaneous".into()),
                    estimated: FootprintEstimate {
                        weights_bytes: 1,
                        kv_cache_bytes: 2,
                        runtime_overhead_bytes: 3,
                        total_bytes: 6
                    },
                    reasons: vec![]
                }],
                pairs: vec![PairVerdict {
                    generation_model_id: "m".into(),
                    embedding_model_id: "e".into(),
                    verdict: "simultaneous".into(),
                    combined_total_bytes: 7,
                    reasons: vec!["insufficient_vram".into()]
                }]
            }
        );
        // T-10/closed-schema: an unknown field is refused, never tolerated.
        let mut extra = serde_json::json!({
            "hardware_source": "detected", "entries": [], "pairs": []
        });
        extra["gpu_names"] = serde_json::json!(["RTX"]);
        assert!(serde_json::from_value::<RecommendationReport>(extra).is_err());
    }

    #[test]
    fn endpoint_and_catalog_schemas_are_closed() {
        // --- Sprint 6 (S6-07): endpoints + the catalog listing ----------
        let endpoint = serde_json::json!({
            "id": ENDPOINT, "display_name": "Dev llama-server",
            "backend": "llama_cpp", "base_url_host_only": "http://127.0.0.1:8080",
            "capabilities": "generation", "registered_at": "2026-11-22T12:00:00Z",
            "last_health_at": "2026-11-22T12:01:00Z", "last_health_state": "ready",
            "last_health_code": null
        });
        assert!(serde_json::from_value::<Endpoint>(endpoint.clone()).is_ok());
        let mut extra_endpoint = endpoint.clone();
        extra_endpoint["base_url"] = serde_json::json!("http://127.0.0.1:8080/some/path");
        // The host-only shape is closed: no full URL field is tolerated.
        assert!(serde_json::from_value::<Endpoint>(extra_endpoint).is_err());
        let list = serde_json::json!({ "endpoints": [endpoint] });
        assert!(serde_json::from_value::<EndpointList>(list).is_ok());
        let attach =
            serde_json::json!({ "role": "generation", "state": "ready", "model_id": ENDPOINT });
        assert!(serde_json::from_value::<AttachResult>(attach).is_ok());

        let catalog = serde_json::json!({
            "catalog_version": "1.0.0",
            "entries": [{
                "model_id": "generic-model", "display_name": "Generic Model",
                "format": "gguf", "backend": "llama_cpp", "roles": ["generation"],
                "tier": "balanced", "recommended": true,
                "recommendation_withheld_reason": null,
                "context_window": 32768u64, "curated_context_tokens": 8192u64,
                "max_input_tokens": 8192u64, "max_output_tokens": 512u64,
                "total_bytes": 1u64, "license": "apache-2.0",
                "revision": "0123456789abcdef0123456789abcdef01234567",
                "repository_url": "https://example.invalid/org/repo",
                "embedding_dimensions": null, "installed": false,
                "installed_model_ids": [], "evaluation_status": "unevaluated",
                "files": [{"filename": "generic.gguf", "size_bytes": 1u64, "sha256": "ab".repeat(32)}]
            }]
        });
        assert!(serde_json::from_value::<ModelCatalog>(catalog).is_ok());
    }

    #[test]
    fn endpoint_request_validation_refuses_bad_shapes() {
        let ok = EndpointCreate {
            display_name: "Dev",
            backend: "llama_cpp",
            base_url: "http://127.0.0.1:8080",
            capabilities: "both",
        };
        assert!(endpoint_create_request(&ok).is_ok());
        let loopback_blocked_service_side = EndpointCreate {
            display_name: "Dev",
            backend: "llama_cpp",
            base_url: "http://10.0.0.5:8080", // shape-valid; the service refuses
            capabilities: "generation",
        };
        assert!(endpoint_create_request(&loopback_blocked_service_side).is_ok());
        let bad_backend = EndpointCreate {
            backend: "vendor_x",
            ..ok
        };
        assert!(endpoint_create_request(&bad_backend).is_err());
        let bad_capabilities = EndpointCreate {
            capabilities: "audio",
            ..EndpointCreate {
                display_name: "Dev",
                backend: "llama_cpp",
                base_url: "http://127.0.0.1:8080",
                capabilities: "both",
            }
        };
        assert!(endpoint_create_request(&bad_capabilities).is_err());

        // Path interpolation is guarded: only uuid-shaped endpoint ids.
        assert!(endpoint_path(ENDPOINT, None).is_ok());
        assert!(endpoint_path(ENDPOINT, Some("health")).is_ok());
        assert!(endpoint_path("../etc", None).is_err());
        assert!(endpoint_path("not-a-uuid", None).is_err());
    }

    #[test]
    fn endpoint_deadlines_outlive_the_bounded_server_probe() {
        // The registration/health probes are ENDPOINT_HEALTH_TIMEOUT_S =
        // 10 s per capability server-side; the client deadlines must
        // comfortably outlive them (D-026).
        assert!(ENDPOINT_REGISTER_TIMEOUT >= HTTP_REQUEST_TIMEOUT);
        assert!(ENDPOINT_REGISTER_TIMEOUT.as_secs() >= 10 + 10);
        assert!(ENDPOINT_HEALTH_TIMEOUT >= HTTP_REQUEST_TIMEOUT);
        assert!(ENDPOINT_ATTACH_TIMEOUT >= HTTP_REQUEST_TIMEOUT);
    }

    #[test]
    fn import_model_argument_validation() {
        let absolute = std::env::temp_dir().join("model.gguf");
        let (path, body) = import_request(absolute.to_str().unwrap(), "generation")
            .expect("a valid absolute gguf path and known role");
        assert_eq!(path, "/models/import");
        assert_eq!(body.content_type, "application/json");
        let parsed: serde_json::Value = serde_json::from_slice(&body.bytes).unwrap();
        assert_eq!(
            parsed,
            serde_json::json!({"path": absolute.to_str().unwrap(), "role": "generation"})
        );

        // Case-insensitive extension is accepted.
        let upper = std::env::temp_dir().join("MODEL.GGUF");
        assert!(import_request(upper.to_str().unwrap(), "embedding").is_ok());

        // Relative on every platform: no root, no prefix. (Deriving one from
        // temp_dir by stripping a separator is Unix-only -- a Windows temp
        // path like "C:\Users\...\relative\model.gguf" has no leading
        // separator to strip and would stay absolute, which import_request
        // correctly accepts.)
        let relative = std::path::Path::new("relative").join("model.gguf");
        let relative = relative.to_str().unwrap().to_string();
        let wrong_extension = std::env::temp_dir().join("model.bin");
        let dotfile = std::env::temp_dir().join(".gguf");
        let bad = [
            // Not absolute (never a path segment or body from free text).
            (relative.as_str(), "generation"),
            // Wrong extension.
            (wrong_extension.to_str().unwrap(), "generation"),
            // Hidden dotfile with no real extension.
            (dotfile.to_str().unwrap(), "generation"),
            // Unknown role.
            (absolute.to_str().unwrap(), "sampler"),
            (absolute.to_str().unwrap(), ""),
            // Empty path (not absolute; the length bound is checked below).
            ("", "generation"),
        ];
        for (path, role) in bad {
            assert_eq!(
                import_request(path, role).err().map(|e| format!("{e:?}")),
                Some(format!("{:?}", ServiceCallError::InvalidArgument)),
                "{path:?} {role:?}"
            );
        }
        let long = std::env::temp_dir().join(format!("{}.gguf", "x".repeat(MODEL_PATH_MAX_CHARS)));
        assert!(import_request(long.to_str().unwrap(), "generation").is_err());
    }

    #[test]
    fn uuid_canonical_check_applies_to_model_ids() {
        assert_eq!(model_path(MODEL).unwrap(), format!("/models/{MODEL}"));
        assert_eq!(
            model_action_path(MODEL, "start").unwrap(),
            format!("/models/{MODEL}/start")
        );
        for id in [
            "0F8FAD5B-D9CB-469F-A165-70867728950E",
            "not-a-uuid",
            "../../../health?x=00000000-0000-0000-0000-000000000000",
            "",
        ] {
            assert_eq!(
                model_path(id).err().map(|e| format!("{e:?}")),
                Some(format!("{:?}", ServiceCallError::InvalidArgument)),
                "{id:?}"
            );
            assert_eq!(
                model_action_path(id, "stop")
                    .err()
                    .map(|e| format!("{e:?}")),
                Some(format!("{:?}", ServiceCallError::InvalidArgument)),
                "{id:?}"
            );
        }
    }

    #[test]
    fn download_paths_validate_ids_and_catalog_model_ids() {
        let id = "0f8fad5bd9cb469fa16570867728950e";
        assert_eq!(
            download_path(id, None).unwrap(),
            format!("/model-catalog/downloads/{id}")
        );
        assert_eq!(
            download_path(id, Some("cancel")).unwrap(),
            format!("/model-catalog/downloads/{id}/cancel")
        );
        for bad in [
            "0F8FAD5B-D9CB-469F-A165-70867728950E", // the UUID form is not the hex id
            "0f8fad5b d9cb469fa16570867728950e",
            "not-a-download-id",
            "",
            "../../../models",
            "0f8fad5bd9cb469fa16570867728950", // 31 chars
        ] {
            assert_eq!(
                download_path(bad, None).err(),
                Some(ServiceCallError::InvalidArgument),
                "{bad:?}"
            );
        }
        assert_eq!(
            catalog_download_path("generic-model-v1.5-q4").unwrap(),
            "/model-catalog/generic-model-v1.5-q4/download"
        );
        for bad in [
            "",
            "Generic",
            "model/slash",
            "-leading",
            ".leading",
            &"x".repeat(101),
        ] {
            assert_eq!(
                catalog_download_path(bad).err(),
                Some(ServiceCallError::InvalidArgument),
                "{bad:?}"
            );
        }
    }

    #[test]
    fn import_url_request_validates_and_encodes() {
        let body = import_url_request(
            "https://example.com/models/generic.gguf",
            "embedding",
            Some("Generic Model"),
            Some(&"a".repeat(64)),
            false,
        )
        .expect("valid request");
        let json: serde_json::Value = serde_json::from_slice(&body.bytes).unwrap();
        assert_eq!(json["url"], "https://example.com/models/generic.gguf");
        assert_eq!(json["role"], "embedding");
        assert_eq!(json["display_name"], "Generic Model");
        assert_eq!(json["expected_sha256"], "a".repeat(64));
        assert_eq!(json["acknowledge_unverified"], false);

        // Optionals omitted when absent; the loopback http rule holds.
        let body = import_url_request("http://127.0.0.1:8/x.gguf", "generation", None, None, true)
            .expect("valid request");
        let json: serde_json::Value = serde_json::from_slice(&body.bytes).unwrap();
        assert!(json.get("display_name").is_none());
        assert!(json.get("expected_sha256").is_none());
        assert_eq!(json["acknowledge_unverified"], true);
        for url in ["http://localhost/x.gguf", "http://[::1]:9/x.gguf"] {
            assert!(
                import_url_request(url, "generation", None, None, true).is_ok(),
                "{url:?}"
            );
        }

        for (url, role, name, sha, ack) in [
            ("http://example.com/x.gguf", "generation", None, None, true), // plaintext non-loopback
            (
                "http://localhost@evil.example/x.gguf",
                "generation",
                None,
                None,
                true,
            ), // userinfo trick
            ("https:///no-host.gguf", "generation", None, None, true),
            ("ftp://example.com/x.gguf", "generation", None, None, true),
            ("", "generation", None, None, true),
            ("https://example.com/x.gguf", "embeddingx", None, None, true), // bad role
            (
                "https://example.com/x.gguf",
                "generation",
                Some(""),
                None,
                true,
            ),
            (
                "https://example.com/x.gguf",
                "generation",
                Some(&"x".repeat(256)),
                None,
                true,
            ),
            (
                "https://example.com/x.gguf",
                "generation",
                None,
                Some("ABCD"),
                true,
            ), // bad shape
            (
                "https://example.com/x.gguf",
                "generation",
                None,
                Some(&"A".repeat(64)),
                true,
            ), // uppercase
            (
                "https://example.com/x.gguf",
                "generation",
                None,
                None,
                false,
            ), // PD-026: neither granted
        ] {
            assert_eq!(
                import_url_request(url, role, name, sha, ack).err(),
                Some(ServiceCallError::InvalidArgument),
                "{url:?} {role:?}"
            );
        }
    }

    #[test]
    fn model_timeout_constants_cover_the_service_budgets() {
        // The service's own start timeout tops out at
        // DEFAULT_START_TIMEOUT_GENERATION_S = 600 s; the Rust deadline
        // must exceed it or a slow-but-successful load would trip the
        // request deadline and fail closed the pinned connection.
        assert!(MODEL_START_TIMEOUT.as_secs() >= 600 + 60);
        // Health: generation health timeout is 60 s + slack.
        assert!(MODEL_HEALTH_TIMEOUT.as_secs() >= 60 + 30);
        // Import: hashing a multi-GB file; bounded but generous.
        assert!(MODEL_IMPORT_TIMEOUT.as_secs() >= 120);
        // Hardware: the nvidia-smi probe is capped at 5 s service-side;
        // the deadline must cover a cold probe plus margin.
        assert!(HARDWARE_TIMEOUT.as_secs() >= 5 + 5);
        // Download starts: bounded local work (manifest load + URL rule +
        // disk pre-flight) that must still outlive the default deadline
        // on a cold, loaded machine.
        assert!(MODEL_CATALOG_TIMEOUT >= HTTP_REQUEST_TIMEOUT);
        assert!(DOWNLOAD_START_TIMEOUT >= HTTP_REQUEST_TIMEOUT);
        // Poll/cancel are in-memory snapshots: tight by design so a
        // wedged service surfaces fast in the UI's poll loop.
        assert!(DOWNLOAD_POLL_TIMEOUT <= HTTP_REQUEST_TIMEOUT);
    }
}
