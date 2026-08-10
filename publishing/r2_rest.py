from __future__ import annotations

import http.client
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import BinaryIO, Mapping

from publishing.cloudflare_oauth import CloudflareOAuthError, CloudflareOAuthSession


API_HOST = "api.cloudflare.com"
API_PREFIX = "/client/v4"
MAX_OBJECT_UPLOAD_BYTES = 300_000_000


class CloudflareApiError(RuntimeError):
    def __init__(
        self,
        provider_code: str,
        status: int,
        message: str,
        request_id: str = "",
    ) -> None:
        super().__init__(message)
        self.response = {
            "Error": {"Code": provider_code, "Message": message},
            "ResponseMetadata": {
                "HTTPStatusCode": int(status),
                "RequestId": request_id,
            },
        }


def _api_error(status: int, payload: object, headers: Mapping[str, str]) -> CloudflareApiError:
    provider_code = str(status)
    message = f"Cloudflare API request failed with HTTP {status}."
    if isinstance(payload, Mapping):
        errors = payload.get("errors", [])
        if isinstance(errors, list) and errors and isinstance(errors[0], Mapping):
            provider_code = str(errors[0].get("code", status))
            message = str(errors[0].get("message", message))
    request_id = str(headers.get("cf-ray", headers.get("Cf-Ray", "")))
    return CloudflareApiError(provider_code, status, message, request_id)


