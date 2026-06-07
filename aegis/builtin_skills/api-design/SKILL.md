---
name: api-design
description: Design clean REST/GraphQL APIs: resources, methods, status codes, pagination, auth, versioning, and OpenAPI specs. Use when designing or reviewing an API.
version: 1.0.0
metadata:
  category: design
  tags: [rest, graphql, openapi, http]
---

## When to Use
Designing a new API surface, adding endpoints, or reviewing an existing API for
consistency, correctness, and ergonomics (REST or GraphQL).

## Procedure
1. Model resources as nouns, not verbs. List entities and their relationships
   before writing any paths. Read existing route/schema files first (read_file).
2. Map operations to HTTP methods: GET (read, safe), POST (create), PUT (full
   replace, idempotent), PATCH (partial update), DELETE (remove). Use plural
   collection paths: `/users`, `/users/{id}`, `/users/{id}/orders`.
3. Pick correct status codes: 200 OK, 201 Created (+`Location`), 204 No Content,
   400 bad input, 401 unauthenticated, 403 unauthorized, 404 not found,
   409 conflict, 422 validation, 429 rate-limited, 5xx server.
4. Standardize collections: cursor pagination (`?cursor=&limit=`) over offset for
   large/changing sets; consistent filtering/sorting (`?sort=-created_at`).
5. Define auth: bearer tokens (OAuth2/JWT) in `Authorization` header; scopes per
   endpoint. Never put secrets/tokens in URLs.
6. Version explicitly: URL prefix `/v1` or `Accept` header. Plan deprecation via
   `Deprecation`/`Sunset` headers. Never break v1 silently.
7. Write/update the OpenAPI 3.1 (REST) or SDL (GraphQL) spec as source of truth;
   include error schema, examples, auth schemes (write_file/edit_file).
8. Validate the spec, then review against the Pitfalls list.

## Quick Reference
- Error body: `{ "error": { "code": "string", "message": "...", "details": [] } }`
- Pagination response: `{ "data": [...], "next_cursor": "...", "has_more": true }`
- Idempotency: require `Idempotency-Key` header on non-idempotent POSTs.
- Validate spec: `npx @redocly/cli lint openapi.yaml` or `spectral lint openapi.yaml`
- GraphQL: one `/graphql` endpoint, POST; errors in `errors[]`; avoid REST verbs.

## Pitfalls
- Verbs in paths (`/getUser`, `/createOrder`) — use methods + nouns.
- 200 for everything (errors hidden in body); return real status codes.
- Offset pagination on large mutating data → skips/dupes; use cursors.
- Unbounded list endpoints (no default `limit`) → DoS risk.
- Breaking changes without a new version; renaming/removing fields in place.
- Inconsistent casing/plurality across endpoints; pick one and enforce it.
- Leaking internal IDs/stack traces in error responses.

## Verification
- Spec lints clean (redocly/spectral) with zero errors.
- Every endpoint: documented request/response, auth requirement, and error cases.
- Status codes, casing, pagination, and error shape consistent across all routes.
- Backward compatibility preserved or version bumped with deprecation noted.
