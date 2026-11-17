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

#[cfg(test)]
mod tests {
    use super::*;

    const ASSESSMENT: &str = "0f8fad5b-d9cb-469f-a165-70867728950e";
    const MODEL: &str = "0f8fad5b-d9cb-469f-a165-70867728950e";

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
            "embedding_normalization": null, "embedding_similarity_metric": null
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

        let relative = std::env::temp_dir().join("relative").join("model.gguf");
        let relative = relative
            .to_str()
            .unwrap()
            .strip_prefix(std::path::MAIN_SEPARATOR_STR)
            .unwrap_or(relative.to_str().unwrap())
            .to_string();
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
    }
}
