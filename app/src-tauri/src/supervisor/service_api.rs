//! Sprint 2 (S2-04): assessment and framework requests made on the
//! frontend's behalf, entirely in Rust, over the **same pinned,
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
//! - Path segments are never interpolated from free text: the only
//!   caller-supplied segment, an assessment id, must be a canonical
//!   lowercase UUID ([`is_canonical_uuid`]); paging values are integers.
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
pub struct FrameworkSummary {
    pub id: String,
    pub name: String,
    pub version: String,
    pub source_reference: Option<String>,
    pub control_count: u64,
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