class CloudflareR2RestClient:
    """Small boto-compatible adapter over Cloudflare's OAuth-capable R2 REST API."""

    def __init__(self, account_id: str, session: CloudflareOAuthSession) -> None:
        self.account_id = str(account_id).strip()
        self.session = session

    def _bucket_path(self, bucket: str, suffix: str = "") -> str:
        account = urllib.parse.quote(self.account_id, safe="")
        bucket_name = urllib.parse.quote(str(bucket), safe="")
        return f"/accounts/{account}/r2/buckets/{bucket_name}{suffix}"

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, object] | None = None,
        body: Mapping[str, object] | None = None,
    ) -> dict:
        target = f"https://{API_HOST}{API_PREFIX}{path}"
        if query:
            target = f"{target}?{urllib.parse.urlencode(query)}"
        data = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.session.access_token()}",
            "User-Agent": "LetterSmith/1",
        }
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(target, data=data, headers=headers, method=method)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30.0) as response:
                    raw = response.read()
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                    if isinstance(payload, Mapping) and payload.get("success") is False:
                        raise _api_error(response.status, payload, response.headers)
                    return dict(payload) if isinstance(payload, Mapping) else {}
            except urllib.error.HTTPError as error:
                raw = error.read()
                try:
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                except (UnicodeError, json.JSONDecodeError):
                    payload = {"errors": [{"message": raw.decode("utf-8", errors="replace")[:2000]}]}
                if error.code in {429, 500, 502, 503, 504} and attempt < 2:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise _api_error(error.code, payload, error.headers) from error
            except (OSError, urllib.error.URLError) as error:
                if attempt < 2:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise CloudflareApiError(type(error).__name__, 0, str(error)) from error
        raise CloudflareApiError("request_failed", 0, "Cloudflare API request failed.")

    def _request_bytes(self, path: str) -> bytes:
        target = f"https://{API_HOST}{API_PREFIX}{path}"
        request = urllib.request.Request(
            target,
            headers={
                "Accept": "application/octet-stream",
                "Authorization": f"Bearer {self.session.access_token()}",
                "User-Agent": "LetterSmith/1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60.0) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeError, json.JSONDecodeError):
                payload = {}
            raise _api_error(error.code, payload, error.headers) from error
        except (OSError, urllib.error.URLError) as error:
            raise CloudflareApiError(type(error).__name__, 0, str(error)) from error

    def head_bucket(self, *, Bucket: str) -> dict:
        return self._request_json("GET", self._bucket_path(Bucket))

    def create_bucket(self, *, Bucket: str) -> dict:
        account = urllib.parse.quote(self.account_id, safe="")
        return self._request_json(
            "POST",
            f"/accounts/{account}/r2/buckets",
            body={"name": str(Bucket)},
        )

    def enable_public_domain(self, bucket: str) -> str:
        payload = self._request_json(
            "PUT",
            self._bucket_path(bucket, "/domains/managed"),
            body={"enabled": True},
        )
        result = payload.get("result", {})
        domain = str(result.get("domain", "")) if isinstance(result, Mapping) else ""
        enabled = bool(result.get("enabled", False)) if isinstance(result, Mapping) else False
        if not domain or not enabled:
            raise CloudflareApiError(
                "public_access",
                400,
                "Cloudflare did not enable the R2 public domain.",
            )
        return f"https://{domain}"

    def get_bucket_lifecycle_configuration(self, *, Bucket: str) -> dict:
        try:
            payload = self._request_json(
                "GET",
                self._bucket_path(Bucket, "/lifecycle"),
            )
        except CloudflareApiError as error:
            if error.response["ResponseMetadata"]["HTTPStatusCode"] == 404:
                return {"Rules": []}
            raise
        result = payload.get("result", {})
        raw_rules = result.get("rules", []) if isinstance(result, Mapping) else []
        rules: list[dict] = []
        for raw in raw_rules:
            if not isinstance(raw, Mapping):
                continue
            rule: dict = {
                "ID": str(raw.get("id", "")),
                "Status": "Enabled" if bool(raw.get("enabled", False)) else "Disabled",
                "Filter": {
                    "Prefix": str(
                        raw.get("conditions", {}).get("prefix", "")
                        if isinstance(raw.get("conditions"), Mapping)
                        else ""
                    )
                },
                "_CloudflareRaw": dict(raw),
            }
            deletion = raw.get("deleteObjectsTransition")
            if isinstance(deletion, Mapping):
                condition = deletion.get("condition", {})
                if isinstance(condition, Mapping) and condition.get("type") == "Age":
                    rule["Expiration"] = {
                        "Days": max(1, int(condition.get("maxAge", 0) or 0) // 86400)
                    }
            abort = raw.get("abortMultipartUploadsTransition")
            if isinstance(abort, Mapping):
                condition = abort.get("condition", {})
                if isinstance(condition, Mapping) and condition.get("type") == "Age":
                    rule["AbortIncompleteMultipartUpload"] = {
                        "DaysAfterInitiation": max(
                            1, int(condition.get("maxAge", 0) or 0) // 86400
                        )
                    }
            rules.append(rule)
        return {"Rules": rules}

    def put_bucket_lifecycle_configuration(
        self,
        *,
        Bucket: str,
        LifecycleConfiguration: Mapping[str, object],
    ) -> dict:
        output: list[dict] = []
        for rule in LifecycleConfiguration.get("Rules", []):
            if not isinstance(rule, Mapping):
                continue
            raw = rule.get("_CloudflareRaw")
            if isinstance(raw, Mapping):
                output.append(dict(raw))
                continue
            prefix_filter = rule.get("Filter", {})
            prefix = (
                str(prefix_filter.get("Prefix", ""))
                if isinstance(prefix_filter, Mapping)
                else ""
            )
            translated: dict = {
                "id": str(rule.get("ID", "")),
                "enabled": str(rule.get("Status", "")).casefold() == "enabled",
                "conditions": {"prefix": prefix},
            }
            expiration = rule.get("Expiration")
            if isinstance(expiration, Mapping) and expiration.get("Days"):
                translated["deleteObjectsTransition"] = {
                    "condition": {
                        "type": "Age",
                        "maxAge": int(expiration["Days"]) * 86400,
                    }
                }
            abort = rule.get("AbortIncompleteMultipartUpload")
            if isinstance(abort, Mapping) and abort.get("DaysAfterInitiation"):
                translated["abortMultipartUploadsTransition"] = {
                    "condition": {
                        "type": "Age",
                        "maxAge": int(abort["DaysAfterInitiation"]) * 86400,
                    }
                }
            output.append(translated)
        return self._request_json(
            "PUT",
            self._bucket_path(Bucket, "/lifecycle"),
            body={"rules": output},
        )

    def list_objects_v2(
        self,
        *,
        Bucket: str,
        Prefix: str = "",
        ContinuationToken: str = "",
        **_kwargs: object,
    ) -> dict:
        query: dict[str, object] = {"per_page": 1000}
        if Prefix:
            query["prefix"] = Prefix
        if ContinuationToken:
            query["cursor"] = ContinuationToken
        payload = self._request_json(
            "GET",
            self._bucket_path(Bucket, "/objects"),
            query=query,
        )
        result = payload.get("result", [])
        info = payload.get("result_info", {})
        contents = []
        for item in result if isinstance(result, list) else []:
            if isinstance(item, Mapping):
                contents.append(
                    {
                        "Key": str(item.get("key", "")),
                        "Size": max(0, int(item.get("size", 0) or 0)),
                    }
                )
        truncated = bool(info.get("is_truncated", False)) if isinstance(info, Mapping) else False
        cursor = str(info.get("cursor", "")) if isinstance(info, Mapping) else ""
        return {
            "Contents": contents,
            "IsTruncated": truncated,
            "NextContinuationToken": cursor,
        }

    def _upload_stream(
        self,
        *,
        bucket: str,
        key: str,
        stream: BinaryIO,
        size: int,
        content_type: str,
        cache_control: str = "",
    ) -> dict:
        object_key = urllib.parse.quote(str(key), safe="/")
        path = f"{API_PREFIX}{self._bucket_path(bucket, f'/objects/{object_key}')}"
        for attempt in range(3):
            if attempt:
                stream.seek(0)
            connection = http.client.HTTPSConnection(API_HOST, timeout=90.0)
            try:
                connection.putrequest("PUT", path)
                connection.putheader("Accept", "application/json")
                connection.putheader("Authorization", f"Bearer {self.session.access_token()}")
                connection.putheader("Content-Type", content_type)
                connection.putheader("Content-Length", str(size))
                connection.putheader("cf-r2-storage-class", "Standard")
                if cache_control:
                    connection.putheader("Cache-Control", cache_control)
                connection.putheader("User-Agent", "LetterSmith/1")
                connection.endheaders()
                while chunk := stream.read(1024 * 1024):
                    connection.send(chunk)
                response = connection.getresponse()
                raw = response.read()
                try:
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                except (UnicodeError, json.JSONDecodeError):
                    payload = {}
                if 200 <= response.status < 300 and (
                    not isinstance(payload, Mapping) or payload.get("success", True)
                ):
                    return dict(payload) if isinstance(payload, Mapping) else {}
                if response.status in {429, 500, 502, 503, 504} and attempt < 2:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise _api_error(response.status, payload, dict(response.headers))
            except CloudflareOAuthError:
                raise
            except (OSError, http.client.HTTPException) as error:
                if attempt < 2:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise CloudflareApiError(type(error).__name__, 0, str(error)) from error
            finally:
                connection.close()
        raise CloudflareApiError("upload_failed", 0, "Cloudflare upload failed.")

    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        *,
        ExtraArgs: Mapping[str, object],
    ) -> None:
        path = Path(filename)
        size = path.stat().st_size
        if size > MAX_OBJECT_UPLOAD_BYTES:
            raise CloudflareApiError(
                "EntityTooLarge",
                413,
                "The R2 REST API accepts files up to 300 MB.",
            )
        with path.open("rb") as stream:
            self._upload_stream(
                bucket=bucket,
                key=key,
                stream=stream,
                size=size,
                content_type=str(ExtraArgs.get("ContentType", "application/octet-stream")),
                cache_control=str(ExtraArgs.get("CacheControl", "")),
            )

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str = "application/octet-stream",
        **_kwargs: object,
    ) -> dict:
        payload = bytes(Body)
        return self._upload_stream(
            bucket=Bucket,
            key=Key,
            stream=io.BytesIO(payload),
            size=len(payload),
            content_type=ContentType,
            cache_control=str(_kwargs.get("CacheControl", "")),
        )

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        response = self.list_objects_v2(Bucket=Bucket, Prefix=Key)
        for item in response.get("Contents", []):
            if isinstance(item, Mapping) and str(item.get("Key", "")) == Key:
                return {"ContentLength": int(item.get("Size", 0) or 0)}
        raise CloudflareApiError("NoSuchKey", 404, "The R2 object was not found.")

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        object_key = urllib.parse.quote(str(Key), safe="/")
        body = self._request_bytes(self._bucket_path(Bucket, f"/objects/{object_key}"))
        return {"Body": io.BytesIO(body), "ContentLength": len(body)}

    def delete_objects(self, *, Bucket: str, Delete: Mapping[str, object]) -> dict:
        deleted: list[dict[str, str]] = []
        raw_objects = Delete.get("Objects", [])
        for item in raw_objects if isinstance(raw_objects, list) else []:
            if not isinstance(item, Mapping):
                continue
            key = str(item.get("Key", ""))
            if not key:
                continue
            object_key = urllib.parse.quote(key, safe="/")
            self._request_json(
                "DELETE",
                self._bucket_path(Bucket, f"/objects/{object_key}"),
            )
            deleted.append({"Key": key})
        return {"Deleted": deleted}


__all__ = ["CloudflareApiError", "CloudflareR2RestClient"]
